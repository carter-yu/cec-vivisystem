# ADR 0017: AIA Medic librarian — offline knowledge tree, redacted model answers

Date: 2026-10-22. Status: Accepted and implemented locally (Phase 32). Decisions
locked by Carter on 2026-10-22. Not deployed.

## Context

Carter wants quick answers about a family member's group medical plan in Slack
(`#jeff-aia-medic`), from the insurer's document only, with page references, and
"I do not know" when the document is silent. The current source is one 4-page
bilingual Benefit Summary (a benefit table and numbered remarks). The full policy
arrives later. The document carries a policy number; questions may carry IDs.

The ground rules keep this swarm "no LLM by default" and require an ADR before a
retriever or live model is added. ADR 0008 allowed a model only for Calendar create
extraction. This ADR is the explicit authorization for a second, separate use.

## Decision

1. **Channel-scoped route.** The existing listener and Slack app serve
   `CEC_MEDIC_CHANNEL_ID` (default `C0CB179V0AE`; `off` disables; a value equal to
   the plans or life-notes channel is refused with an error log). Messages there go
   only to `medic_librarian.handle_medic_message`: never `parse`, confirmations,
   Calendar read/write, important dates or life notes. A thread "yes" there resolves
   nothing. The medic route runs **outside** the Calendar dispatch lock (it shares no
   store with that path), so a slow model answer cannot hold up family-plans replies.
2. **Offline knowledge tree.** `python -m cec_vivisystem.medic_librarian build`
   turns `pdftotext -layout` output into a redacted tree (`tree.json`, schema
   `cec-medic-kb/v1`, plus `tree.md` for review): categories, nodes with EN/ZH
   titles, in-/out-of-network amounts, conditions, footnote numbers, page and a short
   quote. Deterministic rules, no model. Merged table cells are repaired by two
   recorded heuristics (an `N/A` cell extends to neighbouring rows that lack that
   column; a value on the only valued bullet of a 3+ bullet group belongs to the
   group). Repairs are listed in `node.inferred` and the build warnings.
3. **PDF extraction via poppler.** The build shells out to `pdftotext` (fixed argv,
   injectable runner). No new Python dependency; the listener never reads PDFs.
   `--from-text` accepts text extracted elsewhere. Identical PDF copies are skipped.
4. **Seed + data tree.** A sanitized tree built from the Benefit Summary is tracked in
   `knowledge/jeff-aia-medic/` so tests and the first deploy work without the PDF.
   Runtime prefers `CEC_MEDIC_KB_DIR` or `data/agent_librarian/jeff-aia-medic/`
   (gitignored), reloading it when the file changes; a corrupt data tree logs an
   error once and the seed answers. PDFs and raw text are never tracked.
5. **Shared redaction.** `redaction.redact_sensitive(text) -> (safe_text, count)`
   replaces long digit runs (8+), HKID, emails, phone numbers and labelled
   policy/member/card numbers with placeholders. It runs on every page at build, on
   the question before retrieval and the model, on the context pack again, and on
   the final reply. Logs carry counts only.
6. **Retrieval without embeddings.** English words, Chinese character bigrams and a
   small Cantonese/English alias table, IDF-weighted, injectable. No hit above the
   threshold → "not in this document" **without** a model call.
7. **Answer contract.** Same model policy as `parse_fallback` (primary then one other
   model, 15 s each, SDK retries off, non-reasoning SKU first). Strict JSON
   `{status, answer, citations}`. The application rejects answers that cite ids
   outside the context pack, cite nothing, contain Simplified Chinese, or contain an
   amount, percentage or 3+ digit number absent from the pack. Sources are rendered
   by code from the cited node ids (document, page, category, title), never from
   model text. On rejection, outage or no key: an extractive reply that quotes the
   matching tree lines with their pages.
8. **No storage.** Questions and answers are not stored. Logs never include the
   question text, the answer, or redacted values; retrieved node ids only at DEBUG.

## Consequences

- One more LLM use exists; it can only word an answer from redacted document text.
  It has no tools and no write path. Import-isolation and source-scan tests keep
  Calendar writer/reader/confirmation modules out of the medic package.
- The organizer is tuned on the Benefit Summary layout. A different table layout or
  the full policy may need rule changes; prose pages fall back to page-referenced
  chunks. Carter must read the build warnings and `tree.md` after each rebuild.
- Merged-cell repairs are inferences from layout, not from the PDF's cell borders.
  They are flagged to the model ("the table cell is shared") and in the tree.
- The figure guard cannot catch numbers written in Chinese numerals or wrong
  wording around a correct figure; the extractive fallback and the scope line
  ("AIA's policy terms decide each claim") limit the damage.
- `pdftotext` must be installed on the machine that builds (Homebrew `poppler` on
  the Mini), or text must be extracted elsewhere.
- No conversation memory: follow-up questions in a thread are answered alone.
- No offline eval gate yet for medic answers (sibling LLM constitution); add a
  synthetic question set before relying on answer quality.

See [Phase 32](../../phases/phase-32-aia-medic-librarian.md).
