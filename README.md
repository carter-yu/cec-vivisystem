# cec-vivisystem

A living family vivisystem for Carter, Elaine, and Cedric.

Built according to the principles in Kevin Kelly’s *Out of Control*:
bottom-up growth, decentralization, hive mind, and resilience through simple interacting components.

This is not a single monolithic AI agent.  
It is a small swarm of focused, replaceable parts that together serve the family.

## Language Policy
- **User interaction**: Cantonese + English
- **Code, documentation, design, comments, commits**: English only

## Current Status
See [PROGRESS.md](PROGRESS.md) — Phase 0–7 done (create-only Writer + read-only day list). `#family-plans`: create → yes/no → Google create; list queries (`tell me the events on 1 Sept 2026`, `2026年9月1日有乜`) reply a list with no confirmation. Pytest uses a fake Google client. Conflict checks are Phase 8. No LLM by default.

## Phases
- [Phase 0 – Environment & Foundations](phases/phase-0-environment.md) (done)
- [Phase 1 – Natural Language Parser](phases/phase-1-parser.md) (done)
- [Phase 2 – Slack Listener](phases/phase-2-listener.md) (done)
- [Phase 3 – Rule Parser Expansion](phases/phase-3-parser-expansion.md) (done)
- [Phase 4 – Confirmation Path](phases/phase-4-confirmation.md) (done — offline core)
- [Phase 4b – Slack confirmation](phases/phase-4b-slack-confirmation.md) (done — `#family-plans` proposal + thread yes/no)
- [Phase 5A – LifeNotesKeeper](phases/phase-5a-life-notes-keeper.md) (done — Option A raw capture)
- [Phase 5B – Slack wiring for LifeNotesKeeper](phases/phase-5b-life-notes-slack.md) (done — `#family-life-notes` dispatch)
- [Phase 6 – Calendar Writer](phases/phase-6-calendar-writer.md) (done — create-only, accepted confirmations)
- [Phase 7 – Calendar Reader](phases/phase-7-calendar-reader.md) (done — list/summary for a day)

## Quick Start
See [phases/phase-0-environment.md](phases/phase-0-environment.md) and [phases/phase-1-parser.md](phases/phase-1-parser.md)

```bash
uv sync
uv run pytest
uv run ruff check .
uv run python -c "from cec_vivisystem.hello import main; main()"
uv run python -c "from cec_vivisystem.parser import main; main()"
# Live Slack Socket Mode (requires local .env — never commit secrets):
# uv run python -c "from cec_vivisystem.listener import main; main()"
# Logs: stdout + logs/{component}-YYYY-MM-DD.log (archive/purge on start; never commit)
```

## Core Documents
- [Philosophy](docs/philosophy.md)
- [Ground Rules](docs/ground-rules.md) — binding for all phases
- [Architecture](docs/architecture.md) — includes cross-cutting quality bars
- [Resilience](docs/resilience.md)
- [Unit testing standard](docs/unit-testing.md) — every component / phase
- [Logging & data retention](docs/logging-and-retention.md) — every component / phase

Phase docs may narrow **scope**; they may **not** waive unit tests or logging/retention.
