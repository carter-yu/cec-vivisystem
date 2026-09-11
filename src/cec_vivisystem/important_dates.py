"""Important dates (Phase 15) — yearly / one-off family markers.

Not Calendar Writer. Not LifeNotes. JSON store (ADR 0004). Slack add is
immediate (no confirmation). 10:00 HKT review posts once per occurrence
when the date falls in the next 7 local days.

CLI: ``uv run python -c "from cec_vivisystem.important_dates import main; main()"``

Retention: rows under gitignored ``data/important_dates/`` (class **F**);
occurrence post markers under ``data/important_dates_posts/`` (class **C**,
30d purge on CLI start).
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
from cec_vivisystem.models import (
    ImportantDate,
    ImportantDateKind,
    ImportantDatesReviewOutcome,
    ImportantDatesReviewResult,
    ImportantDateWriteOutcome,
    ImportantDateWriteResult,
    ParseResult,
)
from cec_vivisystem.morning_recap import SlackPoster, SlackWebPoster

logger = get_logger(__name__)

COMPONENT = "important_dates"
FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
POSTED_RETENTION = timedelta(days=30)
REVIEW_DAYS = 7
EMPTY_LIST = "未記低重要日子。"
ADD_ACK_YEARLY = "已記低重要日子：{month}月{day}日 {title}（每年）。"
ADD_ACK_ONE_OFF = "已記低重要日子：{year}年{month}月{day}日 {title}。"
ADD_ACK_EXISTS_YEARLY = "已經記低：{month}月{day}日 {title}（每年）。"
ADD_ACK_EXISTS_ONE_OFF = "已經記低：{year}年{month}月{day}日 {title}。"
LIST_HEADER = "重要日子："
REVIEW_HEADER = "未來七日重要日子："
READ_ONLY_DISCLAIMER = "No calendar change was made."
NOT_STORED = "Important date understood, but it was not stored."
NOT_LISTED = "Important dates store is not configured."


class ImportantDatesError(Exception):
    """Controlled important-dates failure (config or store)."""


class ImportantDatesConfigError(ImportantDatesError):
    """Missing or invalid config (no secret values in message)."""


class ImportantDatesStore(Protocol):
    """Persistence for important date rows (class F)."""

    def save(self, item: ImportantDate) -> ImportantDate: ...

    def get(self, date_id: str) -> ImportantDate | None: ...

    def list_all(self) -> list[ImportantDate]: ...


class ImportantDatesPostStore(Protocol):
    """Idempotency markers: one 10:00 post per occurrence (class C)."""

    def has_posted(self, date_id: str, occurrence: date) -> bool: ...

    def mark_posted(
        self,
        date_id: str,
        occurrence: date,
        *,
        posted_at: datetime,
        channel_id: str | None = None,
    ) -> None: ...

    def delete(self, date_id: str, occurrence: date) -> None: ...

    def list_keys(self) -> list[tuple[str, date]]: ...


class InMemoryImportantDatesStore:
    """Test/default store — no disk."""

    def __init__(self) -> None:
        self._items: dict[str, ImportantDate] = {}

    def save(self, item: ImportantDate) -> ImportantDate:
        self._items[item.date_id] = item
        return item

    def get(self, date_id: str) -> ImportantDate | None:
        return self._items.get(date_id)

    def list_all(self) -> list[ImportantDate]:
        return list(self._items.values())


class InMemoryImportantDatesPostStore:
    """Test/default occurrence markers — no disk."""

    def __init__(self) -> None:
        self._posted: dict[tuple[str, date], dict[str, object]] = {}

    def has_posted(self, date_id: str, occurrence: date) -> bool:
        return (date_id, occurrence) in self._posted

    def mark_posted(
        self,
        date_id: str,
        occurrence: date,
        *,
        posted_at: datetime,
        channel_id: str | None = None,
    ) -> None:
        self._posted[(date_id, occurrence)] = {
            "posted_at": posted_at,
            "channel_id": channel_id,
        }

    def delete(self, date_id: str, occurrence: date) -> None:
        self._posted.pop((date_id, occurrence), None)

    def list_keys(self) -> list[tuple[str, date]]:
        return list(self._posted.keys())


class JsonDirImportantDatesStore:
    """One JSON file per date under gitignored ``data/important_dates/``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, date_id: str) -> Path:
        safe = date_id.replace("/", "_")
        return self.root / f"{safe}.json"

    def save(self, item: ImportantDate) -> ImportantDate:
        path = self._path(item.date_id)
        try:
            path.write_text(
                json.dumps(_date_to_dict(item), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error(
                "important_date_store_save_failed",
                component=COMPONENT,
                outcome="failure",
                date_id=item.date_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise
        return item

    def get(self, date_id: str) -> ImportantDate | None:
        path = self._path(date_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return _date_from_dict(data)
        except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            logger.error(
                "important_date_store_load_failed",
                component=COMPONENT,
                outcome="failure",
                date_id=date_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return None

    def list_all(self) -> list[ImportantDate]:
        items: list[ImportantDate] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                items.append(_date_from_dict(data))
            except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
                logger.error(
                    "important_date_store_load_failed",
                    component=COMPONENT,
                    outcome="failure",
                    path=str(path),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
        return items


class JsonDirImportantDatesPostStore:
    """One JSON file per posted occurrence under ``data/important_dates_posts/``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, date_id: str, occurrence: date) -> Path:
        safe = date_id.replace("/", "_")
        return self.root / f"{safe}_{occurrence.isoformat()}.json"

    def has_posted(self, date_id: str, occurrence: date) -> bool:
        return self._path(date_id, occurrence).is_file()

    def mark_posted(
        self,
        date_id: str,
        occurrence: date,
        *,
        posted_at: datetime,
        channel_id: str | None = None,
    ) -> None:
        payload = {
            "date_id": date_id,
            "occurrence": occurrence.isoformat(),
            "posted_at": posted_at.isoformat(),
            "channel_id": channel_id,
        }
        path = self._path(date_id, occurrence)
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error(
                "important_dates_post_store_save_failed",
                component=COMPONENT,
                outcome="failure",
                date_id=date_id,
                occurrence=occurrence.isoformat(),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise

    def delete(self, date_id: str, occurrence: date) -> None:
        path = self._path(date_id, occurrence)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.error(
                "important_dates_post_store_delete_failed",
                component=COMPONENT,
                outcome="failure",
                date_id=date_id,
                occurrence=occurrence.isoformat(),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise

    def list_keys(self) -> list[tuple[str, date]]:
        found: list[tuple[str, date]] = []
        for path in self.root.glob("*.json"):
            stem = path.stem
            if "_" not in stem:
                continue
            date_id, _, occ = stem.rpartition("_")
            try:
                found.append((date_id, date.fromisoformat(occ)))
            except ValueError:
                continue
        return found


def default_data_dir() -> Path:
    """``<repo>/data/important_dates``."""
    return Path(__file__).resolve().parents[2] / "data" / "important_dates"


def default_post_data_dir() -> Path:
    """``<repo>/data/important_dates_posts``."""
    return Path(__file__).resolve().parents[2] / "data" / "important_dates_posts"


def create_important_date(
    parse_result: ParseResult,
    *,
    store: ImportantDatesStore,
    now: datetime | None = None,
    correlation_id: str | None = None,
) -> ImportantDateWriteResult:
    """Persist an add-important-date parse. Duplicate month/day/year/title is a no-op."""
    started = time.perf_counter()
    local = _normalize_now(now)
    kind = (
        ImportantDateKind.ONE_OFF
        if parse_result.notes == "one_off"
        else ImportantDateKind.YEARLY
    )
    if parse_result.start is None:
        raise ImportantDatesError("important date parse is missing start")
    start_local = parse_result.start.astimezone(FAMILY_TZ)
    title = (parse_result.title or "").strip() or "(untitled)"
    year = start_local.year if kind == ImportantDateKind.ONE_OFF else None
    existing = _find_duplicate(
        store,
        month=start_local.month,
        day=start_local.day,
        year=year,
        title=title,
    )
    if existing is not None:
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "important_date_already_exists",
            component=COMPONENT,
            outcome="skipped",
            date_id=existing.date_id,
            duration_ms=duration_ms,
            correlation_id=correlation_id,
        )
        return ImportantDateWriteResult(
            outcome=ImportantDateWriteOutcome.ALREADY_EXISTS,
            date=existing,
        )

    item = ImportantDate(
        date_id=str(uuid.uuid4()),
        title=title,
        month=start_local.month,
        day=start_local.day,
        kind=kind,
        created_at=local,
        year=year,
        participants=list(parse_result.participants),
        raw_text=parse_result.raw_text,
        correlation_id=correlation_id,
    )
    store.save(item)
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "important_date_written",
        component=COMPONENT,
        outcome="success",
        date_id=item.date_id,
        kind=item.kind.value,
        month=item.month,
        day=item.day,
        duration_ms=duration_ms,
        correlation_id=correlation_id,
    )
    return ImportantDateWriteResult(
        outcome=ImportantDateWriteOutcome.CREATED,
        date=item,
    )


def format_important_dates_list(items: list[ImportantDate]) -> str:
    """Human Slack/CLI catalog. Never claims a calendar write."""
    if not items:
        return EMPTY_LIST
    lines = [LIST_HEADER]
    for item in _sorted_dates(items):
        lines.append("• " + _format_date_line(item))
    return "\n".join(lines)


def format_add_ack(result: ImportantDateWriteResult) -> str:
    """Ack after add. Never claims a calendar write."""
    item = result.date
    if result.outcome == ImportantDateWriteOutcome.ALREADY_EXISTS:
        if item.kind == ImportantDateKind.ONE_OFF and item.year is not None:
            return ADD_ACK_EXISTS_ONE_OFF.format(
                year=item.year, month=item.month, day=item.day, title=item.title
            )
        return ADD_ACK_EXISTS_YEARLY.format(
            month=item.month, day=item.day, title=item.title
        )
    if item.kind == ImportantDateKind.ONE_OFF and item.year is not None:
        return ADD_ACK_ONE_OFF.format(
            year=item.year, month=item.month, day=item.day, title=item.title
        )
    return ADD_ACK_YEARLY.format(month=item.month, day=item.day, title=item.title)


def maintain_important_dates_post_storage(
    store: ImportantDatesPostStore,
    *,
    now: datetime | None = None,
    retention: timedelta = POSTED_RETENTION,
) -> int:
    """Delete occurrence markers older than ``retention`` (class C)."""
    local = _normalize_now(now)
    cutoff = local.date() - retention
    deleted = 0
    for date_id, occurrence in list(store.list_keys()):
        if occurrence < cutoff:
            try:
                store.delete(date_id, occurrence)
                deleted += 1
            except OSError:
                continue
    return deleted


def run_important_dates_review(
    *,
    now: datetime | None = None,
    poster: SlackPoster,
    channel_id: str,
    dates_store: ImportantDatesStore,
    post_store: ImportantDatesPostStore,
    correlation_id: str | None = None,
) -> ImportantDatesReviewResult:
    """Post upcoming important dates in the next 7 HKT days. Never writes calendar."""
    started = time.perf_counter()
    local = _normalize_now(now)
    review_date = local.date()
    corr = correlation_id or str(uuid.uuid4())
    window_end = review_date + timedelta(days=REVIEW_DAYS)
    logger.info(
        "important_dates_review_started",
        component=COMPONENT,
        correlation_id=corr,
        review_date=review_date.isoformat(),
        channel_id=channel_id,
        window_end=window_end.isoformat(),
    )

    hits: list[tuple[ImportantDate, date]] = []
    for item in dates_store.list_all():
        occurrence = next_occurrence(item, today=review_date)
        if occurrence is None:
            continue
        if not (review_date <= occurrence < window_end):
            continue
        if post_store.has_posted(item.date_id, occurrence):
            continue
        hits.append((item, occurrence))

    if not hits:
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "important_dates_review_skipped",
            component=COMPONENT,
            correlation_id=corr,
            outcome="skipped",
            review_date=review_date.isoformat(),
            reason="no_new_hits",
            duration_ms=duration_ms,
        )
        return ImportantDatesReviewResult(
            outcome=ImportantDatesReviewOutcome.SKIPPED,
            review_date=review_date,
            channel_id=channel_id,
            duration_ms=duration_ms,
        )

    hits.sort(key=lambda pair: (pair[1], pair[0].title.casefold()))
    post_text = _format_review_post(hits)
    try:
        poster.post(channel_id=channel_id, text=post_text)
    except Exception as exc:  # noqa: BLE001 — do not mark posted
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.error(
            "important_dates_review_failed",
            component=COMPONENT,
            correlation_id=corr,
            outcome="failure",
            review_date=review_date.isoformat(),
            channel_id=channel_id,
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return ImportantDatesReviewResult(
            outcome=ImportantDatesReviewOutcome.FAILED,
            review_date=review_date,
            channel_id=channel_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
            duration_ms=duration_ms,
        )

    for item, occurrence in hits:
        try:
            post_store.mark_posted(
                item.date_id,
                occurrence,
                posted_at=local,
                channel_id=channel_id,
            )
        except OSError as exc:
            logger.error(
                "important_dates_post_store_save_failed",
                component=COMPONENT,
                correlation_id=corr,
                outcome="failure",
                date_id=item.date_id,
                occurrence=occurrence.isoformat(),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "important_dates_review_posted",
        component=COMPONENT,
        correlation_id=corr,
        outcome="success",
        review_date=review_date.isoformat(),
        channel_id=channel_id,
        hit_count=len(hits),
        duration_ms=duration_ms,
    )
    return ImportantDatesReviewResult(
        outcome=ImportantDatesReviewOutcome.POSTED,
        review_date=review_date,
        channel_id=channel_id,
        hit_count=len(hits),
        post_text=post_text,
        duration_ms=duration_ms,
    )


def next_occurrence(item: ImportantDate, *, today: date) -> date | None:
    """Next calendar date for ``item`` on or after ``today``, or None if past one-off."""
    if item.kind == ImportantDateKind.ONE_OFF:
        if item.year is None:
            return None
        try:
            occ = date(item.year, item.month, item.day)
        except ValueError:
            return None
        return occ if occ >= today else None

    year = today.year
    for _ in range(8):
        try:
            occ = date(year, item.month, item.day)
        except ValueError:
            year += 1
            continue
        if occ >= today:
            return occ
        year += 1
    return None


def load_important_dates_config(
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
        raise ImportantDatesConfigError(
            "Missing required important dates configuration: " + ", ".join(missing)
        )
    return bot, channel


def main() -> None:
    """CLI entry: load ``.env`` if present, review next 7 days, post if needed."""
    from dotenv import load_dotenv

    from cec_vivisystem.logging import setup_logging

    load_dotenv()
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    setup_logging(level=log_level)

    try:
        bot_token, channel_id = load_important_dates_config()
    except ImportantDatesConfigError as exc:
        logger.error(
            "important_dates_review_failed",
            component=COMPONENT,
            outcome="failure",
            error_type="ImportantDatesConfigError",
            error_message=str(exc),
        )
        raise SystemExit(1) from exc

    dates_store = JsonDirImportantDatesStore(default_data_dir())
    post_store = JsonDirImportantDatesPostStore(default_post_data_dir())
    maintain_important_dates_post_storage(post_store)
    result = run_important_dates_review(
        poster=SlackWebPoster(bot_token),
        channel_id=channel_id,
        dates_store=dates_store,
        post_store=post_store,
    )
    if result.outcome == ImportantDatesReviewOutcome.FAILED:
        raise SystemExit(1)


def _find_duplicate(
    store: ImportantDatesStore,
    *,
    month: int,
    day: int,
    year: int | None,
    title: str,
) -> ImportantDate | None:
    needle = title.casefold()
    for item in store.list_all():
        if (
            item.month == month
            and item.day == day
            and item.year == year
            and item.title.casefold() == needle
        ):
            return item
    return None


def _sorted_dates(items: list[ImportantDate]) -> list[ImportantDate]:
    return sorted(
        items,
        key=lambda item: (item.month, item.day, item.year or 0, item.title.casefold()),
    )


def _format_date_line(item: ImportantDate) -> str:
    if item.kind == ImportantDateKind.ONE_OFF and item.year is not None:
        return f"{item.year}年{item.month}月{item.day}日 {item.title}"
    return f"{item.month}月{item.day}日 {item.title}（每年）"


def _format_review_post(hits: list[tuple[ImportantDate, date]]) -> str:
    lines = [REVIEW_HEADER]
    for item, _occurrence in hits:
        lines.append("• " + _format_date_line(item))
    lines.append(READ_ONLY_DISCLAIMER)
    return "\n".join(lines)


def _date_to_dict(item: ImportantDate) -> dict[str, object]:
    return {
        "date_id": item.date_id,
        "title": item.title,
        "month": item.month,
        "day": item.day,
        "kind": item.kind.value,
        "created_at": item.created_at.isoformat(),
        "year": item.year,
        "participants": list(item.participants),
        "raw_text": item.raw_text,
        "correlation_id": item.correlation_id,
    }


def _date_from_dict(data: Mapping[str, object]) -> ImportantDate:
    created_raw = str(data["created_at"])
    created = datetime.fromisoformat(created_raw)
    if created.tzinfo is None:
        created = created.replace(tzinfo=FAMILY_TZ)
    year_raw = data.get("year")
    year = int(year_raw) if year_raw is not None else None
    participants_raw = data.get("participants") or []
    if not isinstance(participants_raw, list):
        participants_raw = []
    return ImportantDate(
        date_id=str(data["date_id"]),
        title=str(data["title"]),
        month=int(str(data["month"])),
        day=int(str(data["day"])),
        kind=ImportantDateKind(str(data["kind"])),
        created_at=created,
        year=year,
        participants=[str(p) for p in participants_raw],
        raw_text=str(data.get("raw_text") or ""),
        correlation_id=(
            str(data["correlation_id"]) if data.get("correlation_id") else None
        ),
    )


def _normalize_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(tz=FAMILY_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=FAMILY_TZ)
    return now.astimezone(FAMILY_TZ)


