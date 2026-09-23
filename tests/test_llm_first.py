"""LLM-first routing and failure contracts; no live providers."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from cec_vivisystem.listener import format_reply
from cec_vivisystem.models import IntentType
from cec_vivisystem.parse_fallback import (
    FakeLlmParser,
    parse_with_fallback,
    validate_create_payload,
)
from cec_vivisystem.parse_misses import InMemoryParseMissStore
from cec_vivisystem.parser import parse
from tests.test_create_extraction import MESSAGE, NOW, payload, provider


def run(message, llm, **kwargs):
    return parse_with_fallback(message, now=NOW, llm=llm, llm_first=True, **kwargs)


@pytest.mark.parametrize(
    "message", ["聽日9點去公園", MESSAGE, "Next Tue at nine in the evening, photo walk"]
)
def test_model_first_never_invokes_create_rules(monkeypatch, message):
    llm, create = provider(monkeypatch, payload())
    rules = Mock(side_effect=AssertionError("Create rules must not run"))
    store = InMemoryParseMissStore()
    result = run(message, llm, rule_parse_fn=rules, miss_store=store)
    assert create.call_count == 1
    assert result.title == "影相+散步"
    assert result.notes.startswith("llm_first")
    assert store.list_all() == []


@pytest.mark.parametrize(
    "message,intent",
    [
        ("help", IntentType.HELP),
        ("今日有乜", IntentType.LIST_EVENTS),
        ("重要日子", IntentType.LIST_IMPORTANT_DATES),
        ("3月5日 Alex 生日", IntentType.ADD_IMPORTANT_DATE),
        ("", IntentType.UNKNOWN),
        ("???", IntentType.UNKNOWN),
        ("今日天氣點呀", IntentType.UNKNOWN),
    ],
)
def test_control_routes_never_call_model(message, intent):
    llm = FakeLlmParser(fail_with=AssertionError("No model needed"))
    result = run(message, llm)
    assert result.intent_type == intent
    assert llm.calls == []


def test_script_rejection_never_calls_model():
    # Escaped negative fixture for unsupported script; never a runtime alias.
    llm = FakeLlmParser()
    assert (
        run("\u660e\u5929\u4e0b\u53483\u70b9\u6e38\u6cf3", llm).intent_type
        == IntentType.UNKNOWN
    )
    assert llm.calls == []


def test_no_model_retains_offline_behavior():
    assert run("聽日9點去公園", None) == parse("聽日9點去公園", now=NOW)


@pytest.mark.parametrize("intent", [IntentType.UNKNOWN, IntentType.NEEDS_CLARIFICATION])
def test_model_decline_not_overridden_by_rules(intent):
    known = parse("聽日9點去公園", now=NOW)
    llm = FakeLlmParser(replace(known, intent_type=intent, title=None))
    result = run("聽日9點去公園", llm)
    assert result.intent_type == intent


def test_outage_is_visible_without_rules_create():
    llm = FakeLlmParser(fail_with=TimeoutError("synthetic timeout"))
    result = run("聽日9點去公園", llm)
    assert result.intent_type == IntentType.NEEDS_CLARIFICATION
    assert result.notes == "llm_unavailable"
    assert "temporarily unavailable" in format_reply(result)
    assert "missing" not in format_reply(result)
    assert len(llm.calls) == 1


def test_bounded_failover(monkeypatch):
    primary = FakeLlmParser(fail_with=TimeoutError("synthetic"))
    secondary, create = provider(monkeypatch, payload())
    result = run(MESSAGE, primary, llm_fallback=secondary)
    assert result.intent_type == IntentType.CREATE_EVENT
    assert len(primary.calls) == create.call_count == 1


@pytest.mark.parametrize(
    "updates",
    [
        {"all_day": "false"},
        {"participants": "Coco"},
        {"title": 123},
        {"start": "2026-10-06T21:00:00"},
        {"end": "bad timestamp"},
        {"intent_type": "delete_event"},
        {"confidence": "high"},
        {"extra": "unexpected"},
        {"participants": ["Invented"]},
    ],
)
def test_local_schema_validation(updates):
    with pytest.raises((ValueError, TypeError)):
        validate_create_payload(payload(**updates))


@pytest.mark.parametrize(
    "updates",
    [
        {"title": " "},
        {"start": None},
        {"end": "2026-10-06T20:00:00+08:00"},
    ],
)
def test_invalid_semantics_never_create(monkeypatch, updates):
    llm, _ = provider(monkeypatch, payload(**updates))
    assert run(MESSAGE, llm).intent_type != IntentType.CREATE_EVENT


def test_broken_provider_schema_is_unavailable(monkeypatch):
    llm, _ = provider(monkeypatch, {"intent_type": "create_event"})
    result = run(MESSAGE, llm)
    assert result.notes == "llm_unavailable"


def test_llm_create_confirmation_yes_and_redelivery(monkeypatch):
    from cec_vivisystem.calendar_writer import (
        FakeCalendarClient,
        InMemoryCalendarAuditStore,
    )
    from cec_vivisystem.confirmation import InMemoryConfirmationStore
    from cec_vivisystem.listener import process_slack_message_event

    llm, create = provider(monkeypatch, payload())
    store = InMemoryConfirmationStore()
    calendar = FakeCalendarClient()
    audit = InMemoryCalendarAuditStore()
    event = {
        "type": "message",
        "text": MESSAGE,
        "channel": "C_TEST",
        "user": "U_TEST",
        "ts": "1791180000.000001",
        "client_msg_id": "synthetic-24",
    }
    kwargs = {
        "allowed_channel_ids": {"C_TEST"},
        "confirmation_store": store,
        "calendar_client": calendar,
        "calendar_audit_store": audit,
        "llm_parser": llm,
        "now": NOW,
    }
    process_slack_message_event(event, **kwargs)
    assert len(store.list_pending()) == 1
    assert calendar.calls == []
    answer = event | {
        "text": "yes",
        "ts": "1791180001.000001",
        "thread_ts": event["ts"],
        "client_msg_id": "synthetic-24-yes",
    }
    process_slack_message_event(answer, **kwargs)
    process_slack_message_event(answer, **kwargs)
    assert len(calendar.calls) == 1
    assert create.call_count == 1
    assert store.list_pending() == []


def test_raw_notes_skip_model():
    from cec_vivisystem.life_notes import InMemoryLifeNotesStore
    from cec_vivisystem.listener import process_slack_message_event

    notes = InMemoryLifeNotesStore()
    llm = FakeLlmParser(fail_with=AssertionError("Raw notes must bypass extraction"))
    text = "  今日試咗新食譜。  "
    process_slack_message_event(
        {
            "type": "message",
            "text": text,
            "channel": "C_NOTES",
            "user": "U_TEST",
            "ts": "1791180000.000002",
            "client_msg_id": "synthetic-note-24",
        },
        allowed_channel_ids={"C_TEST"},
        life_notes_channel_id="C_NOTES",
        life_notes_store=notes,
        llm_parser=llm,
        now=NOW,
    )
    assert llm.calls == []
    assert notes.list_recent()[0].raw_text == text


def test_eval_corpus_and_scoring():
    import json

    from scripts.evaluate_create_parser import CASES_PATH, mismatches

    cases = json.loads(CASES_PATH.read_text())
    assert len({case["id"] for case in cases}) == len(cases) == 16
    result = parse("聽日9點去公園", now=NOW)
    assert mismatches(result, cases[0]["expected"]) == []
    assert mismatches(result, {"title": "different"}) == ["title"]
