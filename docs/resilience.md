# Resilience Standards

These standards apply to **every component and every future phase**. They are not Phase 1-only.

Related binding docs:

- [Ground rules](ground-rules.md)
- [Unit testing](unit-testing.md) · [ADR 0003](decisions/0003-unit-testing-standard.md)
- [Logging & retention](logging-and-retention.md) · [ADR 0002](decisions/0002-logging-and-retention.md)
- [Architecture](architecture.md)

---

## 1. Minimum requirements (every component)

- Unit tests per [unit-testing.md](unit-testing.md): happy path, at least one failure/partial path, contract assertions, no crash on garbage, light log boundary
- Structured logging at component boundaries per [logging-and-retention.md](logging-and-retention.md)
- Explicit and visible failure modes
- No silent swallowing of errors
- Any persistent data classified with a **retention period and purge path**
- `correlation_id` propagated when work spans multiple components

## 2. Unit testing (summary)

| Rule | Bar |
|------|-----|
| Offline default | `uv run pytest` needs no network, Slack, or Google credentials |
| Determinism | Injectable clock / fixed `now` for time logic; family TZ `Asia/Hong_Kong` |
| Fixtures | Realistic inputs committed under `tests/` (mixed Canto/English when parsing language) |
| Phase gate | Phase doc locks named cases + non-tests before code |
| External APIs | Unit tests use mocks/fakes; live checks optional and opt-in |

Full rules: **[unit-testing.md](unit-testing.md)**.

## 3. Logging (summary)

Every public entry point logs **start** and **end** with at least:

`component`, `event`, `outcome` (on completion), `duration_ms` (on completion), and on failure `error_type` / `error_message`.

Never log secrets/tokens. Prefer truncated previews of message bodies at INFO.

Per-component event matrix: **[logging-and-retention.md](logging-and-retention.md)** §4.

## 4. Retention & purge (summary)

| Class | Retention |
|-------|-----------|
| Application logs | 14 days |
| Audit (confirm + calendar CUD) | 90 days |
| Operational / pending state | terminal + 7 days (max 30 days) |
| Dead letters | 30 days |
| Health samples | 14 days |
| Life notes (user content) | until family deletes |
| Google Calendar | source of truth; no full local mirror |

**No store without a purge story** (manual script acceptable before automation). Soft disk caps apply on the home Mac Mini.

Full rules: **[logging-and-retention.md](logging-and-retention.md)**.

## 5. Calendar write gate

Before any component is allowed to write to Google Calendar:

- Clear timeout / retry / fallback strategy
- Failures observable (logs + dead-letter path when the store exists)
- Write attempts and results recorded in the **audit** class (90 days)
- Human confirmation id present; writes without confirmation log ERROR/CRITICAL and must not succeed

## 6. Component definition of done (resilience)

A component may be marked done only when:

- [ ] Unit testing standard satisfied for its phase-locked cases
- [ ] Logging standard satisfied (boundary fields + no secrets)
- [ ] Persistent data (if any) has retention class + purge path documented
- [ ] Failure modes are explicit in code and tests
- [ ] `PROGRESS.md` updated with test results and any resilience debt

## 7. Continuous improvement

Flag hardening work with GitHub label `resilience` or entries in `PROGRESS.md`. Short-term debt is allowed if visible; silent gaps are not.
