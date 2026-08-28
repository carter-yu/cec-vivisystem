"""Confirmation Guardian (Phase 4) — thin human gate before calendar writes.

Creates pending confirmations from ``create_event`` ParseResults, resolves
accept/reject/expire, and purges operational rows (retention class C).

No Google Calendar I/O. No LLM. Slack thread yes/no is Phase 4b (Listener).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from cec_vivisystem.logging import get_logger
from cec_vivisystem.models import (
    Confidence,
    Confirmation,
    ConfirmationDecision,
    ConfirmationStatus,
    IntentType,
    ParseResult,
)

logger = get_logger(__name__)

COMPONENT = "confirmation"
FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")

# Default pending lifetime
DEFAULT_TTL = timedelta(hours=24)

# Class C: terminal + 7 days; pending absolute max 30 days
TERMINAL_RETENTION = timedelta(days=7)
PENDING_MAX_AGE = timedelta(days=30)

TERMINAL_STATUSES = frozenset(
    {
        ConfirmationStatus.ACCEPTED,
        ConfirmationStatus.REJECTED,
        ConfirmationStatus.EXPIRED,
    }
)

# Entire-message yes/no tokens (Phase 4b). Casefolded; CJK unchanged.
ACCEPT_REPLIES = frozenset({"yes", "y", "ok", "好", "係", "確認"})
REJECT_REPLIES = frozenset({"no", "n", "不要", "唔好", "否"})
_TRAILING_PUNCT = "!.?。！？"


class ConfirmationError(Exception):
    """Controlled confirmation failure (e.g. non-create_event input)."""


class ConfirmationStore(Protocol):
    """Persistence for confirmation records."""

    def save(self, confirmation: Confirmation) -> None: ...

    def get(self, confirmation_id: str) -> Confirmation | None: ...

    def list_all(self) -> list[Confirmation]: ...

    def delete(self, confirmation_id: str) -> None: ...


class InMemoryConfirmationStore:
    """Test/default store — no disk."""

    def __init__(self) -> None:
        self._items: dict[str, Confirmation] = {}

    def save(self, confirmation: Confirmation) -> None:
        self._items[confirmation.confirmation_id] = confirmation

    def get(self, confirmation_id: str) -> Confirmation | None:
        return self._items.get(confirmation_id)

    def list_all(self) -> list[Confirmation]:
        return list(self._items.values())

    def delete(self, confirmation_id: str) -> None:
        self._items.pop(confirmation_id, None)

    def list_pending(self) -> list[Confirmation]:
        return [c for c in self._items.values() if c.status == ConfirmationStatus.PENDING]


class JsonDirConfirmationStore:
    """One JSON file per confirmation under a directory (gitignored data/)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, confirmation_id: str) -> Path:
        safe = confirmation_id.replace("/", "_")
        return self.root / f"{safe}.json"

    def save(self, confirmation: Confirmation) -> None:
        path = self._path(confirmation.confirmation_id)
        try:
            path.write_text(
                json.dumps(_confirmation_to_dict(confirmation), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error(
                "confirmation_store_save_failed",
                component=COMPONENT,
                outcome="failure",
                confirmation_id=confirmation.confirmation_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise

    def get(self, confirmation_id: str) -> Confirmation | None:
        path = self._path(confirmation_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return _confirmation_from_dict(data)
        except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            logger.error(
                "confirmation_store_load_failed",
                component=COMPONENT,
                outcome="failure",
                confirmation_id=confirmation_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return None

    def list_all(self) -> list[Confirmation]:
        items: list[Confirmation] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                items.append(_confirmation_from_dict(data))
            except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
                logger.error(
                    "confirmation_store_load_failed",
                    component=COMPONENT,
                    outcome="failure",
                    path=str(path),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
        return items

    def delete(self, confirmation_id: str) -> None:
        path = self._path(confirmation_id)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.error(
                "confirmation_store_delete_failed",
                component=COMPONENT,
                outcome="failure",
                confirmation_id=confirmation_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise

    def list_pending(self) -> list[Confirmation]:
        return [c for c in self.list_all() if c.status == ConfirmationStatus.PENDING]


def default_data_dir() -> Path:
    """``<repo>/data/confirmations``."""
    return Path(__file__).resolve().parents[2] / "data" / "confirmations"


def build_proposal(parse_result: ParseResult) -> str:
    """Human-readable proposal. Never claims a calendar write."""
    if parse_result.intent_type != IntentType.CREATE_EVENT:
        raise ConfirmationError(
            f"proposal requires create_event, got {parse_result.intent_type.value}"
        )

    lines = [
        "Please confirm this calendar create proposal:",
        f"• Title: {parse_result.title or '(untitled)'}",
    ]
    if parse_result.start is not None:
        lines.append(f"• Start: {parse_result.start.isoformat()}")
    if parse_result.all_day:
        lines.append("• All-day: yes")
    if parse_result.participants:
        lines.append(f"• Participants: {', '.join(parse_result.participants)}")
    if parse_result.location:
        lines.append(f"• Location: {parse_result.location}")
    lines.append(f"• Confidence: {parse_result.confidence.value}")
    lines.append("Reply yes to accept, or no to reject.")
    lines.append("No calendar change will be made until you confirm (and a later Writer phase).")
    return "\n".join(lines)


def classify_confirmation_reply(text: str) -> ConfirmationDecision | None:
    """Return accept/reject if ``text`` is a locked short reply; else None."""
    token = (text or "").strip().casefold().rstrip(_TRAILING_PUNCT).strip()
    if token in ACCEPT_REPLIES:
        return ConfirmationDecision.ACCEPT
    if token in REJECT_REPLIES:
        return ConfirmationDecision.REJECT
    return None


def find_pending_for_thread(
    *,
    store: ConfirmationStore,
    channel_id: str,
    thread_ts: str,
) -> Confirmation | None:
    """Latest pending confirmation anchored to this Slack thread, if any."""
    matches = [
        item
        for item in store.list_all()
        if item.status == ConfirmationStatus.PENDING
        and item.channel_id == channel_id
        and item.thread_ts == thread_ts
    ]
    if not matches:
        return None
    return max(matches, key=lambda c: (c.created_at, c.confirmation_id))


def create_confirmation(
    parse_result: ParseResult,
    *,
    store: ConfirmationStore,
    now: datetime | None = None,
    correlation_id: str | None = None,
    ttl: timedelta = DEFAULT_TTL,
    channel_id: str | None = None,
    thread_ts: str | None = None,
) -> Confirmation:
    """Create a pending confirmation for a create_event parse result."""
    if parse_result.intent_type != IntentType.CREATE_EVENT:
        raise ConfirmationError(
            f"only create_event can create a confirmation, got {parse_result.intent_type.value}"
        )

    created = _normalize_now(now)
    conf_id = str(uuid.uuid4())
    corr = correlation_id or str(uuid.uuid4())
    proposal = build_proposal(parse_result)

    confirmation = Confirmation(
        confirmation_id=conf_id,
        status=ConfirmationStatus.PENDING,
        correlation_id=corr,
        parse_result=parse_result,
        proposal_text=proposal,
        created_at=created,
        expires_at=created + ttl,
        channel_id=channel_id,
        thread_ts=thread_ts,
    )
    store.save(confirmation)
    logger.info(
        "confirmation_created",
        component=COMPONENT,
        outcome="success",
        confirmation_id=conf_id,
        correlation_id=corr,
        expires_at=confirmation.expires_at.isoformat(),
        intent_type=parse_result.intent_type.value,
    )
    return confirmation


def resolve_confirmation(
    confirmation_id: str,
    decision: ConfirmationDecision | str,
    *,
    store: ConfirmationStore,
    now: datetime | None = None,
    actor: str | None = None,
) -> Confirmation:
    """Accept or reject a pending confirmation.

    If already terminal with the same outcome, returns the existing record
    (idempotent). If terminal with a different status, raises ConfirmationError.
    """
    if isinstance(decision, str):
        decision = ConfirmationDecision(decision)

    current = store.get(confirmation_id)
    if current is None:
        raise ConfirmationError(f"confirmation not found: {confirmation_id}")

    target = (
        ConfirmationStatus.ACCEPTED
        if decision == ConfirmationDecision.ACCEPT
        else ConfirmationStatus.REJECTED
    )

    if current.status in TERMINAL_STATUSES:
        if current.status == target:
            return current
        raise ConfirmationError(
            f"confirmation {confirmation_id} already {current.status.value}"
        )

    resolved_at = _normalize_now(now)
    updated = replace(
        current,
        status=target,
        resolved_at=resolved_at,
        resolved_by=actor,
    )
    store.save(updated)
    pending_ms = int((resolved_at - current.created_at).total_seconds() * 1000)
    logger.info(
        "confirmation_resolved",
        component=COMPONENT,
        outcome="success",
        confirmation_id=confirmation_id,
        correlation_id=current.correlation_id,
        decision=decision.value,
        status=target.value,
        resolved_by=actor,
        duration_pending_ms=pending_ms,
    )
    return updated


def expire_due_confirmations(
    *,
    store: ConfirmationStore,
    now: datetime | None = None,
) -> list[Confirmation]:
    """Mark pending confirmations past expires_at as expired."""
    moment = _normalize_now(now)
    expired: list[Confirmation] = []
    for item in store.list_all():
        if item.status != ConfirmationStatus.PENDING:
            continue
        if item.expires_at <= moment:
            updated = replace(
                item,
                status=ConfirmationStatus.EXPIRED,
                resolved_at=moment,
                resolved_by="system",
            )
            store.save(updated)
            logger.warning(
                "confirmation_timeout",
                component=COMPONENT,
                outcome="partial",
                confirmation_id=item.confirmation_id,
                correlation_id=item.correlation_id,
                expires_at=item.expires_at.isoformat(),
            )
            logger.info(
                "confirmation_resolved",
                component=COMPONENT,
                outcome="success",
                confirmation_id=item.confirmation_id,
                correlation_id=item.correlation_id,
                decision="expire",
                status=ConfirmationStatus.EXPIRED.value,
                resolved_by="system",
            )
            expired.append(updated)
    return expired


def purge_confirmations(
    *,
    store: ConfirmationStore,
    now: datetime | None = None,
    terminal_retention: timedelta = TERMINAL_RETENTION,
    pending_max_age: timedelta = PENDING_MAX_AGE,
) -> int:
    """Delete class-C rows past retention (terminal+7d; pending max 30d).

    Returns number of deleted records.
    """
    moment = _normalize_now(now)
    deleted = 0
    for item in list(store.list_all()):
        should_delete = False
        if item.status in TERMINAL_STATUSES:
            resolved = item.resolved_at or item.created_at
            if resolved <= moment - terminal_retention:
                should_delete = True
        elif item.status == ConfirmationStatus.PENDING:
            if item.created_at <= moment - pending_max_age:
                should_delete = True
        if should_delete:
            store.delete(item.confirmation_id)
            deleted += 1
    logger.info(
        "confirmation_purge_completed",
        component=COMPONENT,
        outcome="success",
        purged=deleted,
        terminal_retention_days=terminal_retention.days,
        pending_max_age_days=pending_max_age.days,
    )
    return deleted


def maintain_confirmation_storage(
    *,
    store: ConfirmationStore,
    now: datetime | None = None,
) -> dict[str, int]:
    """Expire due pendings then purge stale rows (call on service start)."""
    expired = expire_due_confirmations(store=store, now=now)
    purged = purge_confirmations(store=store, now=now)
    return {"expired": len(expired), "purged": purged}


def _normalize_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(tz=FAMILY_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=FAMILY_TZ)
    return now.astimezone(FAMILY_TZ)


def _confirmation_to_dict(c: Confirmation) -> dict:
    pr = c.parse_result
    return {
        "confirmation_id": c.confirmation_id,
        "status": c.status.value,
        "correlation_id": c.correlation_id,
        "proposal_text": c.proposal_text,
        "created_at": c.created_at.isoformat(),
        "expires_at": c.expires_at.isoformat(),
        "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
        "resolved_by": c.resolved_by,
        "channel_id": c.channel_id,
        "thread_ts": c.thread_ts,
        "parse_result": {
            "intent_type": pr.intent_type.value,
            "title": pr.title,
            "start": pr.start.isoformat() if pr.start else None,
            "end": pr.end.isoformat() if pr.end else None,
            "all_day": pr.all_day,
            "location": pr.location,
            "participants": list(pr.participants),
            "raw_text": pr.raw_text,
            "confidence": pr.confidence.value,
            "missing_fields": list(pr.missing_fields),
            "notes": pr.notes,
        },
    }


def _confirmation_from_dict(data: dict) -> Confirmation:
    pr_data = data["parse_result"]
    parse_result = ParseResult(
        intent_type=IntentType(pr_data["intent_type"]),
        title=pr_data.get("title"),
        start=_parse_dt(pr_data.get("start")),
        end=_parse_dt(pr_data.get("end")),
        all_day=bool(pr_data.get("all_day", False)),
        location=pr_data.get("location"),
        participants=list(pr_data.get("participants") or []),
        raw_text=pr_data.get("raw_text") or "",
        confidence=Confidence(pr_data.get("confidence") or Confidence.LOW.value),
        missing_fields=list(pr_data.get("missing_fields") or []),
        notes=pr_data.get("notes"),
    )
    return Confirmation(
        confirmation_id=data["confirmation_id"],
        status=ConfirmationStatus(data["status"]),
        correlation_id=data["correlation_id"],
        parse_result=parse_result,
        proposal_text=data["proposal_text"],
        created_at=_parse_dt(data["created_at"]) or datetime.now(tz=FAMILY_TZ),
        expires_at=_parse_dt(data["expires_at"]) or datetime.now(tz=FAMILY_TZ),
        resolved_at=_parse_dt(data.get("resolved_at")),
        resolved_by=data.get("resolved_by"),
        channel_id=data.get("channel_id"),
        thread_ts=data.get("thread_ts"),
    )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=FAMILY_TZ)
    return dt
