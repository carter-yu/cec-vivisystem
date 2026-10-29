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

Do not: renumber items, truncate an over-cap list, move a past date to next
year, nest a weekday series in a list, or claim a calendar write in copy.
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
)
from cec_vivisystem.parser import contains_simplified_markers

logger = get_logger(__name__)

COMPONENT = "event_batch"
FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
MAX_BATCH_ITEMS = 15
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
) -> ParseResult:
    """Classify every item; return one list create or one clarification.

    Order of outcomes: any date-only line → time question (no proposal);
    nothing usable → clarification with reasons; otherwise a create whose
    ``batch`` holds the included items and ``batch_not_included`` the rest.
    """
    today = _normalize(now).date()
    included: list[EventItem] = []
    not_included: list[NotIncludedItem] = []
    for number, item in enumerate(extracted, start=1):
        verdict = classify_item(item, message=message, today=today, index=number)
        if isinstance(verdict, EventItem):
            included.append(verdict)
        else:
            not_included.append(verdict)
    reasons: dict[str, int] = {}
    for entry in not_included:
        reasons[entry.reason] = reasons.get(entry.reason, 0) + 1
    logger.info(
        "batch_extract_completed",
        component=COMPONENT,
        outcome="success" if included else "partial",
        correlation_id=correlation_id,
        item_count=len(extracted),
        included_count=len(included),
        not_included_count=len(not_included),
        not_included_reasons=",".join(f"{k}:{v}" for k, v in sorted(reasons.items())) or None,
        dated_line_count=count_dated_lines(message),
    )
    if reasons.get(REASON_MISSING_TIME):
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
) -> EventItem | NotIncludedItem:
    """Included ``EventItem`` or a ``NotIncludedItem`` with one reason code."""
    result = item.result
    title = _clip(result.title.strip(), TITLE_MAX) if result.title and result.title.strip() else None
    start = _normalize(result.start) if result.start is not None else None
    local_date = start.date() if start is not None else item.local_date

    def _out(reason: str) -> NotIncludedItem:
        return NotIncludedItem(reason=reason, title=title, source_text=item.source_text,
                               local_date=local_date)

    missing = set(result.missing_fields)
    if contains_simplified_markers((result.title or "") + (result.location or "")):
        return _out(REASON_UNSUPPORTED_SCRIPT)
    if item.has_series or result.series is not None:
        return _out(REASON_REPEATING)
    if not title:
        return _out(REASON_MISSING_TITLE)
    if local_date is None:
        return _out(REASON_MISSING_DATE)
    if item.stated_weekday is not None and item.stated_weekday != local_date.weekday():
        return _out(REASON_WEEKDAY_MISMATCH)
    if local_date < today:
        return _out(REASON_PAST)
    # A model "all day" without 全日 / all day in the text is a date-only line.
    all_day_cue = _ALL_DAY_CUE.search(item.source_text or message or "") is not None
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


def remaining_items(
    batch: tuple[EventItem, ...] | None, dropped: list[int] | tuple[int, ...] = ()
) -> list[EventItem]:
    """Items still in the list after narrowing, in original order."""
    removed = set(dropped)
    return [item for item in (batch or ()) if item.index not in removed]


# --- copy (Traditional Chinese + English) -----------------------------------


def format_batch_item(item: EventItem) -> str:
    """``3. 2026-11-20（五）09:00–10:00 急救考試（冇講結束時間…）· 地點 Location: …``."""
    start = _normalize(item.start)
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


def format_not_included(item: NotIncludedItem) -> str:
    """``• 「12月11日 結業禮」— 冇時間 / no time given``."""
    zh, en = REASON_COPY.get(item.reason, REASON_COPY[REASON_UNCLEAR])
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
    """Advisory lines (items stay included): duplicates, overlaps, year, honesty."""
    lines: list[str] = []
    seen: dict[tuple, int] = {}
    for item in items:
        key = (item.title.casefold(), item.start, item.end)
        if key in seen:
            lines.append(
                f"注意：第 {seen[key]} 同第 {item.index} 項一模一樣 / "
                f"Warning: items {seen[key]} and {item.index} are the same."
            )
        else:
            seen[key] = item.index
    for position, left in enumerate(items):
        for right in items[position + 1:]:
            if (left.title.casefold(), left.start, left.end) == (
                right.title.casefold(), right.start, right.end
            ):
                continue
            if max(left.start, right.start) < min(left.end, right.end):
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


def format_batch_clarification(result: ParseResult) -> str | None:
    """Clarify copy for list outcomes, or None if the result is not a list one."""
    tokens = (result.notes or "").split(";")
    note = next((t for t in tokens if t.startswith("batch_")), "")
    if not note:
        return None
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
    if note == NOTE_TOO_LONG:
        return (
            "個清單太長，我讀唔晒。請分開幾次傳送。\n"
            "The list is too long to read in one go. Please split it.\n" + NO_CHANGE
        )
    if note == NOTE_MISSING_TIME:
        timeless = [n for n in not_included if n.reason == REASON_MISSING_TIME]
        others = [n for n in not_included if n.reason != REASON_MISSING_TIME]
        lines = [
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
        lines.append(RESEND_WHOLE_LIST)
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
