"""Phase 33b vision extract: image(s) + caption → Phase 33a ``events[]``.

Behind ``ImageEventExtractor`` so a Mini-OCR path could replace B1 later without
touching the confirmation path. Default kill switch keeps this unused.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Protocol

from cec_vivisystem.image_intake import PreparedImage
from cec_vivisystem.logging import get_logger
from cec_vivisystem.models import Confidence, IntentType, ParseResult
from cec_vivisystem.parse_fallback import (
    CREATE_SCHEMA_V6,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_S,
    MAX_COMPLETION_TOKENS_V6,
    _events_payload_to_parse_result,
    live_completion_extra,
    order_live_models,
    require_llm_json_content,
    validate_create_payload,
)

COMPONENT = "image_extract"
logger = get_logger(__name__)
_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "create_event_image.v1.txt"
DEFAULT_VISION_MODEL = "grok-4.20-0309-non-reasoning"


class ImageEventExtractor(Protocol):
    def extract(
        self,
        images: Sequence[PreparedImage],
        *,
        caption: str,
        now: datetime,
        correlation_id: str | None = None,
    ) -> ParseResult: ...


class FakeImageEventExtractor:
    """Test double returning a canned ``ParseResult``."""

    def __init__(self, result: ParseResult) -> None:
        self.result = result
        self.calls: list[tuple[int, str]] = []

    def extract(
        self,
        images: Sequence[PreparedImage],
        *,
        caption: str,
        now: datetime,
        correlation_id: str | None = None,
    ) -> ParseResult:
        self.calls.append((len(images), caption))
        return self.result


class XaiVisionEventExtractor:
    """xAI Chat Completions with image_url data URLs and the v6 events schema."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_VISION_MODEL,
        fallback_model: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self.fallback_model = fallback_model
        self._base_url = base_url
        self._timeout_s = timeout_s
        system = _PROMPT_PATH.read_text(encoding="utf-8")
        self.prompt_version = _PROMPT_PATH.name
        self.prompt_hash = hashlib.sha256(system.encode("utf-8")).hexdigest()
        self._system = system

    def extract(
        self,
        images: Sequence[PreparedImage],
        *,
        caption: str,
        now: datetime,
        correlation_id: str | None = None,
    ) -> ParseResult:
        models = [self.model]
        if self.fallback_model and self.fallback_model != self.model:
            models.append(self.fallback_model)
        last_error: Exception | None = None
        for name in models:
            try:
                return self._call(
                    name, images, caption=caption, now=now, correlation_id=correlation_id
                )
            except Exception as exc:  # noqa: BLE001 — try fallback once
                last_error = exc
                logger.warning(
                    "image_extract_model_failed",
                    component=COMPONENT,
                    outcome="failure",
                    correlation_id=correlation_id,
                    model=name,
                    error_type=type(exc).__name__,
                )
        assert last_error is not None
        raise last_error

    def _call(
        self,
        model: str,
        images: Sequence[PreparedImage],
        *,
        caption: str,
        now: datetime,
        correlation_id: str | None,
    ) -> ParseResult:
        from openai import OpenAI

        client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout_s,
            max_retries=0,
        )
        user_text = json.dumps(
            {"now": now.isoformat(), "caption": caption}, ensure_ascii=False
        )
        content: list[dict] = [{"type": "text", "text": user_text}]
        for image in images:
            b64 = base64.b64encode(image.data).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image.mimetype};base64,{b64}",
                    "detail": "high",
                },
            })
        extra = live_completion_extra(model=model)
        started = time.perf_counter()
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            response_format={"type": "json_schema", "json_schema": {
                "name": "calendar_create", "strict": True, "schema": CREATE_SCHEMA_V6,
            }},
            max_tokens=MAX_COMPLETION_TOKENS_V6,
            messages=[
                {"role": "system", "content": self._system},
                {"role": "user", "content": content},
            ],
            extra_body=extra.get("extra_body") if isinstance(extra.get("extra_body"), dict) else None,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        if getattr(response.choices[0], "finish_reason", None) == "length":
            from cec_vivisystem.parse_fallback import truncated_list_result
            return truncated_list_result(caption or "(image)")
        content_text = require_llm_json_content(response.choices[0].message.content)
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        parsed = json.loads(content_text)
        validate_create_payload(parsed)
        if parsed.get("events") == [] and parsed.get("over_cap") is not True:
            # Nothing was read, whatever the intent: give the image reply, not
            # the text "need more detail (missing: title, start)" copy.
            result = empty_image_result(caption)
        else:
            result = _events_payload_to_parse_result(
                parsed, raw_text=caption or "(image)", now=now, correlation_id=correlation_id
            )
        if result.batch is not None and images:
            # One image → that index on every item; several → leave None (v1).
            idx = images[0].source_index if len(images) == 1 else None
            if idx is not None:
                result.batch = tuple(replace(item, image_index=idx) for item in result.batch)
        # Same label as the text LLM-first path (parse_with_fallback): the
        # "llm_fallback" token adds the rules-fallback "less common wording"
        # line to a single proposal, which is wrong for an image.
        notes = (result.notes or "image_extract").replace("llm_fallback", "llm_first")
        result.notes = (
            f"{notes};from_image;prompt_tokens={prompt_tokens};"
            f"completion_tokens={completion_tokens}"
        )
        logger.info(
            "image_extract_completed",
            component=COMPONENT,
            outcome="success" if result.intent_type == IntentType.CREATE_EVENT else "partial",
            correlation_id=correlation_id,
            model=model,
            prompt_version=self.prompt_version,
            prompt_hash=self.prompt_hash,
            image_count=len(images),
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            intent_type=result.intent_type.value,
            item_count=len(result.batch or ()),
        )
        return result


def load_image_extractor(
    env: Mapping[str, str] | None = None,
) -> ImageEventExtractor | None:
    """Return a live extractor when the kill switch is ``xai`` and a key is set."""
    from cec_vivisystem.image_intake import load_image_extract_mode

    source = env if env is not None else os.environ
    if load_image_extract_mode(source) != "xai":
        return None
    key = (source.get("XAI_API_KEY") or source.get("LLM_API_KEY") or "").strip()
    if not key:
        return None
    model = (source.get("LLM_VISION_MODEL") or DEFAULT_VISION_MODEL).strip() or DEFAULT_VISION_MODEL
    fallback_raw = (source.get("LLM_VISION_FALLBACK_MODEL") or "").strip()
    configured = (source.get("LLM_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    other = fallback_raw or (source.get("LLM_FALLBACK_MODEL") or "").strip()
    first, second = order_live_models(model, other or configured)
    # Prefer the vision-configured model as primary.
    primary = model
    fallback = second if second and second != primary else (
        first if first != primary else None
    )
    base = (source.get("LLM_BASE_URL") or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    return XaiVisionEventExtractor(
        api_key=key, model=primary, fallback_model=fallback, base_url=base
    )


def empty_image_result(caption: str) -> ParseResult:
    """No dated events found in the image(s)."""
    return ParseResult(
        intent_type=IntentType.UNKNOWN,
        title=None, start=None, end=None, all_day=False, location=None,
        raw_text=caption, confidence=Confidence.MEDIUM, missing_fields=[],
        notes="image_no_events",
    )
