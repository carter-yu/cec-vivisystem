"""Model-backed create extraction, with legacy rules-first fallback compatibility.

Listener uses LLM-first when configured (ADR 0008). Deterministic control routes,
validation and explicit confirmation still protect all side effects. Offline
parse() remains rules-only; no ongoing create vocabulary expansion is required.

Phase 33a (ADR 0018): prompt v6 returns ``events[]`` (1–15 items). One item maps
exactly like a v5 payload (single create or Phase 27 series); two or more become
one list create checked per item by ``event_batch``. A v5-shaped payload is still
accepted. v7 is the default (Carter, 2026-10-30); ``CEC_CREATE_PROMPT_VERSION=v6``
(or ``v5``) is the rollback path without code changes.

Phase 33c (ADR 0018 amendment): prompt v7 adds the list-series rules (a shared
header line is not an item; the shared range and window are copied into every
class item). An item's ``series`` now reaches ``event_batch`` when
``CEC_BATCH_SERIES`` is on; with the switch off the item is still refused as
``repeating``, so both prompts are safe either way.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as dt_time
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from cec_vivisystem.event_batch import (
    MAX_BATCH_ITEMS,
    NOTE_TOO_LONG,
    ExtractedItem,
    build_batch_result,
    count_dated_lines,
    dated_lines,
    over_cap_result,
    verify_source_text,
)
from cec_vivisystem.logging import get_logger
from cec_vivisystem.models import Confidence, IntentType, ParseResult, SeriesSpec
from cec_vivisystem.parse_misses import ParseMissStore, record_parse_miss
from cec_vivisystem.parser import contains_simplified_markers, parse_control
from cec_vivisystem.parser import parse as rule_parse

logger = get_logger(__name__)

COMPONENT = "parse_fallback"
FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
DEFAULT_MODEL = "grok-4.5"
# Official non-reasoning SKU so a JSON create can finish inside the 15s Slack budget.
# grok-4.5 reasoning cannot be disabled (default effort=high). Incident xAI log
# 2026-09-16: 753 reasoning tokens, 3 completion tokens, empty assistant JSON.
DEFAULT_FALLBACK_MODEL = "grok-4.20-0309-non-reasoning"
DEFAULT_BASE_URL = "https://api.x.ai/v1"
DEFAULT_TIMEOUT_S = 15.0
DEFAULT_MAX_RETRIES = 0
# Flagship SKUs always think. Do not put them first on a 15s Slack budget.
_ALWAYS_REASONING_MODELS = frozenset({"grok-4.5", "grok-4.6"})
_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
# v7 (events[] with the list-series rules) is the default per Carter's decision
# on 2026-10-30; v6 and v5 stay available as rollbacks with
# ``CEC_CREATE_PROMPT_VERSION=v6`` / ``v5``.
PROMPT_PATHS = {
    "v5": _PROMPT_DIR / "create_event.v5.txt",
    "v6": _PROMPT_DIR / "create_event.v6.txt",
    "v7": _PROMPT_DIR / "create_event.v7.txt",
}
DEFAULT_PROMPT_VERSION = "v7"
PROMPT_PATH = PROMPT_PATHS[DEFAULT_PROMPT_VERSION]
ALLOWED_INTENTS = frozenset(
    {
        IntentType.CREATE_EVENT,
        IntentType.NEEDS_CLARIFICATION,
        IntentType.UNKNOWN,
    }
)
ALLOWED_PARTICIPANTS = frozenset({"Cedric", "Coco", "Elaine", "Carter"})
MAX_COMPLETION_TOKENS = 1024
# Fifteen ISO-timestamped items need roughly 1,100–1,400 tokens (estimate).
MAX_COMPLETION_TOKENS_V6 = 2048
SERIES_WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
SERIES_FIELDS = ("weekdays", "range_start", "range_end", "start_time", "end_time")
# Phase 27: a bounded weekday series is structured inputs for the local
# expander, never one long start/end. The model cannot write; the family
# still confirms the expanded list.
SERIES_SCHEMA = {
    "anyOf": [
        {"type": "null"},
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "weekdays": {"type": "array", "items": {
                    "type": "string", "enum": list(SERIES_WEEKDAY_CODES),
                }},
                "range_start": {"type": "string"},
                "range_end": {"type": ["string", "null"]},
                "start_time": {"type": "string"},
                "end_time": {"type": ["string", "null"]},
            },
            "required": list(SERIES_FIELDS),
        },
    ]
}
# The provider constrains shape; local validation remains authoritative.
CREATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "intent_type": {"type": "string", "enum": sorted(i.value for i in ALLOWED_INTENTS)},
        "title": {"type": ["string", "null"]},
        "start": {"type": ["string", "null"], "format": "date-time"},
        "end": {"type": ["string", "null"], "format": "date-time"},
        "all_day": {"type": "boolean"},
        "location": {"type": ["string", "null"]},
        "participants": {"type": "array", "items": {
            "type": "string", "enum": sorted(ALLOWED_PARTICIPANTS),
        }},
        "missing_fields": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["medium"]},
        "series": SERIES_SCHEMA,
    },
    "required": ["intent_type", "title", "start", "end", "all_day", "location",
                 "participants", "missing_fields", "confidence", "series"],
}
# ``series`` may be absent in older-shaped payloads (treated as null).
_BASE_FIELDS = frozenset(CREATE_SCHEMA["required"]) - {"series"}

# Phase 33a item: the v5 event fields minus intent/confidence, plus three
# copied-not-computed fields the local checks use. ``date`` is the day a
# clock-less line names (start stays null), so a missing time can be asked.
_ITEM_FIELDS = (
    "title", "start", "end", "all_day", "location", "participants",
    "missing_fields", "series", "date", "stated_weekday", "source_text",
)
# Strict structured outputs honour ``description`` but ignore ``format`` and
# ``pattern``, so the item rules are written where the model generates each
# field. One line each: the system prompt stays the long form.
_ITEM_DESCRIPTIONS = {
    "title": "The activity, without command, date or time wording.",
    "start": (
        "This item's start as an ISO 8601 timestamp with +08:00. Null only when "
        "the item's line writes no clock."
    ),
    "end": "This item's end as an ISO 8601 timestamp with +08:00, else null.",
    "all_day": "True only when 全日 or all day is written for this item.",
    "missing_fields": (
        "Only field names this item does not provide. Empty when the item is "
        "complete. Never name a field that this item fills."
    ),
    "date": "This item's day as YYYY-MM-DD.",
    "stated_weekday": "The weekday written next to this item's date, else null.",
    "source_text": "This item's own line, copied, at most 80 characters.",
}
EVENT_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        # New dicts: v5 ``CREATE_SCHEMA`` keeps its own property objects, so the
        # rollback request shape never changes with the item wording.
        **{name: {**CREATE_SCHEMA["properties"][name],
                  **({"description": _ITEM_DESCRIPTIONS[name]}
                     if name in _ITEM_DESCRIPTIONS else {})}
           for name in (
               "title", "start", "end", "all_day", "location", "participants",
               "missing_fields", "series",
           )},
        "date": {"type": ["string", "null"], "description": _ITEM_DESCRIPTIONS["date"]},
        "stated_weekday": {
            "anyOf": [
                {"type": "null"},
                {"type": "string", "enum": list(SERIES_WEEKDAY_CODES)},
            ],
            "description": _ITEM_DESCRIPTIONS["stated_weekday"],
        },
        "source_text": {
            "type": ["string", "null"],
            "description": _ITEM_DESCRIPTIONS["source_text"],
        },
    },
    "required": list(_ITEM_FIELDS),
}
# maxItems is a provider guarantee (xAI enforces it up to 256); local mapping
# still treats more than the cap as over cap and never truncates.
CREATE_SCHEMA_V6 = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "intent_type": CREATE_SCHEMA["properties"]["intent_type"],
        "events": {"type": "array", "items": EVENT_ITEM_SCHEMA, "maxItems": MAX_BATCH_ITEMS},
        "over_cap": {"type": "boolean"},
        "confidence": {"type": "string", "enum": ["medium"]},
    },
    "required": ["intent_type", "events", "over_cap", "confidence"],
}
_V6_FIELDS = frozenset(CREATE_SCHEMA_V6["required"])


def validate_create_payload(payload: object) -> None:
    """Reject broken provider contracts even if schema enforcement is unavailable.

    Accepts the v6 ``events[]`` shape and the v5 single-event shape (rollback).
    """
    if isinstance(payload, dict) and "events" in payload:
        _validate_v6_payload(payload)
        return
    if not isinstance(payload, dict) or set(payload) not in (
        _BASE_FIELDS, _BASE_FIELDS | {"series"}
    ):
        raise ValueError("Invalid extraction fields")
    if payload["intent_type"] not in {i.value for i in ALLOWED_INTENTS}:
        raise ValueError("Invalid extraction intent")
    if payload["confidence"] != "medium" or not isinstance(payload["all_day"], bool):
        raise ValueError("Invalid extraction field type")
    for name in ("title", "start", "end", "location"):
        if payload[name] is not None and not isinstance(payload[name], str):
            raise ValueError(f"Invalid extraction {name}")
    for name in ("participants", "missing_fields"):
        if not isinstance(payload[name], list) or any(
            not isinstance(value, str) for value in payload[name]
        ):
            raise ValueError(f"Invalid extraction {name}")
    if any(name not in ALLOWED_PARTICIPANTS for name in payload["participants"]):
        raise ValueError("Invalid extraction participant")
    for name in ("start", "end"):
        if payload[name] is not None:
            dt = datetime.fromisoformat(payload[name])
            if dt.tzinfo is None:
                raise ValueError(f"Extraction {name} requires timezone")
    series = payload.get("series")
    if series is not None:
        _validate_series_payload(series)


def _validate_v6_payload(payload: dict) -> None:
    if set(payload) != _V6_FIELDS:
        raise ValueError("Invalid extraction fields")
    if payload["intent_type"] not in {i.value for i in ALLOWED_INTENTS}:
        raise ValueError("Invalid extraction intent")
    if payload["confidence"] != "medium" or not isinstance(payload["over_cap"], bool):
        raise ValueError("Invalid extraction field type")
    events = payload["events"]
    if not isinstance(events, list):
        raise TypeError("Invalid extraction events")
    for item in events:
        if not isinstance(item, dict) or set(item) != set(_ITEM_FIELDS):
            raise ValueError("Invalid extraction event fields")
        validate_create_payload(_v5_item(item, payload["intent_type"]))
        if item["date"] is not None:
            if not isinstance(item["date"], str):
                raise ValueError("Invalid extraction event date")
            date.fromisoformat(item["date"])
        if item["stated_weekday"] not in (None, *SERIES_WEEKDAY_CODES):
            raise ValueError("Invalid extraction stated_weekday")
        if item["source_text"] is not None and not isinstance(item["source_text"], str):
            raise ValueError("Invalid extraction source_text")


def _v5_item(item: Mapping[str, object], intent: object) -> dict[str, object]:
    """One v6 item in the v5 single-event shape, so the v5 mapper runs per item."""
    defaults: dict[str, object] = {"all_day": False, "participants": [], "missing_fields": []}
    missing = item.get("missing_fields")
    if isinstance(missing, list):
        # v6 says "time" for a clock-less dated line; the v5 vocabulary is "start".
        item = {**item, "missing_fields": list(dict.fromkeys(
            "start" if value == "time" else value for value in missing
        ))}
    return {
        "intent_type": intent,
        "confidence": "medium",
        **{name: item.get(name, defaults.get(name)) for name in (
            "title", "start", "end", "all_day", "location", "participants",
            "missing_fields", "series",
        )},
    }


def _validate_series_payload(series: object) -> None:
    if not isinstance(series, dict) or set(series) != set(SERIES_FIELDS):
        raise ValueError("Invalid extraction series fields")
    weekdays = series["weekdays"]
    if (not isinstance(weekdays, list) or not weekdays
            or any(day not in SERIES_WEEKDAY_CODES for day in weekdays)):
        raise ValueError("Invalid extraction series weekdays")
    for name, parse_value, nullable in (
        ("range_start", date.fromisoformat, False),
        ("range_end", date.fromisoformat, True),
        ("start_time", dt_time.fromisoformat, False),
        ("end_time", dt_time.fromisoformat, True),
    ):
        value = series[name]
        if value is None and nullable:
            continue
        if not isinstance(value, str):
            raise TypeError(f"Invalid extraction series {name}")
        parse_value(value)


# Broader than _CREATE_SIGNAL: next new verb should still look like a create.
_MAYBE_CREATE = re.compile(
    r"點|今日|今晚|今夜|聽日|聽朝|明天|加|活動|event|約|去|帶|同|"
    r"\bam\b|\bpm\b|\d{1,2}\s*[:：]|月|日|星期|禮拜|"
    r"book|schedule|appointment",
    re.IGNORECASE,
)
_WEATHER = re.compile(r"天氣|weather", re.IGNORECASE)


class LlmParser(Protocol):
    """Injectable create-fallback backend. Tests use ``FakeLlmParser``.

    ``correlation_id`` is diagnostics only: it lets the list mapper tie its
    per-item evidence to the Slack message. An implementation may ignore it.
    """

    model: str

    def complete_parse(
        self,
        message: str,
        *,
        now: datetime,
        rule_result: ParseResult,
        correlation_id: str | None = None,
    ) -> ParseResult: ...


class FakeLlmParser:
    """Test double — canned result or raise. Never talks to SpaceXAI."""

    model = "fake-llm"

    def __init__(
        self,
        result: ParseResult | None = None,
        *,
        fail_with: BaseException | None = None,
    ) -> None:
        self.result = result
        self.fail_with = fail_with
        self.calls: list[str] = []

    def complete_parse(
        self,
        message: str,
        *,
        now: datetime,
        rule_result: ParseResult,
        correlation_id: str | None = None,
    ) -> ParseResult:
        self.calls.append(message)
        if self.fail_with is not None:
            raise self.fail_with
        if self.result is None:
            raise RuntimeError("FakeLlmParser has no result")
        return self.result


class FakePayloadLlmParser:
    """Test double returning a canned provider JSON payload (v5 or v6 shape).

    Runs the same local validation and mapping as the live client, so tests
    exercise ``events[]`` handling without SpaceXAI.
    """

    model = "fake-llm-payload"

    def __init__(
        self,
        payload: object = None,
        *,
        fail_with: BaseException | None = None,
        truncated: bool = False,
    ) -> None:
        self.payload = payload
        self.fail_with = fail_with
        self.truncated = truncated
        self.calls: list[str] = []

    def complete_parse(
        self,
        message: str,
        *,
        now: datetime,
        rule_result: ParseResult,
        correlation_id: str | None = None,
    ) -> ParseResult:
        self.calls.append(message)
        if self.fail_with is not None:
            raise self.fail_with
        if self.truncated:
            return truncated_list_result(message)
        validate_create_payload(self.payload)
        assert isinstance(self.payload, dict)
        return llm_payload_to_parse_result(
            self.payload, raw_text=message, now=now, correlation_id=correlation_id
        )


def truncated_list_result(message: str) -> ParseResult:
    """Provider stopped at the token limit: ask to split, not "unavailable"."""
    return ParseResult(
        intent_type=IntentType.NEEDS_CLARIFICATION, title=None, start=None, end=None,
        all_day=False, location=None, raw_text=message, confidence=Confidence.MEDIUM,
        missing_fields=["events"], notes=f"llm_fallback;{NOTE_TOO_LONG}",
    )


def live_openai_kwargs(
    *,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, object]:
    """OpenAI client kwargs. max_retries=0 so timeout_s is one try, not ×3."""
    return {
        "api_key": api_key,
        "base_url": base_url,
        "timeout": timeout_s,
        "max_retries": max_retries,
    }


def require_llm_json_content(raw: object) -> str:
    """Reject empty assistant text. grok-4.5 can 'complete' with no JSON."""
    content = raw.strip() if isinstance(raw, str) else ""
    if not content:
        raise ValueError("empty LLM JSON")
    return content


def live_completion_extra(*, model: str) -> dict[str, object]:
    """Low reasoning on flagship SKUs. Non-reasoning models reject this param."""
    if model in _ALWAYS_REASONING_MODELS:
        return {"extra_body": {"reasoning_effort": "low"}}
    return {}


def order_live_models(configured: str, other: str) -> tuple[str, str | None]:
    """Non-reasoning SKU first when Mini still has LLM_MODEL=grok-4.5."""
    if not other or other == configured:
        return configured, None
    if configured in _ALWAYS_REASONING_MODELS and other not in _ALWAYS_REASONING_MODELS:
        return other, configured
    return configured, other


class XaiChatLlmParser:
    """Live SpaceXAI JSON completion. Not constructed by default pytest."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        prompt_version: str = DEFAULT_PROMPT_VERSION,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._base_url = base_url
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._prompt_key = prompt_version if prompt_version in PROMPT_PATHS else (
            DEFAULT_PROMPT_VERSION
        )

    def complete_parse(
        self,
        message: str,
        *,
        now: datetime,
        rule_result: ParseResult,
        correlation_id: str | None = None,
    ) -> ParseResult:
        from openai import OpenAI

        # One attempt per model. SDK default max_retries=2 made 15s into ~45s.
        client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout_s,
            max_retries=self._max_retries,
        )
        prompt_path = PROMPT_PATHS[self._prompt_key]
        is_v6 = self._prompt_key == "v6"
        system = prompt_path.read_text(encoding="utf-8")
        self.prompt_version = prompt_path.name
        self.prompt_hash = hashlib.sha256(system.encode("utf-8")).hexdigest()
        # A rule miss means unsupported wording, not necessarily absent facts.
        # Keep its verdict in diagnostics, not in the model's extraction input.
        user = json.dumps({"now": now.isoformat(), "message": message}, ensure_ascii=False)
        extra = live_completion_extra(model=self.model)
        extra_body = extra.get("extra_body")
        response = client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_schema", "json_schema": {
                "name": "calendar_create", "strict": True,
                "schema": CREATE_SCHEMA_V6 if is_v6 else CREATE_SCHEMA,
            }},
            max_tokens=MAX_COMPLETION_TOKENS_V6 if is_v6 else MAX_COMPLETION_TOKENS,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            extra_body=extra_body if isinstance(extra_body, dict) else None,
        )
        if is_v6 and getattr(response.choices[0], "finish_reason", None) == "length":
            # A cut-off list is not an outage; ask the family to split it.
            return truncated_list_result(message)
        content = require_llm_json_content(response.choices[0].message.content)
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        parsed = json.loads(content)
        validate_create_payload(parsed)
        result = llm_payload_to_parse_result(
            parsed, raw_text=message, now=now, correlation_id=correlation_id
        )
        result.notes = (
            f"{result.notes};prompt_tokens={prompt_tokens};"
            f"completion_tokens={completion_tokens}"
            if result.notes
            else f"llm_fallback;prompt_tokens={prompt_tokens};completion_tokens={completion_tokens}"
        )
        return result


def looks_like_create(message: str, rule_result: ParseResult) -> bool:
    """True when fallback is allowed. Weather/chat stays rules-unknown."""
    if contains_simplified_markers(message):
        return False
    if rule_result.intent_type not in (
        IntentType.UNKNOWN,
        IntentType.NEEDS_CLARIFICATION,
    ):
        return False
    notes = rule_result.notes or ""
    if (notes.startswith(("list_", "important_date_", "parser_error:", "delete_"))
            or notes in {"empty_message", "non_linguistic", "not_create_event"}):
        return False
    if _WEATHER.search(message):
        return False
    if rule_result.intent_type == IntentType.NEEDS_CLARIFICATION:
        return True
    return bool(_MAYBE_CREATE.search(message))


def parse_with_fallback(
    message: str,
    *,
    now: datetime | None = None,
    correlation_id: str | None = None,
    llm: LlmParser | None = None,
    llm_fallback: LlmParser | None = None,
    miss_store: ParseMissStore | None = None,
    rule_parse_fn: Callable[..., ParseResult] = rule_parse,
    llm_first: bool = False,
) -> ParseResult:
    """Rules first; optional LLM fill; always record create-looking misses.

    ``llm_fallback`` is one other model after ``llm`` raises (timeout / 5xx).
    Same model is not tried twice. SDK retries stay off on the live client.
    Listener uses llm_first=True: bypass create rules when a model is configured,
    preserve deterministic non-create routes, and fail visibly on model outage.
    """
    local = now or datetime.now(tz=FAMILY_TZ)
    if local.tzinfo is None:
        local = local.replace(tzinfo=FAMILY_TZ)
    else:
        local = local.astimezone(FAMILY_TZ)

    clients = _llm_chain(llm, llm_fallback)
    use_llm_first = llm_first and bool(clients)
    if use_llm_first:
        dated_lines = count_dated_lines(message)
        control = parse_control(message, now=local, dated_list=dated_lines >= 2)
        if control is not None:
            return control
        if dated_lines > MAX_BATCH_ITEMS:
            # Over the list cap: clarify before any model call; never truncate.
            logger.info(
                "batch_over_cap", component=COMPONENT, correlation_id=correlation_id,
                outcome="partial", dated_line_count=dated_lines, cap=MAX_BATCH_ITEMS,
            )
            return over_cap_result(message, count=dated_lines, notes_prefix="llm_first")
        # This is a neutral protocol context, not a create-rule prediction.
        rule_result = ParseResult(
            intent_type=IntentType.UNKNOWN, title=None, start=None, end=None,
            all_day=False, location=None, raw_text=message, notes="llm_first",
        )
    else:
        rule_result = rule_parse_fn(message, now=local, correlation_id=correlation_id)
        if not looks_like_create(message, rule_result):
            return rule_result

    llm_result: ParseResult | None = None
    llm_used = False
    for index, client in enumerate(clients):
        started = time.perf_counter()
        logger.info(
            "parse_fallback_attempt",
            component=COMPONENT,
            correlation_id=correlation_id,
            model=client.model,
            rule_intent=rule_result.intent_type.value,
            parse_mode="llm_first" if use_llm_first else "rules_first",
        )
        try:
            llm_result = client.complete_parse(
                message, now=local, rule_result=rule_result, correlation_id=correlation_id
            )
            llm_used = True
            llm_result = _sanitize_llm_result(llm_result, message=message)
            latency_ms = int((time.perf_counter() - started) * 1000)
            tokens = _tokens_from_notes(llm_result.notes)
            logger.info(
                "parse_fallback_succeeded",
                component=COMPONENT,
                correlation_id=correlation_id,
                outcome=("success" if llm_result.intent_type == IntentType.CREATE_EVENT
                         else "partial"),
                model=client.model,
                prompt_version=getattr(client, "prompt_version", None),
                prompt_hash=getattr(client, "prompt_hash", None),
                intent_type=llm_result.intent_type.value,
                missing_fields=list(llm_result.missing_fields),
                prompt_tokens=tokens[0],
                completion_tokens=tokens[1],
                latency_ms=latency_ms,
            )
            break
        except Exception as exc:  # noqa: BLE001 — fallback must not crash intake
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.error(
                "parse_fallback_failed",
                component=COMPONENT,
                correlation_id=correlation_id,
                outcome="failure",
                model=client.model,
                prompt_version=getattr(client, "prompt_version", None),
                prompt_hash=getattr(client, "prompt_hash", None),
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=latency_ms,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            llm_result = None
            llm_used = False
            nxt = clients[index + 1] if index + 1 < len(clients) else None
            if nxt is not None:
                logger.warning(
                    "parse_fallback_failover",
                    component=COMPONENT,
                    correlation_id=correlation_id,
                    outcome="partial",
                    from_model=client.model,
                    to_model=nxt.model,
                )

    if miss_store is not None and not use_llm_first:
        try:
            record_parse_miss(
                raw_text=message,
                rule_result=rule_result,
                store=miss_store,
                now=local,
                llm_result=llm_result,
                llm_used=llm_used,
                correlation_id=correlation_id,
            )
        except Exception as exc:  # noqa: BLE001 — diagnostics must not discard the parse
            logger.error(
                "parse_miss_record_failed", component=COMPONENT,
                correlation_id=correlation_id, outcome="failure",
                error_type=type(exc).__name__, error_message=str(exc),
            )

    if use_llm_first:
        if llm_result is not None:
            llm_result.notes = (llm_result.notes or "").replace("llm_fallback", "llm_first")
            return llm_result
        rule_result.intent_type = IntentType.NEEDS_CLARIFICATION
        rule_result.notes = "llm_unavailable"
        return rule_result

    if llm_result is not None and llm_result.intent_type == IntentType.CREATE_EVENT:
        return llm_result
    if llm_result is not None and llm_result.intent_type == IntentType.NEEDS_CLARIFICATION:
        return llm_result
    return rule_result


def llm_payload_to_parse_result(
    payload: Mapping[str, object],
    *,
    raw_text: str,
    now: datetime,
    correlation_id: str | None = None,
    participants_text: str | None = None,
) -> ParseResult:
    """Validate LLM JSON into ParseResult. Never writes calendar.

    ``participants_text`` is the text a model-named person must really appear
    in. It defaults to ``raw_text`` (the message is its own evidence); the
    Phase 33b image path passes the returned lines plus the caption, because
    「加event」 alone names nobody. A v6 ``events[]`` payload is routed on, so
    pass it to ``_events_payload_to_parse_result`` directly when the line
    evidence and the name evidence are not the same text.
    """
    if "events" in payload:
        return _events_payload_to_parse_result(
            payload, raw_text=raw_text, now=now, correlation_id=correlation_id,
            evidence_text=participants_text,
        )
    intent_raw = str(payload.get("intent_type") or "unknown")
    try:
        intent = IntentType(intent_raw)
    except ValueError:
        intent = IntentType.UNKNOWN
    if intent not in ALLOWED_INTENTS:
        intent = IntentType.UNKNOWN

    title = payload.get("title")
    title_s = str(title).strip() if isinstance(title, str) and title.strip() else None
    start = _parse_start(payload.get("start"), now=now)
    end = _parse_start(payload.get("end"), now=now)
    all_day_raw = payload.get("all_day", False)
    all_day = all_day_raw if isinstance(all_day_raw, bool) else False
    location = payload.get("location")
    location_s = (
        str(location).strip() if isinstance(location, str) and location.strip() else None
    )
    participants = _participants_from_payload(
        payload.get("participants"),
        raw_text if participants_text is None else participants_text,
    )
    missing_raw = payload.get("missing_fields") or []
    missing = [str(x) for x in missing_raw] if isinstance(missing_raw, list) else []
    series, series_note = _series_from_payload(payload.get("series"))
    if intent == IntentType.CREATE_EVENT and series is not None and start is None:
        # The local expander owns occurrence datetimes; a first start is derived.
        start = datetime.combine(series.range_start, series.start_time, tzinfo=FAMILY_TZ)

    if intent == IntentType.CREATE_EVENT:
        if not isinstance(all_day_raw, bool):
            missing.append("all_day")
        if end is not None and start is not None and end <= start:
            missing.append("end")
        if payload.get("end") is not None and end is None:
            missing.append("end")
        if missing:
            intent = IntentType.NEEDS_CLARIFICATION
        if not title_s:
            missing = list(dict.fromkeys([*missing, "title"]))
            intent = IntentType.NEEDS_CLARIFICATION
        if start is None:
            missing = list(dict.fromkeys([*missing, "start"]))
            intent = IntentType.NEEDS_CLARIFICATION

        if series_note is not None:
            missing = list(dict.fromkeys([*missing, "series_range"]))
            intent = IntentType.NEEDS_CLARIFICATION

    confidence = Confidence.MEDIUM
    return ParseResult(
        intent_type=intent,
        title=title_s,
        start=start,
        end=end,
        all_day=all_day,
        location=location_s,
        participants=participants,
        raw_text=raw_text,
        confidence=confidence,
        missing_fields=missing,
        notes=f"llm_fallback;{series_note}" if series_note else "llm_fallback",
        series=series if intent == IntentType.CREATE_EVENT else None,
    )


def _events_payload_to_parse_result(
    payload: Mapping[str, object],
    *,
    raw_text: str,
    now: datetime,
    correlation_id: str | None = None,
    evidence_text: str | None = None,
) -> ParseResult:
    """Route a v6 payload by item count (Phase 33a, ADR 0018).

    0 items → the v5 mapping of an empty event (unknown or clarify);
    1 item → exactly the v5 mapping (single create or Phase 27 series);
    2+ items, or a lone item while several lines name a day → one list.
    Over the cap → clarify; the list is never truncated.

    For a list the family's own lines are the evidence for each item's day and
    clock (``_date_from_line`` / ``_recover_clock``). The model owns the title,
    the grouping and the order; it does not get to drop a written clock, call a
    written clock all-day, or report a gap the same item fills.

    ``evidence_text`` (Phase 33b) names that evidence when the Slack message is
    not it: an image caption such as 「加event」 holds none of the lines, so the
    image path passes the lines the vision model returned instead
    (``image_extract.build_image_evidence``). ``raw_text`` stays the message
    for the reply, the preview and narrowing. None keeps the text path exactly
    as it is: the message is both.

    Phase 33c: the ``CEC_BATCH_SERIES`` switch is read once here and injected
    into ``build_batch_result``; the routing shape is unchanged (a lone series
    item still goes to the single Phase 27 path).
    """
    evidence = raw_text if evidence_text is None else evidence_text
    # A caption is family text: it can name a person that no image line names,
    # so it stays part of the name check. It is never line evidence, because a
    # caption line must not supply an image item's day or clock.
    people_text = evidence if evidence_text is None else f"{evidence}\n{raw_text}"
    intent = payload.get("intent_type")
    events = payload.get("events")
    items = [e for e in events if isinstance(e, Mapping)] if isinstance(events, list) else []
    if intent == IntentType.UNKNOWN.value:
        return llm_payload_to_parse_result(
            _v5_item({}, intent), raw_text=raw_text, now=now,
            correlation_id=correlation_id, participants_text=people_text,
        )
    if payload.get("over_cap") is True or len(items) > MAX_BATCH_ITEMS:
        return over_cap_result(raw_text, count=len(items) or None, notes_prefix="llm_fallback")
    if not items:
        return llm_payload_to_parse_result(
            _v5_item({}, intent), raw_text=raw_text, now=now,
            correlation_id=correlation_id, participants_text=people_text,
        )
    local_now = _local(now)
    lone = len(items) == 1
    if lone and (
        items[0].get("series") is not None
        or count_dated_lines(evidence) < 2
        or _has_series_cue(evidence)
    ):
        single = llm_payload_to_parse_result(
            _v5_item(items[0], intent), raw_text=raw_text, now=now,
            correlation_id=correlation_id, participants_text=people_text,
        )
        if evidence_text is not None and items[0].get("series") is None:
            # Phase 33b: one image item still returns its own line, so the same
            # local reading applies (PR #11/#12). Only a single written clock
            # can clear the time question; the Phase 21 self-report rule for a
            # typed single create is untouched (no evidence text there).
            found, _ = _read_line_evidence(
                single, items[0], evidence=evidence,
                source=verify_source_text(items[0].get("source_text"), evidence),
                fallback_line=None, now=local_now,
            )
            _log_item_evidence(
                [found], correlation_id=correlation_id,
                dated_line_count=count_dated_lines(evidence), aligned=False,
            )
        return single
    allow_series = _batch_series_enabled()
    lines = dated_lines(evidence)
    # v6 contract: one line or entry is one item, in message order. Equal
    # counts therefore make the position a safe last resort for finding an
    # item's own line; a merged or split list breaks the count and is skipped.
    aligned = len(lines) == len(items)
    extracted: list[ExtractedItem] = []
    flags: list[_ItemEvidence] = []
    for position, item in enumerate(items):
        mapped = llm_payload_to_parse_result(
            _v5_item(item, IntentType.CREATE_EVENT.value), raw_text=raw_text, now=now,
            participants_text=people_text,
        )
        _drop_contradicted_self_report(mapped)
        source = verify_source_text(item.get("source_text"), evidence)
        mapped.participants = _item_participants(item.get("participants"), people_text, source)
        weekday = item.get("stated_weekday")
        has_series = item.get("series") is not None
        spec, series_note = (
            _series_from_payload(item.get("series")) if has_series else (None, None)
        )
        if allow_series and has_series:
            # A repeating item's window comes from its spec, so the line
            # readers (dropped clock, missing day) must not touch it: the
            # expander owns every occurrence datetime (ADR 0011).
            found = _series_item_evidence(mapped, item, source=source)
            local_date = _item_date(item.get("date"))
        else:
            found, local_date = _read_line_evidence(
                mapped, item, evidence=evidence, source=source,
                fallback_line=lines[position] if aligned else None, now=local_now,
            )
        flags.append(found)
        extracted.append(ExtractedItem(
            result=mapped,
            stated_weekday=(SERIES_WEEKDAY_CODES.index(weekday)
                            if weekday in SERIES_WEEKDAY_CODES else None),
            source_text=source,
            local_date=local_date,
            has_series=has_series,
            series=spec,
            series_note=series_note,
        ))
    _log_item_evidence(
        flags, correlation_id=correlation_id,
        dated_line_count=len(lines), aligned=aligned,
    )
    return build_batch_result(
        extracted, message=raw_text, now=local_now, evidence=evidence,
        correlation_id=correlation_id, allow_series=allow_series,
    )


def _batch_series_enabled() -> bool:
    # Lazy like ``_has_series_cue``: the medic librarian imports this module
    # and must load no Calendar-path module.
    from cec_vivisystem.series import load_batch_series_enabled

    return load_batch_series_enabled()


def _series_item_evidence(
    result: ParseResult,
    item: Mapping[str, object],
    *,
    source: str | None,
) -> _ItemEvidence:
    """Flags for a repeating list item; nothing is read back from a line."""
    return _ItemEvidence(
        start_from_model=result.start is not None,
        all_day=bool(result.all_day),
        date_from_model=_item_date(item.get("date")) is not None,
        self_missing=_self_missing(item.get("missing_fields")),
        source_verified=source is not None,
        line_found=source is not None,
        has_start=result.start is not None,
    )


def _read_line_evidence(
    result: ParseResult,
    item: Mapping[str, object],
    *,
    evidence: str,
    source: str | None,
    fallback_line: str | None,
    now: datetime,
) -> tuple[_ItemEvidence, date | None]:
    """Read one item's own line into ``result`` (in place); return the flags.

    Contract: every character read here comes from ``evidence`` — the message
    for a typed list, the model's returned lines for an image. An item with no
    locatable line keeps the model's answer, so nothing is invented.
    """
    local_date = _item_date(item.get("date"))
    day = local_date or (_local(result.start).date() if result.start else None)
    line = source or (_line_for_date(evidence, day) if day else None) or fallback_line
    found = _ItemEvidence(
        start_from_model=result.start is not None,
        all_day=bool(result.all_day),
        date_from_model=local_date is not None,
        self_missing=_self_missing(item.get("missing_fields")),
        source_verified=source is not None,
        line_found=line is not None,
    )
    if day is None:
        # A dated line the model answered without a day: read the day it
        # writes, so a clock-less line asks for the time instead of
        # claiming the line has no date.
        day = _date_from_line(line, now=now)
        local_date = day
        found.date_recovered = day is not None
    if day is not None and (result.start is None or result.all_day):
        found.clock_recovered = _recover_clock(result, day, line)
    found.has_start = result.start is not None
    return found, local_date


# One flag token per item, in message order. Letters are evidence about the
# payload, never message content, so this stays class A (14 days).
_EVIDENCE_KEY = (
    "s=model start,a=model all-day,d=model date,m=self missing time,"
    "o=self missing other,t=source line verified,l=line located,"
    "+=clock read from line,#=date read from line,?=no start"
)
# Field names the extractor may report. Anything else is logged as "other" so
# a stray model string cannot put message content in the log.
_MISSING_VOCAB = frozenset({
    "start", "time", "title", "end", "all_day", "location", "participants",
    "series", "series_range", "events", "date", "details", "unconfirmed",
})
_TIME_GAP_NAMES = frozenset({"start", "time"})


@dataclass(slots=True)
class _ItemEvidence:
    """What one v6 item carried, and what was read back from its own line."""

    start_from_model: bool = False
    all_day: bool = False
    date_from_model: bool = False
    self_missing: tuple[str, ...] = ()
    source_verified: bool = False
    line_found: bool = False
    date_recovered: bool = False
    clock_recovered: bool = False
    has_start: bool = False

    @property
    def self_missing_time(self) -> bool:
        return any(name in _TIME_GAP_NAMES for name in self.self_missing)

    @property
    def self_missing_other(self) -> bool:
        return any(name not in _TIME_GAP_NAMES for name in self.self_missing)

    def code(self) -> str:
        """Compact flags for one item; see ``_EVIDENCE_KEY``."""
        flags = (
            ("s", self.start_from_model),
            ("a", self.all_day),
            ("d", self.date_from_model),
            ("m", self.self_missing_time),
            ("o", self.self_missing_other),
            ("t", self.source_verified),
            ("l", self.line_found),
            ("+", self.clock_recovered),
            ("#", self.date_recovered),
            ("?", not self.has_start),
        )
        return "".join(letter for letter, on in flags if on) or "-"


def _self_missing(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(value for value in raw if isinstance(value, str))


def _log_item_evidence(
    evidence: list[_ItemEvidence],
    *,
    correlation_id: str | None,
    dated_line_count: int,
    aligned: bool,
) -> None:
    """One content-free line that explains a list outcome item by item.

    Why: the live 2026-10-29 failure printed only ``missing_time:5``, which
    reads the same whether the model dropped the clock, called the line
    all-day, or reported a gap it had filled. The raw reply is never logged,
    so the next failure must be readable from flags and counts alone.
    """
    values = sorted({
        name if name in _MISSING_VOCAB else "other"
        for found in evidence for name in found.self_missing
    })
    no_start = sum(1 for found in evidence if not found.has_start)
    logger.info(
        "batch_item_evidence",
        component=COMPONENT,
        correlation_id=correlation_id,
        outcome="success" if not no_start else "partial",
        item_count=len(evidence),
        dated_line_count=dated_line_count,
        lines_aligned=aligned,
        start_from_model=sum(1 for found in evidence if found.start_from_model),
        all_day_from_model=sum(1 for found in evidence if found.all_day),
        date_from_model=sum(1 for found in evidence if found.date_from_model),
        self_missing_time=sum(1 for found in evidence if found.self_missing_time),
        self_missing_other=sum(1 for found in evidence if found.self_missing_other),
        self_missing_values=",".join(values) or None,
        source_verified=sum(1 for found in evidence if found.source_verified),
        clock_recovered=sum(1 for found in evidence if found.clock_recovered),
        date_recovered=sum(1 for found in evidence if found.date_recovered),
        no_start=no_start,
        item_flags=" ".join(
            f"{number}:{found.code()}" for number, found in enumerate(evidence, start=1)
        ),
        flags_key=_EVIDENCE_KEY,
    )


def _drop_contradicted_self_report(result: ParseResult) -> None:
    """List items only: forget a gap this item already fills (in place).

    Why: live 2026-10-29 the v6 reply could carry the written clock in
    ``start`` and still report ``missing_fields ["time"]``; ``event_batch``
    then refused every line as "no time". Inside a list the item's own line is
    the evidence, so the self-report loses. Contract: the single-event payload
    keeps the Phase 21 rule (a self-inconsistent create still asks), because
    nothing there re-reads one line; and a cleared item is still only a
    proposal that needs one explicit yes.
    """
    kept = _consistent_missing(
        result.missing_fields,
        title=result.title,
        start=result.start,
        all_day=result.all_day,
    )
    if kept == result.missing_fields:
        return
    result.missing_fields = kept
    if not kept and result.title and result.start is not None:
        result.intent_type = IntentType.CREATE_EVENT


def _consistent_missing(
    missing: list[str],
    *,
    title: str | None,
    start: datetime | None,
    all_day: bool,
) -> list[str]:
    """The reported gaps that the same fields do not already fill.

    Contract: the extracted fields are the evidence, the self-report is a
    hint. Only a gap the item itself fills is dropped, so an unknown name (for
    example "details") still asks. An all-day claim keeps the time question,
    because an all-day start is midnight, not a stated clock.
    """
    kept = [
        name for name in missing
        if not (name in _TIME_GAP_NAMES and start is not None and not all_day)
        and not (name == "title" and title)
    ]
    return list(dict.fromkeys(kept))


# One unambiguous 24-hour clock (HH:MM, full-width colon allowed) on an item's line.
_LINE_CLOCK = re.compile(r"(?<![\d:：])([01]?\d|2[0-3])[:：]([0-5]\d)(?![\d:：])")
_ZERO_WIDTH = re.compile("[\u200b-\u200d\u2060\ufeff]")
_ALL_DAY_WORDS = re.compile(r"全日|整日|成日|all[\s-]?day", re.IGNORECASE)
_PERIOD_CUE = re.compile(r"am|pm|a\.m\.|p\.m\.|上午|下午|朝|晏|晚|夜", re.IGNORECASE)


def _normalize_line(text: str) -> str:
    """NFKC (full-width digits/colon, NBSP → ASCII) and drop zero-width characters."""
    return _ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", text or ""))


def normalize_evidence_text(text: str) -> str:
    """One evidence line: NFKC, no zero-width characters, single spaces.

    Phase 33b turns each returned ``source_text`` into one line with this, so
    the local line readers (``_recover_clock``, ``_date_from_line``) read a
    model line exactly as they read a typed one.
    """
    return " ".join(_normalize_line(text).split())


def _line_for_date(text: str, day: date) -> str | None:
    """The single message line naming ``day`` as M月D日 or M/D, if exactly one."""
    pattern = re.compile(
        rf"(?<!\d){day.month}\s*月\s*{day.day}\s*[日號]|(?<!\d){day.month}/{day.day}(?![\d/])"
    )
    lines = [line for line in _normalize_line(text).splitlines() if pattern.search(line)]
    return lines[0] if len(lines) == 1 else None


def _item_date(raw: object) -> date | None:
    """The item's ``date`` field as a date, or None (never raises)."""
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


# Unambiguous written days only. A slash date (6/11) is day/month in Hong Kong
# writing and month/day elsewhere, so it is never guessed here: that item keeps
# asking instead of risking the wrong month.
_LINE_DATE_ISO = re.compile(r"(?<!\d)(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})(?!\d)")
_LINE_DATE_ZH = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日號]?(?!\d)")


def _date_from_line(line: str | None, *, now: datetime) -> date | None:
    """The day an item's own line writes, or None.

    Why: the model may answer a dated line with neither ``start`` nor
    ``date``; the item then read as "no date" for a line that writes one. A
    yearless day keeps the year of ``now`` and a passed day stays passed
    (ADR 0018: a list never moves a passed date to next year), so the family
    sees "date has passed" instead of a silent next-year event.
    """
    if not line:
        return None
    text = _normalize_line(line)
    if match := _LINE_DATE_ISO.search(text):
        year, month, day = (int(group) for group in match.groups())
    elif match := _LINE_DATE_ZH.search(text):
        year, month, day = now.year, int(match.group(1)), int(match.group(2))
    else:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _recover_clock(result: ParseResult, day: date, line: str | None) -> bool:
    """Fill a dropped start when the item's own line states exactly one clock.

    Why: live 2026-10-29, the v6 model returned no usable start for list lines
    such as 「11月6日（五）15:30 認知訓練」 (start null, or an all-day start), so every
    line became "no time". The clock is written in the user's line, so it is read
    locally (never invented). A second clock, a range, an AM/PM word or an
    explicit all-day cue keeps the model's answer / the time question.
    Returns True when a start was filled.
    """
    if not line:
        return False
    line = _normalize_line(line)
    if _PERIOD_CUE.search(line) or _ALL_DAY_WORDS.search(line):
        return False
    clocks = _LINE_CLOCK.findall(line)
    if len(clocks) != 1 or re.search(r"\d:\d\d\s*[-–~至到]", line):
        return False
    hour, minute = int(clocks[0][0]), int(clocks[0][1])
    result.start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=FAMILY_TZ)
    result.end = None
    result.all_day = False
    result.missing_fields = [f for f in result.missing_fields if f not in _TIME_GAP_NAMES]
    if not result.missing_fields and result.title:
        # The item is complete again; keep the intent honest for any reader.
        # Nothing is written: the list still needs one explicit yes.
        result.intent_type = IntentType.CREATE_EVENT
    return True


def _has_series_cue(text: str) -> bool:
    # Lazy: importing ``series`` at module load would pull Calendar-path
    # modules into the medic librarian, which reuses this module's client.
    from cec_vivisystem.series import has_series_cue

    return has_series_cue(text)


def _item_participants(raw: object, message: str, source_text: str | None) -> list[str]:
    """Per-item people: the model's names that the message really mentions.

    Unlike the single-event rule, names found elsewhere in the message are not
    added to every item; the item's own verified line may add its aliases.
    """
    from cec_vivisystem.parser import _extract_participants

    mentioned = set(_extract_participants(message))
    from_line = _extract_participants(source_text) if source_text else []
    names: list[str] = []
    candidates = [*from_line, *(raw if isinstance(raw, list) else [])]
    for name in candidates:
        if not isinstance(name, str) or name not in ALLOWED_PARTICIPANTS or name in names:
            continue
        if name in from_line or name in mentioned or name.casefold() in message.casefold():
            names.append(name)
    return names


def _local(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=FAMILY_TZ)
    return now.astimezone(FAMILY_TZ)


def _series_from_payload(raw: object) -> tuple[SeriesSpec | None, str | None]:
    """Map validated series JSON to ``SeriesSpec``; open-ended → clarify note."""
    if not isinstance(raw, dict):
        return None, None
    try:
        weekdays = tuple(sorted({SERIES_WEEKDAY_CODES.index(d) for d in raw["weekdays"]}))
        range_start = date.fromisoformat(raw["range_start"])
        start_time = dt_time.fromisoformat(raw["start_time"])
        end_raw = raw.get("end_time")
        end_time = dt_time.fromisoformat(end_raw) if end_raw else None
        if not raw.get("range_end"):
            return None, "series_open_ended"
        range_end = date.fromisoformat(raw["range_end"])
    except (KeyError, TypeError, ValueError):
        return None, "series_invalid"
    return SeriesSpec(
        weekdays=weekdays,
        range_start=range_start,
        range_end=range_end,
        start_time=start_time.replace(tzinfo=None),
        end_time=end_time.replace(tzinfo=None) if end_time else None,
    ), None


def load_live_llm_parsers(
    env: Mapping[str, str] | None = None,
) -> tuple[XaiChatLlmParser | None, XaiChatLlmParser | None]:
    """Primary + optional other-model parser. None if no ``XAI_API_KEY``."""
    source = env if env is not None else os.environ
    key = (source.get("XAI_API_KEY") or source.get("LLM_API_KEY") or "").strip()
    if not key:
        return None, None
    model = (source.get("LLM_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    base = (source.get("LLM_BASE_URL") or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    fallback_model = (
        source.get("LLM_FALLBACK_MODEL") or DEFAULT_FALLBACK_MODEL
    ).strip() or DEFAULT_FALLBACK_MODEL
    first_name, second_name = order_live_models(model, fallback_model)
    version = load_prompt_version(source)
    primary = XaiChatLlmParser(
        api_key=key, model=first_name, base_url=base, prompt_version=version
    )
    fallback: XaiChatLlmParser | None = None
    if second_name is not None:
        fallback = XaiChatLlmParser(
            api_key=key, model=second_name, base_url=base, prompt_version=version
        )
    return primary, fallback


def load_prompt_version(env: Mapping[str, str] | None = None) -> str:
    """``CEC_CREATE_PROMPT_VERSION`` (v7 default = Phase 33c list series; v6/v5 rollback).

    Why: v7 is the default per Carter's decision on 2026-10-30. Rolling back
    to v6 or v5 is a ``.env`` edit plus listener restart, not a code change.
    Unknown values warn and use the default.
    """
    source = env if env is not None else os.environ
    raw = (source.get("CEC_CREATE_PROMPT_VERSION") or "").strip().casefold()
    if not raw:
        return DEFAULT_PROMPT_VERSION
    if raw in PROMPT_PATHS:
        return raw
    logger.warning(
        "create_prompt_version_invalid", component=COMPONENT, outcome="partial",
        value=raw[:20], default=DEFAULT_PROMPT_VERSION,
    )
    return DEFAULT_PROMPT_VERSION


def load_live_llm_parser(
    env: Mapping[str, str] | None = None,
) -> XaiChatLlmParser | None:
    """Construct live parser if ``XAI_API_KEY`` (or ``LLM_API_KEY``) is set."""
    primary, _fallback = load_live_llm_parsers(env)
    return primary


def _llm_chain(
    primary: LlmParser | None, fallback: LlmParser | None
) -> list[LlmParser]:
    clients: list[LlmParser] = []
    if primary is not None:
        clients.append(primary)
    if fallback is not None and (
        primary is None or fallback.model != primary.model
    ):
        clients.append(fallback)
    return clients


def _sanitize_llm_result(result: ParseResult, *, message: str) -> ParseResult:
    if contains_simplified_markers((result.title or "") + (result.location or "")):
        result.intent_type = IntentType.UNKNOWN
    if result.intent_type not in ALLOWED_INTENTS:
        result.intent_type = IntentType.UNKNOWN
    allowed = _participants_from_payload(result.participants, message)
    result.participants = allowed
    if result.intent_type == IntentType.CREATE_EVENT:
        missing = list(result.missing_fields)
        if not result.title:
            missing.append("title")
            result.intent_type = IntentType.NEEDS_CLARIFICATION
        if result.start is None:
            missing.append("start")
            result.intent_type = IntentType.NEEDS_CLARIFICATION
        if not isinstance(result.all_day, bool):
            missing.append("all_day")
        if result.start is not None and result.end is not None and result.end <= result.start:
            missing.append("end")
        if missing:
            result.intent_type = IntentType.NEEDS_CLARIFICATION
        result.missing_fields = list(dict.fromkeys(missing))
    if not result.notes:
        result.notes = "llm_fallback"
    elif "llm_fallback" not in result.notes:
        result.notes = f"llm_fallback;{result.notes}"
    result.confidence = Confidence.MEDIUM
    result.raw_text = message
    return result


def _participants_from_payload(raw: object, message: str) -> list[str]:
    from cec_vivisystem.parser import _extract_participants

    from_text = _extract_participants(message)
    named: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, str):
                continue
            if item in ALLOWED_PARTICIPANTS:
                named.append(item)
    combined: list[str] = []
    seen: set[str] = set()
    for name in [*from_text, *named]:
        if name in seen:
            continue
        if name not in ALLOWED_PARTICIPANTS:
            continue
        # LLM may only add a name that appears in the message or via alias extract.
        if name not in from_text and name.casefold() not in message.casefold():
            continue
        seen.add(name)
        combined.append(name)
    return combined


def _parse_start(raw: object, *, now: datetime) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            dt = datetime.fromisoformat(raw.strip())
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=FAMILY_TZ)
    return dt.astimezone(FAMILY_TZ)


def _tokens_from_notes(notes: str | None) -> tuple[int, int]:
    if not notes:
        return 0, 0
    prompt = 0
    completion = 0
    if match := re.search(r"prompt_tokens=(\d+)", notes):
        prompt = int(match.group(1))
    if match := re.search(r"completion_tokens=(\d+)", notes):
        completion = int(match.group(1))
    return prompt, completion
