"""Offline natural-language parser (Phase 1 + 3 + 9–12 + 14 periods + 15 important dates).

Turns mixed Cantonese/English family messages into structured intents.
Rule/heuristic based — no network, no LLM.

This is the current *strategy* behind the stable ``parse`` → ``ParseResult``
contract. An LLM/hybrid backend is optional later only if real use demands it
(architecture §4.4.1); it is not a scheduled next phase.

Weekday / relative / period policy (documented once):
- Bare weekday names (e.g. 星期六, Sunday, Friday) resolve to the **same or
  next** occurrence from ``now`` (same day if ``now`` already falls on that weekday).
- Prefixed 下/下週 forms (e.g. 下星期三) use the same next-or-same occurrence rule
  for Phase 1 fixtures (from Saturday noon, 下星期三 → the coming Wednesday).
- Relative words: 明天 / tomorrow / **聽日** / **聽朝** → calendar day after ``now`` in family TZ.
  聽朝 is tomorrow morning; with bare ``N點`` the hour stays as written (11點 → 11:00).
- Day periods with ``N點``:
  - 上午 / **上晝** → morning (hour 1–11 stay AM; 12 → 0:00)
  - 下午 / 晚上 / **下晝** → afternoon/evening (hour &lt; 12 → hour+12)
  - Period + clock **without** a date (e.g. 下晝2點 alone) → needs_clarification;
    do not invent a calendar day.
  - Bare ``N點`` (no period) keeps the hour as written (9點 → 09:00).
- List queries (Phase 7 + 12 + 14): 有乜 / 有乜嘢 / 有什麼活動 / tell me the events
  + a date or period → ``list_events``. Single day is 00:00–next 00:00 HKT.
  ``聽日有乜嘢活動`` / ``聽日有乜嘢`` / ``聽日有什麼活動`` are list (tomorrow).
  Periods: **今日** (today); **今個星期** / **今個禮拜** (Monday-start this week);
  **下個星期** (next week); **今個月** (calendar month); ``9月1日至9月7日``
  (inclusive days, exclusive end, year from ``now``).
  No date/period → needs_clarification.
- Help (whole message): **help** / **/help** / **指令** / **點用** → ``help``
  and a Slack list of common allowed inputs. Not create.
- Important dates (Phase 15): month-day + 生日/birthday/考試/exam/旅行/trip,
  no clock and no 聽日 → ``add_important_date`` (year omitted = yearly).
  Whole-message **重要日子** / **有咩生日** → ``list_important_dates``.
  Not a calendar create.
- Family aliases (Phase 9), canonical in ``ParseResult``:
  - Title: **游水** → 游泳; **MS Wong** / MS. Wong / MS Wong 堂 → Miss Wong 堂.
  - Participant: **梓梵** → Cedric. 梓梵 alone is not a create signal.
- Family titles (Phase 10), canonical in ``ParseResult``:
  - 公園 / playground → 公園 (公園 alone is not a create signal).
  - playgroup / 遊戲班 → playgroup.
  - 游水班 / swim class → 游泳.
  - 體能班 / gym / gymnastics → 體能班.
  - 手作 / workshop / 工作坊 → 手作.
  - 商場 / mall → 商場.
  - 生日會 / birthday party → 生日會.
  - 打針 / 打疫苗 / vaccine → 打針.
"""

from __future__ import annotations

import re
import time
import uuid
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from zoneinfo import ZoneInfo

from cec_vivisystem.logging import get_logger
from cec_vivisystem.models import Confidence, IntentType, ParseResult

logger = get_logger(__name__)

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
COMPONENT = "parser"
PREVIEW_LEN = 80

# English weekday name → Monday=0 .. Sunday=6
_WEEKDAY_EN: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

# Chinese day-of-week character → Monday=0 .. Sunday=6
_WEEKDAY_ZH: dict[str, int] = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
}

# Surface form → canonical participant (Phase 9: 梓梵 → Cedric)
_PARTICIPANT_ALIASES: tuple[tuple[str, str], ...] = (
    ("Cedric", "Cedric"),
    ("梓梵", "Cedric"),
    ("Elaine", "Elaine"),
    ("Carter", "Carter"),
)

# Activity / event keywords → title fragment (lowercase match keys)
_TITLE_KEYWORDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"游泳|游水|swim(?:ming)?", re.IGNORECASE), "游泳"),
    (re.compile(r"pediatrician", re.IGNORECASE), "pediatrician"),
    (re.compile(r"牙醫|dentist", re.IGNORECASE), "牙醫"),
    (re.compile(r"\bdinner\b", re.IGNORECASE), "family dinner"),
    (re.compile(r"學校\s*holiday|school\s*holiday|holiday", re.IGNORECASE), "學校 holiday"),
    # Phase 3 + Phase 9: lesson / class (Miss Wong / MS Wong 堂)
    (re.compile(r"Miss\s+Wong\s*堂", re.IGNORECASE), "Miss Wong 堂"),
    (re.compile(r"MS\.?\s+Wong\s*堂", re.IGNORECASE), "Miss Wong 堂"),
    (re.compile(r"Miss\s+Wong", re.IGNORECASE), "Miss Wong 堂"),
    (re.compile(r"MS\.?\s+Wong", re.IGNORECASE), "Miss Wong 堂"),
    # Phase 10 family titles
    (re.compile(r"playground|公園", re.IGNORECASE), "公園"),
    (re.compile(r"playgroup|遊戲班", re.IGNORECASE), "playgroup"),
    (re.compile(r"體能班|gymnastics|\bgym\b", re.IGNORECASE), "體能班"),
    (re.compile(r"手作|工作坊|workshop", re.IGNORECASE), "手作"),
    (re.compile(r"商場|\bmall\b", re.IGNORECASE), "商場"),
    (re.compile(r"生日會|birthday\s+party", re.IGNORECASE), "生日會"),
    (re.compile(r"打針|打疫苗|vaccine", re.IGNORECASE), "打針"),
]

_CREATE_SIGNAL = re.compile(
    r"book|add\b|schedule|帶|去|睇|游泳|游水|swim|pediatrician|牙醫|dentist|"
    r"dinner|holiday|學校|appointment|約|全日|明天|tomorrow|聽日|聽朝|"
    r"上晝|下晝|堂|銅鑼灣|MS\.?\s+Wong|"
    r"playgroup|遊戲班|playground|體能班|gymnastics|\bgym\b|"
    r"手作|workshop|工作坊|商場|\bmall\b|生日會|birthday\s+party|"
    r"打針|打疫苗|vaccine|"
    r"星期|禮拜|礼拜|週|周|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"\d{1,2}\s*([:：]\s*\d{2})?\s*(am|pm)|"
    r"\d{1,2}\s*點|下午|上午|晚上",
    re.IGNORECASE,
)

_WEATHER_OR_CHAT = re.compile(r"天氣|weather|點呀|點呀\s*$", re.IGNORECASE)

# Whole-message only — do not steal "holiday" or "help me book …".
_HELP_MESSAGE = re.compile(
    r"^(?:help|/help|usage|commands|how to use|"
    r"指令|點用|點樣用|有咩指令|有什麼指令|有什么指令)"
    r"\s*[?？!！。.]?\s*$",
    re.IGNORECASE,
)

# Family-facing; keep in sync with rules above. Shown in Slack on HELP.
ALLOWED_INPUTS_HELP = """常用指令（#family-plans）/ Common inputs
Type one of: help · 指令 · 點用

睇行程 / list a day or period（即時回覆，唔使 yes）
• 今日有乜
• 聽日有乜
• 聽日有乜嘢活動
• 聽日有什麼活動
• 今個星期有乜
• 今個月有乜
• 9月1日至9月7日有乜
• 2026年9月1日有乜
• tell me the events on 1 Sept 2026

加活動 / create（會出提案，thread 回 yes / 不要）
• 聽日9點，梓梵游水
• 聽朝11點帶梓梵去MS Wong 度上堂
• 聽日下午3點去公園
• 星期六下午3點帶 Cedric 去游泳
• Sunday 10am pediatrician for Cedric

日期時間 / when
• 聽日 / 聽朝 / 明天 / tomorrow
• 上晝 下晝 上午 下午
• 9點 · 11點 · 2:30pm · 全日

標題例子 / titles
游水、公園、playgroup、體能班、手作、商場、生日會、打針、Miss Wong 堂、牙醫

人 / who
Cedric / 梓梵、Elaine、Carter

重要日子 / important dates（即時記低，唔使 yes；唔寫入日曆）
• 4月12日 梓梵生日
• 10月22日 老婆生日
• 12月4日 Carter 生日
• 2026年9月15日 考試
• 重要日子
• 有咩生日

No calendar change was made."""

# Phase 7 + 12 + 14: list / summary queries (not create). 有乜嘢 before 有乜 is
# documentary; 有乜 still matches 有乜嘢活動.
_LIST_SIGNAL = re.compile(
    r"有乜嘢|有乜|有什麼|有什么|tell me the events|list events|what'?s on|行程",
    re.IGNORECASE,
)
_EN_DMY = re.compile(
    r"\b(\d{1,2})\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
    r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{4})\b",
    re.IGNORECASE,
)
_ZH_YMD = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
# 9月1日至9月7日 / 2026年9月1日至9月7日 / 2026年9月1日至2026年9月7日
_ZH_DATE_RANGE = re.compile(
    r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日?"
    r"\s*至\s*"
    r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日?"
)
_TODAY = re.compile(r"今日|今天|\btoday\b", re.IGNORECASE)
_THIS_WEEK = re.compile(r"今個(?:星期|禮拜|礼拜)")
_NEXT_WEEK = re.compile(r"下個(?:星期|禮拜|礼拜)")
_THIS_MONTH = re.compile(r"今個月")

_ALL_DAY = re.compile(r"全日|all[\s-]?day", re.IGNORECASE)

_LOCATION_HOME = re.compile(r"\bat\s+home\b|在家", re.IGNORECASE)
_LOCATION_CAUSEWAY = re.compile(r"銅鑼灣")

# 下星期三 / 下週三 / 下礼拜三
_ZH_NEXT_WEEKDAY = re.compile(
    r"下\s*(?:個)?\s*(?:星期|禮拜|礼拜|週|周)\s*([一二三四五六日天])"
)
# 星期六 / 星期三 (no 下)
_ZH_WEEKDAY = re.compile(r"(?:星期|禮拜|礼拜|週|周)\s*([一二三四五六日天])")

_EN_WEEKDAY = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)

_TOMORROW = re.compile(r"明天|tomorrow|聽日|聽朝", re.IGNORECASE)
_LIST_IMPORTANT_DATES = re.compile(
    r"^(?:重要日子|有咩生日|有什麼生日|有什么生日|"
    r"list important dates|important dates)"
    r"\s*[?？!！。.]?\s*$",
    re.IGNORECASE,
)
_IMPORTANT_DATE_KEYWORD = re.compile(
    r"生日|birthday|考試|考试|exam|旅行|trip",
    re.IGNORECASE,
)
# Optional year + month-day (4月12日 / 2026年9月15日).
_ZH_MD = re.compile(r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日")

# 下午3點 / 上晝11點 / 下晝2點 / 上午10點
_ZH_CLOCK = re.compile(r"(上午|下午|晚上|上晝|下晝)?\s*(\d{1,2})\s*[點点]")
# 2:30pm / 10am / 7pm / 14:30
_EN_CLOCK = re.compile(
    r"\b(\d{1,2})(?:\s*[:：]\s*(\d{2}))?\s*(am|pm)\b",
    re.IGNORECASE,
)
_EN_24H = re.compile(r"\b([01]?\d|2[0-3])\s*[:：]\s*([0-5]\d)\b")


def parse(
    message: str,
    *,
    now: datetime | None = None,
    correlation_id: str | None = None,
) -> ParseResult:
    """Parse a family message into a structured intent.

    Args:
        message: Raw user text (Cantonese and/or English).
        now: Reference instant for relative dates; defaults to current time in
            Asia/Hong_Kong. Must be timezone-aware when provided (naive values
            are assumed to be family-local).
        correlation_id: Optional flow id for multi-component tracing.

    Returns:
        ParseResult with intent_type create_event, list_events,
        add_important_date, list_important_dates, help, needs_clarification,
        or unknown.
        ``raw_text`` is the original ``message`` (not stripped).
    """
    started = time.perf_counter()
    corr = correlation_id or str(uuid.uuid4())
    preview = _preview(message)

    # structlog uses the first positional arg as `event` (event name / message).
    logger.info(
        "parse_started",
        component=COMPONENT,
        correlation_id=corr,
        message_length=len(message),
        message_preview=preview,
    )

    try:
        result = _parse_impl(message, now=now)
    except (ValueError, TypeError, OverflowError, OSError) as exc:
        # Controlled failure: never raise to caller for bad NL / bad clock data
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.error(
            "parse_completed",
            component=COMPONENT,
            correlation_id=corr,
            outcome="failure",
            intent_type=IntentType.UNKNOWN.value,
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
            error_message=str(exc),
            message_length=len(message),
            message_preview=preview,
        )
        return ParseResult(
            intent_type=IntentType.UNKNOWN,
            title=None,
            start=None,
            end=None,
            all_day=False,
            location=None,
            participants=[],
            raw_text=message,
            confidence=Confidence.LOW,
            missing_fields=[],
            notes=f"parser_error:{type(exc).__name__}",
        )

    duration_ms = int((time.perf_counter() - started) * 1000)
    outcome = _outcome_for(result.intent_type)
    logger.info(
        "parse_completed",
        component=COMPONENT,
        correlation_id=corr,
        outcome=outcome,
        intent_type=result.intent_type.value,
        confidence=result.confidence.value,
        duration_ms=duration_ms,
        message_length=len(message),
        message_preview=preview,
        missing_fields=result.missing_fields,
    )
    return result


def _parse_impl(message: str, *, now: datetime | None) -> ParseResult:
    raw = message
    if not message or not message.strip():
        return _unknown(raw, notes="empty_message")

    # Garbage: only punctuation / emoji / whitespace symbols
    if not re.search(r"[\w\u4e00-\u9fff]", message, re.UNICODE):
        return _unknown(raw, notes="non_linguistic")

    ref = _normalize_now(now)

    help_query = _try_help(raw)
    if help_query is not None:
        return help_query

    if _WEATHER_OR_CHAT.search(message) and not _CREATE_SIGNAL.search(message):
        return _unknown(raw, notes="not_create_event")

    listed_dates = _try_list_important_dates(raw)
    if listed_dates is not None:
        return listed_dates

    list_query = _try_list_events(message, ref)
    if list_query is not None:
        return list_query

    added_date = _try_add_important_date(raw, ref)
    if added_date is not None:
        return added_date

    # Pure chat without scheduling signals
    looks_like_create = bool(_CREATE_SIGNAL.search(message))
    event_date = _extract_date(message, ref)
    clock = _extract_time(message)
    all_day = bool(_ALL_DAY.search(message))
    participants = _extract_participants(message)
    location = _extract_location(message)
    title = _extract_title(message)

    has_when = event_date is not None or clock is not None or all_day
    # 明天 alone counts as a date
    if not has_when and _TOMORROW.search(message):
        event_date = (ref.date() + timedelta(days=1))
        has_when = True

    if not looks_like_create and not has_when:
        return _unknown(raw, notes="no_schedule_signal")

    # Build start datetime when possible
    start: datetime | None = None
    if all_day and event_date is not None:
        start = datetime.combine(event_date, dt_time(0, 0), tzinfo=FAMILY_TZ)
    elif event_date is not None and clock is not None:
        hour, minute = clock
        start = datetime(
            event_date.year,
            event_date.month,
            event_date.day,
            hour,
            minute,
            tzinfo=FAMILY_TZ,
        )
    elif event_date is not None and clock is None and not all_day:
        # Date without time — not enough for timed create
        start = None
    elif event_date is None and clock is not None:
        # Time without date — incomplete
        start = None

    missing: list[str] = []
    if title is None or not title.strip():
        missing.append("title")
    if start is None and not (all_day and event_date is not None):
        # need usable start: either timed start or all-day with date
        if event_date is None and clock is None and not all_day:
            missing.append("start")
        elif event_date is not None and clock is None and not all_day:
            missing.append("start")  # missing time
        elif clock is not None and event_date is None:
            missing.append("start")  # missing date
        else:
            missing.append("start")

    # Recompute start for all_day if we only had all_day + tomorrow already set
    if all_day and event_date is not None and start is None:
        start = datetime.combine(event_date, dt_time(0, 0), tzinfo=FAMILY_TZ)
        missing = [m for m in missing if m != "start"]

    if "title" in missing and "start" in missing and not looks_like_create:
        return _unknown(raw, notes="insufficient_signal")

    if missing:
        conf = Confidence.MEDIUM if looks_like_create or has_when else Confidence.LOW
        # Partial start OK to surface when only title missing
        partial_start = start
        if "start" in missing:
            partial_start = start  # may still be None
        # If we have date+time, don't leave start empty when only title missing
        if "title" in missing and "start" not in missing:
            pass
        return ParseResult(
            intent_type=IntentType.NEEDS_CLARIFICATION,
            title=title,
            start=partial_start if "start" not in missing else None,
            end=None,
            all_day=all_day,
            location=location,
            participants=participants,
            raw_text=raw,
            confidence=conf,
            missing_fields=missing,
            notes=None,
        )

    # Full create_event
    assert title is not None and start is not None
    conf = Confidence.HIGH if participants or location else Confidence.MEDIUM
    # Clear phrases with date+time or all-day → high
    if (clock is not None or all_day) and event_date is not None:
        conf = Confidence.HIGH

    return ParseResult(
        intent_type=IntentType.CREATE_EVENT,
        title=title,
        start=start,
        end=None,
        all_day=all_day,
        location=location,
        participants=participants,
        raw_text=raw,
        confidence=conf,
        missing_fields=[],
        notes=None,
    )


def _normalize_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(tz=FAMILY_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=FAMILY_TZ)
    return now.astimezone(FAMILY_TZ)


def _next_or_same_weekday(ref: datetime, weekday: int) -> date:
    """Return the date of this or the next ``weekday`` (Mon=0)."""
    d = ref.date()
    delta = (weekday - d.weekday()) % 7
    return d + timedelta(days=delta)


def _try_help(message: str) -> ParseResult | None:
    """Return a help result for a whole-message help/指令 line, else None."""
    if not _HELP_MESSAGE.match(message.strip()):
        return None
    return ParseResult(
        intent_type=IntentType.HELP,
        title=None,
        start=None,
        end=None,
        all_day=False,
        location=None,
        participants=[],
        raw_text=message,
        confidence=Confidence.HIGH,
        missing_fields=[],
        notes="help",
    )


def format_allowed_inputs() -> str:
    """Slack/CLI text listing common allowed inputs. Not a calendar write."""
    return ALLOWED_INPUTS_HELP


def _try_list_important_dates(message: str) -> ParseResult | None:
    """Whole-message view of stored important dates."""
    if not _LIST_IMPORTANT_DATES.match(message.strip()):
        return None
    return ParseResult(
        intent_type=IntentType.LIST_IMPORTANT_DATES,
        title=None,
        start=None,
        end=None,
        all_day=True,
        location=None,
        participants=[],
        raw_text=message,
        confidence=Confidence.HIGH,
        missing_fields=[],
        notes="list_important_dates",
    )


def _try_add_important_date(message: str, ref: datetime) -> ParseResult | None:
    """Month-day + keyword, no clock / 聽日 → add_important_date."""
    if _extract_time(message) is not None:
        return None
    if _TOMORROW.search(message):
        return None
    if not _IMPORTANT_DATE_KEYWORD.search(message):
        return None
    match = _ZH_MD.search(message)
    if match is None:
        return None
    year_raw, month_s, day_s = match.group(1), match.group(2), match.group(3)
    month = int(month_s)
    day = int(day_s)
    kind_year = int(year_raw) if year_raw else None
    year = kind_year if kind_year is not None else ref.year
    try:
        start = datetime(year, month, day, 0, 0, tzinfo=FAMILY_TZ)
    except ValueError:
        return None
    title = (message[: match.start()] + message[match.end() :]).strip(" \t,，。.?？!")
    title = re.sub(r"\s+", " ", title).strip()
    if not title:
        return ParseResult(
            intent_type=IntentType.NEEDS_CLARIFICATION,
            title=None,
            start=start,
            end=None,
            all_day=True,
            location=None,
            participants=_extract_participants(message),
            raw_text=message,
            confidence=Confidence.MEDIUM,
            missing_fields=["title"],
            notes="important_date_missing_title",
        )
    notes = "one_off" if kind_year is not None else "yearly"
    return ParseResult(
        intent_type=IntentType.ADD_IMPORTANT_DATE,
        title=title,
        start=start,
        end=None,
        all_day=True,
        location=None,
        participants=_extract_participants(message),
        raw_text=message,
        confidence=Confidence.HIGH,
        missing_fields=[],
        notes=notes,
    )


def _try_list_events(message: str, ref: datetime) -> ParseResult | None:
    """Return a list_events (or clarify) result, or None if this is not a list query."""
    if not _LIST_SIGNAL.search(message):
        return None
    window = _extract_list_window(message, ref)
    if window is None:
        return ParseResult(
            intent_type=IntentType.NEEDS_CLARIFICATION,
            title=None,
            start=None,
            end=None,
            all_day=True,
            location=None,
            participants=[],
            raw_text=message,
            confidence=Confidence.MEDIUM,
            missing_fields=["start"],
            notes="list_missing_date",
        )
    start, end = window
    return ParseResult(
        intent_type=IntentType.LIST_EVENTS,
        title=None,
        start=start,
        end=end,
        all_day=True,
        location=None,
        participants=[],
        raw_text=message,
        confidence=Confidence.HIGH,
        missing_fields=[],
        notes=None,
    )


def _extract_list_window(
    message: str, ref: datetime
) -> tuple[datetime, datetime] | None:
    """Inclusive calendar period as ``[start, end)`` HKT, or None."""
    if m := _ZH_DATE_RANGE.search(message):
        y1 = int(m.group(1)) if m.group(1) else ref.year
        y2 = int(m.group(4)) if m.group(4) else y1
        start_d = date(y1, int(m.group(2)), int(m.group(3)))
        end_d = date(y2, int(m.group(5)), int(m.group(6)))
        if end_d < start_d:
            end_d = date(end_d.year + 1, end_d.month, end_d.day)
        start = datetime.combine(start_d, dt_time(0, 0), tzinfo=FAMILY_TZ)
        end = datetime.combine(
            end_d + timedelta(days=1), dt_time(0, 0), tzinfo=FAMILY_TZ
        )
        return start, end

    if _THIS_WEEK.search(message):
        monday = _monday_of_week(ref.date())
        start = datetime.combine(monday, dt_time(0, 0), tzinfo=FAMILY_TZ)
        return start, start + timedelta(days=7)

    if _NEXT_WEEK.search(message):
        monday = _monday_of_week(ref.date()) + timedelta(days=7)
        start = datetime.combine(monday, dt_time(0, 0), tzinfo=FAMILY_TZ)
        return start, start + timedelta(days=7)

    if _THIS_MONTH.search(message):
        month_start = ref.date().replace(day=1)
        if month_start.month == 12:
            month_end = date(month_start.year + 1, 1, 1)
        else:
            month_end = date(month_start.year, month_start.month + 1, 1)
        start = datetime.combine(month_start, dt_time(0, 0), tzinfo=FAMILY_TZ)
        end = datetime.combine(month_end, dt_time(0, 0), tzinfo=FAMILY_TZ)
        return start, end

    if _TODAY.search(message):
        start = datetime.combine(ref.date(), dt_time(0, 0), tzinfo=FAMILY_TZ)
        return start, start + timedelta(days=1)

    event_date = _extract_date(message, ref)
    if event_date is None:
        return None
    start = datetime.combine(event_date, dt_time(0, 0), tzinfo=FAMILY_TZ)
    return start, start + timedelta(days=1)


def _monday_of_week(day: date) -> date:
    """Monday-start week containing ``day``."""
    return day - timedelta(days=day.weekday())


_MONTH_NUM = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _extract_date(message: str, ref: datetime) -> date | None:
    if m := _ZH_YMD.search(message):
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    if m := _EN_DMY.search(message):
        month = _MONTH_NUM.get(m.group(2).lower().rstrip("."))
        if month is not None:
            return date(int(m.group(3)), month, int(m.group(1)))

    if _TOMORROW.search(message):
        return ref.date() + timedelta(days=1)

    if m := _ZH_NEXT_WEEKDAY.search(message):
        wd = _WEEKDAY_ZH.get(m.group(1))
        if wd is not None:
            return _next_or_same_weekday(ref, wd)

    if m := _ZH_WEEKDAY.search(message):
        wd = _WEEKDAY_ZH.get(m.group(1))
        if wd is not None:
            return _next_or_same_weekday(ref, wd)

    if m := _EN_WEEKDAY.search(message):
        wd = _WEEKDAY_EN[m.group(1).lower()]
        return _next_or_same_weekday(ref, wd)

    return None


def _extract_time(message: str) -> tuple[int, int] | None:
    """Return (hour, minute) in 24h local, or None."""
    if m := _EN_CLOCK.search(message):
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = m.group(3).lower()
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        return hour, minute

    if m := _ZH_CLOCK.search(message):
        period = m.group(1) or ""
        hour = int(m.group(2))
        minute = 0
        if period in ("下午", "晚上", "下晝") and hour < 12:
            hour += 12
        elif period in ("上午", "上晝") and hour == 12:
            hour = 0
        return hour, minute

    # Bare 24h only if not already consumed — avoid double match with am/pm
    if m := _EN_24H.search(message):
        # Skip if this looks like part of am/pm already handled
        return int(m.group(1)), int(m.group(2))

    return None


def _extract_participants(message: str) -> list[str]:
    """Match known names/aliases; allow adjacency to CJK (e.g. 帶Cedric去)."""
    found: list[str] = []
    seen: set[str] = set()
    for form, canonical in _PARTICIPANT_ALIASES:
        # Do not use \\b: CJK is \\w in Python, so 帶Cedric has no ASCII word edge.
        if canonical not in seen and re.search(
            rf"(?<![A-Za-z]){re.escape(form)}(?![A-Za-z])",
            message,
            re.IGNORECASE,
        ):
            found.append(canonical)
            seen.add(canonical)
    return found


def _extract_location(message: str) -> str | None:
    if _LOCATION_HOME.search(message):
        return "home"
    if _LOCATION_CAUSEWAY.search(message):
        return "銅鑼灣"
    return None


def _extract_title(message: str) -> str | None:
    for pattern, title in _TITLE_KEYWORDS:
        if pattern.search(message):
            return title
    return None


def _unknown(raw: str, *, notes: str | None = None) -> ParseResult:
    return ParseResult(
        intent_type=IntentType.UNKNOWN,
        title=None,
        start=None,
        end=None,
        all_day=False,
        location=None,
        participants=[],
        raw_text=raw,
        confidence=Confidence.LOW,
        missing_fields=[],
        notes=notes,
    )


def _outcome_for(intent: IntentType) -> str:
    if intent in (
        IntentType.CREATE_EVENT,
        IntentType.LIST_EVENTS,
        IntentType.ADD_IMPORTANT_DATE,
        IntentType.LIST_IMPORTANT_DATES,
        IntentType.HELP,
    ):
        return "success"
    if intent == IntentType.NEEDS_CLARIFICATION:
        return "partial"
    return "skipped"


def _preview(message: str) -> str:
    text = message.replace("\n", " ")
    if len(text) <= PREVIEW_LEN:
        return text
    return text[: PREVIEW_LEN - 1] + "…"


def main() -> None:
    """CLI smoke: parse a few fixture phrases with fixed reference time."""
    from cec_vivisystem.logging import setup_logging

    setup_logging()
    fixed = datetime(2026, 8, 8, 12, 0, tzinfo=FAMILY_TZ)
    samples = [
        "星期六下午3點帶 Cedric 去游泳",
        "Sunday 10am pediatrician for Cedric",
        "聽日上晝11點，帶Cedric去銅鑼灣上Miss Wong 堂",
        "聽日9點，梓梵游水",
        "聽朝11點帶梓梵去MS Wong 度上堂",
        "聽日下午3點去公園",
        "Sunday 10am playgroup",
        "幫我 book 游泳",
        "今日有乜？",
        "今個星期有乜",
        "4月12日 梓梵生日",
        "重要日子",
        "今日天氣點呀",
        "help",
        "指令",
    ]
    for text in samples:
        result = parse(text, now=fixed)
        print(
            f"{text!r} -> {result.intent_type.value} "
            f"title={result.title!r} start={result.start} "
            f"missing={result.missing_fields}"
        )


if __name__ == "__main__":
    main()
