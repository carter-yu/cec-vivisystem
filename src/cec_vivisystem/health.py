"""Listener heartbeat and alert-only stale check (Phase 29, ADR 0013).

The listener's health loop writes ``data/health/listener.json``; a separate
LaunchAgent runs ``python -m cec_vivisystem.health --alert`` to report
``ok`` / ``stale`` / ``missing`` (exit 0 / 1 / 2) and to post one Slack alert
per outage plus one recovery notice.

Contract: this module observes and reports only. It never kickstarts, kills or
reconnects the listener — launchd ``KeepAlive`` and the Slack SDK own recovery
(Phase 22). Do not add a restart action here; two recovery owners caused the
September reconnect storm.

Blind spot: the checker runs on the same Mini in Carter's GUI session. At the
FileVault or login screen nothing here runs, so that outage cannot be reported
from the Mini side. An off-box checker is out of scope.

Retention: both files are single overwritten operational records (class C);
they never accumulate, so no purge is needed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from cec_vivisystem.logging import get_logger
from cec_vivisystem.models import SlackPostReceipt
from cec_vivisystem.storage import atomic_write_text

logger = get_logger(__name__)

COMPONENT = "health"
FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
HEARTBEAT_INTERVAL_S = 60.0
DEFAULT_MAX_AGE_S = 300.0
DEFAULT_DISCONNECT_MAX_AGE_S = 600.0
# Small allowance for clock adjustments; a heartbeat further in the future is
# not evidence that the listener is alive.
FUTURE_TOLERANCE_S = 60.0
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
    ``interval_s`` plus immediately on a connection-state change. A write
    failure is logged and retried on the next due poll — a full disk must not
    take the listener down with it.
    """

    def __init__(
        self,
        path: Path,
        *,
        clock: Clock | None = None,
        writer: Writer = atomic_write_text,
        pid: int | None = None,
        interval_s: float = HEARTBEAT_INTERVAL_S,
    ) -> None:
        self._path = path
        self._clock = clock if clock is not None else _now_hkt
        self._writer = writer
        self._pid = pid if pid is not None else os.getpid()
        self._interval_s = interval_s
        self._connected: bool | None = None
        self._disconnected_since: datetime | None = None
        self._last_write: datetime | None = None

    def observe(self, connected: bool) -> bool:
        """Record one health observation; return True if a heartbeat was written."""
        now = _normalize(self._clock())
        changed = connected != self._connected
        self._connected = connected
        if connected:
            self._disconnected_since = None
        elif self._disconnected_since is None:
            self._disconnected_since = now

        due = (
            changed
            or self._last_write is None
            or (now - self._last_write).total_seconds() >= self._interval_s
        )
        if not due:
            return False

        payload = {
            "written_at": now.isoformat(),
            "pid": self._pid,
            "socket_mode_connected": connected,
            "disconnected_since": (
                self._disconnected_since.isoformat()
                if self._disconnected_since is not None
                else None
            ),
        }
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

    @property
    def exit_code(self) -> int:
        return _EXIT_CODES[self.status]


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
    current = _normalize(now if now is not None else _now_hkt())
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return HealthCheck(HealthStatus.MISSING, "missing")
    except OSError:
        return HealthCheck(HealthStatus.STALE, "unreadable")
    except ValueError:
        return HealthCheck(HealthStatus.STALE, "invalid")
    if not isinstance(raw, dict):
        return HealthCheck(HealthStatus.STALE, "invalid")

    written_at = _parse_aware(raw.get("written_at"))
    connected = raw.get("socket_mode_connected")
    pid = raw.get("pid") if isinstance(raw.get("pid"), int) else None
    if written_at is None or not isinstance(connected, bool):
        return HealthCheck(HealthStatus.STALE, "invalid", pid=pid)

    age_s = (current - written_at).total_seconds()
    fields: dict[str, Any] = {
        "age_s": age_s,
        "pid": pid,
        "socket_mode_connected": connected,
    }
    if age_s < -FUTURE_TOLERANCE_S:
        return HealthCheck(HealthStatus.STALE, "future_timestamp", **fields)
    if age_s > max_age_s:
        return HealthCheck(HealthStatus.STALE, "too_old", **fields)
    if not connected:
        since = _parse_aware(raw.get("disconnected_since"))
        if since is None:
            return HealthCheck(HealthStatus.STALE, "invalid", **fields)
        if (current - since).total_seconds() > disconnect_max_age_s:
            return HealthCheck(HealthStatus.STALE, "socket_disconnected", **fields)
    return HealthCheck(HealthStatus.OK, "fresh", **fields)


class AlertPoster(Protocol):
    def post(self, *, channel_id: str, text: str) -> SlackPostReceipt: ...


class HealthConfigError(Exception):
    """Missing alert configuration (no secret values in message)."""


def load_alert_config(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Return ``(bot_token, channel_id)`` for alerts.

    ``CEC_HEALTH_ALERT_CHANNEL_ID`` (Carter's DM or an ops channel) wins; the
    plans channel is the fallback so an unset variable never silences alerts.
    """
    source = env if env is not None else os.environ
    bot = (source.get("SLACK_BOT_TOKEN") or "").strip()
    channel = (source.get("CEC_HEALTH_ALERT_CHANNEL_ID") or "").strip() or (
        source.get("SLACK_FAMILY_PLANS_CHANNEL_ID") or ""
    ).strip()
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


def format_stale_alert(check: HealthCheck) -> str:
    return (
        f"cec-vivisystem listener heartbeat is {check.status.value} "
        f"(reason={check.reason}, age={_format_age(check.age_s)}). "
        "Slack replies may be down. On the Mini, follow docs/maintenance.md "
        '"After a reboot". This check never restarts the listener.'
    )


def format_recovery_alert(check: HealthCheck) -> str:
    return (
        "cec-vivisystem listener heartbeat recovered "
        f"(age={_format_age(check.age_s)})."
    )


def _read_alerted(state_path: Path) -> bool:
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as exc:
        logger.warning(
            "health_alert_state_unreadable",
            component=COMPONENT,
            outcome="partial",
            error_type=type(exc).__name__,
        )
        return False
    if not isinstance(raw, dict) or not isinstance(raw.get("alerted"), bool):
        logger.warning(
            "health_alert_state_unreadable",
            component=COMPONENT,
            outcome="partial",
            error_type="InvalidState",
        )
        return False
    return raw["alerted"]


def run_alert(
    check: HealthCheck,
    *,
    state_path: Path,
    poster: AlertPoster,
    channel_id: str,
    now: datetime | None = None,
    writer: Writer = atomic_write_text,
) -> str:
    """Post only on ok→not-ok and not-ok→ok transitions.

    Returns ``stale_alert_sent``, ``recovery_sent``, ``unchanged`` or ``failed``.
    State advances only after Slack accepts the post, so a failed post is
    retried on the next run; an ambiguous failure can therefore repeat one
    operator alert. That trade-off suits an ops notice, unlike family posts
    under ADR 0009.
    """
    current = _normalize(now if now is not None else _now_hkt())
    alerted = _read_alerted(state_path)
    unhealthy = check.status != HealthStatus.OK
    if unhealthy == alerted:
        return "unchanged"

    text = format_stale_alert(check) if unhealthy else format_recovery_alert(check)
    action = "stale_alert_sent" if unhealthy else "recovery_sent"
    try:
        poster.post(channel_id=channel_id, text=text)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        writer(
            state_path,
            json.dumps(
                {
                    "alerted": unhealthy,
                    "status": check.status.value,
                    "reason": check.reason,
                    "updated_at": current.isoformat(),
                },
                sort_keys=True,
            )
            + "\n",
        )
    except Exception as exc:  # noqa: BLE001 — report and let the next run retry
        logger.error(
            "health_alert_failed",
            component=COMPONENT,
            outcome="failure",
            action=action,
            channel_id=channel_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return "failed"
    logger.info(
        "health_alert_sent",
        component=COMPONENT,
        outcome="success",
        action=action,
        channel_id=channel_id,
        status=check.status.value,
        reason=check.reason,
    )
    return action


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
        help="post to Slack on ok→stale/missing and once on recovery",
    )
    parser.add_argument("--state-path", type=Path, default=None)
    return parser


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
    poster_factory: Callable[[str], AlertPoster] | None = None,
) -> int:
    """Testable CLI body; returns the process exit code."""
    args = _build_arg_parser().parse_args(argv)
    path = args.path if args.path is not None else default_heartbeat_path()
    check = check_heartbeat(
        path,
        now=now,
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
    )
    print(
        f"{check.status.value} reason={check.reason} "
        f"age={_format_age(check.age_s)} path={path}"
    )
    if not args.alert:
        return check.exit_code

    try:
        bot_token, channel_id = load_alert_config(env)
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
    run_alert(
        check,
        state_path=(
            args.state_path if args.state_path is not None else default_alert_state_path()
        ),
        poster=poster_factory(bot_token),
        channel_id=channel_id,
        now=now,
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
