"""Read-only status views for the control app (Phase 31a).

Why: the dashboard must say the same thing as the 09:00 daily status. So it
reuses the checker's own readers (``health.check_heartbeat``,
``health.read_alert_state``, ``daily_status`` samples, recap and job states,
``google_token_reminder`` expiry) instead of a second interpretation.

Contract: every function here only reads files under ``data/`` and ``logs/``
and never raises for missing or garbage content: the screen shows "No data"
instead of a 500. Nothing here posts, restarts or writes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from cec_vivisystem import daily_status
from cec_vivisystem.google_token_reminder import (
    days_until_expiry,
    load_issued_at,
    load_ttl_days,
)
from cec_vivisystem.health import (
    HealthCheck,
    HealthStatus,
    check_heartbeat,
    read_alert_state,
    read_heartbeat,
)
from cec_vivisystem.health_policy import AlertPhase, AlertState

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
TOKEN_WARN_DAYS = 2
WINDOW = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class DataPaths:
    data_dir: Path
    log_dir: Path

    @property
    def heartbeat(self) -> Path:
        return self.data_dir / "health" / "listener.json"

    @property
    def alert_state(self) -> Path:
        return self.data_dir / "health" / "alert_state.json"

    @property
    def samples(self) -> Path:
        return self.data_dir / "health" / "samples"

    @property
    def sources(self) -> daily_status.StatusSources:
        return daily_status.StatusSources(
            recap_dir=self.data_dir / "morning_recap", log_dir=self.log_dir
        )


def _aware(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return value.astimezone(FAMILY_TZ) if value.tzinfo is not None else None


@dataclass(frozen=True, slots=True)
class ListenerView:
    state: str  # running | disconnected | stopped | nodata | invalid
    tone: str  # ok | warn | error | unknown
    check: HealthCheck
    pid: int | None = None
    instance_id: str | None = None
    started_at: datetime | None = None
    written_at: datetime | None = None
    outage_since: datetime | None = None
    last_connected_at: datetime | None = None
    stuck_retries: int | None = None
    exit_reason: str | None = None


def listener_view(paths: DataPaths, *, now: datetime) -> ListenerView:
    check = check_heartbeat(paths.heartbeat, now=now)
    raw, _ = read_heartbeat(paths.heartbeat)
    hb: Mapping[str, Any] = raw or {}
    retries = hb.get("stuck_retries")
    common = {
        "check": check,
        "pid": check.pid,
        "instance_id": check.instance_id,
        "started_at": _aware(hb.get("started_at")),
        "written_at": check.written_at.astimezone(FAMILY_TZ) if check.written_at else None,
        "outage_since": (
            check.outage_started_at.astimezone(FAMILY_TZ) if check.outage_started_at else None
        ),
        "last_connected_at": _aware(hb.get("last_connected_at")),
        "stuck_retries": retries if isinstance(retries, int) and not isinstance(retries, bool) else None,
        "exit_reason": check.exit_reason,
    }
    if check.status == HealthStatus.MISSING:
        return ListenerView("nodata", "unknown", **common)
    if check.reason == "too_old":
        return ListenerView("stopped", "error", **common)
    if check.reason == "socket_disconnected":
        return ListenerView("disconnected", "error", **common)
    if check.status != HealthStatus.OK:
        return ListenerView("invalid", "unknown", **common)
    if check.socket_mode_connected is False:
        # Fresh heartbeat, disconnected for less than the alert threshold.
        return ListenerView("disconnected", "warn", **common)
    return ListenerView("running", "ok", **common)


@dataclass(frozen=True, slots=True)
class TokenView:
    state: str  # ok | warn | today | expired | unknown
    tone: str
    days_left: int | None = None
    expires_on: date | None = None
    issued_at: datetime | None = None
    ttl_days: int = 7


def token_view(env: Mapping[str, str], *, now: datetime) -> TokenView:
    issued = load_issued_at(env)
    ttl = load_ttl_days(env)
    if issued is None:
        return TokenView("unknown", "unknown", ttl_days=ttl)
    days_left = days_until_expiry(issued_at=issued, now=now, ttl_days=ttl)
    expires_on = now.astimezone(FAMILY_TZ).date() + timedelta(days=days_left)
    if days_left < 0:
        state, tone = "expired", "error"
    elif days_left == 0:
        state, tone = "today", "error"
    elif days_left <= TOKEN_WARN_DAYS:
        state, tone = "warn", "warn"
    else:
        state, tone = "ok", "ok"
    return TokenView(state, tone, days_left, expires_on, issued, ttl)


@dataclass(frozen=True, slots=True)
class Segment:
    kind: str  # ok | down | gap
    start: datetime
    end: datetime

    def fraction(self, window_start: datetime) -> tuple[float, float]:
        total = WINDOW.total_seconds()
        left = (self.start - window_start).total_seconds() / total
        width = (self.end - self.start).total_seconds() / total
        return max(0.0, left), max(0.0, width)


@dataclass(frozen=True, slots=True)
class ConnectionView:
    summary: daily_status.SampleSummary | None
    segments: list[Segment] = field(default_factory=list)
    outages: list[Segment] = field(default_factory=list)
    window_start: datetime | None = None
    last_check: datetime | None = None
    alerts: list[tuple[datetime, str]] = field(default_factory=list)


def _down(sample: daily_status.Sample) -> bool:
    return sample.status != "ok" or sample.connected is False


def connection_view(paths: DataPaths, *, now: datetime) -> ConnectionView:
    samples = daily_status.load_samples(paths.samples, now=now, days=1)
    summary = daily_status.summarize_samples(samples, now=now)
    start = now - WINDOW
    usable = [s for s in samples if s.checked_at <= now]
    segments: list[Segment] = []

    def add(kind: str, a: datetime, b: datetime) -> None:
        a, b = max(a, start), min(b, now)
        if b <= a:
            return
        if segments and segments[-1].kind == kind and segments[-1].end == a:
            segments[-1] = Segment(kind, segments[-1].start, b)
        else:
            segments.append(Segment(kind, a, b))

    in_window = [s for s in usable if s.checked_at >= start]
    before = [s for s in usable if s.checked_at < start][-1:]
    sequence = before + in_window
    if sequence:
        add("gap", start, sequence[0].checked_at)
    for a, b in pairwise(sequence):
        gap = (b.checked_at - a.checked_at).total_seconds() > daily_status.MAX_SAMPLE_GAP_S
        add("gap" if gap else ("down" if _down(b) else "ok"), a.checked_at, b.checked_at)
    if sequence:
        last = sequence[-1]
        fresh = (now - last.checked_at).total_seconds() <= daily_status.MAX_SAMPLE_GAP_S
        add(("down" if _down(last) else "ok") if fresh else "gap", last.checked_at, now)
    outages = [s for s in segments if s.kind == "down"]
    alerts = _alert_history(paths.samples, now=now)
    return ConnectionView(
        summary=summary,
        segments=segments,
        outages=list(reversed(outages))[:10],
        window_start=start,
        last_check=usable[-1].checked_at.astimezone(FAMILY_TZ) if usable else None,
        alerts=alerts,
    )


_ALERT_ACTIONS = frozenset(
    {"outage_sent", "unstable_sent", "still_down_sent", "recovered_sent"}
)


def _alert_history(samples_dir: Path, *, now: datetime, days: int = 14) -> list[tuple[datetime, str]]:
    """Alert posts recorded by the checker in its sample lines (newest first)."""
    found: list[tuple[datetime, str]] = []
    today = now.astimezone(FAMILY_TZ).date()
    for offset in range(days):
        path = samples_dir / f"{(today - timedelta(days=offset)).isoformat()}.jsonl"
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("alert_action") not in _ALERT_ACTIONS:
                continue
            at = _aware(row.get("checked_at"))
            if at is not None:
                found.append((at, str(row["alert_action"])))
    found.sort(reverse=True)
    return found[:20]


def alert_view(paths: DataPaths) -> AlertState:
    return read_alert_state(paths.alert_state)


def alert_open(state: AlertState) -> bool:
    return state.phase in (AlertPhase.ALERTED, AlertPhase.RECOVERING)


@dataclass(frozen=True, slots=True)
class JobsView:
    recap: str  # posted | pending | failed | missing | unknown
    recap_at: datetime | None
    dates: tuple[date, str] | None
    digest_today: bool
    pending: list[tuple[str, str]]


_MARKER_DIRS = (
    ("recap", ("morning_recap",)),
    ("board", ("morning_recap", "monthly_board")),
    ("dates", ("important_dates_posts",)),
    ("token", ("google_token_reminders",)),
)


def pending_deliveries(data_dir: Path) -> list[tuple[str, str]]:
    """``(kind, marker name)`` for every ``status=pending`` marker (ADR 0009)."""
    found: list[tuple[str, str]] = []
    for kind, parts in _MARKER_DIRS:
        folder = data_dir.joinpath(*parts)
        try:
            paths = sorted(folder.glob("*.json"))
        except OSError:
            continue
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(payload, dict) and payload.get("status") == "pending":
                found.append((kind, path.stem))
    return found


def jobs_view(paths: DataPaths, *, now: datetime, state: AlertState) -> JobsView:
    today = now.astimezone(FAMILY_TZ).date()
    recap, recap_at = daily_status.recap_state(paths.sources, day=today)
    return JobsView(
        recap=recap,
        recap_at=recap_at.astimezone(FAMILY_TZ) if recap_at else None,
        dates=daily_status.dates_job_state(paths.sources, today=today),
        digest_today=state.digest_posted_for == today.isoformat(),
        pending=pending_deliveries(paths.data_dir),
    )
