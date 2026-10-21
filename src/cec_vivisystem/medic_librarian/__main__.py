"""Medic librarian CLI (Phase 32, ADR 0017). Offline: no Slack, no model calls.

    python -m cec_vivisystem.medic_librarian build --pdf agent_librarian/Jeff-AIA-Medic/
    python -m cec_vivisystem.medic_librarian build --from-text benefit-summary.txt
    python -m cec_vivisystem.medic_librarian search "How much physiotherapy can I claim?"
    python -m cec_vivisystem.medic_librarian install-seed

``build`` writes only the redacted tree (``tree.json`` + ``tree.md``) to
``--out`` (default ``CEC_MEDIC_KB_DIR`` or ``data/agent_librarian/jeff-aia-medic``).
It never copies the PDF or the raw text. Exit codes: 0 ok, 1 nothing built,
2 usage or extraction error.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cec_vivisystem.logging import get_logger
from cec_vivisystem.medic_librarian.extract import (
    ExtractionError,
    PdfRunner,
    expand_pdf_inputs,
    pdftotext_runner,
    unique_texts,
)
from cec_vivisystem.medic_librarian.organize import build_tree
from cec_vivisystem.medic_librarian.retrieve import citation_label, retrieve
from cec_vivisystem.medic_librarian.tree import (
    MARKDOWN_FILE,
    TREE_FILE,
    KnowledgeTreeCache,
    resolve_kb_dir,
    save_tree,
    seed_kb_dir,
)
from cec_vivisystem.redaction import redact_sensitive

logger = get_logger(__name__)

COMPONENT = "medic_librarian"
FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")


def main(
    argv: Sequence[str] | None = None,
    *,
    pdf_runner: PdfRunner = pdftotext_runner,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m cec_vivisystem.medic_librarian")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="build the redacted knowledge tree")
    build.add_argument("--pdf", action="append", type=Path, default=[],
                       help="PDF file or directory of PDFs (repeatable; duplicates skipped)")
    build.add_argument("--from-text", action="append", type=Path, default=[],
                       help="pdftotext -layout output (repeatable)")
    build.add_argument("--out", type=Path, help="output directory")
    build.add_argument("--no-markdown", action="store_true", help="skip tree.md")
    search = commands.add_parser("search", help="preview retrieval for a question (no model)")
    search.add_argument("question")
    search.add_argument("--kb", type=Path, help="directory with tree.json")
    seed = commands.add_parser("install-seed", help="copy the tracked seed tree to the data dir")
    seed.add_argument("--out", type=Path, help="output directory")
    seed.add_argument("--force", action="store_true", help="replace an existing tree")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    if args.command == "build":
        return _build(args, pdf_runner=pdf_runner, now=now, env=env)
    if args.command == "search":
        return _search(args, env=env)
    return _install_seed(args, env=env)


def _build(args, *, pdf_runner: PdfRunner, now: datetime | None,
           env: Mapping[str, str] | None) -> int:
    if not args.pdf and not args.from_text:
        print("error: give --pdf and/or --from-text", file=sys.stderr)
        return 2
    sources: list[tuple[str, str]] = []
    try:
        for index, path in enumerate(expand_pdf_inputs(args.pdf), start=1):
            sources.append((f"pdf-{index}", pdf_runner(path)))
        for index, path in enumerate(args.from_text, start=1):
            sources.append((f"text-{index}", path.read_text(encoding="utf-8")))
    except (ExtractionError, OSError, UnicodeDecodeError) as exc:
        message, _ = redact_sensitive(str(exc))
        logger.error("medic_build_failed", component=COMPONENT, outcome="failure",
                     error_type=type(exc).__name__, error_message=message)
        print(f"error: {message}", file=sys.stderr)
        return 2
    if not sources:
        print("error: no PDF files found", file=sys.stderr)
        return 2
    kept, duplicates = unique_texts(sources)
    moment = now or datetime.now(tz=FAMILY_TZ)
    result = build_tree(kept, now=moment)
    out_dir = args.out or resolve_kb_dir(env)
    if not result.tree.nodes:
        logger.error("medic_build_empty", component=COMPONENT, outcome="failure",
                     inputs=len(sources), warnings=len(result.tree.warnings))
        print("\n".join(["no knowledge nodes were built; nothing written", *result.report]))
        return 1
    written = save_tree(result.tree, out_dir, markdown=not args.no_markdown)
    logger.info("medic_build_succeeded", component=COMPONENT, outcome="success",
                inputs=len(sources), duplicates=duplicates, node_count=len(result.tree.nodes),
                redaction_count=result.tree.redaction_count,
                warnings=len(result.tree.warnings))
    print(f"inputs: {len(sources)} (identical copies skipped: {duplicates})")
    print("\n".join(result.report))
    print("written: " + ", ".join(str(p) for p in written))
    print(f"review {MARKDOWN_FILE} against the PDF; the listener reloads {TREE_FILE} "
          "on its next medic message.")
    return 0


def _search(args, *, env: Mapping[str, str] | None) -> int:
    kb_dir = args.kb or resolve_kb_dir(env)
    cache = KnowledgeTreeCache(kb_dir / TREE_FILE, seed_kb_dir() / TREE_FILE)
    tree = cache.get()
    if tree is None:
        print("error: no knowledge tree (build one or run install-seed)", file=sys.stderr)
        return 2
    question, redactions = redact_sensitive(args.question)
    hits = retrieve(tree, question)
    print(f"tree: {cache.source}; redactions in question: {redactions}; hits: {len(hits)}")
    for hit in hits:
        print(f"  {hit.score:7.3f}  {citation_label(tree, hit.node, 'en')}  [{hit.node.id}]")
    if not hits:
        print("  (no match: the bot would answer 'not in this document')")
    return 0


def _install_seed(args, *, env: Mapping[str, str] | None) -> int:
    out_dir = args.out or resolve_kb_dir(env)
    target = out_dir / TREE_FILE
    if target.exists() and not args.force:
        print(f"error: {target} exists; use --force to replace it", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in (TREE_FILE, MARKDOWN_FILE):
        source = seed_kb_dir() / name
        if source.exists():
            temp = out_dir / f".{name}.tmp"
            shutil.copyfile(source, temp)
            temp.replace(out_dir / name)
    logger.info("medic_seed_installed", component=COMPONENT, outcome="success")
    print(f"seed installed: {target}")
    return 0


def _cli() -> None:
    from dotenv import load_dotenv

    from cec_vivisystem.logging import setup_logging

    load_dotenv()
    setup_logging(level=os.environ.get("LOG_LEVEL", "INFO"),
                  enable_file_logging=False, run_retention=False)
    raise SystemExit(main())


if __name__ == "__main__":
    _cli()
