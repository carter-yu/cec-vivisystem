"""Keyword retrieval over the knowledge tree (Phase 32 v1, ADR 0017).

Why no embeddings: the tree is about a hundred short bilingual nodes. English
words, Chinese character bigrams and a small Cantonese/English alias table find
the right rows offline, deterministically and with no new dependency.

Contract: ``retrieve`` returns scored hits (best first) or an empty list when
nothing scores above ``MIN_SCORE``; an empty list means "not in this document"
and the caller must not ask the model. ``build_context_pack`` adds parents,
related overall maxima, referenced footnotes and pinned notes (currency), caps
the size, and redacts again. Both functions are injectable in the handler.

Aliases map family wording to document wording. Traditional Chinese only:
never add Simplified keys (ground rule 9).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from cec_vivisystem.medic_librarian.tree import (
    CATEGORY_TITLES,
    KnowledgeNode,
    KnowledgeTree,
)
from cec_vivisystem.redaction import redact_sensitive

DEFAULT_TOP_K = 5
MIN_SCORE = 2.5
RELATIVE_FLOOR = 0.35
CONTEXT_MAX_CHARS = 7000

_LATIN = re.compile(r"[a-z][a-z0-9'’\-]*|\d[\d,]*%?")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_STOPWORDS = frozenset({
    "a", "an", "the", "of", "for", "to", "in", "on", "and", "or", "is", "are", "am", "do",
    "does", "did", "can", "could", "will", "would", "be", "been", "i", "my", "me", "we",
    "our", "you", "your", "he", "she", "his", "her", "it", "its", "this", "that", "these",
    "those", "with", "per", "up", "by", "at", "as", "if", "any", "there", "what", "which",
    "who", "how", "much", "many", "when", "where", "why", "get", "got", "have", "has",
    "please", "tell", "know", "about", "from", "than", "then", "so", "not", "no", "yes",
    "also", "just", "under", "over", "into",
})
# Question filler that matches many rows ("can I claim", "maximum") counts a
# little; related maxima reach the model through the context pack anyway.
GENERIC_WEIGHT = 0.3
_GENERIC = frozenset({
    "claim", "cover", "covered", "coverage", "pay", "paid", "benefit", "policy", "year",
    "plan", "insurance", "maximum", "max", "limit", "索償", "保障", "福利", "最高", "上限", "賠償",
})
# Cantonese question bigrams that carry no topic.
_CJK_STOP = frozenset({
    "需要", "可以", "有冇", "幾多", "點樣", "點算", "係咪", "係唔", "唔係", "邊個", "乜嘢", "咩嘢", "我哋", "你哋",
    "佢哋", "而家", "請問", "想問", "知道", "一下", "如果", "嘅話", "會唔", "得唔", "冇得", "有得", "個人",
})
_CODE_IN_CONDITION = re.compile(r"\(([A-Z]{2,6})\)")

# Family wording → document wording. Keys are lower-case English or Traditional
# Chinese substrings; values are extra query terms.
DEFAULT_ALIASES: Mapping[str, Sequence[str]] = {
    # Dental
    "睇牙": ("牙科", "dental"), "牙醫": ("牙科", "dental"), "牙痛": ("牙科", "dental pain"),
    "洗牙": ("洗牙", "teeth cleaning"), "脫牙": ("脫牙", "extraction"),
    "剝牙": ("脫牙", "extraction"), "杜牙根": ("齒根管", "root canal"),
    "智慧齒": ("智慧齒", "wisdom tooth"), "補牙": ("補牙", "fillings"),
    "dentist": ("dental",), "teeth": ("dental", "teeth"), "tooth": ("dental", "teeth"),
    # Outpatient
    "睇醫生": ("普通科", "門診", "general physician"), "街症": ("普通科", "門診"),
    "家庭醫生": ("普通科", "general physician"), "睇症": ("門診", "consultation"),
    "gp": ("general physician",), "doctor": ("physician", "consultation"),
    "clinic": ("outpatient", "consultation"), "專科": ("專科", "specialist"),
    "physio": ("physiotherapy",), "脊醫": ("脊骨神經", "chiropractic"),
    "chiro": ("chiropractic",), "跌打": ("跌打", "chinese medicine"),
    "針灸": ("針灸", "acupuncture"), "推拿": ("推拿", "tui na"),
    "驗血": ("blood test", "基本診斷"), "x光": ("x-ray", "基本診斷"),
    "照x光": ("x-ray", "基本診斷"), "掃描": ("mri", "ct", "先進診斷"),
    "mri": ("advanced diagnostic imaging", "磁力共振"), "藥": ("藥物", "medicine"),
    "drug": ("medicine", "drugs"), "打針": ("疫苗",), "流感": ("疫苗", "vaccination"),
    "注射": ("疫苗",), "vaccine": ("vaccination",), "flu": ("vaccination",), "驗身": ("健康檢查", "physical examination"),
    "body check": ("physical examination",), "check-up": ("physical examination",),
    "checkup": ("physical examination",), "驗眼": ("視力檢查", "optical"),
    "眼鏡": ("視力檢查", "optical"), "eye": ("optical",), "心理": ("心理輔導", "psychological"),
    "輔導": ("心理輔導", "psychological"), "counsel": ("psychological",),
    "psycholog": ("psychological",), "psychiatr": ("psychiatric",),
    "therapist": ("psychological", "therapy"), "視像": ("視像診症", "telemedicine"),
    "video": ("telemedicine",), "online doctor": ("telemedicine",),
    # Hospital
    "入院": ("住院", "hospitalisation"), "留院": ("住院", "hospitalisation"),
    "hospital": ("hospitalisation",), "inpatient": ("hospitalisation", "inpatient"),
    "病房": ("膳宿", "room"), "房租": ("膳宿", "room board"),
    "手術": ("手術", "operation", "surgeon"), "surgery": ("operation", "surgeon", "surgical"),
    "麻醉": ("麻醉", "anaesthetist"), "anesthe": ("anaesthetist",), "icu": ("intensive care",),
    "癌": ("癌症", "cancer"), "化療": ("癌症", "cancer"), "chemo": ("cancer",),
    "陪床": ("陪床", "companion bed"), "公立醫院": ("醫管局", "ha hospitals"),
    "政府醫院": ("醫管局", "ha hospitals"), "公院": ("醫管局", "ha hospitals"),
    "海外": ("海外", "overseas"), "旅行": ("海外", "overseas", "trip"),
    "travel": ("overseas", "trip"), "內地": ("中國", "china"), "大陸": ("中國", "china"),
    "按金": ("按金", "deposit"), "救護": ("運送", "evacuation"),
    # Maternity
    "生仔": ("分娩", "maternity", "delivery"), "生bb": ("分娩", "maternity", "delivery"),
    "懷孕": ("分娩", "pregnancy"), "pregnan": ("maternity", "pregnancy"),
    "baby": ("maternity", "delivery"), "開刀生": ("剖腹分娩", "caesarian"),
    "c-section": ("caesarian",), "cesarean": ("caesarian",), "小產": ("流產", "miscarriage"),
    # Limits, co-pay, network, claims
    "上限": ("最高",), "limit": ("maximum",), "cap": ("maximum",), "自付": ("自付費", "co-pay"),
    "copay": ("co-pay",), "俾錢": ("自付費", "co-pay"), "畀錢": ("自付費", "co-pay"),
    "網絡": ("網絡", "network"), "報銷": ("索償",), "e-claim": ("e-claims",),
    "收據": ("收據", "receipt"), "receipt": ("receipt",), "轉介": ("轉薦信", "referral"),
    "referral": ("referral", "轉薦信"), "保健咭": ("healthcard",), "醫療卡": ("healthcard",),
    "急症": ("emergency", "緊急"), "港幣": ("currency", "港元"), "currency": ("currency",),
    "生效": ("effective", "生效"), "幾時到期": ("effective",),
}


@dataclass(frozen=True, slots=True)
class Hit:
    node: KnowledgeNode
    score: float


def tokenize(text: str) -> set[str]:
    """Lower-case English words (light stemming) + Chinese bigrams."""
    tokens: set[str] = set()
    lowered = text.lower()
    for word in _LATIN.findall(lowered):
        word = word.strip("-'’").replace("’", "'")
        word = word.removesuffix("'s")
        if not word or word in _STOPWORDS:
            continue
        if word[0].isdigit():
            tokens.add(word.replace(",", ""))
            continue
        parts = [word, *word.split("-")] if "-" in word else [word]
        for part in parts:
            if len(part) < 2 or part in _STOPWORDS:
                continue
            tokens.add(_stem(part))
    for run in _CJK_RUN.findall(text):
        if len(run) == 1:
            tokens.add(run)
        tokens.update(run[i:i + 2] for i in range(len(run) - 1))
    return tokens - _CJK_STOP


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def expand_query(question: str, aliases: Mapping[str, Sequence[str]]) -> set[str]:
    tokens = tokenize(question)
    lowered = question.lower()
    for key, extra in aliases.items():
        if _alias_present(key, lowered):
            for phrase in extra:
                tokens |= tokenize(phrase)
    return tokens


def _alias_present(key: str, lowered: str) -> bool:
    if _CJK_RUN.search(key):
        return key in lowered
    return re.search(rf"(?<![a-z]){re.escape(key)}", lowered) is not None


def _node_texts(tree: KnowledgeTree, node: KnowledgeNode) -> tuple[set[str], set[str]]:
    titles = [node.title_en, node.title_zh, " ".join(node.codes)]
    if node.kind == "item" and node.parent_id:
        parent = tree.node(node.parent_id)
        if parent is not None and parent.kind == "item":
            titles += [parent.title_en, parent.title_zh]
    body = [node.summary, *node.bullets, *node.conditions, node.section]
    return tokenize(" ".join(titles)), tokenize(" ".join(body))


def retrieve(
    tree: KnowledgeTree,
    question: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    aliases: Mapping[str, Sequence[str]] = DEFAULT_ALIASES,
    min_score: float = MIN_SCORE,
) -> list[Hit]:
    """Best-matching nodes for a (redacted) question; empty means "not found"."""
    query = expand_query(question, aliases)
    if not query or not tree.nodes:
        return []
    indexed = [(node, *_node_texts(tree, node)) for node in tree.nodes]
    total = len(indexed)
    df: dict[str, int] = {}
    for _node, title, body in indexed:
        for token in title | body:
            df[token] = df.get(token, 0) + 1
    scored: list[Hit] = []
    for node, title, body in indexed:
        score = 0.0
        for token in query:
            if token not in df:
                continue
            idf = math.log(1 + total / df[token])
            if token in _GENERIC:
                idf *= GENERIC_WEIGHT
            if token in title:
                score += 2.0 * idf
            elif token in body:
                score += idf
        if score >= min_score:
            scored.append(Hit(node, round(score, 3)))
    if not scored:
        return []
    scored.sort(key=lambda h: -h.score)
    floor = scored[0].score * RELATIVE_FLOOR
    return [h for h in scored if h.score >= floor][:top_k]


def citation_label(tree: KnowledgeTree, node: KnowledgeNode, language: str) -> str:
    """"Benefit Summary p.2 · Outpatient › Physiotherapy (PHY) 物理治療"."""
    doc = tree.document(node.doc_id)
    category_en, category_zh = CATEGORY_TITLES.get(node.category, (node.category, node.category))
    title = node.display_title(language)
    if language == "zh":
        doc_label = doc.label_zh if doc else "文件"
        return f"{doc_label} 第 {node.page} 頁 · {category_zh} › {title}"
    doc_label = doc.label_en if doc else "Document"
    return f"{doc_label} p.{node.page} · {category_en} › {title}"


def build_context_pack(
    tree: KnowledgeTree,
    hits: Sequence[Hit],
    *,
    max_chars: int = CONTEXT_MAX_CHARS,
) -> tuple[list[dict[str, object]], int]:
    """Context entries for the model + how many redactions this pass made.

    Order: hits, their ancestors, related overall maxima, referenced footnotes,
    pinned notes. Entries past ``max_chars`` are dropped (hits are kept first).
    """
    ordered: list[KnowledgeNode] = []
    seen: set[str] = set()

    def add(node: KnowledgeNode | None) -> None:
        if node is not None and node.id not in seen:
            seen.add(node.id)
            ordered.append(node)

    for hit in hits:
        add(hit.node)
    for hit in hits:
        # A footnote hit brings the rows that cite it ("MRI" → note 8 → LAB row).
        if hit.node.note_number is not None:
            citing = [n for n in tree.nodes if n.kind == "item"
                      and n.doc_id == hit.node.doc_id and hit.node.note_number in n.notes]
            for node in citing[:3]:
                add(node)
    lineage: list[KnowledgeNode] = []
    for hit in hits:
        lineage.extend([hit.node, *tree.ancestors(hit.node)])
    for node in lineage:
        add(node)
    for limit in _related_limits(tree, hits):
        add(limit)
    references = sorted({(node.doc_id, number) for node in lineage for number in node.notes},
                        key=lambda ref: (ref[0], ref[1]))
    for doc_id, number in references:
        add(next((n for n in tree.nodes
                  if n.note_number == number and n.doc_id == doc_id), None))
    for node in tree.nodes:
        if node.pinned:
            add(node)
    entries: list[dict[str, object]] = []
    redactions = 0
    used = 0
    for node in ordered:
        entry = _entry(tree, node)
        size = sum(len(str(v)) for v in entry.values())
        if entries and used + size > max_chars:
            continue
        for key in ("title", "path", "text"):
            safe, count = redact_sensitive(str(entry[key]))
            entry[key] = safe
            redactions += count
        entries.append(entry)
        used += size
    return entries, redactions


def _related_limits(tree: KnowledgeTree, hits: Sequence[Hit]) -> list[KnowledgeNode]:
    limits: list[KnowledgeNode] = []
    for hit in hits:
        family = {hit.node.id, *(a.id for a in tree.ancestors(hit.node))}
        for node in tree.nodes:
            if node.category != "limits" or node.parent_id not in family:
                continue
            codes = set(_CODE_IN_CONDITION.findall(" ".join(node.conditions)))
            if not codes or codes & set(hit.node.codes):
                limits.append(node)
    return limits


def _entry(tree: KnowledgeTree, node: KnowledgeNode) -> dict[str, object]:
    path = " › ".join(a.display_title() for a in reversed(tree.ancestors(node)))
    lines = list(node.bullets)
    lines += [f"Inferred from the table layout: {text}" for text in node.inferred]
    doc = tree.document(node.doc_id)
    return {
        "id": node.id,
        "document": doc.label_en if doc else node.doc_id,
        "page": node.page,
        "category": node.category,
        "title": node.display_title(),
        "path": path,
        "text": "\n".join(lines) if lines else node.summary,
    }
