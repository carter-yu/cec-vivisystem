"""Slack Listener — thin intake slice (Phase 2 + 5B + 4b + 6 + 8 + 12 + 14 + 15 + 27 + 28).

Receives family messages from named Slack channels:

- ``#family-plans`` → parse; ``create_event`` creates a pending confirmation
  (Phase 4b) and thread yes/no resolves it. On first accept, an injectable
  Calendar client (Phase 6) may create one Google Calendar event. Create
  proposals may include an overlap / same-person warning (Phase 8).
  ``list_events`` always replies (list, empty, period recap, or explicit
  error) with no confirmation and no calendar write (Phase 12 + 14).
  Important dates add/view immediately (Phase 15; no confirmation; no
  calendar write).
  Phase 27: every create passes the series policy (span guard + weekday
  series expand); a series gets per-occurrence overlap, one proposal and a
  writer loop on yes. Phase 28: ``delete_event`` lists one HKT day, matches,
  and asks for confirmation (numbered pick when ambiguous) before
  ``write_calendar_delete``. Rejecting a create never deletes.
- ``#family-life-notes`` → ``create_life_note`` (Phase 5B).

Secrets load from the environment only (ground rule 13). Unit tests use the
pure handlers below with no network.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import wraps
from threading import RLock
from typing import Any

from cec_vivisystem.calendar_delete import (
    MAX_DELETE_CANDIDATES,
    delete_window,
    match_delete_candidates,
)
from cec_vivisystem.calendar_reader import (
    format_event_list,
    format_recap,
    list_calendar_events,
)
from cec_vivisystem.calendar_writer import (
    CalendarAuditStore,
    CalendarClient,
    CalendarWriterConfigError,
    GoogleCalendarClient,
    JsonDirCalendarAuditStore,
    is_google_auth_error,
    load_google_calendar_config,
    maintain_calendar_audit_storage,
    probe_google_client,
    write_calendar_create,
    write_calendar_delete,
    write_calendar_series_create,
)
from cec_vivisystem.calendar_writer import (
    default_audit_dir as calendar_audit_data_dir,
)
from cec_vivisystem.confirmation import (
    ConfirmationError,
    ConfirmationStore,
    JsonDirConfirmationStore,
    classify_confirmation_reply,
    classify_pick_reply,
    create_confirmation,
    create_delete_confirmation,
    expire_due_confirmations,
    find_accepted_for_thread,
    find_pending_for_thread,
    format_delete_target,
    maintain_confirmation_storage,
    resolve_confirmation,
    select_delete_candidate,
)
from cec_vivisystem.confirmation import (
    default_data_dir as confirmation_data_dir,
)
from cec_vivisystem.important_dates import (
    NOT_LISTED,
    NOT_STORED,
    ImportantDatesStore,
    JsonDirImportantDatesStore,
    create_important_date,
    format_add_ack,
    format_important_dates_list,
)
from cec_vivisystem.important_dates import (
    default_data_dir as important_dates_data_dir,
)
from cec_vivisystem.life_notes import (
    JsonDirLifeNotesStore,
    LifeNotesStore,
    create_life_note,
)
from cec_vivisystem.life_notes import (
    default_data_dir as life_notes_data_dir,
)
from cec_vivisystem.logging import get_logger
from cec_vivisystem.models import (
    CalendarListOutcome,
    CalendarSeriesWriteResult,
    CalendarWriteOutcome,
    CalendarWriteResult,
    Confirmation,
    ConfirmationDecision,
    ConfirmationStatus,
    InboundMessage,
    IntentType,
    ListenerOutcome,
    ListenerResult,
    ParseResult,
)
from cec_vivisystem.overlap import detect_create_overlaps, detect_series_overlaps
from cec_vivisystem.parse_fallback import load_live_llm_parsers, parse_with_fallback
from cec_vivisystem.parse_misses import (
    JsonDirParseMissStore,
    maintain_parse_miss_storage,
)
from cec_vivisystem.parse_misses import (
    default_data_dir as parse_miss_dir,
)
from cec_vivisystem.parser import format_allowed_inputs
from cec_vivisystem.parser import parse as default_parse
from cec_vivisystem.series import (
    apply_series_policy,
    expand_weekday_series,
    format_series_clarification,
    weekday_summary,
)

logger = get_logger(__name__)

COMPONENT = "listener"
PREVIEW_LEN = 80
# Slack SDK owns recovery; application health checks must not replace its socket.
SOCKET_PING_INTERVAL_S = 5.0
SOCKET_HEALTH_INTERVAL_S = 15.0
LIFE_NOTE_ACK = "已記低"
CONFIRM_ACCEPTED_ACK = "Accepted. Calendar write is not configured; no calendar write was attempted."
CONFIRM_ACCEPTED_WRITTEN_ACK = "Accepted. Calendar event created."
CONFIRM_ALREADY_ADDED_ACK = "Already added. No second calendar event was created."
CONFIRM_ACCEPTED_WRITE_FAILED_ACK = (
    "Accepted. Calendar write failed; could not verify whether the event was created. "
    "Reply yes in this thread to retry the same request."
)
CONFIRM_ACCEPTED_WRITE_AUTH_FAILED_ACK = (
    "Accepted. Calendar write failed (Google login expired); no event was created."
)
CONFIRM_REJECTED_ACK = "Rejected. No calendar change was made."
CALENDAR_READ_UNAVAILABLE = (
    "Calendar read is not configured. No calendar change was made."
)
CALENDAR_LIST_ERROR = "Could not read the calendar. No calendar change was made."
READ_ONLY_DISCLAIMER = "No calendar change was made."
NO_CHANGE_BILINGUAL = "未有改動日曆。/ No calendar change was made."
DELETE_UNAVAILABLE = "刪除功能未設定（需要日曆連線）。/ Calendar delete is not configured. " + READ_ONLY_DISCLAIMER
DELETE_LIST_ERROR = "未能讀取日曆，冇刪除任何嘢。/ Could not read the calendar. " + READ_ONLY_DISCLAIMER
DELETE_PICK_FIRST = (
    "請先回覆編號揀一個活動，揀咗之後再回 yes。未有刪除任何嘢。/ "
    "Reply with a number to pick one event first. " + READ_ONLY_DISCLAIMER
)
HELP_HINT = "Type help or 指令 for common inputs."
CREATE_EXAMPLES = (
    "Try e.g. 「今晚10點去公園」 or "
    "「加活動，今日2:30 ，Cedric 睇牙醫」."
)
ParseFn = Callable[..., ParseResult]


class ConfigError(Exception):
    """Missing or invalid Slack configuration (no secret values in message)."""


@dataclass(frozen=True, slots=True)
class SlackConfig:
    """Runtime Slack settings loaded from the environment."""

    bot_token: str
    app_token: str
    allowed_channel_ids: frozenset[str]
    life_notes_channel_id: str


def load_slack_config(env: Mapping[str, str] | None = None) -> SlackConfig:
    """Load Slack config from ``env`` (default: ``os.environ``).

    Raises:
        ConfigError: if required variables are missing or empty.
        Never includes secret values in the error message.
    """
    source = env if env is not None else os.environ
    missing: list[str] = []

    bot = (source.get("SLACK_BOT_TOKEN") or "").strip()
    app = (source.get("SLACK_APP_TOKEN") or "").strip()
    plans_channel = (source.get("SLACK_FAMILY_PLANS_CHANNEL_ID") or "").strip()
    life_notes_channel = (source.get("SLACK_LIFE_NOTES_CHANNEL_ID") or "").strip()

    if not bot:
        missing.append("SLACK_BOT_TOKEN")
    if not app:
        missing.append("SLACK_APP_TOKEN")
    if not plans_channel:
        missing.append("SLACK_FAMILY_PLANS_CHANNEL_ID")
    if not life_notes_channel:
        missing.append("SLACK_LIFE_NOTES_CHANNEL_ID")

    if missing:
        raise ConfigError(
            "Missing required Slack configuration: " + ", ".join(missing)
        )

    return SlackConfig(
        bot_token=bot,
        app_token=app,
        allowed_channel_ids=frozenset({plans_channel}),
        life_notes_channel_id=life_notes_channel,
    )


def normalize_slack_event(raw: object) -> InboundMessage | None:
    """Map a Slack message event dict to ``InboundMessage``, or ``None`` to skip.

    Returns ``None`` for non-dicts, bot messages, non-plain subtypes, or
    payloads missing channel/user/text keys in a usable form.
    """
    if not isinstance(raw, dict):
        return None

    event_type = raw.get("type")
    if event_type is not None and event_type != "message":
        return None

    if raw.get("bot_id") or raw.get("bot_profile"):
        return None

    subtype = raw.get("subtype")
    if subtype:
        # Phase 2: only plain user text (no subtype)
        return None

    channel = raw.get("channel")
    user = raw.get("user")
    text = raw.get("text")

    if not isinstance(channel, str) or not channel:
        return None
    if not isinstance(user, str) or not user:
        return None
    if not isinstance(text, str):
        return None

    event_id = raw.get("client_msg_id") or raw.get("event_ts") or raw.get("ts")
    if event_id is not None and not isinstance(event_id, str):
        event_id = str(event_id)

    ts = raw.get("ts") if isinstance(raw.get("ts"), str) else None
    thread_ts = raw.get("thread_ts") if isinstance(raw.get("thread_ts"), str) else None

    return InboundMessage(
        text=text,
        channel_id=channel,
        user_id=user,
        slack_event_id=event_id,
        correlation_id=str(uuid.uuid4()),
        ts=ts,
        thread_ts=thread_ts,
    )


def should_accept(
    message: InboundMessage,
    *,
    allowed_channel_ids: frozenset[str] | set[str] | list[str],
) -> bool:
    """Return True if the message channel is in the allowlist."""
    allowed = set(allowed_channel_ids)
    return message.channel_id in allowed


def format_reply(result: ParseResult) -> str:
    """Build a short English summary. Never claims a calendar write."""
    disclaimer = "No calendar change was made."

    if result.intent_type == IntentType.CREATE_EVENT:
        lines = [
            "Understood create-event proposal (not written to calendar):",
        ]
        if result.title:
            lines.append(f"• Title: {result.title}")
        if result.series is not None:
            count = len(expand_weekday_series(result.series))
            lines.append(
                f"• Series: {weekday_summary(result.series.weekdays)} "
                f"{result.series.range_start.isoformat()}–{result.series.range_end.isoformat()}"
                f"（共 {count} 次 / {count} occurrences）"
            )
        if result.start is not None:
            lines.append(f"• Start: {result.start.isoformat()}")
        if result.all_day:
            lines.append("• All-day: yes")
        if result.participants:
            lines.append(f"• Participants: {', '.join(result.participants)}")
        if result.location:
            lines.append(f"• Location: {result.location}")
        lines.append(f"• Confidence: {result.confidence.value}")
        lines.append(disclaimer)
        return "\n".join(lines)

    if result.intent_type == IntentType.LIST_EVENTS:
        return (
            "Calendar list request understood, but no calendar was queried. "
            + disclaimer
        )

    if result.intent_type == IntentType.DELETE_EVENT:
        return DELETE_UNAVAILABLE

    if result.intent_type == IntentType.ADD_IMPORTANT_DATE:
        return NOT_STORED + " " + disclaimer

    if result.intent_type == IntentType.LIST_IMPORTANT_DATES:
        return NOT_LISTED + " " + disclaimer

    if result.intent_type == IntentType.HELP:
        return format_allowed_inputs()

    if result.notes == "llm_unavailable":
        return "Event understanding is temporarily unavailable. Please try again later. " + disclaimer

    if result.intent_type == IntentType.NEEDS_CLARIFICATION:
        series_reply = format_series_clarification(result)
        if series_reply is not None:
            return series_reply
        delete_reply = _delete_clarification(result)
        if delete_reply is not None:
            return delete_reply
        missing = ", ".join(result.missing_fields) if result.missing_fields else "details"
        return (
            f"Need more detail before this can be a calendar create "
            f"(missing: {missing}). {CREATE_EXAMPLES} {HELP_HINT} {disclaimer}"
        )

    return (
        "Could not treat that as a calendar create request. "
        + CREATE_EXAMPLES
        + " "
        + HELP_HINT
        + " "
        + disclaimer
    )


def handle_inbound(
    message: InboundMessage,
    *,
    parse: ParseFn = default_parse,
    now: datetime | None = None,
    correlation_id: str | None = None,
    confirmation_store: ConfirmationStore | None = None,
    calendar_client: CalendarClient | None = None,
    calendar_id: str | None = None,
    important_dates_store: ImportantDatesStore | None = None,
) -> ListenerResult:
    """Parse an accepted inbound message and build a reply (no Slack I/O)."""
    started = time.perf_counter()
    corr = correlation_id or message.correlation_id or str(uuid.uuid4())
    preview = _preview(message.text)
    parse_result: ParseResult | None = None

    logger.info(
        "message_received",
        component=COMPONENT,
        correlation_id=corr,
        channel_id=message.channel_id,
        user_id=message.user_id,
        slack_event_id=message.slack_event_id,
        message_length=len(message.text),
        message_preview=preview,
    )

    try:
        parse_result = parse(message.text, now=now, correlation_id=corr)
        # Phase 27: span guard + weekday-series expand for every create path.
        parse_result = apply_series_policy(parse_result, now=now, correlation_id=corr)
        next_component = "parser"
        if parse_result.intent_type == IntentType.DELETE_EVENT:
            reply = _reply_for_delete_event(
                parse_result,
                message=message,
                confirmation_store=confirmation_store,
                calendar_client=calendar_client,
                calendar_id=calendar_id,
                now=now,
                correlation_id=corr,
            )
            if confirmation_store is not None and calendar_client is not None:
                next_component = "confirmation"
        elif parse_result.intent_type == IntentType.LIST_EVENTS:
            reply = _reply_for_list_events(
                parse_result,
                calendar_client=calendar_client,
                calendar_id=calendar_id,
                correlation_id=corr,
            )
            if calendar_client is not None and parse_result.start is not None:
                next_component = "calendar_reader"
        elif parse_result.intent_type == IntentType.ADD_IMPORTANT_DATE:
            reply = _reply_for_add_important_date(
                parse_result,
                store=important_dates_store,
                now=now,
                correlation_id=corr,
            )
            if important_dates_store is not None:
                next_component = "important_dates"
        elif parse_result.intent_type == IntentType.LIST_IMPORTANT_DATES:
            reply = _reply_for_list_important_dates(
                store=important_dates_store,
            )
            if important_dates_store is not None:
                next_component = "important_dates"
        elif (
            confirmation_store is not None
            and parse_result.intent_type == IntentType.CREATE_EVENT
        ):
            overlap_check = None
            series_overlap = None
            if calendar_client is not None and parse_result.series is not None:
                # Per-occurrence windows only — never one multi-week interval.
                series_overlap = detect_series_overlaps(
                    expand_weekday_series(parse_result.series),
                    participants=parse_result.participants,
                    client=calendar_client,
                    calendar_id=calendar_id,
                    correlation_id=corr,
                )
            elif calendar_client is not None:
                overlap_check = detect_create_overlaps(
                    parse_result,
                    client=calendar_client,
                    calendar_id=calendar_id,
                    correlation_id=corr,
                )
            confirmation = create_confirmation(
                parse_result,
                store=confirmation_store,
                now=now,
                correlation_id=corr,
                channel_id=message.channel_id,
                thread_ts=message.thread_ts or message.ts,
                source_message_id=message.ts or message.slack_event_id,
                overlap_check=overlap_check,
                series_overlap=series_overlap,
            )
            reply = (
                confirmation.proposal_text
                if confirmation.status == ConfirmationStatus.PENDING
                else f"This proposal is already {confirmation.status.value}. No new proposal was created."
            )
            next_component = "confirmation"
        else:
            reply = format_reply(parse_result)
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "dispatch_succeeded",
            component=COMPONENT,
            correlation_id=corr,
            outcome="success",
            next_component=next_component,
            intent_type=parse_result.intent_type.value,
            duration_ms=duration_ms,
        )
        return ListenerResult(
            outcome=ListenerOutcome.REPLIED,
            correlation_id=corr,
            parse_result=parse_result,
            reply_text=reply,
        )
    except Exception as exc:  # noqa: BLE001 — boundary: never crash the listener
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.error(
            "dispatch_failed",
            component=COMPONENT,
            correlation_id=corr,
            outcome="failure",
            next_component="parser",
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        # LIST_EVENTS must still produce a user-visible line (Phase 12).
        if (
            parse_result is not None
            and parse_result.intent_type == IntentType.LIST_EVENTS
        ):
            return ListenerResult(
                outcome=ListenerOutcome.REPLIED,
                correlation_id=corr,
                parse_result=parse_result,
                reply_text=CALENDAR_LIST_ERROR,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        return ListenerResult(
            outcome=ListenerOutcome.FAILED,
            correlation_id=corr,
            parse_result=parse_result,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )


def handle_life_note_inbound(
    message: InboundMessage,
    *,
    now: datetime | None = None,
    store: LifeNotesStore | None = None,
    correlation_id: str | None = None,
) -> ListenerResult:
    """Store an accepted life-notes message. Does not call the parser."""
    started = time.perf_counter()
    corr = correlation_id or message.correlation_id or str(uuid.uuid4())
    preview = _preview(message.text)

    logger.info(
        "message_received",
        component=COMPONENT,
        correlation_id=corr,
        channel_id=message.channel_id,
        user_id=message.user_id,
        slack_event_id=message.slack_event_id,
        message_length=len(message.text),
        message_preview=preview,
        next_component="life_notes",
    )

    source = {
        "channel": message.channel_id,
        "message_id": message.slack_event_id or message.ts or "",
        "user": message.user_id,
    }
    try:
        create_life_note(
            message.text,
            source=source,
            deduplicate_source=True,
            now=now,
            store=store,
            correlation_id=corr,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "dispatch_succeeded",
            component=COMPONENT,
            correlation_id=corr,
            outcome="success",
            next_component="life_notes",
            duration_ms=duration_ms,
        )
        return ListenerResult(
            outcome=ListenerOutcome.REPLIED,
            correlation_id=corr,
            reply_text=LIFE_NOTE_ACK,
        )
    except Exception as exc:  # noqa: BLE001 — boundary: never crash the listener
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.error(
            "dispatch_failed",
            component=COMPONENT,
            correlation_id=corr,
            outcome="failure",
            next_component="life_notes",
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return ListenerResult(
            outcome=ListenerOutcome.FAILED,
            correlation_id=corr,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )


def handle_confirmation_reply(
    message: InboundMessage,
    *,
    decision: ConfirmationDecision,
    store: ConfirmationStore,
    now: datetime | None = None,
    correlation_id: str | None = None,
    calendar_client: CalendarClient | None = None,
    calendar_id: str | None = None,
    calendar_audit_store: CalendarAuditStore | None = None,
) -> ListenerResult:
    """Resolve a pending confirmation from a short thread yes/no."""
    started = time.perf_counter()
    corr = correlation_id or message.correlation_id or str(uuid.uuid4())
    thread_ts = message.thread_ts or ""
    pending = find_pending_for_thread(
        store=store,
        channel_id=message.channel_id,
        thread_ts=thread_ts,
    )
    if pending is None:
        if decision == ConfirmationDecision.ACCEPT:
            accepted = find_accepted_for_thread(
                store=store,
                channel_id=message.channel_id,
                thread_ts=thread_ts,
            )
            if accepted is not None:
                return _already_added_reply(
                    message,
                    confirmation=accepted,
                    corr=corr,
                    started=started,
                    now=now,
                    calendar_client=calendar_client,
                    calendar_id=calendar_id,
                    calendar_audit_store=calendar_audit_store,
                )
        expired = [c for c in store.list_all() if c.channel_id == message.channel_id
                   and c.thread_ts == thread_ts and c.status == ConfirmationStatus.EXPIRED]
        if expired:
            return ListenerResult(outcome=ListenerOutcome.REPLIED, correlation_id=corr,
                                  reply_text="This proposal expired. Please send a new request to confirm.")
        return _ignored(corr, "no_pending_confirmation", message=message)

    logger.info(
        "message_received",
        component=COMPONENT,
        correlation_id=corr,
        channel_id=message.channel_id,
        user_id=message.user_id,
        slack_event_id=message.slack_event_id,
        message_length=len(message.text),
        message_preview=_preview(message.text),
        next_component="confirmation",
    )
    if (
        decision == ConfirmationDecision.ACCEPT
        and pending.parse_result.intent_type == IntentType.DELETE_EVENT
        and len(pending.delete_candidates) != 1
    ):
        # Several matches: yes alone must never pick a target to delete.
        return ListenerResult(
            outcome=ListenerOutcome.REPLIED, correlation_id=corr, reply_text=DELETE_PICK_FIRST
        )
    try:
        updated = resolve_confirmation(
            pending.confirmation_id,
            decision,
            store=store,
            now=now,
            actor=message.user_id,
        )
        ack = (
            CONFIRM_ACCEPTED_ACK
            if updated.status == ConfirmationStatus.ACCEPTED
            else CONFIRM_REJECTED_ACK
        )
        next_component = "confirmation"
        if (
            calendar_client is not None
            and updated.status == ConfirmationStatus.ACCEPTED
        ):
            ack = _write_for_accepted(
                updated,
                calendar_client=calendar_client,
                calendar_id=calendar_id,
                calendar_audit_store=calendar_audit_store,
                now=now,
            )
            next_component = "calendar_writer"
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "dispatch_succeeded",
            component=COMPONENT,
            correlation_id=corr,
            outcome="success",
            next_component=next_component,
            duration_ms=duration_ms,
            confirmation_id=updated.confirmation_id,
            status=updated.status.value,
        )
        return ListenerResult(
            outcome=ListenerOutcome.REPLIED,
            correlation_id=corr,
            reply_text=ack,
        )
    except Exception as exc:  # noqa: BLE001 — boundary: never crash the listener
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.error(
            "dispatch_failed",
            component=COMPONENT,
            correlation_id=corr,
            outcome="failure",
            next_component="confirmation",
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return ListenerResult(
            outcome=ListenerOutcome.FAILED,
            correlation_id=corr,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )


def _already_added_reply(
    message: InboundMessage,
    *,
    confirmation: Confirmation,
    corr: str,
    started: float,
    now: datetime | None,
    calendar_client: CalendarClient | None,
    calendar_id: str | None,
    calendar_audit_store: CalendarAuditStore | None,
) -> ListenerResult:
    """Second yes on an accepted thread: no extra insert; still a Slack reply."""
    logger.info(
        "message_received",
        component=COMPONENT,
        correlation_id=corr,
        channel_id=message.channel_id,
        user_id=message.user_id,
        slack_event_id=message.slack_event_id,
        message_length=len(message.text),
        message_preview=_preview(message.text),
        next_component="confirmation",
    )
    ack = CONFIRM_ACCEPTED_ACK
    next_component = "confirmation"
    try:
        if calendar_client is not None:
            ack = _write_for_accepted(
                confirmation,
                calendar_client=calendar_client,
                calendar_id=calendar_id,
                calendar_audit_store=calendar_audit_store,
                now=now,
            )
            next_component = "calendar_writer"
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "dispatch_succeeded",
            component=COMPONENT,
            correlation_id=corr,
            outcome="success",
            next_component=next_component,
            duration_ms=duration_ms,
            confirmation_id=confirmation.confirmation_id,
            status=confirmation.status.value,
        )
        return ListenerResult(
            outcome=ListenerOutcome.REPLIED,
            correlation_id=corr,
            reply_text=ack,
        )
    except Exception as exc:  # noqa: BLE001 — still reply; never crash
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.error(
            "dispatch_failed",
            component=COMPONENT,
            correlation_id=corr,
            outcome="failure",
            next_component="confirmation",
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return ListenerResult(
            outcome=ListenerOutcome.REPLIED,
            correlation_id=corr,
            reply_text=CONFIRM_ACCEPTED_WRITE_FAILED_ACK,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )


_dispatch_lock = RLock()


def _guard_dispatch(handler):
    """Serialize one listener's store transitions and shared Google transport.

    Bolt uses worker threads; JSON read/modify/write and httplib2 are not a
    transaction. This guard is process-local: operate one listener per store.
    """
    @wraps(handler)
    def guarded(*args, **kwargs):
        with _dispatch_lock:
            try:
                result = handler(*args, **kwargs)
                if result.outcome == ListenerOutcome.FAILED and not result.reply_text:
                    result.reply_text = "Could not process this request. Please retry in the same thread."
                return result
            except Exception as exc:  # noqa: BLE001 — intake must remain usable
                corr = kwargs.get("correlation_id") or str(uuid.uuid4())
                logger.error("dispatch_failed", component=COMPONENT, outcome="failure",
                             correlation_id=corr, error_type=type(exc).__name__,
                             error_message=str(exc))
                return ListenerResult(
                    outcome=ListenerOutcome.REPLIED, correlation_id=corr,
                    reply_text="Could not process this request. Please retry in the same thread.",
                    error_type=type(exc).__name__, error_message=str(exc),
                )
    return guarded


@_guard_dispatch
def process_slack_message_event(
    raw: object,
    *,
    allowed_channel_ids: frozenset[str] | set[str] | list[str],
    parse: ParseFn = default_parse,
    now: datetime | None = None,
    correlation_id: str | None = None,
    life_notes_channel_id: str | None = None,
    life_notes_store: LifeNotesStore | None = None,
    confirmation_store: ConfirmationStore | None = None,
    calendar_client: CalendarClient | None = None,
    calendar_id: str | None = None,
    calendar_audit_store: CalendarAuditStore | None = None,
    important_dates_store: ImportantDatesStore | None = None,
    llm_parser: object | None = None,
    llm_fallback: object | None = None,
    miss_store: object | None = None,
) -> ListenerResult:
    """Full offline pipeline: normalize → filter → dispatch.

    Plans channel → parse (and optional confirmation). Life-notes → keeper.
    Safe for any garbage input; does not perform Slack HTTP.
    ``llm_fallback`` is the second SpaceXAI model after the primary raises.
    """
    corr = correlation_id or str(uuid.uuid4())

    if not isinstance(raw, dict):
        return _ignored(corr, "malformed")

    if raw.get("bot_id") or raw.get("bot_profile") or raw.get("subtype") == "bot_message":
        return _ignored(corr, "bot_message", raw=raw)

    message = normalize_slack_event(raw)
    if message is None:
        reason = _normalize_skip_reason(raw)
        return _ignored(corr, reason, raw=raw)

    if correlation_id:
        message.correlation_id = correlation_id
    else:
        corr = message.correlation_id or corr

    is_life_notes = bool(
        life_notes_channel_id and message.channel_id == life_notes_channel_id
    )
    is_plans = should_accept(message, allowed_channel_ids=allowed_channel_ids)
    if not is_life_notes and not is_plans:
        return _ignored(corr, "wrong_channel", raw=raw, message=message)

    if not message.text.strip():
        return _ignored(corr, "empty_text", raw=raw, message=message)

    if is_life_notes:
        return handle_life_note_inbound(
            message,
            now=now,
            store=life_notes_store,
            correlation_id=corr,
        )

    if confirmation_store is not None and is_plans:
        expire_due_confirmations(store=confirmation_store, now=now)
        decision = classify_confirmation_reply(message.text)
        if decision is not None and message.thread_ts:
            return handle_confirmation_reply(
                message,
                decision=decision,
                store=confirmation_store,
                now=now,
                correlation_id=corr,
                calendar_client=calendar_client,
                calendar_id=calendar_id,
                calendar_audit_store=calendar_audit_store,
            )
        pick = classify_pick_reply(message.text)
        if pick is not None and message.thread_ts:
            picked = handle_delete_pick_reply(
                message, choice=pick, store=confirmation_store, now=now, correlation_id=corr
            )
            if picked is not None:
                return picked

    parse_for_inbound = parse
    if llm_parser is not None or llm_fallback is not None or miss_store is not None:

        def _parse_with_fallback(
            text: str,
            *,
            now: datetime | None = None,
            correlation_id: str | None = None,
        ):
            return parse_with_fallback(
                text,
                now=now,
                correlation_id=correlation_id,
                llm=llm_parser,  # type: ignore[arg-type]
                llm_fallback=llm_fallback,  # type: ignore[arg-type]
                miss_store=miss_store,  # type: ignore[arg-type]
                rule_parse_fn=parse,
                llm_first=True,
            )

        parse_for_inbound = _parse_with_fallback

    return handle_inbound(
        message,
        parse=parse_for_inbound,
        now=now,
        correlation_id=corr,
        confirmation_store=confirmation_store if is_plans else None,
        calendar_client=calendar_client if is_plans else None,
        calendar_id=calendar_id,
        important_dates_store=important_dates_store if is_plans else None,
    )


def handle_delete_pick_reply(
    message: InboundMessage,
    *,
    choice: int,
    store: ConfirmationStore,
    now: datetime | None = None,
    correlation_id: str | None = None,
) -> ListenerResult | None:
    """Narrow a pending delete pick list to one target. None if not applicable.

    The pick never deletes: it re-proposes the single target and still waits
    for an explicit yes in the same thread.
    """
    corr = correlation_id or message.correlation_id or str(uuid.uuid4())
    pending = find_pending_for_thread(
        store=store, channel_id=message.channel_id, thread_ts=message.thread_ts or ""
    )
    if (
        pending is None
        or pending.parse_result.intent_type != IntentType.DELETE_EVENT
        or len(pending.delete_candidates) < 2
    ):
        return None
    if choice > len(pending.delete_candidates):
        return ListenerResult(
            outcome=ListenerOutcome.REPLIED,
            correlation_id=corr,
            reply_text=(
                f"請回覆 1 至 {len(pending.delete_candidates)} 之間嘅編號。/ "
                f"Reply with a number from 1 to {len(pending.delete_candidates)}. "
                + READ_ONLY_DISCLAIMER
            ),
        )
    try:
        updated = select_delete_candidate(
            pending.confirmation_id, choice, store=store, now=now
        )
    except ConfirmationError as exc:
        logger.warning(
            "delete_pick_failed", component=COMPONENT, outcome="partial",
            correlation_id=corr, confirmation_id=pending.confirmation_id,
            error_type=type(exc).__name__, error_message=str(exc),
        )
        return ListenerResult(
            outcome=ListenerOutcome.REPLIED,
            correlation_id=corr,
            reply_text="呢個提案已經過期，請重新講一次。/ This proposal expired. Please send a new request.",
        )
    logger.info(
        "dispatch_succeeded", component=COMPONENT, correlation_id=corr, outcome="success",
        next_component="confirmation", confirmation_id=updated.confirmation_id,
        status=updated.status.value,
    )
    return ListenerResult(
        outcome=ListenerOutcome.REPLIED, correlation_id=corr, reply_text=updated.proposal_text
    )


def _reply_for_delete_event(
    parse_result: ParseResult,
    *,
    message: InboundMessage,
    confirmation_store: ConfirmationStore | None,
    calendar_client: CalendarClient | None,
    calendar_id: str | None,
    now: datetime | None,
    correlation_id: str,
) -> str:
    """List one HKT day, match, and propose. Never deletes; never raises."""
    if confirmation_store is None or calendar_client is None:
        return DELETE_UNAVAILABLE
    window = delete_window(parse_result)
    if window is None:
        return _delete_clarification(parse_result) or DELETE_UNAVAILABLE
    listed = list_calendar_events(
        time_min=window[0],
        time_max=window[1],
        client=calendar_client,
        calendar_id=calendar_id,
        correlation_id=correlation_id,
    )
    if listed.outcome != CalendarListOutcome.SUCCESS:
        return DELETE_LIST_ERROR
    candidates = match_delete_candidates(
        parse_result, listed.events, calendar_id=listed.calendar_id
    )
    day = window[0].date().isoformat()
    what = parse_result.title or "活動"
    logger.info(
        "delete_match_completed",
        component=COMPONENT,
        outcome="success",
        correlation_id=correlation_id,
        listed_count=len(listed.events),
        candidate_count=len(candidates),
    )
    if not candidates:
        return (
            f"搵唔到 {day} 相符嘅「{what}」，冇刪除任何嘢。/ "
            f"No matching event found on {day}. " + READ_ONLY_DISCLAIMER
        )
    if len(candidates) > MAX_DELETE_CANDIDATES:
        return (
            f"{day} 有 {len(candidates)} 個相符活動，太多喇。請講埋時間或者更完整嘅名稱。/ "
            f"Too many matches ({len(candidates)}); add a time or a fuller title. "
            + READ_ONLY_DISCLAIMER
        )
    confirmation = create_delete_confirmation(
        parse_result,
        candidates,
        store=confirmation_store,
        now=now,
        correlation_id=correlation_id,
        channel_id=message.channel_id,
        thread_ts=message.thread_ts or message.ts,
        source_message_id=message.ts or message.slack_event_id,
    )
    if confirmation.status == ConfirmationStatus.PENDING:
        return confirmation.proposal_text
    return (
        f"This proposal is already {confirmation.status.value}. No new proposal was created."
    )


def _delete_clarification(result: ParseResult) -> str | None:
    note = result.notes or ""
    if note == "delete_missing_date":
        what = f"「{result.title}」" if result.title else "嗰個活動"
        return (
            f"想刪除邊一日嘅{what}？請講埋日子，例如「刪除星期四游水」或者「刪除10月8號游水」。\n"
            "Which day is the event on? Please include the day, e.g. "
            "\"delete swim Thursday\".\n" + NO_CHANGE_BILINGUAL
        )
    if note == "delete_missing_target":
        return (
            "想刪除邊個活動？請講埋活動名稱或者時間。\n"
            "Which event should be deleted? Please add a title or time.\n" + NO_CHANGE_BILINGUAL
        )
    if note == "delete_invalid_when":
        return "日子或者時間唔啱，請再講一次。/ The day or time is invalid.\n" + NO_CHANGE_BILINGUAL
    return None


def _write_for_accepted(
    confirmation: Confirmation,
    *,
    calendar_client: CalendarClient,
    calendar_id: str | None,
    calendar_audit_store: CalendarAuditStore | None,
    now: datetime | None,
) -> str:
    """Route an accepted confirmation to the one matching Writer entrypoint."""
    if confirmation.parse_result.intent_type == IntentType.DELETE_EVENT:
        result = write_calendar_delete(
            confirmation, client=calendar_client, calendar_id=calendar_id,
            audit_store=calendar_audit_store, now=now,
        )
        return _delete_ack(result, confirmation)
    if confirmation.parse_result.series is not None:
        series_result = write_calendar_series_create(
            confirmation, client=calendar_client, calendar_id=calendar_id,
            audit_store=calendar_audit_store, now=now,
        )
        return _series_ack(series_result)
    return _write_ack(
        write_calendar_create(
            confirmation, client=calendar_client, calendar_id=calendar_id,
            audit_store=calendar_audit_store, now=now,
        )
    )


def _delete_ack(result: CalendarWriteResult, confirmation: Confirmation) -> str:
    """Slack ack after a delete attempt. Never claims a delete that did not happen."""
    target = (
        format_delete_target(confirmation.delete_candidates[0])
        if len(confirmation.delete_candidates) == 1 else "活動"
    )
    if result.outcome == CalendarWriteOutcome.SUCCESS:
        return f"已刪除：{target}\nDeleted the calendar event."
    if result.outcome == CalendarWriteOutcome.ALREADY_DELETED:
        return (
            f"呢個活動已經刪除咗或者已經唔存在，冇再刪除：{target}\n"
            "Already deleted or no longer in Google Calendar; nothing else was removed."
        )
    if result.outcome == CalendarWriteOutcome.REFUSED:
        return "未有刪除任何嘢。/ " + READ_ONLY_DISCLAIMER
    if is_google_auth_error(error_type=result.error_type, error_message=result.error_message):
        return (
            "刪除失敗（Google 登入已過期），活動未有刪除。/ "
            "Calendar delete failed (Google login expired); the event was not deleted."
        )
    return (
        "刪除失敗，未能確認活動有冇刪除。可以喺呢個 thread 再回 yes 重試。/ "
        "Calendar delete failed; could not verify whether the event was deleted. "
        "Reply yes in this thread to retry."
    )


def _series_ack(result: CalendarSeriesWriteResult) -> str:
    """Slack ack with honest created / already / failed counts (Phase 27)."""
    n = result.occurrence_count
    created, already, failed = result.created_count, result.already_count, result.failed_count
    if result.outcome == CalendarWriteOutcome.REFUSED:
        return "未有建立任何活動。/ " + READ_ONLY_DISCLAIMER
    if result.outcome == CalendarWriteOutcome.ALREADY_CREATED:
        return (
            f"全部 {n} 個活動之前已經建立，冇再新增。/ "
            f"Already added. All {n} events existed; no duplicates were created."
        )
    if result.outcome == CalendarWriteOutcome.SUCCESS:
        if already:
            return (
                f"已確認。新建立 {created} 個活動，之前已建立 {already} 個，冇重複。/ "
                f"Accepted. {created} created, {already} already existed."
            )
        return f"已確認。已建立 {created} 個活動。/ Accepted. {created} calendar events created."
    auth = is_google_auth_error(error_type=result.error_type, error_message=result.error_message)
    reason = "（Google 登入已過期）" if auth else ""
    return (
        f"已確認，但 {failed}/{n} 個活動未能建立{reason}（新建立 {created} 個，之前已有 {already} 個）。"
        "可以喺呢個 thread 再回 yes，只會重試未完成嘅日子。/ "
        f"Accepted, but {failed} of {n} events failed ({created} created, {already} already "
        "existed). Reply yes in this thread to retry only the missing dates."
    )


def _ignored(
    corr: str,
    reason: str,
    *,
    raw: dict[str, Any] | None = None,
    message: InboundMessage | None = None,
) -> ListenerResult:
    channel_id = None
    user_id = None
    event_id = None
    if message is not None:
        channel_id = message.channel_id
        user_id = message.user_id
        event_id = message.slack_event_id
    elif raw is not None:
        channel_id = raw.get("channel")
        user_id = raw.get("user")
        event_id = raw.get("client_msg_id") or raw.get("event_ts") or raw.get("ts")

    logger.info(
        "message_ignored",
        component=COMPONENT,
        correlation_id=corr,
        reason=reason,
        channel_id=channel_id,
        user_id=user_id,
        slack_event_id=event_id,
    )
    return ListenerResult(
        outcome=ListenerOutcome.IGNORED,
        correlation_id=corr,
        ignore_reason=reason,
    )


def _normalize_skip_reason(raw: dict[str, Any]) -> str:
    if raw.get("type") is not None and raw.get("type") != "message":
        return "malformed"
    if raw.get("bot_id") or raw.get("bot_profile") or raw.get("subtype") == "bot_message":
        return "bot_message"
    if raw.get("subtype"):
        return "malformed"
    if not isinstance(raw.get("channel"), str) or not raw.get("channel"):
        return "malformed"
    if not isinstance(raw.get("user"), str) or not raw.get("user"):
        return "malformed"
    if not isinstance(raw.get("text"), str):
        return "malformed"
    return "malformed"


def _reply_for_list_events(
    parse_result: ParseResult,
    *,
    calendar_client: CalendarClient | None,
    calendar_id: str | None,
    correlation_id: str,
) -> str:
    """Build a list reply. Never raises; never claims a calendar write."""
    try:
        if calendar_client is None:
            return format_reply(parse_result)
        if parse_result.start is None:
            return CALENDAR_READ_UNAVAILABLE
        listed = list_calendar_events(
            time_min=parse_result.start,
            time_max=parse_result.end or parse_result.start,
            client=calendar_client,
            calendar_id=calendar_id,
            correlation_id=correlation_id,
        )
        if _is_multi_day_window(parse_result):
            reply = format_recap(listed)
        else:
            reply = format_event_list(listed)
        if READ_ONLY_DISCLAIMER.lower() not in reply.lower():
            reply = f"{reply}\n{READ_ONLY_DISCLAIMER}"
        return reply
    except Exception:  # noqa: BLE001 — list path must still reply
        return CALENDAR_LIST_ERROR


def _reply_for_add_important_date(
    parse_result: ParseResult,
    *,
    store: ImportantDatesStore | None,
    now: datetime | None,
    correlation_id: str,
) -> str:
    """Persist an important date. Never claims a calendar write."""
    if store is None:
        return format_reply(parse_result)
    try:
        written = create_important_date(
            parse_result,
            store=store,
            now=now,
            correlation_id=correlation_id,
        )
        reply = format_add_ack(written)
        if READ_ONLY_DISCLAIMER.lower() not in reply.lower():
            reply = f"{reply}\n{READ_ONLY_DISCLAIMER}"
        return reply
    except Exception:  # noqa: BLE001 — still a user-visible line
        return (
            "Could not store the important date. "
            + READ_ONLY_DISCLAIMER
        )


def _reply_for_list_important_dates(
    *,
    store: ImportantDatesStore | None,
) -> str:
    """List stored important dates. Never claims a calendar write."""
    if store is None:
        return NOT_LISTED + " " + READ_ONLY_DISCLAIMER
    try:
        items = store.list_all()
        logger.info(
            "important_dates_listed",
            component="important_dates",
            outcome="success",
            date_count=len(items),
        )
        reply = format_important_dates_list(items)
        if READ_ONLY_DISCLAIMER.lower() not in reply.lower():
            reply = f"{reply}\n{READ_ONLY_DISCLAIMER}"
        return reply
    except Exception:  # noqa: BLE001 — still a user-visible line
        return "Could not list important dates. " + READ_ONLY_DISCLAIMER


def _write_ack(write_result: CalendarWriteResult) -> str:
    """Slack ack after an accept-path write. Never claims success on failure."""
    if write_result.outcome == CalendarWriteOutcome.SUCCESS:
        return CONFIRM_ACCEPTED_WRITTEN_ACK
    if write_result.outcome == CalendarWriteOutcome.ALREADY_CREATED:
        return CONFIRM_ALREADY_ADDED_ACK
    if is_google_auth_error(
        error_type=write_result.error_type,
        error_message=write_result.error_message,
    ):
        return CONFIRM_ACCEPTED_WRITE_AUTH_FAILED_ACK
    return CONFIRM_ACCEPTED_WRITE_FAILED_ACK


def _is_multi_day_window(parse_result: ParseResult) -> bool:
    """True when the list window is longer than one calendar day."""
    if parse_result.start is None or parse_result.end is None:
        return False
    return (parse_result.end - parse_result.start) > timedelta(days=1)


def _preview(message: str) -> str:
    text = message.replace("\n", " ")
    if len(text) <= PREVIEW_LEN:
        return text
    return text[: PREVIEW_LEN - 1] + "…"


def maintain_socket_connection(client: Any) -> str:
    """Observe Socket Mode health without competing with SDK auto-reconnect.

    Returns ``ok``, ``disconnected``, or ``failed``. Never raises.
    A false snapshot can occur during an SDK session swap. Forcing another
    endpoint here can tear down recovery and bypass the SDK's connection lock.
    Connected status is a transport observation, not proof of message delivery.
    """
    try:
        connected = bool(client.is_connected())
    except Exception as exc:  # noqa: BLE001 — health check must not crash
        logger.error(
            "socket_mode_status_failed",
            component=COMPONENT,
            outcome="failure",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return "failed"

    if connected:
        return "ok"

    logger.warning(
        "socket_mode_disconnected",
        component=COMPONENT,
        outcome="partial",
        recovery_owner="slack_sdk",
    )
    return "disconnected"


def run_socket_mode(config: SlackConfig | None = None) -> None:
    """Start Slack Socket Mode (live; requires real tokens). Not used by pytest."""
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    cfg = config if config is not None else load_slack_config()
    app = App(token=cfg.bot_token)
    life_notes_store: LifeNotesStore = JsonDirLifeNotesStore(life_notes_data_dir())
    confirmation_store: ConfirmationStore = JsonDirConfirmationStore(
        confirmation_data_dir()
    )
    maintain_confirmation_storage(store=confirmation_store)
    calendar_audit_store: CalendarAuditStore = JsonDirCalendarAuditStore(
        calendar_audit_data_dir()
    )
    maintain_calendar_audit_storage(store=calendar_audit_store)
    important_dates_store: ImportantDatesStore = JsonDirImportantDatesStore(
        important_dates_data_dir()
    )
    calendar_client: CalendarClient | None = None
    calendar_id: str | None = None
    try:
        google_cfg = load_google_calendar_config()
        calendar_client = GoogleCalendarClient(google_cfg)
        calendar_id = google_cfg.calendar_id
        probe_google_client(calendar_client)
    except CalendarWriterConfigError as exc:
        logger.warning(
            "calendar_client_unavailable",
            component=COMPONENT,
            outcome="skipped",
            error_type="CalendarWriterConfigError",
            error_message=str(exc),
        )

    miss_store = JsonDirParseMissStore(parse_miss_dir())
    maintain_parse_miss_storage(miss_store)
    llm_parser, llm_fallback = load_live_llm_parsers()
    if llm_parser is None:
        logger.info(
            "parse_fallback_unavailable",
            component=COMPONENT,
            outcome="skipped",
            reason="missing_xai_api_key",
        )

    logger.info(
        "event_parser_configured", component=COMPONENT,
        parse_mode="llm_first" if llm_parser is not None else "offline_rules",
    )

    @app.event("message")
    def _on_message(event: dict[str, Any], say: Any) -> None:
        result = process_slack_message_event(
            event,
            allowed_channel_ids=cfg.allowed_channel_ids,
            life_notes_channel_id=cfg.life_notes_channel_id,
            life_notes_store=life_notes_store,
            confirmation_store=confirmation_store,
            calendar_client=calendar_client,
            calendar_id=calendar_id,
            calendar_audit_store=calendar_audit_store,
            important_dates_store=important_dates_store,
            llm_parser=llm_parser,
            llm_fallback=llm_fallback,
            miss_store=miss_store,
        )
        if result.reply_text:
            thread_ts = event.get("thread_ts") or event.get("ts")
            say(text=result.reply_text, thread_ts=thread_ts)

    logger.info(
        "listener_starting",
        component=COMPONENT,
        outcome="success",
        mode="socket_mode",
        allowed_channels=len(cfg.allowed_channel_ids),
        life_notes_configured=bool(cfg.life_notes_channel_id),
    )
    handler = SocketModeHandler(
        app,
        cfg.app_token,
        auto_reconnect_enabled=True,
        ping_interval=SOCKET_PING_INTERVAL_S,
    )

    def _on_socket_error(error: Exception) -> None:
        logger.error(
            "socket_mode_error",
            component=COMPONENT,
            outcome="failure",
            error_type=type(error).__name__,
            error_message=str(error),
        )

    def _on_socket_close(code: int, reason: str | None = None) -> None:
        logger.warning(
            "socket_mode_closed",
            component=COMPONENT,
            outcome="partial",
            close_code=code,
            close_reason=reason,
        )

    handler.client.on_error_listeners.append(_on_socket_error)
    handler.client.on_close_listeners.append(_on_socket_close)
    handler.connect()
    logger.info(
        "socket_mode_connected",
        component=COMPONENT,
        outcome="success",
        ping_interval_s=SOCKET_PING_INTERVAL_S,
        health_interval_s=SOCKET_HEALTH_INTERVAL_S,
        recovery_owner="slack_sdk",
    )
    previous_status = "ok"
    try:
        while True:
            time.sleep(SOCKET_HEALTH_INTERVAL_S)
            status = maintain_socket_connection(handler.client)
            if status == "ok" and previous_status != "ok":
                logger.info(
                    "socket_mode_recovered",
                    component=COMPONENT,
                    outcome="success",
                    recovery_owner="slack_sdk",
                )
            previous_status = status
    except KeyboardInterrupt:
        logger.info(
            "listener_stopping",
            component=COMPONENT,
            outcome="success",
            reason="keyboard_interrupt",
        )
    finally:
        handler.close()


def main() -> None:
    """CLI entry: load ``.env`` if present, then run Socket Mode."""
    from dotenv import load_dotenv

    from cec_vivisystem.logging import setup_logging

    load_dotenv()
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    setup_logging(level=log_level)

    try:
        config = load_slack_config()
    except ConfigError as exc:
        logger.error(
            "listener_config_failed",
            component=COMPONENT,
            outcome="failure",
            error_type="ConfigError",
            error_message=str(exc),
        )
        raise SystemExit(1) from exc

    run_socket_mode(config)


if __name__ == "__main__":
    main()
