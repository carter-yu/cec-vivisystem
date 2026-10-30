"""Multi-event list create: per-item checks, copy and narrowing (Phase 33a, ADR 0018).

Why: a pasted list of dated lines used to become one proposal while the other
lines vanished without notice. A list is now one confirmation holding N
independent ``EventItem`` values; yes creates each as its own Google event.

Contract:
- Pure: no I/O, no clock (``now`` is injected), no model. The model only
  extracts ``events[]``; this module decides, per item, what is included and
  why a line is not.
- ``looks_like_dated_list`` / ``count_dated_lines`` decide routing only (skip
  the single-query list / important-date routes, cap check). They are not a
  create vocabulary; do not grow them to recognise new event wording.
- A line with a date but no clock never becomes an all-day event and is never
  skipped silently: the whole message gets a time question and no proposal
  (Carter, 2026-10-29). Explicit 全日 / all day stays an all-day item.
- Item numbers are the original 1-based positions. Narrowing (``唔要 3`` /
  ``keep 1,4``) removes numbers but never renumbers, so child keys stay stable.

Phase 33c (ADR 0018 amendment, kill switch ``CEC_BATCH_SERIES``): with
``allow_series`` an item may carry its own bounded weekday series and write
``occurrence_count`` Google events. One number is still one item: deselect
removes the whole item with its occurrences. With the switch off the item
stays ``REASON_REPEATING``, which is Phase 33a byte for byte.

Do not: renumber items, truncate an over-cap list, move a past date to next
year, expand a series item into separate numbered items, or claim a calendar
write in copy.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from cec_vivisystem.logging import get_logger
from cec_vivisystem.models import (
    Confidence,
    EventItem,
    IntentType,
    NotIncludedItem,
    ParseResult,
    SeriesOccurrence,
    SeriesSpec,
)
from cec_vivisystem.parser import contains_simplified_markers

logger = get_logger(__name__)

COMPONENT = "event_batch"
FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
MAX_BATCH_ITEMS = 15
# Phase 33c: Google events one confirmation may create (Carter, 2026-10-30).
# 1.5x the single-series cap; a 12-week term of five weekday classes is exactly
# 60. Over the cap the whole message is refused; nothing is ever truncated.
MAX_BATCH_EVENTS = 60
# ``series.MAX_OCCURRENCES`` mirrored for copy only; ``series`` is imported
# lazily here (the medic path must load no Calendar-path module). A test keeps
# the two equal.
MAX_OCCURRENCES_HINT = 40
DEFAULT_DURATION = timedelta(hours=1)
YEAR_CHECK_DAYS = 180
SOURCE_TEXT_MAX = 80
TITLE_MAX = 120
WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
WEEKDAY_ZH_LABELS = ("一", "二", "三", "四", "五", "六", "日")

# Notes consumed by ``format_batch_clarification`` and the listener.
NOTE_BATCH = "batch"
NOTE_OVER_CAP = "batch_over_cap"
NOTE_MISSING_TIME = "batch_missing_time"
NOTE_NONE_INCLUDED = "batch_none_included"
NOTE_TOO_LONG = "batch_too_long"
NOTE_OVER_EVENT_CAP = "batch_over_event_cap"

REASON_MISSING_TITLE = "missing_title"
REASON_MISSING_DATE = "missing_date"
REASON_MISSING_TIME = "missing_time"
REASON_UNCLEAR = "unclear"
REASON_CROSSES_MIDNIGHT = "crosses_midnight"
REASON_MULTI_DAY_ALL_DAY = "multi_day_all_day"
REASON_REPEATING = "repeating"
REASON_WEEKDAY_MISMATCH = "weekday_mismatch"
REASON_PAST = "past"
REASON_UNSUPPORTED_SCRIPT = "unsupported_script"
REASON_UNCONFIRMED = "unconfirmed"
# Phase 33c: only reachable with the switch on; ``repeating`` stays the reason
# when it is off.
REASON_SERIES_OPEN_ENDED = "series_open_ended"
REASON_SERIES_OVER_CAP = "series_over_cap"
REASON_SERIES_NO_OCCURRENCES = "series_no_occurrences"

REASON_COPY = {
    REASON_MISSING_TITLE: ("冇活動名稱", "no activity name"),
    REASON_MISSING_DATE: ("冇日期", "no date"),
    REASON_MISSING_TIME: ("冇時間", "no time given"),
    REASON_UNCLEAR: ("資料唔清楚", "details are unclear"),
    REASON_CROSSES_MIDNIGHT: ("時間跨日", "time crosses midnight"),
    REASON_MULTI_DAY_ALL_DAY: ("多日全日活動，請逐日傳送", "multi-day all-day: send each day"),
    REASON_REPEATING: ("重複活動，請另外傳送", "repeating schedule: send it as its own message"),
    REASON_WEEKDAY_MISMATCH: ("星期幾同日期唔夾", "weekday does not match the date"),
    REASON_PAST: ("日期已過", "date has passed"),
    REASON_UNSUPPORTED_SCRIPT: ("唔支援簡體字", "Simplified Chinese not supported"),
    REASON_UNCONFIRMED: ("未確認／已取消", "unconfirmed or cancelled"),
    REASON_SERIES_OPEN_ENDED: (
        "重複活動冇寫結束日期，請寫明日期範圍",
        "repeating item has no end date: give the date range",
    ),
    REASON_SERIES_OVER_CAP: (
        f"重複多過 {MAX_OCCURRENCES_HINT} 次，請縮短日期範圍",
        f"more than {MAX_OCCURRENCES_HINT} repeats: shorten the range",
    ),
    REASON_SERIES_NO_OCCURRENCES: (
        "日期範圍內冇講明嘅星期",
        "the range has none of those weekdays",
    ),
}

NO_CHANGE = "未有改動日曆。/ No calendar change was made."
IMAGE_PROPOSAL_HEADER = (
    "從圖片讀到，請核對日期同時間 / "
    "Read from an image: check dates and times."
)
# Synthetic public example (ground rule 15). It shows the one shape the time
# question accepts back: the day, the clock and the activity on one line.
TIME_EXAMPLE = "例子 / Example: 11月6日 15:30 游泳課"
# Why: live 2026-10-29, the family answered the time question with a thread
# reply that listed only the clocks. The question stores no state, so only a
# whole new message can carry the times.
RESEND_WHOLE_LIST = (
    "只回覆時間唔會加入日曆，一定要再傳成個清單。/ "
    "A reply that gives only the times does not work. Send the whole list again."
)
# Phase 33b: an image-sourced question has no typed list to resend. The family
# keeps the photo, so the shortest fix is the same photo with the times in the
# message text; typing the list stays the alternative.
IMAGE_TIME_QUESTION = (
    "以下從圖片讀到嘅項目有日期但冇時間。請連張圖再傳一次，"
    "喺訊息文字寫「加event」同每項嘅開始時間（全日活動請寫「全日」）；"
    "又或者直接打字列出成個清單："
)
IMAGE_TIME_QUESTION_EN = (
    "These items from the image have a date but no time. Send the image again. "
    "In the message text, write 加event and a start time for each item. "
    'Write "all day" for an all-day item. You can also type the list:'
)
RESEND_IMAGE_WITH_TIMES = (
    "只回覆時間唔會加入日曆。請連圖片同時間一齊再傳一次，或者打字傳成個清單。/ "
    "A reply that gives only the times does not work. Send the image again with "
    "the times, or send the typed list."
)
OFFLINE_LIST_REPLY = (
    "一次加多個活動需要 AI 理解服務，請逐個傳送。/ "
    "Lists of events need the event-understanding service; send one event per message.\n"
    + NO_CHANGE
)

_MONTHS_EN = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
# Routing guard only: a line "carries a date" when it names a day. Not create
# vocabulary; a false positive only means the model (not a list route) reads it.
_DATE_TOKEN = re.compile(
    r"\d{1,2}\s*月\s*\d{1,2}\s*[日號]?"
    r"|\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}"
    r"|(?<![\d:/])\d{1,2}/\d{1,2}(?![\d:/])"
    r"|(?<!\d)\d{1,2}\s*號"
    rf"|\b(?:{_MONTHS_EN})\.?\s+\d{{1,2}}\b"
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS_EN})\b"
    r"|星期[一二三四五六日天]|禮拜[一二三四五六日天]|週[一二三四五六日]"
    r"|[（(][一二三四五六日][）)]"
    r"|\b(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(?:day)?\b"
    r"|今日|今晚|今朝|聽日|聽朝|聽晚|後日|\btoday\b|\btonight\b|\btomorrow\b",
    re.IGNORECASE,
)
_ALL_DAY_CUE = re.compile(r"全日|整日|成日|all[\s-]?day", re.IGNORECASE)


def dated_lines(text: str) -> list[str]:
    """The message lines that name a day, in message order.

    Routing and evidence only (same vocabulary as ``count_dated_lines``): the
    list mapper uses it to find an item's own line when the model returns no
    day. It never decides that a line is an event.
    """
    return [line for line in (text or "").splitlines() if _DATE_TOKEN.search(line)]


def count_dated_lines(text: str) -> int:
    """Number of non-empty lines that name a day (date, weekday or 今日/聽日)."""
    return len(dated_lines(text))


def looks_like_dated_list(text: str) -> bool:
    """True when at least two lines name a day (Phase 33a routing guard)."""
    return count_dated_lines(text) >= 2


@dataclass(frozen=True, slots=True)
class ExtractedItem:
    """One model item after the existing per-event mapper, before list checks."""

    result: ParseResult
    stated_weekday: int | None = None
    source_text: str | None = None
    local_date: date | None = None
    has_series: bool = False
    # Phase 33c: the item's own validated spec, and the mapper's note when the
    # model returned a series it could not use (``series_open_ended`` for a
    # repeating item with no end date).
    series: SeriesSpec | None = None
    series_note: str | None = None


@dataclass(frozen=True, slots=True)
class BatchOccurrence:
    """One Google event a list proposal will create (Phase 33c)."""

    item_index: int
    local_date: date
    start: datetime
    end: datetime


def expand_item(item: EventItem) -> list[SeriesOccurrence]:
    """The occurrences this one item writes, in date order.

    Contract: a one-off item is its own single window. A series item is its
    spec expanded (pure, cap 40) and then cut to the occurrences starting at
    ``item.start`` or later, so a timetable sent mid-term writes exactly the
    dates the proposal showed and no clock is involved — the same list today,
    tomorrow and on a retry. Raises ``SeriesExpansionError`` for a spec that
    does not expand.
    """
    if item.series is None:
        return [SeriesOccurrence(
            local_date=_normalize(item.start).date(),
            start=_normalize(item.start),
            end=_normalize(item.end),
        )]
    # Lazy like ``parse_fallback._has_series_cue``: the medic librarian imports
    # this module through ``parse_fallback`` and must load no Calendar path.
    from cec_vivisystem.series import expand_weekday_series

    first = _normalize(item.start)
    return [o for o in expand_weekday_series(item.series) if _normalize(o.start) >= first]


def item_occurrences(item: EventItem) -> list[SeriesOccurrence]:
    """``expand_item`` for copy and warnings; a broken stored spec degrades.

    Contract: always at least one window, so every caller (copy, warnings,
    overlap) can read ``[0]`` and ``[-1]``. A stored spec may both raise and
    expand to nothing — a hand-edited file whose ``start`` falls after the
    last occurrence cuts the whole list away — and copy must still render.
    The writer uses ``expand_item`` instead and refuses such an item.
    """
    from cec_vivisystem.series import SeriesExpansionError

    try:
        occurrences = expand_item(item)
    except SeriesExpansionError:
        occurrences = []
    return occurrences or [SeriesOccurrence(
        local_date=_normalize(item.start).date(),
        start=_normalize(item.start),
        end=_normalize(item.end),
    )]


def expand_batch_items(items: list[EventItem] | tuple[EventItem, ...]) -> list[BatchOccurrence]:
    """One flat window list for a whole proposal (at most ``MAX_BATCH_EVENTS``).

    Used by the copy, the between-item warnings and the overlap check, so all
    three see the same windows one yes would create.
    """
    flat: list[BatchOccurrence] = []
    for item in items:
        for occurrence in item_occurrences(item):
            flat.append(BatchOccurrence(
                item_index=item.index,
                local_date=occurrence.local_date,
                start=_normalize(occurrence.start),
                end=_normalize(occurrence.end),
            ))
    return flat


def batch_event_count(items: list[EventItem] | tuple[EventItem, ...]) -> int:
    """Google events these items would create (one per one-off item)."""
    return sum(max(int(item.occurrence_count or 1), 0) for item in items)


def series_items(items: list[EventItem] | tuple[EventItem, ...]) -> list[EventItem]:
    """The repeating items, in order."""
    return [item for item in items if item.series is not None]


def past_dropped_count(item: EventItem) -> int:
    """Occurrences of a series item that had already passed at proposal time."""
    if item.series is None:
        return 0
    from cec_vivisystem.series import SeriesExpansionError, expand_weekday_series

    try:
        whole = expand_weekday_series(item.series)
    except SeriesExpansionError:
        return 0
    return max(len(whole) - len(item_occurrences(item)), 0)


def verify_source_text(excerpt: object, message: str) -> str | None:
    """Return the excerpt only if it really occurs in the message (whitespace-normalised)."""
    if not isinstance(excerpt, str):
        return None
    wanted = " ".join(excerpt.split())
    if not wanted or wanted not in " ".join((message or "").split()):
        return None
    return _clip(wanted, SOURCE_TEXT_MAX)


def build_batch_result(
    extracted: list[ExtractedItem],
    *,
    message: str,
    now: datetime,
    correlation_id: str | None = None,
    notes_prefix: str = "llm_fallback",
    evidence: str | None = None,
    allow_series: bool = False,
) -> ParseResult:
    """Classify every item; return one list create or one clarification.

    Order of outcomes: more events than ``MAX_BATCH_EVENTS`` → whole-message
    clarification (no proposal, nothing truncated); any date-only line → time
    question (no proposal); nothing usable → clarification with reasons;
    otherwise a create whose ``batch`` holds the included items and
    ``batch_not_included`` the rest.

    ``message`` is what the family sent (it becomes ``raw_text`` for the reply
    and for narrowing). ``evidence`` is the text that holds the lines; Phase
    33b passes the vision model's returned lines, because an image caption
    holds none. It defaults to ``message`` (the typed-list path).

    ``allow_series`` is the Phase 33c kill switch, read once at the call site
    (``series.load_batch_series_enabled``); False is Phase 33a behaviour.
    """
    today = _normalize(now).date()
    lines_text = message if evidence is None else evidence
    included: list[EventItem] = []
    not_included: list[NotIncludedItem] = []
    for number, item in enumerate(extracted, start=1):
        verdict = classify_item(
            item, message=message, today=today, index=number, evidence=lines_text,
            allow_series=allow_series, correlation_id=correlation_id,
        )
        if isinstance(verdict, EventItem):
            included.append(verdict)
        else:
            not_included.append(verdict)
    reasons: dict[str, int] = {}
    for entry in not_included:
        reasons[entry.reason] = reasons.get(entry.reason, 0) + 1
    repeating = series_items(included)
    total_events = batch_event_count(included)
    logger.info(
        "batch_extract_completed",
        component=COMPONENT,
        outcome="success" if included else "partial",
        correlation_id=correlation_id,
        item_count=len(extracted),
        included_count=len(included),
        not_included_count=len(not_included),
        not_included_reasons=",".join(f"{k}:{v}" for k, v in sorted(reasons.items())) or None,
        dated_line_count=count_dated_lines(lines_text),
        series_item_count=len(repeating),
        series_event_count=batch_event_count(repeating),
        total_event_count=total_events,
    )
    if total_events > MAX_BATCH_EVENTS:
        # Checked before the time question: the included items alone already
        # exceed the cap, so the message must be split whatever the rest says.
        note = f"{NOTE_OVER_EVENT_CAP}:{total_events}"
    elif reasons.get(REASON_MISSING_TIME):
        note = NOTE_MISSING_TIME
    elif not included:
        note = NOTE_NONE_INCLUDED
    else:
        first = included[0]
        return ParseResult(
            intent_type=IntentType.CREATE_EVENT,
            title=first.title,
            start=first.start,
            end=first.end,
            all_day=first.all_day,
            location=first.location,
            participants=list(first.participants),
            raw_text=message,
            confidence=Confidence.MEDIUM,
            missing_fields=[],
            notes=f"{notes_prefix};{NOTE_BATCH}",
            batch=tuple(included),
            batch_not_included=tuple(not_included),
        )
    return ParseResult(
        intent_type=IntentType.NEEDS_CLARIFICATION,
        title=None,
        start=None,
        end=None,
        all_day=False,
        location=None,
        raw_text=message,
        confidence=Confidence.MEDIUM,
        missing_fields=["time"] if note == NOTE_MISSING_TIME else ["events"],
        notes=f"{notes_prefix};{note}",
        batch=tuple(included),
        batch_not_included=tuple(not_included),
    )


def over_cap_result(message: str, *, count: int | None, notes_prefix: str) -> ParseResult:
    """Clarification for a list above ``MAX_BATCH_ITEMS``. Never truncates."""
    suffix = f":{count}" if count else ""
    return ParseResult(
        intent_type=IntentType.NEEDS_CLARIFICATION,
        title=None, start=None, end=None, all_day=False, location=None,
        raw_text=message, confidence=Confidence.MEDIUM, missing_fields=["events"],
        notes=f"{notes_prefix};{NOTE_OVER_CAP}{suffix}",
    )


def classify_item(
    item: ExtractedItem,
    *,
    message: str,
    today: date,
    index: int,
    evidence: str | None = None,
    allow_series: bool = False,
    correlation_id: str | None = None,
) -> EventItem | NotIncludedItem:
    """Included ``EventItem`` or a ``NotIncludedItem`` with one reason code."""
    result = item.result
    title = _clip(result.title.strip(), TITLE_MAX) if result.title and result.title.strip() else None
    start = _normalize(result.start) if result.start is not None else None
    local_date = start.date() if start is not None else item.local_date

    def _out(reason: str, detail_count: int | None = None) -> NotIncludedItem:
        return NotIncludedItem(reason=reason, title=title, source_text=item.source_text,
                               local_date=local_date, detail_count=detail_count)

    missing = set(result.missing_fields)
    if contains_simplified_markers((result.title or "") + (result.location or "")):
        return _out(REASON_UNSUPPORTED_SCRIPT)
    if item.has_series or result.series is not None:
        if not allow_series:
            return _out(REASON_REPEATING)
        return _classify_series_item(
            item, title=title, index=index, today=today, out=_out,
            correlation_id=correlation_id,
        )
    # Phase 33b: the image prompt marks crossed-out / cancelled / TBC entries
    # with this name. Checked before the time rule: a "TBC" line has no clock,
    # and the time question would otherwise block the whole proposal.
    if REASON_UNCONFIRMED in missing:
        return _out(REASON_UNCONFIRMED)
    if not title:
        return _out(REASON_MISSING_TITLE)
    if local_date is None:
        return _out(REASON_MISSING_DATE)
    if item.stated_weekday is not None and item.stated_weekday != local_date.weekday():
        return _out(REASON_WEEKDAY_MISMATCH)
    if local_date < today:
        return _out(REASON_PAST)
    # A model "all day" without 全日 / all day in the text is a date-only line.
    # For an image the text is the returned lines, never the caption (33b).
    cue_text = item.source_text or (message if evidence is None else evidence) or ""
    all_day_cue = _ALL_DAY_CUE.search(cue_text) is not None
    # ``missing`` is the extractor's own verdict and stays authoritative here.
    # Do not relax it: a payload that reports a gap it also filled (live
    # 2026-10-29: a written clock in ``start`` plus missing_fields ["time"])
    # must be made consistent by the mapper, which still has the line text.
    if start is None or missing & {"time", "start"} or (result.all_day and not all_day_cue):
        return _out(REASON_MISSING_TIME)
    if missing - {"time", "start"}:
        return _out(REASON_UNCLEAR)
    end = _normalize(result.end) if result.end is not None else None
    end_defaulted = False
    if result.all_day:
        start = datetime.combine(local_date, datetime.min.time(), tzinfo=FAMILY_TZ)
        if end is None or end <= start:
            end = start + timedelta(days=1)
        if end - start > timedelta(days=1):
            return _out(REASON_MULTI_DAY_ALL_DAY)
    else:
        if end is None:
            end = start + DEFAULT_DURATION
            end_defaulted = True
        if end <= start:
            return _out(REASON_UNCLEAR)
        if end.date() > start.date():
            return _out(REASON_CROSSES_MIDNIGHT)
    return EventItem(
        index=index,
        title=title,
        start=start,
        end=end,
        all_day=bool(result.all_day),
        location=_clip(result.location.strip(), TITLE_MAX) if result.location else None,
        participants=tuple(result.participants),
        end_defaulted=end_defaulted,
        source_text=item.source_text,
    )


def _classify_series_item(
    item: ExtractedItem,
    *,
    title: str | None,
    index: int,
    today: date,
    out,
    correlation_id: str | None = None,
) -> EventItem | NotIncludedItem:
    """One repeating list item (Phase 33c). The expander owns window and range.

    Order: the item's own declared gaps, title, open-ended, all-day, weekday
    cross-check, expand, past trim. Occurrences already past are dropped and
    the count is stated in the copy; the whole item is refused only when every
    occurrence has passed.
    """
    from cec_vivisystem.series import SeriesExpansionError

    result = item.result
    spec = item.series or result.series
    # The extractor's own verdict is authoritative for a repeating item too.
    # Why: a shared timetable range is copied into *every* class line, so a
    # crossed-out or 待定 class arrives with both a series and
    # ``unconfirmed``. Without this the spec would promote a cancelled class
    # into N Google events (ADR 0019 marks such a line as not an event).
    missing = set(result.missing_fields)
    if REASON_UNCONFIRMED in missing:
        return out(REASON_UNCONFIRMED)
    if not title:
        return out(REASON_MISSING_TITLE)
    # Do not relax this: the spec owns the day and the clock, so ``time`` /
    # ``start`` / ``end`` are expected to be absent and ``series_range`` is
    # the open-ended note handled just below. Any other declared gap is a
    # real one, exactly as on the one-off path.
    if missing - _SERIES_EXPECTED_GAPS:
        _log_series_refused(REASON_UNCLEAR, index=index, correlation_id=correlation_id)
        return out(REASON_UNCLEAR)
    if spec is None:
        reason = (REASON_SERIES_OPEN_ENDED
                  if item.series_note == REASON_SERIES_OPEN_ENDED else REASON_UNCLEAR)
        _log_series_refused(reason, index=index, correlation_id=correlation_id)
        return out(reason)
    if result.all_day:
        _log_series_refused(REASON_MULTI_DAY_ALL_DAY, index=index, correlation_id=correlation_id)
        return out(REASON_MULTI_DAY_ALL_DAY)
    # The weekday written on the item's own line must be one of the item's
    # weekdays (copied, never computed; a multi-weekday line states none).
    if item.stated_weekday is not None and item.stated_weekday not in spec.weekdays:
        _log_series_refused(REASON_WEEKDAY_MISMATCH, index=index, correlation_id=correlation_id)
        return out(REASON_WEEKDAY_MISMATCH)
    draft = EventItem(
        index=index, title=title,
        start=datetime.combine(spec.range_start, spec.start_time, tzinfo=FAMILY_TZ),
        end=datetime.combine(spec.range_start, spec.start_time, tzinfo=FAMILY_TZ),
        all_day=False, series=spec,
    )
    try:
        occurrences = expand_item(draft)
    except SeriesExpansionError as exc:
        reason = _SERIES_ERROR_REASONS.get(exc.reason, REASON_UNCLEAR)
        _log_series_refused(reason, index=index, correlation_id=correlation_id, count=exc.count)
        # Only the cap needs the number in the copy; the range guard variant of
        # ``over_cap`` carries no count, so the plain line is used.
        detail = exc.count if reason == REASON_SERIES_OVER_CAP else None
        return out(reason, detail)
    future = [o for o in occurrences if o.local_date >= today]
    if not future:
        _log_series_refused(REASON_PAST, index=index, correlation_id=correlation_id,
                            count=len(occurrences))
        return out(REASON_PAST)
    first = future[0]
    return EventItem(
        index=index,
        title=title,
        start=first.start,
        end=first.end,
        all_day=False,
        location=_clip(result.location.strip(), TITLE_MAX) if result.location else None,
        participants=tuple(result.participants),
        end_defaulted=spec.end_time is None,
        source_text=item.source_text,
        series=spec,
        occurrence_count=len(future),
    )


# Gaps a repeating item may declare without becoming ``unclear``: its spec
# supplies the day and the clock, and ``series_range`` is the open-ended note.
_SERIES_EXPECTED_GAPS = frozenset({"time", "start", "end", "series_range"})


_SERIES_ERROR_REASONS = {
    "over_cap": REASON_SERIES_OVER_CAP,
    "no_occurrences": REASON_SERIES_NO_OCCURRENCES,
    "invalid_window": REASON_CROSSES_MIDNIGHT,
    "invalid_range": REASON_UNCLEAR,
    "empty_weekdays": REASON_UNCLEAR,
}


def _log_series_refused(
    reason: str, *, index: int, correlation_id: str | None, count: int | None = None
) -> None:
    """Counts and codes only; no title, no date, no message content."""
    logger.info(
        "batch_series_refused",
        component=COMPONENT,
        outcome="partial",
        correlation_id=correlation_id,
        item_index=index,
        reason=reason,
        occurrence_count=count,
    )


def remaining_items(
    batch: tuple[EventItem, ...] | None, dropped: list[int] | tuple[int, ...] = ()
) -> list[EventItem]:
    """Items still in the list after narrowing, in original order."""
    removed = set(dropped)
    return [item for item in (batch or ()) if item.index not in removed]


# --- copy (Traditional Chinese + English) -----------------------------------


def format_batch_item(item: EventItem) -> str:
    """``3. 2026-11-20（五）09:00–10:00 急救考試（冇講結束時間…）· 地點 Location: …``.

    Phase 33c: a repeating item is one line naming its weekdays, daily window,
    first and last occurrence and 「共 N 次」. The shown span is the first and
    last occurrence, not the stated range, so the span and the count always
    describe exactly what yes creates.
    """
    start = _normalize(item.start)
    if item.series is not None:
        line = f"{item.index}. {item.title} · {format_series_summary(item)}"
    else:
        day = f"{start.date().isoformat()}（{WEEKDAY_ZH_LABELS[start.weekday()]}）"
        if item.all_day:
            when = f"{day}全日"
        else:
            when = f"{day}{start.strftime('%H:%M')}–{_normalize(item.end).strftime('%H:%M')}"
        line = f"{item.index}. {when} {item.title}"
    if item.end_defaulted:
        line += "（冇講結束時間，預設 1 小時 / no end time: 1 h default）"
    if item.location:
        line += f" · 地點 Location: {item.location}"
    if item.participants:
        line += f" · 參與者 Participants: {', '.join(item.participants)}"
    return line


def format_series_summary(item: EventItem) -> str:
    """``逢星期一 15:15–16:15 · 2026-11-02 至 2026-11-30 · 共 5 次``."""
    from cec_vivisystem.series import weekday_summary

    spec = item.series
    if spec is None:
        raise ValueError("series summary requires a series item")
    occurrences = item_occurrences(item)
    first, last = occurrences[0], occurrences[-1]
    window = (f"{_normalize(first.start).strftime('%H:%M')}–"
              f"{_normalize(first.end).strftime('%H:%M')}")
    return (
        f"{weekday_summary(spec.weekdays)} {window} · "
        f"{first.local_date.isoformat()} 至 {last.local_date.isoformat()} · "
        f"共 {len(occurrences)} 次"
    )


def format_holiday_warning(event_count: int) -> str:
    """One line per proposal: the bot knows no school or public holidays.

    ``event_count`` counts the **repeating** items' events, so the sentence is
    scoped to them: in a mixed list the header's event total is larger, and
    two different totals two lines apart read as a contradiction. It counts
    events, not days — a 逢一三五 item and a Monday item share days.
    """
    return (
        f"注意：學校假期同公眾假期我分唔到，重複活動嘅 {event_count} 個活動全部會建立，請核對 / "
        f"Warning: I do not know the school holidays or the public holidays. "
        f"I will make all {event_count} events of the repeating items. "
        f"Please check the dates."
    )


# Phase 33c: 15 trimmed items would be 15 warning lines in a proposal the
# family must read before one yes. Capped like the overlap section.
PAST_DROPPED_MAX_LINES = 3


def format_past_dropped_warnings(items: list[EventItem] | tuple[EventItem, ...]) -> list[str]:
    """One line per trimmed repeating item, then one collapsed total line.

    At most ``PAST_DROPPED_MAX_LINES`` items are named; the rest are summed
    into one line, so the block cannot grow with the item count.
    """
    dropped = [(item.index, past_dropped_count(item)) for item in items]
    dropped = [(index, count) for index, count in dropped if count]
    lines = [
        f"注意：第 {index} 項有 {count} 次已經過去，唔會建立 / "
        f"Warning: {count} events of item {index} have passed. I will not make them."
        for index, count in dropped[:PAST_DROPPED_MAX_LINES]
    ]
    rest = dropped[PAST_DROPPED_MAX_LINES:]
    if rest:
        more_items = len(rest)
        more_events = sum(count for _, count in rest)
        lines.append(
            f"注意：另外 {more_items} 項有 {more_events} 次已經過去，唔會建立 / "
            f"Warning: {more_events} events of {more_items} more items have passed. "
            f"I will not make them."
        )
    return lines


def format_removed_label(item: EventItem) -> str:
    """``第 5 項（4 次）`` for a repeating item, ``第 3 項`` for a one-off one."""
    if item.series is None:
        return f"第 {item.index} 項"
    return f"第 {item.index} 項（{item.occurrence_count} 次）"


def format_not_included(item: NotIncludedItem) -> str:
    """``• 「12月11日 結業禮」— 冇時間 / no time given``.

    Phase 33c: a reason that carries ``detail_count`` names the real number, so
    an over-cap repeating item says how many repeats the written range has and
    the family knows how much to shorten it.
    """
    zh, en = REASON_COPY.get(item.reason, REASON_COPY[REASON_UNCLEAR])
    if item.detail_count is not None:
        zh = f"{zh}（呢個範圍有 {item.detail_count} 次）"
        en = f"{en} ({item.detail_count} in this range)"
    return f"{_shown_line(item)}— {zh} / {en}"


def _shown_line(item: NotIncludedItem) -> str:
    shown = item.source_text or item.title
    if not shown and item.local_date is not None:
        shown = item.local_date.isoformat()
    return f"• 「{_clip(shown or '（冇內容）', SOURCE_TEXT_MAX)}」"


def format_item_label(item: EventItem) -> str:
    """``第 3 項（2026-11-20 急救考試）`` for acks."""
    return f"第 {item.index} 項（{_normalize(item.start).date().isoformat()} {item.title}）"


def batch_warnings(
    items: list[EventItem],
    *,
    message: str,
    understood_count: int,
) -> list[str]:
    """Advisory lines (items stay included): duplicates, overlaps, year, honesty.

    Phase 33c: a repeating item is compared by its occurrence windows, so two
    weekly classes warn only when they really share a day and a window. A
    duplicate now also needs the same series spec.
    """
    lines: list[str] = []
    seen: dict[tuple, int] = {}
    for item in items:
        key = _item_identity(item)
        if key in seen:
            lines.append(
                f"注意：第 {seen[key]} 同第 {item.index} 項一模一樣 / "
                f"Warning: items {seen[key]} and {item.index} are the same."
            )
        else:
            seen[key] = item.index
    windows = {item.index: item_occurrences(item) for item in items}
    for position, left in enumerate(items):
        for right in items[position + 1:]:
            if _item_identity(left) == _item_identity(right):
                continue
            if _occurrences_overlap(windows[left.index], windows[right.index]):
                lines.append(
                    f"注意：第 {left.index} 同第 {right.index} 項時間重疊 / "
                    f"Warning: items {left.index} and {right.index} overlap each other."
                )
    if items:
        earliest = min(item.start for item in items)
        far = [i.index for i in items if (i.start - earliest).days > YEAR_CHECK_DAYS]
        if far:
            numbers = "、".join(str(n) for n in far)
            lines.append(
                f"注意：第 {numbers} 項同最早嗰項相隔超過半年，請核對年份 / "
                f"Warning: item(s) {', '.join(str(n) for n in far)} are more than "
                f"{YEAR_CHECK_DAYS} days after the earliest item; check the year."
            )
    dated = count_dated_lines(message)
    if dated > understood_count:
        lines.append(
            f"注意：你個訊息有 {dated} 行有日期，但我只理解到 {understood_count} 項 / "
            f"Warning: {dated} lines have dates, but only {understood_count} items were understood."
        )
    return lines


def _item_identity(item: EventItem) -> tuple:
    """What makes two list items "the same" line for the duplicate warning."""
    return (item.title.casefold(), item.start, item.end, item.series)


def _occurrences_overlap(
    left: list[SeriesOccurrence], right: list[SeriesOccurrence]
) -> bool:
    """True when any occurrence of one item intersects one of the other.

    Exact and bounded: at most 60 windows per proposal on each side.
    """
    by_day: dict[date, list[SeriesOccurrence]] = {}
    for occurrence in right:
        by_day.setdefault(occurrence.local_date, []).append(occurrence)
    for occurrence in left:
        for other in by_day.get(occurrence.local_date, ()):
            if max(_normalize(occurrence.start), _normalize(other.start)) < min(
                _normalize(occurrence.end), _normalize(other.end)
            ):
                return True
    return False


def format_batch_clarification(result: ParseResult) -> str | None:
    """Clarify copy for list outcomes, or None if the result is not a list one."""
    tokens = (result.notes or "").split(";")
    note = next((t for t in tokens if t.startswith("batch_")), "")
    if not note:
        return None
    # Same marker as the proposal header (``confirmation._image_header``): the
    # remedy differs, because an image question has no typed list to resend.
    from_image = "from_image" in tokens
    not_included = list(result.batch_not_included)
    if note.startswith(NOTE_OVER_CAP):
        _, _, count = note.partition(":")
        size = f"（呢個訊息有 {count} 個或者更多）" if count else ""
        size_en = f" (this message has {count} or more)" if count else ""
        return (
            f"一次最多加 {MAX_BATCH_ITEMS} 個活動{size}。請分開幾次傳送，每次最多 "
            f"{MAX_BATCH_ITEMS} 個。我冇建立任何提案。\n"
            f"At most {MAX_BATCH_ITEMS} events per message{size_en}. "
            "Please split the list; nothing was proposed.\n" + NO_CHANGE
        )
    if note.startswith(NOTE_OVER_EVENT_CAP):
        _, _, count = note.partition(":")
        total = count or "?"
        return (
            f"呢個訊息會建立 {total} 個活動，一次最多 {MAX_BATCH_EVENTS} 個。"
            "請分開傳送，例如一個月一次，或者一班一次。我冇建立任何提案。\n"
            f"This message makes {total} events. The limit is {MAX_BATCH_EVENTS} events "
            "per message. Please send it in parts; for example one month per message, "
            "or one class per message. I made no proposal.\n" + NO_CHANGE
        )
    if note == NOTE_TOO_LONG:
        return (
            "個清單太長，我讀唔晒。請分開幾次傳送。\n"
            "The list is too long to read in one go. Please split it.\n" + NO_CHANGE
        )
    if note == NOTE_MISSING_TIME:
        timeless = [n for n in not_included if n.reason == REASON_MISSING_TIME]
        others = [n for n in not_included if n.reason != REASON_MISSING_TIME]
        lines = [IMAGE_TIME_QUESTION, IMAGE_TIME_QUESTION_EN] if from_image else [
            (
                "以下項目有日期但冇時間。請喺每行加開始時間（全日活動請寫「全日」），"
                "然後將成個清單再傳一次："
            ),
            (
                "These lines have a date but no time. Add a start time to each line "
                "(or write \"all day\"). Then send the whole list again:"
            ),
        ]
        lines.extend(_shown_line(n) for n in timeless)
        if others:
            lines.append(f"另外未包括 / Also not included ({len(others)}):")
            lines.extend(format_not_included(n) for n in others)
        lines.append(TIME_EXAMPLE)
        lines.append(RESEND_IMAGE_WITH_TIMES if from_image else RESEND_WHOLE_LIST)
        lines.append("我未建立任何提案。/ No proposal was made.")
        lines.append(NO_CHANGE)
        return "\n".join(lines)
    if note == NOTE_NONE_INCLUDED:
        lines = [
            "呢個訊息入面冇一項可以加入日曆。/ No event in this message can be proposed.",
            f"未包括 / Not included ({len(not_included)}):",
        ]
        lines.extend(format_not_included(n) for n in not_included)
        lines.append(NO_CHANGE)
        return "\n".join(lines)
    return None


# --- narrowing replies (before yes) ------------------------------------------

_DROP_VERBS = ("唔要", "不要", "剔走", "剔除", "drop", "remove", "skip")
_KEEP_VERBS = ("只要", "淨係要", "keep", "only")
_EDIT_REPLY = re.compile(
    r"^\s*(?P<verb>" + "|".join(_DROP_VERBS + _KEEP_VERBS) + r")\s*(?:items?\s*)?"
    r"(?P<nums>.+?)\s*[.。!！]?\s*$",
    re.IGNORECASE,
)
_EDIT_NUMBER = re.compile(r"^第?(\d{1,2})(?:個|項)?$")
_EDIT_SEPARATORS = re.compile(r"[,，、\s]+|同|和|\band\b|&")


@dataclass(frozen=True, slots=True)
class BatchEdit:
    """A whole-message narrowing reply: drop or keep these original numbers."""

    keep: bool
    numbers: tuple[int, ...]


def classify_batch_edit(text: str) -> BatchEdit | None:
    """``唔要 3`` / ``剔走 2,4`` / ``drop 3`` / ``只要 1,4`` / ``keep 1,4``; else None.

    Bare ``不要`` stays a reject (it never reaches here with no number).
    """
    normalized = unicodedata.normalize("NFKC", text or "")
    match = _EDIT_REPLY.match(normalized)
    if match is None:
        return None
    body = re.sub(r"第\s+", "第", match.group("nums"))
    body = re.sub(r"\s+(個|項)", r"\1", body)
    numbers: list[int] = []
    for token in _EDIT_SEPARATORS.split(body):
        if not token:
            continue
        found = _EDIT_NUMBER.match(token)
        if found is None:
            return None
        numbers.append(int(found.group(1)))
    if not numbers:
        return None
    verb = match.group("verb").casefold()
    return BatchEdit(keep=verb in _KEEP_VERBS, numbers=tuple(dict.fromkeys(numbers)))


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _normalize(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=FAMILY_TZ)
    return value.astimezone(FAMILY_TZ)
