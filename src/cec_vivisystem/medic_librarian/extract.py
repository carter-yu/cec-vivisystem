"""PDF/text → pages for the offline build (Phase 32, ADR 0017).

Why ``pdftotext -layout`` (poppler) and not a Python PDF library: the layout
mode keeps the benefit table's column positions, which the organizer needs to
tell in-network from out-of-network amounts, and it adds no dependency to the
listener. On the Mini install it with ``brew install poppler`` or extract the
text elsewhere and use ``--from-text``.

Contract: pages split on form feed (``\\f``), 1-based page numbers by position.
The runner is injectable; tests never execute ``pdftotext``.
Do not save the raw text: callers redact before writing anything.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path

PdfRunner = Callable[[Path], str]


class ExtractionError(RuntimeError):
    """PDF text extraction failed (message names the step, never document text)."""


def pdftotext_runner(path: Path) -> str:
    """Run ``pdftotext -layout -enc UTF-8 <pdf> -`` and return stdout."""
    binary = shutil.which("pdftotext")
    if binary is None:
        raise ExtractionError(
            "pdftotext not found. Install poppler (macOS: brew install poppler) "
            "or extract the text and use --from-text."
        )
    try:
        completed = subprocess.run(
            [binary, "-layout", "-enc", "UTF-8", str(path), "-"],
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExtractionError(f"pdftotext failed ({type(exc).__name__})") from exc
    if completed.returncode != 0:
        raise ExtractionError(f"pdftotext exited with code {completed.returncode}")
    return completed.stdout.decode("utf-8", errors="replace")


def split_pages(text: str) -> list[str]:
    """Form-feed pages; a trailing empty page from the final ``\\f`` is dropped."""
    pages = text.replace("\r\n", "\n").split("\f")
    while pages and not pages[-1].strip():
        pages.pop()
    return pages


def expand_pdf_inputs(paths: Iterable[Path]) -> list[Path]:
    """Files as given; a directory contributes its ``*.pdf`` files (sorted)."""
    result: list[Path] = []
    for path in paths:
        if path.is_dir():
            result.extend(sorted(p for p in path.iterdir() if p.suffix.lower() == ".pdf"))
        else:
            result.append(path)
    return result


def unique_texts(
    sources: Iterable[tuple[str, str]],
) -> tuple[list[tuple[str, str]], int]:
    """Drop byte-identical extracted texts (the Mini has identical PDF copies).

    Returns ``(kept [(name, text)], duplicate_count)``.
    """
    kept: list[tuple[str, str]] = []
    seen: set[str] = set()
    duplicates = 0
    for name, text in sources:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest in seen:
            duplicates += 1
            continue
        seen.add(digest)
        kept.append((name, text))
    return kept, duplicates
