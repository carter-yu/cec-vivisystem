"""Model call and answer validation for medic Q&A (Phase 32, ADR 0017).

Why: the model only words an answer from the redacted context pack. It cannot
choose sources outside that pack, and every figure it writes must appear in
the pack, so a hallucinated limit becomes a visible fallback, not a reply.

Contract: same model policy as ``parse_fallback`` (primary then one other
model, 15 s each, SDK retries off, non-reasoning SKU first). ``ask_models``
returns a validated ``MedicAnswer`` or ``None`` plus the reason; the caller
replies from the tree on ``None``. Tests use ``FakeMedicLlm``; pytest never
constructs the live client against the network.

Do not pass raw user text or tree text that skipped ``redact_sensitive``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cec_vivisystem.logging import get_logger
from cec_vivisystem.parse_fallback import (
    DEFAULT_BASE_URL,
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_S,
    live_completion_extra,
    order_live_models,
    require_llm_json_content,
)
from cec_vivisystem.parser import contains_simplified_markers
from cec_vivisystem.redaction import redact_sensitive

logger = get_logger(__name__)

COMPONENT = "medic_librarian"
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "medic_answer.v1.txt"
MAX_COMPLETION_TOKENS = 900
MAX_ANSWER_CHARS = 1500
STATUSES = ("answered", "not_in_document")
ANSWER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": list(STATUSES)},
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "answer", "citations"],
}
_FIGURE = re.compile(r"(\$\s?)?(\d[\d,]*(?:\.\d+)?)(\s?%)?")
# Distinctive Simplified forms in insurance wording (rejection set only, never a
# translation table). The parser's set covers scheduling words.
_SIMPLIFIED_ANSWER_MARKERS = frozenset(
    "\u5c5e\u9669\u8d54\u507f\u989d\u8d39\u95e8\u8bca\u7597\u836f"
    "\u68c0\u62a4\u7f51\u7edc\u8fd9\u4eec\u4e3a\u4e2a\u533b\u52a1"
    "\u5355\u62a5\u9500\u7ed9\u8ba9\u8bf4\u8bdd\u5bf9\u6ca1\u8fc7"
    "\u5b9e\u9645\u5173\u5e94\u8be5\u9009\u5242"
)


@dataclass(slots=True)
class MedicAnswer:
    status: str
    answer: str
    citations: list[str]
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


class MedicLlm(Protocol):
    """Injectable answer backend. Tests use ``FakeMedicLlm``."""

    model: str

    def answer(
        self, question: str, *, context: Sequence[Mapping[str, object]], language: str
    ) -> MedicAnswer: ...


class FakeMedicLlm:
    """Test double: canned answer or raise. Records what it was given."""

    def __init__(
        self,
        result: MedicAnswer | None = None,
        *,
        fail_with: BaseException | None = None,
        model: str = "fake-medic",
    ) -> None:
        self.result = result
        self.fail_with = fail_with
        self.model = model
        self.calls: list[dict[str, object]] = []

    def answer(
        self, question: str, *, context: Sequence[Mapping[str, object]], language: str
    ) -> MedicAnswer:
        self.calls.append({"question": question, "context": list(context), "language": language})
        if self.fail_with is not None:
            raise self.fail_with
        if self.result is None:
            raise RuntimeError("FakeMedicLlm has no result")
        return self.result


class XaiMedicLlm:
    """Live xAI JSON completion. Not constructed against the network in pytest."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        client_factory=None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._base_url = base_url
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._client_factory = client_factory
        self.prompt_version = PROMPT_PATH.name
        self.prompt_hash: str | None = None

    def _client(self):
        if self._client_factory is not None:
            return self._client_factory()
        from openai import OpenAI

        # One attempt per model: SDK default retries would triple the wait.
        return OpenAI(api_key=self._api_key, base_url=self._base_url,
                      timeout=self._timeout_s, max_retries=self._max_retries)

    def answer(
        self, question: str, *, context: Sequence[Mapping[str, object]], language: str
    ) -> MedicAnswer:
        system = PROMPT_PATH.read_text(encoding="utf-8")
        self.prompt_hash = hashlib.sha256(system.encode("utf-8")).hexdigest()
        user = json.dumps(
            {"question": question, "reply_language": _language_tag(language),
             "context": list(context)},
            ensure_ascii=False,
        )
        extra_body = live_completion_extra(model=self.model).get("extra_body")
        response = self._client().chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_schema", "json_schema": {
                "name": "medic_answer", "strict": True, "schema": ANSWER_SCHEMA,
            }},
            max_tokens=MAX_COMPLETION_TOKENS,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            extra_body=extra_body if isinstance(extra_body, dict) else None,
        )
        payload = json.loads(require_llm_json_content(response.choices[0].message.content))
        validate_answer_payload(payload)
        usage = getattr(response, "usage", None)
        return MedicAnswer(
            status=payload["status"], answer=payload["answer"].strip(),
            citations=list(payload["citations"]), model=self.model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )


def _language_tag(language: str) -> str:
    return "zh-Hant-HK" if language == "zh" else "en"


def validate_answer_payload(payload: object) -> None:
    """Reject a broken provider contract even without schema enforcement."""
    if not isinstance(payload, dict) or set(payload) != {"status", "answer", "citations"}:
        raise ValueError("Invalid answer fields")
    if payload["status"] not in STATUSES:
        raise ValueError("Invalid answer status")
    if not isinstance(payload["answer"], str):
        raise TypeError("Invalid answer text")
    citations = payload["citations"]
    if not isinstance(citations, list) or any(not isinstance(c, str) for c in citations):
        raise TypeError("Invalid answer citations")


def check_answer(
    answer: MedicAnswer, context: Sequence[Mapping[str, object]]
) -> str | None:
    """Return a rejection reason, or None when the answer may be shown.

    Rules: citations only from the pack (at least one); bounded length; no
    Simplified Chinese; every amount, percentage or 3+ digit number must
    appear in the pack text. Small numbers (list markers, "1 visit") pass.
    """
    if answer.status == "not_in_document":
        return None
    allowed = {str(entry["id"]) for entry in context}
    if not answer.citations:
        return "no_citation"
    if any(c not in allowed for c in answer.citations):
        return "unknown_citation"
    text = answer.answer.strip()
    if not text:
        return "empty_answer"
    if len(text) > MAX_ANSWER_CHARS:
        return "too_long"
    if contains_simplified_markers(text) or _SIMPLIFIED_ANSWER_MARKERS.intersection(text):
        return "simplified_chinese"
    known = _figures(" ".join(f"{e.get('title', '')} {e.get('text', '')}" for e in context))
    for match in _FIGURE.finditer(text):
        number = match.group(2).replace(",", "").rstrip(".")
        significant = bool(match.group(1) or match.group(3)) or len(number.split(".")[0]) >= 3
        if significant and number not in known:
            return "unverified_figure"
    return None


def _figures(text: str) -> set[str]:
    return {m.group(2).replace(",", "").rstrip(".") for m in _FIGURE.finditer(text)}


def ask_models(
    question: str,
    *,
    context: Sequence[Mapping[str, object]],
    language: str,
    llm: MedicLlm | None,
    llm_fallback: MedicLlm | None = None,
    correlation_id: str | None = None,
) -> tuple[MedicAnswer | None, str]:
    """Primary, then one other model on error. Returns (answer, outcome reason).

    Reasons: ``ok``, ``not_in_document``, ``llm_unconfigured``, ``llm_failed``
    or a ``check_answer`` rejection. Only error types and redacted messages are
    logged; never the question, context or answer text.
    """
    clients = [c for c in (llm, llm_fallback) if c is not None]
    if len(clients) == 2 and clients[0].model == clients[1].model:
        clients = clients[:1]
    if not clients:
        return None, "llm_unconfigured"
    for index, client in enumerate(clients):
        started = time.perf_counter()
        logger.info("medic_answer_attempt", component=COMPONENT, correlation_id=correlation_id,
                    model=client.model, context_entries=len(context), language=language)
        try:
            result = client.answer(question, context=context, language=language)
        except Exception as exc:  # noqa: BLE001 — a model outage must not crash intake
            safe_message, _ = redact_sensitive(str(exc)[:200])
            logger.error("medic_answer_failed", component=COMPONENT, correlation_id=correlation_id,
                         outcome="failure", model=client.model,
                         latency_ms=int((time.perf_counter() - started) * 1000),
                         error_type=type(exc).__name__, error_message=safe_message)
            if index + 1 < len(clients):
                logger.warning("medic_answer_failover", component=COMPONENT,
                               correlation_id=correlation_id, outcome="partial",
                               from_model=client.model, to_model=clients[index + 1].model)
            continue
        latency_ms = int((time.perf_counter() - started) * 1000)
        reason = check_answer(result, context)
        logger.info("medic_answer_succeeded" if reason is None else "medic_answer_rejected",
                    component=COMPONENT, correlation_id=correlation_id,
                    outcome="success" if reason is None else "failure",
                    model=client.model, status=result.status, reason=reason,
                    citations=len(result.citations), latency_ms=latency_ms,
                    prompt_version=getattr(client, "prompt_version", None),
                    prompt_hash=getattr(client, "prompt_hash", None),
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens)
        if reason is not None:
            return None, reason
        return result, "ok" if result.status == "answered" else "not_in_document"
    return None, "llm_failed"


def load_live_medic_llms(
    env: Mapping[str, str] | None = None,
) -> tuple[XaiMedicLlm | None, XaiMedicLlm | None]:
    """Primary + other model from the same keys as ``parse_fallback``; None without a key."""
    source = env if env is not None else os.environ
    key = (source.get("XAI_API_KEY") or source.get("LLM_API_KEY") or "").strip()
    if not key:
        return None, None
    model = (source.get("LLM_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    base = (source.get("LLM_BASE_URL") or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    other = (source.get("LLM_FALLBACK_MODEL") or DEFAULT_FALLBACK_MODEL).strip() \
        or DEFAULT_FALLBACK_MODEL
    first, second = order_live_models(model, other)
    primary = XaiMedicLlm(api_key=key, model=first, base_url=base)
    fallback = XaiMedicLlm(api_key=key, model=second, base_url=base) if second else None
    return primary, fallback
