"""Morning today-recap (Phase 12) — scheduled list of today's events.

Posts today's Google Calendar events to ``#family-plans`` (or the configured
plans channel). Empty days still post so the family knows the job ran.
Idempotent: one successful post per calendar date.

Not an orchestrator. Reuses ``list_calendar_events`` + ``format_event_list``.
No confirmation. No calendar write. No LLM. No freebusy.

Tests inject ``now=``, ``FakeCalendarClient``, and ``FakeSlackPoster``.
CLI: ``uv run python -c "from cec_vivisystem.morning_recap import main; main()"``

Retention: posted-date markers under gitignored ``data/morning_recap/``
(class **C**; purge older than 30 days on CLI start).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from cec_vivisystem.calendar_reader import format_event_list, list_calendar_events
from cec_vivisystem.calendar_writer import CalendarClient
from cec_vivisystem.logging import get_logger
from cec_vivisystem.models import (
    CalendarListOutcome,
    CalendarListResult,
    MorningRecapOutcome,
    MorningRecapResult,
)

logger = get_logger(__name__)

COMPONENT = "morning_recap"
FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
POSTED_RETENTION = timedelta(days=30)
EMPTY_RECAP = "早晨。今日（{day}）日曆冇活動。"
NONEMPTY_HEADER = "早晨。今日（{day}）活動："


class MorningRecapError(Exception):
    """Controlled morning-recap failure (config or store)."""


class MorningRecapConfigError(MorningRecapError):
    """Missing or invalid config (no secret values in message)."""


class SlackPoster(Protocol):
    """Injectable Slack post backend. Tests use ``FakeSlackPoster``."""

    def post(self, *, channel_id: str, text: str) -> None: ...


class MorningRecapStore(Protocol):
    """Idempotency markers: one successful post per calendar date."""

    def has_posted(self, recap_date: date) -> bool: ...

    def mark_posted(
        self,
        recap_date: date,
        *,
        posted_at: datetime,
        event_count: int = 0,
        channel_id: str | None = None,
    ) -> None: ...

    def delete(self, recap_date: date) -> None: ...

    def list_dates(self) -> list[date]: ...


class FakeSlackPoster:
    """Test double — records posts; never talks to Slack."""

    def __init__(self, *, fail_with: BaseException | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_with = fail_with

    def post(self, *, channel_id: str, text: str) -> None:
        self.calls.append((channel_id, text))
        if self.fail_with is not None:
            raise self.fail_with


class InMemoryMorningRecapStore:
    """Test/default store — no disk."""

    def __init__(self) -> None:
        self._posted: dict[date, dict[str, object]] = {}

    def has_posted(self, recap_date: date) -> bool:
        return recap_date in self._posted

    def mark_posted(
        self,
        recap_date: date,
        *,
        posted_at: datetime,
        event_count: int = 0,
        channel_id: str | None = None,
    ) -> None:
        self._posted[recap_date] = {
            "posted_at": posted_at,
            "event_count": event_count,
            "channel_id": channel_id,
        }

    def delete(self, recap_date: date) -> None:
        self._posted.pop(recap_date, None)

    def list_dates(self) -> list[date]:
        return list(self._posted.keys())


class JsonDirMorningRecapStore:
    """One JSON file per recap date under gitignored ``data/morning_recap/``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, recap_date: date) -> Path:
        return self.root / f"{recap_date.isoformat()}.json"

    def has_posted(self, recap_date: date) -> bool:
        return self._path(recap_date).is_file()

    def mark_posted(
        self,
        recap_date: date,
        *,
        posted_at: datetime,
        event_count: int = 0,
        channel_id: str | None = None,
    ) -> None:
        payload = {
            "recap_date": recap_date.isoformat(),
            "posted_at": posted_at.isoformat(),
            "event_count": event_count,
            "channel_id": channel_id,
        }
        path = self._path(recap_date)
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error(
                "morning_recap_store_save_failed",
                component=COMPONENT,
                outcome="failure",
                recap_date=recap_date.isoformat(),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise

    def delete(self, recap_date: date) -> None:
        path = self._path(recap_date)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.error(
                "morning_recap_store_delete_failed",
                component=COMPONENT,
                outcome="failure",
                recap_date=recap_date.isoformat(),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise

    def list_dates(self) -> list[date]:
        found: list[date] = []
        for path in self.root.glob("*.json"):
            try:
                found.append(date.fromisoformat(path.stem))
            except ValueError:
                continue
        return found


@dataclass(frozen=True, slots=True)
class MorningRecapConfig:
    """Runtime settings for the CLI (bot token + plans channel)."""

    bot_token: str
    channel_id: str
    calendar_id: str


class SlackWebPoster:
    """Live Slack ``chat.postMessage``. Not constructed by default pytest."""

    def __init__(self, token: str) -> None:
        self._token = token

    def post(self, *, channel_id: str, text: str) -> None:
        from slack_sdk import WebClient

        WebClient(token=self._token).chat_postMessage(channel=channel_id, text=text)


def default_data_dir() -> Path:
    """``<repo>/data/morning_recap``."""
    return Path(__file__).resolve().parents[2] / "data" / "morning_recap"


def load_morning_recap_config(
    env: Mapping[str, str] | None = None,
) -> MorningRecapConfig:
    """Load Slack post config from ``env`` (default: ``os.environ``).

    Google Calendar credentials are loaded separately via
    ``load_google_calendar_config``. Never includes secret values in errors.
    """
    source = env if env is not None else os.environ
    missing: list[str] = []
    bot = (source.get("SLACK_BOT_TOKEN") or "").strip()
    channel = (source.get("SLACK_FAMILY_PLANS_CHANNEL_ID") or "").strip()
    calendar_id = (source.get("GOOGLE_CALENDAR_ID") or "").strip() or "primary"
    if not bot:
        missing.append("SLACK_BOT_TOKEN")
    if not channel:
        missing.append("SLACK_FAMILY_PLANS_CHANNEL_ID")
    if missing:
        raise MorningRecapConfigError(
            "Missing required morning recap configuration: " + ", ".join(missing)
        )
    return MorningRecapConfig(
        bot_token=bot,
        channel_id=channel,
        calendar_id=calendar_id,
    )


def maintain_morning_recap_storage(
    store: MorningRecapStore,
    *,
    now: datetime | None = None,
    retention: timedelta = POSTED_RETENTION,
) -> int:
    """Delete posted-date markers older than ``retention`` (class C)."""
    local = _normalize_now(now)
    cutoff = local.date() - retention
    deleted = 0
    for recap_date in list(store.list_dates()):
        if recap_date < cutoff:
            try:
                store.delete(recap_date)
                deleted += 1
            except OSError:
                continue
    return deleted


def run_morning_recap(
    *,
    now: datetime | None = None,
    client: CalendarClient,
    poster: SlackPoster,
    calendar_id: str | None = None,
    channel_id: str,
    store: MorningRecapStore | None = None,
    correlation_id: str | None = None,
) -> MorningRecapResult:
    """List today's HKT events and post once. Never writes the calendar."""
    started = time.perf_counter()
    local = _normalize_now(now)
    recap_date = local.date()
    corr = correlation_id or str(uuid.uuid4())
    recap_store = store if store is not None else JsonDirMorningRecapStore(
        default_data_dir()
    )
    time_min, time_max = _today_window(local)

    logger.info(
        "morning_recap_started",
        component=COMPONENT,
        correlation_id=corr,
        recap_date=recap_date.isoformat(),
        channel_id=channel_id,
        time_min=time_min.isoformat(),
        time_max=time_max.isoformat(),
    )

    if recap_store.has_posted(recap_date):
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "morning_recap_skipped",
            component=COMPONENT,
            correlation_id=corr,
            outcome="skipped",
            recap_date=recap_date.isoformat(),
            reason="already_posted",
            duration_ms=duration_ms,
        )
        return MorningRecapResult(
            outcome=MorningRecapOutcome.SKIPPED_ALREADY_POSTED,
            recap_date=recap_date,
            channel_id=channel_id,
            duration_ms=duration_ms,
        )

    listed = list_calendar_events(
        time_min=time_min,
        time_max=time_max,
        client=client,
        calendar_id=calendar_id,
        correlation_id=corr,
    )
    if listed.outcome != CalendarListOutcome.SUCCESS:
        return _failed(
            started,
            recap_date=recap_date,
            channel_id=channel_id,
            correlation_id=corr,
            error_type=listed.error_type or "list_failed",
            error_message=listed.error_message or "calendar list failed",
        )

    post_text = _format_morning_post(listed, recap_date)
    try:
        poster.post(channel_id=channel_id, text=post_text)
    except Exception as exc:  # noqa: BLE001 — boundary: do not mark posted
        return _failed(
            started,
            recap_date=recap_date,
            channel_id=channel_id,
            correlation_id=corr,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

    try:
        recap_store.mark_posted(
            recap_date,
            posted_at=local,
            event_count=len(listed.events),
            channel_id=channel_id,
        )
    except OSError as exc:
        # Post already reached Slack; still report posted so a retry skip is
        # preferred over a duplicate if the marker cannot be written.
        logger.error(
            "morning_recap_store_save_failed",
            component=COMPONENT,
            correlation_id=corr,
            outcome="failure",
            recap_date=recap_date.isoformat(),
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "morning_recap_posted",
        component=COMPONENT,
        correlation_id=corr,
        outcome="success",
        recap_date=recap_date.isoformat(),
        channel_id=channel_id,
        event_count=len(listed.events),
        duration_ms=duration_ms,
    )
    return MorningRecapResult(
        outcome=MorningRecapOutcome.POSTED,
        recap_date=recap_date,
        channel_id=channel_id,
        event_count=len(listed.events),
        post_text=post_text,
        duration_ms=duration_ms,
    )


def _format_morning_post(listed: CalendarListResult, recap_date: date) -> str:
    """Thin today header plus ``format_event_list`` item lines."""
    day = recap_date.isoformat()
    if not listed.events:
        return EMPTY_RECAP.format(day=day)
    formatted = format_event_list(listed)
    bullets = [line for line in formatted.splitlines() if line.startswith("•")]
    body = "\n".join(bullets) if bullets else formatted
    return f"{NONEMPTY_HEADER.format(day=day)}\n{body}"


def _today_window(now: datetime) -> tuple[datetime, datetime]:
    start = datetime.combine(now.date(), dt_time(0, 0), tzinfo=FAMILY_TZ)
    return start, start + timedelta(days=1)


def _normalize_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(tz=FAMILY_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=FAMILY_TZ)
    return now.astimezone(FAMILY_TZ)


def _failed(
    started: float,
    *,
    recap_date: date,
    channel_id: str,
    correlation_id: str,
    error_type: str,
    error_message: str,
) -> MorningRecapResult:
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.error(
        "morning_recap_failed",
        component=COMPONENT,
        correlation_id=correlation_id,
        outcome="failure",
        recap_date=recap_date.isoformat(),
        channel_id=channel_id,
        duration_ms=duration_ms,
        error_type=error_type,
        error_message=error_message,
    )
    return MorningRecapResult(
        outcome=MorningRecapOutcome.FAILED,
        recap_date=recap_date,
        channel_id=channel_id,
        error_type=error_type,
        error_message=error_message,
        duration_ms=duration_ms,
    )


def main() -> None:
    """CLI entry: load ``.env`` if present, list today, post once."""
    from dotenv import load_dotenv

    from cec_vivisystem.calendar_writer import (
        CalendarWriterConfigError,
        GoogleCalendarClient,
        load_google_calendar_config,
    )
    from cec_vivisystem.logging import setup_logging

    load_dotenv()
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    setup_logging(level=log_level)

    try:
        slack_cfg = load_morning_recap_config()
        google_cfg = load_google_calendar_config()
    except (MorningRecapConfigError, CalendarWriterConfigError) as exc:
        logger.error(
            "morning_recap_failed",
            component=COMPONENT,
            outcome="failure",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        raise SystemExit(1) from exc

    store = JsonDirMorningRecapStore(default_data_dir())
    maintain_morning_recap_storage(store)
    result = run_morning_recap(
        client=GoogleCalendarClient(google_cfg),
        poster=SlackWebPoster(slack_cfg.bot_token),
        calendar_id=google_cfg.calendar_id,
        channel_id=slack_cfg.channel_id,
        store=store,
    )
    if result.outcome == MorningRecapOutcome.FAILED:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
