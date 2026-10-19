"""Phase 30 decision 7: stuck Socket Mode guard and listener self-recovery.

Fake clock, fake sleep and fake Socket Mode handlers only: no sockets, no
real sleeps, no Slack.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem.health import ListenerHeartbeat
from cec_vivisystem.listener import SocketSession, monitor_socket_health
from cec_vivisystem.stuck_connection import (
    EXIT_SOCKET_STUCK,
    StuckAction,
    StuckConnectionGuard,
    StuckPolicy,
    load_stuck_policy,
)

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
T0 = datetime(2026, 10, 12, 3, 0, tzinfo=FAMILY_TZ)
TICK = 15


def _feed(guard: StuckConnectionGuard, pattern, *, seconds: int, start: int = 0):
    """Feed ``pattern(t)`` every 15 s; return [(t, action)] for non-NONE actions."""
    actions = []
    for t in range(start, start + seconds, TICK):
        action = guard.observe(pattern(t), now=T0 + timedelta(seconds=t))
        if action != StuckAction.NONE:
            actions.append((t, action))
    return actions


def broken_pipe_loop(t: int) -> bool:
    """Mini evidence: connected for 15 s about every 45 s, never longer."""
    return t % 45 == 15


# --- guard ------------------------------------------------------------------


def test_k1_stable_connection_never_acts() -> None:
    guard = StuckConnectionGuard(StuckPolicy(), started_at=T0)
    assert _feed(guard, lambda t: True, seconds=4 * 3600) == []


def test_k1_ordinary_sdk_reconnects_do_not_count_as_stuck() -> None:
    """A 2-minute drop every 20 minutes keeps 5-minute connections in each window."""
    guard = StuckConnectionGuard(StuckPolicy(), started_at=T0)
    assert _feed(guard, lambda t: t % 1200 >= 120, seconds=4 * 3600) == []


def test_k2_short_reconnects_do_not_reset_the_stuck_clock() -> None:
    guard = StuckConnectionGuard(StuckPolicy(), started_at=T0)
    actions = _feed(guard, broken_pipe_loop, seconds=900 + TICK)
    assert actions == [(900, StuckAction.RETRY)]
    assert guard.retries_used == 1


def test_k3_k5_retries_are_spaced_then_exit() -> None:
    guard = StuckConnectionGuard(StuckPolicy(), started_at=T0)
    actions = _feed(guard, lambda t: False, seconds=3600)
    assert actions[:4] == [
        (900, StuckAction.RETRY),
        (1020, StuckAction.RETRY),
        (1140, StuckAction.RETRY),
        (1260, StuckAction.EXIT),
    ]


def test_k4_five_minutes_up_after_a_retry_resets_the_counter() -> None:
    guard = StuckConnectionGuard(StuckPolicy(), started_at=T0)
    assert _feed(guard, lambda t: False, seconds=915) == [(900, StuckAction.RETRY)]
    up = _feed(guard, lambda t: True, start=915, seconds=315)
    assert up == [(1215, StuckAction.RECOVERED)] and guard.retries_used == 0
    # A new stuck period needs the full window again.
    later = _feed(guard, lambda t: False, start=1230, seconds=1200)
    assert later[0] == (1215 + 900, StuckAction.RETRY)


def test_k4_short_connection_after_retry_is_still_stuck() -> None:
    guard = StuckConnectionGuard(StuckPolicy(), started_at=T0)
    _feed(guard, lambda t: False, seconds=915)
    actions = _feed(guard, lambda t: t < 1095, start=915, seconds=600)
    assert actions[0] == (1095, StuckAction.RETRY), "up 3 min, then down: retry 2"


def test_k6_zero_retries_exit_at_once() -> None:
    guard = StuckConnectionGuard(StuckPolicy(retries=0), started_at=T0)
    assert _feed(guard, lambda t: False, seconds=915) == [(900, StuckAction.EXIT)]


def test_k7_config_defaults_and_values() -> None:
    assert load_stuck_policy({}) == StuckPolicy(900, 3, 120)
    policy = load_stuck_policy({
        "CEC_LISTENER_STUCK_AFTER_S": "1800",
        "CEC_LISTENER_STUCK_RETRIES": "0",
        "CEC_LISTENER_STUCK_RETRY_WAIT_S": "60",
    })
    assert (policy.stuck_after_s, policy.retries, policy.retry_wait_s) == (1800, 0, 60)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CEC_LISTENER_STUCK_RETRIES", "11"),
        ("CEC_LISTENER_STUCK_RETRIES", "-1"),
        ("CEC_LISTENER_STUCK_RETRIES", "three"),
        ("CEC_LISTENER_STUCK_AFTER_S", "60"),
        ("CEC_LISTENER_STUCK_RETRY_WAIT_S", "1.5"),
    ],
)
def test_k7_invalid_config_keeps_default_and_warns(name: str, value: str, capsys) -> None:
    assert load_stuck_policy({name: value}) == StuckPolicy()
    out = capsys.readouterr().out
    assert "listener_stuck_config_invalid" in out and name in out


# --- listener wiring ----------------------------------------------------------


class FakeClient:
    def __init__(self, connected: bool) -> None:
        self.connected = connected
        self.connect_calls: list[bool] = []

    def is_connected(self) -> bool:
        return self.connected

    def connect_to_new_endpoint(self, force: bool = False) -> None:
        self.connect_calls.append(force)


class FakeHandler:
    def __init__(self, connected_after_connect: bool) -> None:
        self.client = FakeClient(False)
        self._after = connected_after_connect
        self.connects = 0
        self.closed = False

    def connect(self) -> None:
        self.connects += 1
        self.client.connected = self._after

    def close(self) -> None:
        self.closed = True
        self.client.connected = False


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _session(outcomes: list[bool]) -> tuple[SocketSession, list[FakeHandler]]:
    """Handler N connects (True) or stays dead (False) per ``outcomes``."""
    built: list[FakeHandler] = []

    def factory() -> FakeHandler:
        built.append(FakeHandler(outcomes[min(len(built), len(outcomes) - 1)]))
        return built[-1]

    return SocketSession(factory), built


def test_l_stuck_retries_then_exits_75_with_heartbeat(tmp_path: Path, capsys) -> None:
    clock = Clock()
    session, built = _session([True, False])  # first connects, later ones never do
    session.connect()
    built[0].client.connected = False  # the SDK session dies and stays dead
    path = tmp_path / "listener.json"
    heartbeat = ListenerHeartbeat(path, clock=clock, pid=4182, instance_id="inst")
    heartbeat.observe(True)
    with pytest.raises(SystemExit) as exc:
        monitor_socket_health(
            session, heartbeat=heartbeat,
            guard=StuckConnectionGuard(StuckPolicy(), started_at=clock()),
            sleep=clock.sleep, clock=clock,
        )
    assert exc.value.code == EXIT_SOCKET_STUCK
    assert clock.now - T0 == timedelta(seconds=1260)
    assert len(built) == 4, "initial handler plus 3 retries"
    assert all(h.closed for h in built[:3]) and not built[3].closed
    assert all(h.client.connect_calls == [] for h in built), "never force-reconnect"
    data = json.loads(path.read_text())
    assert data["exit_reason"] == "socket_mode_stuck" and data["stuck_retries"] == 3
    out = capsys.readouterr().out
    assert len(re.findall(r"socket_mode_stuck_retry\b", out)) == 3
    assert "socket_mode_stuck_exit" in out


def test_l_retry_success_resets_and_keeps_running(tmp_path: Path, capsys) -> None:
    clock = Clock()
    session, built = _session([True, True])
    session.connect()
    built[0].client.connected = False
    heartbeat = ListenerHeartbeat(tmp_path / "listener.json", clock=clock, pid=1)
    ticks = 0

    def sleep(seconds: float) -> None:
        nonlocal ticks
        ticks += 1
        clock.sleep(seconds)
        if ticks == 200:  # 50 minutes
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        monitor_socket_health(
            session, heartbeat=heartbeat,
            guard=StuckConnectionGuard(StuckPolicy(), started_at=clock()),
            sleep=sleep, clock=clock,
        )
    assert len(built) == 2 and built[0].closed and session.handler is built[1]
    out = capsys.readouterr().out
    assert "socket_mode_stuck_reconnected" in out
    assert "socket_mode_stuck_recovered" in out
    assert json.loads((tmp_path / "listener.json").read_text())["stuck_retries"] == 0


def test_l_failed_rebuild_is_logged_and_counted(tmp_path: Path, capsys) -> None:
    clock = Clock()
    calls = 0

    def factory() -> FakeHandler:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("apps.connections.open failed")
        return FakeHandler(True)

    session = SocketSession(factory)
    session.connect()
    session.client.connected = False
    with pytest.raises(SystemExit):
        monitor_socket_health(
            session,
            heartbeat=ListenerHeartbeat(tmp_path / "hb.json", clock=clock),
            guard=StuckConnectionGuard(StuckPolicy(retries=1), started_at=clock()),
            sleep=clock.sleep, clock=clock,
        )
    assert "socket_mode_stuck_retry_failed" in capsys.readouterr().out


def test_runtime_wiring_exits_75_and_closes(tmp_path: Path, monkeypatch) -> None:
    """run_socket_mode builds handlers through the factory and closes on exit."""
    import slack_bolt
    import slack_bolt.adapter.socket_mode

    from cec_vivisystem import listener

    class FakeApp:
        def __init__(self, **kwargs):
            pass

        def event(self, name):
            return lambda callback: callback

    built: list = []

    class RuntimeHandler(FakeHandler):
        def __init__(self, app, token, **kwargs):
            super().__init__(connected_after_connect=not built)
            self.options = kwargs
            self.client.on_error_listeners = []
            self.client.on_close_listeners = []
            built.append(self)

    clock = Clock()
    monkeypatch.setattr(slack_bolt, "App", FakeApp)
    monkeypatch.setattr(slack_bolt.adapter.socket_mode, "SocketModeHandler", RuntimeHandler)
    for name in (
        "life_notes_data_dir", "confirmation_data_dir", "calendar_audit_data_dir",
        "important_dates_data_dir", "parse_miss_dir",
    ):
        monkeypatch.setattr(listener, name, lambda name=name: tmp_path / name)
    heartbeat_path = tmp_path / "health" / "listener.json"
    monkeypatch.setattr(listener, "health_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(listener, "load_live_llm_parsers", lambda: (None, None))
    monkeypatch.setattr(listener, "load_google_calendar_config",
                        lambda: (_ for _ in ()).throw(listener.CalendarWriterConfigError("x")))
    monkeypatch.setattr(listener, "_socket_clock", clock)

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        built[0].client.connected = False

    monkeypatch.setattr(listener.time, "sleep", sleep)
    monkeypatch.setenv("CEC_LISTENER_STUCK_RETRIES", "1")
    with pytest.raises(SystemExit) as exc:
        listener.run_socket_mode(listener.SlackConfig(
            "fake-bot", "fake-app", frozenset({"C_FAMILY"}), "C_NOTES",
        ))
    assert exc.value.code == 75
    assert len(built) == 2 and all(h.closed for h in built)
    assert all(h.options["auto_reconnect_enabled"] is True for h in built)
    data = json.loads(heartbeat_path.read_text())
    assert data["exit_reason"] == "socket_mode_stuck" and data["instance_id"]
