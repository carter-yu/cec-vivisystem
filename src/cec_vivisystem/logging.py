"""Centralized structured logging setup for cec-vivisystem.

File logs (class A application logs):
- Directory: ``<repo>/logs/`` (override with ``log_dir=`` or ``CEC_LOG_DIR``)
- Filename: ``{component}-YYYY-MM-DD.log`` using the machine's local calendar date
- On every ``setup_logging`` with file logging enabled: archive prior days + purge
  per [docs/logging-and-retention.md](../../docs/logging-and-retention.md)
  (14-day retention, 100 MB soft cap).

Stdout remains enabled for interactive sessions (e.g. Listener terminal).
"""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, TextIO

import structlog

# Class A defaults from docs/logging-and-retention.md
APP_LOG_RETENTION_DAYS = 14
APP_LOG_SOFT_CAP_BYTES = 100 * 1024 * 1024  # 100 MB

_COMPONENT_SAFE = re.compile(r"[^a-zA-Z0-9_-]+")
_LOG_NAME_RE = re.compile(
    r"^(?P<component>.+)-(?P<date>\d{4}-\d{2}-\d{2})\.log(?:\.gz)?$"
)

_file_logging_enabled = False
_log_dir: Path | None = None
_file_handles: dict[str, TextIO] = {}
_console_renderer = structlog.dev.ConsoleRenderer()
_file_renderer = structlog.processors.KeyValueRenderer(
    key_order=["event", "level", "timestamp", "component"]
)


def default_log_dir() -> Path:
    """Repository ``logs/`` directory (…/cec-vivisystem/logs)."""
    env = os.environ.get("CEC_LOG_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    # src/cec_vivisystem/logging.py → repo root is parents[2]
    return Path(__file__).resolve().parents[2] / "logs"


def local_today(now: datetime | None = None) -> date:
    """Local calendar date for log filenames."""
    if now is None:
        return datetime.now().astimezone().date()
    if now.tzinfo is None:
        return now.date()
    return now.astimezone().date()


def log_filename(component: str, day: date) -> str:
    """Return ``{component}-YYYY-MM-DD.log`` with a safe component segment."""
    safe = _safe_component(component)
    return f"{safe}-{day.isoformat()}.log"


def _safe_component(component: str) -> str:
    cleaned = _COMPONENT_SAFE.sub("_", (component or "system").strip())
    cleaned = cleaned.strip("_") or "system"
    return cleaned[:64]


@dataclass(frozen=True, slots=True)
class RetentionResult:
    """Outcome of archive + purge (for logs and tests)."""

    archived: int
    purged: int
    soft_cap_deleted: int
    log_dir: str


def maintain_log_storage(
    log_dir: Path,
    *,
    now: datetime | None = None,
    retention_days: int = APP_LOG_RETENTION_DAYS,
    soft_cap_bytes: int = APP_LOG_SOFT_CAP_BYTES,
) -> RetentionResult:
    """Archive prior-day active logs; purge past retention; enforce soft cap.

    Layout:
    - Active (today): ``logs/{component}-YYYY-MM-DD.log``
    - Archive: ``logs/archive/{component}-YYYY-MM-DD.log`` (moved from active
      when the file date is before local today)

    Purge deletes active and archive files whose date in the filename is older
    than ``today - retention_days`` (class A: 14 days). If total size still
    exceeds ``soft_cap_bytes``, delete oldest remaining files first.
    """
    log_dir = Path(log_dir)
    archive_dir = log_dir / "archive"
    log_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    today = local_today(now)
    cutoff = today - timedelta(days=retention_days)

    archived = _archive_previous_days(log_dir, archive_dir, today=today)
    purged = _purge_older_than(log_dir, archive_dir, cutoff=cutoff)
    soft_cap_deleted = _enforce_soft_cap(log_dir, archive_dir, soft_cap_bytes)

    return RetentionResult(
        archived=archived,
        purged=purged,
        soft_cap_deleted=soft_cap_deleted,
        log_dir=str(log_dir),
    )


def _archive_previous_days(
    log_dir: Path, archive_dir: Path, *, today: date
) -> int:
    moved = 0
    for path in sorted(log_dir.glob("*.log")):
        if not path.is_file():
            continue
        parsed = _parse_log_filename(path.name)
        if parsed is None:
            continue
        _component, file_day = parsed
        if file_day < today:
            dest = archive_dir / path.name
            if dest.exists():
                # Prefer keeping archive copy; drop duplicate active
                path.unlink(missing_ok=True)
            else:
                path.replace(dest)
            moved += 1
    return moved


def _purge_older_than(
    log_dir: Path, archive_dir: Path, *, cutoff: date
) -> int:
    deleted = 0
    for folder in (log_dir, archive_dir):
        for path in list(folder.glob("*.log")) + list(folder.glob("*.log.gz")):
            if not path.is_file():
                continue
            parsed = _parse_log_filename(path.name)
            if parsed is None:
                # Unknown name: fall back to mtime date
                file_day = datetime.fromtimestamp(
                    path.stat().st_mtime, tz=datetime.now().astimezone().tzinfo
                ).date()
            else:
                file_day = parsed[1]
            if file_day < cutoff:
                path.unlink(missing_ok=True)
                deleted += 1
    return deleted


def _enforce_soft_cap(
    log_dir: Path, archive_dir: Path, soft_cap_bytes: int
) -> int:
    files = [
        p
        for p in list(log_dir.glob("*.log"))
        + list(log_dir.glob("*.log.gz"))
        + list(archive_dir.glob("*.log"))
        + list(archive_dir.glob("*.log.gz"))
        if p.is_file()
    ]
    total = sum(p.stat().st_size for p in files)
    if total <= soft_cap_bytes:
        return 0

    def sort_key(p: Path) -> tuple[date, float]:
        parsed = _parse_log_filename(p.name)
        day = parsed[1] if parsed else date.min
        return (day, p.stat().st_mtime)

    deleted = 0
    for path in sorted(files, key=sort_key):
        if total <= soft_cap_bytes:
            break
        size = path.stat().st_size
        path.unlink(missing_ok=True)
        total -= size
        deleted += 1
    return deleted


def _parse_log_filename(name: str) -> tuple[str, date] | None:
    m = _LOG_NAME_RE.match(name)
    if not m:
        return None
    try:
        day = date.fromisoformat(m.group("date"))
    except ValueError:
        return None
    return m.group("component"), day


def _close_file_handles() -> None:
    global _file_handles
    for handle in _file_handles.values():
        try:
            handle.close()
        except OSError:
            pass
    _file_handles = {}


def _handle_for(component: str, day: date) -> TextIO | None:
    if not _file_logging_enabled or _log_dir is None:
        return None
    safe = _safe_component(component)
    key = f"{safe}:{day.isoformat()}"
    handle = _file_handles.get(key)
    if handle is not None and not handle.closed:
        return handle
    path = _log_dir / log_filename(safe, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a", encoding="utf-8")
    _file_handles[key] = handle
    return handle


def _dual_output_processor(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any] | str:
    """Write one line per component file, then render for stdout."""
    if _file_logging_enabled and _log_dir is not None:
        component = str(event_dict.get("component") or "system")
        day = local_today()
        # Prefer timestamp's date if present and parseable (ISO)
        ts = event_dict.get("timestamp")
        if isinstance(ts, str) and len(ts) >= 10:
            try:
                day = date.fromisoformat(ts[:10])
            except ValueError:
                pass
        handle = _handle_for(component, day)
        if handle is not None:
            try:
                line = _file_renderer(logger, method_name, dict(event_dict))
                if not isinstance(line, str):
                    line = str(line)
                handle.write(line + "\n")
                handle.flush()
            except OSError:
                pass

    return _console_renderer(logger, method_name, event_dict)


def _under_pytest() -> bool:
    return "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ


def setup_logging(
    level: str = "INFO",
    *,
    log_dir: Path | str | None = None,
    enable_file_logging: bool | None = None,
    run_retention: bool = True,
    now: datetime | None = None,
) -> RetentionResult | None:
    """Configure structured logging (stdout + optional dated component files).

    Args:
        level: Log level name (INFO, DEBUG, …).
        log_dir: Root log directory; default ``<repo>/logs`` or ``CEC_LOG_DIR``.
        enable_file_logging: When True, write ``{component}-YYYY-MM-DD.log`` files.
            Default: on for real runs; off under pytest unless forced True.
        run_retention: When file logging is on, archive/purge on this call (startup).
        now: Optional clock for retention/date decisions (tests).

    Returns:
        RetentionResult when file logging + retention ran; otherwise None.
    """
    global _file_logging_enabled, _log_dir

    if enable_file_logging is None:
        # Real services default to files; unit tests stay filesystem-clean.
        env_off = os.environ.get("CEC_LOG_TO_FILE", "").lower() in (
            "0",
            "false",
            "no",
        )
        enable_file_logging = not env_off and not _under_pytest()

    log_level = getattr(logging, level.upper(), logging.INFO)

    retention: RetentionResult | None = None
    _close_file_handles()

    if enable_file_logging:
        resolved = Path(log_dir) if log_dir is not None else default_log_dir()
        resolved = resolved.expanduser().resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        (resolved / "archive").mkdir(parents=True, exist_ok=True)
        _log_dir = resolved
        _file_logging_enabled = True
        if run_retention:
            retention = maintain_log_storage(resolved, now=now)
    else:
        _log_dir = None
        _file_logging_enabled = False

    # Root stdlib logger → stdout (Bolt and stdlib loggers still visible)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(log_level)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(stream_handler)

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        _dual_output_processor,
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )

    if retention is not None:
        # Log retention outcome into system file + console (after configure)
        get_logger(__name__).info(
            "log_retention_completed",
            component="system",
            outcome="success",
            archived=retention.archived,
            purged=retention.purged,
            soft_cap_deleted=retention.soft_cap_deleted,
            log_dir=retention.log_dir,
            retention_days=APP_LOG_RETENTION_DAYS,
        )

    return retention


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structured logger."""
    return structlog.get_logger(name)
