"""Phase 16 unit tests for Google token expiry reminder (offline, no Slack)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem.google_token_reminder import (
    GoogleTokenReminderConfigError,
    InMemoryGoogleTokenReminderStore,
    days_until_expiry,
    format_expiry_reminder,
    load_google_token_reminder_config,
    parse_issued_at,
    run_google_token_reminder,
)
from cec_vivisystem.important_dates import (
    load_important_dates_config,
    token_notice_channel,
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
    assert result.post_text.startswith(
        "Google Calendar access expires in 3 days. "
        "Re-authorize Google access before Sun 20 Sep."
    )
    assert "Google 日曆授權將於 3 日後到期。請在 9月20日（星期日）前重新授權。" in result.post_text
    assert poster.calls == [(CHANNEL, result.post_text)]
    assert store.has_posted(now.date())


def test_posts_two_days_before_expiry() -> None:
    """G2: 18 Sep → 2-day text."""
    now = datetime(2026, 9, 18, 10, 0, tzinfo=FAMILY_TZ)
    result, poster, _store = _run(now)
    assert result.outcome == GoogleTokenReminderOutcome.POSTED
    assert result.days_left == 2
    assert result.post_text.startswith("Google Calendar access expires in 2 days.")
    assert poster.calls == [(CHANNEL, result.post_text)]


def test_posts_one_day_before_expiry() -> None:
    """G3: 19 Sep → 1-day text (singular)."""
    now = datetime(2026, 9, 19, 10, 0, tzinfo=FAMILY_TZ)
    result, poster, _store = _run(now)
    assert result.outcome == GoogleTokenReminderOutcome.POSTED
    assert result.days_left == 1
    assert result.post_text.startswith("Google Calendar access expires in 1 day.")
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


def test_posts_on_expiry_day() -> None:
    """G5 (Phase 30): 20 Sep (0 days) → "expires today" notice, not a silent skip."""
    now = datetime(2026, 9, 20, 10, 0, tzinfo=FAMILY_TZ)
    result, poster, store = _run(now)
    assert result.outcome == GoogleTokenReminderOutcome.POSTED
    assert result.days_left == 0
    assert result.post_text.startswith("Google Calendar access expires today (Sun 20 Sep).")
    assert poster.calls == [(CHANNEL, result.post_text)]
    assert store.has_posted(now.date())


def test_expired_posts_once_per_date_until_reauthorized(capsys) -> None:
    """G11 (Phase 30 decision 8): expired → one "expired" notice per HKT date."""
    store = InMemoryGoogleTokenReminderStore()
    poster = FakeSlackPoster()
    day_one = datetime(2026, 9, 25, 10, 0, tzinfo=FAMILY_TZ)
    first, _, _ = _run(day_one, store=store, poster=poster)
    again, _, _ = _run(day_one, store=store, poster=poster)
    next_day, _, _ = _run(datetime(2026, 9, 26, 10, 0, tzinfo=FAMILY_TZ), store=store,
                          poster=poster)
    assert first.outcome == GoogleTokenReminderOutcome.POSTED and first.days_left == -5
    assert again.outcome == GoogleTokenReminderOutcome.SKIPPED
    assert again.skip_reason == "already_posted"
    assert next_day.outcome == GoogleTokenReminderOutcome.POSTED
    assert len(poster.calls) == 2
    text = poster.calls[0][1]
    assert text.startswith("Google Calendar access expired on Sun 20 Sep.")
    assert "Re-authorize Google access." in text
    assert "Google 日曆授權已於 9月20日（星期日）到期。" in text
    assert "expired" in capsys.readouterr().out


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
    assert format_expiry_reminder(1, expires_on=now.date()).startswith(
        "Google Calendar access expires in 1 day."
    )


def test_config_routes_to_system_channel_with_logged_fallback(capsys) -> None:
    """C2: token notices use the status channel; plans channel only as a logged fallback."""
    env = {
        "SLACK_BOT_TOKEN": "fake-bot",
        "CEC_HEALTH_ALERT_CHANNEL_ID": "C_STATUS",
        "SLACK_FAMILY_PLANS_CHANNEL_ID": "C_PLANS",
    }
    assert load_google_token_reminder_config(env) == ("fake-bot", "C_STATUS")
    assert "system_channel_fallback" not in capsys.readouterr().out
    del env["CEC_HEALTH_ALERT_CHANNEL_ID"]
    assert load_google_token_reminder_config(env) == ("fake-bot", "C_PLANS")
    assert "system_channel_fallback" in capsys.readouterr().out
    with pytest.raises(GoogleTokenReminderConfigError) as exc:
        load_google_token_reminder_config({"SLACK_BOT_TOKEN": "fake-bot"})
    assert "CEC_HEALTH_ALERT_CHANNEL_ID" in str(exc.value)


def test_dates_job_sends_token_notice_to_system_channel() -> None:
    """C2: the 10:00 job keeps the dates review in plans; the token notice moves."""
    env = {"CEC_HEALTH_ALERT_CHANNEL_ID": "C_STATUS", "SLACK_FAMILY_PLANS_CHANNEL_ID": "C_PLANS",
           "SLACK_BOT_TOKEN": "fake-bot"}
    assert token_notice_channel(env) == "C_STATUS"
    assert load_important_dates_config(env) == ("fake-bot", "C_PLANS")
    del env["CEC_HEALTH_ALERT_CHANNEL_ID"]
    assert token_notice_channel(env) == "C_PLANS"


def test_notice_text_is_traditional_and_token_free() -> None:
    """P15 for token notices: EN then TC; no Simplified markers; no token-like text."""
    from cec_vivisystem.parser import contains_simplified_markers

    issued = FIXED_ISSUED.date()
    for days in (3, 1, 0, -2):
        text = format_expiry_reminder(days, expires_on=issued)
        en, tc = text.split("\n\n")
        assert en.isascii() and not tc.isascii()
        assert not contains_simplified_markers(text)
        assert "xox" not in text and "xapp" not in text
