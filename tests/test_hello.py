"""Tests for the hello module."""

from __future__ import annotations

from cec_vivisystem.hello import main
from cec_vivisystem.logging import get_logger, setup_logging


def test_setup_logging_does_not_raise() -> None:
    """Logging setup should work without errors."""
    setup_logging(level="INFO")


def test_get_logger_returns_logger() -> None:
    """get_logger should return a usable logger."""
    setup_logging()
    log = get_logger("test")
    assert log is not None
    log.info("test message", test=True)


def test_main_runs_without_error() -> None:
    """main() should execute cleanly."""
    main()