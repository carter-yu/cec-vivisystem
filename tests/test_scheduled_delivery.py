"""Phase 25 crash-window coverage. Synthetic records; no external services."""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem.calendar_writer import FakeCalendarClient
from cec_vivisystem.google_token_reminder import (
    JsonDirGoogleTokenReminderStore,
    maintain_google_token_reminder_storage,
    run_google_token_reminder,
)
from cec_vivisystem.important_dates import (
    InMemoryImportantDatesStore,
    JsonDirImportantDatesPostStore,
    maintain_important_dates_post_storage,
    run_important_dates_review,
)
from cec_vivisystem.models import ImportantDate, ImportantDateKind, SlackPostReceipt
from cec_vivisystem.morning_recap import (
    FakeSlackPoster,
    JsonDirMorningRecapStore,
    SlackWebPoster,
    maintain_morning_recap_storage,
    run_morning_recap,
)

NOW = datetime(2026, 9, 28, 10, tzinfo=ZoneInfo("Asia/Hong_Kong"))
CHANNEL = "C_SYNTHETIC"


@pytest.fixture(params=["recap", "token", "dates"])
def delivery(request, tmp_path):
    kind = request.param
    dates = InMemoryImportantDatesStore()
    if kind == "recap":
        store_type = JsonDirMorningRecapStore
        maintain = maintain_morning_recap_storage
    elif kind == "token":
        store_type = JsonDirGoogleTokenReminderStore
        maintain = maintain_google_token_reminder_storage
    else:
        store_type = JsonDirImportantDatesPostStore
        maintain = maintain_important_dates_post_storage
        for name in ("a", "b"):
            dates.save(
                ImportantDate(
                    date_id=name,
                    title=f"Synthetic {name}",
                    month=9,
                    day=29,
                    kind=ImportantDateKind.YEARLY,
                    created_at=NOW,
                )
            )
    keys = (
        [(name, NOW.date() + timedelta(days=1)) for name in ("a", "b")]
        if kind == "dates"
        else [(NOW.date(),)]
    )

    def run(poster, store=None):
        store = store if store is not None else store_type(tmp_path)
        common = {"now": NOW, "poster": poster, "channel_id": CHANNEL}
        if kind == "recap":
            return run_morning_recap(
                **common,
                client=FakeCalendarClient(),
                store=store,
            )
        if kind == "token":
            return run_google_token_reminder(
                **common,
                issued_at=NOW - timedelta(days=4),
                post_store=store,
            )
        return run_important_dates_review(
            **common,
            dates_store=dates,
            post_store=store,
        )

    return run, store_type, keys, tmp_path, maintain


def test_success_persists_response_handle_and_skips(delivery):
    run, store_type, keys, root, _ = delivery
    poster = FakeSlackPoster()
    assert run(poster).outcome.value == "posted"
    store = store_type(root)
    rows = [store.read_delivery(key) for key in keys]
    assert len({row["attempt_id"] for row in rows}) == 1
    for row in rows:
        assert row["status"] == "posted"
        assert row["slack_channel_id"] == CHANNEL
        assert row["slack_ts"] == "1.000001"
        assert row["attempted_at"] == NOW.isoformat()
        assert row["batch_keys"] == [[str(part) for part in key] for key in keys]
        assert "text" not in row
    assert run(poster).outcome.value.startswith("skipped")
    assert len(poster.calls) == 1


def test_accepted_then_response_lost_never_reposts(delivery, capsys):
    run, store_type, keys, root, _ = delivery
    poster = FakeSlackPoster(fail_with=TimeoutError("response lost after acceptance"))
    assert run(poster).outcome.value == "failed"
    for key in keys:
        assert store_type(root).read_delivery(key)["status"] == "pending"
        assert not store_type(root).has_posted(*key)
    retry = FakeSlackPoster()
    result = run(retry)
    assert result.outcome.value == "failed"
    assert result.error_type == "ReconciliationRequired"
    assert retry.calls == []
    assert "scheduled_delivery_reconciliation_required" in capsys.readouterr().out


@pytest.mark.parametrize("stage", ["before_post", "after_response"])
def test_process_crash_leaves_durable_block(delivery, stage):
    run, store_type, keys, root, _ = delivery
    poster = FakeSlackPoster()

    class CrashingStore(store_type):
        def write_delivery(self, key, payload):
            if stage == "after_response" and payload["status"] == "posted":
                raise SystemExit("injected crash after response")
            super().write_delivery(key, payload)
            if stage == "before_post" and key == keys[-1]:
                raise SystemExit("injected crash before post")

    with pytest.raises(SystemExit):
        run(poster, CrashingStore(root))
    assert len(poster.calls) == (stage == "after_response")
    retry = FakeSlackPoster()
    assert run(retry).outcome.value == "failed"
    assert retry.calls == []


def test_reservation_failure_prevents_slack_call(delivery):
    run, store_type, _, root, _ = delivery

    class BrokenStore(store_type):
        def write_delivery(self, key, payload):
            raise OSError("injected disk failure")

    poster = FakeSlackPoster()
    assert run(poster, BrokenStore(root)).outcome.value == "failed"
    assert poster.calls == []
    assert list(root.glob("*.json")) == []


def test_final_save_failure_is_failed_and_blocks_restart(delivery, capsys):
    run, store_type, keys, root, _ = delivery

    class BrokenStore(store_type):
        def write_delivery(self, key, payload):
            if payload["status"] == "posted":
                raise OSError("injected final-save failure")
            super().write_delivery(key, payload)

    poster = FakeSlackPoster()
    result = run(poster, BrokenStore(root))
    assert result.outcome.value == "failed"
    assert len(poster.calls) == 1
    assert all(
        store_type(root).read_delivery(key)["status"] == "pending" for key in keys
    )
    assert run(poster).outcome.value == "failed"
    assert len(poster.calls) == 1
    assert "1.000001" in capsys.readouterr().out


@pytest.mark.parametrize("delivery", ["dates"], indirect=True)
@pytest.mark.parametrize("stage", ["pending", "posted"])
def test_partial_batch_failure_blocks_remaining_occurrences(delivery, stage):
    run, store_type, keys, root, _ = delivery

    class PartialStore(store_type):
        def write_delivery(self, key, payload):
            if key == keys[1] and payload["status"] == stage:
                raise OSError("injected partial-batch failure")
            super().write_delivery(key, payload)

    poster = FakeSlackPoster()
    assert run(poster, PartialStore(root)).outcome.value == "failed"
    assert len(poster.calls) == (stage == "posted")
    before = list(poster.calls)
    assert run(poster).outcome.value == "failed"
    assert poster.calls == before
    row = store_type(root).read_delivery(keys[0])
    assert len(row["batch_keys"]) == 2
    if stage == "posted":
        assert row["slack_ts"] == "1.000001"


def test_legacy_markers_still_skip(delivery):
    run, store_type, keys, root, _ = delivery
    store = store_type(root)
    for key in keys:
        metadata = (
            {"days_left": 3} if store_type is JsonDirGoogleTokenReminderStore else {}
        )
        store.mark_posted(*key, posted_at=NOW, channel_id=CHANNEL, **metadata)
    poster = FakeSlackPoster()
    assert run(poster).outcome.value.startswith("skipped")
    assert poster.calls == []


@pytest.mark.parametrize("response", [None, SlackPostReceipt("", ""), {"ts": "1"}])
def test_missing_handle_is_ambiguous(delivery, response):
    run, _, _, _, _ = delivery

    class BadPoster(FakeSlackPoster):
        def post(self, **kwargs):
            super().post(**kwargs)
            return response

    poster = BadPoster()
    assert run(poster).outcome.value == "failed"
    assert run(poster).outcome.value == "failed"
    assert len(poster.calls) == 1


@pytest.mark.parametrize(
    "contents",
    [
        "{bad json",
        "[]",
        "{}",
        '{"status":"unknown"}',
        '{"status":"posted","posted_at":"2026-09-28"}',
    ],
)
def test_damaged_marker_is_not_absence(delivery, contents):
    run, store_type, keys, root, _ = delivery
    path = store_type(root)._path(*keys[0])
    path.write_text(contents)
    poster = FakeSlackPoster()
    assert run(poster).outcome.value == "failed"
    assert poster.calls == []
    assert path.read_text() == contents


def test_pending_and_posted_share_existing_retention(delivery):
    run, store_type, keys, root, maintain = delivery
    assert run(FakeSlackPoster(fail_with=TimeoutError())).outcome.value == "failed"
    store = store_type(root)
    cutoff_now = NOW + timedelta(days=31)
    # Important-date retention is keyed by occurrence (tomorrow), not attempt day.
    assert maintain(store, now=cutoff_now + timedelta(days=1)) == len(keys)
    assert list(root.glob("*.json")) == []


def test_web_poster_retains_handle_and_disables_sdk_retries(monkeypatch):
    import slack_sdk

    calls = []

    class FakeWebClient:
        def __init__(self, *, token, retry_handlers):
            assert token == "synthetic-token"
            assert retry_handlers == []

        def chat_postMessage(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "channel": CHANNEL, "ts": "123.456"}

    monkeypatch.setattr(slack_sdk, "WebClient", FakeWebClient)
    receipt = SlackWebPoster("synthetic-token").post(
        channel_id=CHANNEL, text="Synthetic"
    )
    assert receipt == SlackPostReceipt(CHANNEL, "123.456")
    assert calls == [{"channel": CHANNEL, "text": "Synthetic"}]


def test_manual_reconciliation_can_finalize_without_reposting(tmp_path):
    store = JsonDirMorningRecapStore(tmp_path)
    poster = FakeSlackPoster(fail_with=TimeoutError())
    args = {
        "now": NOW,
        "client": FakeCalendarClient(),
        "channel_id": CHANNEL,
        "store": store,
    }
    assert run_morning_recap(poster=poster, **args).outcome.value == "failed"
    key = (NOW.date(),)
    row = store.read_delivery(key)
    store.write_delivery(
        key,
        {
            **row,
            "status": "posted",
            "posted_at": NOW.isoformat(),
            "slack_channel_id": CHANNEL,
            "slack_ts": "123.456",
        },
    )
    assert (
        run_morning_recap(poster=poster, **args).outcome.value
        == "skipped_already_posted"
    )
    assert len(poster.calls) == 1
    assert json.loads(store._path(*key).read_text())["slack_ts"] == "123.456"
