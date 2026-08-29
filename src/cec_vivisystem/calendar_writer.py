"""Calendar Writer (Phase 6) — create-only, accepted confirmations.

The only component allowed to write to Google Calendar. Creates one event
from an accepted confirmation that has a confirmation_id.

Default pytest injects ``FakeCalendarClient``: no network, no tokens, no LLM.
Live Google I/O is constructed from env only (Socket Mode / manual smoke).

Retention: class **B** audit of every attempt + result (90 days). Google
Calendar remains class **G** source of truth — no full local event mirror.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from cec_vivisystem.logging import get_logger
from cec_vivisystem.models import (
    CalendarAuditRecord,
    CalendarEventCreated,
    CalendarEventDraft,
    CalendarWriteOutcome,
    CalendarWriteResult,
    Confirmation,
    ConfirmationStatus,
)

logger = get_logger(__name__)

COMPONENT = "calendar_writer"
FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
TIME_ZONE_NAME = "Asia/Hong_Kong"
OP_CREATE = "create"
DEFAULT_DURATION = timedelta(hours=1)
FALLBACK_TITLE = "Family event"
AUDIT_RETENTION = timedelta(days=90)
AUDIT_SOFT_CAP_BYTES = 50 * 1024 * 1024
GOOGLE_SCOPES = ("https://www.googleapis.com/auth/calendar.events",)
LIVE_HTTP_TIMEOUT_S = 30


class CalendarWriterError(Exception):
    """Controlled calendar-writer failure (config or live mapping)."""


class CalendarWriterConfigError(CalendarWriterError):
    """Missing or invalid Google configuration (no secret values in message)."""


class CalendarClient(Protocol):
    """Injectable calendar backend. Tests use ``FakeCalendarClient``."""

    def create_event(self, draft: CalendarEventDraft) -> CalendarEventCreated: ...


class CalendarAuditStore(Protocol):
    """Persistence for class B write-audit rows."""

    def save(self, record: CalendarAuditRecord) -> None: ...

    def list_all(self) -> list[CalendarAuditRecord]: ...

    def delete(self, audit_id: str) -> None: ...


class FakeCalendarClient:
    """Test double — records drafts; never talks to Google."""

    def __init__(
        self,
        *,
        fail_with: BaseException | None = None,
        event_id: str = "evt_fake_1",
    ) -> None:
        self.calls: list[CalendarEventDraft] = []
        self.fail_with = fail_with
        self.event_id = event_id

    def create_event(self, draft: CalendarEventDraft) -> CalendarEventCreated:
        self.calls.append(draft)
        if self.fail_with is not None:
            raise self.fail_with
        return CalendarEventCreated(event_id=self.event_id, calendar_id=draft.calendar_id)


class InMemoryCalendarAuditStore:
    """Test/default audit store — no disk."""

    def __init__(self) -> None:
        self._items: dict[str, CalendarAuditRecord] = {}

    def save(self, record: CalendarAuditRecord) -> None:
        self._items[record.audit_id] = record

    def list_all(self) -> list[CalendarAuditRecord]:
        return list(self._items.values())

    def delete(self, audit_id: str) -> None:
        self._items.pop(audit_id, None)


class JsonDirCalendarAuditStore:
    """One JSON file per audit row under gitignored ``data/calendar_audit/``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, audit_id: str) -> Path:
        safe = audit_id.replace("/", "_")
        return self.root / f"{safe}.json"

    def save(self, record: CalendarAuditRecord) -> None:
        path = self._path(record.audit_id)
        try:
            path.write_text(
                json.dumps(_audit_to_dict(record), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error(
                "calendar_audit_save_failed",
                component=COMPONENT,
                outcome="failure",
                audit_id=record.audit_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise

    def list_all(self) -> list[CalendarAuditRecord]:
        items: list[CalendarAuditRecord] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                items.append(_audit_from_dict(data))
            except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
                logger.error(
                    "calendar_audit_load_failed",
                    component=COMPONENT,
                    outcome="failure",
                    path=str(path),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
        return items

    def delete(self, audit_id: str) -> None:
        path = self._path(audit_id)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.error(
                "calendar_audit_delete_failed",
                component=COMPONENT,
                outcome="failure",
                audit_id=audit_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise

    def enforce_soft_cap(self, max_bytes: int = AUDIT_SOFT_CAP_BYTES) -> int:
        """Delete oldest files first if the directory exceeds ``max_bytes``."""
        files = [p for p in self.root.glob("*.json") if p.is_file()]
        total = sum(p.stat().st_size for p in files)
        if total <= max_bytes:
            return 0
        deleted = 0
        for path in sorted(files, key=lambda p: p.stat().st_mtime):
            if total <= max_bytes:
                break
            size = path.stat().st_size
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            total -= size
            deleted += 1
        return deleted


@dataclass(frozen=True, slots=True)
class GoogleCalendarConfig:
    """Runtime Google Calendar settings loaded from the environment."""

    client_id: str
    client_secret: str
    refresh_token: str
    calendar_id: str


class GoogleCalendarClient:
    """Live Google Calendar API client. Not constructed by default pytest."""

    def __init__(self, config: GoogleCalendarConfig) -> None:
        self._config = config
        self._service = None

    def create_event(self, draft: CalendarEventDraft) -> CalendarEventCreated:
        service = self._get_service()
        body = _draft_to_google_event(draft)
        created = (
            service.events()
            .insert(calendarId=draft.calendar_id, body=body)
            .execute()
        )
        event_id = created.get("id") if isinstance(created, dict) else None
        if not event_id:
            raise CalendarWriterError("Google Calendar insert returned no event id")
        html_link = created.get("htmlLink") if isinstance(created, dict) else None
        return CalendarEventCreated(
            event_id=str(event_id),
            calendar_id=draft.calendar_id,
            html_link=str(html_link) if html_link else None,
        )

    def _get_service(self):
        if self._service is not None:
            return self._service
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise CalendarWriterConfigError(
                "Google Calendar libraries are not installed"
            ) from exc

        creds = Credentials(
            token=None,
            refresh_token=self._config.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self._config.client_id,
            client_secret=self._config.client_secret,
            scopes=list(GOOGLE_SCOPES),
        )
        if not creds.valid:
            creds.refresh(Request())

        http = None
        try:
            import httplib2
            from google_auth_httplib2 import AuthorizedHttp

            http = AuthorizedHttp(creds, http=httplib2.Http(timeout=LIVE_HTTP_TIMEOUT_S))
        except ImportError:
            http = None

        if http is not None:
            self._service = build("calendar", "v3", http=http, cache_discovery=False)
        else:
            self._service = build(
                "calendar", "v3", credentials=creds, cache_discovery=False
            )
        return self._service


def default_audit_dir() -> Path:
    """``<repo>/data/calendar_audit``."""
    return Path(__file__).resolve().parents[2] / "data" / "calendar_audit"


def load_google_calendar_config(
    env: Mapping[str, str] | None = None,
) -> GoogleCalendarConfig:
    """Load Google Calendar config from ``env`` (default: ``os.environ``).

    Raises:
        CalendarWriterConfigError: if required variables are missing or empty.
        Never includes secret values in the error message.
    """
    source = env if env is not None else os.environ
    missing: list[str] = []
    client_id = (source.get("GOOGLE_CLIENT_ID") or "").strip()
    client_secret = (source.get("GOOGLE_CLIENT_SECRET") or "").strip()
    refresh_token = (source.get("GOOGLE_REFRESH_TOKEN") or "").strip()
    calendar_id = (source.get("GOOGLE_CALENDAR_ID") or "").strip() or "primary"

    if not client_id:
        missing.append("GOOGLE_CLIENT_ID")
    if not client_secret:
        missing.append("GOOGLE_CLIENT_SECRET")
    if not refresh_token:
        missing.append("GOOGLE_REFRESH_TOKEN")
    if missing:
        raise CalendarWriterConfigError(
            "Missing required Google Calendar configuration: " + ", ".join(missing)
        )
    return GoogleCalendarConfig(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
        calendar_id=calendar_id,
    )


def write_calendar_create(
    confirmation: Confirmation,
    *,
    client: CalendarClient,
    calendar_id: str | None = None,
    audit_store: CalendarAuditStore | None = None,
    now: datetime | None = None,
) -> CalendarWriteResult:
    """Create one calendar event from an accepted confirmation.

    Refuses (no Google call) unless status is accepted and confirmation_id
    is a non-empty string. Google API errors become ``outcome=failed``;
    this function does not raise them.
    """
    started = time.perf_counter()
    moment = _normalize_now(now)
    cal_id = _resolve_calendar_id(calendar_id)
    conf_id_raw = confirmation.confirmation_id if confirmation else ""
    conf_id = (conf_id_raw or "").strip()
    corr = confirmation.correlation_id if confirmation else None
    parse_result = confirmation.parse_result
    title = (parse_result.title or "").strip() or FALLBACK_TITLE
    start = parse_result.start

    if not conf_id:
        return _finish(
            started=started,
            moment=moment,
            outcome=CalendarWriteOutcome.REFUSED,
            confirmation_id=conf_id_raw or None,
            calendar_id=cal_id,
            calendar_event_id=None,
            title=title,
            start=start,
            correlation_id=corr,
            error_type="missing_confirmation_id",
            error_message="write refused: confirmation_id is required",
            audit_store=audit_store,
            log_event="write_without_confirmation_id",
            log_level="critical",
        )

    if confirmation.status != ConfirmationStatus.ACCEPTED:
        return _finish(
            started=started,
            moment=moment,
            outcome=CalendarWriteOutcome.REFUSED,
            confirmation_id=conf_id,
            calendar_id=cal_id,
            calendar_event_id=None,
            title=title,
            start=start,
            correlation_id=corr,
            error_type="not_accepted",
            error_message=(
                f"write refused: status is {confirmation.status.value}, not accepted"
            ),
            audit_store=audit_store,
            log_event="write_refused",
            log_level="warning",
        )

    if start is None:
        return _finish(
            started=started,
            moment=moment,
            outcome=CalendarWriteOutcome.REFUSED,
            confirmation_id=conf_id,
            calendar_id=cal_id,
            calendar_event_id=None,
            title=title,
            start=None,
            correlation_id=corr,
            error_type="missing_start",
            error_message="write refused: parse_result.start is required",
            audit_store=audit_store,
            log_event="write_refused",
            log_level="warning",
        )

    draft = _build_draft(
        confirmation,
        calendar_id=cal_id,
        confirmation_id=conf_id,
        title=title,
        start=start,
    )
    start_iso = draft.start.isoformat()
    logger.info(
        "write_attempt",
        component=COMPONENT,
        op=OP_CREATE,
        confirmation_id=conf_id,
        correlation_id=corr,
        title=title,
        start=start_iso,
        calendar_id=cal_id,
    )
    try:
        created = client.create_event(draft)
    except Exception as exc:  # noqa: BLE001 — boundary: never crash the writer
        return _finish(
            started=started,
            moment=moment,
            outcome=CalendarWriteOutcome.FAILED,
            confirmation_id=conf_id,
            calendar_id=cal_id,
            calendar_event_id=None,
            title=title,
            start=draft.start,
            correlation_id=corr,
            error_type=type(exc).__name__,
            error_message=str(exc),
            audit_store=audit_store,
            log_event="write_failed",
            log_level="error",
        )

    return _finish(
        started=started,
        moment=moment,
        outcome=CalendarWriteOutcome.SUCCESS,
        confirmation_id=conf_id,
        calendar_id=created.calendar_id or cal_id,
        calendar_event_id=created.event_id,
        title=title,
        start=draft.start,
        correlation_id=corr,
        error_type=None,
        error_message=None,
        audit_store=audit_store,
        log_event="write_succeeded",
        log_level="info",
    )


def purge_calendar_audit(
    *,
    store: CalendarAuditStore,
    now: datetime | None = None,
    retention: timedelta = AUDIT_RETENTION,
) -> int:
    """Delete class-B audit rows older than retention (default 90 days)."""
    moment = _normalize_now(now)
    deleted = 0
    cutoff = moment - retention
    for item in list(store.list_all()):
        if item.attempted_at <= cutoff:
            store.delete(item.audit_id)
            deleted += 1
    logger.info(
        "calendar_audit_purge_completed",
        component=COMPONENT,
        outcome="success",
        purged=deleted,
        retention_days=retention.days,
    )
    return deleted


def maintain_calendar_audit_storage(
    *,
    store: CalendarAuditStore,
    now: datetime | None = None,
) -> dict[str, int]:
    """Purge stale audit rows; enforce soft cap when the store supports it."""
    purged = purge_calendar_audit(store=store, now=now)
    cap_deleted = 0
    enforce = getattr(store, "enforce_soft_cap", None)
    if callable(enforce):
        try:
            cap_deleted = int(enforce(AUDIT_SOFT_CAP_BYTES))
        except Exception as exc:  # noqa: BLE001 — purge helper must not crash start
            logger.error(
                "calendar_audit_soft_cap_failed",
                component=COMPONENT,
                outcome="failure",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
    return {"purged": purged, "soft_cap_deleted": cap_deleted}


def _build_draft(
    confirmation: Confirmation,
    *,
    calendar_id: str,
    confirmation_id: str,
    title: str,
    start: datetime,
) -> CalendarEventDraft:
    parse_result = confirmation.parse_result
    start_local = start.astimezone(FAMILY_TZ)
    all_day = bool(parse_result.all_day)
    if all_day:
        start_day = start_local.date()
        if parse_result.end is not None:
            end_day = parse_result.end.astimezone(FAMILY_TZ).date()
            if end_day <= start_day:
                end_day = start_day + timedelta(days=1)
        else:
            end_day = start_day + timedelta(days=1)
        start_dt = datetime.combine(start_day, datetime.min.time(), tzinfo=FAMILY_TZ)
        end_dt = datetime.combine(end_day, datetime.min.time(), tzinfo=FAMILY_TZ)
    else:
        start_dt = start_local
        if parse_result.end is not None:
            end_dt = parse_result.end.astimezone(FAMILY_TZ)
        else:
            end_dt = start_dt + DEFAULT_DURATION

    attendees = [p for p in parse_result.participants if _looks_like_email(p)]
    description_lines: list[str] = []
    named = [p for p in parse_result.participants if p]
    if named:
        description_lines.append("Participants: " + ", ".join(named))
    description_lines.append(f"confirmation_id: {confirmation_id}")
    description = "\n".join(description_lines)

    return CalendarEventDraft(
        calendar_id=calendar_id,
        summary=title,
        start=start_dt,
        end=end_dt,
        all_day=all_day,
        time_zone=TIME_ZONE_NAME,
        confirmation_id=confirmation_id,
        location=parse_result.location,
        description=description,
        attendees=attendees,
        correlation_id=confirmation.correlation_id,
    )


def _finish(
    *,
    started: float,
    moment: datetime,
    outcome: CalendarWriteOutcome,
    confirmation_id: str | None,
    calendar_id: str | None,
    calendar_event_id: str | None,
    title: str | None,
    start: datetime | None,
    correlation_id: str | None,
    error_type: str | None,
    error_message: str | None,
    audit_store: CalendarAuditStore | None,
    log_event: str,
    log_level: str,
) -> CalendarWriteResult:
    duration_ms = int((time.perf_counter() - started) * 1000)
    result = CalendarWriteResult(
        outcome=outcome,
        op=OP_CREATE,
        confirmation_id=confirmation_id,
        calendar_id=calendar_id,
        calendar_event_id=calendar_event_id,
        error_type=error_type,
        error_message=error_message,
        duration_ms=duration_ms,
    )
    if audit_store is not None:
        record = CalendarAuditRecord(
            audit_id=str(uuid.uuid4()),
            attempted_at=moment,
            op=OP_CREATE,
            confirmation_id=confirmation_id,
            correlation_id=correlation_id,
            outcome=outcome,
            calendar_id=calendar_id,
            calendar_event_id=calendar_event_id,
            title=title,
            start=start,
            error_type=error_type,
            error_message=error_message,
        )
        try:
            audit_store.save(record)
        except Exception as exc:  # noqa: BLE001 — audit must not hide write outcome
            logger.error(
                "calendar_audit_save_failed",
                component=COMPONENT,
                outcome="failure",
                confirmation_id=confirmation_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    log_outcome = (
        "success"
        if outcome == CalendarWriteOutcome.SUCCESS
        else ("failure" if outcome == CalendarWriteOutcome.FAILED else "skipped")
    )
    fields = {
        "component": COMPONENT,
        "op": OP_CREATE,
        "outcome": log_outcome,
        "confirmation_id": confirmation_id,
        "correlation_id": correlation_id,
        "calendar_id": calendar_id,
        "calendar_event_id": calendar_event_id,
        "title": title,
        "start": start.isoformat() if start is not None else None,
        "duration_ms": duration_ms,
        "error_type": error_type,
        "error_message": error_message,
    }
    if log_level == "critical":
        logger.critical(log_event, **fields)
    elif log_level == "error":
        logger.error(log_event, **fields)
    elif log_level == "warning":
        logger.warning(log_event, **fields)
    else:
        logger.info(log_event, **fields)
    return result


def _resolve_calendar_id(calendar_id: str | None) -> str:
    if calendar_id is not None and calendar_id.strip():
        return calendar_id.strip()
    env_id = os.environ.get("GOOGLE_CALENDAR_ID", "").strip()
    return env_id or "primary"


def _looks_like_email(value: str) -> bool:
    text = (value or "").strip()
    if "@" not in text or " " in text:
        return False
    local, _, domain = text.partition("@")
    return bool(local and "." in domain)


def _normalize_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(tz=FAMILY_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=FAMILY_TZ)
    return now.astimezone(FAMILY_TZ)


def _draft_to_google_event(draft: CalendarEventDraft) -> dict:
    if draft.all_day:
        start: dict[str, str] = {"date": draft.start.date().isoformat()}
        end: dict[str, str] = {"date": draft.end.date().isoformat()}
    else:
        start = {
            "dateTime": draft.start.isoformat(),
            "timeZone": draft.time_zone,
        }
        end = {
            "dateTime": draft.end.isoformat(),
            "timeZone": draft.time_zone,
        }
    body: dict = {
        "summary": draft.summary,
        "start": start,
        "end": end,
        "extendedProperties": {"private": {"confirmation_id": draft.confirmation_id}},
    }
    if draft.location:
        body["location"] = draft.location
    if draft.description:
        body["description"] = draft.description
    if draft.attendees:
        body["attendees"] = [{"email": email} for email in draft.attendees]
    return body


def _audit_to_dict(record: CalendarAuditRecord) -> dict:
    return {
        "audit_id": record.audit_id,
        "attempted_at": record.attempted_at.isoformat(),
        "op": record.op,
        "confirmation_id": record.confirmation_id,
        "correlation_id": record.correlation_id,
        "outcome": record.outcome.value,
        "calendar_id": record.calendar_id,
        "calendar_event_id": record.calendar_event_id,
        "title": record.title,
        "start": record.start.isoformat() if record.start else None,
        "error_type": record.error_type,
        "error_message": record.error_message,
    }


def _audit_from_dict(data: dict) -> CalendarAuditRecord:
    return CalendarAuditRecord(
        audit_id=data["audit_id"],
        attempted_at=_parse_dt(data["attempted_at"]) or datetime.now(tz=FAMILY_TZ),
        op=data.get("op") or OP_CREATE,
        confirmation_id=data.get("confirmation_id"),
        correlation_id=data.get("correlation_id"),
        outcome=CalendarWriteOutcome(data["outcome"]),
        calendar_id=data.get("calendar_id"),
        calendar_event_id=data.get("calendar_event_id"),
        title=data.get("title"),
        start=_parse_dt(data.get("start")),
        error_type=data.get("error_type"),
        error_message=data.get("error_message"),
    )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=FAMILY_TZ)
    return dt
