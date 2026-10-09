"""Phase 29 heartbeat, stale checker and alert dedupe (offline, fake Slack)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem.health import (
    EXIT_ALERT_CONFIG,
    EXIT_MISSING,
    EXIT_OK,
    EXIT_STALE,
    HealthCheck,
    HealthConfigError,
    HealthStatus,
    ListenerHeartbeat,
    check_heartbeat,
    load_alert_config,
    run_alert,
    run_cli,
)
from cec_vivisystem.models import SlackPostReceipt

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
T0 = datetime(2026, 10, 10, 9, 0, tzinfo=FAMILY_TZ)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakePoster:
    def __init__(self, *, fail: bool = False) -> None:
        self.posts: list[dict] = []
        self.fail = fail

    def post(self, *, channel_id: str, text: str) -> SlackPostReceipt:
        if self.fail:
            raise ConnectionError("synthetic slack outage")
        self.posts.append({"channel_id": channel_id, "text": text})
        return SlackPostReceipt(channel_id, f"{len(self.posts)}.0")


def _write(path: Path, **fields) -> None:
    payload = {
        "written_at": T0.isoformat(),
        "pid": 4242,
        "socket_mode_connected": True,
        "disconnected_since": None,
    }
    payload.update(fields)
    path.write_text(json.dumps(payload), encoding="utf-8")


# --- heartbeat writer ---------------------------------------------------


def test_heartbeat_first_observe_writes_contract_shape(tmp_path: Path) -> None:
    path = tmp_path / "health" / "listener.json"
    heartbeat = ListenerHeartbeat(path, clock=Clock(T0), pid=4242)
    assert heartbeat.observe(True) is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {
        "written_at": "2026-10-10T09:00:00+08:00",
        "pid": 4242,
        "socket_mode_connected": True,
        "disconnected_since": None,
    }


def test_heartbeat_respects_interval(tmp_path: Path) -> None:
    path = tmp_path / "listener.json"
    clock = Clock(T0)
    writes: list[str] = []
    heartbeat = ListenerHeartbeat(
        path, clock=clock, writer=lambda p, text: writes.append(text), interval_s=60
    )
    assert heartbeat.observe(True)
    clock.advance(15)
    assert not heartbeat.observe(True)
    clock.advance(30)
    assert not heartbeat.observe(True)
    clock.advance(15)
    assert heartbeat.observe(True)
    assert len(writes) == 2
    assert json.loads(writes[-1])["written_at"] == "2026-10-10T09:01:00+08:00"


def test_heartbeat_tracks_disconnected_since(tmp_path: Path) -> None:
    path = tmp_path / "listener.json"
    clock = Clock(T0)
    heartbeat = ListenerHeartbeat(path, clock=clock, pid=1)
    heartbeat.observe(True)
    clock.advance(15)
    assert heartbeat.observe(False), "state change writes immediately"
    down = json.loads(path.read_text())
    assert down["socket_mode_connected"] is False
    assert down["disconnected_since"] == "2026-10-10T09:00:15+08:00"
    clock.advance(60)
    assert heartbeat.observe(False)
    still_down = json.loads(path.read_text())
    assert still_down["disconnected_since"] == "2026-10-10T09:00:15+08:00"
    clock.advance(15)
    assert heartbeat.observe(True)
    assert json.loads(path.read_text())["disconnected_since"] is None


def test_heartbeat_normalizes_clock_to_hkt(tmp_path: Path) -> None:
    path = tmp_path / "listener.json"
    utc = datetime(2026, 10, 10, 1, 0, tzinfo=ZoneInfo("UTC"))
    ListenerHeartbeat(path, clock=Clock(utc), pid=1).observe(True)
    assert json.loads(path.read_text())["written_at"] == "2026-10-10T09:00:00+08:00"


def test_heartbeat_write_failure_is_logged_not_raised(tmp_path: Path, capsys) -> None:
    clock = Clock(T0)
    calls = 0

    def flaky(path: Path, text: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("No space left on device")

    heartbeat = ListenerHeartbeat(tmp_path / "listener.json", clock=clock, writer=flaky)
    assert heartbeat.observe(True) is False
    assert "listener_heartbeat_write_failed" in capsys.readouterr().out
    clock.advance(15)
    assert heartbeat.observe(True) is True, "a failed write is retried next poll"


# --- checker ------------------------------------------------------------


def test_check_fresh_is_ok(tmp_path: Path) -> None:
    path = tmp_path / "listener.json"
    _write(path)
    check = check_heartbeat(path, now=T0 + timedelta(seconds=90))
    assert check.status == HealthStatus.OK
    assert check.exit_code == EXIT_OK
    assert check.age_s == 90 and check.pid == 4242


def test_check_old_is_stale(tmp_path: Path) -> None:
    path = tmp_path / "listener.json"
    _write(path)
    check = check_heartbeat(path, now=T0 + timedelta(seconds=301), max_age_s=300)
    assert (check.status, check.reason, check.exit_code) == (
        HealthStatus.STALE, "too_old", EXIT_STALE,
    )
    assert check_heartbeat(path, now=T0 + timedelta(seconds=300)).status == HealthStatus.OK


def test_check_missing(tmp_path: Path) -> None:
    check = check_heartbeat(tmp_path / "absent.json", now=T0)
    assert (check.status, check.exit_code) == (HealthStatus.MISSING, EXIT_MISSING)


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        json.dumps({"pid": 1, "socket_mode_connected": True}),
        json.dumps({"written_at": "2026-10-10T09:00:00", "socket_mode_connected": True}),
        json.dumps({"written_at": T0.isoformat(), "socket_mode_connected": "yes"}),
    ],
)
def test_check_garbage_is_stale_invalid(tmp_path: Path, content: str) -> None:
    path = tmp_path / "listener.json"
    path.write_text(content, encoding="utf-8")
    check = check_heartbeat(path, now=T0)
    assert (check.status, check.reason) == (HealthStatus.STALE, "invalid")


def test_check_future_timestamp_is_stale(tmp_path: Path) -> None:
    path = tmp_path / "listener.json"
    _write(path, written_at=(T0 + timedelta(hours=1)).isoformat())
    check = check_heartbeat(path, now=T0)
    assert (check.status, check.reason) == (HealthStatus.STALE, "future_timestamp")
    _write(path, written_at=(T0 + timedelta(seconds=30)).isoformat())
    assert check_heartbeat(path, now=T0).status == HealthStatus.OK


def test_check_disconnected_threshold(tmp_path: Path) -> None:
    path = tmp_path / "listener.json"
    since = T0 - timedelta(seconds=300)
    _write(path, socket_mode_connected=False, disconnected_since=since.isoformat())
    short = check_heartbeat(path, now=T0, disconnect_max_age_s=600)
    assert short.status == HealthStatus.OK and short.socket_mode_connected is False
    long = check_heartbeat(path, now=T0, disconnect_max_age_s=120)
    assert (long.status, long.reason) == (HealthStatus.STALE, "socket_disconnected")


def test_check_disconnected_without_since_is_stale(tmp_path: Path) -> None:
    path = tmp_path / "listener.json"
    _write(path, socket_mode_connected=False, disconnected_since=None)
    check = check_heartbeat(path, now=T0)
    assert (check.status, check.reason) == (HealthStatus.STALE, "invalid")


def test_checker_reads_what_heartbeat_writes(tmp_path: Path) -> None:
    path = tmp_path / "listener.json"
    ListenerHeartbeat(path, clock=Clock(T0), pid=7).observe(True)
    check = check_heartbeat(path, now=T0 + timedelta(seconds=60))
    assert check == HealthCheck(HealthStatus.OK, "fresh", 60.0, 7, True)


# --- alert dedupe -------------------------------------------------------

OK = HealthCheck(HealthStatus.OK, "fresh", 10.0, 1, True)
STALE = HealthCheck(HealthStatus.STALE, "too_old", 900.0, 1, True)
MISSING = HealthCheck(HealthStatus.MISSING, "missing")


def test_alert_posts_once_per_transition(tmp_path: Path) -> None:
    state = tmp_path / "health" / "alert_state.json"
    poster = FakePoster()
    results = [
        run_alert(check, state_path=state, poster=poster, channel_id="D_TEST", now=T0)
        for check in (OK, STALE, STALE, STALE, OK, OK)
    ]
    assert results == [
        "unchanged", "stale_alert_sent", "unchanged", "unchanged",
        "recovery_sent", "unchanged",
    ]
    assert len(poster.posts) == 2
    assert "stale" in poster.posts[0]["text"] and "too_old" in poster.posts[0]["text"]
    assert "never restarts" in poster.posts[0]["text"]
    assert "recovered" in poster.posts[1]["text"]
    assert all(p["channel_id"] == "D_TEST" for p in poster.posts)
    assert json.loads(state.read_text())["alerted"] is False


def test_alert_missing_counts_as_outage(tmp_path: Path) -> None:
    state = tmp_path / "alert_state.json"
    poster = FakePoster()
    assert run_alert(MISSING, state_path=state, poster=poster, channel_id="C1") == (
        "stale_alert_sent"
    )
    assert run_alert(STALE, state_path=state, poster=poster, channel_id="C1") == "unchanged"
    assert "missing" in poster.posts[0]["text"]


def test_alert_corrupt_state_treated_as_not_alerted(tmp_path: Path, capsys) -> None:
    state = tmp_path / "alert_state.json"
    state.write_text("{oops", encoding="utf-8")
    poster = FakePoster()
    assert run_alert(STALE, state_path=state, poster=poster, channel_id="C1") == (
        "stale_alert_sent"
    )
    assert "health_alert_state_unreadable" in capsys.readouterr().out
    assert json.loads(state.read_text())["alerted"] is True


def test_alert_post_failure_does_not_advance_state(tmp_path: Path, capsys) -> None:
    state = tmp_path / "alert_state.json"
    assert run_alert(
        STALE, state_path=state, poster=FakePoster(fail=True), channel_id="C1"
    ) == "failed"
    assert "health_alert_failed" in capsys.readouterr().out
    assert not state.exists()
    retry = FakePoster()
    assert run_alert(STALE, state_path=state, poster=retry, channel_id="C1") == (
        "stale_alert_sent"
    )
    assert len(retry.posts) == 1


def test_alert_config_prefers_explicit_channel() -> None:
    env = {
        "SLACK_BOT_TOKEN": "fake-bot",
        "CEC_HEALTH_ALERT_CHANNEL_ID": "D_OPS",
        "SLACK_FAMILY_PLANS_CHANNEL_ID": "C_PLANS",
    }
    assert load_alert_config(env) == ("fake-bot", "D_OPS")
    del env["CEC_HEALTH_ALERT_CHANNEL_ID"]
    assert load_alert_config(env) == ("fake-bot", "C_PLANS")


def test_alert_config_missing_names_vars_without_values() -> None:
    with pytest.raises(HealthConfigError) as exc:
        load_alert_config({"SLACK_FAMILY_PLANS_CHANNEL_ID": "C_PLANS"})
    assert "SLACK_BOT_TOKEN" in str(exc.value)
    assert "C_PLANS" not in str(exc.value)


# --- CLI ----------------------------------------------------------------


def test_cli_exit_codes(tmp_path: Path, capsys) -> None:
    path = tmp_path / "listener.json"
    assert run_cli(["--path", str(path)], now=T0) == EXIT_MISSING
    _write(path)
    assert run_cli(["--path", str(path)], now=T0 + timedelta(seconds=60)) == EXIT_OK
    assert run_cli(
        ["--path", str(path), "--max-age-s", "30"], now=T0 + timedelta(seconds=60)
    ) == EXIT_STALE
    out = capsys.readouterr().out
    assert "missing reason=missing" in out
    assert "ok reason=fresh age=60s" in out
    assert "stale reason=too_old" in out


def test_cli_alert_mode_uses_fake_poster(tmp_path: Path) -> None:
    path = tmp_path / "listener.json"
    state = tmp_path / "alert_state.json"
    _write(path)
    posters: list[FakePoster] = []

    def factory(token: str) -> FakePoster:
        assert token == "fake-bot"
        posters.append(FakePoster())
        return posters[-1]

    env = {"SLACK_BOT_TOKEN": "fake-bot", "CEC_HEALTH_ALERT_CHANNEL_ID": "D_OPS"}
    args = ["--path", str(path), "--state-path", str(state), "--alert"]
    late = T0 + timedelta(hours=1)
    assert run_cli(args, now=late, env=env, poster_factory=factory) == EXIT_STALE
    assert run_cli(args, now=late, env=env, poster_factory=factory) == EXIT_STALE
    assert [len(p.posts) for p in posters] == [1, 0]


def test_cli_alert_without_config_is_visible(tmp_path: Path, capsys) -> None:
    path = tmp_path / "listener.json"
    _write(path)
    code = run_cli(
        ["--path", str(path), "--state-path", str(tmp_path / "s.json"), "--alert"],
        now=T0, env={}, poster_factory=lambda token: FakePoster(),
    )
    assert code == EXIT_ALERT_CONFIG
    assert "health_alert_config_failed" in capsys.readouterr().out
