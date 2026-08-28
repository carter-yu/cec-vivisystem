"""Phase 2 unit tests for the Slack Listener (offline, no real tokens)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem.life_notes import InMemoryLifeNotesStore
from cec_vivisystem.listener import (
    ConfigError,
    format_reply,
    handle_inbound,
    load_slack_config,
    normalize_slack_event,
    process_slack_message_event,
    should_accept,
)
from cec_vivisystem.models import (
    InboundMessage,
    IntentType,
    LifeNoteStatus,
    ListenerOutcome,
    ListenerResult,
)
from cec_vivisystem.parser import parse

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
FIXED_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=FAMILY_TZ)

ALLOWED_CHANNEL = "C_FAMILY"
OTHER_CHANNEL = "C_OTHER"
LIFE_NOTES_CHANNEL = "C_LIFE_NOTES"

# Phase 1 F1 — create_event under FIXED_NOW
F1 = "星期六下午3點帶 Cedric 去游泳"
# Phase 5A/5B — life note (not a calendar phrase)
LIFE_NOTE_TEXT = "梓梵今日喺學校同朋友一齊砌積木，好開心。"


def _dispatch_with_life_notes(
    raw: dict,
    *,
    store: InMemoryLifeNotesStore | None = None,
) -> tuple:
    store = store if store is not None else InMemoryLifeNotesStore()
    result = process_slack_message_event(
        raw,
        allowed_channel_ids={ALLOWED_CHANNEL},
        life_notes_channel_id=LIFE_NOTES_CHANNEL,
        life_notes_store=store,
        now=FIXED_NOW,
    )
    return result, store


def _user_message(
    text: str,
    *,
    channel: str = ALLOWED_CHANNEL,
    user: str = "U_PARENT",
    **extra: object,
) -> dict:
    event: dict = {
        "type": "message",
        "channel": channel,
        "user": user,
        "text": text,
        "ts": "1723123456.000100",
        "client_msg_id": "msg-test-001",
    }
    event.update(extra)
    return event


def test_handle_inbound_create_event_replies() -> None:
    """L1: allowlisted create-event message → replied with parse summary."""
    raw = _user_message(F1)
    result = process_slack_message_event(
        raw,
        allowed_channel_ids={ALLOWED_CHANNEL},
        now=FIXED_NOW,
    )
    assert result.outcome == ListenerOutcome.REPLIED
    assert result.correlation_id
    assert result.parse_result is not None
    assert result.parse_result.intent_type == IntentType.CREATE_EVENT
    assert result.reply_text
    assert "游泳" in result.reply_text or "15:00" in result.reply_text or "Title" in result.reply_text
    lower = result.reply_text.lower()
    assert "written to calendar" in lower or "no calendar change" in lower
    assert "successfully added" not in lower
    assert "created on google" not in lower


def test_ignore_wrong_channel() -> None:
    """L2: valid message in non-allowlisted channel → ignored."""
    raw = _user_message(F1, channel=OTHER_CHANNEL)
    result = process_slack_message_event(
        raw,
        allowed_channel_ids={ALLOWED_CHANNEL},
        now=FIXED_NOW,
    )
    assert result.outcome == ListenerOutcome.IGNORED
    assert result.ignore_reason == "wrong_channel"
    assert result.parse_result is None
    assert result.reply_text is None


def test_ignore_bot_message() -> None:
    """L3: bot messages are ignored."""
    raw = _user_message(F1, bot_id="B_BOT", subtype="bot_message")
    # subtype alone is enough; also bot_id
    result = process_slack_message_event(
        raw,
        allowed_channel_ids={ALLOWED_CHANNEL},
        now=FIXED_NOW,
    )
    assert result.outcome == ListenerOutcome.IGNORED
    assert result.ignore_reason == "bot_message"


def test_ignore_or_handle_empty_text() -> None:
    """L4: empty / whitespace text does not crash."""
    raw = _user_message("   ")
    result = process_slack_message_event(
        raw,
        allowed_channel_ids={ALLOWED_CHANNEL},
        now=FIXED_NOW,
    )
    assert result.outcome == ListenerOutcome.IGNORED
    assert result.ignore_reason == "empty_text"


def test_malformed_payload_does_not_raise() -> None:
    """L5: garbage payloads return controlled results."""
    for payload in (
        None,
        "not-a-dict",
        {},
        {"type": "message"},
        {"type": "message", "channel": ALLOWED_CHANNEL},
        {"type": "app_mention", "text": "hi", "channel": ALLOWED_CHANNEL, "user": "U1"},
        {"type": "message", "channel": 123, "user": "U1", "text": "hi"},
    ):
        result = process_slack_message_event(
            payload,
            allowed_channel_ids={ALLOWED_CHANNEL},
            now=FIXED_NOW,
        )
        assert isinstance(result, ListenerResult)
        assert result.outcome in (
            ListenerOutcome.IGNORED,
            ListenerOutcome.FAILED,
        )


def test_listener_result_contract_fields() -> None:
    """L1 contract: outcome, correlation_id, parse_result, reply_text."""
    result = process_slack_message_event(
        _user_message(F1),
        allowed_channel_ids={ALLOWED_CHANNEL},
        now=FIXED_NOW,
    )
    assert hasattr(result, "outcome")
    assert hasattr(result, "correlation_id")
    assert hasattr(result, "ignore_reason")
    assert hasattr(result, "parse_result")
    assert hasattr(result, "reply_text")
    assert result.outcome == ListenerOutcome.REPLIED
    assert result.correlation_id
    assert result.parse_result is not None
    assert result.reply_text


def test_missing_credentials_or_auth_failure_is_explicit() -> None:
    """L6: missing env is ConfigError naming keys only (no secret values)."""
    with pytest.raises(ConfigError) as exc_info:
        load_slack_config(env={})
    msg = str(exc_info.value)
    assert "SLACK_BOT_TOKEN" in msg
    assert "SLACK_APP_TOKEN" in msg
    assert "SLACK_FAMILY_PLANS_CHANNEL_ID" in msg
    assert "SLACK_LIFE_NOTES_CHANNEL_ID" in msg
    assert "xoxb-" not in msg
    assert "xapp-" not in msg

    with pytest.raises(ConfigError):
        load_slack_config(
            env={
                "SLACK_BOT_TOKEN": "  ",
                "SLACK_APP_TOKEN": "xapp-test",
                "SLACK_FAMILY_PLANS_CHANNEL_ID": ALLOWED_CHANNEL,
                "SLACK_LIFE_NOTES_CHANNEL_ID": LIFE_NOTES_CHANNEL,
            }
        )

    with pytest.raises(ConfigError) as missing_life_notes:
        load_slack_config(
            env={
                "SLACK_BOT_TOKEN": "xoxb-test-token",
                "SLACK_APP_TOKEN": "xapp-test-token",
                "SLACK_FAMILY_PLANS_CHANNEL_ID": ALLOWED_CHANNEL,
            }
        )
    assert "SLACK_LIFE_NOTES_CHANNEL_ID" in str(missing_life_notes.value)

    cfg = load_slack_config(
        env={
            "SLACK_BOT_TOKEN": "xoxb-test-token",
            "SLACK_APP_TOKEN": "xapp-test-token",
            "SLACK_FAMILY_PLANS_CHANNEL_ID": ALLOWED_CHANNEL,
            "SLACK_LIFE_NOTES_CHANNEL_ID": LIFE_NOTES_CHANNEL,
        }
    )
    assert cfg.allowed_channel_ids == frozenset({ALLOWED_CHANNEL})
    assert cfg.life_notes_channel_id == LIFE_NOTES_CHANNEL
    assert cfg.bot_token == "xoxb-test-token"


def test_listener_logs_boundary() -> None:
    """L7: accepted message completes with logging configured (autouse fixture)."""
    result = process_slack_message_event(
        _user_message(F1),
        allowed_channel_ids={ALLOWED_CHANNEL},
        now=FIXED_NOW,
    )
    assert result.outcome == ListenerOutcome.REPLIED


def test_normalize_user_message_extracts_text_channel_user() -> None:
    """D: normalize fills inbound contract fields."""
    raw = _user_message(F1, channel=ALLOWED_CHANNEL, user="U_PARENT")
    msg = normalize_slack_event(raw)
    assert msg is not None
    assert msg.text == F1
    assert msg.channel_id == ALLOWED_CHANNEL
    assert msg.user_id == "U_PARENT"
    assert msg.slack_event_id == "msg-test-001"
    assert msg.ts == "1723123456.000100"
    assert should_accept(msg, allowed_channel_ids={ALLOWED_CHANNEL})
    assert not should_accept(msg, allowed_channel_ids={OTHER_CHANNEL})


def test_format_reply_never_claims_calendar_write() -> None:
    """Reply copy stays on the non-write side of the calendar gate."""
    parsed = parse(F1, now=FIXED_NOW)
    text = format_reply(parsed)
    assert "No calendar change was made" in text
    assert "added to Google" not in text


def test_handle_inbound_with_inbound_model() -> None:
    """Direct handle_inbound path (public API)."""
    msg = InboundMessage(
        text=F1,
        channel_id=ALLOWED_CHANNEL,
        user_id="U_PARENT",
        correlation_id="corr-test",
    )
    result = handle_inbound(msg, now=FIXED_NOW)
    assert result.outcome == ListenerOutcome.REPLIED
    assert result.correlation_id == "corr-test"
    assert result.parse_result is not None


def test_life_notes_channel_stores_raw_text() -> None:
    """S1: message in life-notes channel is stored exactly; parser is not used."""
    result, store = _dispatch_with_life_notes(
        _user_message(LIFE_NOTE_TEXT, channel=LIFE_NOTES_CHANNEL)
    )
    notes = store.list_recent()
    assert len(notes) == 1
    assert notes[0].raw_text == LIFE_NOTE_TEXT
    assert notes[0].status == LifeNoteStatus.RAW
    assert result.parse_result is None
    assert result.outcome == ListenerOutcome.REPLIED
    assert result.reply_text == "已記低"
    assert result.correlation_id


def test_family_plans_does_not_create_life_note() -> None:
    """S2: #family-plans stays on parse-reply; no life note created."""
    result, store = _dispatch_with_life_notes(_user_message(F1, channel=ALLOWED_CHANNEL))
    assert result.outcome == ListenerOutcome.REPLIED
    assert result.parse_result is not None
    assert result.parse_result.intent_type == IntentType.CREATE_EVENT
    assert store.list_recent() == []


def test_wrong_channel_bot_subtype_do_not_create_life_note() -> None:
    """S3: wrong channel / bot / subtype ignored; no note stored."""
    store = InMemoryLifeNotesStore()
    cases = (
        _user_message(LIFE_NOTE_TEXT, channel=OTHER_CHANNEL),
        _user_message(
            LIFE_NOTE_TEXT,
            channel=LIFE_NOTES_CHANNEL,
            bot_id="B_BOT",
            subtype="bot_message",
        ),
        _user_message(
            LIFE_NOTE_TEXT,
            channel=LIFE_NOTES_CHANNEL,
            subtype="message_changed",
        ),
    )
    for raw in cases:
        result, store = _dispatch_with_life_notes(raw, store=store)
        assert result.outcome == ListenerOutcome.IGNORED
        assert result.ignore_reason in {"wrong_channel", "bot_message", "malformed"}
    assert store.list_recent() == []


def test_empty_life_notes_text_does_not_store() -> None:
    """S4: empty/whitespace in life-notes channel is controlled; no empty note."""
    store = InMemoryLifeNotesStore()
    for text in ("", "   ", "\n\t"):
        result, store = _dispatch_with_life_notes(
            _user_message(text, channel=LIFE_NOTES_CHANNEL),
            store=store,
        )
        assert result.outcome in (ListenerOutcome.IGNORED, ListenerOutcome.FAILED)
        if result.outcome == ListenerOutcome.IGNORED:
            assert result.ignore_reason == "empty_text"
    assert store.list_recent() == []


def test_life_note_source_metadata_from_slack() -> None:
    """S5: source.channel, message_id/ts, and user come from the Slack event."""
    result, store = _dispatch_with_life_notes(
        _user_message(LIFE_NOTE_TEXT, channel=LIFE_NOTES_CHANNEL, user="U_PARENT")
    )
    notes = store.list_recent()
    assert len(notes) == 1
    note = notes[0]
    assert note.source.channel == LIFE_NOTES_CHANNEL
    assert note.source.message_id == "msg-test-001"
    assert note.source.user == "U_PARENT"
    assert result.parse_result is None
