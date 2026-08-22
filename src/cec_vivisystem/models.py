"""Shared domain models for cec-vivisystem components."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class IntentType(str, Enum):
    """Structured intent kinds produced by the Parser (Phase 1)."""

    CREATE_EVENT = "create_event"
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
