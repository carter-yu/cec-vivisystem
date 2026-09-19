# Project takeover review — 2026-09-19

**Reviewer and implementer:** OpenAI GPT-6 Astra (Codex; Carter's AI assistant). The exact serving snapshot is not exposed in this session.
**Baseline:** commit `684d165`; local working tree reviewed and amended.
**Result:** completed local review and reliability fixes; 281 offline tests pass, Ruff passes, and `git diff --check` passes. No deployment, live Slack messages, Google writes, or paid model calls were performed. Carter subsequently authorized committing and pushing the verified changes with OpenAI GPT-6 Astra attribution.

## 1. Requirements and family use cases

The requirements come from README, PROGRESS, ground rules, architecture, resilience/testing/retention standards, ADRs 0001–0005, and the phase specifications. Historical phase exclusions are distinguished from defects in shipped capabilities.

| Use case / invariant | Implementation and assessment |
| --- | --- |
| Family schedules in Cantonese and English, Traditional Chinese | `parser.py` extracts known phrases deterministically. Relative dates use HKT. Fixed explicit morning colon times and invalid AM/PM clocks; reinforced rejection of known Simplified forms before fallback. |
| Propose an activity, then explicitly accept/reject in its Slack thread | `listener.py` and `confirmation.py`. Preserved the permanent human gate. Fixed expiry enforcement at resolution and showed effective start/end in proposals. |
| Warn about overlapping activities, including the same participant | `overlap.py` reads Google; warnings never prohibit acceptance. Fixed all-day window alignment with the Writer and complete Google pagination. |
| One event per accepted confirmation, including repeated yes | `calendar_writer.py`. Existing audit-only protection did not cover lost responses, failed audit persistence, or concurrent intake. Added stable Google event IDs and verified conflict recovery; serialized intake within one listener process. |
| Read a day, week, month, or date range without confirmation | `calendar_reader.py` and listener. Preserved explicit replies on empty/error results. Fixed pagination and multi-day events that previously appeared under dates outside the requested range. |
| Capture exact family notes independently of scheduling | `life_notes.py`. Raw text remains unchanged, class F. Slack redeliveries now reuse a source-derived ID and survive restart without duplicate notes. |
| Store yearly birthdays and one-off markers immediately | `important_dates.py`, parser, listener. Separate class-F catalog, no Calendar write. Fixed yearly February 29 creation in non-leap years and invalid-date fallback routing. |
| 07:00 recap, including empty days | `morning_recap.py`; reads today and posts with a date marker. Scheduling is external launchd configuration, not part of the running listener. |
| 10:00 upcoming-date review and token warning | `important_dates.py`, `google_token_reminder.py`. Existing occurrence/window and 3/2/1-day policies preserved. JSON marker writes made atomic. |
| Optional LLM only for novel create-looking phrases | `parse_fallback.py`, versioned prompt, miss store. Known phrases still avoid the LLM; no model tools or direct calendar writes. Fixed non-create clarification hijacking, invalid payload acceptance, and diagnostic-store failures blocking useful results. |
| Troubleshoot without losing family content | Structured logs and classified stores. Fixed log-open failures, local-date rollover, stale handles, archive collisions, and non-atomic JSON overwrites. |

Calendar update/delete, freebusy, the full Reminder Agent, health Observer, note enrichment/search, and important-date edit/delete UI remain explicitly planned capabilities, not completed features accidentally missing from this patch.

## 2. Architecture assessment

The implementation is a small Python package with injectable I/O boundaries and dataclass contracts. Slack intake routes plans to Parser → Confirmation → Calendar Writer, while notes and important dates use independent stores. Scheduled recap/date/token commands run separately. Google remains the source of truth for calendar events; local files hold confirmations, audit records, markers, notes, important dates, and parse misses.

This is a sound fit for a small family installation: simple deployment, offline tests, no database service, and explicit confirmation. The architecture document's description of event/message-based coordination is aspirational: current dispatch uses synchronous function calls. The listener is the shared intake dependency for interactive use, though scheduled jobs are independent. This distinction should guide future changes rather than pretending the deployed code is already a distributed event system.

The large listener, parser, and Calendar Writer modules combine several responsibilities. A broad rewrite would risk the established phrase fixtures and require more operational change than these defects justify. This patch keeps the contracts and storage schemas, removes redundant parser branches, and adds one shared atomic-write helper. Future extraction of Google transport, Slack posting, and scheduling-window utilities can be incremental.

## 3. Confirmed findings and fixes

Severity reflects the pre-fix impact. All rows below are implemented locally and covered by new regression cases or the existing suite.

| ID | Severity | Finding / evidence | Fix |
| --- | --- | --- | --- |
| F01 | High | A fake Google insert committed, raised BrokenPipeError, then the automatic retry created another event. Another call without an audit row created a third. | Stable SHA-256 event ID derived from confirmation ID. On HTTP 409, fetch the event and verify its confirmation property and non-cancelled status. Local audit remains an optimization and history, not the only duplicate guard. |
| F02 | High | Repeated Slack delivery minted new confirmation IDs and raw note IDs. | Confirmation IDs use channel + source message ID; existing terminal state is preserved. Slack notes opt into source-based deduplication. Standalone note creation keeps its existing ID contract. |
| F03 | High | Bolt workers could overlap JSON state transitions and use the shared Google transport concurrently. | A process-local intake lock serializes the supported listener boundary. Concurrent duplicate dispatch is tested. Multiple listener processes sharing a store remain unsupported. |
| F04 | High | All-day LLM creates could lack a start; string `false` became true; reversed end and nonempty missing-fields could remain creates. Incomplete list requests could invoke create fallback. | Validate these fields before proposals; block list/important-date clarification and parser-error routes from create fallback. The Writer additionally refuses non-create intent and nonpositive duration. |
| F05 | Medium | Google list ignored `nextPageToken`; a second-page failure could be hidden by never fetching it. | Fetch all pages, including empty intermediate pages. Reject malformed/repeated pagination tokens; page failures fail the read. This also improves overlap and recap completeness. |
| F06 | Medium | Direct `resolve_confirmation` accepted a pending record at/after its expiry unless a separate sweep ran. Expired thread replies disappeared. | Persist expiry and refuse direct acceptance; expired thread replies explain that a new request is needed. |
| F07 | Medium | Proposal omitted end/default duration and still said the Writer was a later phase. All-day overlap could check 09:00–09:00 while Writer created midnight–midnight. | Show effective start and exclusive all-day end; normalize all-day overlap windows to Writer behavior; remove obsolete UI copy. |
| F08 | Medium | Second yes without a configured Calendar claimed “already added”; an audit-read exception also returned that success text. Generic write failures claimed no event existed despite uncertain remote outcome. | Truthful unavailable/error replies. Ambiguous failures say the result could not be verified and direct retries to the same confirmation thread. Intake/store failures also produce visible replies. |
| F09 | Medium | Explicit morning colon times on today fell through to the afternoon heuristic. Invalid 13am/0pm could become valid timestamps. An explicitly reversed year range was silently advanced. | Explicit morning wins; invalid AM/PM clocks do not create; reversed explicitly dated ranges clarify. Existing bare-time family conventions are retained. |
| F10 | Medium | Yearly February 29 used the current non-leap year for validation and could not be stored. Invalid important dates could reach the create LLM. | Use a leap-capable carrier year only for yearly February 29. Mark invalid important-date inputs so fallback cannot change their intent. |
| F11 | Medium | Store `write_text` truncated an existing record before a complete replacement was durable. | All seven JSON store modules now flush/fsync a private temporary sibling and atomically replace the destination. A simulated rename failure preserves the original confirmation and removes the temporary file. |
| F12 | Medium | Parse-miss recording errors discarded an otherwise useful parse/proposal. | Log the diagnostic failure and retain the useful result. |
| F13 | Medium | A log-file open error escaped into business logic. UTC filename dates disagreed with local retention dates; old handles accumulated; retention ran only at startup; archive collisions deleted the later segment. | Catch runtime file errors and retain console output; use local filename dates; close handles and run retention at rollover; append archive collisions before removing the active segment. |
| F14 | Medium | Simplified inputs rejected by the rule vocabulary could be accepted through the LLM fallback. | Reject known distinctive scheduling characters before both routes; reject those markers in model-generated title/location. This is a vocabulary rejection set, not a complete Chinese script classifier. |
| F15 | Medium | Multi-day recap grouped only by original start, showing a trip under a date before the requested window and omitting occupied later days. | Clip to the query window and show every occupied day, respecting exclusive end. Timed continuations are marked. |

The Google integration changes follow the official [event creation guide](https://developers.google.com/workspace/calendar/api/guides/create-events), [insert reference](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert), and [list reference](https://developers.google.com/workspace/calendar/api/v3/reference/events/list), checked during review. Client-supplied IDs address retries after a successful remote insert whose response is lost; pagination is required to obtain the complete result set.

## 4. Remaining risks and improvement backlog

These are explicit limits of the reviewed system, not claims that every conceivable defect has been eliminated.

| Priority | Remaining issue | Recommended next slice |
| --- | --- | --- |
| P1 operational | Slack post and local posted-marker save are separate operations. A crash or marker failure after Slack accepted a post can duplicate a scheduled message on retry. Atomic files do not make this a cross-service transaction. | Define delivery/reconciliation policy, retain Slack response handles, and add injected crash-window tests. Until then, avoid automatic repeated reruns after ambiguous posting failures. |
| P1 operational | New stable Calendar IDs protect new writes. Historical events used Google-generated IDs; if their local success audits are lost, this patch cannot infer their new IDs. Existing duplicates are not cleaned up. | Preserve existing data during rollout; inspect legacy uncertain confirmations before retrying them. Any reconciliation/cleanup should be a separate reviewed operation. |
| P2 | JSON stores have permissive deserialization, and some skip unreadable records. Corruption can appear as absent content; there is no implemented backup/restore workflow for class-F data. | Add schema validation, visible corruption status, and a tested backup/restore command without deleting damaged originals. |
| P2 | Operational retention mostly runs at startup. Logs now also rotate/purge daily, but confirmations/audit/miss rows can exceed retention during a long uninterrupted listener run. Disk caps are not continuously enforced. | Schedule the existing maintenance functions or add a small independent maintenance command. |
| P2 | Model telemetry records model/tokens/latency, but does not meet every field in the referenced LLM-learning constitution; there is no recorded quality evaluation proving novel phrase accuracy. SDK per-request timeouts are not a proven whole-interaction latency bound. | Add prompt hash, provider/finish/request metadata, explicit extraction budgets, and a small recorded/fake evaluation corpus before changing models or prompts. |
| P2 | Common Simplified markers are blocked, but script detection is not exhaustive. Natural-language title matching remains ordered and can lose qualifiers or choose one activity from a compound request. | Expand synthetic negative/ambiguous fixtures from observed misses; split parser rules by intent when adding new behavior. Keep explicit confirmation. |
| P2 | Thread yes/no selects the latest pending proposal. Multiple different proposals in one thread can be confusing; there is no explicit proposal selector. | Define one-active-proposal or proposal-specific reply policy before expanding conversational editing. |
| P2 | Serialization is process-local and includes slow external calls. A slow request can delay later requests. Scheduled jobs are not locked against overlapping process runs. | Keep one listener and one scheduled invocation at a time; move to scoped locks/transactional persistence only when concurrency is a real need. |
| P2 | Daily recap and token/date commands rely on separately installed launchd jobs and local timezone/configuration. No automatic health observer proves they ran. | Verify Mini configuration, then add a small independent last-success health check. |
| P3 | Engineering docs and historical phase prose contain drift; some older public examples do not meet the newest synthetic-fixture policy. | Clean current examples and consolidate current behavior documentation incrementally; do not rewrite history as part of routine maintenance. |

No credentials, `.env` values, live family stores, or incident-log bodies were needed for this review. Their contents were not included in the findings. An earlier third-party assessment was consulted as a set of hypotheses and checked against the code, not accepted as authoritative. It was subsequently removed at the user’s request; this review and the maintenance handover stand independently.

## 5. Verification and scope of confidence

- Baseline: **234 tests passed**, Ruff clean.
- Added **47 regression cases** in `tests/test_review_regressions.py` covering transport responses, pagination, expiry, concurrency, disk failure, duplicate delivery, parsing/fallback validation, logging, and period recap.
- First regression batch demonstrated **27 failures** before fixes; further focused cases exposed additional faults before their implementation.
- Final: **281 tests passed** using `.venv/bin/python -m pytest -q`; `.venv/bin/python -m ruff check .` and `git diff --check` passed.
- Tests use synthetic messages, fixed clocks, temporary directories, fake Slack/Google/LLM boundaries, and simulated provider requests. No external account mutation or live delivery is proven by this suite.
- Production modules and cross-component paths were reviewed; existing tests and phase requirements informed the traceability above. This is a code/contract review, not a production penetration test, exhaustive language proof, or disaster-recovery exercise.

## 6. Takeover handover

Start subsequent sessions with [maintenance notes](maintenance.md), [architecture](architecture.md), and the newest PROGRESS entry. Root `AGENTS.md` points future coding sessions to the project constraints and the review backlog. New features should continue from the existing system, retain offline fakes and the confirmation gate, and update phase/test/retention documentation.

**Signed:** OpenAI GPT-6 Astra (Codex; Carter's AI assistant), 2026-09-19, Asia/Hong_Kong.
