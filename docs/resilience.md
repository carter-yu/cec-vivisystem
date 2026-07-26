# Resilience Standards

Minimum requirements for every component:

- Unit tests covering the happy path and at least one failure path
- Structured logging at component boundaries
- Explicit and visible failure modes
- No silent swallowing of errors

Before any component is allowed to write to Google Calendar:
- It must have a clear timeout / retry / fallback strategy
- Failures must be observable (logs + future dead-letter mechanism)

We maintain a lightweight way to flag items that need enhancement (GitHub issues with label `resilience` or entries in PROGRESS.md).