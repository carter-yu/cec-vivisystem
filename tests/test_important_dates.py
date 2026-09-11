"""Phase 15 unit tests for important dates (offline, no Slack)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cec_vivisystem.important_dates import (
    InMemoryImportantDatesPostStore,
    InMemoryImportantDatesStore,
    JsonDirImportantDatesStore,
    create_important_date,
    format_important_dates_list,
    next_occurrence,
    run_important_dates_review,
)
from cec_vivisystem.models import (
    ImportantDateKind,
    ImportantDatesReviewOutcome,
    ImportantDateWriteOutcome,
)
from cec_vivisystem.morning_recap import FakeSlackPoster
from cec_vivisystem.parser import parse

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
PHASE15_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=FAMILY_TZ)
REVIEW_NOW = datetime(2026, 4, 6, 10, 0, tzinfo=FAMILY_TZ)
I1 = "4月12日 梓梵生日"
CHANNEL = "C_FAMILY"


def test_create_important_date_stores_yearly() -> None:
    """D1: I1 stores yearly 4/12 with Cedric."""
    store = InMemoryImportantDatesStore()
    parsed = parse(I1, now=PHASE15_NOW)
    result = create_important_date(parsed, store=store, now=PHASE15_NOW)
    assert result.outcome == ImportantDateWriteOutcome.CREATED
    item = result.date
    assert item.kind == ImportantDateKind.YEARLY
    assert item.month == 4
    assert item.day == 12
    assert item.year is None
    assert "生日" in item.title
    assert "Cedric" in item.participants
    assert store.get(item.date_id) is not None


def test_list_important_dates_empty() -> None:
    """D2: empty store formats the empty line."""
    store = InMemoryImportantDatesStore()
    assert format_important_dates_list(store.list_all()) == "未記低重要日子。"


def test_create_important_date_duplicate() -> None:
    """D3: same month/day/title → already_exists; one row."""
    store = InMemoryImportantDatesStore()
    parsed = parse(I1, now=PHASE15_NOW)
    first = create_important_date(parsed, store=store, now=PHASE15_NOW)
    second = create_important_date(parsed, store=store, now=PHASE15_NOW)
    assert first.outcome == ImportantDateWriteOutcome.CREATED
    assert second.outcome == ImportantDateWriteOutcome.ALREADY_EXISTS
    assert second.date.date_id == first.date.date_id
    assert len(store.list_all()) == 1


def test_important_date_contract_fields() -> None:
    """D4: stored row exposes contract fields."""
    store = InMemoryImportantDatesStore()
    parsed = parse(I1, now=PHASE15_NOW)
    item = create_important_date(parsed, store=store, now=PHASE15_NOW).date
    for name in (
        "date_id",
        "title",
        "month",
        "day",
        "kind",
        "created_at",
        "year",
        "participants",
        "raw_text",
        "correlation_id",
    ):
        assert hasattr(item, name), f"missing {name}"


def test_create_important_date_logs_boundary(capsys) -> None:
    """D5: create logs important_date_written."""
    store = InMemoryImportantDatesStore()
    parsed = parse(I1, now=PHASE15_NOW)
    result = create_important_date(parsed, store=store, now=PHASE15_NOW)
    assert result.outcome == ImportantDateWriteOutcome.CREATED
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "important_date_written" in combined


def test_run_important_dates_review_posts_hits() -> None:
    """D6: birthday in next 7 days → one poster call."""
    dates = InMemoryImportantDatesStore()
    posts = InMemoryImportantDatesPostStore()
    poster = FakeSlackPoster()
    parsed = parse(I1, now=PHASE15_NOW)
    create_important_date(parsed, store=dates, now=PHASE15_NOW)
    result = run_important_dates_review(
        now=REVIEW_NOW,
        poster=poster,
        channel_id=CHANNEL,
        dates_store=dates,
        post_store=posts,
    )
    assert result.outcome == ImportantDatesReviewOutcome.POSTED
    assert result.hit_count == 1
    assert result.post_text
    assert "梓梵生日" in result.post_text or "生日" in result.post_text
    assert "4月12日" in result.post_text
    assert len(poster.calls) == 1
    assert poster.calls[0][0] == CHANNEL
    item = dates.list_all()[0]
    occ = next_occurrence(item, today=REVIEW_NOW.date())
    assert occ is not None
    assert posts.has_posted(item.date_id, occ)


def test_run_important_dates_review_skips_when_none() -> None:
    """D7: no date in the next 7 days → skip; no poster call."""
    dates = InMemoryImportantDatesStore()
    posts = InMemoryImportantDatesPostStore()
    poster = FakeSlackPoster()
    parsed = parse(I1, now=PHASE15_NOW)
    create_important_date(parsed, store=dates, now=PHASE15_NOW)
    result = run_important_dates_review(
        now=PHASE15_NOW,
        poster=poster,
        channel_id=CHANNEL,
        dates_store=dates,
        post_store=posts,
    )
    assert result.outcome == ImportantDatesReviewOutcome.SKIPPED
    assert poster.calls == []


def test_run_important_dates_review_skips_already_posted() -> None:
    """D8: second review of the same occurrence does not post again."""
    dates = InMemoryImportantDatesStore()
    posts = InMemoryImportantDatesPostStore()
    poster = FakeSlackPoster()
    parsed = parse(I1, now=PHASE15_NOW)
    create_important_date(parsed, store=dates, now=PHASE15_NOW)
    first = run_important_dates_review(
        now=REVIEW_NOW,
        poster=poster,
        channel_id=CHANNEL,
        dates_store=dates,
        post_store=posts,
    )
    second = run_important_dates_review(
        now=REVIEW_NOW,
        poster=poster,
        channel_id=CHANNEL,
        dates_store=dates,
        post_store=posts,
    )
    assert first.outcome == ImportantDatesReviewOutcome.POSTED
    assert second.outcome == ImportantDatesReviewOutcome.SKIPPED
    assert len(poster.calls) == 1


def test_run_important_dates_review_poster_error() -> None:
    """D9: poster error → failed; occurrence not marked."""
    dates = InMemoryImportantDatesStore()
    posts = InMemoryImportantDatesPostStore()
    poster = FakeSlackPoster(fail_with=RuntimeError("slack down"))
    parsed = parse(I1, now=PHASE15_NOW)
    create_important_date(parsed, store=dates, now=PHASE15_NOW)
    result = run_important_dates_review(
        now=REVIEW_NOW,
        poster=poster,
        channel_id=CHANNEL,
        dates_store=dates,
        post_store=posts,
    )
    assert result.outcome == ImportantDatesReviewOutcome.FAILED
    assert result.error_type == "RuntimeError"
    item = dates.list_all()[0]
    occ = next_occurrence(item, today=REVIEW_NOW.date())
    assert occ is not None
    assert posts.has_posted(item.date_id, occ) is False


def test_json_store_skips_corrupt_file(tmp_path: Path) -> None:
    """D10: corrupt JSON is skipped; list does not crash."""
    store = JsonDirImportantDatesStore(tmp_path)
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    parsed = parse(I1, now=PHASE15_NOW)
    create_important_date(parsed, store=store, now=PHASE15_NOW)
    items = store.list_all()
    assert len(items) == 1
    assert items[0].month == 4
