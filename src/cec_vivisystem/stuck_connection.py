"""Stuck Socket Mode connection guard (Phase 30 decision 7, ADR 0014).

Evidence (2026-10-10 to 10-14): one listener's slack_sdk builtin client looped
on ``BrokenPipeError`` for four days. The SDK "recovered" about 100 times a
day, but no connection lasted more than about 30 s. Only a reboot fixed it.

Contract:
- The Slack SDK stays the first recovery owner. This guard acts only after
  ``stuck_after_s`` (default 900 s) with no connection that lasted
  ``stable_s`` (300 s), and only while the latest sample is disconnected.
- Then the listener replaces the whole Socket Mode client (close, build a new
  one, connect) at most ``retries`` times, ``retry_wait_s`` apart. A connection
  that stays up ``stable_s`` resets the counter.
- After the last failed retry the listener exits with code 75 and launchd
  ``KeepAlive`` starts a new process.

What not to do: do not call ``connect_to_new_endpoint(force=True)`` on the
live client or poll-reconnect every health tick. Two concurrent recovery
owners caused the September reconnect storm (Phase 22). The guard is pure
(no I/O, no sleeps); the listener performs the actions.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from cec_vivisystem.logging import get_logger

logger = get_logger(__name__)

COMPONENT = "listener"
EXIT_SOCKET_STUCK = 75  # EX_TEMPFAIL: launchd KeepAlive restarts the listener
ENV_STUCK_AFTER_S = "CEC_LISTENER_STUCK_AFTER_S"
ENV_STUCK_RETRIES = "CEC_LISTENER_STUCK_RETRIES"
ENV_STUCK_RETRY_WAIT_S = "CEC_LISTENER_STUCK_RETRY_WAIT_S"
DEFAULT_STUCK_AFTER_S = 900
DEFAULT_STUCK_RETRIES = 3
DEFAULT_STUCK_RETRY_WAIT_S = 120
STABLE_CONNECTION_S = 300.0
# Inclusive ranges. A window shorter than STABLE_CONNECTION_S could never
# contain a stable connection, so every disconnect would count as stuck.
_RANGES = {
    ENV_STUCK_AFTER_S: (int(STABLE_CONNECTION_S), 86_400, DEFAULT_STUCK_AFTER_S),
    ENV_STUCK_RETRIES: (0, 10, DEFAULT_STUCK_RETRIES),
    ENV_STUCK_RETRY_WAIT_S: (15, 3_600, DEFAULT_STUCK_RETRY_WAIT_S),
}


@dataclass(frozen=True, slots=True)
class StuckPolicy:
    stuck_after_s: float = DEFAULT_STUCK_AFTER_S
    retries: int = DEFAULT_STUCK_RETRIES
    retry_wait_s: float = DEFAULT_STUCK_RETRY_WAIT_S
    stable_s: float = STABLE_CONNECTION_S


def _int_setting(source: Mapping[str, str], name: str) -> int:
    low, high, default = _RANGES[name]
    raw = (source.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = None
    if value is None or not low <= value <= high:
        # An optional tuning value must not crash-loop the listener under
        # KeepAlive; reject it visibly and keep the default.
        logger.warning(
            "listener_stuck_config_invalid",
            component=COMPONENT,
            outcome="partial",
            variable=name,
            allowed_min=low,
            allowed_max=high,
            used_default=default,
        )
        return default
    return value


def load_stuck_policy(env: Mapping[str, str] | None = None) -> StuckPolicy:
    """Read and validate the three ``CEC_LISTENER_STUCK_*`` variables."""
    source = env if env is not None else os.environ
    return StuckPolicy(
        stuck_after_s=float(_int_setting(source, ENV_STUCK_AFTER_S)),
        retries=_int_setting(source, ENV_STUCK_RETRIES),
        retry_wait_s=float(_int_setting(source, ENV_STUCK_RETRY_WAIT_S)),
    )


class StuckAction(StrEnum):
    NONE = "none"
    RETRY = "retry"
    RECOVERED = "recovered"
    EXIT = "exit"


class StuckConnectionGuard:
    """Decide retry / recovered / exit from 15-s connection samples.

    ``observe`` is called once per health poll with an injected time, so tests
    drive it with a fake clock. It never sleeps and never touches a socket.
    """

    def __init__(self, policy: StuckPolicy, *, started_at: datetime) -> None:
        self.policy = policy
        self._anchor = started_at
        self._run_start: datetime | None = None
        self._last_stable_at: datetime | None = None
        self._last_retry_at: datetime | None = None
        self.retries_used = 0

    def observe(self, connected: bool, *, now: datetime) -> StuckAction:
        if connected:
            if self._run_start is None:
                self._run_start = now
            if (now - self._run_start).total_seconds() >= self.policy.stable_s:
                self._last_stable_at = now
                if self.retries_used:
                    self.retries_used = 0
                    self._last_retry_at = None
                    return StuckAction.RECOVERED
            return StuckAction.NONE

        # Disconnected: the SDK has not fixed it yet.
        self._run_start = None
        if self.retries_used == 0:
            reference = self._anchor
            if self._last_stable_at is not None and self._last_stable_at > reference:
                reference = self._last_stable_at
            if (now - reference).total_seconds() < self.policy.stuck_after_s:
                return StuckAction.NONE
        elif self._last_retry_at is not None and (
            (now - self._last_retry_at).total_seconds() < self.policy.retry_wait_s
        ):
            return StuckAction.NONE
        if self.retries_used >= self.policy.retries:
            return StuckAction.EXIT
        self.retries_used += 1
        self._last_retry_at = now
        return StuckAction.RETRY
