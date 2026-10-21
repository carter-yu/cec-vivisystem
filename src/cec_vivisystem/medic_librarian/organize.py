"""Offline organizer: redacted ``pdftotext -layout`` pages → knowledge tree.

Why deterministic rules and not a model: the build runs once per document and
its output is reviewed by a human (``tree.md``). Rules are repeatable, offline
and cannot invent a limit. The model only reads the finished tree at answer time.

Contract (benefit-table pages, i.e. pages with an In-Network / Out-of-Network
header):
- A value's network column is the nearest of the in-column centre, the
  out-column centre or their midpoint (a merged cell = both networks).
- Label lines group into items: EN line + ZH line, ``(``-details, lowercase
  wraps, and ``/`` alternatives (for example a Telemedicine line).
- A value-only line attaches to the centre of its EN/ZH or ``(…)`` pair, else
  to the nearest label line (tie → the label below).
- Merged-cell repairs are recorded in ``node.inferred`` and the build warnings:
  an ``N/A`` cell extends to adjacent rows that lack that column; a group
  heading without values takes the value of its only valued bullet.
Other pages: numbered remarks after a "Remarks" heading become ``notes``,
"Note:" bullets and paragraphs become ``general``, anything else becomes
``policy`` prose chunks (the later full policy starts here).

Do not feed unredacted text: ``build_tree`` redacts every page first.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from cec_vivisystem.medic_librarian.tree import (
    CATEGORY_TITLES,
    KB_NAME,
    KnowledgeCategory,
    KnowledgeNode,
    KnowledgeTree,
    SourceDocument,
)
from cec_vivisystem.redaction import redact_sensitive

BUILDER = "medic_librarian.organize v1"
QUOTE_MAX = 300
CHUNK_MAX = 900

_IDEOGRAPH = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_CJK_EDGE = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")
_SEGMENT = re.compile(r"\S+(?: \S+)*")
_FOOTNOTE_ONLY = re.compile(r"^\d{1,2}(?:,\d{1,2})*$")
# A trailing superscript reference sits right after Chinese text or a ")".
_TRAILING_FOOTNOTE = re.compile(
    r"(?<=[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff)）])\s*(\d{1,2}(?:,\d{1,2})*)$"
)
_BULLET = re.compile(r"^[●•▪◦]\s*")
_DASH = re.compile(r"^[-–]\s+")
_CODE = re.compile(r"\(([A-Z]{2,6})\)")
_NA = re.compile(r"^N/A\b", re.IGNORECASE)
_IN_HEADER = re.compile(r"^In-Network\b", re.IGNORECASE)
_OUT_HEADER = re.compile(r"^Out-of-Network\b", re.IGNORECASE)
_REMARKS_HEADING = re.compile(r"\bremarks?\b|備註", re.IGNORECASE)
_NOTE_HEADING = re.compile(r"^notes?\s*[:：]", re.IGNORECASE)
_NUMBERED = re.compile(r"^(\d{1,2})\.\s+(\S.*)$")
_DASH_BULLET = re.compile(r"^[-–•●]\s+(\S.*)$")
_TOP_SECTIONS = (
    ("hospitalisation", re.compile(r"^hospitali[sz]ation benefits?$")),
    ("outpatient", re.compile(r"^outpatient benefits?$")),
    ("other", re.compile(r"^other benefits?$")),
)
_SUB_SECTIONS = (
    ("maternity", re.compile(r"^maternity\b")),
    ("dental", re.compile(r"^dental benefits?$")),
    ("assistance", re.compile(r"\bassist(?:ance)? services?\b|\bevacuation\b")),
)
_LIMITS = re.compile(r"^overall maxim", re.IGNORECASE)
_PROSE_CATEGORIES = (
    ("exclusions", re.compile(r"exclusion|不保事項", re.IGNORECASE)),
    ("definitions", re.compile(r"definition|釋義", re.IGNORECASE)),
    ("claims", re.compile(r"\bclaims?\b|索償", re.IGNORECASE)),
)
_COLUMN_LABEL = {
    "both": "In-network and out-of-network 網絡及非網絡",
    "in": "In-network 網絡",
    "out": "Out-of-network 非網絡",
}


@dataclass(slots=True)
class BuildResult:
    tree: KnowledgeTree
    report: list[str]


@dataclass(slots=True)
class _Line:
    index: int
    label: str = ""
    values: list[tuple[str, str]] = field(default_factory=list)  # (column, text)
    footnotes: list[int] = field(default_factory=list)


@dataclass(slots=True)
class _Unit:
    en: str = ""
    zh: str = ""


@dataclass(slots=True)
class _Item:
    page: int
    marker: str  # bullet | dash | plain
    first_line: int
    units: list[_Unit] = field(default_factory=list)
    conditions: list[list[str]] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
    footnotes: list[int] = field(default_factory=list)
    label_lines: list[int] = field(default_factory=list)
    quote: list[tuple[int, str]] = field(default_factory=list)
    # column → [(line index, text)]; sorted by line when the node is written.
    values: dict[str, list[tuple[int, str]]] = field(
        default_factory=lambda: {"both": [], "in": [], "out": []}
    )
    inferred: list[str] = field(default_factory=list)
    paren_depth: int = 0
    alt_pending: bool = False
    last_role: str = "title"
    # Assigned by the hierarchy pass.
    kind: str = "item"
    category: str = "other"
    section: str = ""
    parent: _Item | None = None
    node_id: str = ""

    @property
    def needs_zh(self) -> bool:
        unit = self.units[-1]
        return bool(unit.en) and not unit.zh

    @property
    def title_en(self) -> str:
        return " / ".join(u.en for u in self.units if u.en)

    @property
    def title_zh(self) -> str:
        return " / ".join(u.zh for u in self.units if u.zh)

    def has_values(self) -> bool:
        return any(self.values.values())


# --- public entry -------------------------------------------------------------------


def build_tree(
    sources: Sequence[tuple[str, str]],
    *,
    now: datetime,
    kb_id: str = KB_NAME,
) -> BuildResult:
    """Organize extracted texts ``[(name, text)]`` into a redacted tree."""
    documents: list[SourceDocument] = []
    nodes: list[KnowledgeNode] = []
    warnings: list[str] = []
    redactions = 0
    used_ids: set[str] = set()
    used_doc_ids: set[str] = set()
    for position, (_name, raw_text) in enumerate(sources, start=1):
        pages: list[str] = []
        for page in _pages(raw_text):
            safe, count = redact_sensitive(page)
            redactions += count
            pages.append(safe)
        if not any(p.strip() for p in pages):
            warnings.append(f"document {position}: no text found (scanned PDF needs OCR)")
            continue
        doc = _document(pages, position, used_doc_ids)
        documents.append(doc)
        doc_nodes, doc_warnings = _organize_document(doc, pages, used_ids)
        nodes.extend(doc_nodes)
        warnings.extend(f"{doc.doc_id}: {w}" for w in doc_warnings)
    if not nodes:
        warnings.append("no knowledge nodes were produced")
    tree = KnowledgeTree(
        kb_id=kb_id,
        built_at=now.isoformat(timespec="seconds"),
        builder=BUILDER,
        documents=documents,
        categories=_categories(nodes),
        nodes=nodes,
        redaction_count=redactions,
        warnings=warnings,
    )
    return BuildResult(tree=tree, report=_report(tree))


def _pages(text: str) -> list[str]:
    from cec_vivisystem.medic_librarian.extract import split_pages

    return split_pages(text if isinstance(text, str) else "")


def _document(pages: list[str], position: int, used: set[str]) -> SourceDocument:
    first = next((ln.strip() for ln in pages[0].splitlines() if ln.strip()), "") if pages else ""
    if re.search(r"benefit summary", first, re.IGNORECASE):
        doc_id, label_en, label_zh = "benefit-summary", "Benefit Summary", "福利概要"
    elif re.search(r"\bpolicy\b|保單", "\n".join(pages[:1]), re.IGNORECASE):
        doc_id, label_en, label_zh = "policy", "Policy document", "保單文件"
    else:
        doc_id, label_en, label_zh = f"doc-{position}", f"Document {position}", f"文件 {position}"
    base, n = doc_id, 2
    while doc_id in used:
        doc_id, n = f"{base}-{n}", n + 1
    used.add(doc_id)
    digest = hashlib.sha256("\f".join(pages).encode("utf-8")).hexdigest()
    return SourceDocument(doc_id, label_en, label_zh, len(pages), digest)


# --- per document -------------------------------------------------------------------


def _organize_document(
    doc: SourceDocument, pages: list[str], used_ids: set[str]
) -> tuple[list[KnowledgeNode], list[str]]:
    warnings: list[str] = []
    title_line = next((ln.strip() for ln in pages[0].splitlines() if ln.strip()), "")
    table_items: list[_Item] = []
    text_nodes: list[KnowledgeNode] = []
    for page_no, page in enumerate(pages, start=1):
        lines = page.split("\n")
        header = _find_header(lines)
        if header is not None:
            items, page_warnings = _table_page(lines, header, page_no, title_line)
            table_items.extend(items)
            warnings.extend(page_warnings)
        else:
            text_nodes.extend(_text_page(lines, page_no, doc, title_line))
    _assign_hierarchy(table_items)
    warnings.extend(_hoist_group_values(table_items))
    warnings.extend(_extend_na_cells(table_items))
    note_numbers = {n.note_number for n in text_nodes if n.note_number is not None}
    dropped = 0
    for item in table_items:
        kept = [n for n in item.footnotes if n in note_numbers]
        dropped += len(item.footnotes) - len(kept)
        item.footnotes = kept
    if dropped:
        warnings.append(f"{dropped} footnote reference(s) without a matching remark were dropped")
    table_nodes = _item_nodes(table_items, doc, used_ids)
    for node in text_nodes:
        node.id = _unique(node.id, used_ids)
    return table_nodes + text_nodes, warnings


def _find_header(lines: list[str]) -> tuple[int, float, float, int] | None:
    """(line index, in-centre, out-centre, value threshold) of the table header."""
    for index, line in enumerate(lines):
        segments = [(m.start(), m.end(), m.group()) for m in _SEGMENT.finditer(line)]
        in_seg = next((s for s in segments if _IN_HEADER.match(s[2])), None)
        out_seg = next((s for s in segments if _OUT_HEADER.match(s[2])), None)
        if in_seg and out_seg and in_seg[0] < out_seg[0]:
            in_centre = (in_seg[0] + in_seg[1]) / 2
            out_centre = (out_seg[0] + out_seg[1]) / 2
            return index, in_centre, out_centre, max(in_seg[0] - 8, 20)
    return None


def _column(start: int, end: int, in_centre: float, out_centre: float) -> str:
    centre = (start + end) / 2
    distances = {
        "in": abs(centre - in_centre),
        "both": abs(centre - (in_centre + out_centre) / 2),
        "out": abs(centre - out_centre),
    }
    return min(distances, key=lambda k: (distances[k], k != "both"))


def _parse_lines(
    lines: list[str], header: tuple[int, float, float, int], title_line: str
) -> list[_Line]:
    header_index, in_centre, out_centre, threshold = header
    parsed: list[_Line] = []
    for index in range(header_index + 1, len(lines)):
        line = lines[index]
        if line.strip() == title_line:
            continue
        entry = _Line(index)
        labels: list[str] = []
        for match in _SEGMENT.finditer(line):
            text = match.group()
            if match.start() >= threshold:
                entry.values.append((_column(match.start(), match.end(), in_centre, out_centre), text))
            elif _FOOTNOTE_ONLY.match(text) and match.start() > 4:
                entry.footnotes.extend(int(n) for n in text.split(","))
            else:
                labels.append(text)
        entry.label = " ".join(labels)
        if entry.label or entry.values or entry.footnotes:
            parsed.append(entry)
    # A standalone superscript belongs to the label just below (else above).
    for position, entry in enumerate(parsed):
        if entry.footnotes and not entry.label:
            target = next((e for e in parsed[position + 1:position + 2] if e.label), None)
            if target is None:
                target = next((e for e in reversed(parsed[:position]) if e.label), None)
            if target is not None:
                target.footnotes.extend(entry.footnotes)
                entry.footnotes = []
    return parsed


def _table_page(
    lines: list[str], header: tuple[int, float, float, int], page_no: int, title_line: str
) -> tuple[list[_Item], list[str]]:
    parsed = _parse_lines(lines, header, title_line)
    items: list[_Item] = []
    line_item: dict[int, _Item] = {}
    current: _Item | None = None
    for entry in parsed:
        if not entry.label:
            continue
        text, footnotes, alternative = _clean_label(entry.label)
        footnotes += entry.footnotes
        if not text:
            continue
        marker = "bullet" if _BULLET.match(text) else "dash" if _DASH.match(text) else "plain"
        body = _DASH.sub("", _BULLET.sub("", text)).strip()
        if _starts_new_item(current, body, marker):
            current = _Item(page=page_no, marker=marker, first_line=entry.index)
            current.units.append(_Unit())
            items.append(current)
            role = "title"
        else:
            role = _continuation_role(current, body)
        _apply_line(current, body, role)
        current.footnotes.extend(n for n in footnotes if n not in current.footnotes)
        current.alt_pending = current.alt_pending or alternative
        current.label_lines.append(entry.index)
        current.quote.append((entry.index, body))
        line_item[entry.index] = current
        for column, value in entry.values:
            current.values[column].append((entry.index, value))
            current.quote.append((entry.index, value))
    warnings = _attach_value_lines(parsed, line_item, page_no)
    for item in items:
        item.quote.sort(key=lambda pair: pair[0])
    return items, warnings


def _clean_label(label: str) -> tuple[str, list[int], bool]:
    text = label.strip()
    alternative = text.endswith("/")
    if alternative:
        text = text[:-1].rstrip()
    footnotes: list[int] = []
    match = _TRAILING_FOOTNOTE.search(text)
    if match:
        footnotes = [int(n) for n in match.group(1).split(",")]
        text = text[: match.start()].rstrip()
    return text, footnotes, alternative


def _starts_new_item(current: _Item | None, body: str, marker: str) -> bool:
    if current is None:
        return True
    if current.paren_depth > 0:
        return False
    if marker != "plain":
        return True
    first = body[0]
    if first in "(（" or first.islower() or _IDEOGRAPH.match(first):
        return False
    if current.alt_pending:
        return False
    return not (current.needs_zh and not _IDEOGRAPH.search(body))


def _continuation_role(current: _Item, body: str) -> str:
    if current.paren_depth > 0:
        return current.last_role
    first = body[0]
    if first in "(（":
        return "detail"
    if first.islower():
        return current.last_role
    if _IDEOGRAPH.match(first):
        return "zh" if current.needs_zh else "detail"
    if current.alt_pending:
        return "alt"
    return "title"


def _apply_line(item: _Item, body: str, role: str) -> None:
    item.paren_depth = max(
        0, item.paren_depth + body.count("(") + body.count("（") - body.count(")") - body.count("）")
    )
    if role == "detail":
        if item.last_role == "detail" and item.conditions:
            item.conditions[-1].append(body)
        else:
            item.conditions.append([body])
        item.last_role = "detail"
        return
    if role == "alt":
        item.units.append(_Unit())
        item.alt_pending = False
        role = "title"
    for code in _CODE.findall(body):
        if code not in item.codes:
            item.codes.append(code)
    body = re.sub(r"\s{2,}", " ", _CODE.sub("", body)).strip()
    unit = item.units[-1]
    if role == "zh":
        unit.zh = _join([unit.zh, body])
    else:
        match = _IDEOGRAPH.search(body)
        en, zh = (body[: match.start()].strip(), body[match.start():].strip()) if match else (body, "")
        unit.en = _join([unit.en, en])
        if zh:
            unit.zh = _join([unit.zh, zh])
    item.last_role = role


def _attach_value_lines(
    parsed: list[_Line], line_item: dict[int, _Item], page_no: int
) -> list[str]:
    warnings: list[str] = []
    value_only = [e for e in parsed if e.values and not e.label]
    blocks: list[list[_Line]] = []
    for entry in value_only:
        if blocks and _continues_block(blocks[-1][-1], entry):
            blocks[-1].append(entry)
        else:
            blocks.append([entry])
    label_indexes = sorted(line_item)
    for block in blocks:
        if not label_indexes:
            warnings.append(f"page {page_no}: values without any label were skipped")
            continue
        centre = (block[0].index + block[-1].index) / 2
        best = min(label_indexes, key=lambda i: (abs(i - centre), -i))
        distance = abs(best - centre)
        others = [i for i in label_indexes if abs(i - centre) == distance and i != best]
        item = line_item[best]
        if any(line_item[i] is not item for i in others):
            warnings.append(
                f"page {page_no}: a value line was attached to "
                f"'{item.title_en or item.title_zh}' by tie-break (label below)"
            )
        if distance > 2:
            warnings.append(f"page {page_no}: a value line is {distance:g} lines from its label")
        for entry in block:
            for column, value in entry.values:
                item.values[column].append((entry.index, value))
                item.quote.append((entry.index, value))
    return warnings


def _continues_block(previous: _Line, entry: _Line) -> bool:
    if entry.index - previous.index > 2:
        return False
    if len(previous.values) != 1 or len(entry.values) != 1:
        return False
    (prev_column, prev_text), (column, text) = previous.values[0], entry.values[0]
    if prev_column != column:
        return False
    if text.startswith(("(", "（")):
        return True
    return not _IDEOGRAPH.search(prev_text) and bool(_IDEOGRAPH.search(text))


# --- hierarchy and merged-cell repairs ----------------------------------------------


def _section_of(item: _Item) -> tuple[str, str] | None:
    title = item.title_en.lower().strip()
    for name, pattern in _TOP_SECTIONS:
        if pattern.search(title):
            return "top", name
    for name, pattern in _SUB_SECTIONS:
        if pattern.search(title):
            return "sub", name
    return None


def _assign_hierarchy(items: list[_Item]) -> None:
    top: _Item | None = None
    sub: _Item | None = None
    group: _Item | None = None
    bullet: _Item | None = None
    category = "other"
    for position, item in enumerate(items):
        section = _section_of(item) if item.marker == "plain" else None
        if section is not None and section[0] == "top":
            top, sub, group, bullet, category = item, None, None, None, section[1]
            item.kind, item.parent = "section", None
        elif section is not None:
            sub, group, bullet, category = item, None, None, section[1]
            item.kind, item.parent = "section", top
        elif item.marker == "plain":
            item.parent = sub or top
            following = items[position + 1] if position + 1 < len(items) else None
            is_group = following is not None and following.marker == "bullet"
            item.kind = "heading" if is_group else "item"
            group, bullet = (item if is_group else None), None
        elif item.marker == "bullet":
            item.parent = group or sub or top
            bullet = item
        else:
            item.parent = bullet or group or sub or top
        item.category = "limits" if _LIMITS.match(item.title_en) else category
        item.section = " › ".join(
            s.title_en for s in (top, sub) if s is not None and s is not item
        )


def _hoist_group_values(items: list[_Item]) -> list[str]:
    """One value cell spanning a bullet group lands on one bullet; give it to the group."""
    warnings: list[str] = []
    for heading in items:
        if heading.kind not in ("heading", "section") or heading.has_values():
            continue
        bullets = [i for i in items if i.parent is heading and i.marker == "bullet"]
        valued = [b for b in bullets if b.has_values()]
        # A bullet whose dash items carry values is a sub-table, not a merged cell.
        nested = any(i.parent in bullets and i.has_values() for i in items)
        if len(bullets) >= 3 and len(valued) == 1 and not nested:
            for column, values in valued[0].values.items():
                heading.values[column].extend(values)
            heading.inferred.append(
                "values cover every item in this group (one merged table cell)"
            )
            warnings.append(
                f"'{heading.title_en}': group value taken from its only valued item"
            )
    return warnings


def _extend_na_cells(items: list[_Item]) -> list[str]:
    """Extend a vertically merged ``N/A`` cell to neighbours lacking that column."""
    warnings: list[str] = []
    for column in ("in", "out"):
        sources = [
            i for i in items
            if len(i.values[column]) == 1 and _NA.match(i.values[column][0][1])
        ]
        for source in sources:
            na_value = source.values[column][0]
            index = items.index(source)
            for step in (-1, 1):
                position = index + step
                while 0 <= position < len(items):
                    other = items[position]
                    if _top_section(other) is not _top_section(source) or other.values["both"]:
                        break
                    if other.values[column]:
                        break
                    if other.has_values():
                        other.values[column].append(na_value)
                        other.inferred.append(
                            f"{'in-network' if column == 'in' else 'out-of-network'} "
                            "N/A comes from a merged table cell"
                        )
                        warnings.append(
                            f"'{other.title_en}': {column}-network N/A from a merged cell"
                        )
                    position += step
    return warnings


def _top_section(item: _Item) -> _Item | None:
    current: _Item | None = item
    while current is not None and current.parent is not None:
        current = current.parent
    return current


# --- nodes ---------------------------------------------------------------------------


def _item_nodes(
    items: list[_Item], doc: SourceDocument, used_ids: set[str]
) -> list[KnowledgeNode]:
    nodes: list[KnowledgeNode] = []
    for item in items:
        slug = _slug(" ".join([item.title_en, *item.codes]))
        if item.marker == "dash" and item.parent is not None and slug:
            # Short parent prefix keeps "operating-room-minor-operation" distinct
            # from "surgeons-fee-minor-operation" without truncation collisions.
            slug = f"{_slug(item.parent.title_en)[:24].rstrip('-')}-{slug}"
        slug = slug or f"p{item.page}-l{item.first_line}"
        item.node_id = _unique(f"{item.category}.{slug}", used_ids)
    for item in items:
        conditions = [_join(group) for group in item.conditions]
        node = KnowledgeNode(
            id=item.node_id,
            doc_id=doc.doc_id,
            category=item.category,
            kind=item.kind,
            title_en=item.title_en,
            title_zh=item.title_zh,
            page=item.page,
            section=item.section,
            parent_id=item.parent.node_id if item.parent is not None else None,
            codes=list(item.codes),
            in_network=[text for _, text in sorted(item.values["in"])],
            out_of_network=[text for _, text in sorted(item.values["out"])],
            both_networks=[text for _, text in sorted(item.values["both"])],
            conditions=conditions,
            notes=sorted(item.footnotes),
            inferred=list(item.inferred),
            source_quote=_quote(text for _, text in item.quote),
        )
        node.bullets = _item_bullets(node)
        children = [i.title_en or i.title_zh for i in items if i.parent is item]
        node.summary = _item_summary(node, children)
        nodes.append(node)
    return nodes


def _item_bullets(node: KnowledgeNode) -> list[str]:
    bullets = []
    for column, values in (
        ("both", node.both_networks), ("in", node.in_network), ("out", node.out_of_network)
    ):
        if values:
            bullets.append(f"{_COLUMN_LABEL[column]}: {' / '.join(values)}")
    bullets += [f"Condition 條件: {c}" for c in node.conditions]
    if node.notes:
        bullets.append("See notes 參閱備註: " + ", ".join(str(n) for n in node.notes))
    return bullets


def _item_summary(node: KnowledgeNode, children: list[str]) -> str:
    title = node.display_title()
    if node.bullets and not node.bullets[0].startswith(("Condition", "See notes")):
        return _clip(f"{title}: {node.bullets[0]}", 240)
    if children:
        return _clip(f"{title}: contains {', '.join(children)}", 240)
    return title


def _text_page(
    lines: list[str], page_no: int, doc: SourceDocument, title_line: str
) -> list[KnowledgeNode]:
    """Remarks → notes; "Note:" bullets/paragraphs → general; else prose chunks."""
    mode = "prose"
    entries: list[tuple[str, int | None, list[str]]] = []
    current: tuple[str, int | None, list[str]] | None = None
    for raw in lines:
        text = raw.strip()
        if not text:
            if mode != "notes":
                current = None
            continue
        if text == title_line or re.fullmatch(r"\d{1,3}", text):
            continue
        if mode != "notes" and _REMARKS_HEADING.search(text) and len(text) < 80:
            mode, current = "notes", None
            continue
        if _NOTE_HEADING.match(text):
            mode, current = "general", None
            continue
        if re.search(r"\beffective from\b", text, re.IGNORECASE):
            entries.append(("effective", None, [text]))
            current = None
            continue
        numbered = _NUMBERED.match(text) if mode == "notes" else None
        bullet = _DASH_BULLET.match(text) if mode == "general" else None
        if numbered:
            current = ("note", int(numbered.group(1)), [numbered.group(2)])
            entries.append(current)
        elif bullet:
            current = ("general", None, [bullet.group(1)])
            entries.append(current)
        elif current is not None:
            current[2].append(text)
        else:
            kind = "general" if mode in ("general", "notes") else "prose"
            current = (kind, None, [text])
            entries.append(current)
    nodes: list[KnowledgeNode] = []
    prose: list[str] = []
    for kind, number, parts in entries:
        if kind == "prose":
            prose.append(_join(parts))
            continue
        nodes.append(_text_node(kind, number, _join(parts), page_no, doc))
    nodes.extend(_prose_nodes(prose, page_no, doc))
    return nodes


def _text_node(
    kind: str, number: int | None, text: str, page_no: int, doc: SourceDocument
) -> KnowledgeNode:
    en, zh = _split_en_zh(text)
    first = _first_sentence(en) or _clip(zh, 60)
    if kind == "note" and number is not None:
        return KnowledgeNode(
            id=f"notes.note-{number}", doc_id=doc.doc_id, category="notes", kind="note",
            title_en=f"Note {number}", title_zh=f"備註 {number}", page=page_no,
            summary=_clip(first, 200), bullets=[b for b in (en, zh) if b],
            note_number=number, source_quote=_quote([text]),
        )
    pinned = False
    if kind == "effective":
        title_en, title_zh = "Benefit effective period", "福利生效期"
    elif re.search(r"\bcurrency\b", en, re.IGNORECASE):
        title_en, title_zh, pinned = "Currency of all amounts", "金額貨幣", True
    elif re.search(r"final interpretation", en, re.IGNORECASE):
        title_en, title_zh = "Final interpretation", "最後詮釋"
    else:
        title_en, title_zh = _clip(first, 70), ""
    return KnowledgeNode(
        id=f"general.{_slug(title_en) or f'p{page_no}'}", doc_id=doc.doc_id,
        category="general", kind="general", title_en=title_en, title_zh=title_zh,
        page=page_no, summary=_clip(first, 200), bullets=[b for b in (en, zh) if b],
        pinned=pinned, source_quote=_quote([text]),
    )


def _prose_nodes(paragraphs: list[str], page_no: int, doc: SourceDocument) -> list[KnowledgeNode]:
    chunks: list[str] = []
    for paragraph in paragraphs:
        if chunks and len(chunks[-1]) + len(paragraph) < CHUNK_MAX:
            chunks[-1] = f"{chunks[-1]} {paragraph}"
        else:
            chunks.append(paragraph)
    nodes = []
    for index, chunk in enumerate(chunks, start=1):
        en, zh = _split_en_zh(chunk)
        category = next(
            (name for name, pattern in _PROSE_CATEGORIES if pattern.search(chunk)), "policy"
        )
        title_en = _clip(_first_sentence(en) or "", 80)
        nodes.append(KnowledgeNode(
            id=f"{category}.p{page_no}-{index}", doc_id=doc.doc_id, category=category,
            kind="chunk", title_en=title_en, title_zh=_clip(zh, 40) if not title_en else "",
            page=page_no, summary=_clip(chunk, 200), bullets=[_clip(chunk, CHUNK_MAX)],
            source_quote=_quote([chunk]),
        ))
    return nodes


def _categories(nodes: list[KnowledgeNode]) -> list[KnowledgeCategory]:
    result = []
    for category_id, (title_en, title_zh) in CATEGORY_TITLES.items():
        members = [n for n in nodes if n.category == category_id]
        if not members:
            continue
        pages = sorted({n.page for n in members})
        names = [n.title_en or n.title_zh for n in members if n.kind != "section"]
        summary = f"{len(members)} entries on page(s) {', '.join(map(str, pages))}"
        if names:
            more = " …" if len(names) > 8 else ""
            summary += ": " + ", ".join(names[:8]) + more
        result.append(KnowledgeCategory(
            category_id, title_en, title_zh, summary, [n.id for n in members]
        ))
    return result


def _report(tree: KnowledgeTree) -> list[str]:
    lines = [
        f"documents: {len(tree.documents)} "
        + ", ".join(f"{d.doc_id} ({d.pages} pages)" for d in tree.documents),
        f"nodes: {len(tree.nodes)}; redactions: {tree.redaction_count}",
    ]
    lines += [f"  {c.id}: {len(c.node_ids)}" for c in tree.categories]
    inferred = sum(1 for n in tree.nodes if n.inferred)
    lines.append(f"inferred merged-cell values: {inferred} node(s)")
    lines += [f"warning: {w}" for w in tree.warnings]
    return lines


# --- text helpers --------------------------------------------------------------------


def _join(parts: Sequence[str]) -> str:
    """Join wrapped lines; no space between two Chinese edges."""
    result = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if result and _CJK_EDGE.match(result[-1]) and _CJK_EDGE.match(part[0]):
            result += part
        elif result:
            result += " " + part
        else:
            result = part
    return result


def _split_en_zh(text: str) -> tuple[str, str]:
    match = _IDEOGRAPH.search(text)
    if not match:
        return text.strip(), ""
    # Keep an opening bracket/quote with the Chinese half.
    start = match.start()
    while start > 0 and text[start - 1] in "「『（([【":
        start -= 1
    return text[:start].strip(), text[start:].strip()


def _first_sentence(text: str) -> str:
    match = re.search(r"^(.+?[.!?])(?:\s|$)", text)
    return (match.group(1) if match else text).strip()


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _quote(parts) -> str:
    return _clip(" | ".join(re.sub(r"\s+", " ", p).strip() for p in parts if p.strip()), QUOTE_MAX)


def _slug(text: str) -> str:
    text = text.replace("’", "").replace("'", "")
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:56].rstrip("-")


def _unique(node_id: str, used: set[str]) -> str:
    candidate, n = node_id, 2
    while candidate in used:
        candidate, n = f"{node_id}-{n}", n + 1
    used.add(candidate)
    return candidate
