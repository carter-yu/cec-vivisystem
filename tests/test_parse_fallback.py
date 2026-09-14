"""Phase 18 hybrid parse fallback — Fake LLM only, no network."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from cec_vivisystem.models import Confidence, IntentType, ParseResult
from cec_vivisystem.parse_fallback import (
    FakeLlmParser,
    looks_like_create,
    parse_with_fallback,
)
from cec_vivisystem.parse_misses import InMemoryParseMissStore
from cec_vivisystem.parser import parse

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 9, 14, 9, 51, tzinfo=FAMILY_TZ)
KNOWN_CREATE = "聽日9點，梓梵游水"
MISS_CREATE = "後日3點帶梓梵去買餸"
WEATHER = "今日天氣點呀"


def _llm_create() -> ParseResult:
    return ParseResult(
        intent_type=IntentType.CREATE_EVENT,
        title="買餸",
        start=datetime(2026, 9, 16, 15, 0, tzinfo=FAMILY_TZ),
        end=None,
        all_day=False,
        location=None,
        participants=["Cedric"],
        raw_text=MISS_CREATE,
        confidence=Confidence.MEDIUM,
        missing_fields=[],
        notes="llm_fallback",
    )


def test_rules_create_does_not_call_llm() -> None:
    """H1: known create stays rules; Fake is not called; no miss."""
    llm = FakeLlmParser(result=_llm_create())
    store = InMemoryParseMissStore()
    result = parse_with_fallback(
        KNOWN_CREATE, now=NOW, llm=llm, miss_store=store
    )
    assert result.intent_type == IntentType.CREATE_EVENT
    assert result.title == "游泳"
    assert llm.calls == []
    assert store.list_all() == []


def test_unknown_create_uses_fake_llm_and_records_miss() -> None:
    """H2: create-looking unknown + Fake create → proposal fields + miss row."""
    rule = parse(MISS_CREATE, now=NOW)
    assert rule.intent_type in (IntentType.UNKNOWN, IntentType.NEEDS_CLARIFICATION)
    llm = FakeLlmParser(result=_llm_create())
    store = InMemoryParseMissStore()
    result = parse_with_fallback(
        MISS_CREATE, now=NOW, llm=llm, miss_store=store, correlation_id="h2"
    )
    assert result.intent_type == IntentType.CREATE_EVENT
    assert result.title == "買餸"
    assert result.start == datetime(2026, 9, 16, 15, 0, tzinfo=FAMILY_TZ)
    assert "Cedric" in result.participants
    assert result.notes is not None and "llm_fallback" in result.notes
    assert llm.calls == [MISS_CREATE]
    misses = store.list_all()
    assert len(misses) == 1
    assert misses[0].rule_intent == rule.intent_type.value
    assert misses[0].llm_intent == IntentType.CREATE_EVENT.value
    assert misses[0].llm_used is True


def test_llm_failure_keeps_rule_result() -> None:
    """H3: Fake raises → rule result; miss still recorded."""
    llm = FakeLlmParser(fail_with=RuntimeError("SpaceXAI 500"))
    store = InMemoryParseMissStore()
    rule = parse(MISS_CREATE, now=NOW)
    result = parse_with_fallback(
        MISS_CREATE, now=NOW, llm=llm, miss_store=store
    )
    assert result.intent_type == rule.intent_type
    assert result.notes != "llm_fallback" or rule.notes == "llm_fallback"
    assert store.list_all()[0].llm_used is False


def test_weather_does_not_call_llm() -> None:
    """H4: 今日天氣點呀 is not a create fallback."""
    llm = FakeLlmParser(result=_llm_create())
    store = InMemoryParseMissStore()
    result = parse_with_fallback(WEATHER, now=NOW, llm=llm, miss_store=store)
    assert result.intent_type == IntentType.UNKNOWN
    assert llm.calls == []
    assert store.list_all() == []
    assert looks_like_create(WEATHER, result) is False


def test_no_llm_still_records_miss() -> None:
    """H5: no client → unknown stays; miss recorded for keyword promotion."""
    store = InMemoryParseMissStore()
    result = parse_with_fallback(MISS_CREATE, now=NOW, llm=None, miss_store=store)
    assert result.intent_type in (IntentType.UNKNOWN, IntentType.NEEDS_CLARIFICATION)
    assert len(store.list_all()) == 1
    assert store.list_all()[0].llm_used is False


def test_fallback_result_contract() -> None:
    """H6: fallback create exposes ParseResult contract fields."""
    result = parse_with_fallback(
        MISS_CREATE, now=NOW, llm=FakeLlmParser(result=_llm_create())
    )
    for name in (
        "intent_type",
        "title",
        "start",
        "end",
        "all_day",
        "location",
        "participants",
        "raw_text",
        "confidence",
        "missing_fields",
        "notes",
    ):
        assert hasattr(result, name), name
    assert result.confidence == Confidence.MEDIUM
    assert result.raw_text == MISS_CREATE


def test_fallback_success_logs_model(capsys) -> None:
    """H7: Fake success logs parse_fallback_succeeded + model + latency_ms."""
    parse_with_fallback(
        MISS_CREATE, now=NOW, llm=FakeLlmParser(result=_llm_create())
    )
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "parse_fallback_succeeded" in text
    assert "fake-llm" in text
    assert "latency_ms" in text
