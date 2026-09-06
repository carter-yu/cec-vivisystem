"""Phase 4 unit tests for Confirmation Guardian (offline, no Slack/Google)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem.calendar_writer import FakeCalendarClient
from cec_vivisystem.confirmation import (
    ConfirmationError,
    InMemoryConfirmationStore,
    build_proposal,
    create_confirmation,
    expire_due_confirmations,
    purge_confirmations,
    resolve_confirmation,
)
from cec_vivisystem.models import (
    CalendarListedEvent,
    ConfirmationDecision,
    ConfirmationStatus,
    IntentType,
)
from cec_vivisystem.overlap import detect_create_overlaps
from cec_vivisystem.parser import parse

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
FIXED_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=FAMILY_TZ)
TTL = timedelta(hours=1)
F1 = "星期六下午3點帶 Cedric 去游泳"


def _create_event_parse():
    return parse(F1, now=FIXED_NOW)


def test_build_proposal_create_event_no_calendar_claim() -> None:
    """C1: proposal summarizes create_event and never claims a write."""
    result = _create_event_parse()
    text = build_proposal(result)
    assert text
    assert result.title is None or result.title in text or "Title" in text
    assert result.start is None or "2026-08-08" in text or "Start" in text
    lower = text.lower()
    assert "no calendar change" in lower or "until you confirm" in lower
    assert "added to google" not in lower
    assert "written to calendar" not in lower or "will be made" in lower


def test_create_confirmation_pending() -> None:
    """C2: create_event → pending with expiry = created + ttl."""
    store = InMemoryConfirmationStore()
    parsed = _create_event_parse()
    conf = create_confirmation(
        parsed,
        store=store,
        now=FIXED_NOW,
        correlation_id="corr-c2",
        ttl=TTL,
    )
    assert conf.status == ConfirmationStatus.PENDING
    assert conf.confirmation_id
    assert conf.correlation_id == "corr-c2"
    assert conf.created_at == FIXED_NOW
    assert conf.expires_at == FIXED_NOW + TTL
    assert conf.proposal_text
    assert store.get(conf.confirmation_id) is not None


def test_proposal_overlap_hits_are_bilingual() -> None:
    """C1 (Phase 13): overlap hits show 撞期 plus times and titles."""
    parsed = _create_event_parse()
    client = FakeCalendarClient(
        listed_events=[
            CalendarListedEvent(
                event_id="e-dentist",
                summary="牙醫",
                start=datetime(2026, 8, 8, 15, 30, tzinfo=FAMILY_TZ),
                end=datetime(2026, 8, 8, 16, 30, tzinfo=FAMILY_TZ),
                all_day=False,
                participants=["Cedric"],
            )
        ]
    )
    check = detect_create_overlaps(parsed, client=client, calendar_id="cal-test")
    text = build_proposal(parsed, overlap_check=check)
    assert "撞期" in text
    assert "牙醫" in text
    assert "15:30" in text
    assert "16:30" in text
    assert "Warning:" in text
    assert "added to google" not in text.lower()


def test_proposal_overlap_fail_still_warns() -> None:
    """C2 (Phase 13): list fail → degraded 撞期 warning; still a proposal."""
    parsed = _create_event_parse()
    check = detect_create_overlaps(
        parsed,
        client=FakeCalendarClient(fail_list_with=RuntimeError("Google 403")),
        calendar_id="cal-test",
    )
    text = build_proposal(parsed, overlap_check=check)
    assert "撞期" in text
    assert "could not check" in text.lower()
    assert "yes" in text.lower()
    assert "Warning:" in text


def test_proposal_no_overlap_has_no_conflict_warning() -> None:
    """C3 (Phase 13): no hits → no 撞期 line."""
    parsed = _create_event_parse()
    check = detect_create_overlaps(
        parsed,
        client=FakeCalendarClient(),
        calendar_id="cal-test",
    )
    text = build_proposal(parsed, overlap_check=check)
    assert "撞期" not in text
    assert "Warning:" not in text
    assert "yes" in text.lower() or "confirm" in text.lower()


def test_create_confirmation_rejects_non_create_event() -> None:
    """C3: needs_clarification / unknown do not create pending."""
    store = InMemoryConfirmationStore()
    unclear = parse("幫我 book 游泳", now=FIXED_NOW)
    assert unclear.intent_type == IntentType.NEEDS_CLARIFICATION
    with pytest.raises(ConfirmationError):
        create_confirmation(unclear, store=store, now=FIXED_NOW, ttl=TTL)
    assert store.list_all() == []

    unknown = parse("今日天氣點呀", now=FIXED_NOW)
    with pytest.raises(ConfirmationError):
        create_confirmation(unknown, store=store, now=FIXED_NOW, ttl=TTL)
    assert store.list_all() == []


def test_resolve_accept() -> None:
    """C4: accept → accepted with resolved_at."""
    store = InMemoryConfirmationStore()
    conf = create_confirmation(
        _create_event_parse(), store=store, now=FIXED_NOW, ttl=TTL
    )
    later = FIXED_NOW + timedelta(minutes=10)
    updated = resolve_confirmation(
        conf.confirmation_id,
        ConfirmationDecision.ACCEPT,
        store=store,
        now=later,
        actor="U_PARENT",
    )
    assert updated.status == ConfirmationStatus.ACCEPTED
    assert updated.resolved_at == later
    assert updated.resolved_by == "U_PARENT"


def test_resolve_reject() -> None:
    """C5: reject → rejected."""
    store = InMemoryConfirmationStore()
    conf = create_confirmation(
        _create_event_parse(), store=store, now=FIXED_NOW, ttl=TTL
    )
    updated = resolve_confirmation(
        conf.confirmation_id,
        "reject",
        store=store,
        now=FIXED_NOW + timedelta(minutes=5),
    )
    assert updated.status == ConfirmationStatus.REJECTED


def test_expire_due_confirmations() -> None:
    """C6: pending past expires_at → expired."""
    store = InMemoryConfirmationStore()
    conf = create_confirmation(
        _create_event_parse(), store=store, now=FIXED_NOW, ttl=TTL
    )
    after = FIXED_NOW + TTL + timedelta(seconds=1)
    expired = expire_due_confirmations(store=store, now=after)
    assert len(expired) == 1
    assert expired[0].confirmation_id == conf.confirmation_id
    assert expired[0].status == ConfirmationStatus.EXPIRED
    assert store.get(conf.confirmation_id).status == ConfirmationStatus.EXPIRED


def test_resolve_idempotent_or_safe_when_terminal() -> None:
    """C7: double accept is idempotent; conflicting resolve raises; no crash."""
    store = InMemoryConfirmationStore()
    conf = create_confirmation(
        _create_event_parse(), store=store, now=FIXED_NOW, ttl=TTL
    )
    resolve_confirmation(
        conf.confirmation_id, ConfirmationDecision.ACCEPT, store=store, now=FIXED_NOW
    )
    again = resolve_confirmation(
        conf.confirmation_id, ConfirmationDecision.ACCEPT, store=store, now=FIXED_NOW
    )
    assert again.status == ConfirmationStatus.ACCEPTED

    with pytest.raises(ConfirmationError):
        resolve_confirmation(
            conf.confirmation_id,
            ConfirmationDecision.REJECT,
            store=store,
            now=FIXED_NOW,
        )

    # Expired then resolve → controlled error
    store2 = InMemoryConfirmationStore()
    c2 = create_confirmation(
        _create_event_parse(), store=store2, now=FIXED_NOW, ttl=TTL
    )
    expire_due_confirmations(store=store2, now=FIXED_NOW + TTL + timedelta(minutes=1))
    with pytest.raises(ConfirmationError):
        resolve_confirmation(
            c2.confirmation_id, ConfirmationDecision.ACCEPT, store=store2, now=FIXED_NOW
        )


def test_confirmation_contract_fields() -> None:
    """C8: Confirmation exposes Phase 4 contract fields."""
    store = InMemoryConfirmationStore()
    conf = create_confirmation(
        _create_event_parse(), store=store, now=FIXED_NOW, ttl=TTL
    )
    for name in (
        "confirmation_id",
        "status",
        "correlation_id",
        "parse_result",
        "proposal_text",
        "created_at",
        "expires_at",
        "resolved_at",
        "resolved_by",
        "channel_id",
        "thread_ts",
    ):
        assert hasattr(conf, name), f"missing {name}"


def test_purge_terminal_confirmations() -> None:
    """C9: terminal older than retention purged; fresh pending kept."""
    store = InMemoryConfirmationStore()
    conf = create_confirmation(
        _create_event_parse(), store=store, now=FIXED_NOW, ttl=TTL
    )
    resolve_confirmation(
        conf.confirmation_id,
        ConfirmationDecision.ACCEPT,
        store=store,
        now=FIXED_NOW + timedelta(minutes=1),
    )

    # Within retention window — keep
    kept = purge_confirmations(
        store=store,
        now=FIXED_NOW + timedelta(days=3),
        terminal_retention=timedelta(days=7),
    )
    assert kept == 0
    assert store.get(conf.confirmation_id) is not None

    # Past terminal + 7d — delete
    removed = purge_confirmations(
        store=store,
        now=FIXED_NOW + timedelta(days=10),
        terminal_retention=timedelta(days=7),
    )
    assert removed == 1
    assert store.get(conf.confirmation_id) is None

    # Fresh pending survives pending_max_age window
    pending = create_confirmation(
        _create_event_parse(),
        store=store,
        now=FIXED_NOW + timedelta(days=10),
        ttl=TTL,
    )
    assert (
        purge_confirmations(
            store=store,
            now=FIXED_NOW + timedelta(days=12),
            pending_max_age=timedelta(days=30),
        )
        == 0
    )
    assert store.get(pending.confirmation_id) is not None


def test_confirmation_logs_boundary() -> None:
    """C10: create + resolve complete with logging configured."""
    store = InMemoryConfirmationStore()
    conf = create_confirmation(
        _create_event_parse(), store=store, now=FIXED_NOW, ttl=TTL
    )
    updated = resolve_confirmation(
        conf.confirmation_id,
        ConfirmationDecision.ACCEPT,
        store=store,
        now=FIXED_NOW + timedelta(minutes=2),
    )
    assert updated.status == ConfirmationStatus.ACCEPTED
