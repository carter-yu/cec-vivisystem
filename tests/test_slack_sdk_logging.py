"""Phase 30b: Slack SDK / Bolt stdlib logs kept in a rotating, redacted file."""

from __future__ import annotations

import logging
import logging.handlers
from datetime import datetime
from pathlib import Path

import pytest

from cec_vivisystem.logging import (
    SLACK_SDK_LOG_NAME,
    maintain_log_storage,
    redact_log_text,
    setup_logging,
)


@pytest.fixture
def sdk_log(tmp_path: Path):
    """Returns a starter; logging is set up inside the test body because the
    console handler binds the stdout that capsys installs for the call phase."""

    def start() -> Path:
        setup_logging("INFO", log_dir=tmp_path, enable_file_logging=True, run_retention=False)
        return tmp_path / SLACK_SDK_LOG_NAME

    yield start
    setup_logging("INFO", enable_file_logging=False)


def _lines(path: Path) -> list[str]:
    for handler in logging.getLogger("slack_sdk").handlers:
        handler.flush()
    return path.read_text(encoding="utf-8").splitlines()


def test_s1_sdk_and_bolt_records_reach_the_file_once(sdk_log, capsys) -> None:
    path = sdk_log()
    logging.getLogger("slack_sdk.socket_mode.builtin.client").info(
        "A new session has been established (session id: s-1)"
    )
    logging.getLogger("slack_bolt.App").error(
        "on_error invoked (session id: s-1, error: BrokenPipeError, message: Broken pipe)"
    )
    logging.getLogger("slack_sdk.web.base_client").debug("request body: secret payload")
    logging.getLogger("cec_vivisystem.other").info("not an SDK record")
    lines = _lines(path)
    assert len(lines) == 2
    assert "INFO slack_sdk.socket_mode.builtin.client" in lines[0]
    assert "ERROR slack_bolt.App" in lines[1] and "BrokenPipeError" in lines[1]
    assert capsys.readouterr().out.count("A new session has been established") == 1


def test_s1_repeated_setup_does_not_duplicate_handlers(tmp_path: Path) -> None:
    for _ in range(3):
        setup_logging("INFO", log_dir=tmp_path, enable_file_logging=True, run_retention=False)
    logging.getLogger("slack_sdk").warning("one line")
    assert len(_lines(tmp_path / SLACK_SDK_LOG_NAME)) == 1
    setup_logging("INFO", enable_file_logging=False)
    assert not any(
        isinstance(h, logging.handlers.RotatingFileHandler)
        for h in logging.getLogger("slack_sdk").handlers
    )


def test_s2_tokens_tickets_and_text_are_redacted(sdk_log, capsys) -> None:
    path = sdk_log()
    logging.getLogger("slack_bolt.App").warning(
        "token %s app %s url wss://example.invalid/link/?ticket=abc-123&app_id=A1 "
        "body {'text': 'family secret', 'type': 'message'}",
        "xoxb-1111-2222-abcdEFGH", "xapp-1-A111-222-ffff",
    )
    line = _lines(path)[0]
    out = capsys.readouterr().out
    for text in (line, out):
        assert "xoxb-1111" not in text and "xapp-1-A111" not in text
        assert "abc-123" not in text and "family secret" not in text
        assert "xox?-[redacted]" in text and "'text': '[redacted]'" in text
    assert redact_log_text("plain") == "plain"


def test_retention_leaves_the_rotating_sdk_file_alone(tmp_path: Path) -> None:
    sdk = tmp_path / SLACK_SDK_LOG_NAME
    sdk.write_text("x" * 2048, encoding="utf-8")
    old = tmp_path / "listener-2026-01-01.log"
    old.write_text("y", encoding="utf-8")
    result = maintain_log_storage(
        tmp_path, now=datetime(2026, 10, 15, 12, 0).astimezone(), soft_cap_bytes=10
    )
    assert sdk.exists() and not (tmp_path / "archive" / SLACK_SDK_LOG_NAME).exists()
    assert result.purged == 1
