"""Slack Listener — thin intake slice (Phase 2 + 5B + 4b + 6).

Receives family messages from named Slack channels:

- ``#family-plans`` → parse; ``create_event`` creates a pending confirmation
  (Phase 4b) and thread yes/no resolves it. On first accept, an injectable
  Calendar client (Phase 6) may create one Google Calendar event.
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
from datetime import datetime
from typing import Any

from cec_vivisystem.calendar_writer import (
    CalendarAuditStore,
    CalendarClient,
    CalendarWriterConfigError,
    GoogleCalendarClient,
    JsonDirCalendarAuditStore,
    load_google_calendar_config,
    maintain_calendar_audit_storage,
    write_calendar_create,
)
from cec_vivisystem.calendar_writer import (
    default_audit_dir as calendar_audit_data_dir,
)
from cec_vivisystem.confirmation import (
    ConfirmationStore,
    JsonDirConfirmationStore,
    classify_confirmation_reply,
    create_confirmation,
    expire_due_confirmations,
    find_pending_for_thread,
    maintain_confirmation_storage,
    resolve_confirmation,
)
from cec_vivisystem.confirmation import (
    default_data_dir as confirmation_data_dir,
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
    CalendarWriteOutcome,
    ConfirmationDecision,
    ConfirmationStatus,
    InboundMessage,
    IntentType,
    ListenerOutcome,
    ListenerResult,
    ParseResult,
)
from cec_vivisystem.parser import parse as default_parse

logger = get_logger(__name__)

COMPONENT = "listener"
PREVIEW_LEN = 80
LIFE_NOTE_ACK = "已記低"
CONFIRM_ACCEPTED_ACK = "Accepted. No calendar write yet (Writer is a later phase)."
CONFIRM_ACCEPTED_WRITTEN_ACK = "Accepted. Calendar event created."
CONFIRM_ACCEPTED_WRITE_FAILED_ACK = (
    "Accepted. Calendar write failed; no event was created."
)
CONFIRM_REJECTED_ACK = "Rejected. No calendar change was made."
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

    if result.intent_type == IntentType.NEEDS_CLARIFICATION:
        missing = ", ".join(result.missing_fields) if result.missing_fields else "details"
        return (
            f"Need more detail before this can be a calendar create "
            f"(missing: {missing}). {disclaimer}"
        )

    return (
        "Could not treat that as a calendar create request. " + disclaimer
    )


def handle_inbound(
    message: InboundMessage,
    *,
    parse: ParseFn = default_parse,
    now: datetime | None = None,
    correlation_id: str | None = None,
    confirmation_store: ConfirmationStore | None = None,
) -> ListenerResult:
    """Parse an accepted inbound message and build a reply (no Slack I/O)."""
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
    )

    try:
        parse_result = parse(message.text, now=now, correlation_id=corr)
        next_component = "parser"
        if (
            confirmation_store is not None
            and parse_result.intent_type == IntentType.CREATE_EVENT
        ):
            confirmation = create_confirmation(
                parse_result,
                store=confirmation_store,
                now=now,
                correlation_id=corr,
                channel_id=message.channel_id,
                thread_ts=message.thread_ts or message.ts,
            )
            reply = confirmation.proposal_text
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
        return ListenerResult(
            outcome=ListenerOutcome.FAILED,
            correlation_id=corr,
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
            write_result = write_calendar_create(
                updated,
                client=calendar_client,
                calendar_id=calendar_id,
                audit_store=calendar_audit_store,
                now=now,
            )
            next_component = "calendar_writer"
            if write_result.outcome == CalendarWriteOutcome.SUCCESS:
                ack = CONFIRM_ACCEPTED_WRITTEN_ACK
            else:
                ack = CONFIRM_ACCEPTED_WRITE_FAILED_ACK
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
) -> ListenerResult:
    """Full offline pipeline: normalize → filter → dispatch.

    Plans channel → parse (and optional confirmation). Life-notes → keeper.
    Safe for any garbage input; does not perform Slack HTTP.
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

    return handle_inbound(
        message,
        parse=parse,
        now=now,
        correlation_id=corr,
        confirmation_store=confirmation_store if is_plans else None,
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
    text = raw.get("text")
    if isinstance(text, str) and not text.strip():
        # empty text still has keys — treat after normalize; if user/channel missing:
        pass
    if not isinstance(raw.get("channel"), str) or not raw.get("channel"):
        return "malformed"
    if not isinstance(raw.get("user"), str) or not raw.get("user"):
        return "malformed"
    if not isinstance(raw.get("text"), str):
        return "malformed"
    return "malformed"


def _preview(message: str) -> str:
    text = message.replace("\n", " ")
    if len(text) <= PREVIEW_LEN:
        return text
    return text[: PREVIEW_LEN - 1] + "…"


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
    calendar_client: CalendarClient | None = None
    calendar_id: str | None = None
    try:
        google_cfg = load_google_calendar_config()
        calendar_client = GoogleCalendarClient(google_cfg)
        calendar_id = google_cfg.calendar_id
    except CalendarWriterConfigError as exc:
        logger.warning(
            "calendar_client_unavailable",
            component=COMPONENT,
            outcome="skipped",
            error_type="CalendarWriterConfigError",
            error_message=str(exc),
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
        )
        if result.outcome == ListenerOutcome.REPLIED and result.reply_text:
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
    handler = SocketModeHandler(app, cfg.app_token)
    handler.start()


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
