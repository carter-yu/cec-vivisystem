"""Alert policy v2: hysteresis for heartbeat alerts (Phase 30a, ADR 0014).

Why: the Phase 29 checker posted on every ok↔not-ok edge. A stuck SDK
connection that "recovered" for under 30 s about 100 times a day made the
verdict flip at every check, and each flip became a post.

Contract (defaults in ``AlertPolicy``):
- Open an outage after 2 consecutive unhealthy checks AND an outage age of at
  least 15 min. A second outage within 6 h posts "unstable" instead.
- Close it after 3 consecutive healthy checks AND 15 min healthy (60 min when
  the outage was posted as unstable). "Recovered" posts only after an outage
  post. A relapse while recovering returns to ``alerted`` without a post.
- Remind every 6 h while an outage stays open. Keep at least 30 min between
  these posts; a blocked post waits for the next check.
- ``evaluate_alert`` is pure. The caller posts, and saves the returned state
  only after Slack accepts the post, so a failed post is retried next run.

What not to do: never add a restart, kill or reconnect action here. The
checker observes and reports only; the listener owns its single self-exit.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from cec_vivisystem.status_messages import (
    format_outage,
    format_recovered,
    format_still_down,
    format_unstable,
)

STATE_VERSION = 2


@dataclass(frozen=True, slots=True)
class AlertPolicy:
    open_checks: int = 2
    open_after_s: float = 15 * 60
    close_checks: int = 3
    close_after_s: float = 15 * 60
    unstable_close_after_s: float = 60 * 60
    unstable_window_s: float = 6 * 3600
    reminder_every_s: float = 6 * 3600
    min_gap_s: float = 30 * 60


class AlertPhase(StrEnum):
    OK = "ok"
    SUSPECT = "suspect"
    ALERTED = "alerted"
    RECOVERING = "recovering"


class AlertKind(StrEnum):
    OUTAGE = "outage"
    UNSTABLE = "unstable"
    STILL_DOWN = "still_down"
    RECOVERED = "recovered"


@dataclass(frozen=True, slots=True)
class CheckView:
    """The parts of ``health.HealthCheck`` the policy needs."""

    healthy: bool
    reason: str
    pid: int | None = None
    outage_started_at: datetime | None = None
    exit_reason: str | None = None


@dataclass(frozen=True, slots=True)
class AlertState:
    phase: AlertPhase = AlertPhase.OK
    unhealthy_streak: int = 0
    healthy_streak: int = 0
    outage_started_at: datetime | None = None
    outage_reason: str | None = None
    outage_pid: int | None = None
    outage_unstable: bool = False
    alerted_at: datetime | None = None
    last_reminder_at: datetime | None = None
    healthy_since: datetime | None = None
    last_post_at: datetime | None = None
    outage_opens: tuple[datetime, ...] = field(default_factory=tuple)
    digest_posted_for: str | None = None
    updated_at: datetime | None = None

    def to_json(self) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value is not None else None

        return {
            "version": STATE_VERSION,
            "phase": self.phase.value,
            "unhealthy_streak": self.unhealthy_streak,
            "healthy_streak": self.healthy_streak,
            "outage_started_at": iso(self.outage_started_at),
            "outage_reason": self.outage_reason,
            "outage_pid": self.outage_pid,
            "outage_unstable": self.outage_unstable,
            "alerted_at": iso(self.alerted_at),
            "last_reminder_at": iso(self.last_reminder_at),
            "healthy_since": iso(self.healthy_since),
            "last_post_at": iso(self.last_post_at),
            "outage_opens": [t.isoformat() for t in self.outage_opens],
            "digest_posted_for": self.digest_posted_for,
            "updated_at": iso(self.updated_at),
            # Phase 29 readers only know this key.
            "alerted": self.phase in (AlertPhase.ALERTED, AlertPhase.RECOVERING),
        }


class StateFormatError(ValueError):
    """The state file is neither v2 nor a usable v1 record."""


def _dt(raw: object, *, required: bool = False) -> datetime | None:
    if raw is None and not required:
        return None
    if not isinstance(raw, str):
        raise StateFormatError("timestamp is not a string")
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        raise StateFormatError("timestamp has no timezone")
    return parsed


def _int(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise StateFormatError("counter is not a non-negative integer")
    return raw


def state_from_json(raw: object) -> AlertState:
    """Parse v2, or migrate a Phase 29 v1 ``{"alerted": bool}`` record.

    Raises ``StateFormatError`` (a ``ValueError``) for anything else.
    """
    if not isinstance(raw, dict):
        raise StateFormatError("state is not an object")
    try:
        if raw.get("version") == STATE_VERSION:
            pid = raw.get("outage_pid")
            opens = raw.get("outage_opens") or []
            digest = raw.get("digest_posted_for")
            reason = raw.get("outage_reason")
            if not isinstance(opens, list):
                raise StateFormatError("outage_opens is not a list")
            if digest is not None and not isinstance(digest, str):
                raise StateFormatError("digest_posted_for is not a string")
            return AlertState(
                phase=AlertPhase(raw.get("phase")),
                unhealthy_streak=_int(raw.get("unhealthy_streak", 0)),
                healthy_streak=_int(raw.get("healthy_streak", 0)),
                outage_started_at=_dt(raw.get("outage_started_at")),
                outage_reason=reason if isinstance(reason, str) else None,
                outage_pid=pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
                outage_unstable=bool(raw.get("outage_unstable", False)),
                alerted_at=_dt(raw.get("alerted_at")),
                last_reminder_at=_dt(raw.get("last_reminder_at")),
                healthy_since=_dt(raw.get("healthy_since")),
                last_post_at=_dt(raw.get("last_post_at")),
                outage_opens=tuple(_dt(t, required=True) for t in opens),  # type: ignore[misc]
                digest_posted_for=digest,
                updated_at=_dt(raw.get("updated_at")),
            )
        if "version" not in raw and isinstance(raw.get("alerted"), bool):
            if not raw["alerted"]:
                return AlertState()
            # v1 alerted: an outage post was sent at updated_at. Keep that as
            # the open outage so recovery still posts exactly once.
            try:
                opened = _dt(raw.get("updated_at"))
            except (StateFormatError, ValueError):
                opened = None
            reason = raw.get("reason")
            return AlertState(
                phase=AlertPhase.ALERTED,
                unhealthy_streak=1,
                outage_started_at=opened,
                outage_reason=reason if isinstance(reason, str) else None,
                alerted_at=opened,
                last_reminder_at=opened,
                last_post_at=opened,
                outage_opens=(opened,) if opened is not None else (),
            )
    except (TypeError, ValueError) as exc:
        raise StateFormatError(str(exc)) from exc
    raise StateFormatError("unknown state version")


@dataclass(frozen=True, slots=True)
class AlertAction:
    kind: AlertKind
    text: str


@dataclass(frozen=True, slots=True)
class AlertDecision:
    """``state`` is final when ``action`` is None; else save it only after the post."""

    state: AlertState
    action: AlertAction | None = None
    suppressed: str | None = None  # hysteresis | min_gap | relapse
    opened: bool = False
    closed: bool = False


def _clear_outage(state: AlertState) -> AlertState:
    return replace(
        state,
        phase=AlertPhase.OK,
        unhealthy_streak=0,
        healthy_streak=0,
        outage_started_at=None,
        outage_reason=None,
        outage_pid=None,
        outage_unstable=False,
        alerted_at=None,
        last_reminder_at=None,
        healthy_since=None,
    )


def evaluate_alert(
    state: AlertState,
    check: CheckView,
    *,
    now: datetime,
    policy: AlertPolicy | None = None,
) -> AlertDecision:
    """Advance the state machine by one check. Pure; no I/O."""
    p = policy if policy is not None else AlertPolicy()
    s = replace(state, updated_at=now)
    # Repair partial records (for example a v1 file without ``updated_at``)
    # instead of failing the checker.
    if s.phase != AlertPhase.OK and s.outage_started_at is None:
        s = replace(s, outage_started_at=now)
    if s.phase == AlertPhase.RECOVERING and s.healthy_since is None:
        s = replace(s, healthy_since=now)

    def gap_blocked() -> bool:
        return s.last_post_at is not None and (
            (now - s.last_post_at).total_seconds() < p.min_gap_s
        )

    if not check.healthy:
        start = check.outage_started_at or now
        relapse = False
        if s.phase == AlertPhase.OK:
            s = replace(
                s,
                phase=AlertPhase.SUSPECT,
                unhealthy_streak=1,
                healthy_streak=0,
                healthy_since=None,
                outage_started_at=start,
            )
        elif s.phase == AlertPhase.RECOVERING:
            relapse = True
            s = replace(
                s,
                phase=AlertPhase.ALERTED,
                unhealthy_streak=1,
                healthy_streak=0,
                healthy_since=None,
            )
        else:
            known = s.outage_started_at or start
            s = replace(
                s,
                unhealthy_streak=s.unhealthy_streak + 1,
                healthy_streak=0,
                healthy_since=None,
                outage_started_at=min(known, start)
                if s.phase == AlertPhase.SUSPECT
                else known,
            )
        s = replace(s, outage_reason=check.reason, outage_pid=check.pid)

        if s.phase == AlertPhase.SUSPECT:
            age = (now - s.outage_started_at).total_seconds()
            if s.unhealthy_streak < p.open_checks or age < p.open_after_s:
                return AlertDecision(s, suppressed="hysteresis")
            if gap_blocked():
                return AlertDecision(s, suppressed="min_gap")
            recent = tuple(
                t for t in s.outage_opens
                if (now - t).total_seconds() < p.unstable_window_s
            )
            unstable = bool(recent)
            if unstable:
                kind = AlertKind.UNSTABLE
                text = format_unstable(
                    count=len(recent) + 1,
                    window_hours=int(p.unstable_window_s // 3600),
                    started_at=s.outage_started_at,
                    now=now,
                    recovery_minutes=int(p.unstable_close_after_s // 60),
                    pid=check.pid,
                )
            else:
                kind = AlertKind.OUTAGE
                text = format_outage(
                    reason=check.reason,
                    started_at=s.outage_started_at,
                    now=now,
                    pid=check.pid,
                    exit_reason=check.exit_reason,
                )
            s = replace(
                s,
                phase=AlertPhase.ALERTED,
                outage_unstable=unstable,
                alerted_at=now,
                last_reminder_at=now,
                last_post_at=now,
                outage_opens=(*recent, now),
            )
            return AlertDecision(s, AlertAction(kind, text), opened=True)

        # ALERTED (possibly just relapsed): remind every reminder_every_s.
        last = s.last_reminder_at or s.alerted_at or now
        if (now - last).total_seconds() >= p.reminder_every_s:
            if gap_blocked():
                return AlertDecision(s, suppressed="min_gap")
            text = format_still_down(
                reason=check.reason,
                started_at=s.outage_started_at,
                now=now,
                pid=check.pid,
            )
            s = replace(s, last_reminder_at=now, last_post_at=now)
            return AlertDecision(s, AlertAction(AlertKind.STILL_DOWN, text))
        return AlertDecision(s, suppressed="relapse" if relapse else None)

    # Healthy check.
    if s.phase == AlertPhase.OK:
        recent = tuple(
            t for t in s.outage_opens if (now - t).total_seconds() < p.unstable_window_s
        )
        return AlertDecision(replace(s, outage_opens=recent))
    if s.phase == AlertPhase.SUSPECT:
        # A brief problem that never met the open rule: no post.
        return AlertDecision(_clear_outage(s), suppressed="hysteresis")
    if s.phase == AlertPhase.ALERTED:
        s = replace(
            s,
            phase=AlertPhase.RECOVERING,
            unhealthy_streak=0,
            healthy_streak=1,
            healthy_since=now,
        )
        return AlertDecision(s, suppressed="hysteresis")

    # RECOVERING
    s = replace(s, healthy_streak=s.healthy_streak + 1)
    required = p.unstable_close_after_s if s.outage_unstable else p.close_after_s
    healthy_for = (now - s.healthy_since).total_seconds()
    if s.healthy_streak < p.close_checks or healthy_for < required:
        return AlertDecision(s, suppressed="hysteresis")
    if gap_blocked():
        return AlertDecision(s, suppressed="min_gap")
    text = format_recovered(
        reason=s.outage_reason or "",
        started_at=s.outage_started_at,
        restored_at=s.healthy_since,
        now=now,
        pid=check.pid,
    )
    s = replace(_clear_outage(s), last_post_at=now)
    return AlertDecision(s, AlertAction(AlertKind.RECOVERED, text), closed=True)
