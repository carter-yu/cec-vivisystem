"""Phase 2 unit tests for the Slack Listener (offline, no real tokens)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem.calendar_writer import (
    FakeCalendarClient,
    InMemoryCalendarAuditStore,
)
from cec_vivisystem.confirmation import InMemoryConfirmationStore
from cec_vivisystem.life_notes import InMemoryLifeNotesStore
from cec_vivisystem.listener import (
    ConfigError,
    format_reply,
    handle_inbound,
    load_slack_config,
    maintain_socket_connection,
    normalize_slack_event,
    process_slack_message_event,
    should_accept,
)
from cec_vivisystem.models import (
    CalendarListedEvent,
    ConfirmationStatus,
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


def _dispatch_plans(
    raw: dict,
    *,
    confirmation_store: InMemoryConfirmationStore,
    life_notes_store: InMemoryLifeNotesStore | None = None,
    calendar_client: FakeCalendarClient | None = None,
    calendar_id: str | None = None,
    now: datetime | None = None,
    calendar_audit_store: InMemoryCalendarAuditStore | None = None,
) -> ListenerResult:
    return process_slack_message_event(
        raw,
        allowed_channel_ids={ALLOWED_CHANNEL},
        life_notes_channel_id=LIFE_NOTES_CHANNEL,
        life_notes_store=life_notes_store,
        confirmation_store=confirmation_store,
        calendar_client=calendar_client,
        calendar_id=calendar_id,
        now=now if now is not None else FIXED_NOW,
        calendar_audit_store=calendar_audit_store,
    )


def test_create_event_creates_pending_confirmation() -> None:
    """Y1: create_event in plans channel creates pending + proposal reply."""
    store = InMemoryConfirmationStore()
    result = _dispatch_plans(_user_message(F1), confirmation_store=store)
    pending = store.list_pending()
    assert len(pending) == 1
    conf = pending[0]
    assert conf.status == ConfirmationStatus.PENDING
    assert conf.channel_id == ALLOWED_CHANNEL
    assert conf.thread_ts == "1723123456.000100"
    assert result.outcome == ListenerOutcome.REPLIED
    assert result.parse_result is not None
    assert result.parse_result.intent_type == IntentType.CREATE_EVENT
    assert result.reply_text
    assert "confirm" in result.reply_text.lower() or "yes" in result.reply_text.lower()
    lower = result.reply_text.lower()
    assert "added to google" not in lower
    assert "successfully added" not in lower


def test_non_create_event_does_not_create_confirmation() -> None:
    """Y2: needs_clarification / unknown do not create pending."""
    store = InMemoryConfirmationStore()
    unclear = _dispatch_plans(
        _user_message("幫我 book 游泳"), confirmation_store=store
    )
    assert unclear.parse_result is not None
    assert unclear.parse_result.intent_type == IntentType.NEEDS_CLARIFICATION
    unknown = _dispatch_plans(_user_message("今日天氣點呀"), confirmation_store=store)
    assert unknown.parse_result is not None
    assert unknown.parse_result.intent_type == IntentType.UNKNOWN
    assert store.list_all() == []


def test_thread_yes_accepts_pending() -> None:
    """Y3: yes in the proposal thread accepts the pending confirmation."""
    store = InMemoryConfirmationStore()
    _dispatch_plans(_user_message(F1), confirmation_store=store)
    result = _dispatch_plans(
        _user_message("yes", thread_ts="1723123456.000100"),
        confirmation_store=store,
    )
    assert result.outcome == ListenerOutcome.REPLIED
    assert result.reply_text
    assert "accepted" in result.reply_text.lower()
    assert "writer" in result.reply_text.lower() or "no calendar write" in result.reply_text.lower()
    pending = store.list_pending()
    assert pending == []
    rows = store.list_all()
    assert len(rows) == 1
    assert rows[0].status == ConfirmationStatus.ACCEPTED
    assert rows[0].resolved_by == "U_PARENT"


def test_thread_reject_vocabulary_rejects_pending() -> None:
    """Y4: 不要 in thread rejects; no calendar-write claim."""
    store = InMemoryConfirmationStore()
    _dispatch_plans(_user_message(F1), confirmation_store=store)
    result = _dispatch_plans(
        _user_message("不要", thread_ts="1723123456.000100"),
        confirmation_store=store,
    )
    assert result.outcome == ListenerOutcome.REPLIED
    assert result.reply_text
    assert "reject" in result.reply_text.lower()
    assert "added to google" not in result.reply_text.lower()
    assert store.list_pending() == []
    assert store.list_all()[0].status == ConfirmationStatus.REJECTED


def test_life_notes_does_not_create_confirmation() -> None:
    """Y5: life-notes path stores a note and never creates a confirmation."""
    notes = InMemoryLifeNotesStore()
    confs = InMemoryConfirmationStore()
    result = _dispatch_plans(
        _user_message(LIFE_NOTE_TEXT, channel=LIFE_NOTES_CHANNEL),
        confirmation_store=confs,
        life_notes_store=notes,
    )
    assert result.parse_result is None
    assert notes.list_recent()
    assert confs.list_all() == []


def test_toplevel_yes_is_not_a_resolve() -> None:
    """Y6: top-level yes (no thread_ts) is parsed, not treated as accept."""
    store = InMemoryConfirmationStore()
    _dispatch_plans(_user_message(F1), confirmation_store=store)
    result = _dispatch_plans(_user_message("yes"), confirmation_store=store)
    assert result.outcome in (ListenerOutcome.REPLIED, ListenerOutcome.IGNORED)
    assert store.list_pending()
    assert all(c.status == ConfirmationStatus.PENDING for c in store.list_all())


def test_thread_yes_without_pending_is_ignored() -> None:
    """Y7: yes in a thread with no pending confirmation is ignored."""
    store = InMemoryConfirmationStore()
    result = _dispatch_plans(
        _user_message("yes", thread_ts="1723123456.000100"),
        confirmation_store=store,
    )
    assert result.outcome == ListenerOutcome.IGNORED
    assert result.ignore_reason == "no_pending_confirmation"
    assert store.list_all() == []


def test_wrong_channel_does_not_create_confirmation() -> None:
    """Y8: wrong channel does not create a confirmation."""
    store = InMemoryConfirmationStore()
    result = _dispatch_plans(
        _user_message(F1, channel=OTHER_CHANNEL), confirmation_store=store
    )
    assert result.outcome == ListenerOutcome.IGNORED
    assert result.ignore_reason == "wrong_channel"
    assert store.list_all() == []


class _FakeSocketClient:
    def __init__(
        self,
        *,
        connected: bool = True,
        fail_connect: BaseException | None = None,
        fail_status: BaseException | None = None,
    ) -> None:
        self._connected = connected
        self.fail_connect = fail_connect
        self.fail_status = fail_status
        self.connect_calls: list[bool] = []

    def is_connected(self) -> bool:
        if self.fail_status is not None:
            raise self.fail_status
        return self._connected

    def connect_to_new_endpoint(self, force: bool = False) -> None:
        self.connect_calls.append(force)
        if self.fail_connect is not None:
            raise self.fail_connect
        self._connected = True


def test_maintain_socket_connection_ok_when_connected() -> None:
    """Connected websocket → no reconnect."""
    client = _FakeSocketClient(connected=True)
    assert maintain_socket_connection(client) == "ok"
    assert client.connect_calls == []


def test_maintain_socket_connection_reconnects_when_disconnected() -> None:
    """Dead websocket → force new endpoint."""
    client = _FakeSocketClient(connected=False)
    assert maintain_socket_connection(client) == "reconnected"
    assert client.connect_calls == [True]
    assert client.is_connected() is True


def test_maintain_socket_connection_failed_connect_does_not_crash() -> None:
    """Reconnect error is a failed result, not a process crash."""
    client = _FakeSocketClient(connected=False, fail_connect=RuntimeError("wss down"))
    assert maintain_socket_connection(client) == "failed"
    assert client.connect_calls == [True]


def test_maintain_socket_connection_status_error_tries_reconnect() -> None:
    """is_connected() raising still attempts a force reconnect."""
    client = _FakeSocketClient(fail_status=RuntimeError("status boom"))
    assert maintain_socket_connection(client) == "reconnected"
    assert client.connect_calls == [True]


def test_list_events_replies_without_confirmation() -> None:
    """L-list: list intent + fake client replies a list; no confirmation."""
    store = InMemoryConfirmationStore()
    start = datetime(2026, 9, 1, 9, 0, tzinfo=FAMILY_TZ)
    client = FakeCalendarClient(
        listed_events=[
            CalendarListedEvent(
                event_id="e1",
                summary="游泳",
                start=start,
                end=datetime(2026, 9, 1, 10, 0, tzinfo=FAMILY_TZ),
                all_day=False,
            )
        ]
    )
    result = _dispatch_plans(
        _user_message("tell me the events on 1 Sept 2026"),
        confirmation_store=store,
        calendar_client=client,
        calendar_id="cal-test",
    )
    assert result.outcome == ListenerOutcome.REPLIED
    assert result.parse_result is not None
    assert result.parse_result.intent_type == IntentType.LIST_EVENTS
    assert result.reply_text
    assert "游泳" in result.reply_text
    assert store.list_all() == []
    assert client.calls == []
    assert client.list_calls


PHASE12_NOW = datetime(2026, 9, 5, 12, 0, tzinfo=FAMILY_TZ)
P0_LIST = "聽日有乜嘢活動"


def test_list_events_ting_yat_ye_activity_always_replies() -> None:
    """L1: listener P0 + fake client → Slack reply; no confirmation."""
    store = InMemoryConfirmationStore()
    tomorrow_start = datetime(2026, 9, 6, 9, 0, tzinfo=FAMILY_TZ)
    client = FakeCalendarClient(
        listed_events=[
            CalendarListedEvent(
                event_id="e1",
                summary="游泳",
                start=tomorrow_start,
                end=datetime(2026, 9, 6, 10, 0, tzinfo=FAMILY_TZ),
                all_day=False,
            )
        ]
    )
    result = _dispatch_plans(
        _user_message(P0_LIST),
        confirmation_store=store,
        calendar_client=client,
        calendar_id="cal-test",
        now=PHASE12_NOW,
    )
    assert result.outcome == ListenerOutcome.REPLIED
    assert result.parse_result is not None
    assert result.parse_result.intent_type == IntentType.LIST_EVENTS
    assert result.reply_text
    assert "游泳" in result.reply_text
    assert "no calendar change" in result.reply_text.lower()
    assert "please confirm" not in result.reply_text.lower()
    assert store.list_all() == []
    assert client.calls == []
    assert client.list_calls
    cal_id, time_min, time_max = client.list_calls[0]
    assert cal_id == "cal-test"
    assert time_min == datetime(2026, 9, 6, 0, 0, tzinfo=FAMILY_TZ)
    assert time_max == datetime(2026, 9, 7, 0, 0, tzinfo=FAMILY_TZ)


def test_list_events_google_error_still_replies() -> None:
    """L2: listener list + Google error → reply with error; no write."""
    store = InMemoryConfirmationStore()
    client = FakeCalendarClient(fail_list_with=RuntimeError("Google 403"))
    result = _dispatch_plans(
        _user_message(P0_LIST),
        confirmation_store=store,
        calendar_client=client,
        calendar_id="cal-test",
        now=PHASE12_NOW,
    )
    assert result.outcome == ListenerOutcome.REPLIED
    assert result.parse_result is not None
    assert result.parse_result.intent_type == IntentType.LIST_EVENTS
    assert result.reply_text
    assert "could not read" in result.reply_text.lower()
    assert "no calendar change" in result.reply_text.lower()
    assert store.list_all() == []
    assert client.calls == []
    assert client.list_calls


PHASE14_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=FAMILY_TZ)
P1_TODAY = "今日有乜？"
P2_WEEK = "今個星期有乜"


def test_list_events_today_replies_without_confirmation() -> None:
    """L3: 今日有乜？ + fake client → today's events; no confirmation."""
    store = InMemoryConfirmationStore()
    client = FakeCalendarClient(
        listed_events=[
            CalendarListedEvent(
                event_id="e1",
                summary="游泳",
                start=datetime(2026, 9, 8, 9, 0, tzinfo=FAMILY_TZ),
                end=datetime(2026, 9, 8, 10, 0, tzinfo=FAMILY_TZ),
                all_day=False,
            )
        ]
    )
    result = _dispatch_plans(
        _user_message(P1_TODAY),
        confirmation_store=store,
        calendar_client=client,
        calendar_id="cal-test",
        now=PHASE14_NOW,
    )
    assert result.outcome == ListenerOutcome.REPLIED
    assert result.parse_result is not None
    assert result.parse_result.intent_type == IntentType.LIST_EVENTS
    assert result.parse_result.start == datetime(2026, 9, 8, 0, 0, tzinfo=FAMILY_TZ)
    assert result.parse_result.end == datetime(2026, 9, 9, 0, 0, tzinfo=FAMILY_TZ)
    assert result.reply_text
    assert "游泳" in result.reply_text
    assert "no calendar change" in result.reply_text.lower()
    assert "please confirm" not in result.reply_text.lower()
    assert store.list_all() == []
    assert client.calls == []
    assert client.list_calls
    cal_id, time_min, time_max = client.list_calls[0]
    assert cal_id == "cal-test"
    assert time_min == datetime(2026, 9, 8, 0, 0, tzinfo=FAMILY_TZ)
    assert time_max == datetime(2026, 9, 9, 0, 0, tzinfo=FAMILY_TZ)


def test_list_events_this_week_recap_without_confirmation() -> None:
    """L4: 今個星期有乜 + two days → day-grouped recap; no confirmation."""
    store = InMemoryConfirmationStore()
    client = FakeCalendarClient(
        listed_events=[
            CalendarListedEvent(
                event_id="e1",
                summary="游泳",
                start=datetime(2026, 9, 7, 9, 0, tzinfo=FAMILY_TZ),
                end=datetime(2026, 9, 7, 10, 0, tzinfo=FAMILY_TZ),
                all_day=False,
            ),
            CalendarListedEvent(
                event_id="e2",
                summary="牙醫",
                start=datetime(2026, 9, 8, 15, 0, tzinfo=FAMILY_TZ),
                end=datetime(2026, 9, 8, 16, 0, tzinfo=FAMILY_TZ),
                all_day=False,
            ),
        ]
    )
    result = _dispatch_plans(
        _user_message(P2_WEEK),
        confirmation_store=store,
        calendar_client=client,
        calendar_id="cal-test",
        now=PHASE14_NOW,
    )
    assert result.outcome == ListenerOutcome.REPLIED
    assert result.parse_result is not None
    assert result.parse_result.intent_type == IntentType.LIST_EVENTS
    assert result.reply_text
    assert "2026-09-07" in result.reply_text
    assert "2026-09-08" in result.reply_text
    assert "游泳" in result.reply_text
    assert "牙醫" in result.reply_text
    assert "no calendar change" in result.reply_text.lower()
    assert "please confirm" not in result.reply_text.lower()
    assert store.list_all() == []
    assert client.calls == []
    cal_id, time_min, time_max = client.list_calls[0]
    assert cal_id == "cal-test"
    assert time_min == datetime(2026, 9, 7, 0, 0, tzinfo=FAMILY_TZ)
    assert time_max == datetime(2026, 9, 14, 0, 0, tzinfo=FAMILY_TZ)


def test_help_replies_allowed_inputs_without_confirmation() -> None:
    """help / 指令 in plans channel → allowed-input list; no write."""
    store = InMemoryConfirmationStore()
    client = FakeCalendarClient()
    result = _dispatch_plans(
        _user_message("指令"),
        confirmation_store=store,
        calendar_client=client,
    )
    assert result.outcome == ListenerOutcome.REPLIED
    assert result.parse_result is not None
    assert result.parse_result.intent_type == IntentType.HELP
    assert result.reply_text
    assert "聽日有乜" in result.reply_text
    assert "help" in result.reply_text.lower()
    assert "no calendar change" in result.reply_text.lower()
    assert "please confirm" not in result.reply_text.lower()
    assert store.list_all() == []
    assert client.calls == []
    assert client.list_calls == []


def test_thread_yes_with_calendar_client_writes_once() -> None:
    """W1: first accept creates; second yes already added; one Google create."""
    store = InMemoryConfirmationStore()
    audit = InMemoryCalendarAuditStore()
    client = FakeCalendarClient()
    _dispatch_plans(_user_message(F1), confirmation_store=store, calendar_client=client)
    assert client.calls == []
    result = _dispatch_plans(
        _user_message("yes", thread_ts="1723123456.000100"),
        confirmation_store=store,
        calendar_client=client,
        calendar_id="cal-test",
        calendar_audit_store=audit,
    )
    assert result.outcome == ListenerOutcome.REPLIED
    assert result.reply_text
    assert "accepted" in result.reply_text.lower()
    assert "created" in result.reply_text.lower()
    assert len(client.calls) == 1
    assert client.calls[0].confirmation_id == store.list_all()[0].confirmation_id

    second = _dispatch_plans(
        _user_message("yes", thread_ts="1723123456.000100"),
        confirmation_store=store,
        calendar_client=client,
        calendar_id="cal-test",
        calendar_audit_store=audit,
    )
    assert second.outcome == ListenerOutcome.REPLIED
    assert second.reply_text
    assert "already added" in second.reply_text.lower()
    assert len(client.calls) == 1


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


def _overlapping_dentist() -> CalendarListedEvent:
    return CalendarListedEvent(
        event_id="e-dentist",
        summary="牙醫",
        start=datetime(2026, 8, 8, 15, 30, tzinfo=FAMILY_TZ),
        end=datetime(2026, 8, 8, 16, 30, tzinfo=FAMILY_TZ),
        all_day=False,
        participants=["Cedric"],
    )


def test_create_proposal_includes_overlap_warning() -> None:
    """L-overlap: create + overlapping listed event warns; no write yet."""
    store = InMemoryConfirmationStore()
    client = FakeCalendarClient(listed_events=[_overlapping_dentist()])
    result = _dispatch_plans(
        _user_message(F1),
        confirmation_store=store,
        calendar_client=client,
        calendar_id="cal-test",
    )
    assert result.outcome == ListenerOutcome.REPLIED
    assert result.reply_text
    assert "Warning:" in result.reply_text
    assert "牙醫" in result.reply_text
    assert "Cedric" in result.reply_text
    assert store.list_pending()
    assert client.calls == []
    assert client.list_calls


def test_overlap_check_failure_still_creates_pending() -> None:
    """List error warns but still creates a pending confirmation (no write)."""
    store = InMemoryConfirmationStore()
    client = FakeCalendarClient(fail_list_with=RuntimeError("Google 403"))
    result = _dispatch_plans(
        _user_message(F1),
        confirmation_store=store,
        calendar_client=client,
        calendar_id="cal-test",
    )
    assert result.outcome == ListenerOutcome.REPLIED
    assert result.reply_text
    assert "Warning:" in result.reply_text
    assert "could not check" in result.reply_text.lower()
    assert store.list_pending()
    assert client.calls == []


def test_overlap_warning_still_writes_on_yes() -> None:
    """L-yes: overlap warning does not block first-accept write."""
    store = InMemoryConfirmationStore()
    client = FakeCalendarClient(listed_events=[_overlapping_dentist()])
    first = _dispatch_plans(
        _user_message(F1),
        confirmation_store=store,
        calendar_client=client,
        calendar_id="cal-test",
    )
    assert first.reply_text
    assert "Warning:" in first.reply_text
    assert client.calls == []
    result = _dispatch_plans(
        _user_message("yes", thread_ts="1723123456.000100"),
        confirmation_store=store,
        calendar_client=client,
        calendar_id="cal-test",
    )
    assert result.outcome == ListenerOutcome.REPLIED
    assert result.reply_text
    assert "accepted" in result.reply_text.lower()
    assert "created" in result.reply_text.lower()
    assert len(client.calls) == 1
