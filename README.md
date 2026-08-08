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
See [PROGRESS.md](PROGRESS.md)

## Phases
- [Phase 0 – Environment & Foundations](phases/phase-0-environment.md) (done)
- [Phase 1 – Natural Language Parser](phases/phase-1-parser.md) (done)
- [Phase 2 – Slack Listener](phases/phase-2-listener.md) (done)

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
```

## Core Documents
- [Philosophy](docs/philosophy.md)
- [Ground Rules](docs/ground-rules.md) — binding for all phases
- [Architecture](docs/architecture.md) — includes cross-cutting quality bars
- [Resilience](docs/resilience.md)
- [Unit testing standard](docs/unit-testing.md) — every component / phase
- [Logging & data retention](docs/logging-and-retention.md) — every component / phase

Phase docs may narrow **scope**; they may **not** waive unit tests or logging/retention.
