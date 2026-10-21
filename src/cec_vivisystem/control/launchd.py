"""Read listener state from launchd and restart it on request (Phase 31).

Why: "is the listener running, and when did it last exit?" is the first
question in every outage, and the answer lives in ``launchctl print``.

Contract:
- ``print_service`` is read-only and returns "not available" off macOS.
- ``kickstart_listener`` is the only state change: one fixed argv
  ``launchctl kickstart -k gui/<uid>/com.cec.vivisystem.listener``, no shell,
  no user text in the argv, and only for the listener label. The app calls it
  only as part of a human action: a signed-in admin confirmed the restart
  screen, or a person (admin or member) just saved new Google access (ADR 0016,
  amended 2026-10-21). Health state never calls it. Do not add a restart for
  the 07:00/10:00 jobs (each run already reads ``.env`` fresh, and a kickstart
  would post family messages off schedule) or a timer that restarts on its own.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

LISTENER_LABEL = "com.cec.vivisystem.listener"
COMMAND_TIMEOUT_S = 20.0


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class Runner(Protocol):
    def __call__(self, argv: Sequence[str], *, timeout: float) -> CommandResult: ...


def subprocess_runner(argv: Sequence[str], *, timeout: float) -> CommandResult:
    completed = subprocess.run(
        list(argv), shell=False, capture_output=True, text=True, timeout=timeout, check=False
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True, slots=True)
class ServiceInfo:
    available: bool
    loaded: bool = False
    state: str | None = None
    pid: int | None = None
    last_exit: str | None = None
    runs: int | None = None
    error: str | None = None


_FIELDS = {
    "state": re.compile(r"^\s*state = (.+)$", re.MULTILINE),
    "pid": re.compile(r"^\s*pid = (\d+)\s*$", re.MULTILINE),
    "last_exit": re.compile(r"^\s*last exit code = (.+)$", re.MULTILINE),
    "runs": re.compile(r"^\s*runs = (\d+)\s*$", re.MULTILINE),
}


def parse_launchctl_print(text: str) -> ServiceInfo:
    """First (top-level) ``state``, ``pid``, ``last exit code`` and ``runs``."""
    found: dict[str, str] = {}
    for name, pattern in _FIELDS.items():
        match = pattern.search(text or "")
        if match:
            found[name] = match.group(1).strip()
    return ServiceInfo(
        available=True,
        loaded=True,
        state=found.get("state"),
        pid=int(found["pid"]) if "pid" in found else None,
        last_exit=found.get("last_exit"),
        runs=int(found["runs"]) if "runs" in found else None,
    )


def service_target(uid: int, label: str = LISTENER_LABEL) -> str:
    return f"gui/{uid}/{label}"


def print_service(runner: Runner, *, uid: int, platform: str, label: str = LISTENER_LABEL) -> ServiceInfo:
    if platform != "darwin":
        return ServiceInfo(available=False)
    try:
        result = runner(["launchctl", "print", service_target(uid, label)], timeout=COMMAND_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        return ServiceInfo(available=True, error=type(exc).__name__)
    if result.returncode != 0:
        return ServiceInfo(available=True, loaded=False, error=f"exit {result.returncode}")
    return parse_launchctl_print(result.stdout)


def kickstart_argv(uid: int) -> list[str]:
    return ["launchctl", "kickstart", "-k", service_target(uid, LISTENER_LABEL)]


class RestartUnavailable(RuntimeError):
    """Not macOS: there is no launchd to ask."""


def kickstart_listener(runner: Runner, *, uid: int, platform: str) -> CommandResult:
    if platform != "darwin":
        raise RestartUnavailable("launchd is not available on this computer")
    return runner(kickstart_argv(uid), timeout=COMMAND_TIMEOUT_S)
