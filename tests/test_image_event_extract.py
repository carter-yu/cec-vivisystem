"""Phase 33b: image intake, prepare, vision extract → 33a path (offline)."""

from __future__ import annotations

import io
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from cec_vivisystem.confirmation import InMemoryConfirmationStore, build_batch_proposal
from cec_vivisystem.image_extract import FakeImageEventExtractor, empty_image_result
from cec_vivisystem.image_intake import (
    ALLOWED_HOST,
    REPLY_FORMAT,
    REPLY_NO_EVENTS,
    REPLY_TOO_LARGE,
    REPLY_TOO_MANY,
    ImageIntakeError,
    StdlibSlackFileFetcher,
    has_image_trigger,
    image_extract_enabled,
    intake_images,
    load_image_extract_mode,
    normalize_plans_file_event,
    prepare_image,
    select_image_files,
)
from cec_vivisystem.listener import _process_family_message_event
from cec_vivisystem.models import (
    Confidence,
    InboundFile,
    IntentType,
    ParseResult,
)

HK = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 11, 1, 10, 0, tzinfo=HK)
CHANNEL = "Cplans"


def _png_bytes(color=(200, 200, 200), size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _file(**over) -> InboundFile:
    base = {
        "id": "F1", "name": "s.png", "mimetype": "image/png", "filetype": "png", "size": 100,
        "url_private_download": f"https://{ALLOWED_HOST}/files-pri/T/F1/download",
        "mode": "hosted", "is_external": False, "file_access": None,
    }
    base.update(over)
    return InboundFile(**base)


class _FakeFetcher:
    def __init__(self, data: bytes | Exception) -> None:
        self.data = data
        self.calls: list[str] = []

    def fetch(self, url: str, *, size_hint: int | None = None) -> bytes:
        self.calls.append(url)
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


def test_kill_switch_defaults_off():
    assert load_image_extract_mode({}) == "off"
    assert image_extract_enabled({}) is False
    assert load_image_extract_mode({"CEC_IMAGE_EXTRACT": "xai"}) == "xai"
    assert load_image_extract_mode({"CEC_IMAGE_EXTRACT": "ocr"}) == "off"


def test_trigger_requires_jia_event():
    assert has_image_trigger("加event 請讀") is True
    assert has_image_trigger("加活動") is False


def test_normalize_file_share_and_plain_with_files():
    raw = {
        "type": "message", "subtype": "file_share", "channel": CHANNEL,
        "user": "U1", "text": "加event", "ts": "1.0",
        "files": [{"id": "F1", "mimetype": "image/png", "filetype": "png",
                   "size": 10, "url_private_download": f"https://{ALLOWED_HOST}/x",
                   "mode": "hosted"}],
    }
    msg = normalize_plans_file_event(raw)
    assert msg is not None and len(msg.files) == 1
    raw2 = {**raw, "subtype": None}
    assert normalize_plans_file_event(raw2) is not None
    # Thread reply ignored in v1
    assert normalize_plans_file_event({**raw, "thread_ts": "0.5", "ts": "1.0"}) is None
    # Other subtype ignored
    assert normalize_plans_file_event({**raw, "subtype": "message_changed"}) is None


def test_select_rejects_format_too_many_too_large():
    with pytest.raises(ImageIntakeError) as e:
        select_image_files([_file(mimetype="image/gif", filetype="gif")])
    assert e.value.reply == REPLY_FORMAT
    with pytest.raises(ImageIntakeError) as e:
        select_image_files([_file(id=f"F{i}") for i in range(4)])
    assert e.value.reply == REPLY_TOO_MANY
    with pytest.raises(ImageIntakeError) as e:
        select_image_files([_file(size=11 * 1024 * 1024)])
    assert e.value.reply == REPLY_TOO_LARGE


def test_prepare_strips_and_resizes():
    data = _png_bytes(size=(3000, 1000))
    prepared = prepare_image(data, source_index=1, declared_mime="image/png")
    assert prepared.width <= 2048 and prepared.height <= 2048
    assert prepared.mimetype == "image/png"
    with pytest.raises(ImageIntakeError):
        prepare_image(b"not-an-image", source_index=1)


def test_fetcher_refuses_non_slack_host():
    fetcher = StdlibSlackFileFetcher(bot_token="xoxb-test")
    with pytest.raises(ImageIntakeError):
        fetcher.fetch("https://evil.example/file.png")


def test_intake_skips_without_trigger():
    from cec_vivisystem.models import InboundMessage
    msg = InboundMessage(text="hello", channel_id=CHANNEL, user_id="U1", files=(_file(),))
    assert intake_images(msg, fetcher=_FakeFetcher(_png_bytes())) == []


def test_switch_off_drops_file_share_like_today():
    raw = {
        "type": "message", "subtype": "file_share", "channel": CHANNEL,
        "user": "U1", "text": "加event", "ts": "1.0",
        "files": [{"id": "F1", "mimetype": "image/png", "url_private_download":
                   f"https://{ALLOWED_HOST}/x", "mode": "hosted", "size": 10}],
    }
    result = _process_family_message_event(
        raw, allowed_channel_ids={CHANNEL}, image_extract_on=False,
    )
    assert result.outcome.value == "ignored"


def test_image_without_trigger_ignored_when_switch_on():
    raw = {
        "type": "message", "subtype": "file_share", "channel": CHANNEL,
        "user": "U1", "text": "睇下呢張圖", "ts": "1.0",
        "files": [{"id": "F1", "mimetype": "image/png", "url_private_download":
                   f"https://{ALLOWED_HOST}/x", "mode": "hosted", "size": 10}],
    }
    fetcher = _FakeFetcher(_png_bytes())
    _process_family_message_event(
        raw, allowed_channel_ids={CHANNEL}, image_extract_on=True,
        image_extractor=FakeImageEventExtractor(empty_image_result("")),
        slack_file_fetcher=fetcher,
    )
    # No 加event → no fetch; message may be ignored as empty/create-unknown path
    assert fetcher.calls == []


def test_jia_event_plus_image_builds_proposal():
    png = _png_bytes()
    batch = ParseResult(
        intent_type=IntentType.CREATE_EVENT,
        title="游泳", start=datetime(2026, 11, 6, 15, 30, tzinfo=HK),
        end=datetime(2026, 11, 6, 16, 30, tzinfo=HK), all_day=False, location=None,
        raw_text="加event", confidence=Confidence.MEDIUM, notes="image_extract;from_image;batch",
        batch=(
            __import__("cec_vivisystem.models", fromlist=["EventItem"]).EventItem(
                index=1, title="游泳",
                start=datetime(2026, 11, 6, 15, 30, tzinfo=HK),
                end=datetime(2026, 11, 6, 16, 30, tzinfo=HK),
                all_day=False, image_index=1,
            ),
        ),
    )
    store = InMemoryConfirmationStore()
    raw = {
        "type": "message", "subtype": "file_share", "channel": CHANNEL,
        "user": "U1", "text": "加event", "ts": "1.0", "client_msg_id": "m1",
        "files": [{"id": "F1", "mimetype": "image/png", "filetype": "png", "size": len(png),
                   "url_private_download": f"https://{ALLOWED_HOST}/x", "mode": "hosted"}],
    }
    result = _process_family_message_event(
        raw, allowed_channel_ids={CHANNEL}, now=NOW,
        confirmation_store=store, image_extract_on=True,
        image_extractor=FakeImageEventExtractor(batch),
        slack_file_fetcher=_FakeFetcher(png),
    )
    assert result.outcome.value == "replied"
    assert result.reply_text is not None
    assert "從圖片讀到" in result.reply_text
    assert "游泳" in result.reply_text
    assert store.list_pending()


def test_no_events_reply():
    png = _png_bytes()
    raw = {
        "type": "message", "channel": CHANNEL, "user": "U1", "text": "加event",
        "ts": "1.0",
        "files": [{"id": "F1", "mimetype": "image/png", "filetype": "png", "size": 10,
                   "url_private_download": f"https://{ALLOWED_HOST}/x", "mode": "hosted"}],
    }
    result = _process_family_message_event(
        raw, allowed_channel_ids={CHANNEL}, now=NOW, image_extract_on=True,
        image_extractor=FakeImageEventExtractor(empty_image_result("加event")),
        slack_file_fetcher=_FakeFetcher(png),
    )
    assert result.reply_text == REPLY_NO_EVENTS


def test_proposal_header_helper():
    from cec_vivisystem.models import EventItem
    item = EventItem(
        index=1, title="課", start=datetime(2026, 11, 6, 15, 0, tzinfo=HK),
        end=datetime(2026, 11, 6, 16, 0, tzinfo=HK), all_day=False,
    )
    pr = ParseResult(
        intent_type=IntentType.CREATE_EVENT, title="課", start=item.start, end=item.end,
        all_day=False, location=None, notes="from_image;batch", batch=(item,),
    )
    text = build_batch_proposal(pr)
    assert text.startswith("從圖片讀到")
