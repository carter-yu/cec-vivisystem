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

## Quick Start (Phase 0)
See [phases/phase-0-environment.md](phases/phase-0-environment.md)

```bash
uv sync
uv run pytest
uv run ruff check .
uv run python -c "from cec_vivisystem.hello import main; main()"
```

## Core Documents
- [Philosophy](docs/philosophy.md)
- [Ground Rules](docs/ground-rules.md)
- [Architecture](docs/architecture.md)
- [Resilience](docs/resilience.md)
