"""Phase 18 parse-miss keyword promotion — offline."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from cec_vivisystem.models import IntentType, ParseResult
from cec_vivisystem.parse_misses import (
    InMemoryParseMissStore,
    extract_miss_tokens,
    record_parse_miss,
    summarize_parse_misses,
)

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 9, 14, 10, 0, tzinfo=FAMILY_TZ)


def _unknown(text: str) -> ParseResult:
    return ParseResult(
        intent_type=IntentType.UNKNOWN,
        title=None,
        start=None,
        end=None,
        all_day=False,
        location=None,
        participants=[],
        raw_text=text,
        missing_fields=[],
    )


def test_summarize_repeated_ear_cleaning_token() -> None:
    """M1: two 洗耳仔 misses → count 2."""
    store = InMemoryParseMissStore()
    for text in ("今晚同椰子糖洗耳仔", "聽日洗耳仔"):
        record_parse_miss(
            raw_text=text,
            rule_result=_unknown(text),
            store=store,
            now=NOW,
        )
    ranked = dict(summarize_parse_misses(store, min_count=2))
    assert ranked.get("洗耳仔") == 2


def test_known_swim_title_not_promoted() -> None:
    """M2: 游泳 is a known title — not a new-keyword candidate."""
    store = InMemoryParseMissStore()
    for text in ("聽日游泳", "後天游泳"):
        record_parse_miss(
            raw_text=text,
            rule_result=_unknown(text),
            store=store,
            now=NOW,
        )
    ranked = dict(summarize_parse_misses(store, min_count=2))
    assert "游泳" not in ranked
    tokens = extract_miss_tokens("聽日9點游泳")
    assert "游泳" not in tokens
