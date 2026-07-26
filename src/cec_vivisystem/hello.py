"""Minimal proof-of-life module for Phase 0."""

from __future__ import annotations

from cec_vivisystem.logging import get_logger, setup_logging

logger = get_logger(__name__)


def main() -> None:
    """Simple entry point to verify environment and logging."""
    setup_logging()
    logger.info("cec-vivisystem is alive", component="hello", phase="0")
    print("Hello from cec-vivisystem!")


if __name__ == "__main__":
    main()