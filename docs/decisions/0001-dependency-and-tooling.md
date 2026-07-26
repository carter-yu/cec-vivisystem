# ADR 0001: Dependency Management and Tooling

## Status
Accepted

## Context
We need a fast, reliable, reproducible Python environment that a busy parent can set up and maintain in limited weekend time.

## Decision
- Python 3.12
- `uv` for dependency management and virtual environments
- `ruff` for linting + formatting
- `pytest` for testing
- `structlog` (or standard library with structured approach) for logging

## Consequences
Faster setup, better reproducibility, lower ongoing friction.