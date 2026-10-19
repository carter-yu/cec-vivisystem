"""09:00 daily status and checker samples (Phase 30c, ADR 0014).

The health checker appends one JSON line per ``--alert`` run to
``data/health/samples/YYYY-MM-DD.jsonl`` (class E, purged after 14 days). The
first run at or after 09:00 HKT posts one daily status per HKT date to the
system-status channel, always: its absence is the liveness signal for the
FileVault/login blind spot (ADR 0013).

Contract: everything here is read-only except the sample files. Garbage
samples, markers or logs are skipped with a warning and never stop the post.
Report only what a record shows; when a source is absent, omit the line
instead of guessing. Numbers come from 5-minute samples of the listener's
counters, so they are approximate.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from cec_vivisystem.health_policy import AlertPhase, AlertState
from cec_vivisystem.logging import default_log_dir, get_logger
from cec_vivisystem.status_channel import ENV_STATUS_CHANNEL
from cec_vivisystem.status_messages import (
    NEXT_STEP_EN,
    NEXT_STEP_TC,
    bilingual,
    en_date,
    en_duration,
    en_time,
    tc_date,
    tc_duration,
    tc_time,
)

logger = get_logger(__name__)

COMPONENT = "health"
FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
WINDOW_S = 24 * 3600
# Pairs of samples further apart than this are a gap (checker not running),
# not evidence of a connection either way.
MAX_SAMPLE_GAP_S = 900.0
_SAMPLE_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")
_EVENT = re.compile(r"event='([a-z0-9_]+)'")
_STOPPED_CHECKS = frozenset({"missing", "too_old", "invalid", "unreadable", "future_timestamp"})


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class StatusSources:
    """Where the daily status reads job outcomes (injectable for tests)."""

    recap_dir: Path
    log_dir: Path

    @classmethod
    def default(cls) -> StatusSources:
        return cls(recap_dir=_repo_root() / "data" / "morning_recap", log_dir=default_log_dir())


# --- samples ---------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _num(raw: object) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


def _aware(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def record_sample(
    samples_dir: Path,
    *,
    check: Any,
    heartbeat: Mapping[str, Any] | None,
    now: datetime,
    alert_phase: str,
    alert_action: str,
) -> bool:
    """Append one line for this checker run. Never raises."""
    hb = heartbeat or {}
    line = {
        "checked_at": now.isoformat(),
        "status": check.status.value,
        "reason": check.reason,
        "pid": check.pid,
        "instance_id": check.instance_id,
        "connected": check.socket_mode_connected,
        "started_at": hb.get("started_at") if isinstance(hb.get("started_at"), str) else None,
        "outage_since": (
            hb.get("outage_since") if isinstance(hb.get("outage_since"), str) else None
        ),
        "disconnected_s": _num(hb.get("disconnected_s")),
        "disconnect_episodes": _num(hb.get("disconnect_episodes")),
        "last_outage_s": _num(hb.get("last_outage_s")),
        "last_outage_ended_at": (
            hb.get("last_outage_ended_at")
            if isinstance(hb.get("last_outage_ended_at"), str)
            else None
        ),
        "alert_phase": alert_phase,
        "alert_action": alert_action,
    }
    path = samples_dir / f"{now.astimezone(FAMILY_TZ).date().isoformat()}.jsonl"
    try:
        samples_dir.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, sort_keys=True) + "\n")
    except OSError as exc:
        logger.error(
            "health_sample_write_failed",
            component=COMPONENT,
            outcome="failure",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return False
    return True


def purge_samples(samples_dir: Path, *, now: datetime, keep_days: int) -> int:
    """Delete ``YYYY-MM-DD.jsonl`` files older than ``keep_days``. Never raises."""
    cutoff = now.astimezone(FAMILY_TZ).date() - timedelta(days=keep_days)
    deleted = 0
    try:
        paths = list(samples_dir.glob("*.jsonl"))
    except OSError:
        return 0
    for path in paths:
        match = _SAMPLE_NAME.match(path.name)
        if match is None:
            continue
        try:
            day = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if day < cutoff:
            try:
                path.unlink(missing_ok=True)
                deleted += 1
            except OSError:
                continue
    return deleted


@dataclass(frozen=True, slots=True)
class Sample:
    checked_at: datetime
    status: str
    reason: str
    connected: bool | None
    instance_id: str | None
    started_at: datetime | None
    outage_since: datetime | None
    disconnected_s: float | None
    disconnect_episodes: float | None
    last_outage_s: float | None
    last_outage_ended_at: datetime | None

    @property
    def stopped(self) -> bool:
        return self.status != "ok" and self.reason in _STOPPED_CHECKS


def _sample_from(raw: object) -> Sample | None:
    if not isinstance(raw, dict):
        return None
    checked = _aware(raw.get("checked_at"))
    status, reason = raw.get("status"), raw.get("reason")
    if checked is None or not isinstance(status, str) or not isinstance(reason, str):
        return None
    connected = raw.get("connected")
    instance = raw.get("instance_id")
    return Sample(
        checked_at=checked,
        status=status,
        reason=reason,
        connected=connected if isinstance(connected, bool) else None,
        instance_id=instance if isinstance(instance, str) else None,
        started_at=_aware(raw.get("started_at")),
        outage_since=_aware(raw.get("outage_since")),
        disconnected_s=_num(raw.get("disconnected_s")),
        disconnect_episodes=_num(raw.get("disconnect_episodes")),
        last_outage_s=_num(raw.get("last_outage_s")),
        last_outage_ended_at=_aware(raw.get("last_outage_ended_at")),
    )


def load_samples(samples_dir: Path, *, now: datetime, days: int = 2) -> list[Sample]:
    """Samples from the last ``days`` HKT dates plus today, oldest first."""
    today = now.astimezone(FAMILY_TZ).date()
    samples: list[Sample] = []
    skipped = 0
    for offset in range(days, -1, -1):
        path = samples_dir / f"{(today - timedelta(days=offset)).isoformat()}.jsonl"
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            continue
        except OSError:
            skipped += 1
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                sample = _sample_from(json.loads(line))
            except ValueError:
                sample = None
            if sample is None:
                skipped += 1
                continue
            samples.append(sample)
    if skipped:
        logger.warning(
            "health_samples_skipped",
            component=COMPONENT,
            outcome="partial",
            skipped=skipped,
        )
    samples.sort(key=lambda s: s.checked_at)
    return samples


@dataclass(frozen=True, slots=True)
class SampleSummary:
    connected_pct: float
    disconnects: int
    longest_s: float
    restarts: int
    covered_s: float


def summarize_samples(samples: list[Sample], *, now: datetime) -> SampleSummary | None:
    """Connected %, disconnects, longest outage and restarts over 24 h."""
    window_start = now - timedelta(seconds=WINDOW_S)
    usable = [s for s in samples if s.checked_at <= now]
    in_window = [s for s in usable if s.checked_at >= window_start]
    before = [s for s in usable if s.checked_at < window_start]
    sequence = before[-1:] + in_window
    if not in_window or len(sequence) < 2:
        return None

    covered = down = 0.0
    episodes = restarts = 0
    stopped_runs: list[float] = []
    run = 0.0
    for a, b in pairwise(sequence):
        interval = (b.checked_at - a.checked_at).total_seconds()
        restarted = bool(a.instance_id and b.instance_id and a.instance_id != b.instance_id)
        restarts += int(restarted)
        if interval <= 0 or interval > MAX_SAMPLE_GAP_S:
            if run:
                stopped_runs.append(run)
                run = 0.0
            continue
        covered += interval
        if b.stopped:
            down += interval
            run += interval
            continue
        if run:
            stopped_runs.append(run)
            run = 0.0
        if b.disconnected_s is not None and b.disconnect_episodes is not None:
            if (
                not restarted
                and a.instance_id == b.instance_id
                and a.disconnected_s is not None
                and a.disconnect_episodes is not None
            ):
                lost = b.disconnected_s - a.disconnected_s
                episodes += max(0, int(b.disconnect_episodes - a.disconnect_episodes))
            else:
                # New process: its counters start at 0; count the time before
                # it started as down.
                gap = 0.0
                if b.started_at is not None:
                    gap = max(0.0, (b.started_at - a.checked_at).total_seconds())
                lost = b.disconnected_s + gap
                episodes += int(b.disconnect_episodes)
            down += min(interval, max(0.0, lost))
        elif b.connected is False:
            # Phase 29 heartbeat without counters: the snapshot is all we have.
            down += interval
    if run:
        stopped_runs.append(run)
    if covered <= 0:
        return None

    longest = max(stopped_runs, default=0.0)
    for s in in_window:
        if s.outage_since is not None:
            longest = max(longest, (s.checked_at - s.outage_since).total_seconds())
        if (
            s.last_outage_s is not None
            and s.last_outage_ended_at is not None
            and s.last_outage_ended_at >= window_start
        ):
            longest = max(longest, s.last_outage_s)
    pct = max(0.0, min(100.0, 100.0 * (1 - down / covered)))
    # Round down so any lost time never shows as 100.0 %.
    pct = int(pct * 10) / 10
    return SampleSummary(
        connected_pct=pct,
        disconnects=episodes + len(stopped_runs),
        longest_s=longest,
        restarts=restarts,
        covered_s=covered,
    )


# --- job outcomes (existing markers and dated logs only) -------------------


def _log_events(log_dir: Path, component: str, day: date) -> list[str] | None:
    """Event names from ``{component}-{day}.log`` (active or archive), or None."""
    name = f"{component}-{day.isoformat()}.log"
    events: list[str] = []
    found = False
    for folder in (log_dir / "archive", log_dir):
        path = folder / name
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        except OSError:
            continue
        found = True
        for line in text.splitlines():
            match = _EVENT.search(line)
            if match:
                events.append(match.group(1))
    return events if found else None


def recap_state(sources: StatusSources, *, day: date) -> tuple[str, datetime | None]:
    """``posted`` (with time), ``pending``, ``failed``, ``missing`` or ``unknown``."""
    path = sources.recap_dir / f"{day.isoformat()}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = None
    except (OSError, ValueError):
        logger.warning("health_status_marker_unreadable", component=COMPONENT,
                       outcome="partial", marker="morning_recap")
        return "unknown", None
    if raw is not None:
        if not isinstance(raw, dict):
            logger.warning("health_status_marker_unreadable", component=COMPONENT,
                           outcome="partial", marker="morning_recap")
            return "unknown", None
        status = raw.get("status", "posted")
        if status == "pending":
            return "pending", None
        posted_at = _aware(raw.get("posted_at"))
        if status == "posted" and posted_at is not None:
            return "posted", posted_at
        logger.warning("health_status_marker_unreadable", component=COMPONENT,
                       outcome="partial", marker="morning_recap")
        return "unknown", None
    events = _log_events(sources.log_dir, "morning_recap", day) or []
    if "morning_recap_failed" in events:
        return "failed", None
    return "missing", None


_DATES_OUTCOMES = {
    "important_dates_review_posted": "posted",
    "important_dates_review_skipped": "nothing",
    "important_dates_review_failed": "failed",
}


def dates_job_state(sources: StatusSources, *, today: date) -> tuple[date, str] | None:
    """Latest 10:00 dates-review outcome from today's or yesterday's log."""
    for day in (today, today - timedelta(days=1)):
        events = _log_events(sources.log_dir, "important_dates", day)
        if not events:
            continue
        outcomes = [_DATES_OUTCOMES[e] for e in events if e in _DATES_OUTCOMES]
        if outcomes:
            return day, outcomes[-1]
    return None


def token_state(
    env: Mapping[str, str], *, now: datetime
) -> tuple[int, date] | None:
    """``(days_left, expires_on)`` from the configured issue date, or None."""
    from cec_vivisystem.google_token_reminder import (
        days_until_expiry,
        load_issued_at,
        load_ttl_days,
    )

    issued_at = load_issued_at(env)
    if issued_at is None:
        return None
    ttl = load_ttl_days(env)
    days_left = days_until_expiry(issued_at=issued_at, now=now, ttl_days=ttl)
    return days_left, now.astimezone(FAMILY_TZ).date() + timedelta(days=days_left)


# --- text ------------------------------------------------------------------


def _listener_lines(check: Any, now: datetime) -> tuple[str, str]:
    pid_en = f" PID {check.pid}." if check.pid is not None else ""
    pid_tc = f"PID {check.pid}。" if check.pid is not None else ""
    since = check.outage_started_at
    if check.status.value == "ok":
        if check.socket_mode_connected is False and since is not None:
            return (
                f"Listener: running. No Slack connection from {en_time(since, ref=now)}.{pid_en}",
                f"監聽程式：運行中。由 {tc_time(since, ref=now)} 起沒有 Slack 連線。{pid_tc}",
            )
        return f"Listener: running.{pid_en}", f"監聽程式：運行中。{pid_tc}"
    if check.reason == "socket_disconnected" and since is not None:
        seconds = (now - since).total_seconds()
        return (
            (
                f"Listener: no Slack connection from {en_time(since, ref=now)} "
                f"({en_duration(seconds)}).{pid_en}"
            ),
            (
                f"監聽程式：由 {tc_time(since, ref=now)} 起沒有 Slack 連線"
                f"（{tc_duration(seconds)}）。{pid_tc}"
            ),
        )
    if check.reason == "too_old" and check.written_at is not None:
        return (
            (
                "Listener: stopped. The last heartbeat was at "
                f"{en_time(check.written_at, ref=now)}.{pid_en}"
            ),
            f"監聽程式：已停止。最後心跳訊號在 {tc_time(check.written_at, ref=now)}。{pid_tc}",
        )
    if check.reason == "missing":
        return "Listener: no heartbeat data.", "監聽程式：沒有心跳訊號資料。"
    return (
        f"Listener: the heartbeat data is not valid ({check.reason}).",
        f"監聽程式：心跳訊號資料無效（{check.reason}）。",
    )


def build_daily_status(
    *,
    now: datetime,
    check: Any,
    state: AlertState,
    samples_dir: Path,
    sources: StatusSources,
    env: Mapping[str, str] | None = None,
) -> str:
    """Bilingual daily status. Omits any line whose source is absent."""
    source_env = env if env is not None else os.environ
    today = now.astimezone(FAMILY_TZ).date()
    en = [f"cec-vivisystem daily status: {en_date(today)}, {now:%H:%M}."]
    tc = [f"cec-vivisystem 每日狀態：{tc_date(today)}{now:%H:%M}。"]

    line_en, line_tc = _listener_lines(check, now)
    en.append(line_en)
    tc.append(line_tc)

    summary = summarize_samples(load_samples(samples_dir, now=now), now=now)
    if summary is None:
        en.append("No data for the last 24 hours.")
        tc.append("沒有過去 24 小時的資料。")
    else:
        if summary.disconnects:
            longest_en = f" (longest {en_duration(summary.longest_s)})"
            longest_tc = f"（最長 {tc_duration(summary.longest_s)}）"
        else:
            longest_en = longest_tc = ""
        en.append(
            f"Last 24 hours: connected {summary.connected_pct:.1f}%. "
            f"Disconnects: {summary.disconnects}{longest_en}. "
            f"Restarts: {summary.restarts}."
        )
        tc.append(
            f"過去 24 小時：連線率 {summary.connected_pct:.1f}%。"
            f"連線中斷：{summary.disconnects} 次{longest_tc}。"
            f"重新啟動：{summary.restarts} 次。"
        )

    if state.phase in (AlertPhase.ALERTED, AlertPhase.RECOVERING) and (
        state.outage_started_at is not None
    ):
        start = state.outage_started_at
        en.append(f"Outage alert: open from {en_time(start, ref=now)}.")
        tc.append(f"警報：由 {tc_time(start, ref=now)} 起的中斷警報仍未解除。")
    else:
        en.append("Outage alert: none open.")
        tc.append("警報：沒有未處理的警報。")

    recap, posted_at = recap_state(sources, day=today)
    if recap == "posted" and posted_at is not None:
        en.append(f"07:00 recap: posted at {en_time(posted_at, ref=now)}.")
        tc.append(f"07:00 早上摘要：已於 {tc_time(posted_at, ref=now)} 發送。")
    elif recap == "pending":
        en.append(
            "07:00 recap: the delivery is not confirmed. Do not start the recap again. "
            "Read the reconciliation steps first."
        )
        tc.append("07:00 早上摘要：未能確認是否已發送。請勿再次執行早上摘要。請先閱讀核對步驟。")
    elif recap == "failed":
        en.append("07:00 recap: failed. No recap was posted today.")
        tc.append("07:00 早上摘要：執行失敗。今日沒有發送早上摘要。")
    elif recap == "missing":
        en.append("07:00 recap: not posted today.")
        tc.append("07:00 早上摘要：今日沒有發送。")
    else:
        en.append(f"07:00 recap: the delivery record is not valid. {NEXT_STEP_EN}")
        tc.append(f"07:00 早上摘要：發送記錄無效。{NEXT_STEP_TC}")

    job = dates_job_state(sources, today=today)
    if job is not None:
        day, outcome = job
        words = {
            "posted": ("posted", "已發送"),
            "nothing": ("ran, nothing to post", "已執行，沒有需要發送的內容"),
            "failed": ("failed", "執行失敗"),
        }[outcome]
        en.append(f"10:00 dates review ({en_date(day)}): {words[0]}.")
        tc.append(f"10:00 重要日子（{tc_date(day)}）：{words[1]}。")

    token = token_state(source_env, now=now)
    if token is None:
        en.append("Google access: no issue date is set.")
        tc.append("Google 授權：未設定授權日期。")
    else:
        days_left, expires_on = token
        if days_left < 0:
            en.append(
                f"Google access: EXPIRED on {en_date(expires_on)}. "
                "Re-authorize Google access."
            )
            tc.append(f"Google 授權：已於 {tc_date(expires_on)}到期。請重新授權 Google。")
        elif days_left == 0:
            en.append("Google access: expires today.")
            tc.append("Google 授權：今日到期。")
        else:
            unit = "day" if days_left == 1 else "days"
            en.append(
                f"Google access: expires in {days_left} {unit} ({en_date(expires_on)})."
            )
            tc.append(f"Google 授權：{days_left} 日後到期，到期日為 {tc_date(expires_on)}。")

    if not (source_env.get(ENV_STATUS_CHANNEL) or "").strip():
        en.append(
            "No system status channel is set. System messages go to the family "
            "plans channel."
        )
        tc.append("未設定系統狀態頻道。系統訊息會發送到家庭計劃頻道。")

    return bilingual("\n".join(en), "\n".join(tc))
