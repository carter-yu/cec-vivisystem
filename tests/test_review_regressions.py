"""Phase 21 regressions: fake service responses, fixed clocks, temporary stores."""

from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem.calendar_writer import (
    FakeCalendarClient,
    GoogleCalendarClient,
    GoogleCalendarConfig,
    InMemoryCalendarAuditStore,
    write_calendar_create,
)
from cec_vivisystem.confirmation import (
    ConfirmationError,
    InMemoryConfirmationStore,
    build_proposal,
    create_confirmation,
    resolve_confirmation,
)
from cec_vivisystem.life_notes import InMemoryLifeNotesStore
from cec_vivisystem.listener import process_slack_message_event
from cec_vivisystem.models import (
    CalendarWriteOutcome,
    ConfirmationStatus,
    IntentType,
    ListenerOutcome,
)
from cec_vivisystem.parse_fallback import (
    FakeLlmParser,
    llm_payload_to_parse_result,
    parse_with_fallback,
)
from cec_vivisystem.parser import parse

TZ = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 9, 19, 8, tzinfo=TZ)
PHRASE = "聽日9點去公園"


def accepted():
    store = InMemoryConfirmationStore()
    conf = create_confirmation(parse(PHRASE, now=NOW), store=store, now=NOW)
    return resolve_confirmation(conf.confirmation_id, "accept", store=store, now=NOW)


class Request:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class Conflict(Exception):
    resp = SimpleNamespace(status=409)


class Service:
    def __init__(self, *, lose_response=False, pages=None):
        self.rows = {}
        self.bodies = []
        self.lose_response = lose_response
        self.pages = pages or [{}]
        self.list_calls = []

    def events(self):
        return self

    def insert(self, *, calendarId, body):
        def run():
            self.bodies.append(body)
            key = body.get("id", f"generated{len(self.bodies)}")
            if key in self.rows:
                raise Conflict()
            self.rows[key] = {**body, "id": key}
            if self.lose_response:
                self.lose_response = False
                raise BrokenPipeError("response lost after commit")
            return self.rows[key]

        return Request(run)

    def get(self, *, calendarId, eventId):
        return Request(lambda: self.rows[eventId])

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        page = self.pages[int(kwargs.get("pageToken", "0"))]

        def run():
            if isinstance(page, Exception):
                raise page
            return page

        return Request(run)


def google(monkeypatch, service):
    client = GoogleCalendarClient(GoogleCalendarConfig("fake", "fake", "fake", "test"))
    monkeypatch.setattr(client, "_get_service", lambda: service)
    return client


def test_insert_lost_response_and_missing_audit_do_not_duplicate(monkeypatch):
    service = Service(lose_response=True)
    client = google(monkeypatch, service)
    conf = accepted()
    first = write_calendar_create(conf, client=client, now=NOW)
    second = write_calendar_create(conf, client=client, now=NOW)
    assert first.outcome == second.outcome == CalendarWriteOutcome.SUCCESS
    assert first.calendar_event_id == second.calendar_event_id
    assert len(service.rows) == 1
    assert all(body.get("id") == first.calendar_event_id for body in service.bodies)


@pytest.mark.parametrize("change", ["owner", "cancelled"])
def test_conflict_requires_existing_matching_live_event(monkeypatch, change):
    service = Service()
    client = google(monkeypatch, service)
    conf = accepted()
    first = write_calendar_create(conf, client=client, now=NOW)
    row = service.rows[first.calendar_event_id]
    if change == "owner":
        row["extendedProperties"] = {"private": {"confirmation_id": "someone-else"}}
    else:
        row["status"] = "cancelled"
    second = write_calendar_create(conf, client=client, now=NOW)
    assert second.outcome == CalendarWriteOutcome.FAILED
    assert len(service.rows) == 1


def test_google_reads_every_page_including_empty_page(monkeypatch):
    item = {"id": "e1", "start": {"date": "2026-09-20"}, "end": {"date": "2026-09-21"}}
    service = Service(
        pages=[
            {"items": [], "nextPageToken": "1"},
            {"items": [item], "nextPageToken": "2"},
            {"items": [{**item, "id": "e2"}]},
        ]
    )
    events = google(monkeypatch, service).list_events(
        calendar_id="test", time_min=NOW, time_max=NOW + timedelta(days=7)
    )
    assert [e.event_id for e in events] == ["e1", "e2"]
    assert len(service.list_calls) == 3


def test_google_page_failure_is_not_partial_success(monkeypatch):
    service = Service(pages=[{"nextPageToken": "1"}, RuntimeError("page unavailable")])
    with pytest.raises(RuntimeError, match="page unavailable"):
        google(monkeypatch, service).list_events(
            calendar_id="test", time_min=NOW, time_max=NOW + timedelta(days=7)
        )


def test_direct_resolve_enforces_expiry():
    store = InMemoryConfirmationStore()
    conf = create_confirmation(parse(PHRASE, now=NOW), store=store, now=NOW)
    with pytest.raises(ConfirmationError, match="expired"):
        resolve_confirmation(
            conf.confirmation_id, "accept", store=store, now=conf.expires_at
        )
    assert store.get(conf.confirmation_id).status == ConfirmationStatus.EXPIRED


@pytest.mark.parametrize("explicit", [False, True])
def test_proposal_discloses_effective_end(explicit):
    parsed = parse(PHRASE, now=NOW)
    parsed.end = parsed.start + timedelta(hours=2) if explicit else None
    expected = parsed.end or parsed.start + timedelta(hours=1)
    assert expected.isoformat() in build_proposal(parsed)
    assert "later Writer phase" not in build_proposal(parsed)


def dispatch(text, store, *, ts="1.0", thread=None, **kwargs):
    raw = {
        "type": "message",
        "channel": "plans",
        "user": "parent",
        "text": text,
        "ts": ts,
    }
    if thread:
        raw["thread_ts"] = thread
    return process_slack_message_event(
        raw, allowed_channel_ids={"plans"}, confirmation_store=store, now=NOW, **kwargs
    )


def test_duplicate_delivery_keeps_one_terminal_confirmation():
    store = InMemoryConfirmationStore()
    client = FakeCalendarClient()
    audit = InMemoryCalendarAuditStore()
    for _ in range(2):
        dispatch(PHRASE, store)
    assert len(store.list_all()) == 1
    dispatch(
        "yes",
        store,
        ts="2.0",
        thread="1.0",
        calendar_client=client,
        calendar_audit_store=audit,
    )
    dispatch(PHRASE, store)
    dispatch(
        "yes",
        store,
        ts="3.0",
        thread="1.0",
        calendar_client=client,
        calendar_audit_store=audit,
    )
    assert len(store.list_all()) == 1
    assert store.list_all()[0].status == ConfirmationStatus.ACCEPTED
    assert len(client.calls) == 1


def test_duplicate_note_delivery_preserves_one_raw_note():
    store = InMemoryLifeNotesStore()
    raw = {
        "type": "message",
        "channel": "notes",
        "user": "parent",
        "text": "  Building blocks.\n",
        "ts": "1.0",
    }
    for _ in range(2):
        process_slack_message_event(
            raw,
            allowed_channel_ids={"plans"},
            life_notes_channel_id="notes",
            life_notes_store=store,
            now=NOW,
        )
    assert len(store.list_recent()) == 1
    assert store.list_recent()[0].raw_text == raw["text"]


def test_second_yes_without_calendar_does_not_claim_added():
    store = InMemoryConfirmationStore()
    dispatch(PHRASE, store)
    dispatch("yes", store, ts="2", thread="1.0")
    reply = dispatch("yes", store, ts="3", thread="1.0").reply_text
    assert "already added" not in reply.lower()
    assert "not configured" in reply.lower()


def test_write_exception_does_not_claim_no_event_created():
    store = InMemoryConfirmationStore()
    dispatch(PHRASE, store)
    reply = dispatch(
        "yes",
        store,
        ts="2",
        thread="1.0",
        calendar_client=FakeCalendarClient(fail_with=TimeoutError("response lost")),
    ).reply_text
    assert "no event was created" not in reply.lower()
    assert "could not verify" in reply.lower()


@pytest.mark.parametrize(
    "phrase,hour",
    [
        ("今日上晝3:30去公園", 3),
        ("今日上午7:30去公園", 7),
        ("今日下晝3:30去公園", 15),
        ("今日2:30去公園", 14),
    ],
)
def test_explicit_period_wins(phrase, hour):
    result = parse(phrase, now=NOW)
    assert result.intent_type == IntentType.CREATE_EVENT
    assert result.start.hour == hour


@pytest.mark.parametrize(
    "phrase",
    [
        "tomorrow 13am playground",
        "tomorrow 0pm playground",
        "2027年9月20日至2026年9月19日有乜",
    ],
)
def test_invalid_clock_or_explicit_range_never_succeeds(phrase):
    assert parse(phrase, now=NOW).intent_type in {
        IntentType.UNKNOWN,
        IntentType.NEEDS_CLARIFICATION,
    }


def test_yearly_leap_day_does_not_depend_on_current_year():
    result = parse("2月29日 Example 生日", now=NOW)
    assert result.intent_type == IntentType.ADD_IMPORTANT_DATE
    assert (result.start.month, result.start.day, result.notes) == (2, 29, "yearly")


@pytest.mark.parametrize("phrase", ["list events", "有乜行程", "2月30日 Example 生日"])
def test_non_create_clarification_never_calls_llm(phrase):
    llm = FakeLlmParser(result=parse(PHRASE, now=NOW))
    result = parse_with_fallback(phrase, now=NOW, llm=llm)
    assert llm.calls == []
    assert result.intent_type != IntentType.CREATE_EVENT


@pytest.mark.parametrize(
    "updates",
    [
        {"start": None, "all_day": True},
        {"all_day": "false"},
        {"end": "2026-09-20T08:00:00+08:00"},
        {"missing_fields": ["start"]},
    ],
)
def test_invalid_llm_payload_is_not_create(updates):
    payload = {
        "intent_type": "create_event",
        "title": "Park",
        "start": "2026-09-20T09:00:00+08:00",
        "all_day": False,
        **updates,
    }
    result = llm_payload_to_parse_result(payload, raw_text="tomorrow park", now=NOW)
    assert result.intent_type != IntentType.CREATE_EVENT


def test_miss_store_failure_does_not_discard_useful_parse(capsys):
    class BrokenStore:
        def save(self, miss):
            raise OSError("disk unavailable")

    result = parse_with_fallback(
        "後日3點去買餸",
        now=NOW,
        llm=FakeLlmParser(result=parse(PHRASE, now=NOW)),
        miss_store=BrokenStore(),
    )
    assert result.intent_type == IntentType.CREATE_EVENT
    assert "parse_miss_record_failed" in capsys.readouterr().out


def test_repeated_yes_audit_failure_does_not_claim_success():
    class BrokenAudit:
        def list_all(self):
            raise OSError("disk unavailable")

    store = InMemoryConfirmationStore()
    dispatch(PHRASE, store)
    dispatch("yes", store, ts="2", thread="1.0")
    result = dispatch(
        "yes",
        store,
        ts="3",
        thread="1.0",
        calendar_client=FakeCalendarClient(),
        calendar_audit_store=BrokenAudit(),
    )
    assert result.outcome == ListenerOutcome.REPLIED
    assert "already added" not in result.reply_text.lower()


def test_writer_normalizes_naive_family_time():
    conf = accepted()
    conf = replace(
        conf,
        parse_result=replace(
            conf.parse_result, start=NOW.replace(day=20, hour=9, tzinfo=None)
        ),
    )
    client = FakeCalendarClient()
    assert (
        write_calendar_create(conf, client=client, now=NOW).outcome
        == CalendarWriteOutcome.SUCCESS
    )
    assert client.calls[0].start == datetime(2026, 9, 20, 9, tzinfo=TZ)


def test_atomic_save_failure_preserves_confirmation(tmp_path, monkeypatch):
    from cec_vivisystem import storage
    from cec_vivisystem.confirmation import JsonDirConfirmationStore

    store = JsonDirConfirmationStore(tmp_path)
    conf = create_confirmation(parse(PHRASE, now=NOW), store=store, now=NOW)

    def fail(*args):
        raise OSError("rename failed")

    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(OSError):
        resolve_confirmation(conf.confirmation_id, "accept", store=store, now=NOW)
    assert store.get(conf.confirmation_id).status == ConfirmationStatus.PENDING
    assert len(list(tmp_path.iterdir())) == 1


def test_disk_note_replay_after_restart(tmp_path):
    from cec_vivisystem.life_notes import JsonDirLifeNotesStore, create_life_note

    kwargs = {
        "source": {"channel": "notes", "message_id": "3.0", "user": "parent"},
        "deduplicate_source": True,
    }
    first = create_life_note(
        " exact text\n", store=JsonDirLifeNotesStore(tmp_path), now=NOW, **kwargs
    )
    second = create_life_note(
        " exact text\n",
        store=JsonDirLifeNotesStore(tmp_path),
        now=NOW + timedelta(days=1),
        **kwargs,
    )
    assert first == second
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_log_open_failure_still_reaches_console(tmp_path, monkeypatch, capsys):
    from cec_vivisystem import logging as log

    log.setup_logging(log_dir=tmp_path, enable_file_logging=True, run_retention=False)

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(log, "_handle_for", fail)
    log.get_logger().info("still_visible", component="test")
    output = capsys.readouterr().out
    assert "still_visible" in output
    assert "log_file_error" in output


def test_log_rollover_closes_handles_and_runs_retention(tmp_path, monkeypatch):
    from cec_vivisystem import logging as log

    day = NOW.date()
    monkeypatch.setattr(log, "local_today", lambda now=None: day)
    log.setup_logging(log_dir=tmp_path, enable_file_logging=True, run_retention=False)
    log.get_logger().info("first", component="test")
    first_handles = list(log._file_handles.values())
    day += timedelta(days=20)
    # A UTC event timestamp must not override the local filename date.
    log.get_logger().info("second", component="test")
    assert all(h.closed for h in first_handles)
    assert not (tmp_path / log.log_filename("test", NOW.date())).exists()
    assert not list((tmp_path / "archive").glob("*.log"))
    assert (tmp_path / log.log_filename("test", day)).exists()


@pytest.mark.parametrize(
    "phrase",
    ["明天9点去公园", "今日有什么活动", "加個Event，9月18号，下晝3:30 ，去公園"],
)
def test_simplified_input_cannot_escape_through_fallback(phrase):
    llm = FakeLlmParser(result=parse(PHRASE, now=NOW))
    assert (
        parse_with_fallback(phrase, now=NOW, llm=llm).intent_type == IntentType.UNKNOWN
    )
    assert llm.calls == []


def test_invalid_direct_writer_end_is_refused():
    conf = accepted()
    conf.parse_result.end = conf.parse_result.start - timedelta(hours=1)
    client = FakeCalendarClient()
    assert (
        write_calendar_create(conf, client=client, now=NOW).outcome
        == CalendarWriteOutcome.REFUSED
    )
    assert client.calls == []


def test_all_day_overlap_and_proposal_use_midnight_window():
    from cec_vivisystem.overlap import proposed_window

    parsed = parse(PHRASE, now=NOW)
    parsed.all_day = True
    start, end = proposed_window(parsed)
    assert start.hour == end.hour == 0
    assert end - start == timedelta(days=1)


def test_expired_thread_yes_replies_instead_of_disappearing():
    store = InMemoryConfirmationStore()
    dispatch(PHRASE, store)
    raw = {
        "channel": "plans",
        "user": "parent",
        "text": "yes",
        "ts": "2.0",
        "thread_ts": "1.0",
    }
    result = process_slack_message_event(
        raw,
        allowed_channel_ids={"plans"},
        confirmation_store=store,
        now=NOW + timedelta(days=2),
    )
    assert result.outcome == ListenerOutcome.REPLIED
    assert "expired" in result.reply_text.lower()


def test_concurrent_duplicate_dispatch_creates_one_record():
    from concurrent.futures import ThreadPoolExecutor

    store = InMemoryConfirmationStore()
    client = FakeCalendarClient()
    audit = InMemoryCalendarAuditStore()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: dispatch(PHRASE, store), range(8)))
        list(
            pool.map(
                lambda i: dispatch(
                    "yes",
                    store,
                    ts=str(i + 2),
                    thread="1.0",
                    calendar_client=client,
                    calendar_audit_store=audit,
                ),
                range(8),
            )
        )
    assert len(store.list_all()) == 1
    assert len(client.calls) == 1


def test_confirmation_storage_failure_is_visible():
    class BrokenStore:
        def list_all(self):
            raise OSError("disk unavailable")

    result = dispatch(PHRASE, BrokenStore())
    assert result.outcome == ListenerOutcome.REPLIED
    assert "could not process" in result.reply_text.lower()


def test_writer_refuses_non_create_intent_even_if_accepted():
    conf = accepted()
    conf.parse_result.intent_type = IntentType.LIST_EVENTS
    client = FakeCalendarClient()
    result = write_calendar_create(conf, client=client, now=NOW)
    assert result.outcome == CalendarWriteOutcome.REFUSED
    assert client.calls == []


def test_failed_parse_still_has_user_visible_reply():
    def broken_parse(*args, **kwargs):
        raise RuntimeError("parse unavailable")

    result = dispatch(PHRASE, InMemoryConfirmationStore(), parse=broken_parse)
    assert result.error_type == "RuntimeError"
    assert result.reply_text


def test_archive_collision_preserves_both_log_segments(tmp_path):
    from cec_vivisystem.logging import local_today, log_filename, maintain_log_storage

    day = local_today(NOW) - timedelta(days=1)
    name = log_filename("test", day)
    (tmp_path / "archive").mkdir()
    (tmp_path / "archive" / name).write_text("earlier\n")
    (tmp_path / name).write_text("later\n")
    maintain_log_storage(tmp_path, now=NOW)
    assert (tmp_path / "archive" / name).read_text() == "earlier\nlater\n"


def test_all_day_proposal_start_matches_writer():
    parsed = parse(PHRASE, now=NOW)
    parsed.all_day = True
    text = build_proposal(parsed)
    assert parsed.start.replace(hour=0).isoformat() in text


def test_period_recap_clips_ongoing_events_to_requested_days():
    from cec_vivisystem.calendar_reader import format_recap
    from cec_vivisystem.models import (
        CalendarListedEvent,
        CalendarListOutcome,
        CalendarListResult,
    )

    start = NOW.replace(hour=0)
    result = CalendarListResult(
        outcome=CalendarListOutcome.SUCCESS,
        calendar_id="test",
        time_min=start,
        time_max=start + timedelta(days=2),
        events=[
            CalendarListedEvent(
                event_id="trip",
                summary="Trip",
                all_day=True,
                start=start - timedelta(days=1),
                end=start + timedelta(days=2),
            )
        ],
    )
    recap = format_recap(result)
    assert "2026-09-18" not in recap
    assert "2026-09-19" in recap and "2026-09-20" in recap
    assert "2026-09-21" not in recap
    assert recap.count("Trip") == 2
