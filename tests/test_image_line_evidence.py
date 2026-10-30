"""Phase 33b follow-up: an image item's evidence is the line the model returned.

Before this, ``image_extract`` passed the Slack caption (「加event」) as the
text behind every Phase 33a line check. The caption holds none of the lines,
so each image item lost its verified excerpt and its people, an explicit 全日
row became the whole-list time question, and the PR #11/#12 dropped-clock
recovery never ran. The evidence is now the model's own ``source_text`` lines,
one per item; an item that returned no line still has no evidence.

All cases are offline: the vision client is faked, the clock is fixed and no
Slack, Google or xAI call is made. Titles and names are synthetic (rule 15).
"""

from __future__ import annotations

import io
import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from cec_vivisystem.calendar_writer import (
    FakeCalendarClient,
    InMemoryCalendarAuditStore,
)
from cec_vivisystem.confirmation import InMemoryConfirmationStore
from cec_vivisystem.event_batch import (
    IMAGE_PROPOSAL_HEADER,
    IMAGE_TIME_QUESTION,
    REASON_MISSING_TIME,
    RESEND_IMAGE_WITH_TIMES,
    RESEND_WHOLE_LIST,
)
from cec_vivisystem.image_extract import XaiVisionEventExtractor, build_image_evidence
from cec_vivisystem.image_intake import ALLOWED_HOST
from cec_vivisystem.listener import _process_family_message_event
from cec_vivisystem.models import IntentType
from cec_vivisystem.parse_fallback import (
    _events_payload_to_parse_result,
    normalize_evidence_text,
)

HK = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 11, 1, 10, 0, tzinfo=HK)
CHANNEL = "Cplans"
SLACK_URL = f"https://{ALLOWED_HOST}/files-pri/T0/F1/download/s.png"
CAPTION = "加event"


# --- payloads and the offline image pipeline ---------------------------------


def _item(**over) -> dict:
    """One v6 item; every field the schema requires, nothing filled by default."""
    base = {
        "title": None, "start": None, "end": None, "all_day": False, "location": None,
        "participants": [], "missing_fields": ["time"], "series": None, "date": None,
        "stated_weekday": None, "source_text": None,
    }
    base.update(over)
    return base


def _payload(*items: dict, intent: str = "create_event") -> dict:
    return {"intent_type": intent, "events": list(items), "over_cap": False,
            "confidence": "medium"}


def _map(payload: dict, *, caption: str = CAPTION):
    """The image mapping: evidence from the reply, caption only as the message."""
    prepared, evidence = build_image_evidence(payload)
    return _events_payload_to_parse_result(
        prepared, raw_text=caption, now=NOW, evidence_text=evidence,
    )


def _map_as_text(payload: dict, *, message: str):
    """The unchanged text path: the message is its own evidence."""
    return _events_payload_to_parse_result(payload, raw_text=message, now=NOW)


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 200, 200)).save(buf, format="PNG")
    return buf.getvalue()


class _FakeFetcher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch(self, url: str, *, size_hint: int | None = None) -> bytes:
        self.calls.append(url)
        return _png_bytes()


def _vision(monkeypatch, payload: dict) -> None:
    """Fake xAI client that answers every call with ``payload``."""
    def create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)),
                                     finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=800, completion_tokens=100),
        )

    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )


def _raw(text: str = CAPTION, ts: str = "1.0", **extra) -> dict:
    return {
        "type": "message", "subtype": "file_share", "channel": CHANNEL, "user": "U1",
        "text": text, "ts": ts, "client_msg_id": f"m-{ts}",
        "files": [{"id": "F1", "name": "s.png", "mimetype": "image/png",
                   "filetype": "png", "size": 1000,
                   "url_private_download": SLACK_URL, "mode": "hosted"}],
        **extra,
    }


def _listener(monkeypatch, payload: dict, *, caption: str = CAPTION):
    """Post one captioned image and return the reply, the store and the calendar."""
    _vision(monkeypatch, payload)
    store, calendar = InMemoryConfirmationStore(), FakeCalendarClient()
    result = _process_family_message_event(
        _raw(caption),
        allowed_channel_ids={CHANNEL}, now=NOW, image_extract_on=True,
        image_extractor=XaiVisionEventExtractor(api_key="fake-key", model="vision-a"),
        slack_file_fetcher=_FakeFetcher(),
        confirmation_store=store, calendar_client=calendar,
        calendar_audit_store=InMemoryCalendarAuditStore(),
    )
    return result.reply_text, store, calendar


# --- the evidence text --------------------------------------------------------


def test_evidence_is_one_normalised_line_per_item_and_invents_nothing():
    payload = _payload(
        _item(title="游泳課程", source_text="11月6日（五）\u3000１５：３０\u200b 游泳課程"),
        _item(title="鋼琴課程"),  # the model returned no line for this item
        _item(title="畫畫", source_text="11月8日（日）09:00 畫畫"),
    )
    prepared, evidence = build_image_evidence(payload)
    assert evidence.split("\n") == [
        "11月6日(五) 15:30 游泳課程", "", "11月8日(日)09:00 畫畫",
    ]
    # The payload copy carries the same lines, so a verified excerpt matches.
    assert [e["source_text"] for e in prepared["events"]] == [
        "11月6日(五) 15:30 游泳課程", None, "11月8日(日)09:00 畫畫",
    ]
    assert prepared is not payload and payload["events"][0]["source_text"].startswith("11月6日（五）")
    assert {k: v for k, v in prepared.items() if k != "events"} == {
        "intent_type": "create_event", "over_cap": False, "confidence": "medium",
    }
    assert normalize_evidence_text("  １５：３0\u200b  ") == "15:30"


# --- the dropped clock comes back from the returned line (PR #11/#12) ---------


def test_a_dropped_clock_is_read_from_the_items_own_returned_line():
    result = _map(_payload(
        _item(title="游泳課程", source_text="11月6日（五）15:30 游泳課程"),
        _item(title="鋼琴課程", date="2026-11-10", source_text="11月10日（二）15:30 鋼琴課程"),
    ))
    assert result.intent_type == IntentType.CREATE_EVENT
    assert list(result.batch_not_included) == []
    assert [(i.start.strftime("%Y-%m-%d %H:%M"), i.all_day) for i in result.batch] == [
        ("2026-11-06 15:30", False), ("2026-11-10 15:30", False),
    ]
    # One hour is the documented default, not an invented end time.
    assert all(item.end_defaulted for item in result.batch)


def test_a_single_image_item_also_reads_its_own_returned_line(monkeypatch):
    payload = _payload(_item(title="游泳課程", source_text="11月6日（五）15:30 游泳課程"))
    result = _map(payload)
    assert result.intent_type == IntentType.CREATE_EVENT
    assert result.batch is None and result.missing_fields == []
    assert result.start == datetime(2026, 11, 6, 15, 30, tzinfo=HK)

    reply, store, calendar = _listener(monkeypatch, payload)
    assert reply.startswith(IMAGE_PROPOSAL_HEADER)
    assert "• Start: 2026-11-06T15:30:00+08:00" in reply
    assert len(store.list_pending()) == 1 and calendar.calls == []


def test_an_explicit_all_day_line_stays_all_day():
    result = _map(_payload(
        _item(title="運動會", all_day=True, start="2026-11-07T00:00:00+08:00",
              date="2026-11-07", missing_fields=[], source_text="11月7日（六）全日 運動會"),
        _item(title="游泳課程", source_text="11月6日（五）15:30 游泳課程"),
    ))
    assert result.intent_type == IntentType.CREATE_EVENT
    assert list(result.batch_not_included) == []
    all_day_item = result.batch[0]
    assert all_day_item.all_day is True
    assert all_day_item.start == datetime(2026, 11, 7, tzinfo=HK)
    assert all_day_item.end == datetime(2026, 11, 8, tzinfo=HK)


@pytest.mark.parametrize("line", [
    "11月6日（五）下午3:30 游泳課程",
    "11月6日（五）3pm 游泳課程",
    "11月6日（五）15:30-16:30 游泳課程",
    "11月6日（五）15:30 至 16:30 游泳課程",
    "11月6日（五）游泳課程",
])
def test_am_pm_a_range_or_no_clock_still_asks(line):
    result = _map(_payload(
        _item(title="游泳課程", source_text=line),
        _item(title="鋼琴課程", date="2026-11-10", source_text="11月10日（二）15:30 鋼琴課程"),
    ))
    assert result.intent_type == IntentType.NEEDS_CLARIFICATION
    assert result.missing_fields == ["time"]
    assert [n.reason for n in result.batch_not_included] == [REASON_MISSING_TIME]


def test_an_item_without_a_returned_line_asks_and_nothing_is_invented():
    result = _map(_payload(
        _item(title="游泳課程", date="2026-11-06"),
        _item(title="鋼琴課程", date="2026-11-10"),
    ), caption="加event\n11月6日 15:30 游泳課程")
    assert result.intent_type == IntentType.NEEDS_CLARIFICATION
    # The caption is sent to the model, which may read it; it is never a local
    # line, so a caption clock can never be attached to an image row here.
    assert [(n.reason, n.source_text) for n in result.batch_not_included] == [
        (REASON_MISSING_TIME, None), (REASON_MISSING_TIME, None),
    ]
    assert result.batch == ()


# --- people come from the item's own line ------------------------------------


def test_participants_come_from_the_returned_line_and_stay_verified():
    result = _map(_payload(
        _item(title="游泳課程", source_text="11月6日（五）15:30 游泳課程 梓梵"),
        _item(title="鋼琴課程", participants=["Coco"], date="2026-11-10",
              source_text="11月10日（二）15:30 鋼琴課程"),
    ))
    assert result.intent_type == IntentType.CREATE_EVENT
    # 梓梵 → Cedric from the item's own line; an unmentioned name is dropped.
    assert [list(item.participants) for item in result.batch] == [["Cedric"], []]


def test_a_caption_name_is_still_allowed_for_a_named_item():
    result = _map(_payload(
        _item(title="游泳課程", participants=["Cedric"],
              source_text="11月6日（五）15:30 游泳課程"),
        _item(title="鋼琴課程", date="2026-11-10", source_text="11月10日（二）15:30 鋼琴課程"),
    ), caption="加event 只要 Cedric 嘅")
    assert [list(item.participants) for item in result.batch] == [["Cedric"], []]


# --- the time question for an image ------------------------------------------


def test_the_image_time_question_asks_for_the_image_again_with_the_times(monkeypatch):
    payload = _payload(
        _item(title="游泳課程", date="2026-11-06", source_text="11月6日（五）游泳課程"),
        _item(title="鋼琴課程", date="2026-11-10", source_text="11月10日（二）15:30 鋼琴課程"),
    )
    reply, store, calendar = _listener(monkeypatch, payload)
    assert reply.startswith(IMAGE_TIME_QUESTION)
    assert "加event" in reply
    assert "「11月6日(五)游泳課程」" in reply
    assert "例子 / Example: 11月6日 15:30 游泳課" in reply
    assert RESEND_IMAGE_WITH_TIMES in reply
    assert "Send the image again" in reply
    # The typed-list copy must not be reused: there is no list to resend.
    assert RESEND_WHOLE_LIST not in reply and "Send the whole list again" not in reply
    assert "我未建立任何提案。/ No proposal was made." in reply
    assert store.list_all() == [] and calendar.calls == []


def test_the_typed_list_time_question_is_unchanged():
    from cec_vivisystem.event_batch import format_batch_clarification

    message = "加以下events:\n11月6日（五）游泳課程\n11月10日（二）15:30 鋼琴課程"
    lines = message.splitlines()[1:]
    result = _map_as_text(_payload(
        _item(title="游泳課程", date="2026-11-06", source_text=lines[0]),
        _item(title="鋼琴課程", start="2026-11-10T15:30:00+08:00", date="2026-11-10",
              missing_fields=[], source_text=lines[1]),
    ), message=message)
    reply = format_batch_clarification(result)
    assert reply.startswith("以下項目有日期但冇時間")
    assert RESEND_WHOLE_LIST in reply
    assert RESEND_IMAGE_WITH_TIMES not in reply and "加event" not in reply


def test_an_image_list_still_waits_for_one_yes(monkeypatch):
    payload = _payload(
        _item(title="游泳課程", source_text="11月6日（五）15:30 游泳課程"),
        _item(title="鋼琴課程", source_text="11月10日（二）15:30 鋼琴課程"),
    )
    reply, store, calendar = _listener(monkeypatch, payload)
    assert reply.startswith(IMAGE_PROPOSAL_HEADER + "\n請確認以下 2 個活動")
    assert "1. 2026-11-06（五）15:30–16:30 游泳課程" in reply
    # No count warning: the caption is not a list of dated lines.
    assert "行有日期" not in reply
    assert len(store.list_pending()) == 1 and calendar.calls == []
