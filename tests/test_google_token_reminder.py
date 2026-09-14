"""Phase 16 unit tests for Google token expiry reminder (offline, no Slack)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from cec_vivisystem.google_token_reminder import (
    InMemoryGoogleTokenReminderStore,
    days_until_expiry,
    format_expiry_reminder,
    parse_issued_at,
    run_google_token_reminder,
)
from cec_vivisystem.models import GoogleTokenReminderOutcome
from cec_vivisystem.morning_recap import FakeSlackPoster

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
FIXED_ISSUED = datetime(2026, 9, 13, 22, 31, tzinfo=FAMILY_TZ)
CHANNEL = "C_FAMILY"


def _run(
    now: datetime,
    *,
    store: InMemoryGoogleTokenReminderStore | None = None,
    poster: FakeSlackPoster | None = None,
    issued_at: datetime | None = FIXED_ISSUED,
) -> tuple:
    store = store or InMemoryGoogleTokenReminderStore()
    poster = poster or FakeSlackPoster()
    result = run_google_token_reminder(
        now=now,
        poster=poster,
        channel_id=CHANNEL,
        issued_at=issued_at,
        post_store=store,
        correlation_id="corr-g",
    )
    return result, poster, store


def test_posts_three_days_before_expiry() -> None:
    """G1: 17 Sep 10:00 HKT → 3 days left; one Slack post."""
    now = datetime(2026, 9, 17, 10, 0, tzinfo=FAMILY_TZ)
    result, poster, store = _run(now)
    assert result.outcome == GoogleTokenReminderOutcome.POSTED
    assert result.days_left == 3
    assert result.post_text == "Google calendar will be expired in 3 days. Please refresh"
    assert poster.calls == [(CHANNEL, result.post_text)]
    assert store.has_posted(now.date())


def test_posts_two_days_before_expiry() -> None:
    """G2: 18 Sep → 2-day text."""
    now = datetime(2026, 9, 18, 10, 0, tzinfo=FAMILY_TZ)
    result, poster, _store = _run(now)
    assert result.outcome == GoogleTokenReminderOutcome.POSTED
    assert result.days_left == 2
    assert result.post_text == "Google calendar will be expired in 2 days. Please refresh"
    assert poster.calls == [(CHANNEL, result.post_text)]


def test_posts_one_day_before_expiry() -> None:
    """G3: 19 Sep → 1-day text (singular)."""
    now = datetime(2026, 9, 19, 10, 0, tzinfo=FAMILY_TZ)
    result, poster, _store = _run(now)
    assert result.outcome == GoogleTokenReminderOutcome.POSTED
    assert result.days_left == 1
    assert result.post_text == "Google calendar will be expired in 1 day. Please refresh"
    assert poster.calls == [(CHANNEL, result.post_text)]


def test_skips_four_days_before_expiry() -> None:
    """G4: 16 Sep (4 days) → skip; no post."""
    now = datetime(2026, 9, 16, 10, 0, tzinfo=FAMILY_TZ)
    result, poster, store = _run(now)
    assert result.outcome == GoogleTokenReminderOutcome.SKIPPED
    assert result.skip_reason == "too_early"
    assert result.days_left == 4
    assert poster.calls == []
    assert not store.has_posted(now.date())


def test_skips_on_expiry_day() -> None:
    """G5: 20 Sep (0 days) → skip; no post."""
    now = datetime(2026, 9, 20, 10, 0, tzinfo=FAMILY_TZ)
    result, poster, store = _run(now)
    assert result.outcome == GoogleTokenReminderOutcome.SKIPPED
    assert result.skip_reason == "expired_or_today"
    assert result.days_left == 0
    assert poster.calls == []
    assert not store.has_posted(now.date())


def test_second_run_same_day_skips() -> None:
    """G6: two runs on the 3-day date → one Slack call."""
    now = datetime(2026, 9, 17, 10, 0, tzinfo=FAMILY_TZ)
    store = InMemoryGoogleTokenReminderStore()
    poster = FakeSlackPoster()
    first, poster, store = _run(now, store=store, poster=poster)
    second, poster, store = _run(now, store=store, poster=poster)
    assert first.outcome == GoogleTokenReminderOutcome.POSTED
    assert second.outcome == GoogleTokenReminderOutcome.SKIPPED
    assert second.skip_reason == "already_posted"
    assert len(poster.calls) == 1


def test_missing_issued_at_skips_without_crash() -> None:
    """G7: no issued-at → skip missing_issued_at; no post."""
    now = datetime(2026, 9, 17, 10, 0, tzinfo=FAMILY_TZ)
    result, poster, store = _run(now, issued_at=None)
    assert result.outcome == GoogleTokenReminderOutcome.SKIPPED
    assert result.skip_reason == "missing_issued_at"
    assert poster.calls == []
    assert not store.has_posted(now.date())


def test_poster_failure_does_not_mark_posted() -> None:
    """G8: Slack error → failed; not marked posted."""
    now = datetime(2026, 9, 17, 10, 0, tzinfo=FAMILY_TZ)
    poster = FakeSlackPoster(fail_with=RuntimeError("Slack 500"))
    result, poster, store = _run(now, poster=poster)
    assert result.outcome == GoogleTokenReminderOutcome.FAILED
    assert result.error_type == "RuntimeError"
    assert len(poster.calls) == 1
    assert not store.has_posted(now.date())


def test_result_contract_fields() -> None:
    """G9: GoogleTokenReminderResult exposes Phase 16 contract fields."""
    now = datetime(2026, 9, 17, 10, 0, tzinfo=FAMILY_TZ)
    result, _poster, _store = _run(now)
    for name in (
        "outcome",
        "review_date",
        "channel_id",
        "days_left",
        "post_text",
        "skip_reason",
        "error_type",
        "error_message",
        "duration_ms",
    ):
        assert hasattr(result, name), f"missing {name}"
    assert result.outcome.value == "posted"
    assert result.review_date == now.date()
    assert result.channel_id == CHANNEL


def test_logs_boundary(capsys) -> None:
    """G10: start + posted events on the 3-day run."""
    now = datetime(2026, 9, 17, 10, 0, tzinfo=FAMILY_TZ)
    result, _poster, _store = _run(now)
    assert result.outcome == GoogleTokenReminderOutcome.POSTED
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "google_token_reminder_started" in combined
    assert "google_token_reminder_posted" in combined


def test_days_until_and_parse_issued_at() -> None:
    """Issued-at date-only and ISO both count HKT calendar days; no token in helpers."""
    now = datetime(2026, 9, 17, 10, 0, tzinfo=FAMILY_TZ)
    assert days_until_expiry(issued_at=FIXED_ISSUED, now=now) == 3
    parsed = parse_issued_at("2026-09-13")
    assert parsed is not None
    assert days_until_expiry(issued_at=parsed, now=now) == 3
    zulu = parse_issued_at("2026-09-13T14:31:05Z")
    assert zulu is not None
    assert days_until_expiry(issued_at=zulu, now=now) == 3
    assert parse_issued_at("") is None
    assert parse_issued_at("not-a-date") is None
    assert format_expiry_reminder(1).endswith("1 day. Please refresh")
