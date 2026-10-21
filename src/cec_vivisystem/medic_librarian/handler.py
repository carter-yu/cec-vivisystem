"""#jeff-aia-medic message handling (Phase 32, ADR 0017).

Why: the medic channel is a read-only librarian over the insurer's document.
It must never reach the Calendar parser, confirmations, important dates or
life notes, and must not send ID-like tokens to a cloud model.

Contract: ``handle_medic_message`` always returns a ``ListenerResult`` with a
short reply (help, answer with sources, "not in this document", extractive
fallback, or an apology). It stores nothing. Logs carry counts, lengths,
modes and model names, never question text, answers or redacted values.

Do not import Calendar writer/reader/confirmation modules here (import
isolation test). Do not let the model's text choose sources: sources are
rendered from validated node ids.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from cec_vivisystem.logging import get_logger
from cec_vivisystem.medic_librarian.answer import (
    MedicAnswer,
    MedicLlm,
    ask_models,
    load_live_medic_llms,
)
from cec_vivisystem.medic_librarian.retrieve import (
    Hit,
    build_context_pack,
    citation_label,
    retrieve,
)
from cec_vivisystem.medic_librarian.tree import (
    KnowledgeNode,
    KnowledgeTree,
    KnowledgeTreeCache,
    default_tree_cache,
)
from cec_vivisystem.models import InboundMessage, ListenerOutcome, ListenerResult
from cec_vivisystem.redaction import redact_sensitive

logger = get_logger(__name__)

COMPONENT = "medic_librarian"
DEFAULT_MEDIC_CHANNEL_ID = "C0CB179V0AE"
_DISABLED = frozenset({"off", "none", "disabled", "0", "false"})
MAX_SOURCES = 3
MAX_FALLBACK_HITS = 3
_IDEOGRAPH = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_HELP = re.compile(
    r"^\s*(?:help|\?|？|指令|幫助|說明|點用|使用說明|用法)\s*[?？!！.。]*\s*$", re.IGNORECASE
)

# Runtime copy: English in simplified technical English style, Chinese in HK
# Traditional (Cantonese-friendly). Never Simplified Chinese.
HELP_TEXT = (
    "This channel answers questions about the AIA medical plan. I use only the plan "
    "documents and I show the page. Examples: \"How much physiotherapy can I claim?\", "
    "\"Is the GP co-pay in-network?\". I do not change the calendar here. "
    "Do not type policy, member or ID card numbers.\n"
    "呢個頻道解答友邦醫療計劃嘅問題，只會根據計劃文件回答，並註明頁數。例如："
    "「物理治療每年可以 claim 幾多？」、「睇牙有冇得 claim？」。呢度唔會改日曆。"
    "請唔好打保單號碼、會員號碼或者身份證號碼。"
)
COPY = {
    "en": {
        "unknown": "I cannot find this in {docs}. I do not know the answer from this "
                   "document. Ask AIA or check the full policy.",
        "fallback_intro": "I cannot write an answer now. These lines in {docs} match "
                          "your question:",
        "sources": "Source",
        "scope": "(Only from {docs}. AIA's policy terms decide each claim.)",
        "kb_missing": "The plan document is not available now. Ask an administrator to "
                      "rebuild the knowledge base.",
        "error": "Sorry, I cannot answer now. Please try again later.",
    },
    "zh": {
        "unknown": "{docs}入面搵唔到呢個資料，所以我唔知道答案。請向友邦查詢或者睇返完整保單。",
        "fallback_intro": "暫時未能整理答案。{docs}入面以下內容同你嘅問題有關：",
        "sources": "來源",
        "scope": "（只根據{docs}；實際賠償以友邦保單條款為準。）",
        "kb_missing": "暫時讀唔到計劃文件，請管理員重新建立資料庫。",
        "error": "唔好意思，暫時答唔到，請稍後再試。",
    },
}

RetrieveFn = Callable[[KnowledgeTree, str], list[Hit]]


def detect_language(text: str) -> str:
    """``zh`` when the question has Chinese characters, else ``en``."""
    return "zh" if _IDEOGRAPH.search(text) else "en"


def is_help(text: str) -> bool:
    return bool(_HELP.match(text))


def resolve_medic_channel_id(env: Mapping[str, str] | None = None) -> str | None:
    """``CEC_MEDIC_CHANNEL_ID``; unset/empty → default; off/none/disabled → None."""
    source = env if env is not None else os.environ
    raw = (source.get("CEC_MEDIC_CHANNEL_ID") or "").strip()
    if not raw:
        return DEFAULT_MEDIC_CHANNEL_ID
    if raw.lower() in _DISABLED:
        return None
    return raw


def handle_medic_message(
    message: InboundMessage,
    *,
    tree: KnowledgeTree | None,
    llm: MedicLlm | None = None,
    llm_fallback: MedicLlm | None = None,
    retrieve_fn: RetrieveFn = retrieve,
    correlation_id: str | None = None,
    kb_source: str | None = None,
) -> ListenerResult:
    """Answer one medic-channel message. Never raises; never touches Calendar."""
    started = time.perf_counter()
    corr = correlation_id or message.correlation_id or str(uuid.uuid4())
    language = detect_language(message.text)
    mode = "error"
    try:
        safe_question, redactions = redact_sensitive(message.text)
        logger.info("medic_message_received", component=COMPONENT, correlation_id=corr,
                    channel_id=message.channel_id, user_id=message.user_id,
                    slack_event_id=message.slack_event_id, message_length=len(message.text),
                    redaction_count=redactions, language=language, kb_source=kb_source)
        if is_help(safe_question):
            mode, reply = "help", HELP_TEXT
        elif tree is None:
            mode, reply = "kb_missing", COPY[language]["kb_missing"]
        else:
            mode, reply = _answer(tree, safe_question, language=language, llm=llm,
                                  llm_fallback=llm_fallback, retrieve_fn=retrieve_fn,
                                  correlation_id=corr)
        # Defense in depth: nothing ID-like leaves in a reply either.
        reply, reply_redactions = redact_sensitive(reply)
        logger.info("medic_message_handled", component=COMPONENT, correlation_id=corr,
                    outcome="success" if mode in ("help", "answered", "unknown") else "partial",
                    mode=mode, reply_redaction_count=reply_redactions,
                    duration_ms=int((time.perf_counter() - started) * 1000))
        return ListenerResult(outcome=ListenerOutcome.REPLIED, correlation_id=corr,
                              reply_text=reply)
    except Exception as exc:  # noqa: BLE001 — boundary: reply with an apology, no trace
        logger.error("medic_message_failed", component=COMPONENT, correlation_id=corr,
                     outcome="failure", mode=mode, error_type=type(exc).__name__,
                     duration_ms=int((time.perf_counter() - started) * 1000))
        return ListenerResult(outcome=ListenerOutcome.REPLIED, correlation_id=corr,
                              reply_text=COPY[language]["error"],
                              error_type=type(exc).__name__)


def _answer(
    tree: KnowledgeTree,
    question: str,
    *,
    language: str,
    llm: MedicLlm | None,
    llm_fallback: MedicLlm | None,
    retrieve_fn: RetrieveFn,
    correlation_id: str,
) -> tuple[str, str]:
    copy = COPY[language]
    docs = _doc_labels(tree, language)
    hits = retrieve_fn(tree, question)
    logger.info("medic_retrieval", component=COMPONENT, correlation_id=correlation_id,
                hit_count=len(hits), top_score=hits[0].score if hits else 0.0)
    logger.debug("medic_retrieval_nodes", component=COMPONENT, correlation_id=correlation_id,
                 node_ids=[h.node.id for h in hits])
    if not hits:
        return "unknown", copy["unknown"].format(docs=docs)
    context, context_redactions = build_context_pack(tree, hits)
    if context_redactions:
        logger.warning("medic_context_redacted", component=COMPONENT,
                       correlation_id=correlation_id, outcome="partial",
                       redaction_count=context_redactions)
    result, reason = ask_models(question, context=context, language=language, llm=llm,
                                llm_fallback=llm_fallback, correlation_id=correlation_id)
    if result is not None and result.status == "not_in_document":
        return "unknown", copy["unknown"].format(docs=docs)
    if result is not None:
        return "answered", _format_answer(tree, result, language, docs)
    logger.warning("medic_extractive_fallback", component=COMPONENT,
                   correlation_id=correlation_id, outcome="partial", reason=reason)
    return "extractive", _format_extractive(tree, hits, language, docs)


def _doc_labels(tree: KnowledgeTree, language: str) -> str:
    labels = [d.label_zh if language == "zh" else d.label_en for d in tree.documents]
    if not labels:
        return "文件" if language == "zh" else "the document"
    joiner = "、" if language == "zh" else " and "
    text = joiner.join(labels)
    return text if language == "zh" else f"the {text}"


def _format_answer(tree: KnowledgeTree, result: MedicAnswer, language: str, docs: str) -> str:
    copy = COPY[language]
    sources: list[str] = []
    for node_id in result.citations:
        node = tree.node(node_id)
        if node is None:
            continue
        label = citation_label(tree, node, language)
        if label not in sources:
            sources.append(label)
        if len(sources) == MAX_SOURCES:
            break
    lines = [result.answer.strip(), ""]
    separator = "：" if language == "zh" else ": "
    lines += [f"{copy['sources']}{separator}{s}" for s in sources]
    lines.append(copy["scope"].format(docs=docs))
    return "\n".join(lines)


def _format_extractive(
    tree: KnowledgeTree, hits: Sequence[Hit], language: str, docs: str
) -> str:
    copy = COPY[language]
    lines = [copy["fallback_intro"].format(docs=docs)]
    for hit in hits[:MAX_FALLBACK_HITS]:
        node = hit.node
        parts = list(node.bullets[:3])
        if not _has_amounts(node):
            # A bullet inside a shared table cell: show the group's amount too.
            valued = next((a for a in tree.ancestors(node) if _has_amounts(a)), None)
            if valued is not None:
                amounts = [b for b in valued.bullets if not b.startswith(("Condition", "See notes"))]
                parts.append(f"{valued.display_title(language)}: {'; '.join(amounts)}")
        detail = "; ".join(parts) or node.summary
        lines.append(f"• {citation_label(tree, node, language)} — {detail}")
    lines.append(copy["scope"].format(docs=docs))
    return "\n".join(lines)


def _has_amounts(node: KnowledgeNode) -> bool:
    return bool(node.in_network or node.out_of_network or node.both_networks)


@dataclass(slots=True)
class MedicResponder:
    """Listener-facing callable: tree cache + models bound once at start."""

    cache: KnowledgeTreeCache
    llm: MedicLlm | None = None
    llm_fallback: MedicLlm | None = None
    retrieve_fn: RetrieveFn = retrieve

    def __call__(self, message: InboundMessage, *, correlation_id: str | None = None) -> ListenerResult:
        tree = self.cache.get()
        return handle_medic_message(
            message, tree=tree, llm=self.llm, llm_fallback=self.llm_fallback,
            retrieve_fn=self.retrieve_fn, correlation_id=correlation_id,
            kb_source=self.cache.source,
        )


def load_medic_responder(env: Mapping[str, str] | None = None) -> MedicResponder:
    """Build the runtime responder from the environment (no network calls)."""
    cache = default_tree_cache(env)
    llm, llm_fallback = load_live_medic_llms(env)
    tree = cache.get()
    logger.info("medic_configured", component=COMPONENT,
                outcome="success" if tree is not None else "partial",
                kb_source=cache.source, node_count=len(tree.nodes) if tree else 0,
                answer_mode="llm" if llm is not None else "extractive_only")
    return MedicResponder(cache=cache, llm=llm, llm_fallback=llm_fallback)
