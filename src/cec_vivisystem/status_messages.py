"""Status-channel message catalog (Phase 30; design note §7.4).

Every post is an English block in ASD-STE100 style (short sentences, active
voice, one instruction per sentence), then a Traditional Chinese (HK) block.
Simplified Chinese is rejected (ground rule 9); tests run the parser's
Simplified-marker check over every template.

Times are 24-hour HKT. Messages contain no tokens, channel IDs or family
content. Until the Phase 31 control app ships, the next step is
"Do the steps in docs/maintenance.md."
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
_EN_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_EN_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
_TC_DAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

NO_RESTART_EN = "This check does not restart the listener."
NO_RESTART_TC = "此檢查不會重新啟動監聽程式。"
NEXT_STEP_EN = "Do the steps in docs/maintenance.md."
NEXT_STEP_TC = "請按 docs/maintenance.md 的步驟處理。"

# Reason codes from health.check_heartbeat grouped by what the operator sees.
SOCKET_REASONS = frozenset({"socket_disconnected"})
STOPPED_REASONS = frozenset({"too_old"})
NO_DATA_REASONS = frozenset({"missing"})


def bilingual(en: str, tc: str) -> str:
    return f"{en}\n\n{tc}"


def _local(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=FAMILY_TZ)
    return value.astimezone(FAMILY_TZ)


def en_date(day: date) -> str:
    """``Wed 14 Oct``."""
    return f"{_EN_DAYS[day.weekday()]} {day.day} {_EN_MONTHS[day.month - 1]}"


def tc_date(day: date) -> str:
    """``10月14日（星期三）``."""
    return f"{day.month}月{day.day}日（{_TC_DAYS[day.weekday()]}）"


def en_time(value: datetime, *, ref: datetime) -> str:
    """``14:05``; adds the date when ``value`` is not on the HKT date of ``ref``."""
    local = _local(value)
    clock = f"{local:%H:%M}"
    if local.date() == _local(ref).date():
        return clock
    return f"{en_date(local.date())} {clock}"


def tc_time(value: datetime, *, ref: datetime) -> str:
    local = _local(value)
    clock = f"{local:%H:%M}"
    if local.date() == _local(ref).date():
        return clock
    return f"{tc_date(local.date())}{clock}"


def en_duration(seconds: float) -> str:
    """``25 minutes`` / ``1 minute`` / ``6 hours 5 minutes`` / ``2 hours``."""
    minutes = max(0, int(seconds // 60))
    hours, rest = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour" + ("" if hours == 1 else "s"))
    if rest or not hours:
        parts.append(f"{rest} minute" + ("" if rest == 1 else "s"))
    return " ".join(parts)


def tc_duration(seconds: float) -> str:
    """``25 分鐘`` / ``6 小時 5 分鐘``."""
    minutes = max(0, int(seconds // 60))
    hours, rest = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} 小時")
    if rest or not hours:
        parts.append(f"{rest} 分鐘")
    return " ".join(parts)


def _pid_en(pid: int | None) -> str:
    return f" PID {pid}." if pid is not None else ""


def _pid_tc(pid: int | None) -> str:
    return f"PID {pid}。" if pid is not None else ""


def _invalid_words(reason: str) -> tuple[str, str]:
    if reason == "future_timestamp":
        return (
            "the heartbeat time is in the future. Examine the Mini clock",
            "心跳訊號時間在未來。請檢查 Mac mini 的時鐘",
        )
    if reason == "unreadable":
        return ("the heartbeat file cannot be read", "未能讀取心跳訊號檔案")
    return ("the heartbeat data is not valid", "心跳訊號資料無效")


STUCK_EXIT_EN = (
    " The last listener process stopped itself because its Slack connection was stuck."
)
STUCK_EXIT_TC = "上一個監聽程式因 Slack 連線卡住而自行停止。"


def format_outage(
    *,
    reason: str,
    started_at: datetime,
    now: datetime,
    pid: int | None,
    exit_reason: str | None = None,
) -> str:
    """First post for a sustained outage (EN block, then TC block)."""
    start_en, start_tc = en_time(started_at, ref=now), tc_time(started_at, ref=now)
    seconds = (now - started_at).total_seconds()
    dur_en, dur_tc = en_duration(seconds), tc_duration(seconds)
    if reason in SOCKET_REASONS:
        en = (
            f"⚠ Listener: no Slack connection. The connection stopped at {start_en} "
            f"({dur_en}). The bot cannot reply to family messages. "
            f"{NO_RESTART_EN} {NEXT_STEP_EN}{_pid_en(pid)}"
        )
        tc = (
            f"⚠ 監聽程式：沒有 Slack 連線。連線於 {start_tc} 中斷（{dur_tc}）。"
            f"機械人未能回覆家人的訊息。{NO_RESTART_TC}{NEXT_STEP_TC}{_pid_tc(pid)}"
        )
    elif reason in STOPPED_REASONS:
        stuck_en = STUCK_EXIT_EN if exit_reason == "socket_mode_stuck" else ""
        stuck_tc = STUCK_EXIT_TC if exit_reason == "socket_mode_stuck" else ""
        en = (
            f"⚠ Listener: stopped. The last heartbeat was at {start_en} ({dur_en})."
            f"{stuck_en} launchd starts the listener again if the process stopped. "
            f"{NO_RESTART_EN} {NEXT_STEP_EN}{_pid_en(pid)}"
        )
        tc = (
            f"⚠ 監聽程式：已停止。最後心跳訊號在 {start_tc}（{dur_tc}前）。{stuck_tc}"
            f"如程式停止，launchd 會再次啟動監聽程式。{NO_RESTART_TC}{NEXT_STEP_TC}"
            f"{_pid_tc(pid)}"
        )
    elif reason in NO_DATA_REASONS:
        en = (
            "⚠ Listener: no heartbeat data. The listener did not start, or the "
            f"heartbeat file is missing. The first check without data was at {start_en} "
            f"({dur_en}). {NO_RESTART_EN} {NEXT_STEP_EN}"
        )
        tc = (
            "⚠ 監聽程式：沒有心跳訊號資料。監聽程式未有啟動，或心跳訊號檔案不存在。"
            f"首次沒有資料的檢查在 {start_tc}（{dur_tc}）。{NO_RESTART_TC}{NEXT_STEP_TC}"
        )
    else:
        words_en, words_tc = _invalid_words(reason)
        en = (
            f"⚠ Listener: {words_en}. The problem started at {start_en} ({dur_en}). "
            f"{NO_RESTART_EN} {NEXT_STEP_EN}{_pid_en(pid)}"
        )
        tc = (
            f"⚠ 監聽程式：{words_tc}。問題由 {start_tc} 開始（{dur_tc}）。"
            f"{NO_RESTART_TC}{NEXT_STEP_TC}{_pid_tc(pid)}"
        )
    return bilingual(en, tc)


def format_unstable(
    *,
    count: int,
    window_hours: int,
    started_at: datetime,
    now: datetime,
    recovery_minutes: int,
    pid: int | None,
) -> str:
    start_en, start_tc = en_time(started_at, ref=now), tc_time(started_at, ref=now)
    seconds = (now - started_at).total_seconds()
    en = (
        f"⚠ Listener: the connection is unstable. It stopped {count} times in "
        f"{window_hours} hours. The latest outage started at {start_en} "
        f"({en_duration(seconds)}). The next recovery message comes after "
        f"{recovery_minutes} minutes of stable connection. {NO_RESTART_EN}{_pid_en(pid)}"
    )
    tc = (
        f"⚠ 監聽程式：連線不穩定。{window_hours} 小時內中斷了 {count} 次。"
        f"最近一次中斷由 {start_tc} 開始（{tc_duration(seconds)}）。"
        f"連線穩定 {recovery_minutes} 分鐘後才會發出恢復訊息。{NO_RESTART_TC}{_pid_tc(pid)}"
    )
    return bilingual(en, tc)


def format_still_down(
    *, reason: str, started_at: datetime, now: datetime, pid: int | None
) -> str:
    start_en, start_tc = en_time(started_at, ref=now), tc_time(started_at, ref=now)
    seconds = (now - started_at).total_seconds()
    if reason in SOCKET_REASONS:
        head_en, head_tc = "still no Slack connection", "仍然沒有 Slack 連線"
    elif reason in STOPPED_REASONS:
        head_en, head_tc = "still stopped", "仍然停止"
    elif reason in NO_DATA_REASONS:
        head_en, head_tc = "still no heartbeat data", "仍然沒有心跳訊號資料"
    else:
        head_en, head_tc = "the heartbeat data is still not valid", "心跳訊號資料仍然無效"
    en = (
        f"⚠ Listener: {head_en}. The outage started at {start_en} "
        f"({en_duration(seconds)}). {NO_RESTART_EN} {NEXT_STEP_EN}{_pid_en(pid)}"
    )
    tc = (
        f"⚠ 監聽程式：{head_tc}。中斷由 {start_tc} 開始（{tc_duration(seconds)}）。"
        f"{NO_RESTART_TC}{NEXT_STEP_TC}{_pid_tc(pid)}"
    )
    return bilingual(en, tc)


def format_recovered(
    *,
    reason: str,
    started_at: datetime,
    restored_at: datetime,
    now: datetime,
    pid: int | None,
) -> str:
    start_en, start_tc = en_time(started_at, ref=now), tc_time(started_at, ref=now)
    end_en, end_tc = en_time(restored_at, ref=now), tc_time(restored_at, ref=now)
    seconds = (restored_at - started_at).total_seconds()
    if reason in SOCKET_REASONS:
        head_en = f"connection restored at {end_en}"
        head_tc = f"連線已於 {end_tc} 恢復"
    else:
        head_en = f"running again at {end_en}"
        head_tc = f"已於 {end_tc} 恢復運行"
    en = (
        f"✓ Listener: {head_en}. Outage: {start_en}–{end_en} "
        f"({en_duration(seconds)}).{_pid_en(pid)}"
    )
    tc = (
        f"✓ 監聽程式：{head_tc}。中斷時間：{start_tc}–{end_tc}"
        f"（{tc_duration(seconds)}）。{_pid_tc(pid)}"
    )
    return bilingual(en, tc)


def format_token_notice(days_left: int, *, expires_on: date) -> str:
    """Google access reminder (3/2/1 days), "expires today" (0) or "expired" (< 0)."""
    if days_left > 0:
        unit = "day" if days_left == 1 else "days"
        en = (
            f"Google Calendar access expires in {days_left} {unit}. "
            f"Re-authorize Google access before {en_date(expires_on)}."
        )
        tc = (
            f"Google 日曆授權將於 {days_left} 日後到期。"
            f"請在 {tc_date(expires_on)}前重新授權。"
        )
    elif days_left == 0:
        en = (
            f"Google Calendar access expires today ({en_date(expires_on)}). "
            "Re-authorize Google access now."
        )
        tc = f"Google 日曆授權今日（{tc_date(expires_on)}）到期。請立即重新授權。"
    else:
        en = (
            f"Google Calendar access expired on {en_date(expires_on)}. "
            "The bot cannot read or write the calendar. Re-authorize Google access. "
            f"{NEXT_STEP_EN}"
        )
        tc = (
            f"Google 日曆授權已於 {tc_date(expires_on)}到期。"
            f"機械人未能讀取或寫入日曆。請重新授權 Google。{NEXT_STEP_TC}"
        )
    return bilingual(en, tc)
