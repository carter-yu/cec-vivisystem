"""Phase 12 unit tests for morning recap (offline, no Slack / Google)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem.calendar_writer import FakeCalendarClient
from cec_vivisystem.models import (
    CalendarListedEvent,
    MorningRecapOutcome,
)
from cec_vivisystem.morning_recap import (
    FakeSlackPoster,
    InMemoryMorningRecapStore,
    JsonDirMorningRecapStore,
    MorningRecapConfigError,
    default_data_dir,
    load_morning_recap_config,
    run_morning_recap,
)

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
MORNING_NOW = datetime(2026, 9, 5, 7, 0, tzinfo=FAMILY_TZ)
DAY_START = datetime(2026, 9, 5, 0, 0, tzinfo=FAMILY_TZ)
DAY_END = datetime(2026, 9, 6, 0, 0, tzinfo=FAMILY_TZ)
CHANNEL = "C_FAMILY"
CAL_ID = "cal-test"


def _event(event_id: str, hour: int, title: str) -> CalendarListedEvent:
    start = datetime(2026, 9, 5, hour, 0, tzinfo=FAMILY_TZ)
    return CalendarListedEvent(
        event_id=event_id,
        summary=title,
        start=start,
        end=datetime(2026, 9, 5, hour + 1, 0, tzinfo=FAMILY_TZ),
        all_day=False,
        participants=["Cedric"],
    )


def test_run_morning_recap_posts_today_events() -> None:
    """M1: two events → one poster call; today HKT window."""
    client = FakeCalendarClient(
        listed_events=[_event("e1", 9, "游泳"), _event("e2", 15, "牙醫")]
    )
    poster = FakeSlackPoster()
    store = InMemoryMorningRecapStore()
    result = run_morning_recap(
        now=MORNING_NOW,
        client=client,
        poster=poster,
        calendar_id=CAL_ID,
        channel_id=CHANNEL,
        store=store,
    )
    assert result.outcome == MorningRecapOutcome.POSTED
    assert result.recap_date == MORNING_NOW.date()
    assert result.event_count == 2
    assert result.channel_id == CHANNEL
    assert result.post_text
    assert "早晨" in result.post_text
    assert "2026-09-05" in result.post_text
    assert "游泳" in result.post_text
    assert "牙醫" in result.post_text
    assert len(poster.calls) == 1
    assert poster.calls[0][0] == CHANNEL
    assert poster.calls[0][1] == result.post_text
    assert client.calls == []
    assert client.list_calls == [(CAL_ID, DAY_START, DAY_END)]
    assert store.has_posted(MORNING_NOW.date())
    for name in (
        "outcome",
        "recap_date",
        "channel_id",
        "event_count",
        "post_text",
        "error_type",
        "error_message",
        "duration_ms",
    ):
        assert hasattr(result, name), f"missing {name}"


def test_run_morning_recap_empty_day_still_posts() -> None:
    """M2: empty calendar → still one poster call; empty wording."""
    client = FakeCalendarClient()
    poster = FakeSlackPoster()
    result = run_morning_recap(
        now=MORNING_NOW,
        client=client,
        poster=poster,
        calendar_id=CAL_ID,
        channel_id=CHANNEL,
        store=InMemoryMorningRecapStore(),
    )
    assert result.outcome == MorningRecapOutcome.POSTED
    assert result.event_count == 0
    assert result.post_text
    assert "冇活動" in result.post_text
    assert "2026-09-05" in result.post_text
    assert len(poster.calls) == 1
    assert client.calls == []
    assert client.list_calls == [(CAL_ID, DAY_START, DAY_END)]


def test_run_morning_recap_second_run_skips() -> None:
    """M3: second run same date → skip; no second post."""
    client = FakeCalendarClient(listed_events=[_event("e1", 9, "游泳")])
    poster = FakeSlackPoster()
    store = InMemoryMorningRecapStore()
    first = run_morning_recap(
        now=MORNING_NOW,
        client=client,
        poster=poster,
        calendar_id=CAL_ID,
        channel_id=CHANNEL,
        store=store,
    )
    assert first.outcome == MorningRecapOutcome.POSTED
    second = run_morning_recap(
        now=MORNING_NOW,
        client=client,
        poster=poster,
        calendar_id=CAL_ID,
        channel_id=CHANNEL,
        store=store,
    )
    assert second.outcome == MorningRecapOutcome.SKIPPED_ALREADY_POSTED
    assert len(poster.calls) == 1
    assert len(client.list_calls) == 1
    assert client.calls == []


def test_run_morning_recap_list_failure() -> None:
    """M4: list failure → failed outcome; no calendar write."""
    client = FakeCalendarClient(fail_list_with=RuntimeError("Google 403"))
    poster = FakeSlackPoster()
    store = InMemoryMorningRecapStore()
    result = run_morning_recap(
        now=MORNING_NOW,
        client=client,
        poster=poster,
        calendar_id=CAL_ID,
        channel_id=CHANNEL,
        store=store,
    )
    assert result.outcome == MorningRecapOutcome.FAILED
    assert result.error_type == "RuntimeError"
    assert poster.calls == []
    assert client.calls == []
    assert not store.has_posted(MORNING_NOW.date())


def test_run_morning_recap_logs_boundary(capsys) -> None:
    """Boundary logs: morning_recap_started / morning_recap_posted."""
    result = run_morning_recap(
        now=MORNING_NOW,
        client=FakeCalendarClient(listed_events=[_event("e1", 9, "游泳")]),
        poster=FakeSlackPoster(),
        calendar_id=CAL_ID,
        channel_id=CHANNEL,
        store=InMemoryMorningRecapStore(),
    )
    assert result.outcome == MorningRecapOutcome.POSTED
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "morning_recap_started" in combined
    assert "morning_recap_posted" in combined


def test_json_dir_store_roundtrip(tmp_path: Path) -> None:
    """JsonDir marker persists across store instances."""
    store = JsonDirMorningRecapStore(tmp_path)
    assert store.has_posted(MORNING_NOW.date()) is False
    store.mark_posted(
        MORNING_NOW.date(),
        posted_at=MORNING_NOW,
        event_count=2,
        channel_id=CHANNEL,
    )
    again = JsonDirMorningRecapStore(tmp_path)
    assert again.has_posted(MORNING_NOW.date()) is True
    poster = FakeSlackPoster()
    result = run_morning_recap(
        now=MORNING_NOW,
        client=FakeCalendarClient(listed_events=[_event("e1", 9, "游泳")]),
        poster=poster,
        calendar_id=CAL_ID,
        channel_id=CHANNEL,
        store=again,
    )
    assert result.outcome == MorningRecapOutcome.SKIPPED_ALREADY_POSTED
    assert poster.calls == []


def test_default_data_dir_is_gitignored_morning_recap_path() -> None:
    path = default_data_dir()
    assert path.name == "morning_recap"
    assert path.parent.name == "data"


def test_load_morning_recap_config_missing_vars() -> None:
    with pytest.raises(MorningRecapConfigError) as excinfo:
        load_morning_recap_config(env={})
    assert "SLACK_BOT_TOKEN" in str(excinfo.value)
    assert "SLACK_FAMILY_PLANS_CHANNEL_ID" in str(excinfo.value)
