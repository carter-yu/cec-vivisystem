"""Weekday-series detect, span guard and discrete expand (Phase 27, ADR 0011).

Why: the October 2026 incident collapsed 「逢星期一至五 8:30–12:00，10月2日至
10月30日」 into one timed event spanning weeks; overlap then flagged every
afternoon. A bounded weekday series is now N discrete HKT occurrences, and a
single timed create that crosses calendar days never becomes a proposal.

Contract:
- ``expand_weekday_series`` is pure (no I/O, no clock): weekday set ×
  inclusive date range × daily window → occurrences in Asia/Hong_Kong.
  Over ``MAX_OCCURRENCES`` raises; it never silently truncates.
- ``apply_series_policy`` runs after any parse (model or offline rules) and
  returns either an unchanged single create, a create carrying a validated
  ``SeriesSpec`` (``start``/``end`` = first occurrence), or a clarification.
- Text cue helpers are deterministic guards, not a create vocabulary: they
  only decide when a single-event reading is unsafe and recover the weekday
  set / range / window when the family wrote them explicitly.

Do not: emit Google RRULE here, invent an end date for open-ended phrasing,
or turn an all-day multi-day span into a series.
"""

from __future__ import annotations

import re
import time as perf_time
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from cec_vivisystem.logging import get_logger
from cec_vivisystem.models import (
    IntentType,
    ParseResult,
    SeriesOccurrence,
    SeriesSpec,
)

logger = get_logger(__name__)

COMPONENT = "series"
FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
# ≈ 8 weeks of Mon–Fri. Tunable; recorded in ADR 0011.
MAX_OCCURRENCES = 40
DEFAULT_DURATION = timedelta(hours=1)
# Beyond this many days we refuse without counting (defensive bound only).
_MAX_RANGE_DAYS = 3700

WEEKDAY_ZH_LABELS = ("一", "二", "三", "四", "五", "六", "日")
WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")

# Clarify notes consumed by ``format_series_clarification``.
NOTE_SPAN_GUARD = "span_guard"
NOTE_ALL_DAY_SPAN = "all_day_span"
NOTE_SERIES_PREFIX = "series_"


class SeriesExpansionError(ValueError):
    """Controlled expand refusal; ``reason`` is a short machine token."""

    def __init__(self, reason: str, *, count: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.count = count


def expand_weekday_series(
    spec: SeriesSpec,
    *,
    cap: int = MAX_OCCURRENCES,
) -> list[SeriesOccurrence]:
    """Expand a bounded weekday series into discrete HKT occurrences.

    Raises ``SeriesExpansionError`` for an empty weekday set, a reversed
    range, a daily window that does not end after it starts (same day),
    zero matching days, or more than ``cap`` occurrences.
    """
    weekdays = set(spec.weekdays)
    if not weekdays or any(not 0 <= d <= 6 for d in weekdays):
        raise SeriesExpansionError("empty_weekdays")
    if spec.range_end < spec.range_start:
        raise SeriesExpansionError("invalid_range")
    if spec.end_time is not None and spec.end_time <= spec.start_time:
        # A daily window crossing midnight is a multi-day span per occurrence.
        raise SeriesExpansionError("invalid_window")
    if (spec.range_end - spec.range_start).days > _MAX_RANGE_DAYS:
        raise SeriesExpansionError("over_cap")

    occurrences: list[SeriesOccurrence] = []
    count = 0
    day = spec.range_start
    while day <= spec.range_end:
        if day.weekday() in weekdays:
            count += 1
            if count <= cap:
                start = datetime.combine(day, spec.start_time, tzinfo=FAMILY_TZ)
                end = (
                    datetime.combine(day, spec.end_time, tzinfo=FAMILY_TZ)
                    if spec.end_time is not None
                    else start + DEFAULT_DURATION
                )
                occurrences.append(SeriesOccurrence(local_date=day, start=start, end=end))
        day += timedelta(days=1)
    if count == 0:
        raise SeriesExpansionError("no_occurrences", count=0)
    if count > cap:
        raise SeriesExpansionError("over_cap", count=count)
    return occurrences


# --- deterministic text cues -------------------------------------------------

_ZH_DAY = "一二三四五六日天"
_ZH_DAY_INDEX = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_ZH_PREFIX = r"(?:星期|禮拜|週)"
_RANGE_SEP = r"(?:至|到|-|–|—|~|～)"
# 星期一至五 / 禮拜一到禮拜五. (?!本) keeps 「星期六到日本」 a trip, not Sat–Sun.
_ZH_WEEKDAY_RANGE = re.compile(
    rf"{_ZH_PREFIX}\s*([{_ZH_DAY}])\s*{_RANGE_SEP}\s*{_ZH_PREFIX}?\s*([{_ZH_DAY}])(?!本)"
)
# 星期一、三、五 / 星期一，星期三 / 星期一三五 (天 excluded from compact: 星期三天氣).
# 同/和/及 need a repeated prefix (「星期六同一班朋友」 is not Sat+Mon); a trailing
# clock marker means 「星期六，三點」 is Saturday at three, not Sat+Wed.
_ZH_WEEKDAY_LIST = re.compile(
    rf"{_ZH_PREFIX}\s*([{_ZH_DAY}](?:\s*[、,，/]\s*{_ZH_PREFIX}?\s*[{_ZH_DAY}]"
    rf"|\s*[及和同]\s*{_ZH_PREFIX}\s*[{_ZH_DAY}])+)(?![點:：\d時])"
)
_ZH_WEEKDAY_COMPACT = re.compile(rf"{_ZH_PREFIX}([一二三四五六日]{{2,}})(?![點:：\d時本])")
_ZH_EVERY = re.compile(rf"逢\s*{_ZH_PREFIX}\s*([{_ZH_DAY}])")
_ZH_WORKDAYS = re.compile(r"平日|工作日|返工日")
_ZH_DAILY = re.compile(r"每日|每天")

_EN_DAY = (
    r"(mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|"
    r"fri(?:day)?|sat(?:urday)?|sun(?:day)?)"
)
_EN_DAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_EN_WEEKDAY_RANGE = re.compile(
    rf"\b{_EN_DAY}\.?\s*(?:-|–|—|to|through|thru)\s*{_EN_DAY}\b", re.IGNORECASE
)
_EN_WEEKDAY_LIST = re.compile(
    rf"\b{_EN_DAY}\b(?:\s*[/,]\s*\b{_EN_DAY}\b)+", re.IGNORECASE
)
_EN_EVERY = re.compile(rf"\bevery\s+{_EN_DAY}\b", re.IGNORECASE)
_EN_WORKDAYS = re.compile(r"\b(?:every\s+)?weekdays?\b", re.IGNORECASE)
_EN_DAILY = re.compile(r"\bevery\s*day\b|\bdaily\b", re.IGNORECASE)

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_EN_MON = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
_ORD = r"(?:st|nd|rd|th)?"
_ZH_DATE_RANGE = re.compile(
    r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日號]?"
    rf"\s*{_RANGE_SEP}\s*"
    r"(?:(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*)?(\d{1,2})\s*[日號]?(?![點:：\d])"
)
# Oct 2–30 / Oct 2 to Nov 6 [2026]
_EN_DATE_RANGE_MD = re.compile(
    rf"\b{_EN_MON}\s+(\d{{1,2}}){_ORD}\s*(?:-|–|—|to|through|until|till)\s*"
    rf"(?:{_EN_MON}\s+)?(\d{{1,2}}){_ORD}(?:,?\s+(\d{{4}}))?\b",
    re.IGNORECASE,
)
# 2–30 Oct / 2 Oct to 6 Nov [2026]
_EN_DATE_RANGE_DM = re.compile(
    rf"(?<![\d:：])\b(\d{{1,2}}){_ORD}\s*(?:{_EN_MON}\s*)?(?:-|–|—|to|through|until|till)\s*"
    rf"(\d{{1,2}}){_ORD}\s+{_EN_MON}(?:,?\s+(\d{{4}}))?\b",
    re.IGNORECASE,
)
_CLOCK = (
    r"(上午|下午|上晝|下晝|晚上|夜晚)?\s*(\d{1,2})"
    r"(?:\s*[:：]\s*(\d{2})|\s*點\s*(半)?)?\s*(am|pm)?"
)
_TIME_WINDOW = re.compile(
    rf"(?<![\d/月:：]){_CLOCK}\s*(?:-|–|—|~|～|至|到|to)\s*{_CLOCK}(?![\d月日號])",
    re.IGNORECASE,
)


def _range_days(first: int, last: int) -> tuple[int, ...]:
    if first <= last:
        return tuple(range(first, last + 1))
    return tuple(list(range(first, 7)) + list(range(last + 1)))


def extract_weekdays(text: str) -> tuple[int, ...] | None:
    """Explicit weekday set in ``text`` (Monday=0), or None if none stated."""
    if m := _ZH_WEEKDAY_RANGE.search(text):
        return _range_days(_ZH_DAY_INDEX[m.group(1)], _ZH_DAY_INDEX[m.group(2)])
    if m := _ZH_WEEKDAY_LIST.search(text):
        days = [_ZH_DAY_INDEX[c] for c in m.group(1) if c in _ZH_DAY_INDEX]
        return tuple(sorted(set(days)))
    if m := _ZH_WEEKDAY_COMPACT.search(text):
        return tuple(sorted({_ZH_DAY_INDEX[c] for c in m.group(1)}))
    if m := _EN_WEEKDAY_RANGE.search(text):
        return _range_days(
            _EN_DAY_INDEX[m.group(1)[:3].lower()], _EN_DAY_INDEX[m.group(2)[:3].lower()]
        )
    if m := _EN_WEEKDAY_LIST.search(text):
        found = re.findall(_EN_DAY, m.group(0), re.IGNORECASE)
        return tuple(sorted({_EN_DAY_INDEX[d[:3].lower()] for d in found}))
    if _ZH_WORKDAYS.search(text) or _EN_WORKDAYS.search(text):
        return (0, 1, 2, 3, 4)
    if _ZH_DAILY.search(text) or _EN_DAILY.search(text):
        return (0, 1, 2, 3, 4, 5, 6)
    if m := _ZH_EVERY.search(text):
        return (_ZH_DAY_INDEX[m.group(1)],)
    if m := _EN_EVERY.search(text):
        return (_EN_DAY_INDEX[m.group(1)[:3].lower()],)
    return None


def has_series_cue(text: str) -> bool:
    """True when the wording names a repeating weekday set (逢 / Mon–Fri / …)."""
    if not text:
        return False
    return extract_weekdays(text) is not None


def extract_date_range(text: str, *, now: datetime) -> tuple[date, date] | None:
    """Inclusive explicit date range; yearless ranges use the next occurrence."""
    ref = _normalize(now).date()
    try:
        if m := _ZH_DATE_RANGE.search(text):
            y1, m1, d1, y2, m2, d2 = m.groups()
            return _build_range(ref, y1, int(m1), int(d1), y2, int(m2) if m2 else None, int(d2))
        if m := _EN_DATE_RANGE_MD.search(text):
            mon1, d1, mon2, d2, year = m.groups()
            month1 = _MONTHS[mon1[:3].lower()]
            month2 = _MONTHS[mon2[:3].lower()] if mon2 else None
            return _build_range(ref, year, month1, int(d1), year, month2, int(d2))
        if m := _EN_DATE_RANGE_DM.search(text):
            d1, mon1, d2, mon2, year = m.groups()
            month2 = _MONTHS[mon2[:3].lower()]
            month1 = _MONTHS[mon1[:3].lower()] if mon1 else month2
            return _build_range(ref, year, month1, int(d1), year, month2, int(d2))
    except ValueError:
        return None
    return None


def _build_range(
    ref: date,
    y1: str | None,
    m1: int,
    d1: int,
    y2: str | None,
    m2: int | None,
    d2: int,
) -> tuple[date, date]:
    month2 = m2 if m2 is not None else m1
    year1 = int(y1) if y1 else ref.year
    start = date(year1, m1, d1)
    year2 = int(y2) if y2 else start.year
    end = date(year2, month2, d2)
    if end < start and not y2:
        end = date(year2 + 1, month2, d2)
    if not y1 and end < ref:
        # Yearless range already over this year → the next occurrence.
        start = date(start.year + 1, start.month, start.day)
        end = date(end.year + 1, end.month, end.day)
    return start, end


def extract_time_window(text: str) -> tuple[time, time] | None:
    """Daily ``start–end`` clock window (8:30–12:00 / 3pm-5pm / 下晝2點至4點)."""
    for m in _TIME_WINDOW.finditer(text):
        p1, h1, mm1, half1, ap1, p2, h2, mm2, half2, ap2 = m.groups()
        # Need a real clock marker so "Oct 2–12" is never a time window.
        if not any([mm1, mm2, ap1, ap2, half1, half2, "點" in m.group(0)]):
            continue
        try:
            start = _clock(p1, h1, mm1, half1, ap1 or ap2, None)
            end = _clock(p2 or p1, h2, mm2, half2, ap2, start)
        except ValueError:
            continue
        if end <= start:
            continue
        return start, end
    return None


def _clock(
    period: str | None,
    hour_s: str,
    minute_s: str | None,
    half: str | None,
    ampm: str | None,
    after: time | None,
) -> time:
    hour = int(hour_s)
    minute = 30 if half else int(minute_s or 0)
    if ampm:
        if not 1 <= hour <= 12:
            raise ValueError("bad am/pm hour")
        ampm = ampm.lower()
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
    elif period in ("下午", "下晝", "晚上", "夜晚") and hour < 12:
        hour += 12
    elif after is not None and hour < 12 and time(hour, minute) <= after:
        # 下晝2點至4點 / 1:30–3:00 after a PM start: keep the window same-day.
        if time(hour + 12, minute) > after:
            hour += 12
    return time(hour, minute)


def extract_series_from_text(text: str, *, now: datetime) -> SeriesSpec | None:
    """Full explicit series (weekdays + date range + daily window), or None."""
    weekdays = extract_weekdays(text)
    window = extract_time_window(text)
    date_range = extract_date_range(text, now=now)
    if weekdays is None or window is None or date_range is None:
        return None
    return SeriesSpec(
        weekdays=weekdays,
        range_start=date_range[0],
        range_end=date_range[1],
        start_time=window[0],
        end_time=window[1],
    )


# --- policy -------------------------------------------------------------------


def apply_series_policy(
    result: ParseResult,
    *,
    now: datetime | None = None,
    correlation_id: str | None = None,
) -> ParseResult:
    """Span guard + series detect/expand after any parse. Never raises."""
    started = perf_time.perf_counter()
    try:
        return _apply(result, now=_normalize(now or datetime.now(tz=FAMILY_TZ)),
                      correlation_id=correlation_id, started=started)
    except Exception as exc:  # noqa: BLE001 — guard must fail closed, not crash intake
        logger.error(
            "series_expand_failed",
            component=COMPONENT,
            outcome="failure",
            correlation_id=correlation_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        if result.intent_type == IntentType.CREATE_EVENT:
            return _clarify(result, "series_invalid", correlation_id=correlation_id,
                            started=started)
        return result


def _apply(
    result: ParseResult,
    *,
    now: datetime,
    correlation_id: str | None,
    started: float,
) -> ParseResult:
    text = result.raw_text or ""
    notes = result.notes or ""
    if result.intent_type == IntentType.NEEDS_CLARIFICATION:
        # Model clarifications are authoritative (ADR 0008). Only an offline
        # rules partial with an explicit full series may become a series create.
        if "llm" in notes or result.series is not None or not result.title:
            return result
        if not has_series_cue(text):
            return result
        spec = extract_series_from_text(text, now=now)
        if spec is None:
            return _clarify(result, _incomplete_note(text, now), correlation_id=correlation_id,
                            started=started)
        return _expand_into(replace(result, intent_type=IntentType.CREATE_EVENT,
                                    missing_fields=[]),
                            spec, source="text", correlation_id=correlation_id,
                            started=started)
    if result.intent_type != IntentType.CREATE_EVENT:
        return result

    spec = result.series
    source = "model"
    if spec is None and has_series_cue(text):
        spec = extract_series_from_text(text, now=now)
        source = "text"
        if spec is None:
            spec = _salvage_from_span(result, text)
            source = "span"
        if spec is None:
            return _clarify(result, _incomplete_note(text, now), correlation_id=correlation_id,
                            started=started)

    if spec is None:
        if _is_timed_multi_day(result):
            return _clarify(result, NOTE_SPAN_GUARD, correlation_id=correlation_id,
                            started=started)
        if _is_all_day_multi_day(result):
            return _clarify(result, NOTE_ALL_DAY_SPAN, correlation_id=correlation_id,
                            started=started)
        return result

    if result.all_day:
        return _clarify(result, "series_all_day", correlation_id=correlation_id,
                        started=started)
    return _expand_into(result, spec, source=source, correlation_id=correlation_id,
                        started=started)


def _expand_into(
    result: ParseResult,
    spec: SeriesSpec,
    *,
    source: str,
    correlation_id: str | None,
    started: float,
) -> ParseResult:
    try:
        occurrences = expand_weekday_series(spec)
    except SeriesExpansionError as exc:
        note = f"{NOTE_SERIES_PREFIX}{exc.reason}"
        if exc.count is not None:
            note = f"{note}:{exc.count}"
        return _clarify(result, note, correlation_id=correlation_id, started=started)
    first = occurrences[0]
    logger.info(
        "series_expand_completed",
        component=COMPONENT,
        outcome="success",
        correlation_id=correlation_id,
        source=source,
        occurrence_count=len(occurrences),
        weekdays=",".join(WEEKDAY_CODES[d] for d in spec.weekdays),
        range_start=spec.range_start.isoformat(),
        range_end=spec.range_end.isoformat(),
        duration_ms=int((perf_time.perf_counter() - started) * 1000),
    )
    return replace(
        result,
        start=first.start,
        end=first.end,
        all_day=False,
        series=spec,
        missing_fields=[],
    )


def _salvage_from_span(result: ParseResult, text: str) -> SeriesSpec | None:
    """Model collapsed an explicit weekday series into one long timed span.

    The weekday set must come from the text; the span's dates and clocks then
    give range and daily window. The family still reviews the full list.
    """
    if not _is_timed_multi_day(result):
        return None
    weekdays = extract_weekdays(text)
    if weekdays is None or result.start is None or result.end is None:
        return None
    start = _normalize(result.start)
    end = _normalize(result.end)
    if end.time() <= start.time():
        return None
    return SeriesSpec(
        weekdays=weekdays,
        range_start=start.date(),
        range_end=end.date(),
        start_time=start.time().replace(tzinfo=None),
        end_time=end.time().replace(tzinfo=None),
    )


def _incomplete_note(text: str, now: datetime) -> str:
    if extract_date_range(text, now=now) is None:
        return "series_open_ended"
    return "series_missing_time"


def _is_timed_multi_day(result: ParseResult) -> bool:
    if result.all_day or result.start is None or result.end is None:
        return False
    return _normalize(result.end).date() > _normalize(result.start).date()


def _is_all_day_multi_day(result: ParseResult) -> bool:
    if not result.all_day or result.start is None or result.end is None:
        return False
    return (_normalize(result.end).date() - _normalize(result.start).date()).days > 1


def _clarify(
    result: ParseResult,
    note: str,
    *,
    correlation_id: str | None,
    started: float,
) -> ParseResult:
    logger.info(
        "span_guard_clarify" if note in (NOTE_SPAN_GUARD, NOTE_ALL_DAY_SPAN)
        else "series_expand_refused",
        component=COMPONENT,
        outcome="partial",
        correlation_id=correlation_id,
        reason=note,
        duration_ms=int((perf_time.perf_counter() - started) * 1000),
    )
    return replace(
        result,
        intent_type=IntentType.NEEDS_CLARIFICATION,
        series=None,
        missing_fields=list(dict.fromkeys([*result.missing_fields, "series_or_span"])),
        notes=note,
    )


# --- copy (Traditional Chinese + English) -----------------------------------

_EXAMPLE = "例如「逢星期一至五 8:30-12:00，10月5日至10月30日 暑期班」"


def format_series_clarification(result: ParseResult) -> str | None:
    """Clarify copy for span-guard / series refusals, or None if not ours."""
    note = next(
        (
            token for token in (result.notes or "").split(";")
            if token in (NOTE_SPAN_GUARD, NOTE_ALL_DAY_SPAN)
            or token.startswith(NOTE_SERIES_PREFIX)
        ),
        "",
    )
    disclaimer = "未有改動日曆。/ No calendar change was made."
    if note == NOTE_SPAN_GUARD:
        return (
            "呢個時間跨咗幾日，我唔會開一個連續幾日嘅活動。"
            f"如果係每個星期幾都有，請講明星期幾、日期範圍同每日時間，{_EXAMPLE}。\n"
            "This time spans several days, so it was not proposed as one event. "
            "For a repeating weekday activity, give the weekdays, date range and daily time.\n"
            + disclaimer
        )
    if note == NOTE_ALL_DAY_SPAN:
        return (
            "多日全日活動暫時唔會自動建立。請逐日講，或者講明係邊幾日。\n"
            "Multi-day all-day events are not created automatically yet. "
            "Please send each day separately.\n" + disclaimer
        )
    if not note.startswith(NOTE_SERIES_PREFIX):
        return None
    reason, _, count = note[len(NOTE_SERIES_PREFIX):].partition(":")
    if reason == "over_cap":
        size = f"有 {count} 次，" if count else ""
        return (
            f"呢個重複活動{size}超過每次最多 {MAX_OCCURRENCES} 次嘅上限。"
            "請縮短日期範圍，或者分開幾次講。\n"
            f"This series exceeds the {MAX_OCCURRENCES}-occurrence limit. "
            "Please shorten the date range or split it.\n" + disclaimer
        )
    if reason == "open_ended":
        return (
            f"重複活動需要開始同結束日期，我唔會無限期咁加。請講埋日期範圍，{_EXAMPLE}。\n"
            "A repeating activity needs a start and end date. Please add the date range.\n"
            + disclaimer
        )
    if reason == "missing_time":
        return (
            f"請講埋每日嘅開始同結束時間，{_EXAMPLE}。\n"
            "Please add the daily start and end time.\n" + disclaimer
        )
    if reason == "no_occurrences":
        return (
            "日期範圍入面冇符合嘅星期幾，請再檢查日期。\n"
            "No matching weekdays fall inside that date range.\n" + disclaimer
        )
    if reason == "all_day":
        return (
            "全日嘅重複活動暫時唔支援，請講明每日時間。\n"
            "All-day repeating activities are not supported yet; please give a daily time.\n"
            + disclaimer
        )
    return (
        f"未能展開呢個重複活動，請講清楚星期幾、日期範圍同每日時間，{_EXAMPLE}。\n"
        "Could not expand this series; please restate weekdays, date range and daily time.\n"
        + disclaimer
    )


def weekday_summary(weekdays: tuple[int, ...]) -> str:
    """逢星期一至五 / 逢星期一、三、五 (Traditional Chinese)."""
    days = sorted(set(weekdays))
    if len(days) == 7:
        return "每日"
    if len(days) >= 3 and days == list(range(days[0], days[-1] + 1)):
        return f"逢星期{WEEKDAY_ZH_LABELS[days[0]]}至{WEEKDAY_ZH_LABELS[days[-1]]}"
    return "逢星期" + "、".join(WEEKDAY_ZH_LABELS[d] for d in days)


def format_occurrence(occurrence: SeriesOccurrence) -> str:
    """``2026-10-05（一）08:30–12:00``."""
    label = WEEKDAY_ZH_LABELS[occurrence.local_date.weekday()]
    start = _normalize(occurrence.start).strftime("%H:%M")
    end = _normalize(occurrence.end).strftime("%H:%M")
    return f"{occurrence.local_date.isoformat()}（{label}）{start}–{end}"


def _normalize(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=FAMILY_TZ)
    return value.astimezone(FAMILY_TZ)
