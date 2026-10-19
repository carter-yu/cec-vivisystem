"""Phase 30a/30c: alert policy v2, status messages and the 09:00 daily status.

Pure policy cases use fixed HKT clocks; CLI cases use temporary directories
and a fake poster. No network, sleeps or real launchd.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem.daily_status import (
    StatusSources,
    build_daily_status,
    load_samples,
    purge_samples,
    summarize_samples,
)
from cec_vivisystem.health import HealthCheck, HealthStatus, run_cli
from cec_vivisystem.health_policy import (
    AlertKind,
    AlertPhase,
    AlertState,
    CheckView,
    evaluate_alert,
)
from cec_vivisystem.models import SlackPostReceipt
from cec_vivisystem.parser import contains_simplified_markers
from cec_vivisystem.status_messages import (
    format_outage,
    format_recovered,
    format_still_down,
    format_token_notice,
    format_unstable,
)

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
T0 = datetime(2026, 10, 13, 14, 0, tzinfo=FAMILY_TZ)
CHECK_S = 300


def down(start: datetime, *, reason: str = "socket_disconnected", pid: int = 4182) -> CheckView:
    return CheckView(healthy=False, reason=reason, pid=pid, outage_started_at=start)


def up(pid: int = 4182) -> CheckView:
    return CheckView(healthy=True, reason="fresh", pid=pid)


class Run:
    """Feed checks through evaluate_alert, saving state only when a post succeeds."""

    def __init__(self) -> None:
        self.state = AlertState()
        self.posts: list[tuple[datetime, AlertKind, str]] = []
        self.suppressed: list[str | None] = []

    def check(self, view: CheckView, now: datetime, *, post_ok: bool = True) -> None:
        decision = evaluate_alert(self.state, view, now=now)
        self.suppressed.append(decision.suppressed)
        if decision.action is None:
            self.state = decision.state
        elif post_ok:
            self.posts.append((now, decision.action.kind, decision.action.text))
            self.state = decision.state


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


# --- policy -----------------------------------------------------------------


def test_p2_single_unhealthy_check_only_suspects() -> None:
    run = Run()
    run.check(down(at(-12)), at(0))
    assert run.posts == [] and run.state.phase == AlertPhase.SUSPECT
    assert run.suppressed == ["hysteresis"]


def test_p3_two_checks_and_fifteen_minutes_open_one_outage() -> None:
    run = Run()
    run.check(down(at(-11)), at(0))
    run.check(down(at(-11)), at(5))
    run.check(down(at(-11)), at(10))
    assert [kind for _, kind, _ in run.posts] == [AlertKind.OUTAGE]
    text = run.posts[0][2]
    assert "The connection stopped at 13:49 (16 minutes)." in text
    assert "PID 4182." in text and "連線於 13:49 中斷（16 分鐘）" in text
    assert run.state.phase == AlertPhase.ALERTED


def test_p3_two_checks_wait_for_fifteen_minute_outage_age() -> None:
    run = Run()
    run.check(down(at(0), reason="missing"), at(0))
    run.check(down(at(0), reason="missing"), at(5))
    run.check(down(at(0), reason="missing"), at(10))
    assert run.posts == []
    run.check(down(at(0), reason="missing"), at(15))
    assert len(run.posts) == 1


def test_p4_brief_problem_never_posts() -> None:
    run = Run()
    run.check(down(at(-11)), at(0))
    run.check(up(), at(5))
    assert run.posts == [] and run.state.phase == AlertPhase.OK
    assert run.state.outage_started_at is None


def _alerted() -> Run:
    run = Run()
    run.check(down(at(-11)), at(0))
    run.check(down(at(-11)), at(5))
    assert len(run.posts) == 1
    return run


def test_p5_p6_recovery_needs_three_checks_and_fifteen_minutes() -> None:
    run = _alerted()
    run.check(up(), at(40))
    run.check(up(), at(45))
    assert run.state.phase == AlertPhase.RECOVERING and len(run.posts) == 1
    run.check(up(), at(50))  # 3 checks but only 10 minutes
    assert len(run.posts) == 1
    run.check(up(), at(55))
    assert [kind for _, kind, _ in run.posts] == [AlertKind.OUTAGE, AlertKind.RECOVERED]
    text = run.posts[1][2]
    assert "connection restored at 14:40. Outage: 13:49–14:40 (51 minutes)." in text
    assert "中斷時間：13:49–14:40（51 分鐘）" in text
    assert run.state.phase == AlertPhase.OK


def test_p7_relapse_while_recovering_returns_to_alerted_without_post() -> None:
    run = _alerted()
    run.check(up(), at(40))
    run.check(up(), at(45))
    run.check(down(at(47)), at(50))
    assert run.state.phase == AlertPhase.ALERTED and len(run.posts) == 1
    assert run.suppressed[-1] == "relapse"
    assert run.state.outage_started_at == at(-11), "the original start is kept"


def test_p8_second_outage_within_six_hours_is_unstable() -> None:
    run = _alerted()
    for minute in (40, 45, 50, 55):
        run.check(up(), at(minute))
    run.check(down(at(80)), at(95))
    run.check(down(at(80)), at(100))
    kinds = [kind for _, kind, _ in run.posts]
    assert kinds == [AlertKind.OUTAGE, AlertKind.RECOVERED, AlertKind.UNSTABLE]
    assert "It stopped 2 times in 6 hours." in run.posts[2][2]
    for minute in (105, 110, 115, 120):  # 15 minutes is not enough now
        run.check(up(), at(minute))
    assert len(run.posts) == 3
    for minute in range(125, 170, 5):
        run.check(up(), at(minute))
    assert run.posts[-1][1] == AlertKind.RECOVERED and run.posts[-1][0] == at(165)


def _replay(pattern_minutes: int, *, cadence_s: int, hours: int = 3) -> tuple[int, int]:
    """Verdict alternates every ``pattern_minutes``. Returns (v1 posts, v2 posts)."""
    run = Run()
    v1_posts = 0
    v1_alerted = False
    t = 0
    while t < hours * 3600:
        unhealthy = (t // 60 // pattern_minutes) % 2 == 0
        if unhealthy != v1_alerted:
            v1_posts += 1
            v1_alerted = unhealthy
        now = T0 + timedelta(seconds=t)
        if unhealthy:
            # The heartbeat outage began 11 min before the first stale verdict.
            block_start = T0 + timedelta(minutes=(t // 60 // pattern_minutes) * pattern_minutes)
            run.check(down(block_start - timedelta(minutes=11)), now)
        else:
            run.check(up(), now)
        t += cadence_s
    return v1_posts, len(run.posts)


def test_p9_replay_of_the_flapping_pattern() -> None:
    """Baseline v1 posts 18 / 9 per 3 h; v2 posts at most 1 / 3."""
    v1, _ = _replay(10, cadence_s=600)
    assert v1 == 18
    _, v2 = _replay(10, cadence_s=CHECK_S)
    assert v2 == 1  # outage only; healthy stretches never reach 15 min
    v1, _ = _replay(20, cadence_s=600)
    assert v1 == 9
    _, v2 = _replay(20, cadence_s=CHECK_S)
    assert v2 == 3  # outage, recovered, unstable


def test_p10_still_down_every_six_hours() -> None:
    run = _alerted()
    minute = 10
    while minute <= 13 * 60:
        run.check(down(at(-11)), at(minute))
        minute += 5
    reminders = [when for when, kind, _ in run.posts if kind == AlertKind.STILL_DOWN]
    assert reminders == [at(5 + 6 * 60), at(5 + 12 * 60)]
    text = next(t for _, kind, t in run.posts if kind == AlertKind.STILL_DOWN)
    assert "still no Slack connection. The outage started at 13:49 (6 hours 16 minutes)" in text


def test_p11_min_gap_defers_the_second_post() -> None:
    run = _alerted()  # outage post at 14:05
    for minute in (10, 15, 20, 25):
        run.check(up(), at(minute))
    assert len(run.posts) == 1, "recovery ready at 14:25 but within 30 min of 14:05"
    assert run.suppressed[-1] == "min_gap"
    run.check(up(), at(30))
    run.check(up(), at(35))
    assert [when for when, _, _ in run.posts] == [at(5), at(35)]


def test_p12_failed_post_is_retried_by_the_next_check() -> None:
    run = Run()
    run.check(down(at(-11)), at(0))
    run.check(down(at(-11)), at(5), post_ok=False)
    assert run.posts == [] and run.state.phase == AlertPhase.SUSPECT
    run.check(down(at(-11)), at(10))
    assert len(run.posts) == 1


def test_p14_missing_heartbeat_at_install() -> None:
    run = Run()
    for minute in (0, 5, 10, 15):
        run.check(CheckView(healthy=False, reason="missing"), at(minute))
    assert [when for when, _, _ in run.posts] == [at(15)]
    text = run.posts[0][2]
    assert "no heartbeat data" in text and "14:00 (15 minutes)" in text
    assert "沒有心跳訊號資料" in text and "PID" not in text


def test_state_round_trips_through_json() -> None:
    from cec_vivisystem.health_policy import state_from_json

    run = _alerted()
    restored = state_from_json(json.loads(json.dumps(run.state.to_json())))
    assert restored == run.state


# --- message catalog (P15) --------------------------------------------------


def _all_messages() -> list[str]:
    start, now = at(-25), at(0)
    texts = [
        format_outage(reason=reason, started_at=start, now=now, pid=4182)
        for reason in ("socket_disconnected", "too_old", "missing", "invalid",
                       "unreadable", "future_timestamp")
    ]
    texts.append(format_outage(reason="too_old", started_at=start, now=now, pid=1,
                               exit_reason="socket_mode_stuck"))
    texts.append(format_unstable(count=2, window_hours=6, started_at=start, now=now,
                                 recovery_minutes=60, pid=4182))
    texts += [format_still_down(reason=r, started_at=start, now=now, pid=4182)
              for r in ("socket_disconnected", "too_old", "missing", "invalid")]
    texts += [format_recovered(reason=r, started_at=start, restored_at=now, now=now, pid=1)
              for r in ("socket_disconnected", "too_old")]
    texts += [format_token_notice(d, expires_on=now.date()) for d in (2, 1, 0, -3)]
    return texts


@pytest.mark.parametrize("text", _all_messages())
def test_p15_messages_are_bilingual_traditional_and_safe(text: str) -> None:
    en, tc = text.split("\n\n")
    assert en.isascii() or en.startswith(("⚠", "✓"))
    assert any("一" <= ch <= "鿿" for ch in tc)
    assert not any("一" <= ch <= "鿿" for ch in en)
    assert not contains_simplified_markers(text)
    assert "xox" not in text and "xapp" not in text


def test_p15_outage_posts_say_the_check_does_not_restart() -> None:
    for text in _all_messages()[:12]:
        assert "This check does not restart the listener." in text
        assert "此檢查不會重新啟動監聽程式。" in text


def test_times_on_another_date_show_the_date() -> None:
    text = format_outage(reason="socket_disconnected", started_at=at(-15 * 60),
                         now=at(0), pid=1)
    assert "Mon 12 Oct 23:00 (15 hours)" in text
    assert "10月12日（星期一）23:00" in text


# --- samples and daily status (30c) -----------------------------------------


class FakePoster:
    def __init__(self, *, fail_times: int = 0) -> None:
        self.posts: list[dict] = []
        self.fail_times = fail_times

    def post(self, *, channel_id: str, text: str) -> SlackPostReceipt:
        if self.fail_times:
            self.fail_times -= 1
            raise ConnectionError("synthetic slack outage")
        self.posts.append({"channel_id": channel_id, "text": text})
        return SlackPostReceipt(channel_id, f"{len(self.posts)}.0")


ENV = {
    "SLACK_BOT_TOKEN": "fake-bot",
    "CEC_HEALTH_ALERT_CHANNEL_ID": "C_STATUS",
    "GOOGLE_REFRESH_TOKEN_ISSUED_AT": "2026-10-10",
}


def _heartbeat(path: Path, now: datetime, **fields) -> None:
    payload = {
        "written_at": now.isoformat(), "pid": 1735, "socket_mode_connected": True,
        "disconnected_since": None, "heartbeat_version": 2, "instance_id": "inst-a",
        "started_at": (now - timedelta(days=2)).isoformat(), "outage_since": None,
        "disconnect_episodes": 0, "disconnected_s": 0.0, "last_outage_s": None,
        "last_outage_ended_at": None,
    }
    payload.update(fields)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _cli(tmp_path: Path, now: datetime, poster: FakePoster, env: dict | None = None) -> int:
    sources = StatusSources(recap_dir=tmp_path / "recap", log_dir=tmp_path / "logs")
    return run_cli(
        ["--path", str(tmp_path / "listener.json"), "--state-path",
         str(tmp_path / "alert_state.json"), "--alert"],
        now=now, env=env or ENV, poster_factory=lambda token: poster,
        status_sources=sources,
    )


def test_d1_one_sample_per_run_and_purge(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    old = samples / "2026-09-28.jsonl"
    kept = samples / "2026-09-29.jsonl"
    other = samples / "notes.txt"
    for path in (old, kept, other):
        path.write_text("{}\n", encoding="utf-8")
    now = datetime(2026, 10, 13, 8, 0, tzinfo=FAMILY_TZ)
    _heartbeat(tmp_path / "listener.json", now)
    poster = FakePoster()
    _cli(tmp_path, now, poster)
    _cli(tmp_path, now + timedelta(minutes=5), poster)
    today = samples / "2026-10-13.jsonl"
    rows = [json.loads(line) for line in today.read_text().splitlines()]
    assert len(rows) == 2 and rows[0]["instance_id"] == "inst-a"
    assert rows[0]["status"] == "ok" and rows[0]["alert_phase"] == "ok"
    assert not old.exists() and kept.exists() and other.exists()
    assert poster.posts == []
    assert purge_samples(samples, now=now, keep_days=14) == 0


def test_d2_daily_status_once_per_date_from_0900_with_retry(tmp_path: Path, capsys) -> None:
    poster = FakePoster(fail_times=1)
    for hhmm in ((8, 55), (9, 0), (9, 5), (9, 10)):
        now = datetime(2026, 10, 13, *hhmm, tzinfo=FAMILY_TZ)
        _heartbeat(tmp_path / "listener.json", now)
        _cli(tmp_path, now, poster)
    out = capsys.readouterr().out
    assert "health_digest_failed" in out and "health_digest_sent" in out
    assert len(poster.posts) == 1, "08:55 none, 09:00 failed, 09:05 posted, 09:10 none"
    assert poster.posts[0]["channel_id"] == "C_STATUS"
    assert "13 Oct, 09:05" in poster.posts[0]["text"]
    state = json.loads((tmp_path / "alert_state.json").read_text())
    assert state["digest_posted_for"] == "2026-10-13"
    next_day = datetime(2026, 10, 14, 9, 0, tzinfo=FAMILY_TZ)
    _heartbeat(tmp_path / "listener.json", next_day)
    _cli(tmp_path, next_day, poster)
    assert len(poster.posts) == 2


def _write_samples(samples: Path, now: datetime) -> None:
    """24 h at 5-min cadence: one 10-min outage, one restart, then 4 min down."""
    rows = []
    t = now - timedelta(hours=24, minutes=5)
    instance, started = "inst-a", now - timedelta(days=3)
    ds = eps = 0.0
    last_outage_s = last_end = None
    step = 0
    while t <= now:
        outage_since = None
        if step == 100:
            ds, eps = ds + 300, eps + 1
            outage_since = t - timedelta(minutes=5)
        elif step == 101:
            ds += 300
            outage_since = t - timedelta(minutes=10)
        elif step == 102:
            last_outage_s, last_end = 600.0, (t - timedelta(minutes=5)).isoformat()
        if step == 200:
            instance, started = "inst-b", t - timedelta(minutes=2)
            ds, eps, last_outage_s, last_end = 0.0, 0.0, None, None
        if step == 250:
            ds, eps = 240.0, 1.0
            last_outage_s, last_end = 240.0, (t - timedelta(seconds=30)).isoformat()
        rows.append({
            "checked_at": t.isoformat(), "status": "ok", "reason": "fresh",
            "connected": True, "instance_id": instance, "started_at": started.isoformat(),
            "outage_since": outage_since.isoformat() if outage_since else None,
            "disconnected_s": ds, "disconnect_episodes": eps, "last_outage_s": last_outage_s,
            "last_outage_ended_at": last_end, "pid": 1, "alert_phase": "ok",
            "alert_action": "unchanged",
        })
        t += timedelta(minutes=5)
        step += 1
    samples.mkdir(parents=True, exist_ok=True)
    for row in rows:
        day = datetime.fromisoformat(row["checked_at"]).date().isoformat()
        with (samples / f"{day}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")


def test_d3_daily_status_from_fixtures(tmp_path: Path) -> None:
    now = datetime(2026, 10, 13, 9, 0, tzinfo=FAMILY_TZ)
    samples = tmp_path / "samples"
    _write_samples(samples, now)
    summary = summarize_samples(load_samples(samples, now=now), now=now)
    assert summary is not None
    assert summary.disconnects == 2 and summary.restarts == 1
    assert summary.longest_s == 600
    # 600 s + 240 s + 180 s before inst-b started, over 24 h 5 min of coverage.
    assert summary.connected_pct == pytest.approx(98.8)

    recap = tmp_path / "recap"
    recap.mkdir()
    (recap / "2026-10-13.json").write_text(json.dumps({
        "status": "posted", "posted_at": "2026-10-13T07:00:06+08:00",
        "slack_channel_id": "C_PLANS", "slack_ts": "1.0",
    }), encoding="utf-8")
    logs = tmp_path / "logs"
    (logs / "archive").mkdir(parents=True)
    (logs / "archive" / "important_dates-2026-10-12.log").write_text(
        "event='important_dates_review_started' level='info'\n"
        "event='important_dates_review_skipped' level='info' reason='no_new_hits'\n",
        encoding="utf-8",
    )
    text = build_daily_status(
        now=now,
        check=HealthCheck(HealthStatus.OK, "fresh", 20.0, 1735, True),
        state=AlertState(),
        samples_dir=samples,
        sources=StatusSources(recap_dir=recap, log_dir=logs),
        env=ENV,
    )
    en, tc = text.split("\n\n")
    assert en.splitlines() == [
        "cec-vivisystem daily status: Tue 13 Oct, 09:00.",
        "Listener: running. PID 1735.",
        "Last 24 hours: connected 98.8%. Disconnects: 2 (longest 10 minutes). Restarts: 1.",
        "Outage alert: none open.",
        "07:00 recap: posted at 07:00.",
        "10:00 dates review (Mon 12 Oct): ran, nothing to post.",
        "Google access: expires in 4 days (Sat 17 Oct).",
    ]
    assert "過去 24 小時：連線率 98.8%。連線中斷：2 次（最長 10 分鐘）。重新啟動：1 次。" in tc
    assert not contains_simplified_markers(text)


def test_d3_recap_states_expired_token_and_open_alert(tmp_path: Path) -> None:
    now = datetime(2026, 10, 15, 9, 0, tzinfo=FAMILY_TZ)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "morning_recap-2026-10-15.log").write_text(
        "event='morning_recap_failed' level='error' error_type='RefreshError'\n",
        encoding="utf-8",
    )
    state = AlertState(phase=AlertPhase.ALERTED, outage_started_at=now - timedelta(hours=1))
    check = HealthCheck(HealthStatus.STALE, "socket_disconnected", 10.0, 7, False,
                        outage_started_at=now - timedelta(hours=1))
    text = build_daily_status(
        now=now, check=check, state=state, samples_dir=tmp_path / "none",
        sources=StatusSources(recap_dir=tmp_path / "recap", log_dir=logs),
        env={**ENV, "CEC_HEALTH_ALERT_CHANNEL_ID": "",
             "GOOGLE_REFRESH_TOKEN_ISSUED_AT": "2026-10-01"},
    )
    assert "Listener: no Slack connection from 08:00 (1 hour). PID 7." in text
    assert "Outage alert: open from 08:00." in text
    assert "07:00 recap: failed. No recap was posted today." in text
    assert "Google access: EXPIRED on Thu 8 Oct. Re-authorize Google access." in text
    assert "Google 授權：已於 10月8日（星期四）到期。請重新授權 Google。" in text
    assert "System messages go to the family plans channel." in text
    assert "10:00 dates review" not in text, "no record: the line is omitted"

    recap = tmp_path / "recap"
    recap.mkdir()
    (recap / "2026-10-15.json").write_text(json.dumps({"status": "pending"}), encoding="utf-8")
    pending = build_daily_status(
        now=now, check=check, state=state, samples_dir=tmp_path / "none",
        sources=StatusSources(recap_dir=recap, log_dir=logs), env=ENV,
    )
    assert "07:00 recap: the delivery is not confirmed. Do not start the recap again." in pending


def test_d4_fresh_install_still_posts(tmp_path: Path) -> None:
    poster = FakePoster()
    now = datetime(2026, 10, 13, 9, 0, tzinfo=FAMILY_TZ)
    assert _cli(tmp_path, now, poster, env={"SLACK_BOT_TOKEN": "fake-bot",
                                            "CEC_HEALTH_ALERT_CHANNEL_ID": "C_STATUS"}) == 2
    assert len(poster.posts) == 1
    text = poster.posts[0]["text"]
    assert "Listener: no heartbeat data." in text
    assert "No data for the last 24 hours." in text
    assert "07:00 recap: not posted today." in text
    assert "Google access: no issue date is set." in text


def test_g1_garbage_sources_are_skipped_and_status_still_posts(tmp_path: Path, capsys) -> None:
    now = datetime(2026, 10, 13, 9, 0, tzinfo=FAMILY_TZ)
    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "2026-10-13.jsonl").write_text(
        "not json\n[]\n{\"checked_at\": \"nope\"}\n", encoding="utf-8"
    )
    recap = tmp_path / "recap"
    recap.mkdir()
    (recap / "2026-10-13.json").write_text("{broken", encoding="utf-8")
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "important_dates-2026-10-12.log").write_bytes(b"\xff\xfe garbage\n")
    (tmp_path / "alert_state.json").write_text("{oops", encoding="utf-8")
    _heartbeat(tmp_path / "listener.json", now)
    poster = FakePoster()
    sources = StatusSources(recap_dir=recap, log_dir=logs)
    run_cli(["--path", str(tmp_path / "listener.json"), "--state-path",
             str(tmp_path / "alert_state.json"), "--alert"],
            now=now, env=ENV, poster_factory=lambda token: poster, status_sources=sources)
    assert len(poster.posts) == 1
    text = poster.posts[0]["text"]
    assert "07:00 recap: the delivery record is not valid." in text
    out = capsys.readouterr().out
    assert "health_samples_skipped" in out
    assert "health_status_marker_unreadable" in out
    assert "health_alert_state_unreadable" in out
