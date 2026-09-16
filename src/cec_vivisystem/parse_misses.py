"""Rule-miss store (Phase 18) — promote repeated tokens into parser rules.

Records create-looking lines that rules returned as unknown / needs_clarification.
Not a calendar write. Not an LLM. Class C JSON under ``data/parse_misses/``.

CLI: ``uv run python -c "from cec_vivisystem.parse_misses import main; main()"``
"""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from cec_vivisystem.logging import get_logger
from cec_vivisystem.models import IntentType, ParseResult

logger = get_logger(__name__)

COMPONENT = "parse_misses"
FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
MISS_RETENTION = timedelta(days=90)
KNOWN_SKIP = frozenset(
    {
        "cedric",
        "elaine",
        "carter",
        "coco",
        "梓梵",
        "椰子糖",
        "糖糖",
        "今日",
        "今晚",
        "聽日",
        "聽朝",
        "明天",
        "下午",
        "上午",
        "晚上",
        "夜晚",
        "上晝",
        "下晝",
        "活動",
        "event",
        "yes",
        "help",
        "游泳",
        "游水",
        "公園",
        "牙醫",
        "返學",
        "the",
        "and",
        "for",
        "with",
    }
)
_LATIN = re.compile(r"[A-Za-z]{2,}")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")


@dataclass(slots=True)
class ParseMiss:
    """One rule-miss row (create-looking unknown / clarification)."""

    miss_id: str
    recorded_at: datetime
    raw_text: str
    rule_intent: str
    rule_missing: list[str]
    llm_intent: str | None
    llm_used: bool
    tokens: list[str]
    correlation_id: str | None = None


class ParseMissStore(Protocol):
    def save(self, miss: ParseMiss) -> None: ...

    def list_all(self) -> list[ParseMiss]: ...

    def delete(self, miss_id: str) -> None: ...


class InMemoryParseMissStore:
    def __init__(self) -> None:
        self._items: dict[str, ParseMiss] = {}

    def save(self, miss: ParseMiss) -> None:
        self._items[miss.miss_id] = miss

    def list_all(self) -> list[ParseMiss]:
        return list(self._items.values())

    def delete(self, miss_id: str) -> None:
        self._items.pop(miss_id, None)


class JsonDirParseMissStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, miss_id: str) -> Path:
        return self.root / f"{miss_id.replace('/', '_')}.json"

    def save(self, miss: ParseMiss) -> None:
        path = self._path(miss.miss_id)
        payload = {
            "miss_id": miss.miss_id,
            "recorded_at": miss.recorded_at.isoformat(),
            "raw_text": miss.raw_text,
            "rule_intent": miss.rule_intent,
            "rule_missing": miss.rule_missing,
            "llm_intent": miss.llm_intent,
            "llm_used": miss.llm_used,
            "tokens": miss.tokens,
            "correlation_id": miss.correlation_id,
        }
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error(
                "parse_miss_save_failed",
                component=COMPONENT,
                outcome="failure",
                miss_id=miss.miss_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise

    def list_all(self) -> list[ParseMiss]:
        items: list[ParseMiss] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                items.append(_miss_from_dict(data))
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                logger.error(
                    "parse_miss_load_failed",
                    component=COMPONENT,
                    outcome="failure",
                    path=str(path),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
        return items

    def delete(self, miss_id: str) -> None:
        try:
            self._path(miss_id).unlink(missing_ok=True)
        except OSError as exc:
            logger.error(
                "parse_miss_delete_failed",
                component=COMPONENT,
                outcome="failure",
                miss_id=miss_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise


def default_data_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "parse_misses"


def extract_miss_tokens(text: str) -> list[str]:
    """Surface tokens for keyword promotion. Not a parser."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _LATIN.finditer(text):
        token = match.group(0)
        key = token.casefold()
        if key in KNOWN_SKIP or key in seen:
            continue
        seen.add(key)
        found.append(token)
    for match in _CJK_RUN.finditer(text):
        run = match.group(0)
        for n in (2, 3, 4):
            if len(run) < n:
                continue
            for i in range(len(run) - n + 1):
                token = run[i : i + n]
                if token in KNOWN_SKIP or token in seen:
                    continue
                seen.add(token)
                found.append(token)
    return found


def record_parse_miss(
    *,
    raw_text: str,
    rule_result: ParseResult,
    store: ParseMissStore,
    now: datetime,
    llm_result: ParseResult | None = None,
    llm_used: bool = False,
    correlation_id: str | None = None,
) -> ParseMiss:
    """Persist one miss. Never logs the full body at INFO."""
    miss = ParseMiss(
        miss_id=str(uuid.uuid4()),
        recorded_at=now if now.tzinfo else now.replace(tzinfo=FAMILY_TZ),
        raw_text=raw_text,
        rule_intent=rule_result.intent_type.value,
        rule_missing=list(rule_result.missing_fields),
        llm_intent=llm_result.intent_type.value if llm_result is not None else None,
        llm_used=llm_used,
        tokens=extract_miss_tokens(raw_text),
        correlation_id=correlation_id,
    )
    store.save(miss)
    logger.info(
        "parse_miss_recorded",
        component=COMPONENT,
        outcome="success",
        miss_id=miss.miss_id,
        rule_intent=miss.rule_intent,
        llm_intent=miss.llm_intent,
        llm_used=llm_used,
        token_count=len(miss.tokens),
        correlation_id=correlation_id,
    )
    return miss


def summarize_parse_misses(
    store: ParseMissStore,
    *,
    min_count: int = 2,
) -> list[tuple[str, int]]:
    """Repeated tokens across misses — candidates for a later rule phase."""
    counts: Counter[str] = Counter()
    for miss in store.list_all():
        for token in miss.tokens:
            key = token.casefold()
            if key in KNOWN_SKIP:
                continue
            counts[token] += 1
    ranked = [(tok, n) for tok, n in counts.most_common() if n >= min_count]
    logger.info(
        "parse_misses_summarized",
        component=COMPONENT,
        outcome="success",
        keyword_count=len(ranked),
        min_count=min_count,
    )
    return ranked


def purge_parse_misses(
    store: ParseMissStore,
    *,
    now: datetime,
    retention: timedelta = MISS_RETENTION,
) -> int:
    cutoff = now.astimezone(FAMILY_TZ) - retention
    deleted = 0
    for miss in list(store.list_all()):
        recorded = miss.recorded_at
        if recorded.tzinfo is None:
            recorded = recorded.replace(tzinfo=FAMILY_TZ)
        if recorded <= cutoff:
            store.delete(miss.miss_id)
            deleted += 1
    logger.info(
        "parse_miss_purge_completed",
        component=COMPONENT,
        outcome="success",
        purged=deleted,
        retention_days=retention.days,
    )
    return deleted


def maintain_parse_miss_storage(
    store: ParseMissStore,
    *,
    now: datetime | None = None,
) -> int:
    moment = now or datetime.now(tz=FAMILY_TZ)
    return purge_parse_misses(store, now=moment)


def main() -> None:
    """Print repeated miss tokens for the next parser-rules weekend."""
    from cec_vivisystem.logging import setup_logging

    setup_logging()
    store = JsonDirParseMissStore(default_data_dir())
    maintain_parse_miss_storage(store)
    ranked = summarize_parse_misses(store)
    if not ranked:
        print("No repeated parse-miss keywords (min_count=2).")
        return
    print("parse_miss keyword counts (promote into rules if still missing):")
    for token, count in ranked:
        print(f"  {count}\t{token}")


def _miss_from_dict(data: dict) -> ParseMiss:
    recorded = datetime.fromisoformat(str(data["recorded_at"]))
    missing = data.get("rule_missing") or []
    if not isinstance(missing, list):
        missing = []
    tokens = data.get("tokens") or []
    if not isinstance(tokens, list):
        tokens = []
    return ParseMiss(
        miss_id=str(data["miss_id"]),
        recorded_at=recorded,
        raw_text=str(data.get("raw_text") or ""),
        rule_intent=str(data.get("rule_intent") or IntentType.UNKNOWN.value),
        rule_missing=[str(x) for x in missing],
        llm_intent=data.get("llm_intent"),
        llm_used=bool(data.get("llm_used")),
        tokens=[str(x) for x in tokens],
        correlation_id=data.get("correlation_id"),
    )


def iter_tokens(misses: Iterable[ParseMiss]) -> Iterable[str]:
    for miss in misses:
        yield from miss.tokens
