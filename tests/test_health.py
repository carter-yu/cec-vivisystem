"""Phase 29/30 heartbeat, checker, alert runner and CLI (offline, fake Slack)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem.daily_status import StatusSources
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


PHASE29_KEYS = ("written_at", "pid", "socket_mode_connected", "disconnected_since")


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
    """H1: Phase 29 keys unchanged plus the v2 identity and counter fields."""
    path = tmp_path / "health" / "listener.json"
    heartbeat = ListenerHeartbeat(
        path, clock=Clock(T0), pid=4242, instance_id="inst-a",
        started_at=T0 - timedelta(minutes=5),
    )
    assert heartbeat.observe(True) is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert {k: data[k] for k in PHASE29_KEYS} == {
        "written_at": "2026-10-10T09:00:00+08:00",
        "pid": 4242,
        "socket_mode_connected": True,
        "disconnected_since": None,
    }
    assert data["heartbeat_version"] == 2
    assert data["instance_id"] == "inst-a"
    assert data["started_at"] == "2026-10-10T08:55:00+08:00"
    assert data["outage_since"] is None
    assert data["last_connected_at"] == "2026-10-10T09:00:00+08:00"
    assert data["disconnect_episodes"] == 0 and data["disconnected_s"] == 0
    assert data["stuck_retries"] == 0
    assert "exit_reason" not in data


def test_heartbeat_instance_id_is_unique_per_process_start(tmp_path: Path) -> None:
    first = ListenerHeartbeat(tmp_path / "a.json", clock=Clock(T0))
    second = ListenerHeartbeat(tmp_path / "b.json", clock=Clock(T0))
    assert first.instance_id and first.instance_id != second.instance_id


def test_heartbeat_counts_episodes_and_disconnected_seconds(tmp_path: Path) -> None:
    """H2: samples true, false, false, true at 15 s → 1 episode, 30 s down."""
    path = tmp_path / "listener.json"
    clock = Clock(T0)
    heartbeat = ListenerHeartbeat(path, clock=clock, pid=1)
    for connected in (True, False, False, True):
        heartbeat.observe(connected)
        clock.advance(15)
    data = json.loads(path.read_text())
    assert data["disconnect_episodes"] == 1
    assert data["disconnected_s"] == 30
    assert data["outage_since"] == "2026-10-10T09:00:15+08:00", "45 s up is not enough"
    assert data["disconnected_since"] is None, "Phase 29 key still resets at once"


def test_heartbeat_short_reconnects_do_not_reset_outage(tmp_path: Path) -> None:
    """Mini evidence: < 30 s "recoveries" must not hide a long outage."""
    path = tmp_path / "listener.json"
    clock = Clock(T0)
    heartbeat = ListenerHeartbeat(path, clock=clock, pid=1)
    heartbeat.observe(False)
    for _ in range(20):  # 10 minutes: 15 s up, 15 s down
        clock.advance(15)
        heartbeat.observe(True)
        clock.advance(15)
        heartbeat.observe(False)
    data = json.loads(path.read_text())
    assert data["outage_since"] == "2026-10-10T09:00:00+08:00"
    assert data["disconnect_episodes"] == 1
    for _ in range(5):  # stays up for 60 s → outage closes
        clock.advance(15)
        heartbeat.observe(True)
    data = json.loads(path.read_text())
    assert data["outage_since"] is None
    assert data["last_outage_s"] == 615.0
    assert data["last_outage_ended_at"] == "2026-10-10T09:10:15+08:00"


def test_heartbeat_write_exit_records_reason(tmp_path: Path) -> None:
    path = tmp_path / "listener.json"
    heartbeat = ListenerHeartbeat(path, clock=Clock(T0), pid=9, instance_id="i")
    heartbeat.observe(False)
    heartbeat.set_stuck_retries(3)
    assert heartbeat.write_exit("socket_mode_stuck")
    data = json.loads(path.read_text())
    assert data["exit_reason"] == "socket_mode_stuck"
    assert data["stuck_retries"] == 3 and data["pid"] == 9


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
    ListenerHeartbeat(path, clock=Clock(T0), pid=7, instance_id="inst").observe(True)
    check = check_heartbeat(path, now=T0 + timedelta(seconds=60))
    assert (check.status, check.reason, check.age_s, check.pid) == (
        HealthStatus.OK, "fresh", 60.0, 7,
    )
    assert check.socket_mode_connected is True and check.instance_id == "inst"
    assert check.outage_started_at is None and check.disconnected_for_s is None


def test_check_phase29_file_fills_outage_fields_from_disconnected_since(
    tmp_path: Path,
) -> None:
    """P1: a Phase 29 file classifies as before and now reports the outage start."""
    path = tmp_path / "listener.json"
    since = T0 - timedelta(minutes=11)
    _write(path, socket_mode_connected=False, disconnected_since=since.isoformat())
    check = check_heartbeat(path, now=T0)
    assert (check.status, check.reason) == (HealthStatus.STALE, "socket_disconnected")
    assert check.outage_started_at == since and check.disconnected_for_s == 660
    _write(path)  # connected Phase 29 file
    assert check_heartbeat(path, now=T0).outage_started_at is None


def test_check_v2_outage_survives_short_reconnect(tmp_path: Path) -> None:
    """P1: v2 uses outage_since, so a connected snapshot inside an outage is stale."""
    path = tmp_path / "listener.json"
    since = T0 - timedelta(minutes=11)
    _write(path, outage_since=since.isoformat(), instance_id="inst")
    check = check_heartbeat(path, now=T0)
    assert (check.status, check.reason) == (HealthStatus.STALE, "socket_disconnected")
    assert check.socket_mode_connected is True
    assert check.outage_started_at == since and check.disconnected_for_s == 660
    assert check.instance_id == "inst"
    _write(path, outage_since=(T0 - timedelta(minutes=5)).isoformat())
    short = check_heartbeat(path, now=T0)
    assert short.status == HealthStatus.OK and short.disconnected_for_s == 300
    _write(path, outage_since=None)
    assert check_heartbeat(path, now=T0).status == HealthStatus.OK


@pytest.mark.parametrize(
    "fields",
    [
        {"outage_since": "not-a-time"},
        {"outage_since": None, "socket_mode_connected": False,
         "disconnected_since": T0.isoformat()},
    ],
)
def test_check_v2_garbage_outage_is_invalid(tmp_path: Path, fields: dict) -> None:
    path = tmp_path / "listener.json"
    _write(path, **fields)
    assert check_heartbeat(path, now=T0).reason == "invalid"


def test_check_too_old_outage_starts_at_last_write(tmp_path: Path) -> None:
    path = tmp_path / "listener.json"
    _write(path, exit_reason="socket_mode_stuck")
    check = check_heartbeat(path, now=T0 + timedelta(minutes=20))
    assert check.reason == "too_old" and check.outage_started_at == T0
    assert check.exit_reason == "socket_mode_stuck"


# --- alert runner (policy details in test_status_channel.py) ----------------

OK = HealthCheck(HealthStatus.OK, "fresh", 10.0, 1, True)


def _down(minutes: float) -> HealthCheck:
    """socket_disconnected check whose outage started ``minutes`` before T0."""
    return HealthCheck(
        HealthStatus.STALE, "socket_disconnected", 10.0, 4182, False,
        outage_started_at=T0 - timedelta(minutes=minutes),
    )


def test_run_alert_needs_two_checks_and_persists_v2_state(tmp_path: Path) -> None:
    state = tmp_path / "health" / "alert_state.json"
    poster = FakePoster()

    def run(check: HealthCheck, minutes: float) -> str:
        return run_alert(check, state_path=state, poster=poster, channel_id="C_STATUS",
                         now=T0 + timedelta(minutes=minutes))

    assert run(_down(11), 0) == "unchanged"
    saved = json.loads(state.read_text())
    assert saved["version"] == 2 and saved["phase"] == "suspect" and saved["alerted"] is False
    assert run(_down(11), 5) == "outage_sent"
    assert json.loads(state.read_text())["phase"] == "alerted"
    assert len(poster.posts) == 1 and poster.posts[0]["channel_id"] == "C_STATUS"
    assert "08:49" in poster.posts[0]["text"] and "PID 4182" in poster.posts[0]["text"]


def test_alert_post_failure_does_not_advance_state(tmp_path: Path, capsys) -> None:
    """P12: a raising poster leaves the state; the next run retries the same post."""
    state = tmp_path / "alert_state.json"
    run_alert(_down(11), state_path=state, poster=FakePoster(), channel_id="C1", now=T0)
    before = state.read_text()
    later = T0 + timedelta(minutes=5)
    assert run_alert(
        _down(11), state_path=state, poster=FakePoster(fail=True), channel_id="C1", now=later
    ) == "failed"
    assert "health_alert_failed" in capsys.readouterr().out
    assert state.read_text() == before
    retry = FakePoster()
    assert run_alert(
        _down(11), state_path=state, poster=retry, channel_id="C1", now=later
    ) == "outage_sent"
    assert len(retry.posts) == 1


@pytest.mark.parametrize(
    ("content", "phase"),
    [
        (json.dumps({"alerted": True, "status": "stale", "reason": "too_old",
                     "updated_at": "2026-10-10T08:00:00+08:00"}), "alerted"),
        (json.dumps({"alerted": False}), "ok"),
    ],
)
def test_alert_state_v1_migrates(tmp_path: Path, content: str, phase: str) -> None:
    """P13: Phase 29 state files keep their meaning."""
    state = tmp_path / "alert_state.json"
    state.write_text(content, encoding="utf-8")
    poster = FakePoster()
    run_alert(OK, state_path=state, poster=poster, channel_id="C1", now=T0)
    saved = json.loads(state.read_text())
    assert saved["version"] == 2
    assert saved["phase"] == ("recovering" if phase == "alerted" else "ok")
    assert poster.posts == []


@pytest.mark.parametrize(
    "content", ["{oops", "[]", json.dumps({"version": 2, "phase": "bogus"}),
                json.dumps({"version": 9})],
)
def test_alert_corrupt_state_treated_as_ok(tmp_path: Path, capsys, content: str) -> None:
    """P13: corrupt state → ok with health_alert_state_unreadable; no crash."""
    state = tmp_path / "alert_state.json"
    state.write_text(content, encoding="utf-8")
    assert run_alert(_down(11), state_path=state, poster=FakePoster(), channel_id="C1",
                     now=T0) == "unchanged"
    assert "health_alert_state_unreadable" in capsys.readouterr().out
    assert json.loads(state.read_text())["phase"] == "suspect"


def test_alert_config_prefers_status_channel_and_logs_fallback(capsys) -> None:
    """C1: the status channel wins; the plans fallback logs system_channel_fallback."""
    env = {
        "SLACK_BOT_TOKEN": "fake-bot",
        "CEC_HEALTH_ALERT_CHANNEL_ID": "C_STATUS",
        "SLACK_FAMILY_PLANS_CHANNEL_ID": "C_PLANS",
    }
    assert load_alert_config(env) == ("fake-bot", "C_STATUS")
    assert "system_channel_fallback" not in capsys.readouterr().out
    del env["CEC_HEALTH_ALERT_CHANNEL_ID"]
    assert load_alert_config(env) == ("fake-bot", "C_PLANS")
    assert "system_channel_fallback" in capsys.readouterr().out


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
    assert "ok reason=fresh age=60s pid=4242" in out
    assert "stale reason=too_old" in out
    assert not (tmp_path / "samples").exists(), "plain checks stay read-only"


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
    sources = StatusSources(recap_dir=tmp_path / "recap", log_dir=tmp_path / "logs")
    times = [T0 + timedelta(minutes=1), T0 + timedelta(hours=1),
             T0 + timedelta(hours=1, minutes=5)]
    codes = [run_cli(args, now=t, env=env, poster_factory=factory, status_sources=sources)
             for t in times]
    assert codes == [EXIT_OK, EXIT_STALE, EXIT_STALE]
    # 09:01 daily status (once per date); 10:00 suspect, no post; 10:05 outage post.
    assert [len(p.posts) for p in posters] == [1, 0, 1]
    assert posters[0].posts[0]["text"].startswith("cec-vivisystem daily status")
    assert posters[2].posts[0]["text"].startswith("⚠ Listener: stopped.")
    assert all(p["channel_id"] == "D_OPS" for poster in posters for p in poster.posts)
    samples = list((tmp_path / "samples").glob("*.jsonl"))
    assert len(samples) == 1 and len(samples[0].read_text().splitlines()) == 3


def test_cli_alert_without_config_is_visible(tmp_path: Path, capsys) -> None:
    path = tmp_path / "listener.json"
    _write(path)
    code = run_cli(
        ["--path", str(path), "--state-path", str(tmp_path / "s.json"), "--alert"],
        now=T0, env={}, poster_factory=lambda token: FakePoster(),
    )
    assert code == EXIT_ALERT_CONFIG
    assert "health_alert_config_failed" in capsys.readouterr().out
