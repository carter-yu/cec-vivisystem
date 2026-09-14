"""Google refresh-token expiry reminder (Phase 16).

Posts to ``#family-plans`` at 10:00 HKT when a Testing-mode refresh token
expires in 3, 2, or 1 HKT calendar days. Issued-at comes from env
(``GOOGLE_REFRESH_TOKEN_ISSUED_AT``); the token value is never logged.

Not the architecture Reminder Agent. Not a calendar write. No LLM.

CLI: ``uv run python -c "from cec_vivisystem.google_token_reminder import main; main()"``
Also run from ``important_dates.main`` (same 10:00 launchd).

Retention: posted-date markers under gitignored ``data/google_token_reminders/``
(class **C**; purge older than 30 days on CLI start).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from cec_vivisystem.logging import get_logger
from cec_vivisystem.models import GoogleTokenReminderOutcome, GoogleTokenReminderResult
from cec_vivisystem.morning_recap import SlackPoster, SlackWebPoster

logger = get_logger(__name__)

COMPONENT = "google_token_reminder"
FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
POSTED_RETENTION = timedelta(days=30)
DEFAULT_TTL_DAYS = 7
REMIND_DAYS = frozenset({1, 2, 3})
ENV_ISSUED_AT = "GOOGLE_REFRESH_TOKEN_ISSUED_AT"
ENV_TTL_DAYS = "GOOGLE_TOKEN_TTL_DAYS"


class GoogleTokenReminderError(Exception):
    """Controlled token-reminder failure (config or store)."""


class GoogleTokenReminderConfigError(GoogleTokenReminderError):
    """Missing or invalid config (no secret values in message)."""


class GoogleTokenReminderStore(Protocol):
    """Idempotency markers: one successful reminder post per HKT date."""

    def has_posted(self, reminder_date: date) -> bool: ...

    def mark_posted(
        self,
        reminder_date: date,
        *,
        posted_at: datetime,
        days_left: int,
        channel_id: str | None = None,
    ) -> None: ...

    def delete(self, reminder_date: date) -> None: ...

    def list_dates(self) -> list[date]: ...


class InMemoryGoogleTokenReminderStore:
    """Test/default store — no disk."""

    def __init__(self) -> None:
        self._posted: dict[date, dict[str, object]] = {}

    def has_posted(self, reminder_date: date) -> bool:
        return reminder_date in self._posted

    def mark_posted(
        self,
        reminder_date: date,
        *,
        posted_at: datetime,
        days_left: int,
        channel_id: str | None = None,
    ) -> None:
        self._posted[reminder_date] = {
            "posted_at": posted_at,
            "days_left": days_left,
            "channel_id": channel_id,
        }

    def delete(self, reminder_date: date) -> None:
        self._posted.pop(reminder_date, None)

    def list_dates(self) -> list[date]:
        return list(self._posted.keys())


class JsonDirGoogleTokenReminderStore:
    """One JSON file per reminder date under ``data/google_token_reminders/``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, reminder_date: date) -> Path:
        return self.root / f"{reminder_date.isoformat()}.json"

    def has_posted(self, reminder_date: date) -> bool:
        return self._path(reminder_date).is_file()

    def mark_posted(
        self,
        reminder_date: date,
        *,
        posted_at: datetime,
        days_left: int,
        channel_id: str | None = None,
    ) -> None:
        payload = {
            "reminder_date": reminder_date.isoformat(),
            "posted_at": posted_at.isoformat(),
            "days_left": days_left,
            "channel_id": channel_id,
        }
        path = self._path(reminder_date)
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error(
                "google_token_reminder_store_save_failed",
                component=COMPONENT,
                outcome="failure",
                reminder_date=reminder_date.isoformat(),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise

    def delete(self, reminder_date: date) -> None:
        path = self._path(reminder_date)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.error(
                "google_token_reminder_store_delete_failed",
                component=COMPONENT,
                outcome="failure",
                reminder_date=reminder_date.isoformat(),
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


def default_data_dir() -> Path:
    """``<repo>/data/google_token_reminders``."""
    return Path(__file__).resolve().parents[2] / "data" / "google_token_reminders"


def parse_issued_at(raw: str | None) -> datetime | None:
    """Parse env issued-at. Date-only values are midnight HKT. None if empty/garbage."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        if len(text) >= 10 and text[4] == "-" and "T" not in text[:11] and " " not in text[:11]:
            day = date.fromisoformat(text[:10])
            return datetime(day.year, day.month, day.day, tzinfo=FAMILY_TZ)
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=FAMILY_TZ)
    return dt.astimezone(FAMILY_TZ)


def load_issued_at(env: Mapping[str, str] | None = None) -> datetime | None:
    """``GOOGLE_REFRESH_TOKEN_ISSUED_AT`` from env, or None."""
    source = env if env is not None else os.environ
    return parse_issued_at(source.get(ENV_ISSUED_AT))


def load_ttl_days(env: Mapping[str, str] | None = None) -> int:
    """``GOOGLE_TOKEN_TTL_DAYS`` (default 7). Garbage/empty → 7."""
    source = env if env is not None else os.environ
    raw = (source.get(ENV_TTL_DAYS) or "").strip()
    if not raw:
        return DEFAULT_TTL_DAYS
    try:
        days = int(raw)
    except ValueError:
        return DEFAULT_TTL_DAYS
    return days if days > 0 else DEFAULT_TTL_DAYS


def days_until_expiry(
    *,
    issued_at: datetime,
    now: datetime,
    ttl_days: int = DEFAULT_TTL_DAYS,
) -> int:
    """HKT calendar days from today until issued-date + ttl (may be negative)."""
    issued_day = issued_at.astimezone(FAMILY_TZ).date()
    today = now.astimezone(FAMILY_TZ).date()
    expires = issued_day + timedelta(days=ttl_days)
    return (expires - today).days


def format_expiry_reminder(days_left: int) -> str:
    """Slack line for 1/2/3 days left. Never claims a calendar write."""
    unit = "day" if days_left == 1 else "days"
    return f"Google calendar will be expired in {days_left} {unit}. Please refresh"


def run_google_token_reminder(
    *,
    now: datetime | None = None,
    poster: SlackPoster,
    channel_id: str,
    issued_at: datetime | None,
    ttl_days: int = DEFAULT_TTL_DAYS,
    post_store: GoogleTokenReminderStore,
    correlation_id: str | None = None,
) -> GoogleTokenReminderResult:
    """Post a 3/2/1-day expiry ping. Never writes calendar. Never logs tokens."""
    started = time.perf_counter()
    local = _normalize_now(now)
    review_date = local.date()
    corr = correlation_id or str(uuid.uuid4())
    logger.info(
        "google_token_reminder_started",
        component=COMPONENT,
        correlation_id=corr,
        review_date=review_date.isoformat(),
        channel_id=channel_id,
        ttl_days=ttl_days,
        has_issued_at=issued_at is not None,
    )

    if issued_at is None:
        return _skip(
            started=started,
            review_date=review_date,
            channel_id=channel_id,
            corr=corr,
            reason="missing_issued_at",
            days_left=None,
        )

    days_left = days_until_expiry(issued_at=issued_at, now=local, ttl_days=ttl_days)
    if days_left not in REMIND_DAYS:
        reason = "expired_or_today" if days_left <= 0 else "too_early"
        return _skip(
            started=started,
            review_date=review_date,
            channel_id=channel_id,
            corr=corr,
            reason=reason,
            days_left=days_left,
        )

    if post_store.has_posted(review_date):
        return _skip(
            started=started,
            review_date=review_date,
            channel_id=channel_id,
            corr=corr,
            reason="already_posted",
            days_left=days_left,
        )

    post_text = format_expiry_reminder(days_left)
    try:
        poster.post(channel_id=channel_id, text=post_text)
    except Exception as exc:  # noqa: BLE001 — do not mark posted
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.error(
            "google_token_reminder_failed",
            component=COMPONENT,
            correlation_id=corr,
            outcome="failure",
            review_date=review_date.isoformat(),
            channel_id=channel_id,
            days_left=days_left,
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return GoogleTokenReminderResult(
            outcome=GoogleTokenReminderOutcome.FAILED,
            review_date=review_date,
            channel_id=channel_id,
            days_left=days_left,
            post_text=post_text,
            error_type=type(exc).__name__,
            error_message=str(exc),
            duration_ms=duration_ms,
        )

    try:
        post_store.mark_posted(
            review_date,
            posted_at=local,
            days_left=days_left,
            channel_id=channel_id,
        )
    except OSError as exc:
        logger.error(
            "google_token_reminder_store_save_failed",
            component=COMPONENT,
            correlation_id=corr,
            outcome="failure",
            reminder_date=review_date.isoformat(),
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "google_token_reminder_posted",
        component=COMPONENT,
        correlation_id=corr,
        outcome="success",
        review_date=review_date.isoformat(),
        channel_id=channel_id,
        days_left=days_left,
        duration_ms=duration_ms,
    )
    return GoogleTokenReminderResult(
        outcome=GoogleTokenReminderOutcome.POSTED,
        review_date=review_date,
        channel_id=channel_id,
        days_left=days_left,
        post_text=post_text,
        duration_ms=duration_ms,
    )


def maintain_google_token_reminder_storage(
    store: GoogleTokenReminderStore,
    *,
    now: datetime | None = None,
    retention: timedelta = POSTED_RETENTION,
) -> int:
    """Delete class-C reminder markers older than retention (default 30 days)."""
    local = _normalize_now(now)
    cutoff = local.date() - retention
    deleted = 0
    for reminder_date in list(store.list_dates()):
        if reminder_date < cutoff:
            try:
                store.delete(reminder_date)
                deleted += 1
            except OSError:
                continue
    return deleted


def load_google_token_reminder_config(
    env: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """Return ``(bot_token, channel_id)``. Never includes secret values in errors."""
    source = env if env is not None else os.environ
    missing: list[str] = []
    bot = (source.get("SLACK_BOT_TOKEN") or "").strip()
    channel = (source.get("SLACK_FAMILY_PLANS_CHANNEL_ID") or "").strip()
    if not bot:
        missing.append("SLACK_BOT_TOKEN")
    if not channel:
        missing.append("SLACK_FAMILY_PLANS_CHANNEL_ID")
    if missing:
        raise GoogleTokenReminderConfigError(
            "Missing required Google token reminder configuration: " + ", ".join(missing)
        )
    return bot, channel


def main() -> None:
    """CLI entry: load ``.env`` if present, post 3/2/1-day ping if needed."""
    from dotenv import load_dotenv

    from cec_vivisystem.logging import setup_logging

    load_dotenv()
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    setup_logging(level=log_level)

    try:
        bot_token, channel_id = load_google_token_reminder_config()
    except GoogleTokenReminderConfigError as exc:
        logger.error(
            "google_token_reminder_failed",
            component=COMPONENT,
            outcome="failure",
            error_type="GoogleTokenReminderConfigError",
            error_message=str(exc),
        )
        raise SystemExit(1) from exc

    store = JsonDirGoogleTokenReminderStore(default_data_dir())
    maintain_google_token_reminder_storage(store)
    result = run_google_token_reminder(
        poster=SlackWebPoster(bot_token),
        channel_id=channel_id,
        issued_at=load_issued_at(),
        ttl_days=load_ttl_days(),
        post_store=store,
    )
    if result.outcome == GoogleTokenReminderOutcome.FAILED:
        raise SystemExit(1)


def _skip(
    *,
    started: float,
    review_date: date,
    channel_id: str,
    corr: str,
    reason: str,
    days_left: int | None,
) -> GoogleTokenReminderResult:
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "google_token_reminder_skipped",
        component=COMPONENT,
        correlation_id=corr,
        outcome="skipped",
        review_date=review_date.isoformat(),
        reason=reason,
        days_left=days_left,
        duration_ms=duration_ms,
    )
    return GoogleTokenReminderResult(
        outcome=GoogleTokenReminderOutcome.SKIPPED,
        review_date=review_date,
        channel_id=channel_id,
        days_left=days_left,
        skip_reason=reason,
        duration_ms=duration_ms,
    )


def _normalize_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(tz=FAMILY_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=FAMILY_TZ)
    return now.astimezone(FAMILY_TZ)
