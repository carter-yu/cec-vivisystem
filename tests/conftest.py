"""Shared pytest fixtures for cec-vivisystem."""

from __future__ import annotations

import pytest

from cec_vivisystem.logging import setup_logging


@pytest.fixture(autouse=True)
def configure_logging() -> None:
    """Ensure logging is configured for every test."""
    setup_logging(level="DEBUG")