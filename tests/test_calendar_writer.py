"""Phase 6 unit tests for Calendar Writer (offline, no Google/Slack/LLM)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from cec_vivisystem.calendar_writer import (
    FakeCalendarClient,
    InMemoryCalendarAuditStore,
    purge_calendar_audit,
    write_calendar_create,
)
from cec_vivisystem.confirmation import (
    InMemoryConfirmationStore,
    create_confirmation,
    expire_due_confirmations,
    resolve_confirmation,
)
from cec_vivisystem.models import (
    CalendarAuditRecord,
    CalendarWriteOutcome,
    ConfirmationDecision,
    ConfirmationStatus,
)
from cec_vivisystem.parser import parse

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
FIXED_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=FAMILY_TZ)
TTL = timedelta(hours=1)
F1 = "星期六下午3點帶 Cedric 去游泳"
CAL_ID = "cal-test"


def _accepted_confirmation():
    store = InMemoryConfirmationStore()
    parsed = parse(F1, now=FIXED_NOW)
    conf = create_confirmation(
        parsed,
        store=store,
        now=FIXED_NOW,
        correlation_id="corr-w1",
        ttl=TTL,
    )
    return resolve_confirmation(
        conf.confirmation_id,
        ConfirmationDecision.ACCEPT,
        store=store,
        now=FIXED_NOW + timedelta(minutes=5),
        actor="U_PARENT",
    )


def test_second_write_same_confirmation_skips_create() -> None:
    """W1 (Phase 13): two accepts → one create; second is already_created."""
    audit = InMemoryCalendarAuditStore()
    conf = _accepted_confirmation()
    client = FakeCalendarClient()
    first = write_calendar_create(
        conf,
        client=client,
        calendar_id=CAL_ID,
        audit_store=audit,
        now=FIXED_NOW,
    )
    second = write_calendar_create(
        conf,
        client=client,
        calendar_id=CAL_ID,
        audit_store=audit,
        now=FIXED_NOW + timedelta(minutes=1),
    )
    assert first.outcome == CalendarWriteOutcome.SUCCESS
    assert second.outcome == CalendarWriteOutcome.ALREADY_CREATED
    assert second.calendar_event_id == first.calendar_event_id
    assert len(client.calls) == 1


def test_accepted_confirmation_creates_event() -> None:
    """W1: accepted confirmation → create called with title/start; id passed."""
    conf = _accepted_confirmation()
    client = FakeCalendarClient()
    result = write_calendar_create(
        conf,
        client=client,
        calendar_id=CAL_ID,
        now=FIXED_NOW,
    )
    assert result.outcome == CalendarWriteOutcome.SUCCESS
    assert result.op == "create"
    assert result.confirmation_id == conf.confirmation_id
    assert result.calendar_event_id == "evt_fake_1"
    assert result.calendar_id == CAL_ID
    assert len(client.calls) == 1
    draft = client.calls[0]
    assert draft.confirmation_id == conf.confirmation_id
    assert draft.calendar_id == CAL_ID
    assert draft.summary
    assert "游" in draft.summary or "swim" in draft.summary.lower()
    assert draft.start.tzinfo is not None
    assert draft.start.astimezone(FAMILY_TZ) == datetime(
        2026, 8, 8, 15, 0, tzinfo=FAMILY_TZ
    )
    assert draft.time_zone == "Asia/Hong_Kong"
    assert "Cedric" in (draft.description or "")
    assert draft.attendees == []


def test_non_accepted_confirmation_refuses_without_google_call() -> None:
    """W2: pending / rejected / expired → refuse; no Google call."""
    store = InMemoryConfirmationStore()
    parsed = parse(F1, now=FIXED_NOW)
    pending = create_confirmation(
        parsed, store=store, now=FIXED_NOW, ttl=TTL, correlation_id="corr-w2"
    )
    client = FakeCalendarClient()
    pending_result = write_calendar_create(
        pending, client=client, calendar_id=CAL_ID, now=FIXED_NOW
    )
    assert pending_result.outcome == CalendarWriteOutcome.REFUSED
    assert pending_result.error_type == "not_accepted"
    assert client.calls == []

    rejected = resolve_confirmation(
        pending.confirmation_id,
        ConfirmationDecision.REJECT,
        store=store,
        now=FIXED_NOW,
    )
    rejected_result = write_calendar_create(
        rejected, client=client, calendar_id=CAL_ID, now=FIXED_NOW
    )
    assert rejected_result.outcome == CalendarWriteOutcome.REFUSED
    assert client.calls == []

    store2 = InMemoryConfirmationStore()
    other = create_confirmation(parsed, store=store2, now=FIXED_NOW, ttl=TTL)
    expire_due_confirmations(store=store2, now=FIXED_NOW + TTL + timedelta(seconds=1))
    expired = store2.get(other.confirmation_id)
    assert expired is not None
    assert expired.status == ConfirmationStatus.EXPIRED
    expired_result = write_calendar_create(
        expired, client=client, calendar_id=CAL_ID, now=FIXED_NOW
    )
    assert expired_result.outcome == CalendarWriteOutcome.REFUSED
    assert client.calls == []


def test_missing_confirmation_id_refuses_loudly(capsys) -> None:
    """W3: missing confirmation id → refuse; loud log; no success; no Google."""
    conf = replace(_accepted_confirmation(), confirmation_id="")
    client = FakeCalendarClient()
    result = write_calendar_create(
        conf, client=client, calendar_id=CAL_ID, now=FIXED_NOW
    )
    assert result.outcome == CalendarWriteOutcome.REFUSED
    assert result.error_type == "missing_confirmation_id"
    assert result.calendar_event_id is None
    assert client.calls == []
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "write_without_confirmation_id" in combined


def test_failed_write_can_retry_same_confirmation() -> None:
    """A failed create is not treated as already_created; retry may insert."""
    audit = InMemoryCalendarAuditStore()
    conf = _accepted_confirmation()
    first = write_calendar_create(
        conf,
        client=FakeCalendarClient(fail_with=RuntimeError("boom")),
        calendar_id=CAL_ID,
        audit_store=audit,
        now=FIXED_NOW,
    )
    assert first.outcome == CalendarWriteOutcome.FAILED
    client = FakeCalendarClient()
    second = write_calendar_create(
        conf,
        client=client,
        calendar_id=CAL_ID,
        audit_store=audit,
        now=FIXED_NOW + timedelta(minutes=1),
    )
    assert second.outcome == CalendarWriteOutcome.SUCCESS
    assert len(client.calls) == 1


def test_google_api_error_returns_failed_without_crash() -> None:
    """W4: fake Google API error → failed result; process does not crash."""
    conf = _accepted_confirmation()
    client = FakeCalendarClient(fail_with=RuntimeError("Google Calendar API 403"))
    result = write_calendar_create(
        conf, client=client, calendar_id=CAL_ID, now=FIXED_NOW
    )
    assert result.outcome == CalendarWriteOutcome.FAILED
    assert result.error_type == "RuntimeError"
    assert "403" in (result.error_message or "")
    assert result.calendar_event_id is None
    assert len(client.calls) == 1


def test_calendar_write_result_contract_fields() -> None:
    """W6: CalendarWriteResult exposes Phase 6 contract fields."""
    conf = _accepted_confirmation()
    result = write_calendar_create(
        conf, client=FakeCalendarClient(), calendar_id=CAL_ID, now=FIXED_NOW
    )
    for name in (
        "outcome",
        "op",
        "confirmation_id",
        "calendar_id",
        "calendar_event_id",
        "error_type",
        "error_message",
        "duration_ms",
    ):
        assert hasattr(result, name), f"missing {name}"
    assert result.outcome.value == "success"
    assert result.op == "create"


def test_audit_records_attempt_and_purge_after_90d() -> None:
    """W7: attempt+result stored; purge deletes rows older than 90d."""
    audit = InMemoryCalendarAuditStore()
    conf = _accepted_confirmation()
    ok = write_calendar_create(
        conf,
        client=FakeCalendarClient(),
        calendar_id=CAL_ID,
        audit_store=audit,
        now=FIXED_NOW,
    )
    assert ok.outcome == CalendarWriteOutcome.SUCCESS
    other = _accepted_confirmation()
    fail = write_calendar_create(
        other,
        client=FakeCalendarClient(fail_with=RuntimeError("boom")),
        calendar_id=CAL_ID,
        audit_store=audit,
        now=FIXED_NOW,
    )
    assert fail.outcome == CalendarWriteOutcome.FAILED
    rows = audit.list_all()
    assert len(rows) == 2
    outcomes = {r.outcome for r in rows}
    assert CalendarWriteOutcome.SUCCESS in outcomes
    assert CalendarWriteOutcome.FAILED in outcomes
    assert {r.confirmation_id for r in rows} == {
        conf.confirmation_id,
        other.confirmation_id,
    }
    assert all(r.op == "create" for r in rows)

    old = CalendarAuditRecord(
        audit_id="old-audit",
        attempted_at=FIXED_NOW - timedelta(days=91),
        op="create",
        confirmation_id="old-conf",
        correlation_id="corr-old",
        outcome=CalendarWriteOutcome.SUCCESS,
        calendar_id=CAL_ID,
        calendar_event_id="evt-old",
        title="old",
        start=FIXED_NOW,
    )
    audit.save(old)
    removed = purge_calendar_audit(store=audit, now=FIXED_NOW, retention=timedelta(days=90))
    assert removed == 1
    remaining_ids = {r.audit_id for r in audit.list_all()}
    assert "old-audit" not in remaining_ids
    assert len(remaining_ids) == 2


def test_calendar_writer_logs_boundary(capsys) -> None:
    """W8: accepted write completes with write_attempt / write_succeeded."""
    conf = _accepted_confirmation()
    result = write_calendar_create(
        conf, client=FakeCalendarClient(), calendar_id=CAL_ID, now=FIXED_NOW
    )
    assert result.outcome == CalendarWriteOutcome.SUCCESS
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "write_attempt" in combined
    assert "write_succeeded" in combined
