# Progress Log – cec-vivisystem

## How to use
Add a new entry at the top after every session (below this section, above older entries).

---

## 2026-09-11 (Phase 15 implementation)

- **Phase**: 15 – Important dates (add / view / 10:00 next-7-days) **implemented**
- **Completed**:
  - Locked [phases/phase-15-important-dates.md](phases/phase-15-important-dates.md) and [ADR 0004](docs/decisions/0004-important-dates-store.md) (JSON store, not Calendar Writer, not LifeNotes)
  - Parser: `4月12日 梓梵生日` / `10月22日 老婆生日` / `12月4日 Carter 生日` → yearly add; `2026年9月15日 考試` → one-off; `重要日子` / `有咩生日` → list
  - Slack add is immediate (`已記低`); view lists or `未記低重要日子。`; no confirmation; no calendar write
  - `help` / `指令` includes add and view examples
  - `run_important_dates_review` + CLI: next 7 HKT days; one Slack post per occurrence; skip when none
- **Tests**: I1–I8 parse; D1–D10 store/review; L-add / L-view / help phrases; prior suite + ruff
- **Issues / Friction**: Mini still needs `git pull` + Listener restart. 10:00 launchd is operator stretch. No Slack delete/edit this phase
- **Resilience notes**: Class F rows (`data/important_dates/`); class C post markers 30d (`data/important_dates_posts/`). Class A logs. No Writer. No LLM
- **Next session plan**: Operator Mini pull + kickstart + optional 10:00 plist. Do not jump to LLM
- **Session status**: Phase 15 offline acceptance met

---

## 2026-09-11 (Phase 14 implementation)

- **Phase**: 14 – Period recap (today / week / month / date range) **implemented**
- **Incident (2026-09-11 logs, `incident-logs/2026-09-11/`)**: Live `#family-plans` `今日有乜？` (`listener-2026-09-08.log` / `parser-2026-09-08.log`, also Mini stdout 2026-09-06) was accepted (`dispatch_succeeded`) but parsed `needs_clarification` `missing_fields=['start']`. List signal `有乜` matched; `_extract_date` had no **今日**. Family got a create-clarification, not today’s events. Same gap as Phase 12 left for today/week/month. Socket Mode `URLError` Errno 49 (`Can't assign requested address`) then Errno 8 DNS in `listener-2026-09-07.log` is Mini network/sleep; Listener reconnected ~00:57 and `help` worked after restart. Token `invalid_grant` in older Mini stdout remains operator.
- **Completed**:
  - Locked [phases/phase-14-period-recap.md](phases/phase-14-period-recap.md)
  - Parser: `今日有乜？` → today HKT; `今個星期` / `今個禮拜` Monday-start this week; `下個星期` next week; `今個月` calendar month; `9月1日至9月7日有乜` inclusive days (year from `now`)
  - `format_recap` groups multi-day lists by HKT day; empty → `呢段時間日曆冇活動。`
  - Listener uses recap for windows longer than one day; still no confirmation / no write; list always replies
  - `help` / `指令` lists the new period phrases
- **Tests**: P1–P5 parse windows; R6–R8 recap; L3 today list; L4 week recap; Q5 weather / Q6 bare 有乜 / weekday-not-week; prior suite + ruff
- **Issues / Friction**: Mini still needs `git pull` + Listener restart before live Slack sees this. `今日有咩做？` / `明天活動？` remain out of scope. Reconnect storm not changed this phase
- **Resilience notes**: Class A logs only. No new store. No calendar write. No LLM. Writer / confirmation unchanged
- **Next session plan**: Operator Mini pull + kickstart, or Phase 15 important-dates. Do not jump to LLM
- **Session status**: Phase 14 offline acceptance met

---

## 2026-09-06 (Phase 13 implementation)

- **Phase**: 13 – Conflict-before-create + idempotent Writer **implemented**
- **Completed**:
  - Locked [phases/phase-13-conflict-and-idempotent-write.md](phases/phase-13-conflict-and-idempotent-write.md)
  - Writer: same `confirmation_id` → at most one Google `create_event` (`already_created` from class B audit). Failed create may retry
  - Second thread `yes` replies **Already added**; no second insert
  - Create proposals show bilingual **撞期** (times + titles). Warn, do not hard-block. Reuse `overlap.py`
- **Tests**: W1 two accepts → one create; C1–C3 overlap hits/fail/none on proposal text; Phases 6/8 stay green
- **Issues / Friction**: Mini still needs `git pull` + Listener restart + Google refresh token. Two *different* confirmations for the same phrase (Incident B 08:27 vs 11:35) are still two creates — out of scope
- **Resilience notes**: Idempotency needs `audit_store` (Socket Mode already has it). Overlap remains a warn. No freebusy. No LLM. No Writer update/delete
- **Next session plan**: Operator Mini pull/token, or Phase 14 period recap. Do not jump to LLM
- **Session status**: Phase 13 offline acceptance met

---

## 2026-09-06 (help / 指令 allowed-inputs)

- **Phase**: post-12 small add — Slack help text for rule-based inputs
- **Completed**:
  - Whole-message `help` / `/help` / `指令` / `點用` / `有咩指令` → `IntentType.HELP`
  - `#family-plans` replies the common allowed list (list-a-day + create examples + when/titles/who); no confirmation; no calendar write
  - Unknown / clarification replies point at `help` or `指令`
- **Tests**: parser help vs create/list; listener help reply; existing suite + ruff
- **Issues / Friction**: Mini still needs `git pull` + Listener restart before live Slack sees this
- **Resilience notes**: Class A logs only (`intent_type=help`). Not a write. No LLM
- **Next session plan**: Operator Mini pull + kickstart, or Phase 13. Do not jump to LLM
- **Session status**: Help command offline-ready

---

## 2026-09-06 (Phase 12 implementation)

- **Phase**: 12 – Morning today-recap (07:00) + list must-reply **implemented**
- **Incident A (2026-09-05, `聽日有乜嘢活動` no Slack recap)**: Source is committed [incident-logs/2026-09-05/](incident-logs/2026-09-05/) (`listener-*.log`, `parser-*.log`, `calendar_reader-*.log`; Mini copies of gitignored `logs/`). The **exact** phrase `聽日有乜嘢活動` never appears. Same-day recap-style lines in `listener-2026-09-05.log` / `parser-2026-09-05.log` were accepted (`dispatch_succeeded`, never `message_ignored` / `dispatch_failed`) but not listed: `聽日有咩做？` and `明天活動？` and `what are the events for tomorrow?` → `needs_clarification`; later `今日有咩做？` / `what are the events for today?` / `tell me all events for next 24 hours?` → `unknown`. Those Slack replies were create-clarification or “not a create”, not a calendar list. The only successful list parse is next morning in `listener-2026-09-06.log` / `parser-2026-09-06.log`: `聽日有乜` → `list_events` → reader. `calendar_reader-2026-09-06.log` then `list_failed` `RefreshError` `invalid_grant: Token has been expired or revoked` (same token failure on overlap lists in `calendar_reader-2026-09-05.log`). Listener still `dispatch_succeeded` (`next_component=calendar_reader`). **Hypothesis:** (1) P0-like phrases that day did not match `_LIST_SIGNAL` (`有乜` / `有什麼` / `tell me the events`, not `有咩` / bare `活動` / `what are the events`), so no recap; (2) when list *did* match (`聽日有乜`), Google refresh was revoked, so the family still got no event list (error line at best). Phase 12 locks P0/P0b as `list_events` and makes LIST_EVENTS always reply (list / empty / explicit error). Token refresh is operator, not this phase.
- **Completed**:
  - Locked [phases/phase-12-morning-recap.md](phases/phase-12-morning-recap.md)
  - Parser: `聽日有乜嘢活動` / `聽日有乜嘢` / `聽日有什麼活動` → `list_events` tomorrow window (Q3 `聽日有乜` unchanged)
  - Listener: LIST_EVENTS always replies (list, empty, or explicit error); no confirmation; no calendar write
  - `run_morning_recap` + CLI: today HKT `[00:00, next 00:00)`; empty day still posts; one post per date (`data/morning_recap/`); fake client/poster in pytest
  - Documented 07:00 HKT launchd **command**; plist on Mini is operator stretch
- **Tests**: 129 passed (P0/P0b/Q3, L1/L2, M1–M4 + Phases 0–11); ruff clean
- **Issues / Friction**: Mini still needs `git pull` + kickstart; 07:00 plist not installed here. `聽日有咩做？` / `明天活動？` remain `needs_clarification` (not in Phase 12 locked table). Week/month recap and duplicate-create wait for 13–14
- **Resilience notes**: Class A logs (`component=morning_recap`). Posted-date markers class **C** (30d purge on CLI start). No calendar write. No LLM. No freebusy. Writer unchanged
- **Next session plan**: Operator Mini pull + 07:00 launchd, or Phase 13 duplicate Google create / bilingual 撞期. Do not jump to LLM or period recap
- **Session status**: Phase 12 offline acceptance met

---

## 2026-09-05 (Phase 11 implementation)

- **Phase**: 11 – Parser 聽朝 (tomorrow morning) **implemented**
- **Completed**:
  - Locked [phases/phase-11-ting-chiu.md](phases/phase-11-ting-chiu.md)
  - Live Mini line `聽朝11點帶梓梵去MS Wong 度上堂` → `create_event`; title Miss Wong 堂; start tomorrow 11:00 HKT; participant **Cedric** (梓梵)
  - 聽朝 without a clock still `needs_clarification` (no invented time)
  - Writer / Listener / overlap source unchanged
- **Tests**: 116 passed; ruff clean
- **Issues / Friction**: Mini needs `git pull` + kickstart before live Slack smoke of M1. 聽晚 / 今朝 not added
- **Resilience notes**: Class A logs only; same parse contract; no LLM. 梓梵 remains Cedric
- **Next session plan**: Mini pull/kickstart and repost M1, or more parser day-words/titles from live lines. Do not jump to LLM
- **Session status**: Phase 11 offline acceptance met

---

## 2026-09-05 (Phase 10 implementation)

- **Phase**: 10 – Parser family titles (公園 / playgroup / classes / errands) **implemented**
- **Completed**:
  - Locked [phases/phase-10-parser-titles.md](phases/phase-10-parser-titles.md)
  - Operator-picked titles: 公園/playground; playgroup/遊戲班; 游水班→游泳; 體能班/gym/gymnastics; 手作/workshop/工作坊; 商場/mall; 生日會/birthday party; 打針/打疫苗/vaccine
  - Park chat without a schedule stays `unknown` (公園 is not a create signal alone)
  - Writer / Listener / overlap source unchanged
- **Tests**: 114 passed; ruff clean
- **Issues / Friction**: Mini launchd still needs the Homebrew `uv` path (`my-notes/fix-launchd-uv-path.md`) before live Slack smoke. Rest of the HK activity catalog (買餸, 迪士尼, phonics, …) not in this phase
- **Resilience notes**: Class A logs only; same parse contract; no LLM
- **Next session plan**: Operator Mini launchd, or more parser titles from new live lines. Do not jump to LLM
- **Session status**: Phase 10 offline acceptance met

---

## 2026-08-30 (operator – family calendar id)

- **Phase**: operator only (no code phase)
- **Completed**:
  - Local `.env` `GOOGLE_CALENDAR_ID` set from Google Calendar **Settings → Integrate calendar** for the calendar titled **Shared Family calendar**
  - That id is `carter.yu.ai@gmail.com` (Carter’s Gmail calendar, renamed). Same event set as `primary` for this OAuth user (including the 2026-08-31 游泳 smoke creates)
  - No writable `…@group.calendar.google.com` family calendar on this account: the only group ids in local history are **Cedric's Minion Calendar** (404 with current token)
  - Listener restarted after the env change so list / overlap / writes use the explicit id
- **Tests**: unchanged (105 last known; no code this session)
- **Issues / Friction**: `calendar.events` cannot call `calendarList.list` (403 insufficient scopes) — id taken from Calendar settings URL, not a scope expansion. If Elaine’s events live on a different calendar, share **Shared Family calendar** with her as Make changes to events, or re-consent as Elaine
- **Resilience notes**: Secrets stayed in gitignored `.env` / `my-notes/` (ground rule 13). Pytest still offline. Writer gate unchanged. OAuth app stays Testing; no new scopes
- **Next session plan**: Parser-only phase for new live titles (買餸, playdate) if intake is the pain — lock `phases/phase-10-*.md` first. Do not jump to LLM, freebusy, update/delete, or reminders
- **Session status**: Operator family calendar id set; Listener restarted

---

## 2026-08-30 (Phase 9 implementation)

- **Phase**: 9 – Parser family aliases (梓梵 / 游水 / MS Wong) **implemented**
- **Completed**:
  - Locked [phases/phase-9-parser-aliases.md](phases/phase-9-parser-aliases.md)
  - `游水` → title `游泳`; `MS Wong` / `MS. Wong` → `Miss Wong 堂`; `梓梵` → participant `Cedric`
  - Live line `聽日9點，梓梵游水` is `create_event` (tomorrow 09:00 HKT)
  - 梓梵 is not a create signal (life-note-style text without schedule stays `unknown`)
  - Writer / Listener / overlap source unchanged
- **Tests**: 105 passed; ruff clean
- **Issues / Friction**: Shared family `GOOGLE_CALENDAR_ID` is still operator. Extra titles (買餸, playdate) later from live need
- **Resilience notes**: Class A logs only; same parse contract; no LLM
- **Next session plan**: Operator family calendar id, or more parser titles from new live lines. Do not jump to LLM
- **Session status**: Phase 9 offline acceptance met

---

## 2026-08-30 (Phase 8 implementation)

- **Phase**: 8 – Overlap / same-person warn on create proposal **implemented**
- **Completed**:
  - Locked [phases/phase-8-overlap-warn.md](phases/phase-8-overlap-warn.md)
  - `detect_create_overlaps` reuses `list_calendar_events` for proposed `[start, end)` (default +1h; all-day +1 day); half-open intersect in `Asia/Hong_Kong`
  - Same-person: casefold exact intersect of `parse_result.participants` with listed attendees / `Participants:` line; `梓梵` ≠ Cedric; no invented emails
  - Warning appended to the create **proposal**; yes still required; list errors do not crash or hard-block
  - Writer and parser unchanged; no local calendar mirror; no freebusy API
- **Tests**: 102 passed; ruff clean
- **Issues / Friction**: Overlap listing still uses `GOOGLE_CALENDAR_ID` / `primary` (shared family calendar id is operator). Parser 梓梵 / 游水 still later
- **Resilience notes**: Overlap is not a write. Writer gate unchanged. Class A logs only; class G (no local event DB). Fake Google client in pytest
- **Next session plan**: Parser phase for 梓梵/游水 if that is the family blocker, or set shared family `GOOGLE_CALENDAR_ID`. Do not mix parser + Writer + freebusy
- **Session status**: Phase 8 offline acceptance met

---

## 2026-08-30 (Phase 7 implementation)

- **Phase**: 7 – Calendar Reader (list / summary for a period) **implemented**
- **Completed**:
  - Locked [phases/phase-7-calendar-reader.md](phases/phase-7-calendar-reader.md)
  - `IntentType.LIST_EVENTS`; Q1–Q3 phrases (`1 Sept 2026`, `2026年9月1日有乜`, `聽日有乜`)
  - `list_calendar_events` + `FakeCalendarClient.list_events`; no local calendar mirror
  - Slack `#family-plans` list path replies a list; **no** confirmation / no write
- **Tests**: 91 passed; ruff clean
- **Issues / Friction**: Listing uses `GOOGLE_CALENDAR_ID` / `primary`. Conflict / same-person is Phase 8. Parser 梓梵 still later
- **Resilience notes**: Read-only; Google errors do not crash. Class A logs only for list
- **Next session plan**: Phase 8 overlap warn on create proposal **or** parser 梓梵/游水. Do not mix
- **Session status**: Phase 7 offline acceptance met

---

## 2026-08-29 (Phase 6 implementation)

- **Phase**: 6 – Calendar Writer (create only, accepted confirmations) **implemented**
- **Completed**:
  - Locked [phases/phase-6-calendar-writer.md](phases/phase-6-calendar-writer.md)
  - `write_calendar_create(confirmation, *, client=)` — refuses unless `accepted` + non-empty `confirmation_id`; fake Google client in pytest
  - Maps `parse_result` → event (title, start `Asia/Hong_Kong`, location; names in description, emails only as attendees)
  - Class **B** audit store `data/calendar_audit/` (90d purge + 50 MB soft cap); `maintain_calendar_audit_storage` on Socket Mode start
  - Listener: first thread accept with injected client creates one event; omitting client preserves Phase 4b ack
  - Stretch ack when write succeeds: `Accepted. Calendar event created.`
  - Env: `GOOGLE_CALENDAR_ID` named in `.env.example` only; live client from existing `GOOGLE_*` (not used by pytest)
- **Tests**: 77 passed (W1–W4, W6–W8 + optional listener write-once); ruff clean
- **Issues / Friction**:
  - First live write failed `403 accessNotConfigured` (Calendar API off on project `625746289455` / `cec-vivisystem`). Enabled API, retried with a new plan
  - Live smoke **succeeded** 2026-08-29: `#family-plans` `聽日上午9點帶 Cedric 去游泳` → thread `yes` → `write_succeeded` (`calendar_event_id=696pavtgplml3dclj96n8s5bi8`, title 游泳, start 2026-08-31 09:00 HKT) on `primary`
  - `GOOGLE_CALENDAR_ID` still `primary` (shared family calendar id not copied yet)
  - `#family-plans` line `聽日9點，梓梵游水` still `needs_clarification` (parser title gap — **not** this phase)
- **Resilience notes**: Write gate closed without confirmation id (CRITICAL log, no Google call). Single attempt, no retry loop. Failures logged + audited. No local calendar mirror (class G). Secrets stay local (ground rule 13)
- **Next session plan**: Parser phase for 梓梵/游水 if that is the family blocker, or set shared family `GOOGLE_CALENDAR_ID`. Do not publish the OAuth app. Freebusy later
- **Session status**: Phase 6 offline acceptance met; live Slack → Google create smoke green

---

## 2026-08-29 (Google Calendar OAuth — operator prep)

- **Phase**: 6 prep only — Desktop OAuth for live Calendar Writer smoke. **No Writer code.**
- **Completed**:
  - GCP project `cec-vivisystem`; Google Calendar API enabled
  - Google Auth Platform **Branding**: app name `cec-vivisystem`, support + developer contact `carter.yu.ai@gmail.com`. Logo / homepage / privacy / **Authorized domains** left empty (Testing does not need them)
  - **Audience**: External, **Testing**, test user `carter.yu.ai@gmail.com`. App not published; no Google verification
  - Desktop OAuth client JSON at gitignored `my-notes/google-oauth-client.json`
  - One-shot helper `my-notes/get-google-refresh-token.py` (run with `uv run --with google-auth-oauthlib python …`; do not paste Python into zsh)
  - Browser consent completed (`access_type=offline`, `prompt=consent`, scope `calendar.events`). Local `.env` now has `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`, `GOOGLE_CREDENTIALS_PATH`
  - Operator notes: `my-notes/google-calendar-credentials.md` (Branding, Access blocked / test users, zsh vs Python)
- **Tests**: unchanged (69 last known; no code this session)
- **Issues / Friction**:
  - `GOOGLE_CALENDAR_ID` is still `primary` (Carter’s default calendar). Shared family calendar id (`…@group.calendar.google.com`) not copied yet
  - A second GCP project may still exist from a mistaken create — shut it down from Manage resources if it is not `cec-vivisystem`
  - Live `#family-plans` line `聽日9點，梓梵游水` still `needs_clarification` (parser title gap; separate from Writer)
- **Resilience notes**: Secrets only in gitignored `.env` / `my-notes/` (ground rule 13). Pytest still offline / no tokens. Write gate still closed until Phase 6. OAuth app stays Testing
- **Next session plan**: Implement Phase 6 Calendar Writer (accepted confirmations only, fake Google client in pytest). Live smoke is now unblocked as stretch. Do not publish the OAuth app. Do not combine Writer + parser (梓梵/游水) in one session
- **Session status**: Local Google OAuth ready; Calendar Writer not started

---

## 2026-08-28 (Phase 4b implementation)

- **Phase**: 4b – Slack confirmation (proposal + thread yes/no) **implemented**
- **Completed**:
  - Locked [phases/phase-4b-slack-confirmation.md](phases/phase-4b-slack-confirmation.md)
  - `#family-plans` `create_event` → `create_confirmation` + `build_proposal` reply; thread `yes`/`不要`/locked vocab → accept/reject
  - Helpers: `classify_confirmation_reply`, `find_pending_for_thread`; `expire_due` before resolve
  - Omitting `confirmation_store` preserves Phase 2 parse-reply; life-notes path unchanged
  - Socket Mode: JSON confirmation store + `maintain_confirmation_storage` on start
  - **No** Calendar Writer / Google / LLM
- **Tests**: 69 passed; ruff clean
- **Issues / Friction**: Live smoke needs Listener restart; accepted rows still do not write to Google Calendar (next = Writer)
- **Resilience notes**: Class C purge on start; replies never claim a calendar write; offline tests inject in-memory store
- **Next session plan**: Calendar Writer for **accepted** confirmations only (Google OAuth). Freebusy later.
- **Session status**: Phase 4b offline acceptance met

---

## 2026-08-28 (Phase 5B implementation)

- **Phase**: 5B – Slack wiring for LifeNotesKeeper **implemented**
- **Completed**:
  - Locked [phases/phase-5b-life-notes-slack.md](phases/phase-5b-life-notes-slack.md) (S1–S6, two named env vars, no mixed paths)
  - Listener dispatch: `#family-life-notes` → `create_life_note` (exact `raw_text`, `status=raw`, source metadata); `#family-plans` unchanged parse-reply
  - Config: `SLACK_FAMILY_PLANS_CHANNEL_ID` + required `SLACK_LIFE_NOTES_CHANNEL_ID`; `allowed_channel_ids` remains plans-only
  - Thin ack `已記低` on successful store; empty life-notes text ignored (`empty_text`); no empty notes
  - Tests S1–S5 in `tests/test_listener.py`; Socket Mode passes life-notes channel + `JsonDirLifeNotesStore`
- **Tests**: 61 passed; ruff clean
- **Issues / Friction**: Live smoke of `#family-life-notes` left to family after restarting Listener (previous live message was `wrong_channel` before this dispatch)
- **Resilience notes**: Offline fakes only in pytest; class F store unchanged (no auto-purge); secrets still local `.env`
- **Next session plan**: Slack confirmation (4b) or Calendar Writer — pick from need. Structured enrichment of stored notes is later.
- **Session status**: Phase 5B offline acceptance met

---

## 2026-08-28 (multi-channel Slack env names)

- **Phase**: 5A stretch prep — named channel env vars (no Listener dispatch yet)
- **Completed**:
  - Renamed `SLACK_ALLOWED_CHANNEL_ID` → `SLACK_FAMILY_PLANS_CHANNEL_ID` (maps to `#family-plans`)
  - Added placeholder `SLACK_LIFE_NOTES_CHANNEL_ID` in `.env.example` (maps to `#family-life-notes`; not read by code yet)
  - Gitignored `my-notes/` for local operator docs
  - Live smoke: Socket Mode still starts; a `#family-life-notes` message is logged `wrong_channel` (expected until wiring)
- **Tests**: 56 passed; ruff clean; hello/parser CLI smokes green
- **Issues / Friction**: `#family-life-notes` exists in Slack but Listener still allowlists only the plans channel
- **Resilience notes**: Secrets still local `.env` only; channel ids not committed
- **Next session plan**: Wire Listener dispatch for `#family-life-notes` (Phase 5A stretch / 5B) so messages call `create_life_note`
- **Session status**: Env rename committed; life-notes Slack wiring still open

---

## 2026-08-25 (Phase 5A implementation)

- **Phase**: 5A – LifeNotesKeeper Option A (reliable raw capture) **implemented**
- **Completed**:
  - `src/cec_vivisystem/life_notes.py` — `create_life_note`, `LifeNotesStore` protocol, `InMemoryLifeNotesStore`, `JsonDirLifeNotesStore` (`data/life_notes/`, gitignored)
  - Models: `LifeNote`, `LifeNoteStatus`, `LifeNoteSource`
  - Dedicated channel concept `#family-life-notes` (Listener wiring left as stretch)
  - `tests/test_life_notes.py` — LN1–LN5 plus contract, JSON store, incomplete source, logging boundary
  - Class **F** retention: notes kept until family deletes; no auto-purge; no LLM / no structured extraction / no calendar coupling
- **Tests**: 56 passed; ruff clean
- **Issues / Friction**: Slack `#family-life-notes` not wired yet (stretch)
- **Resilience notes**: Parallel component; injectable `now` + store; empty text / missing source raise `LifeNoteError`; store load failures logged and skipped; `raw_text` is the durable source of truth
- **Next session plan**: Wire Listener for `#family-life-notes` **or** Slack confirmation (4b) **or** Calendar Writer — pick from need. Structured enrichment of stored notes is later.
- **Session status**: Phase 5A offline acceptance met

---

## 2026-08-22 (Phase 4 implementation)

- **Phase**: 4 – Confirmation path **implemented** (offline core)
- **Completed**:
  - `src/cec_vivisystem/confirmation.py` — `build_proposal`, `create_confirmation`, `resolve_confirmation`, `expire_due_confirmations`, `purge_confirmations`, `maintain_confirmation_storage`
  - Models: `Confirmation`, `ConfirmationStatus`, `ConfirmationDecision`
  - Stores: `InMemoryConfirmationStore` + `JsonDirConfirmationStore` (`data/confirmations/`, gitignored)
  - `tests/test_confirmation.py` — C1–C10 locked cases
  - Class C retention: terminal+7d purge; pending max 30d; no calendar / no LLM / no Slack yes-no wire (stretch left open)
- **Tests**: 46 passed; ruff clean
- **Issues / Friction**: Listener still posts parse summary only — confirmation not yet created from Slack (Phase 4b stretch)
- **Resilience notes**: Write gate closed until Calendar Writer; purge path tested
- **Next session plan**: Wire Slack proposal + yes/no **or** start Calendar Writer for accepted confirmations only — decide from need. Freebusy still optional later.
- **Session status**: Phase 4 offline acceptance met

---

## 2026-08-22 (Phase 4 scope)

- **Phase**: 4 – Confirmation path **scope only** (not implemented)
- **Completed**:
  - Confirmed tip `eae3e0b` / 36 tests green; decided Phase 4 = **Option A – thin Confirmation** (not freebusy, not Calendar Writer, not LLM)
  - Wrote [phases/phase-4-confirmation.md](phases/phase-4-confirmation.md): goal, in/out, models, create/resolve/expire/purge, unit test plan C1–C10 (~10 tests), class **C** retention + purge mandatory, Slack yes/no as stretch
  - README + architecture: Confirmation / minimal Proposal → Scoped
- **Tests**: unchanged (36 passed; no confirmation code yet)
- **Issues / Friction**: Freebusy explicitly deferred to a later phase
- **Resilience notes**: First durable operational store must ship with purge; write gate still closed
- **Next session plan**: TDD implement Phase 4 from locked C1–C10. Do not start Calendar or freebusy until Confirmation acceptance is met.
- **Session status**: Phase 4 scope locked; ready for implementation

---

## 2026-08-22 (Phase 3 implementation)

- **Phase**: 3 – Rule parser expansion **implemented**
- **Completed**:
  - Heuristics: 聽日 (= tomorrow), 上晝/下晝 clock periods, Miss Wong 堂 title, 銅鑼灣 location; participant match adjacent to CJK (`帶Cedric去`)
  - Tests L1–L4 + L6 in `tests/test_parser.py`; Phase 1 suite still green
  - Module docstring documents relative/period policy; acceptance checked in phase doc
  - Architecture Parser → Done (Phase 1+3); README status updated
- **Tests**: 36 passed; ruff clean
- **Issues / Friction**: Optional live Slack re-smoke of L1 left to family; more dialect gaps may appear later (rules-first, not LLM by default)
- **Resilience notes**: Contract unchanged; no new stores; write gate still closed
- **Next session plan**: Decide Phase 4 from need (likely Confirmation path). Do not start Calendar CUD until Confirmation exists.
- **Session status**: Phase 3 implementation complete

---

## 2026-08-22 (Phase 3 scope)

- **Phase**: 3 – Rule parser expansion **scope only** (not implemented)
- **Completed**:
  - Confirmed git: `main` clean @ `12a65ad` (Phase 2 weekend close); fixed flaky file-log date assert (wall-clock vs injected retention `now`)
  - Decided Phase 3 = **Option A – parser rules expansion** from live friction (not Confirmation, not LLM)
  - Wrote [phases/phase-3-parser-expansion.md](phases/phase-3-parser-expansion.md): goal, in/out, fixtures L1–L4 (聽日 / 上晝 / 下晝 / Miss Wong 堂 + 銅鑼灣), unit test plan, class A logging only, acceptance criteria
  - README + architecture: Phase 3 scoped; next = implement expansion; Confirmation/Calendar parked
- **Tests**: 31 passed expected after log-test fix
- **Issues / Friction**: Live Cantonese still often `needs_clarification` until Phase 3 is implemented
- **Resilience notes**: No new stores; contract unchanged; LLM still demand-driven + ADR only
- **Next session plan**: TDD implement Phase 3 from locked L1–L4 (red → green heuristics). Do not start Confirmation or Calendar Writer until Phase 3 acceptance is met (or family explicitly re-prioritizes).
- **Session status**: Phase 3 scope locked; ready for implementation

---

## 2026-08-08 (weekend close)

- **Phase**: 0 done · 1 done · 2 done (live smoke on workspace **Three of Us** / `#family-plans`)
- **Saved**: `main` clean after this close; pushed to `origin/main` (carter-yu/cec-vivisystem)
- **Tests last known**: 31 passed
- **Live use**: Slack Socket Mode Listener works end-to-end (message → parse → thread reply; logs on stdout + `logs/{component}-YYYY-MM-DD.log`)
- **Known friction (grow from need)**: Real family Cantonese phrases often return `needs_clarification` (rule parser gaps: e.g. 聽日 / 上晝 / place+class titles). **Not** jumping to LLM by default — next session should decide Phase 3 scope from this friction *or* Confirmation path; rules-first enhance parser behind the same `ParseResult` contract unless sustained pain + ADR
- **Do not start next without scope**: Confirmation Guardian, Calendar Writer/read, LLM parser
- **Also this weekend**: secrets ground rule 13; file logs + startup archive/purge (class A, 14d / 100 MB); `logs/` gitignored
- **Next session plan**: Read PROGRESS + architecture §4.4–4.4.1; decide Phase 3 scope only first (candidates: **parser rules expansion from live phrases** *or* **Confirmation path** — pick what unblocks family use). Do not implement until `phases/phase-3-*.md` is locked
- **Session status**: Closed for this weekend

---

## 2026-08-08 (file logs + retention)

- **Phase**: ops / logging (post Phase 2 smoke)
- **Completed**:
  - File logging under `logs/{component}-YYYY-MM-DD.log` + `logs/archive/`
  - `setup_logging` runs archive (prior days) + purge (14d) + soft cap (100 MB) on every real service start
  - Docs §6 updated; `logs/` gitignored; unit tests for retention + per-component files
  - Pytest keeps stdout-only (no clutter in repo logs/)
- **Tests**: 31 passed
- **Next session plan**: live smoke with file logs (done same weekend)
- **Session status**: class A file sink + startup purge path live

---

## 2026-08-08 (Phase 2 implementation)

- **Phase**: 2 – Slack Listener **implemented**
- **Completed**:
  - `src/cec_vivisystem/listener.py` — `normalize_slack_event`, `should_accept`, `handle_inbound`, `process_slack_message_event`, `format_reply`, `load_slack_config`, Socket Mode `main`/`run_socket_mode`
  - Models: `InboundMessage`, `ListenerOutcome`, `ListenerResult` in `models.py`
  - Dependencies: `slack-bolt`, `python-dotenv` (live path only; unit tests stay offline)
  - `tests/test_listener.py` — L1–L7 + normalize + reply/handle paths
  - Reply text never claims calendar write; ground rule 13 honored (env-only secrets)
  - Phase doc acceptance checked; architecture Listener → Done; README run note for Socket Mode
- **Tests**: 26 passed (15 prior + 11 listener); ruff clean
- **Issues / Friction**: none material
- **Resilience notes**: No durable store; class A logs only; write gate closed; live smoke is manual with local `.env`
- **Next session plan**: Decide Phase 3 from need (likely Confirmation before Calendar Writer). Do not start Calendar CUD.
- **Session status**: Phase 2 implementation complete (manual Slack smoke left to family when tokens exist)

---

## 2026-08-08 (Phase 2 scope)

- **Phase**: 2 – Slack Listener **scope only** (not implemented)
- **Completed**:
  - Confirmed git: `main` clean @ `800cc47` (Phase 1 weekend close); 15 tests green
  - Decided Phase 2 = **Option A – Slack Listener** (thin intake: allowlisted channel → existing `parse` → reply summary; no calendar / confirmation / LLM)
  - Wrote [phases/phase-2-listener.md](phases/phase-2-listener.md): goal, in/out, Socket Mode preference, unit test plan (L1–L7, ~8–9 tests), logging/retention class A only, acceptance criteria
  - Ground rule **13 – Secrets Stay Local**: credentials only in never-committed `.env`; names/placeholders in `.env.example`; never log secrets; tests secret-free
  - Expanded `.env.example` stubs: Slack (bot/app/signing/channel), Google Calendar (future), optional LLM placeholders
  - README + architecture: Phase 2 scoped; Listener row → Scoped
- **Tests**: unchanged (15 passed expected; no listener code yet)
- **Issues / Friction**: none
- **Resilience notes**: Phase 2 must keep default pytest offline; live Socket Mode is manual smoke only. No durable stores; write gate still closed.
- **Next session plan**: TDD implement Phase 2 from locked unit test plan (red tests → handler → optional Socket Mode smoke). Do not start Confirmation or Calendar Writer until Listener acceptance is met.
- **Session status**: Phase 2 scope locked; ready for implementation weekend

---

## 2026-08-05 (weekend close)

- **Phase**: 0 done · 1 done (design + implementation + growth stance on git)
- **Saved**: `main` @ `501c2f4` clean and pushed to `origin/main` (carter-yu/cec-vivisystem)
- **Tests last known**: 15 passed
- **Do not start next**: Listener, Calendar, Confirmation, or LLM parser until Phase 2 scope is decided
- **Next session plan**: Decide Phase 2 scope only (grow from need). Candidates: Slack Listener *or* calendar read-only + confirmation path — not LLM by default
- **Session status**: Closed for this weekend

---

## 2026-08-05 (growth stance)

- **Phase**: 1 complete; docs clarification only (no code behavior change)
- **Completed**:
  - Recorded explicit stance: **grow from need**; do not pre-schedule an LLM parser phase
  - Architecture §4.4 / §4.4.1: rules are current strategy; LLM optional later behind same contract + ADR
  - Phase 1 doc + `parser.py` module note aligned
- **Tests**: unchanged (15 passed expected)
- **Next session plan**: Decide Phase 2 scope from need (e.g. Listener or calendar path)—not LLM parser by default
- **Session status**: Stance documented for final review on git

---

## 2026-08-05 (implementation)

- **Phase**: 1 – Natural Language Parser (implemented)
- **Completed**:
  - `src/cec_vivisystem/models.py` — `IntentType`, `Confidence`, `ParseResult` (Phase 1 contract)
  - `src/cec_vivisystem/parser.py` — offline rule/heuristic `parse(message, *, now=, correlation_id=)`; weekday policy documented in module
  - Boundary logs: `parse_started` / `parse_completed` with `component`, `outcome`, `intent_type`, `duration_ms`, preview (structlog event name = boundary event)
  - `tests/test_parser.py` — F1–F5 create-event, 2 clarification, 2 unknown, contract + garbage + log boundary
  - CLI smoke: `uv run python -c "from cec_vivisystem.parser import main; main()"`
  - Acceptance criteria checked in `phases/phase-1-parser.md`; architecture Parser → Done
- **Tests**: 15 passed (12 parser + 3 hello); ruff clean
- **Issues / Friction**: structlog reserves keyword `event` — use event name as the log message positional arg
- **Resilience notes**: No network/LLM; parse never raises on garbage; class A logs only (no durable parse store)
- **Next session plan**: Decide Phase 2 scope only (do not implement Listener/Calendar until scope is explicit)
- **Session status**: Phase 1 implementation complete

---

## 2026-08-05

- **Phase**: 1 – scope + unit test plan + logging/retention standard (no swarm implementation)
- **Completed**:
  - Decided Phase 1: first swarm component = offline **Natural Language Parser** (create-event intent)
  - Wrote [phases/phase-1-parser.md](phases/phase-1-parser.md) with goal, contract fields, acceptance criteria, and explicit out-of-scope
  - **Locked unit test plan** in the same phase doc: ~11–12 tests in `tests/test_parser.py`
    - F1–F5 create-event fixtures (mixed Canto/English; fixed `now=2026-08-08 12:00 Asia/Hong_Kong`)
    - 2 needs_clarification, 2 unknown/empty, contract + no-raise + log boundary
    - Explicit non-tests: Slack, Calendar, LLM mocks, fuzzing
  - **Logging & retention standard** ([docs/logging-and-retention.md](docs/logging-and-retention.md), [ADR 0002](docs/decisions/0002-logging-and-retention.md)):
    - Per-component boundary log matrix (troubleshoot without replaying Slack/Calendar)
    - Retention: app logs 14d; audit 90d; pending state terminal+7d (max 30d); dead letters 30d; notes until family deletes
    - No store without purge story; soft disk caps; secrets never logged
  - **Elevated to system spine (all future phases)**:
    - [docs/unit-testing.md](docs/unit-testing.md) + [ADR 0003](docs/decisions/0003-unit-testing-standard.md)
    - Ground rules 4–5 + 12: unit tests + logging/retention mandatory; phases cannot waive
    - Resilience rewritten as permanent checklist (tests + logs + retention + calendar write gate)
    - Architecture §4.5–4.6: cross-cutting quality bars + phase document contract
    - Phase 0/1 headers + README: inherit non-waivable standards
  - Rationale: highest leverage before Listener/Calendar; fully offline so OAuth/Slack cannot burn the 1–2h session; contract-first for later components
- **Tests**: unchanged in code (plan only; Phase 0 still 3 passed)
- **Issues / Friction**: none
- **Resilience notes**: Unit testing + log/retention are now permanent system rules, not Phase 1 one-offs. Phase 1 still forbids calendar writers; parser ships with happy + failure paths and boundary logs (class A only).
- **Next session plan**: TDD implement Phase 1 — red tests from unit test plan, then green parser with logging fields from the standard. Do not start Slack or Google Calendar until Parser acceptance criteria are met.
- **Session status**: Scope + tests + log/retention policy locked as **system-wide** standards; ready for implementation weekend

---

## 2026-07-26

- **Phase**: 0 – Environment & Foundations
- **Completed**:
  - Project structure and core docs (philosophy, ground rules, architecture, resilience, ADR 0001)
  - Python 3.12 pinned; `uv` + lock file; `ruff` and `pytest` configured
  - Hatchling package install for `src/cec_vivisystem`
  - `src/cec_vivisystem/logging.py` (structlog) and `hello.py` proof-of-life module
  - `.env.example` present
  - Three unit tests passing; structured logging verified
- **Tests**: 3 passed
- **Issues / Friction**: Package import / editable install friction early on (resolved with hatchling build config)
- **Resilience notes**: Logging and tests in place from day one; no calendar writers yet
- **Repo**: Pushed to `carter-yu/cec-vivisystem` on GitHub
- **Next session plan**: Decide Phase 1 scope only; do not implement swarm components until that decision is explicit
- **Session status**: Closed for this weekend

---

## Template
### YYYY-MM-DD
- **Phase**:
- **Completed**:
- **Tests**:
- **Issues / Friction**:
- **Resilience notes**:
- **Next session plan**:
