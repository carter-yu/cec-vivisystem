"""Shared domain models for cec-vivisystem components."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class IntentType(str, Enum):
    """Structured intent kinds produced by the Parser (Phase 1)."""

    CREATE_EVENT = "create_event"
    LIST_EVENTS = "list_events"
    HELP = "help"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    """Coarse confidence signal for parse results."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(slots=True)
class ParseResult:
    """Structured output of ``parse()`` — Phase 1 contract fields."""

    intent_type: IntentType
    title: str | None
    start: datetime | None
    end: datetime | None
    all_day: bool
    location: str | None
    participants: list[str] = field(default_factory=list)
    raw_text: str = ""
    confidence: Confidence = Confidence.LOW
    missing_fields: list[str] = field(default_factory=list)
    notes: str | None = None


class ListenerOutcome(str, Enum):
    """Outcomes of the Slack Listener (Phase 2)."""

    REPLIED = "replied"
    IGNORED = "ignored"
    FAILED = "failed"


@dataclass(slots=True)
class InboundMessage:
    """Normalized inbound Slack message — Phase 2 contract fields."""

    text: str
    channel_id: str
    user_id: str
    slack_event_id: str | None = None
    correlation_id: str = ""
    ts: str | None = None
    thread_ts: str | None = None


@dataclass(slots=True)
class ListenerResult:
    """Structured output of listener handling — Phase 2 contract fields."""

    outcome: ListenerOutcome
    correlation_id: str
    ignore_reason: str | None = None
    parse_result: ParseResult | None = None
    reply_text: str | None = None
    error_type: str | None = None
    error_message: str | None = None


class ConfirmationStatus(str, Enum):
    """Lifecycle of a confirmation (Phase 4)."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ConfirmationDecision(str, Enum):
    """Human resolve decisions (Phase 4)."""

    ACCEPT = "accept"
    REJECT = "reject"


@dataclass(slots=True)
class Confirmation:
    """Pending or terminal confirmation — Phase 4 contract fields."""

    confirmation_id: str
    status: ConfirmationStatus
    correlation_id: str
    parse_result: ParseResult
    proposal_text: str
    created_at: datetime
    expires_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    channel_id: str | None = None
    thread_ts: str | None = None


class LifeNoteStatus(str, Enum):
    """Lifecycle of a life note (Phase 5A stores raw capture only)."""

    RAW = "raw"


@dataclass(slots=True)
class LifeNoteSource:
    """Origin of a life note — Slack identifiers (Phase 5A minimum)."""

    channel: str
    message_id: str
    user: str


@dataclass(slots=True)
class LifeNote:
    """Durable raw family life note — Phase 5A contract fields.

    ``raw_text`` is the source of truth. Structured fields (people, emotion,
    location, split events) are deferred to a later enrichment phase.
    """

    note_id: str
    raw_text: str
    created_at: datetime
    status: LifeNoteStatus
    source: LifeNoteSource
    correlation_id: str | None = None


class CalendarWriteOutcome(str, Enum):
    """Result of a calendar write attempt (Phase 6)."""

    SUCCESS = "success"
    REFUSED = "refused"
    FAILED = "failed"


@dataclass(slots=True)
class CalendarEventDraft:
    """Mapped create payload sent to a CalendarClient (not a Google dump)."""

    calendar_id: str
    summary: str
    start: datetime
    end: datetime
    all_day: bool
    time_zone: str
    confirmation_id: str
    location: str | None = None
    description: str | None = None
    attendees: list[str] = field(default_factory=list)
    correlation_id: str | None = None


@dataclass(slots=True)
class CalendarEventCreated:
    """Minimal handle returned by a successful create."""

    event_id: str
    calendar_id: str
    html_link: str | None = None


@dataclass(slots=True)
class CalendarWriteResult:
    """Structured output of ``write_calendar_create`` — Phase 6 contract."""

    outcome: CalendarWriteOutcome
    op: str
    confirmation_id: str | None
    calendar_id: str | None
    calendar_event_id: str | None
    error_type: str | None = None
    error_message: str | None = None
    duration_ms: int = 0


@dataclass(slots=True)
class CalendarAuditRecord:
    """Class B audit row for a calendar write attempt + result (90d)."""

    audit_id: str
    attempted_at: datetime
    op: str
    confirmation_id: str | None
    correlation_id: str | None
    outcome: CalendarWriteOutcome
    calendar_id: str | None
    calendar_event_id: str | None
    title: str | None
    start: datetime | None
    error_type: str | None = None
    error_message: str | None = None


class CalendarListOutcome(str, Enum):
    """Result of a calendar list (Phase 7)."""

    SUCCESS = "success"
    FAILED = "failed"


@dataclass(slots=True)
class CalendarListedEvent:
    """One event from a calendar list (not a local SoT row)."""

    event_id: str
    summary: str | None
    start: datetime
    end: datetime | None
    all_day: bool
    location: str | None = None
    participants: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CalendarListResult:
    """Structured output of ``list_calendar_events`` — Phase 7 contract."""

    outcome: CalendarListOutcome
    calendar_id: str | None
    time_min: datetime | None
    time_max: datetime | None
    events: list[CalendarListedEvent] = field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None
    duration_ms: int = 0


class MorningRecapOutcome(str, Enum):
    """Result of a scheduled today-recap (Phase 12)."""

    POSTED = "posted"
    SKIPPED_ALREADY_POSTED = "skipped_already_posted"
    FAILED = "failed"


@dataclass(slots=True)
class MorningRecapResult:
    """Structured output of ``run_morning_recap`` — Phase 12 contract."""

    outcome: MorningRecapOutcome
    recap_date: date | None = None
    channel_id: str | None = None
    event_count: int = 0
    post_text: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    duration_ms: int = 0


class OverlapCheckOutcome(str, Enum):
    """Result of an overlap check on a create proposal (Phase 8)."""

    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(slots=True)
class OverlapHit:
    """One listed event that intersects the proposed window."""

    event: CalendarListedEvent
    same_person_names: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OverlapCheckResult:
    """Structured output of ``detect_create_overlaps`` — Phase 8 contract."""

    outcome: OverlapCheckOutcome
    calendar_id: str | None = None
    time_min: datetime | None = None
    time_max: datetime | None = None
    hits: list[OverlapHit] = field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None
    duration_ms: int = 0
