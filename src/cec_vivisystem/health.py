"""Listener heartbeat and alert-only health checker (Phase 29 + 30, ADRs 0013/0014).

The listener's health loop writes ``data/health/listener.json``; a separate
LaunchAgent runs ``python -m cec_vivisystem.health --alert`` every 300 s to
report ``ok`` / ``stale`` / ``missing`` (exit 0 / 1 / 2). With ``--alert`` it
also appends one sample line, applies alert policy v2 (``health_policy``) and
posts the 09:00 daily status, all to the system-status channel.

Contract: this module observes and reports only. It never kickstarts, kills or
reconnects the listener — launchd ``KeepAlive``, the Slack SDK and the
listener's own stuck-connection exit own recovery (Phase 22, ADR 0014). Do not
add a restart action here; two recovery owners caused the September reconnect
storm.

Blind spot: the checker runs on the same Mini in Carter's GUI session. At the
FileVault or login screen nothing here runs, so that outage cannot be reported
from the Mini side. A missing 09:00 daily status is the only signal.

Retention: ``listener.json`` and ``alert_state.json`` are single overwritten
records (class C). ``samples/YYYY-MM-DD.jsonl`` is class E, purged after
``SAMPLE_RETENTION_DAYS`` on every ``--alert`` run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from cec_vivisystem.health_policy import (
    AlertDecision,
    AlertPolicy,
    AlertState,
    CheckView,
    evaluate_alert,
    state_from_json,
)
from cec_vivisystem.logging import get_logger
from cec_vivisystem.models import SlackPostReceipt
from cec_vivisystem.status_channel import load_system_channel
from cec_vivisystem.storage import atomic_write_text

logger = get_logger(__name__)

COMPONENT = "health"
FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
HEARTBEAT_INTERVAL_S = 60.0
HEARTBEAT_VERSION = 2
# A connection must stay up this long before an outage counts as closed.
OUTAGE_CLEAR_AFTER_S = 60.0
DEFAULT_MAX_AGE_S = 300.0
DEFAULT_DISCONNECT_MAX_AGE_S = 600.0
# Small allowance for clock adjustments; a heartbeat further in the future is
# not evidence that the listener is alive.
FUTURE_TOLERANCE_S = 60.0
SAMPLE_RETENTION_DAYS = 14
DIGEST_HOUR = 9
EXIT_OK = 0
EXIT_STALE = 1
EXIT_MISSING = 2
EXIT_ALERT_CONFIG = 3

Clock = Callable[[], datetime]
Writer = Callable[[Path, str], None]


def default_health_dir() -> Path:
    """``<repo>/data/health``."""
    return Path(__file__).resolve().parents[2] / "data" / "health"


def default_heartbeat_path() -> Path:
    return default_health_dir() / "listener.json"


def default_alert_state_path() -> Path:
    return default_health_dir() / "alert_state.json"


def _now_hkt() -> datetime:
    return datetime.now(tz=FAMILY_TZ)


def _normalize(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=FAMILY_TZ)
    return now.astimezone(FAMILY_TZ)


class ListenerHeartbeat:
    """Periodic heartbeat writer driven by the listener's health loop.

    ``observe`` is cheap to call every health poll; it writes at most once per
    ``interval_s`` plus immediately on a connection or outage change. A write
    failure is logged and retried on the next due poll — a full disk must not
    take the listener down with it.

    Heartbeat v2 (Phase 30) keeps every Phase 29 key and adds the writer's
    identity (``instance_id``, ``started_at``) and outage counters.
    ``disconnected_since`` still resets on any connected sample (Phase 29
    meaning). ``outage_since`` resets only after the connection stayed up for
    ``OUTAGE_CLEAR_AFTER_S``, so the sub-30-s "recoveries" of a stuck SDK
    connection no longer hide a long outage from the checker.
    """

    def __init__(
        self,
        path: Path,
        *,
        clock: Clock | None = None,
        writer: Writer = atomic_write_text,
        pid: int | None = None,
        interval_s: float = HEARTBEAT_INTERVAL_S,
        instance_id: str | None = None,
        started_at: datetime | None = None,
    ) -> None:
        self._path = path
        self._clock = clock if clock is not None else _now_hkt
        self._writer = writer
        self._pid = pid if pid is not None else os.getpid()
        self._interval_s = interval_s
        self.instance_id = instance_id if instance_id is not None else uuid.uuid4().hex
        self._started_at = _normalize(
            started_at if started_at is not None else self._clock()
        )
        self._connected: bool | None = None
        self._disconnected_since: datetime | None = None
        self._last_write: datetime | None = None
        self._last_sample_at: datetime | None = None
        self._connected_since: datetime | None = None
        self._last_connected_at: datetime | None = None
        self._outage_since: datetime | None = None
        self._episodes = 0
        self._disconnected_s = 0.0
        self._last_outage_s: float | None = None
        self._last_outage_ended_at: datetime | None = None
        self._stuck_retries = 0

    def set_stuck_retries(self, count: int) -> None:
        """Record the listener's stuck-reconnect counter in the next write."""
        self._stuck_retries = count

    def observe(self, connected: bool) -> bool:
        """Record one health observation; return True if a heartbeat was written."""
        now = _normalize(self._clock())
        changed = connected != self._connected
        self._connected = connected
        if not connected and self._last_sample_at is not None:
            self._disconnected_s += max(
                0.0, (now - self._last_sample_at).total_seconds()
            )
        self._last_sample_at = now
        if connected:
            self._disconnected_since = None
            self._last_connected_at = now
            if self._connected_since is None:
                self._connected_since = now
            if self._outage_since is not None and (
                (now - self._connected_since).total_seconds() >= OUTAGE_CLEAR_AFTER_S
            ):
                self._last_outage_s = (
                    self._connected_since - self._outage_since
                ).total_seconds()
                self._last_outage_ended_at = self._connected_since
                self._outage_since = None
                changed = True
        else:
            self._connected_since = None
            if self._disconnected_since is None:
                self._disconnected_since = now
            if self._outage_since is None:
                self._outage_since = now
                self._episodes += 1
                changed = True

        due = (
            changed
            or self._last_write is None
            or (now - self._last_write).total_seconds() >= self._interval_s
        )
        if not due:
            return False
        return self._write(now, connected)

    def write_exit(self, exit_reason: str) -> bool:
        """Write a final heartbeat naming why the process is about to exit."""
        now = _normalize(self._clock())
        return self._write(now, bool(self._connected), exit_reason=exit_reason)

    def _write(
        self, now: datetime, connected: bool, *, exit_reason: str | None = None
    ) -> bool:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value is not None else None

        payload: dict[str, Any] = {
            # Phase 29 keys: keep names and meaning.
            "written_at": now.isoformat(),
            "pid": self._pid,
            "socket_mode_connected": connected,
            "disconnected_since": iso(self._disconnected_since),
            # Phase 30 (v2) keys; readers must tolerate their absence.
            "heartbeat_version": HEARTBEAT_VERSION,
            "instance_id": self.instance_id,
            "started_at": self._started_at.isoformat(),
            "outage_since": iso(self._outage_since),
            "last_connected_at": iso(self._last_connected_at),
            "disconnect_episodes": self._episodes,
            "disconnected_s": round(self._disconnected_s, 1),
            "last_outage_s": (
                round(self._last_outage_s, 1) if self._last_outage_s is not None else None
            ),
            "last_outage_ended_at": iso(self._last_outage_ended_at),
            "stuck_retries": self._stuck_retries,
        }
        if exit_reason is not None:
            payload["exit_reason"] = exit_reason
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._writer(self._path, json.dumps(payload, sort_keys=True) + "\n")
        except Exception as exc:  # noqa: BLE001 — heartbeat must never stop the listener
            logger.error(
                "listener_heartbeat_write_failed",
                component=COMPONENT,
                outcome="failure",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return False
        self._last_write = now
        return True


class HealthStatus(StrEnum):
    OK = "ok"
    STALE = "stale"
    MISSING = "missing"


_EXIT_CODES = {
    HealthStatus.OK: EXIT_OK,
    HealthStatus.STALE: EXIT_STALE,
    HealthStatus.MISSING: EXIT_MISSING,
}


@dataclass(frozen=True, slots=True)
class HealthCheck:
    status: HealthStatus
    reason: str
    age_s: float | None = None
    pid: int | None = None
    socket_mode_connected: bool | None = None
    # Phase 30: best known outage start and length (None when not known).
    outage_started_at: datetime | None = None
    disconnected_for_s: float | None = None
    instance_id: str | None = None
    written_at: datetime | None = None
    exit_reason: str | None = None

    @property
    def exit_code(self) -> int:
        return _EXIT_CODES[self.status]

    def view(self) -> CheckView:
        return CheckView(
            healthy=self.status == HealthStatus.OK,
            reason=self.reason,
            pid=self.pid,
            outage_started_at=self.outage_started_at,
            exit_reason=self.exit_reason,
        )


def _parse_aware(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def read_heartbeat(path: Path) -> tuple[dict[str, Any] | None, HealthCheck | None]:
    """Return ``(raw, None)`` for a JSON object, else ``(None, failure_check)``."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, HealthCheck(HealthStatus.MISSING, "missing")
    except OSError:
        return None, HealthCheck(HealthStatus.STALE, "unreadable")
    except ValueError:
        return None, HealthCheck(HealthStatus.STALE, "invalid")
    if not isinstance(raw, dict):
        return None, HealthCheck(HealthStatus.STALE, "invalid")
    return raw, None


def classify_heartbeat(
    raw: Mapping[str, Any],
    *,
    now: datetime,
    max_age_s: float = DEFAULT_MAX_AGE_S,
    disconnect_max_age_s: float = DEFAULT_DISCONNECT_MAX_AGE_S,
) -> HealthCheck:
    """Classify one parsed heartbeat. Never raises for garbage values.

    A Phase 29 file (no ``outage_since`` key) classifies exactly as before.
    A v2 file uses ``outage_since`` so a connection that came back for less
    than ``OUTAGE_CLEAR_AFTER_S`` does not reset the outage.
    """
    current = _normalize(now)
    written_at = _parse_aware(raw.get("written_at"))
    connected = raw.get("socket_mode_connected")
    pid_raw = raw.get("pid")
    pid = pid_raw if isinstance(pid_raw, int) and not isinstance(pid_raw, bool) else None
    instance = raw.get("instance_id") if isinstance(raw.get("instance_id"), str) else None
    exit_reason = raw.get("exit_reason") if isinstance(raw.get("exit_reason"), str) else None
    if written_at is None or not isinstance(connected, bool):
        return HealthCheck(HealthStatus.STALE, "invalid", pid=pid, instance_id=instance)

    age_s = (current - written_at).total_seconds()
    fields: dict[str, Any] = {
        "age_s": age_s,
        "pid": pid,
        "socket_mode_connected": connected,
        "instance_id": instance,
        "written_at": written_at,
        "exit_reason": exit_reason,
    }
    if age_s < -FUTURE_TOLERANCE_S:
        return HealthCheck(HealthStatus.STALE, "future_timestamp", **fields)
    if age_s > max_age_s:
        # The process stopped writing: the outage started at the last write.
        return HealthCheck(
            HealthStatus.STALE, "too_old", outage_started_at=written_at, **fields
        )

    if "outage_since" in raw:
        since_raw = raw.get("outage_since")
        since = _parse_aware(since_raw)
        if (since_raw is not None and since is None) or (not connected and since is None):
            return HealthCheck(HealthStatus.STALE, "invalid", **fields)
    else:
        since = _parse_aware(raw.get("disconnected_since")) if not connected else None
        if not connected and since is None:
            return HealthCheck(HealthStatus.STALE, "invalid", **fields)
    if since is not None:
        down_s = (current - since).total_seconds()
        fields["outage_started_at"] = since
        fields["disconnected_for_s"] = down_s
        if down_s > disconnect_max_age_s:
            return HealthCheck(HealthStatus.STALE, "socket_disconnected", **fields)
    return HealthCheck(HealthStatus.OK, "fresh", **fields)


def check_heartbeat(
    path: Path,
    *,
    now: datetime | None = None,
    max_age_s: float = DEFAULT_MAX_AGE_S,
    disconnect_max_age_s: float = DEFAULT_DISCONNECT_MAX_AGE_S,
) -> HealthCheck:
    """Classify the heartbeat file. Never raises for missing or garbage content.

    Missing is reported separately (exit 2) so "never started since install"
    differs from "was running, then stopped". Anything unreadable or malformed
    is stale: it cannot prove the listener is alive.
    """
    raw, failure = read_heartbeat(path)
    if raw is None:
        return failure or HealthCheck(HealthStatus.STALE, "invalid")
    return classify_heartbeat(
        raw,
        now=now if now is not None else _now_hkt(),
        max_age_s=max_age_s,
        disconnect_max_age_s=disconnect_max_age_s,
    )


class AlertPoster(Protocol):
    def post(self, *, channel_id: str, text: str) -> SlackPostReceipt: ...


class HealthConfigError(Exception):
    """Missing alert configuration (no secret values in message)."""


def load_alert_config(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Return ``(bot_token, channel_id)`` for alerts and the daily status.

    The system-status channel (``CEC_HEALTH_ALERT_CHANNEL_ID``) wins; the
    plans channel is the logged fallback so an unset variable never silences
    alerts.
    """
    source = env if env is not None else os.environ
    bot = (source.get("SLACK_BOT_TOKEN") or "").strip()
    channel = load_system_channel(source, component=COMPONENT)
    missing = []
    if not bot:
        missing.append("SLACK_BOT_TOKEN")
    if not channel:
        missing.append("CEC_HEALTH_ALERT_CHANNEL_ID or SLACK_FAMILY_PLANS_CHANNEL_ID")
    if missing:
        raise HealthConfigError(
            "Missing required health alert configuration: " + ", ".join(missing)
        )
    return bot, channel


def _format_age(age_s: float | None) -> str:
    return "unknown" if age_s is None else f"{int(age_s)}s"


def read_alert_state(state_path: Path) -> AlertState:
    """Load v2 state, migrate v1, or fall back to ``ok`` with a warning."""
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return AlertState()
    except (OSError, ValueError) as exc:
        logger.warning(
            "health_alert_state_unreadable",
            component=COMPONENT,
            outcome="partial",
            error_type=type(exc).__name__,
        )
        return AlertState()
    try:
        return state_from_json(raw)
    except ValueError:
        logger.warning(
            "health_alert_state_unreadable",
            component=COMPONENT,
            outcome="partial",
            error_type="InvalidState",
        )
        return AlertState()


def write_alert_state(
    state_path: Path, state: AlertState, *, writer: Writer = atomic_write_text
) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    writer(state_path, json.dumps(state.to_json(), sort_keys=True) + "\n")


def run_alert(
    check: HealthCheck,
    *,
    state_path: Path,
    poster: AlertPoster,
    channel_id: str,
    now: datetime | None = None,
    writer: Writer = atomic_write_text,
    policy: AlertPolicy | None = None,
) -> str:
    """Apply alert policy v2 to one check; post at most one alert.

    Returns ``outage_sent``, ``unstable_sent``, ``still_down_sent``,
    ``recovered_sent``, ``unchanged`` or ``failed``. State advances only after
    Slack accepts the post, so a failed post is retried on the next run; an
    ambiguous failure can therefore repeat one operator alert. That trade-off
    suits an ops notice, unlike family posts under ADR 0009.
    """
    current = _normalize(now if now is not None else _now_hkt())
    previous = read_alert_state(state_path)
    decision = evaluate_alert(previous, check.view(), now=current, policy=policy)
    return _apply_decision(
        decision,
        previous=previous,
        check=check,
        state_path=state_path,
        poster=poster,
        channel_id=channel_id,
        writer=writer,
    )[0]


def _apply_decision(
    decision: AlertDecision,
    *,
    previous: AlertState,
    check: HealthCheck,
    state_path: Path,
    poster: AlertPoster,
    channel_id: str,
    writer: Writer,
) -> tuple[str, AlertState]:
    """Post the decided action, then persist. Returns (result, saved state)."""
    if decision.suppressed is not None:
        logger.info(
            "health_alert_suppressed",
            component=COMPONENT,
            outcome="skipped",
            reason=decision.suppressed,
            phase=decision.state.phase.value,
            check_reason=check.reason,
        )
    if decision.action is None:
        try:
            write_alert_state(state_path, decision.state, writer=writer)
        except Exception as exc:  # noqa: BLE001 — report; next run recomputes
            logger.error(
                "health_alert_state_write_failed",
                component=COMPONENT,
                outcome="failure",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return "failed", previous
        return "unchanged", decision.state

    action = decision.action
    result = f"{action.kind.value}_sent"
    try:
        poster.post(channel_id=channel_id, text=action.text)
        write_alert_state(state_path, decision.state, writer=writer)
    except Exception as exc:  # noqa: BLE001 — report and let the next run retry
        logger.error(
            "health_alert_failed",
            component=COMPONENT,
            outcome="failure",
            action=result,
            channel_id=channel_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return "failed", previous
    if decision.opened:
        logger.info(
            "health_outage_opened",
            component=COMPONENT,
            outcome="success",
            kind=action.kind.value,
            reason=check.reason,
            outage_started_at=(
                decision.state.outage_started_at.isoformat()
                if decision.state.outage_started_at
                else None
            ),
            pid=check.pid,
        )
    if decision.closed:
        logger.info(
            "health_outage_closed",
            component=COMPONENT,
            outcome="success",
            reason=previous.outage_reason,
            pid=check.pid,
        )
    logger.info(
        "health_alert_sent",
        component=COMPONENT,
        outcome="success",
        action=result,
        channel_id=channel_id,
        status=check.status.value,
        reason=check.reason,
        pid=check.pid,
    )
    return result, decision.state


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cec_vivisystem.health",
        description=(
            "Report listener heartbeat health: exit 0 ok, 1 stale, 2 missing, "
            "3 alert configuration missing. Never restarts anything."
        ),
    )
    parser.add_argument("--path", type=Path, default=None, help="heartbeat JSON path")
    parser.add_argument("--max-age-s", type=float, default=DEFAULT_MAX_AGE_S)
    parser.add_argument(
        "--disconnect-max-age-s", type=float, default=DEFAULT_DISCONNECT_MAX_AGE_S
    )
    parser.add_argument(
        "--alert",
        action="store_true",
        help=(
            "record a sample, apply alert policy v2 and post the 09:00 daily "
            "status to the system-status channel"
        ),
    )
    parser.add_argument("--state-path", type=Path, default=None)
    parser.add_argument(
        "--samples-dir",
        type=Path,
        default=None,
        help="sample directory (default: samples/ next to the state file)",
    )
    return parser


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
    poster_factory: Callable[[str], AlertPoster] | None = None,
    status_sources: Any = None,
) -> int:
    """Testable CLI body; returns the process exit code.

    ``status_sources`` (``daily_status.StatusSources``) lets tests point the
    daily status at temporary marker and log directories.
    """
    from cec_vivisystem import daily_status

    args = _build_arg_parser().parse_args(argv)
    path = args.path if args.path is not None else default_heartbeat_path()
    current = _normalize(now if now is not None else _now_hkt())
    raw, failure = read_heartbeat(path)
    if raw is None:
        check = failure or HealthCheck(HealthStatus.STALE, "invalid")
    else:
        check = classify_heartbeat(
            raw,
            now=current,
            max_age_s=args.max_age_s,
            disconnect_max_age_s=args.disconnect_max_age_s,
        )
    logger.info(
        "health_check_completed",
        component=COMPONENT,
        outcome="success" if check.status == HealthStatus.OK else "failure",
        status=check.status.value,
        reason=check.reason,
        age_s=check.age_s,
        socket_mode_connected=check.socket_mode_connected,
        pid=check.pid,
        instance_id=check.instance_id,
        disconnected_for_s=check.disconnected_for_s,
    )
    print(
        f"{check.status.value} reason={check.reason} "
        f"age={_format_age(check.age_s)} pid={check.pid} path={path}"
    )
    if not args.alert:
        return check.exit_code

    source_env = env if env is not None else os.environ
    try:
        bot_token, channel_id = load_alert_config(source_env)
    except HealthConfigError as exc:
        logger.error(
            "health_alert_config_failed",
            component=COMPONENT,
            outcome="failure",
            error_type="HealthConfigError",
            error_message=str(exc),
        )
        return EXIT_ALERT_CONFIG
    if poster_factory is None:
        from cec_vivisystem.morning_recap import SlackWebPoster

        poster_factory = SlackWebPoster
    poster = poster_factory(bot_token)
    state_path = (
        args.state_path if args.state_path is not None else default_alert_state_path()
    )
    samples_dir = (
        args.samples_dir if args.samples_dir is not None else state_path.parent / "samples"
    )

    previous = read_alert_state(state_path)
    decision = evaluate_alert(previous, check.view(), now=current)
    alert_result, state = _apply_decision(
        decision,
        previous=previous,
        check=check,
        state_path=state_path,
        poster=poster,
        channel_id=channel_id,
        writer=atomic_write_text,
    )
    daily_status.record_sample(
        samples_dir,
        check=check,
        heartbeat=raw,
        now=current,
        alert_phase=state.phase.value,
        alert_action=alert_result,
    )
    daily_status.purge_samples(samples_dir, now=current, keep_days=SAMPLE_RETENTION_DAYS)

    today = current.date().isoformat()
    if current.hour >= DIGEST_HOUR and state.digest_posted_for != today:
        sources = (
            status_sources
            if status_sources is not None
            else daily_status.StatusSources.default()
        )
        text = daily_status.build_daily_status(
            now=current,
            check=check,
            state=state,
            samples_dir=samples_dir,
            sources=sources,
            env=source_env,
        )
        try:
            poster.post(channel_id=channel_id, text=text)
        except Exception as exc:  # noqa: BLE001 — retried by the next run
            logger.error(
                "health_digest_failed",
                component=COMPONENT,
                outcome="failure",
                channel_id=channel_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        else:
            try:
                write_alert_state(state_path, replace(state, digest_posted_for=today))
            except Exception as exc:  # noqa: BLE001 — a repeat digest is acceptable
                logger.error(
                    "health_alert_state_write_failed",
                    component=COMPONENT,
                    outcome="failure",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
            logger.info(
                "health_digest_sent",
                component=COMPONENT,
                outcome="success",
                channel_id=channel_id,
                digest_date=today,
            )
    return check.exit_code


def main() -> None:
    """CLI entry: load ``.env`` if present, check, optionally alert."""
    from dotenv import load_dotenv

    from cec_vivisystem.logging import setup_logging

    load_dotenv()
    setup_logging(level=os.environ.get("LOG_LEVEL", "INFO"))
    raise SystemExit(run_cli(sys.argv[1:]))


if __name__ == "__main__":
    main()
