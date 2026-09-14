# Phase 16 – Google refresh-token expiry Slack reminder

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  

Google **Testing** Desktop OAuth refresh tokens expire **7 days** after consent (`invalid_grant`). This phase posts a `#family-plans` reminder at **10:00 Asia/Hong_Kong** when expiry is **3, 2, or 1** HKT calendar days away. It does **not** waive system standards.

This is **not** the architecture Reminder Agent (morning/evening family reminders). It is an operator/ops ping so Carter refreshes `.env` before list/write die.

## Goal

1. Know when the current refresh token was issued (`GOOGLE_REFRESH_TOKEN_ISSUED_AT` in local `.env`; never the token value).
2. At 10:00 HKT (same launchd as important-dates review), if remaining days ∈ {3, 2, 1}, post once to `#family-plans`.
3. Offline-testable with injectable `now=`, fake poster, fake post-marker store.

No Calendar Writer. No Listener parse path. No LLM. No auto re-consent.

## Why this phase (decision)

Incidents 2026-09-05 and 2026-09-12: parse/confirm succeeded; Google list/write failed `RefreshError` `invalid_grant`. Publishing the OAuth app is a family decision and may still not be available. A 10:00 ping is the cheap mitigation. Issued-at travels with `.env` (AirDrop / iCloud) so Mini stays in sync when the operator copies the file.

## Time box

1–2 hours. Green offline tests + CLI documented. launchd plist on Mini remains **operator stretch** (reuse the Phase 15 10:00 command).

## In Scope

### 1. Issued-at (no secret)

| Env | Meaning |
|-----|---------|
| `GOOGLE_REFRESH_TOKEN_ISSUED_AT` | ISO datetime or `YYYY-MM-DD` when the current refresh token was obtained |
| `GOOGLE_TOKEN_TTL_DAYS` | Optional; default **7** (Testing) |

`my-notes/get-google-refresh-token.py` writes both `GOOGLE_REFRESH_TOKEN` and `GOOGLE_REFRESH_TOKEN_ISSUED_AT` into MacBook `.env`. Copy **the whole `.env`** (or both lines) onto Mini. Never log the token.

Expiry day (HKT date) = issued HKT date + TTL days. Remaining = that date minus today. Example: issued 2026-09-13 → expires 2026-09-20; 10:00 on 17/18/19 → 3/2/1 days.

### 2. `run_google_token_reminder`

```text
run_google_token_reminder(*, now=, poster=, channel_id=, issued_at=, ttl_days=7, post_store=) -> GoogleTokenReminderResult
```

- Remaining ∈ {3, 2, 1} and not yet posted for that HKT date → post and mark.
- Else skip (`too_early`, `expired_or_today`, `missing_issued_at`, `already_posted`).
- Poster errors → `failed`; do **not** mark posted.
- Never claims a calendar write. Never logs tokens.

Slack text (exact):

| Days left | Text |
|-----------|------|
| 3 | `Google calendar will be expired in 3 days. Please refresh` |
| 2 | `Google calendar will be expired in 2 days. Please refresh` |
| 1 | `Google calendar will be expired in 1 day. Please refresh` |

### 3. Store + CLI

- Post markers: `data/google_token_reminders/` (class **C**, 30d purge on CLI start). One successful post per HKT date.
- CLI: `uv run python -c "from cec_vivisystem.google_token_reminder import main; main()"`
- Phase 15 `important_dates.main` also runs this reminder after the dates review (one 10:00 launchd). Missing issued-at is skip, not a failed important-dates job.

### 4. Unit test plan (`tests/test_google_token_reminder.py`)

`FIXED_ISSUED = 2026-09-13 22:31 Asia/Hong_Kong` (expires HKT date 2026-09-20). Fake poster. No network.

| ID | Input | Expect |
|----|--------|--------|
| G1 | `now=2026-09-17 10:00 HKT` | post 3-day text; one Slack call |
| G2 | `now=2026-09-18 10:00 HKT` | post 2-day text |
| G3 | `now=2026-09-19 10:00 HKT` | post 1-day text (`1 day`) |
| G4 | `now=2026-09-16 10:00 HKT` (4 days) | skip; no post |
| G5 | `now=2026-09-20 10:00 HKT` (0 days) | skip; no post |
| G6 | G1 twice same date | second `already_posted`; still one Slack call |
| G7 | `issued_at=None` | skip `missing_issued_at`; no crash |
| G8 | poster raises | `failed`; not marked posted |
| G9 | result contract fields | `outcome`, `days_left`, `post_text`, `duration_ms`, … |
| G10 | G1 with logging | `google_token_reminder_started` / posted or skipped |

**Non-tests:** live Slack, live Google, launchd plist, OAuth publish, auto token refresh, Reminder Agent.

## Out of Scope

- Publishing / verifying the OAuth app
- Refreshing the token from Slack
- Listener / parser / Writer behaviour
- Day-0 “expired today” ping
- 07:00 morning recap changes

## Logging / retention

`component=google_token_reminder`. Start/end with `outcome`, `days_left`, `duration_ms`; never tokens. Markers class **C** (`data/google_token_reminders/`, 30d). App logs class **A**. Not a calendar write.

## Acceptance

- [x] G1–G10 green offline; prior suite + ruff
- [x] Helper writes `GOOGLE_REFRESH_TOKEN_ISSUED_AT`; `.env.example` names it
- [x] 10:00 important-dates CLI also runs the reminder
- [x] README / architecture / PROGRESS updated; Mini plist still operator

## Done when

At 10:00 HKT, three/two/one days before Testing-token expiry, `#family-plans` gets one reminder that day, and pytest stays offline and secret-free.
