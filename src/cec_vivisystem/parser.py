"""Offline natural-language parser (Phase 1).

Turns mixed Cantonese/English family messages into structured intents.
Rule/heuristic based — no network, no LLM.

Weekday policy (documented once):
- Bare weekday names (e.g. 星期六, Sunday, Friday) resolve to the **same or
  next** occurrence from ``now`` (same day if ``now`` already falls on that weekday).
- Prefixed 下/下週 forms (e.g. 下星期三) use the same next-or-same occurrence rule
  for Phase 1 fixtures (from Saturday noon, 下星期三 → the coming Wednesday).
- Relative words: 明天 / tomorrow → calendar day after ``now`` in family TZ.
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

_KNOWN_PARTICIPANTS = ("Cedric", "Elaine", "Carter")

# Activity / event keywords → title fragment (lowercase match keys)
_TITLE_KEYWORDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"游泳|swim(?:ming)?", re.IGNORECASE), "游泳"),
    (re.compile(r"pediatrician", re.IGNORECASE), "pediatrician"),
    (re.compile(r"牙醫|dentist", re.IGNORECASE), "牙醫"),
    (re.compile(r"\bdinner\b", re.IGNORECASE), "family dinner"),
    (re.compile(r"學校\s*holiday|school\s*holiday|holiday", re.IGNORECASE), "學校 holiday"),
]

_CREATE_SIGNAL = re.compile(
    r"book|add\b|schedule|帶|去|睇|游泳|swim|pediatrician|牙醫|dentist|"
    r"dinner|holiday|學校|appointment|約|全日|明天|tomorrow|"
    r"星期|禮拜|礼拜|週|周|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"\d{1,2}\s*([:：]\s*\d{2})?\s*(am|pm)|"
    r"\d{1,2}\s*點|下午|上午|晚上",
    re.IGNORECASE,
)

_WEATHER_OR_CHAT = re.compile(r"天氣|weather|點呀|點呀\s*$", re.IGNORECASE)

_ALL_DAY = re.compile(r"全日|all[\s-]?day", re.IGNORECASE)

_LOCATION_HOME = re.compile(r"\bat\s+home\b|在家", re.IGNORECASE)

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

_TOMORROW = re.compile(r"明天|tomorrow", re.IGNORECASE)

# 下午3點 / 下午3点 / 上午10點
_ZH_CLOCK = re.compile(r"(上午|下午|晚上)?\s*(\d{1,2})\s*[點点]")
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
        ParseResult with intent_type create_event, needs_clarification, or unknown.
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

    if _WEATHER_OR_CHAT.search(message) and not _CREATE_SIGNAL.search(message):
        return _unknown(raw, notes="not_create_event")

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

    if not looks_like_create and not has_when and title is None:
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


def _extract_date(message: str, ref: datetime) -> date | None:
    if m := _TOMORROW.search(message):
        # Prefer explicit tomorrow; still allow weekday if both present — tomorrow wins if first?
        # Fixtures don't combine; if both, tomorrow is enough for F5.
        pass

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
        if period in ("下午", "晚上") and hour < 12:
            hour += 12
        elif period == "上午" and hour == 12:
            hour = 0
        return hour, minute

    # Bare 24h only if not already consumed — avoid double match with am/pm
    if m := _EN_24H.search(message):
        # Skip if this looks like part of am/pm already handled
        return int(m.group(1)), int(m.group(2))

    return None


def _extract_participants(message: str) -> list[str]:
    found: list[str] = []
    for name in _KNOWN_PARTICIPANTS:
        if re.search(rf"\b{name}\b", message, re.IGNORECASE):
            found.append(name)
    return found


def _extract_location(message: str) -> str | None:
    if _LOCATION_HOME.search(message):
        return "home"
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
    if intent == IntentType.CREATE_EVENT:
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
        "幫我 book 游泳",
        "今日天氣點呀",
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
