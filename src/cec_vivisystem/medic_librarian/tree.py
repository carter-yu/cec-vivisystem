"""Knowledge tree contract for the medic librarian (Phase 32, ADR 0017).

Why: answers must come only from the insurer's document. The offline build
turns the document into small, page-referenced nodes once; runtime retrieves
nodes and never reads the PDF. A human can review ``tree.md`` before use.

Contract: ``tree.json`` uses schema ``cec-medic-kb/v1``. Every text field is
already redacted by the build. Runtime prefers the data-dir tree
(``data/agent_librarian/jeff-aia-medic/`` or ``CEC_MEDIC_KB_DIR``) and falls
back to the tracked seed in ``knowledge/jeff-aia-medic/``.

Do not put the PDF, raw extracted text or unredacted IDs next to the tree.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

from cec_vivisystem.logging import get_logger
from cec_vivisystem.storage import atomic_write_text

logger = get_logger(__name__)

COMPONENT = "medic_librarian"
SCHEMA = "cec-medic-kb/v1"
KB_NAME = "jeff-aia-medic"
TREE_FILE = "tree.json"
MARKDOWN_FILE = "tree.md"
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Display names and fixed order. Chinese is Traditional (HK) only.
CATEGORY_TITLES: dict[str, tuple[str, str]] = {
    "hospitalisation": ("Hospitalisation", "住院"),
    "outpatient": ("Outpatient", "門診"),
    "maternity": ("Maternity", "分娩"),
    "dental": ("Dental", "牙科"),
    "assistance": ("Assistance services", "支援服務"),
    "other": ("Other benefits", "其他福利"),
    "limits": ("Limits and maxima", "最高賠償額"),
    "notes": ("Notes and footnotes", "備註"),
    "general": ("General information", "一般資料"),
    "claims": ("Claims", "索償"),
    "exclusions": ("Exclusions", "不保事項"),
    "definitions": ("Definitions", "釋義"),
    "policy": ("Policy text", "保單條款"),
}


class KnowledgeTreeError(ValueError):
    """Tree file is missing, unreadable or not schema v1 (no content in message)."""


@dataclass(slots=True)
class KnowledgeNode:
    """One page-referenced fact group. ``source_quote`` is short and redacted."""

    id: str
    doc_id: str
    category: str
    kind: str  # section | heading | item | note | general | chunk
    title_en: str
    title_zh: str
    page: int
    summary: str = ""
    section: str = ""
    parent_id: str | None = None
    codes: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)
    in_network: list[str] = field(default_factory=list)
    out_of_network: list[str] = field(default_factory=list)
    both_networks: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    notes: list[int] = field(default_factory=list)
    inferred: list[str] = field(default_factory=list)
    note_number: int | None = None
    pinned: bool = False
    source_quote: str = ""

    def display_title(self, language: str = "en") -> str:
        """Bilingual title with codes; Chinese first for ``zh``."""
        en = self.title_en
        if self.codes:
            en = f"{en} ({'/'.join(self.codes)})" if en else f"({'/'.join(self.codes)})"
        parts = [self.title_zh, en] if language == "zh" else [en, self.title_zh]
        return " ".join(part for part in parts if part).strip()


@dataclass(slots=True)
class KnowledgeCategory:
    id: str
    title_en: str
    title_zh: str
    summary: str
    node_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SourceDocument:
    doc_id: str
    label_en: str
    label_zh: str
    pages: int
    text_sha256: str


@dataclass(slots=True)
class KnowledgeTree:
    kb_id: str
    built_at: str
    builder: str
    documents: list[SourceDocument]
    categories: list[KnowledgeCategory]
    nodes: list[KnowledgeNode]
    redaction_count: int = 0
    warnings: list[str] = field(default_factory=list)
    schema: str = SCHEMA
    _index: dict[str, KnowledgeNode] = field(default_factory=dict, repr=False, compare=False)

    def node(self, node_id: str) -> KnowledgeNode | None:
        if len(self._index) != len(self.nodes):
            self._index = {n.id: n for n in self.nodes}
        return self._index.get(node_id)

    def document(self, doc_id: str) -> SourceDocument | None:
        return next((d for d in self.documents if d.doc_id == doc_id), None)

    def category(self, category_id: str) -> KnowledgeCategory | None:
        return next((c for c in self.categories if c.id == category_id), None)

    def ancestors(self, node: KnowledgeNode) -> list[KnowledgeNode]:
        """Parent chain, nearest first (cycle-safe)."""
        chain: list[KnowledgeNode] = []
        seen = {node.id}
        current = node
        while current.parent_id and current.parent_id not in seen:
            parent = self.node(current.parent_id)
            if parent is None:
                break
            chain.append(parent)
            seen.add(parent.id)
            current = parent
        return chain

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "kb_id": self.kb_id,
            "built_at": self.built_at,
            "builder": self.builder,
            "redaction_count": self.redaction_count,
            "documents": [asdict(d) for d in self.documents],
            "categories": [asdict(c) for c in self.categories],
            "nodes": [asdict(n) for n in self.nodes],
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, raw: object) -> KnowledgeTree:
        if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
            raise KnowledgeTreeError("Knowledge tree is not schema cec-medic-kb/v1")
        try:
            tree = cls(
                kb_id=str(raw["kb_id"]),
                built_at=str(raw.get("built_at", "")),
                builder=str(raw.get("builder", "")),
                documents=[SourceDocument(**d) for d in raw["documents"]],
                categories=[KnowledgeCategory(**c) for c in raw["categories"]],
                nodes=[KnowledgeNode(**n) for n in raw["nodes"]],
                redaction_count=int(raw.get("redaction_count", 0)),
                warnings=[str(w) for w in raw.get("warnings", [])],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise KnowledgeTreeError(
                f"Knowledge tree has invalid fields ({type(exc).__name__})"
            ) from exc
        ids = [n.id for n in tree.nodes]
        if len(ids) != len(set(ids)):
            raise KnowledgeTreeError("Knowledge tree has duplicate node ids")
        return tree


def default_data_kb_dir() -> Path:
    """``<repo>/data/agent_librarian/jeff-aia-medic`` (gitignored)."""
    return _REPO_ROOT / "data" / "agent_librarian" / KB_NAME


def seed_kb_dir() -> Path:
    """Tracked sanitized seed: ``<repo>/knowledge/jeff-aia-medic``."""
    return _REPO_ROOT / "knowledge" / KB_NAME


def resolve_kb_dir(env: Mapping[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    override = (source.get("CEC_MEDIC_KB_DIR") or "").strip()
    return Path(override).expanduser() if override else default_data_kb_dir()


def load_tree(path: Path) -> KnowledgeTree:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise KnowledgeTreeError("Knowledge tree file not found") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KnowledgeTreeError(
            f"Knowledge tree file unreadable ({type(exc).__name__})"
        ) from exc
    return KnowledgeTree.from_dict(raw)


def save_tree(tree: KnowledgeTree, out_dir: Path, *, markdown: bool = True) -> list[Path]:
    """Atomically write ``tree.json`` (+ ``tree.md``); returns written paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [out_dir / TREE_FILE]
    atomic_write_text(
        written[0], json.dumps(tree.to_dict(), ensure_ascii=False, indent=2) + "\n"
    )
    if markdown:
        written.append(out_dir / MARKDOWN_FILE)
        atomic_write_text(written[1], render_markdown(tree))
    return written


def render_markdown(tree: KnowledgeTree) -> str:
    """Human-review view of the tree (same redacted content as the JSON)."""
    lines = [
        f"# Knowledge tree: {tree.kb_id}",
        "",
        (f"Schema `{tree.schema}` · built {tree.built_at} · {tree.builder} · "
         f"redactions {tree.redaction_count}"),
        "",
    ]
    for doc in tree.documents:
        lines.append(f"- Document `{doc.doc_id}`: {doc.label_en} {doc.label_zh} ({doc.pages} pages)")
    if tree.warnings:
        lines += ["", "## Build warnings (review against the PDF)", ""]
        lines += [f"- {w}" for w in tree.warnings]
    for category in tree.categories:
        lines += ["", f"## {category.title_en} {category.title_zh} (`{category.id}`)", ""]
        if category.summary:
            lines += [category.summary, ""]
        for node_id in category.node_ids:
            node = tree.node(node_id)
            if node is None:
                continue
            path = " › ".join(a.display_title() for a in reversed(tree.ancestors(node)))
            lines.append(f"### {node.display_title()} — p.{node.page}")
            lines.append(f"`{node.id}`" + (f" · under {path}" if path else ""))
            lines.append("")
            lines += [f"- {b}" for b in node.bullets]
            lines += [f"- Inferred: {i}" for i in node.inferred]
            if node.source_quote:
                lines.append(f"- Quote: “{node.source_quote}”")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


class KnowledgeTreeCache:
    """Serve the data-dir tree (reloaded on mtime change), else the seed.

    Why: Carter rebuilds the tree on the Mini; the listener should pick it up
    without a restart. A corrupt data tree logs an error and the seed answers.
    """

    def __init__(
        self,
        data_path: Path,
        seed_path: Path | None,
        *,
        loader: Callable[[Path], KnowledgeTree] = load_tree,
    ) -> None:
        self.data_path = data_path
        self.seed_path = seed_path
        self._loader = loader
        self._data_tree: KnowledgeTree | None = None
        self._data_stamp: tuple[int, int] | None = None
        self._failed_stamp: tuple[int, int] | None = None
        self._seed_tree: KnowledgeTree | None = None
        self._seed_failed = False
        self.source: str | None = None

    def get(self) -> KnowledgeTree | None:
        try:
            stat = self.data_path.stat()
            # Size as well as mtime: two writes can share one mtime tick.
            stamp: tuple[int, int] | None = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = None
        if stamp is not None and stamp != self._failed_stamp:
            if self._data_tree is not None and stamp == self._data_stamp:
                self.source = "data"
                return self._data_tree
            try:
                self._data_tree = self._loader(self.data_path)
                self._data_stamp = stamp
                self.source = "data"
                logger.info("medic_kb_loaded", component=COMPONENT, outcome="success",
                            kb_source="data", node_count=len(self._data_tree.nodes))
                return self._data_tree
            except KnowledgeTreeError as exc:
                self._data_tree = None
                self._data_stamp = None
                self._failed_stamp = stamp  # log once per bad file, then serve the seed
                logger.error("medic_kb_load_failed", component=COMPONENT, outcome="failure",
                             kb_source="data", error_type=type(exc).__name__,
                             error_message=str(exc))
        return self._seed()

    def _seed(self) -> KnowledgeTree | None:
        if self._seed_tree is None and not self._seed_failed and self.seed_path is not None:
            try:
                self._seed_tree = self._loader(self.seed_path)
                logger.info("medic_kb_loaded", component=COMPONENT, outcome="success",
                            kb_source="seed", node_count=len(self._seed_tree.nodes))
            except KnowledgeTreeError as exc:
                self._seed_failed = True
                logger.error("medic_kb_load_failed", component=COMPONENT, outcome="failure",
                             kb_source="seed", error_type=type(exc).__name__,
                             error_message=str(exc))
        self.source = "seed" if self._seed_tree is not None else None
        return self._seed_tree


def default_tree_cache(env: Mapping[str, str] | None = None) -> KnowledgeTreeCache:
    return KnowledgeTreeCache(
        resolve_kb_dir(env) / TREE_FILE, seed_kb_dir() / TREE_FILE
    )
