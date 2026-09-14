"""Hybrid parse fallback (Phase 18, ADR 0005).

Rules first. If a create-looking line is unknown / needs_clarification, an
injectable LLM may fill ``ParseResult``. Confirmation **yes** still gates Google.

Why: Elaine should not be the test suite. Miss rows still feed the next
rule phase via ``parse_misses``.

Do not call this from default ``parse()``. Listener / CLI opt in.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from cec_vivisystem.logging import get_logger
from cec_vivisystem.models import Confidence, IntentType, ParseResult
from cec_vivisystem.parse_misses import ParseMissStore, record_parse_miss
from cec_vivisystem.parser import parse as rule_parse

logger = get_logger(__name__)

COMPONENT = "parse_fallback"
FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
DEFAULT_MODEL = "grok-4.5"
DEFAULT_BASE_URL = "https://api.x.ai/v1"
PROMPT_PATH = (
    Path(__file__).resolve().parent / "prompts" / "create_fallback.v1.txt"
)
ALLOWED_INTENTS = frozenset(
    {
        IntentType.CREATE_EVENT,
        IntentType.NEEDS_CLARIFICATION,
        IntentType.UNKNOWN,
    }
)
ALLOWED_PARTICIPANTS = frozenset({"Cedric", "Coco", "Elaine", "Carter"})
# Broader than _CREATE_SIGNAL: next new verb should still look like a create.
_MAYBE_CREATE = re.compile(
    r"點|点|今日|今晚|今夜|聽日|聽朝|明天|加|活動|event|約|去|帶|同|"
    r"\bam\b|\bpm\b|\d{1,2}\s*[:：]|月|日|星期|禮拜|"
    r"book|schedule|appointment",
    re.IGNORECASE,
)
_WEATHER = re.compile(r"天氣|weather", re.IGNORECASE)


class LlmParser(Protocol):
    """Injectable create-fallback backend. Tests use ``FakeLlmParser``."""

    model: str

    def complete_parse(
        self,
        message: str,
        *,
        now: datetime,
        rule_result: ParseResult,
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
    ) -> ParseResult:
        self.calls.append(message)
        if self.fail_with is not None:
            raise self.fail_with
        if self.result is None:
            raise RuntimeError("FakeLlmParser has no result")
        return self.result


class XaiChatLlmParser:
    """Live SpaceXAI JSON completion. Not constructed by default pytest."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = 15.0,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._base_url = base_url
        self._timeout_s = timeout_s

    def complete_parse(
        self,
        message: str,
        *,
        now: datetime,
        rule_result: ParseResult,
    ) -> ParseResult:
        from openai import OpenAI

        client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout_s,
        )
        system = PROMPT_PATH.read_text(encoding="utf-8")
        user = (
            f"now={now.isoformat()}\n"
            f"rule_intent={rule_result.intent_type.value}\n"
            f"rule_missing={list(rule_result.missing_fields)}\n"
            f"message={message}"
        )
        response = client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = (response.choices[0].message.content or "").strip()
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise TypeError("LLM JSON was not an object")
        result = llm_payload_to_parse_result(parsed, raw_text=message, now=now)
        result.notes = (
            f"{result.notes};prompt_tokens={prompt_tokens};"
            f"completion_tokens={completion_tokens}"
            if result.notes
            else f"llm_fallback;prompt_tokens={prompt_tokens};completion_tokens={completion_tokens}"
        )
        return result


def looks_like_create(message: str, rule_result: ParseResult) -> bool:
    """True when fallback is allowed. Weather/chat stays rules-unknown."""
    if rule_result.intent_type not in (
        IntentType.UNKNOWN,
        IntentType.NEEDS_CLARIFICATION,
    ):
        return False
    notes = rule_result.notes or ""
    if notes in {"empty_message", "non_linguistic", "not_create_event"}:
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
    miss_store: ParseMissStore | None = None,
    rule_parse_fn: Callable[..., ParseResult] = rule_parse,
) -> ParseResult:
    """Rules first; optional LLM fill; always record create-looking misses."""
    local = now or datetime.now(tz=FAMILY_TZ)
    if local.tzinfo is None:
        local = local.replace(tzinfo=FAMILY_TZ)
    else:
        local = local.astimezone(FAMILY_TZ)

    rule_result = rule_parse_fn(message, now=local, correlation_id=correlation_id)
    if not looks_like_create(message, rule_result):
        return rule_result

    llm_result: ParseResult | None = None
    llm_used = False
    if llm is not None:
        started = time.perf_counter()
        logger.info(
            "parse_fallback_attempt",
            component=COMPONENT,
            correlation_id=correlation_id,
            model=llm.model,
            rule_intent=rule_result.intent_type.value,
        )
        try:
            llm_result = llm.complete_parse(
                message, now=local, rule_result=rule_result
            )
            llm_used = True
            llm_result = _sanitize_llm_result(llm_result, message=message)
            latency_ms = int((time.perf_counter() - started) * 1000)
            tokens = _tokens_from_notes(llm_result.notes)
            logger.info(
                "parse_fallback_succeeded",
                component=COMPONENT,
                correlation_id=correlation_id,
                outcome="success",
                model=llm.model,
                intent_type=llm_result.intent_type.value,
                prompt_tokens=tokens[0],
                completion_tokens=tokens[1],
                latency_ms=latency_ms,
            )
        except Exception as exc:  # noqa: BLE001 — fallback must not crash intake
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.error(
                "parse_fallback_failed",
                component=COMPONENT,
                correlation_id=correlation_id,
                outcome="failure",
                model=llm.model,
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=latency_ms,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            llm_result = None
            llm_used = False

    if miss_store is not None:
        record_parse_miss(
            raw_text=message,
            rule_result=rule_result,
            store=miss_store,
            now=local,
            llm_result=llm_result,
            llm_used=llm_used,
            correlation_id=correlation_id,
        )

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
) -> ParseResult:
    """Validate LLM JSON into ParseResult. Never writes calendar."""
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
    all_day = bool(payload.get("all_day"))
    location = payload.get("location")
    location_s = (
        str(location).strip() if isinstance(location, str) and location.strip() else None
    )
    participants = _participants_from_payload(payload.get("participants"), raw_text)
    missing_raw = payload.get("missing_fields") or []
    missing = [str(x) for x in missing_raw] if isinstance(missing_raw, list) else []

    if intent == IntentType.CREATE_EVENT:
        if not title_s:
            missing = list(dict.fromkeys([*missing, "title"]))
            intent = IntentType.NEEDS_CLARIFICATION
        if start is None and not all_day:
            missing = list(dict.fromkeys([*missing, "start"]))
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
        notes="llm_fallback",
    )


def load_live_llm_parser(
    env: Mapping[str, str] | None = None,
) -> XaiChatLlmParser | None:
    """Construct live parser if ``XAI_API_KEY`` (or ``LLM_API_KEY``) is set."""
    source = env if env is not None else os.environ
    key = (source.get("XAI_API_KEY") or source.get("LLM_API_KEY") or "").strip()
    if not key:
        return None
    model = (source.get("LLM_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    base = (source.get("LLM_BASE_URL") or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    return XaiChatLlmParser(api_key=key, model=model, base_url=base)


def _sanitize_llm_result(result: ParseResult, *, message: str) -> ParseResult:
    if result.intent_type not in ALLOWED_INTENTS:
        result.intent_type = IntentType.UNKNOWN
    allowed = _participants_from_payload(result.participants, message)
    result.participants = allowed
    if result.intent_type == IntentType.CREATE_EVENT:
        missing = list(result.missing_fields)
        if not result.title:
            missing.append("title")
            result.intent_type = IntentType.NEEDS_CLARIFICATION
        if result.start is None and not result.all_day:
            missing.append("start")
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
