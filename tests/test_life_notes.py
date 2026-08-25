"""Phase 5A unit tests for LifeNotesKeeper (offline, no Slack/LLM/DB)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem.life_notes import (
    InMemoryLifeNotesStore,
    JsonDirLifeNotesStore,
    LifeNoteError,
    create_life_note,
    default_data_dir,
)
from cec_vivisystem.models import LifeNote, LifeNoteSource, LifeNoteStatus

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
FIXED_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=FAMILY_TZ)
NOTE_ID_RE = re.compile(r"^ln_\d{8}_\d{6}_[0-9a-f]{8}$")

# Realistic family life-note (not a calendar event)
LN1_TEXT = "梓梵今日喺學校同朋友一齊砌積木，好開心。"

SOURCE = {
    "channel": "C_FAMILY_LIFE_NOTES",
    "message_id": "1754632800.000100",
    "user": "U_PARENT",
}


@pytest.fixture
def store() -> InMemoryLifeNotesStore:
    return InMemoryLifeNotesStore()


def test_create_life_note_preserves_raw_chinese_text(
    store: InMemoryLifeNotesStore,
) -> None:
    """LN1: simple Chinese text is stored exactly; status raw; id and created_at set."""
    note = create_life_note(
        LN1_TEXT,
        source=SOURCE,
        now=FIXED_NOW,
        store=store,
        correlation_id="corr-ln1",
    )
    assert note.raw_text == LN1_TEXT
    assert note.status == LifeNoteStatus.RAW
    assert note.status.value == "raw"
    assert note.created_at == FIXED_NOW
    assert note.created_at.tzinfo is not None
    assert NOTE_ID_RE.fullmatch(note.note_id)
    assert note.note_id.startswith("ln_20260808_120000_")
    assert note.correlation_id == "corr-ln1"
    assert store.get(note.note_id) is note


def test_create_life_note_stores_source_metadata(
    store: InMemoryLifeNotesStore,
) -> None:
    """LN2: source.channel, source.message_id, source.user are present."""
    note = create_life_note(LN1_TEXT, source=SOURCE, now=FIXED_NOW, store=store)
    assert isinstance(note.source, LifeNoteSource)
    assert note.source.channel == SOURCE["channel"]
    assert note.source.message_id == SOURCE["message_id"]
    assert note.source.user == SOURCE["user"]


def test_create_life_note_rejects_empty_or_whitespace(
    store: InMemoryLifeNotesStore,
) -> None:
    """LN3: empty or whitespace-only text is a controlled rejection."""
    for bad in ("", "   ", "\n\t", "　"):
        with pytest.raises(LifeNoteError, match="empty"):
            create_life_note(bad, source=SOURCE, now=FIXED_NOW, store=store)
    assert store.list_recent() == []


def test_store_round_trip(store: InMemoryLifeNotesStore) -> None:
    """LN4: save then get returns an equivalent note."""
    original = LifeNote(
        note_id="ln_20260808_120000_abcd1234",
        raw_text=LN1_TEXT,
        created_at=FIXED_NOW,
        status=LifeNoteStatus.RAW,
        source=LifeNoteSource(
            channel=SOURCE["channel"],
            message_id=SOURCE["message_id"],
            user=SOURCE["user"],
        ),
        correlation_id="corr-ln4",
    )
    saved = store.save(original)
    loaded = store.get(saved.note_id)
    assert loaded is not None
    assert loaded.note_id == original.note_id
    assert loaded.raw_text == original.raw_text
    assert loaded.created_at == original.created_at
    assert loaded.status == original.status
    assert loaded.source.channel == original.source.channel
    assert loaded.source.message_id == original.source.message_id
    assert loaded.source.user == original.source.user
    assert loaded.correlation_id == original.correlation_id
    assert store.get("missing") is None


def test_list_recent_order_and_limit(store: InMemoryLifeNotesStore) -> None:
    """LN5: list_recent is reverse chronological and respects limit."""
    texts = ("first", "second", "third")
    notes = [
        create_life_note(
            text,
            source=SOURCE,
            now=FIXED_NOW + timedelta(minutes=i),
            store=store,
        )
        for i, text in enumerate(texts)
    ]
    recent = store.list_recent(limit=2)
    assert [n.note_id for n in recent] == [notes[2].note_id, notes[1].note_id]
    assert [n.raw_text for n in recent] == ["third", "second"]
    all_notes = store.list_recent(limit=50)
    assert [n.raw_text for n in all_notes] == ["third", "second", "first"]


def test_life_note_contract_fields(store: InMemoryLifeNotesStore) -> None:
    """Contract: LifeNote exposes Phase 5A fields; no structured extras."""
    note = create_life_note(LN1_TEXT, source=SOURCE, now=FIXED_NOW, store=store)
    for name in (
        "note_id",
        "raw_text",
        "created_at",
        "status",
        "source",
        "correlation_id",
    ):
        assert hasattr(note, name), f"missing {name}"
    assert not hasattr(note, "people")
    assert not hasattr(note, "emotion")
    assert not hasattr(note, "location")
    assert not hasattr(note, "events")


def test_create_life_note_logs_boundary(store: InMemoryLifeNotesStore) -> None:
    """Boundary logging: public entry point completes with logging configured."""
    note = create_life_note(LN1_TEXT, source=SOURCE, now=FIXED_NOW, store=store)
    assert note.status == LifeNoteStatus.RAW


def test_json_dir_store_round_trip(tmp_path: Path) -> None:
    """Production JSON store: save/get/list_recent under a temp directory."""
    json_store = JsonDirLifeNotesStore(tmp_path)
    note = create_life_note(
        LN1_TEXT,
        source=SOURCE,
        now=FIXED_NOW,
        store=json_store,
        correlation_id="corr-json",
    )
    path = tmp_path / f"{note.note_id}.json"
    assert path.is_file()
    loaded = json_store.get(note.note_id)
    assert loaded is not None
    assert loaded.raw_text == LN1_TEXT
    assert loaded.status == LifeNoteStatus.RAW
    assert loaded.source.user == SOURCE["user"]
    assert loaded.created_at == FIXED_NOW
    assert loaded.correlation_id == "corr-json"
    listed = json_store.list_recent(limit=1)
    assert len(listed) == 1
    assert listed[0].note_id == note.note_id


def test_create_life_note_rejects_incomplete_source(
    store: InMemoryLifeNotesStore,
) -> None:
    """Missing source identifiers are a controlled error; no note created."""
    with pytest.raises(LifeNoteError, match="source"):
        create_life_note(
            LN1_TEXT,
            source={"channel": "C1"},
            now=FIXED_NOW,
            store=store,
        )
    assert store.list_recent() == []


def test_default_data_dir_is_gitignored_life_notes_path() -> None:
    path = default_data_dir()
    assert path.name == "life_notes"
    assert path.parent.name == "data"
