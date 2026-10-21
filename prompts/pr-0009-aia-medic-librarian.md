---
date: 2026-10-22 HK
branch: feat/phase-32-aia-medic-librarian
model: claude-opus-5-5 (Claude Code)
goal: Add an AIA Medic librarian Q&A to the existing listener for one Slack channel, answering only from an offline, redacted knowledge tree built from the plan PDF.
constraints: decisions locked by Carter 2026-10-22; existing Slack app and listener; redact ID-like tokens before any cloud model call and in every build output; never commit the PDF or agent_librarian/; no Calendar parse/create/list/delete for the medic channel; same primary + fallback model pattern as parse_fallback with a fake in tests; Traditional Chinese only in runtime copy; no commit, push or Mini access.
acceptance: uv run pytest -q and uv run ruff check . green; phase-32 doc, ADR 0017, PROGRESS entry, maintenance snippet, AGENTS map line, README and this archive; summary of files, test count, rebuild command and default channel env.
---

# Manager request (sanitized archive)

Implement Phase 32 — AIA Medic librarian Q&A — on branch
feat/phase-32-aia-medic-librarian (from main@0dce7cd). `.gitignore` already lists
`agent_librarian/`. The PDF must never be committed.

Locked (Carter 2026-10-22):
- Source: one unique Benefit Summary PDF (several identical copies on the Mini under
  the gitignored `agent_librarian/` folder). The workshop has extracted text and the
  PDF in its scratch folder for rebuild testing; neither goes into tracked files.
- Channel: `#jeff-aia-medic`, env `CEC_MEDIC_CHANNEL_ID` with the channel id as default.
- Use the existing bot and listener, not a new Slack app.
- Redact sensitive IDs and policy numbers before any cloud model call.
- Librarian style: pre-organize offline into a knowledge tree (categories, short
  summaries, page refs); answer only from the documents with citations; say unknown
  when the document does not say.
- The full policy PDF comes later: provide a rebuild command.

Architecture requested:
1. Offline build CLI (`python -m cec_vivisystem.medic_librarian build --pdf <path>
   --out data/agent_librarian/jeff-aia-medic/` or `--from-text`): extract pages,
   redact ID-like tokens (long digit runs, policy/member number patterns), organize
   into a tree JSON (+ optional Markdown) with categories such as hospitalisation,
   outpatient, dental, limits, notes, assistance services; each node with id, EN/ZH
   titles, summary, bullets with in-/out-of-network amounts, page and a short quote.
2. A sanitized seed tree tracked under `knowledge/jeff-aia-medic/` with a rebuild
   README; runtime prefers the data-dir tree, else the seed.
3. Medic-channel messages handled only by medic Q&A; plans and life-notes channels
   unchanged; help in the medic channel explains medic Q&A.
4. Retrieval (keyword overlap is fine for v1, injectable) → redacted context pack →
   xAI model with the parse_fallback primary + fallback pattern. Answer in the user's
   language (Cantonese/Traditional Chinese or English STE style), only from context,
   cite page/category, say unknown when missing; never invent limits; never echo
   redacted IDs.
5. Shared `redact_sensitive(text) -> (safe_text, count)` for questions and
   PDF-derived text: long digit runs (8+), HKID-like, emails, phone-like. Log counts.
6. No Calendar writes from the medic path (static test).
7. `.env.example`: `CEC_MEDIC_CHANNEL_ID`, optional `CEC_MEDIC_KB_DIR`.

Listener: route the medic channel to `handle_medic_message` and return; reply in
thread; errors give a short STE apology, no stack traces.

Tests (offline): build from the scratch text gives the expected categories and no raw
policy number; redaction unit tests; FakeLlm answers cite a page; unknown when
retrieval is empty; never calls Calendar; listener routing; import isolation. Full
pytest and Ruff green.

Docs: phase-32, ADR 0017, PROGRESS (2026-10-22), maintenance (rebuild, channel, invite
bot), AGENTS map line, this archive (English, no secrets, no policy numbers), README.

Redactions in this archive: the scratch file paths, the policy number example from
the request and the family member's document folder details were generalized.
