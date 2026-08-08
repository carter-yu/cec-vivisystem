"""Unit tests for file logging layout and archive/purge on startup."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cec_vivisystem.logging import (
    get_logger,
    local_today,
    log_filename,
    maintain_log_storage,
    setup_logging,
)

HKT = ZoneInfo("Asia/Hong_Kong")
FIXED_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=HKT)


def test_log_filename_pattern() -> None:
    assert log_filename("listener", date(2026, 8, 8)) == "listener-2026-08-08.log"
    assert log_filename("parser", date(2026, 8, 8)) == "parser-2026-08-08.log"
    assert "hello" in log_filename("hello/../x", date(2026, 1, 1))


def test_maintain_archives_previous_day_and_purges_old(
    tmp_path: Path,
) -> None:
    """Prior-day files move to archive/; older than 14d are deleted."""
    archive = tmp_path / "archive"
    today = local_today(FIXED_NOW)
    yesterday = today - timedelta(days=1)
    old = today - timedelta(days=20)

    (tmp_path / log_filename("listener", yesterday)).write_text(
        "old-active\n", encoding="utf-8"
    )
    (tmp_path / log_filename("listener", today)).write_text(
        "today\n", encoding="utf-8"
    )
    archive.mkdir()
    (archive / log_filename("parser", old)).write_text("stale\n", encoding="utf-8")

    result = maintain_log_storage(tmp_path, now=FIXED_NOW)

    assert result.archived == 1
    assert not (tmp_path / log_filename("listener", yesterday)).exists()
    assert (archive / log_filename("listener", yesterday)).exists()
    assert (tmp_path / log_filename("listener", today)).exists()
    assert not (archive / log_filename("parser", old)).exists()
    assert result.purged >= 1


def test_maintain_soft_cap_deletes_oldest(tmp_path: Path) -> None:
    """When over soft cap, oldest dated files are removed first."""
    archive = tmp_path / "archive"
    archive.mkdir()
    today = local_today(FIXED_NOW)
    # Three ~1KB-ish files; cap forces deletion
    for i, day in enumerate(
        [today - timedelta(days=3), today - timedelta(days=2), today]
    ):
        path = tmp_path / log_filename(f"svc{i}", day)
        path.write_bytes(b"x" * 400)

    result = maintain_log_storage(
        tmp_path,
        now=FIXED_NOW,
        soft_cap_bytes=500,
        retention_days=30,
    )
    remaining = list(tmp_path.glob("*.log")) + list(archive.glob("*.log"))
    assert result.soft_cap_deleted >= 1
    assert sum(p.stat().st_size for p in remaining if p.exists()) <= 500


def test_setup_logging_writes_per_component_files(tmp_path: Path) -> None:
    """Each component field lands in its own dated file; stdout still works."""
    setup_logging(
        level="INFO",
        log_dir=tmp_path,
        enable_file_logging=True,
        run_retention=True,
        now=FIXED_NOW,
    )
    log = get_logger("test")
    log.info("probe_listener", component="listener", outcome="success")
    log.info("probe_parser", component="parser", outcome="success")

    day = local_today(FIXED_NOW)
    listener_path = tmp_path / log_filename("listener", day)
    parser_path = tmp_path / log_filename("parser", day)
    system_path = tmp_path / log_filename("system", day)

    assert listener_path.is_file()
    assert parser_path.is_file()
    assert "probe_listener" in listener_path.read_text(encoding="utf-8")
    assert "probe_parser" in parser_path.read_text(encoding="utf-8")
    # Retention completion is logged under component=system
    assert system_path.is_file()
    assert "log_retention_completed" in system_path.read_text(encoding="utf-8")


def test_setup_logging_file_disabled_writes_nothing(tmp_path: Path) -> None:
    setup_logging(level="DEBUG", enable_file_logging=False)
    get_logger("test").info("no_file", component="listener")
    assert list(tmp_path.glob("**/*")) == [] or not any(tmp_path.rglob("*.log"))
