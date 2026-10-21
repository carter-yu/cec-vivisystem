# Phase 32: AIA Medic librarian Q&A (#jeff-aia-medic)

Date: 2026-10-22. Scope: one offline implementation slice on branch
`feat/phase-32-aia-medic-librarian` (from `main@0dce7cd`), one PR, no Mini deploy.
Inherits ground rules, unit-testing standard, logging/retention, resilience and
architecture. Decision record: [ADR 0017](../docs/decisions/0017-aia-medic-librarian.md).

## Why

Carter wants to ask quick questions about a family member's group medical plan
("How much physiotherapy can I claim?", 「洗牙有冇得 claim？」) in Slack and get
an answer that comes only from the insurer's document, with a page reference, and
that says "not in this document" instead of guessing. The source today is one
4-page bilingual Benefit Summary (a benefit schedule table plus numbered remarks).
The full policy document comes later and must be re-ingestible without code changes.

## Locked product decisions (Carter, 2026-10-22)

| Decision | Choice |
| --- | --- |
| Source | One unique Benefit Summary PDF (identical copies on the Mini under gitignored `agent_librarian/`). Never committed. |
| Channel | `#jeff-aia-medic`, `CEC_MEDIC_CHANNEL_ID`, default `C0CB179V0AE` |
| Bot | Existing Vivi Slack app and listener; no new Slack app |
| Privacy | Redact ID-like tokens (policy/member numbers, long digit runs, HKID, email, phone) before any cloud model call and in every build output; log counts only |
| Style | Librarian: offline pre-organized knowledge tree (categories, short summaries, page refs); answer only from the document with citations; say unknown when the document does not say |
| Later source | Full policy PDF: re-run the build command; no code change needed for a first pass |

## Goal

1. **Build (offline)**: `python -m cec_vivisystem.medic_librarian build --pdf <path|dir>`
   or `--from-text <file>` → redacted knowledge tree `tree.json` (+ `tree.md`) in
   `data/agent_librarian/jeff-aia-medic/` (or `--out`, or `CEC_MEDIC_KB_DIR`).
2. **Seed**: a sanitized tree built from the Benefit Summary is tracked under
   `knowledge/jeff-aia-medic/` so tests and the first deploy work without the PDF.
   Runtime prefers the data-dir tree, else the seed.
3. **Runtime**: every message in the medic channel goes to medic Q&A only. No
   Calendar parse/create/list/delete, no confirmation, no important dates, no life
   notes. Plans and life-notes channels are unchanged.
4. **Answer**: retrieve tree nodes → redacted context pack → xAI model (same primary
   + fallback pattern as `parse_fallback`) → validated JSON → reply with sources
   rendered by code from the tree. No hit → "not in this document" without a model call.
5. **No Calendar writes** from the medic path (static and import-isolation tests).

## Non-goals

- No conversation memory: each message (thread or top level) is answered alone.
- No storage of questions or answers; no new runtime store.
- No OCR, no embeddings, no vector store, no new Python dependency.
- No claim submission, no policy advice beyond quoting the document.
- No control-app screen for the medic channel or KB (follow-up).
- Not a general chat bot: help, questions and "not found" only.

## Architecture

```text
Slack message in CEC_MEDIC_CHANNEL_ID
  └─ listener.process_slack_message_event  (medic check first; outside the Calendar
       │                                     dispatch lock; never reaches parse)
       └─ medic_librarian.handle_medic_message
            ├─ help? → bilingual help (no model)
            ├─ redact_sensitive(question)            → count logged, never values
            ├─ KnowledgeTreeCache.get()              → data tree (mtime reload) or seed
            ├─ retrieve(tree, safe_question)         → scored nodes (keyword + CJK bigram + alias)
            │     └─ no hit → "not in this Benefit Summary" (no model call)
            ├─ build_context_pack(hits + parents + footnotes + currency note) → redacted
            ├─ MedicLlm primary → fallback (15 s each, no SDK retries)
            │     └─ outage / invalid / unverified figure → extractive reply from the tree
            └─ reply = answer + "Source: Benefit Summary p.N · Category › Item" + scope line
```

### Modules

| Module | Role |
| --- | --- |
| `redaction.py` | Shared `redact_sensitive(text) -> (safe_text, count)` |
| `medic_librarian/extract.py` | `pdftotext -layout` runner (injectable), text → pages on form feed |
| `medic_librarian/organize.py` | Layout-aware organizer: benefit-table rows, remarks/notes, prose fallback → `KnowledgeTree` |
| `medic_librarian/tree.py` | Tree dataclasses, JSON/Markdown I/O, path resolution, mtime cache |
| `medic_librarian/retrieve.py` | Tokenizer, alias table, scoring, context pack |
| `medic_librarian/answer.py` | Model protocol, fake, live xAI client, JSON validation, figure guard |
| `medic_librarian/handler.py` | `handle_medic_message`, copy (EN STE + Traditional Chinese), runtime loader |
| `medic_librarian/__main__.py` | CLI: `build`, `search` (offline retrieval preview), `install-seed` |

### Knowledge tree (schema `cec-medic-kb/v1`)

- `documents[]`: `doc_id`, `label_en`, `label_zh`, `pages`, `text_sha256` (of redacted text).
- `categories[]`: `id`, `title_en`, `title_zh`, `summary`, `node_ids`.
  Categories: `hospitalisation`, `outpatient`, `maternity`, `dental`, `assistance`,
  `limits` (overall maxima), `notes` (numbered remarks), `general` (currency,
  claims, effective period, interpretation), `other`, and `policy` (prose fallback).
- `nodes[]`: `id`, `doc_id`, `category`, `section`, `parent_id`, `title_en`,
  `title_zh`, `codes`, `summary`, `bullets`, `in_network`, `out_of_network`,
  `both_networks`, `conditions`, `notes` (footnote numbers), `inferred`, `page`,
  `source_quote` (short, redacted).

### Organizer rules (benefit table pages)

A page is a benefit table when it has an `In-Network` / `Out-of-Network` header.
Column of a value = nearest of in-centre, out-centre or the midpoint (merged cell =
both networks), from that page's header positions. Label lines group into items
(EN line + ZH line, `(`-detail and lowercase continuations, `/` alternatives such as
Telemedicine). Value lines attach to the label on the same line, else to the centre
of an EN/ZH or `(…)` value pair, else the nearest label (tie → below).
Merged-cell repairs, each recorded in the node's `inferred` list and the build report:
(a) an `N/A` value in one column extends to adjacent rows that lack that column;
(b) a group heading with no values whose bullets have exactly one valued bullet takes
that value (one cell spanning the group). Pages without the header use the remarks
parser (`N. text`, `Note:` bullets) or the prose chunker.

## Test plan (locked before implementation)

Fixtures: the Benefit Summary text is **not** a test fixture (it is not tracked). Build
tests use a **synthetic** benefit-summary text in the same `pdftotext -layout` shape
(`tests/fixtures/medic_synthetic_layout.txt`, invented amounts, a fake 10-digit policy
number). Answer and routing tests use the tracked seed tree or small in-memory trees.
Fixed clocks, temporary directories, `FakeMedicLlm`, fake PDF runner; no network.
Live-file test (skipped when absent): when the workshop scratch text exists, building
from it yields the expected categories and no raw policy number.

| # | Area | Case | Expect |
| --- | --- | --- | --- |
| R1 | redaction | 10-digit run, run inside `_` (`0000000000_001`) | `[ID-REDACTED]`, count 1 |
| R2 | redaction | HKID `A123456(7)`, email, `+852 9123 4567`, `9123-4567` | each redacted, counts add |
| R3 | redaction | labelled `Policy No.: GP-12345`, `保單號碼：AB12345` | token redacted, label kept |
| R4 | redaction | amounts `$1,560,000`, `HKD/MOP 350`, `30 visits`, dates `04/01/2026`, `7 days` | unchanged, count 0 |
| R5 | redaction | idempotent (second pass count 0); empty and non-str-safe input | stable |
| B1 | build | synthetic layout → categories hospitalisation, outpatient, dental, maternity, assistance, limits, notes, general | present |
| B2 | build | no raw 10-digit policy number anywhere in tree JSON/MD; redaction count ≥ 1 | true |
| B3 | build | row columns: two-value row → in/out; merged centre → both; out-only row | correct |
| B4 | build | `N/A` in-network merged cell extends to adjacent out-only rows, flagged `inferred` | true |
| B5 | build | group hoist: one valued bullet in a 3+ bullet group → heading value, flagged | true |
| B6 | build | footnote refs parsed (`5,18`), remarks become `notes` nodes with numbers | true |
| B7 | build | EN/ZH title split, codes `(PHY)`, `/` alternative absorbed (Telemedicine) | true |
| B8 | build | stable unique ids; JSON round-trip (save → load) equal; `tree.md` written | true |
| B9 | build | prose page (no table header) → `policy` chunks with page numbers | true |
| B10 | build | `--pdf` uses injected runner; missing `pdftotext` → clear error, exit 2; identical PDFs deduped | true |
| B11 | build | garbage / empty text → empty tree with warning, no crash | true |
| B12 | build | live scratch text (skip if absent): categories + no raw policy number | true |
| S1 | seed | tracked seed loads, schema v1, has key nodes (physiotherapy, dental, room & board), no ≥8-digit run | true |
| S2 | seed | resolver prefers data-dir tree, else seed; `CEC_MEDIC_KB_DIR` override; corrupt data tree → seed + error log | true |
| Q1 | retrieve | 「洗牙」, "physiotherapy limit", 「生仔」, "GP co-pay" → expected top node | true |
| Q2 | retrieve | unrelated question ("weather tomorrow") → no hits | true |
| Q3 | retrieve | context pack includes parent + footnotes + currency note, redacted, size-capped | true |
| A1 | answer | FakeMedicLlm answered + citation → reply has answer and "p.2" source line | true |
| A2 | answer | no retrieval hit → unknown copy, model not called | true |
| A3 | answer | model `not_in_document` → unknown copy | true |
| A4 | answer | citation id not in context → extractive fallback, logged | true |
| A5 | answer | invented figure (`$99,999`) → extractive fallback, logged `unverified_figure` | true |
| A6 | answer | primary raises → fallback model used; both raise → extractive reply with outage line | true |
| A7 | answer | question with fake policy number → model receives `[ID-REDACTED]`, never the digits; logs no value | true |
| A8 | answer | Chinese question → Traditional Chinese copy; English → English; help → bilingual help | true |
| A9 | answer | answer containing Simplified markers or a long digit run → fallback / redacted | true |
| A10 | answer | KB missing → short STE apology; handler exception → apology, no stack trace in reply | true |
| A11 | answer | live client payload shape: strict JSON schema, temperature 0, max_retries 0 (fake OpenAI) | true |
| L1 | listener | medic channel message → medic handler; parse fake never called; no confirmation created | true |
| L2 | listener | medic channel "yes" in a thread with a pending plans confirmation → not resolved | true |
| L3 | listener | plans channel unchanged (create proposal); life-notes unchanged | true |
| L4 | listener | config: default medic channel; `off` disables; same id as plans → disabled + error log | true |
| L5 | listener | medic route bot message / empty text ignored; handler failure → apology reply | true |
| L6 | listener | runtime wiring replies in thread for a medic message (fake Bolt) | true |
| I1 | isolation | importing `cec_vivisystem.medic_librarian` loads no `calendar_writer`, `confirmation`, `calendar_reader`, `calendar_delete`, `series`, `overlap`, `listener` | true |
| I2 | isolation | medic package source never references Calendar write or confirmation functions | true |
| I3 | isolation | `knowledge/` contains no PDF; `.gitignore` keeps `agent_librarian/` | true |

**Non-tests**: real xAI calls, real Slack posts, real `pdftotext` on the Mini, answer
quality on live questions, full-policy layouts (not yet available).

## Implementation notes (2026-10-22)

- **As locked**: channel route first, offline build, tracked seed, data-dir
  preference, shared redaction, keyword retrieval, primary + fallback model, no
  Calendar imports. 55 new tests in `tests/test_medic_librarian.py` (one more
  than the table: A12 `search` CLI and A13 extractive group amount).
- **Additions**: `search` (offline retrieval preview) and `install-seed` CLI
  commands; directory input for `--pdf` with identical-copy skipping; mtime/size
  reload of the data tree; a figure guard and citation check on model answers.
- **Deviation**: on model outage, missing key or a rejected answer the reply
  quotes the matching tree lines with pages (extractive fallback) instead of a bare
  apology; the short STE apology is kept for handler errors and an unavailable
  tree. Reason: the quoted lines are still "only from the document, with a
  citation", and a model outage should not hide them.
- **Deviation**: the medic route runs outside the Calendar dispatch lock (ADR 0017
  decision 1); the family path keeps the lock unchanged.
- **Seed**: built from the workshop text of the Benefit Summary (110 nodes, 1
  redaction: the policy number in the footer). The build and `pdftotext -layout`
  on the scratch PDF gave identical trees. 26 nodes carry a merged-cell inference;
  each was checked against the rendered PDF pages.
- **Simplified Chinese**: a medic-specific rejection set (insurance wording) is
  checked in addition to the parser's set; written as escapes, never a mapping.

## Acceptance

- [x] Rules 4 and 5: tests above green offline; boundary logs `medic_*` with
      `component=medic_librarian`, counts only, no question text or IDs.
- [x] `uv run pytest -q` and `uv run ruff check .` green; `git diff --check` clean.
- [x] No PDF or `agent_librarian/` content tracked.
- [ ] Mini (authorized later): invite the bot to `#jeff-aia-medic`, set
      `CEC_MEDIC_CHANNEL_ID`, build from the local PDF or install the seed, restart
      the listener, ask one English and one Cantonese question, check sources.
