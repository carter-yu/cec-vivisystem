"""LifeNotesKeeper (Phase 5A) — Option A reliable raw capture.

Stores exact original message text plus basic metadata. Independent from the
calendar / confirmation path. No structured extraction, no LLM, no database.

Retention: class **F** (life notes / user content) — until the family deletes.
Not auto-purged as logs. Access logs remain class A.

Dedicated Slack channel: ``#family-life-notes``. Listener dispatch is Phase 5B.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from cec_vivisystem.logging import get_logger
from cec_vivisystem.models import LifeNote, LifeNoteSource, LifeNoteStatus

logger = get_logger(__name__)

COMPONENT = "life_notes"
FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
PREVIEW_LEN = 80

# Dedicated Slack channel name (Listener dispatch: Phase 5B)
LIFE_NOTES_CHANNEL_NAME = "family-life-notes"

REQUIRED_SOURCE_KEYS = ("channel", "message_id", "user")


class LifeNoteError(Exception):
    """Controlled life-note failure (e.g. empty text or missing source)."""


class LifeNotesStore(Protocol):
    """Persistence for life notes (class F)."""

    def save(self, note: LifeNote) -> LifeNote: ...

    def get(self, note_id: str) -> LifeNote | None: ...

    def list_recent(self, *, limit: int = 50) -> list[LifeNote]: ...


class InMemoryLifeNotesStore:
    """Test/default store — no disk."""

    def __init__(self) -> None:
        self._items: dict[str, LifeNote] = {}

    def save(self, note: LifeNote) -> LifeNote:
        self._items[note.note_id] = note
        return note

    def get(self, note_id: str) -> LifeNote | None:
        return self._items.get(note_id)

    def list_recent(self, *, limit: int = 50) -> list[LifeNote]:
        return _list_recent(list(self._items.values()), limit=limit)


class JsonDirLifeNotesStore:
    """One JSON file per note under a directory (gitignored ``data/life_notes/``)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, note_id: str) -> Path:
        safe = note_id.replace("/", "_")
        return self.root / f"{safe}.json"

    def save(self, note: LifeNote) -> LifeNote:
        path = self._path(note.note_id)
        try:
            path.write_text(
                json.dumps(_note_to_dict(note), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error(
                "note_store_save_failed",
                component=COMPONENT,
                outcome="failure",
                note_id=note.note_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise
        return note

    def get(self, note_id: str) -> LifeNote | None:
        path = self._path(note_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return _note_from_dict(data)
        except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            logger.error(
                "note_store_load_failed",
                component=COMPONENT,
                outcome="failure",
                note_id=note_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return None

    def list_recent(self, *, limit: int = 50) -> list[LifeNote]:
        items: list[LifeNote] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                items.append(_note_from_dict(data))
            except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
                logger.error(
                    "note_store_load_failed",
                    component=COMPONENT,
                    outcome="failure",
                    path=str(path),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
        return _list_recent(items, limit=limit)


def default_data_dir() -> Path:
    """``<repo>/data/life_notes``."""
    return Path(__file__).resolve().parents[2] / "data" / "life_notes"


def create_life_note(
    raw_text: str,
    *,
    source: Mapping[str, Any],
    now: datetime | None = None,
    store: LifeNotesStore | None = None,
    correlation_id: str | None = None,
) -> LifeNote:
    """Create and persist a raw life note. Preserves ``raw_text`` exactly.

    Args:
        raw_text: Exact original message. Whitespace-only is rejected.
        source: Mapping with at least ``channel``, ``message_id``, ``user``.
        now: Reference instant; defaults to current time in Asia/Hong_Kong.
        store: Persistence; defaults to JSON files under ``data/life_notes/``.
        correlation_id: Optional flow id for tracing.

    Raises:
        LifeNoteError: empty/whitespace text or missing source fields.
    """
    started = time.perf_counter()
    corr = correlation_id or str(uuid.uuid4())
    preview = _preview(raw_text or "")
    logger.info(
        "note_create_started",
        component=COMPONENT,
        correlation_id=corr,
        message_length=len(raw_text or ""),
        message_preview=preview,
    )

    try:
        _require_non_empty_text(raw_text)
        source_obj = _source_from_mapping(source)
        created = _normalize_now(now)
        note = LifeNote(
            note_id=_new_note_id(created),
            raw_text=raw_text,
            created_at=created,
            status=LifeNoteStatus.RAW,
            source=source_obj,
            correlation_id=corr,
        )
        if store is None:
            store = JsonDirLifeNotesStore(default_data_dir())
        saved = store.save(note)
    except LifeNoteError as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.warning(
            "note_create_failed",
            component=COMPONENT,
            correlation_id=corr,
            outcome="failure",
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
            error_message=str(exc),
            message_length=len(raw_text or ""),
        )
        raise
    except OSError as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.error(
            "note_create_failed",
            component=COMPONENT,
            correlation_id=corr,
            outcome="failure",
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
            error_message=str(exc),
            message_length=len(raw_text or ""),
        )
        raise

    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "note_written",
        component=COMPONENT,
        correlation_id=corr,
        outcome="success",
        note_id=saved.note_id,
        size=len(saved.raw_text),
        duration_ms=duration_ms,
        status=saved.status.value,
        op="create",
    )
    return saved


def _require_non_empty_text(raw_text: str) -> None:
    if not (raw_text or "").strip():
        raise LifeNoteError("raw_text must not be empty")


def _source_from_mapping(source: Mapping[str, Any] | None) -> LifeNoteSource:
    if source is None:
        raise LifeNoteError("source is required")
    missing = [
        key
        for key in REQUIRED_SOURCE_KEYS
        if not str(source.get(key) or "").strip()
    ]
    if missing:
        raise LifeNoteError(
            "source missing required fields: " + ", ".join(missing)
        )
    return LifeNoteSource(
        channel=str(source["channel"]),
        message_id=str(source["message_id"]),
        user=str(source["user"]),
    )


def _new_note_id(created: datetime) -> str:
    stamp = created.strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"ln_{stamp}_{short}"


def _normalize_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(tz=FAMILY_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=FAMILY_TZ)
    return now.astimezone(FAMILY_TZ)


def _list_recent(items: list[LifeNote], *, limit: int) -> list[LifeNote]:
    if limit < 1:
        return []
    ordered = sorted(
        items,
        key=lambda n: (n.created_at, n.note_id),
        reverse=True,
    )
    return ordered[:limit]


def _preview(text: str) -> str:
    if len(text) <= PREVIEW_LEN:
        return text
    return text[: PREVIEW_LEN - 1] + "…"


def _note_to_dict(note: LifeNote) -> dict[str, Any]:
    return {
        "note_id": note.note_id,
        "raw_text": note.raw_text,
        "created_at": note.created_at.isoformat(),
        "status": note.status.value,
        "source": {
            "channel": note.source.channel,
            "message_id": note.source.message_id,
            "user": note.source.user,
        },
        "correlation_id": note.correlation_id,
    }


def _note_from_dict(data: dict[str, Any]) -> LifeNote:
    source_data = data["source"]
    created = _parse_dt(data["created_at"])
    if created is None:
        raise ValueError("created_at is required")
    return LifeNote(
        note_id=data["note_id"],
        raw_text=data["raw_text"],
        created_at=created,
        status=LifeNoteStatus(data["status"]),
        source=LifeNoteSource(
            channel=str(source_data["channel"]),
            message_id=str(source_data["message_id"]),
            user=str(source_data["user"]),
        ),
        correlation_id=data.get("correlation_id"),
    )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=FAMILY_TZ)
    return dt
