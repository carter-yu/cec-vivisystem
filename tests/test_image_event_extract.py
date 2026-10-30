"""Phase 33b: image intake, prepare, vision extract → 33a path (offline)."""

from __future__ import annotations

import base64
import email.message
import io
import json
import subprocess
import sys
from datetime import datetime
from http.client import IncompleteRead
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError
from urllib.request import HTTPHandler, HTTPSHandler, ProxyHandler
from urllib.response import addinfourl
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from cec_vivisystem.calendar_writer import (
    FakeCalendarClient,
    InMemoryCalendarAuditStore,
)
from cec_vivisystem.confirmation import InMemoryConfirmationStore, build_batch_proposal
from cec_vivisystem.event_batch import IMAGE_PROPOSAL_HEADER
from cec_vivisystem.image_extract import (
    DEFAULT_VISION_MODEL,
    FakeImageEventExtractor,
    XaiVisionEventExtractor,
    empty_image_result,
    load_image_extractor,
)
from cec_vivisystem.image_intake import (
    ALLOWED_HOST,
    MAX_BYTES,
    REPLY_DOWNLOAD,
    REPLY_FORMAT,
    REPLY_NO_EVENTS,
    REPLY_TOO_LARGE,
    REPLY_TOO_MANY,
    REPLY_UNREADABLE,
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
from cec_vivisystem.life_notes import InMemoryLifeNotesStore
from cec_vivisystem.listener import (
    _process_family_message_event,
    process_slack_message_event,
)
from cec_vivisystem.models import (
    Confidence,
    InboundFile,
    IntentType,
    ListenerOutcome,
    ListenerResult,
    ParseResult,
)
from cec_vivisystem.parse_fallback import CREATE_SCHEMA_V6, DEFAULT_BASE_URL

REPO = Path(__file__).resolve().parents[1]
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


# --- review additions: download guards (D1–D5, G1) ------------------------------

TOKEN = "xoxb-synthetic-test-token"
SLACK_URL = f"https://{ALLOWED_HOST}/files-pri/T0/F1/download/s.png"


class _FakeTransport(HTTPHandler, HTTPSHandler):
    """Offline http + https: canned (status, headers, body) per URL; records requests.

    Subclassing both default transports keeps every scheme off the network,
    even if a redirect guard regresses.
    """

    def __init__(self, routes: dict) -> None:
        super().__init__()
        self.routes = routes
        self.seen: list[tuple[str, str | None]] = []

    def _open(self, req):
        self.seen.append((req.full_url, req.get_header("Authorization")))
        outcome = self.routes[req.full_url]
        if isinstance(outcome, BaseException):
            raise outcome
        status, headers, body = outcome
        message = email.message.Message()
        for key, value in headers.items():
            message[key] = value
        fp = body if hasattr(body, "read") else io.BytesIO(body)
        response = addinfourl(fp, message, req.full_url, code=status)
        response.msg = "canned"
        return response

    http_open = https_open = _open


class _TruncatedBody(io.BytesIO):
    def read(self, *args):
        raise IncompleteRead(b"")


def _fetcher(routes: dict) -> tuple[StdlibSlackFileFetcher, _FakeTransport]:
    transport = _FakeTransport(routes)
    # ProxyHandler({}) ignores any proxy variables of the test machine.
    fetcher = StdlibSlackFileFetcher(bot_token=TOKEN, handlers=(ProxyHandler({}), transport))
    return fetcher, transport


def test_fetch_sends_the_bearer_token_to_the_slack_file_host():
    png = _png_bytes()
    fetcher, transport = _fetcher({SLACK_URL: (200, {}, png)})
    assert fetcher.fetch(SLACK_URL, size_hint=len(png)) == png
    assert transport.seen == [(SLACK_URL, f"Bearer {TOKEN}")]


@pytest.mark.parametrize("url", [
    "https://evil.example/file.png",
    f"http://{ALLOWED_HOST}/files-pri/T0/F1/s.png",
    f"https://{ALLOWED_HOST}.evil.example/s.png",
    f"https://{ALLOWED_HOST}@evil.example/s.png",
])
def test_fetch_refuses_other_hosts_before_any_request(url):
    fetcher, transport = _fetcher({})
    with pytest.raises(ImageIntakeError) as err:
        fetcher.fetch(url)
    assert err.value.reply == REPLY_DOWNLOAD
    assert transport.seen == []


@pytest.mark.parametrize("target", [
    "https://evil.example/collect",
    "https://workspace.slack.com/?redir=%2Ffiles-pri%2FT0",
    f"http://{ALLOWED_HOST}/files-pri/T0/F1/s.png",
])
def test_fetch_refuses_a_redirect_off_the_file_host_before_the_token_is_sent(target, capsys):
    fetcher, transport = _fetcher({
        SLACK_URL: (302, {"Location": target}, b""),
        target: (200, {}, _png_bytes()),
    })
    with pytest.raises(ImageIntakeError) as err:
        fetcher.fetch(SLACK_URL)
    assert err.value.reply == REPLY_DOWNLOAD
    assert transport.seen == [(SLACK_URL, f"Bearer {TOKEN}")]  # target never requested
    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert "image_fetch_refused_redirect" in out
    assert TOKEN not in out and "evil.example" not in out and "workspace.slack" not in out


def test_fetch_follows_a_redirect_that_stays_on_the_file_host():
    png = _png_bytes()
    moved = f"https://{ALLOWED_HOST}/files-pri/T0/F1/s.png"
    fetcher, transport = _fetcher({
        SLACK_URL: (302, {"Location": moved}, b""),
        moved: (200, {}, png),
    })
    assert fetcher.fetch(SLACK_URL) == png
    assert transport.seen == [(SLACK_URL, f"Bearer {TOKEN}"), (moved, f"Bearer {TOKEN}")]


def test_fetch_caps_the_declared_and_the_streamed_size():
    fetcher, transport = _fetcher({SLACK_URL: (200, {}, b"\0" * (MAX_BYTES + 1))})
    with pytest.raises(ImageIntakeError) as err:
        fetcher.fetch(SLACK_URL, size_hint=MAX_BYTES + 1)
    assert err.value.reply == REPLY_TOO_LARGE and transport.seen == []
    with pytest.raises(ImageIntakeError) as err:
        fetcher.fetch(SLACK_URL)  # no declared size: the read loop stops it
    assert err.value.reply == REPLY_TOO_LARGE


@pytest.mark.parametrize("outcome", [
    (403, {}, b"denied"),
    URLError(TimeoutError("timed out")),
    (200, {}, _TruncatedBody()),
], ids=["http_403", "timeout", "truncated_read"])
def test_fetch_failures_reply_download_failed_and_log_no_url_or_token(outcome, capsys):
    fetcher, _ = _fetcher({SLACK_URL: outcome})
    with pytest.raises(ImageIntakeError) as err:
        fetcher.fetch(SLACK_URL)
    assert err.value.reply == REPLY_DOWNLOAD
    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert "image_fetch_failed" in out
    assert TOKEN not in out and ALLOWED_HOST not in out and "files-pri" not in out


# --- review additions: prepare (X1–X3) -----------------------------------------


def test_prepare_applies_exif_orientation_and_drops_every_metadata_block():
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate to display
    exif[0x010F] = "SyntheticCam"  # Make
    exif[0x8825] = {1: "N", 2: (22.0, 17.0, 0.0)}  # synthetic GPS IFD
    source = io.BytesIO()
    Image.new("RGB", (60, 40), (200, 30, 30)).save(
        source, format="JPEG", exif=exif, comment=b"synthetic-note"
    )
    assert Image.open(io.BytesIO(source.getvalue())).getexif().get_ifd(0x8825)  # precondition
    prepared = prepare_image(source.getvalue(), source_index=1, declared_mime="image/jpeg")
    assert (prepared.width, prepared.height) == (40, 60)
    assert prepared.mimetype == "image/jpeg"
    assert b"SyntheticCam" not in prepared.data and b"synthetic-note" not in prepared.data
    out = Image.open(io.BytesIO(prepared.data))
    assert not out.getexif() and not {"exif", "comment", "icc_profile"} & set(out.info)

    png = io.BytesIO()
    Image.new("RGB", (32, 32)).save(png, format="PNG", icc_profile=b"synthetic-profile")
    prepared_png = prepare_image(png.getvalue(), source_index=1, declared_mime="image/png")
    assert "icc_profile" not in Image.open(io.BytesIO(prepared_png.data)).info


def test_prepare_caps_the_long_side_and_never_enlarges():
    big = prepare_image(_png_bytes(size=(4000, 3000)), source_index=1, declared_mime="image/png")
    assert (big.width, big.height) == (2048, 1536)
    small = prepare_image(_png_bytes(size=(800, 600)), source_index=1, declared_mime="image/png")
    assert (small.width, small.height) == (800, 600)


def test_prepare_refuses_over_40_megapixels_before_decoding(monkeypatch):
    source = io.BytesIO()
    Image.new("1", (9000, 5000)).save(source, format="PNG")  # 45 MP in a few KB
    decoded: list[int] = []
    monkeypatch.setattr("PIL.ImageOps.exif_transpose", lambda *a, **k: decoded.append(1))
    with pytest.raises(ImageIntakeError) as err:
        prepare_image(source.getvalue(), source_index=1, declared_mime="image/png")
    assert err.value.reply == REPLY_UNREADABLE
    assert decoded == []  # refused on the header size, never decoded


def test_prepare_decodes_only_png_and_jpeg_and_keeps_png_by_content():
    gif = io.BytesIO()
    Image.new("RGB", (8, 8)).save(gif, format="GIF")
    for data in (gif.getvalue(), b"not-an-image"):
        with pytest.raises(ImageIntakeError) as err:
            prepare_image(data, source_index=1, declared_mime="image/png")
        assert err.value.reply == REPLY_UNREADABLE
    rgba = io.BytesIO()
    Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(rgba, format="PNG")
    prepared = prepare_image(rgba.getvalue(), source_index=1)  # Slack sent no mimetype
    assert prepared.mimetype == "image/png"
    assert Image.open(io.BytesIO(prepared.data)).mode == "RGB"


# --- review additions: listener gating (S3–S5) ---------------------------------


def _slack_file(**over) -> dict:
    entry = {"id": "F1", "name": "s.png", "mimetype": "image/png", "filetype": "png",
             "size": 1000, "url_private_download": SLACK_URL, "mode": "hosted"}
    entry.update(over)
    return entry


def _raw(text: str = "加event", *, files=None, subtype: str | None = "file_share",
         ts: str = "1.0", channel: str = CHANNEL, **extra) -> dict:
    raw = {"type": "message", "channel": channel, "user": "U1", "text": text, "ts": ts,
           "client_msg_id": f"m-{ts}", **extra}
    if subtype:
        raw["subtype"] = subtype
    entries = [_slack_file()] if files is None else list(files)
    if entries:
        raw["files"] = entries
    return raw


def _on(extractor, fetcher=None, **extra) -> dict:
    return {
        "allowed_channel_ids": {CHANNEL}, "now": NOW, "image_extract_on": True,
        "image_extractor": extractor,
        "slack_file_fetcher": fetcher if fetcher is not None else _FakeFetcher(_png_bytes()),
        **extra,
    }


@pytest.mark.parametrize("subtype", ["file_share", None])
def test_switch_on_photo_without_jia_event_behaves_exactly_as_switch_off(subtype):
    # A create-looking caption: today a file_share is dropped and a plain
    # message is parsed as text. The switch must change neither.
    raw = _raw("聽日3點游水", subtype=subtype)
    store_off, store_on = InMemoryConfirmationStore(), InMemoryConfirmationStore()
    extractor = FakeImageEventExtractor(empty_image_result(""))
    fetcher = _FakeFetcher(_png_bytes())
    off = _process_family_message_event(
        raw, allowed_channel_ids={CHANNEL}, now=NOW, confirmation_store=store_off,
    )
    on = _process_family_message_event(
        raw, confirmation_store=store_on, **_on(extractor, fetcher),
    )
    assert (on.outcome, on.reply_text) == (off.outcome, off.reply_text)
    assert len(store_on.list_all()) == len(store_off.list_all())
    assert fetcher.calls == [] and extractor.calls == []


@pytest.mark.parametrize("subtype", ["file_share", None])
def test_switch_off_never_fetches_or_calls_the_model(subtype):
    raw = _raw(subtype=subtype)
    extractor = FakeImageEventExtractor(empty_image_result(""))
    fetcher = _FakeFetcher(_png_bytes())
    result = _process_family_message_event(
        raw, allowed_channel_ids={CHANNEL}, now=NOW, image_extract_on=False,
        image_extractor=extractor, slack_file_fetcher=fetcher,
    )
    assert fetcher.calls == [] and extractor.calls == []
    if subtype == "file_share":
        assert result.outcome == ListenerOutcome.IGNORED
    else:
        text_only = _process_family_message_event(
            _raw(subtype=None, files=()), allowed_channel_ids={CHANNEL}, now=NOW,
        )
        assert result.reply_text == text_only.reply_text


def test_thread_image_image_only_and_bot_file_are_not_read():
    extractor = FakeImageEventExtractor(empty_image_result(""))
    fetcher = _FakeFetcher(_png_bytes())
    for raw in (
        _raw(ts="2.0", thread_ts="1.0"),
        _raw(""),
        _raw(bot_id="B1"),
    ):
        result = _process_family_message_event(raw, **_on(extractor, fetcher))
        assert result.outcome == ListenerOutcome.IGNORED
    assert fetcher.calls == [] and extractor.calls == []


def test_images_in_life_notes_and_medic_channels_are_not_read():
    extractor = FakeImageEventExtractor(empty_image_result(""))
    fetcher = _FakeFetcher(_png_bytes())
    answered: list[str] = []

    def medic(message, *, correlation_id):
        answered.append(message.text)
        return ListenerResult(outcome=ListenerOutcome.REPLIED, correlation_id=correlation_id,
                              reply_text="medic")

    notes = InMemoryLifeNotesStore()
    common = {"life_notes_channel_id": "Clife", "life_notes_store": notes,
              "medic_channel_id": "Cmedic", "medic_responder": medic, **_on(extractor, fetcher)}
    for subtype in ("file_share", None):
        process_slack_message_event(_raw(channel="Clife", subtype=subtype), **common)
        process_slack_message_event(_raw(channel="Cmedic", subtype=subtype), **common)
    assert answered == ["加event"]  # plain medic message only; routed before any file path
    assert [n.raw_text for n in notes.list_recent()] == ["加event"]
    assert fetcher.calls == [] and extractor.calls == []


# --- review additions: live extractor shape and 33a reuse (M1–M7, W1–W2, I1) ---


def _v6(day: str, title: str, clock: str | None, **over) -> dict:
    item = {
        "title": title, "start": f"{day}T{clock}:00+08:00" if clock else None,
        "end": None, "all_day": False, "location": None, "participants": [],
        "missing_fields": [] if clock else ["time"], "series": None, "date": day,
        "stated_weekday": None, "source_text": None,
    }
    item.update(over)
    return item


def _events(*items: dict, intent: str = "create_event") -> dict:
    return {"intent_type": intent, "events": list(items), "over_cap": False,
            "confidence": "medium"}


def _vision(monkeypatch, *outcomes) -> tuple[list[dict], list[dict]]:
    """Fake xAI client: each create() takes the next payload or raises the next error."""
    clients: list[dict] = []
    requests: list[dict] = []
    queue = list(outcomes)

    def create(**kwargs):
        requests.append(kwargs)
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(outcome)),
                                     finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=900, completion_tokens=120),
        )

    def client(**kwargs):
        clients.append(kwargs)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setattr("openai.OpenAI", client)
    return clients, requests


def _extractor(**over) -> XaiVisionEventExtractor:
    return XaiVisionEventExtractor(api_key="fake-key", **{"model": "vision-a", **over})


def _image(index: int = 1):
    return prepare_image(_png_bytes(), source_index=index, declared_mime="image/png")


def test_vision_request_shape(monkeypatch):
    clients, requests = _vision(monkeypatch, _events(_v6("2026-11-06", "游泳", "15:30")))
    image = _image()
    extractor = _extractor()
    extractor.extract([image], caption="加event 只要 Cedric 嘅", now=NOW)
    assert clients == [{"api_key": "fake-key", "base_url": DEFAULT_BASE_URL,
                        "timeout": 15.0, "max_retries": 0}]
    (request,) = requests
    assert (request["model"], request["temperature"], request["max_tokens"]) == (
        "vision-a", 0, 2048)
    fmt = request["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] is CREATE_SCHEMA_V6
    system, user = request["messages"]
    prompt = REPO / "src/cec_vivisystem/prompts/create_event_image.v1.txt"
    assert system == {"role": "system", "content": prompt.read_text(encoding="utf-8")}
    assert extractor.prompt_version == "create_event_image.v1.txt"
    text_part, image_part = user["content"]
    assert json.loads(text_part["text"]) == {"now": NOW.isoformat(),
                                             "caption": "加event 只要 Cedric 嘅"}
    assert image_part["type"] == "image_url" and image_part["image_url"]["detail"] == "high"
    url = image_part["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == image.data


def test_vision_fails_over_once_then_replies_unavailable(monkeypatch):
    _, requests = _vision(
        monkeypatch, RuntimeError("primary down"), _events(_v6("2026-11-06", "游泳", "15:30")),
    )
    extractor = _extractor(fallback_model="vision-b")
    result = extractor.extract([_image()], caption="加event", now=NOW)
    assert [r["model"] for r in requests] == ["vision-a", "vision-b"]
    assert result.intent_type == IntentType.CREATE_EVENT

    _, requests = _vision(monkeypatch, RuntimeError("a down"), RuntimeError("b down"))
    store = InMemoryConfirmationStore()
    reply = _process_family_message_event(_raw(), confirmation_store=store, **_on(extractor))
    assert reply.reply_text.startswith("Event understanding is temporarily unavailable")
    assert len(requests) == 2 and store.list_all() == []


def test_vision_items_carry_the_image_index_and_several_images_use_one_call(monkeypatch):
    payload = _events(_v6("2026-11-06", "游泳", "15:30"), _v6("2026-11-07", "鋼琴", "10:00"),
                      _v6("2026-11-08", "畫畫", "09:00"))
    _, requests = _vision(monkeypatch, payload, payload)
    one = _extractor().extract([_image(1)], caption="加event", now=NOW)
    assert [(i.index, i.image_index) for i in one.batch] == [(1, 1), (2, 1), (3, 1)]
    two = _extractor().extract([_image(1), _image(2)], caption="加event", now=NOW)
    assert len(requests) == 2  # one model call per message
    parts = requests[1]["messages"][1]["content"]
    assert [p["type"] for p in parts] == ["text", "image_url", "image_url"]
    assert [(i.index, i.image_index) for i in two.batch] == [(1, None), (2, None), (3, None)]


def test_vision_single_item_proposal_has_the_header_and_no_wording_note(monkeypatch):
    _vision(monkeypatch, _events(_v6("2026-11-06", "游泳", "15:30")))
    store = InMemoryConfirmationStore()
    result = _process_family_message_event(_raw(), confirmation_store=store, **_on(_extractor()))
    text = result.reply_text
    assert text.startswith(IMAGE_PROPOSAL_HEADER + "\nPlease confirm this calendar create proposal:")
    assert "• Start: 2026-11-06T15:30:00+08:00" in text
    assert "less common wording" not in text
    assert len(store.list_pending()) == 1


def test_vision_weekday_series_proposal_has_the_header(monkeypatch):
    series = {"weekdays": ["SA"], "range_start": "2026-11-07", "range_end": "2026-11-28",
              "start_time": "10:00", "end_time": "11:00"}
    _vision(monkeypatch, _events(_v6("2026-11-07", "游泳班", "10:00",
                                     end="2026-11-07T11:00:00+08:00", series=series)))
    store = InMemoryConfirmationStore()
    result = _process_family_message_event(_raw(), confirmation_store=store, **_on(_extractor()))
    assert result.reply_text.startswith(IMAGE_PROPOSAL_HEADER + "\n請確認以下重複活動提案")
    assert len(store.list_pending()) == 1


@pytest.mark.parametrize("intent", ["unknown", "create_event", "needs_clarification"])
def test_vision_no_items_gives_the_image_reply_and_no_confirmation(monkeypatch, intent):
    _vision(monkeypatch, _events(intent=intent))
    store = InMemoryConfirmationStore()
    result = _process_family_message_event(_raw(), confirmation_store=store, **_on(_extractor()))
    assert result.reply_text == REPLY_NO_EVENTS
    assert store.list_all() == []


def test_vision_cancelled_and_wrong_weekday_items_are_listed_not_included(monkeypatch):
    _vision(monkeypatch, _events(
        _v6("2026-11-06", "游泳", "15:30", stated_weekday="FR"),
        # A "TBC" entry has no clock: it must not become the whole-list time question.
        _v6("2026-11-07", "鋼琴", None, missing_fields=["time", "unconfirmed"]),
        _v6("2026-11-09", "畫畫", "09:00", stated_weekday="TU"),  # 2026-11-09 is a Monday
    ))
    store = InMemoryConfirmationStore()
    result = _process_family_message_event(_raw(), confirmation_store=store, **_on(_extractor()))
    text = result.reply_text
    assert text.startswith(IMAGE_PROPOSAL_HEADER + "\n請確認以下 1 個活動")
    assert "1. 2026-11-06（五）15:30–16:30 游泳" in text
    assert "「鋼琴」— 未確認／已取消 / unconfirmed or cancelled" in text
    assert "「畫畫」— 星期幾同日期唔夾 / weekday does not match the date" in text
    (conf,) = store.list_pending()
    assert [item.index for item in conf.parse_result.batch] == [1]


def test_yes_on_an_image_list_writes_each_item_only_after_yes(monkeypatch):
    _vision(monkeypatch, _events(_v6("2026-11-06", "游泳", "15:30"),
                                 _v6("2026-11-07", "鋼琴", "10:00"),
                                 _v6("2026-11-08", "畫畫", "09:00")))
    store, audit, calendar = (InMemoryConfirmationStore(), InMemoryCalendarAuditStore(),
                              FakeCalendarClient())
    kwargs = _on(_extractor(), confirmation_store=store, calendar_client=calendar,
                 calendar_audit_store=audit)
    proposal = _process_family_message_event(_raw(), **kwargs)
    assert proposal.reply_text.startswith(IMAGE_PROPOSAL_HEADER + "\n請確認以下 3 個活動")
    assert calendar.calls == []
    edited = _process_family_message_event(
        _raw("唔要 2", subtype=None, files=(), ts="2.0", thread_ts="1.0"), **kwargs)
    assert edited.reply_text.startswith(IMAGE_PROPOSAL_HEADER + "\n請確認以下 2 個活動")
    assert calendar.calls == []
    ack = _process_family_message_event(
        _raw("yes", subtype=None, files=(), ts="3.0", thread_ts="1.0"), **kwargs)
    assert ack.reply_text.startswith("已確認。已建立 2 個活動")
    assert [draft.summary for draft in calendar.calls] == ["游泳", "畫畫"]


def test_same_image_message_delivered_twice_reuses_one_confirmation(monkeypatch):
    payload = _events(_v6("2026-11-06", "游泳", "15:30"), _v6("2026-11-07", "鋼琴", "10:00"))
    _vision(monkeypatch, payload, _events(_v6("2026-11-06", "另一個", "09:00")))
    store = InMemoryConfirmationStore()
    kwargs = _on(_extractor(), confirmation_store=store)
    first = _process_family_message_event(_raw(), **kwargs)
    second = _process_family_message_event(_raw(), **kwargs)
    assert second.reply_text == first.reply_text  # existing proposal; items unchanged
    assert len(store.list_all()) == 1


def test_load_image_extractor_needs_the_switch_and_a_key():
    assert load_image_extractor({"XAI_API_KEY": "fake-key"}) is None
    assert load_image_extractor({"CEC_IMAGE_EXTRACT": "xai"}) is None
    live = load_image_extractor({"CEC_IMAGE_EXTRACT": "xai", "XAI_API_KEY": "fake-key"})
    assert live is not None and live.model == DEFAULT_VISION_MODEL
    pinned = load_image_extractor({"CEC_IMAGE_EXTRACT": "XAI", "XAI_API_KEY": "fake-key",
                                   "LLM_VISION_MODEL": "v1", "LLM_VISION_FALLBACK_MODEL": "v1"})
    assert pinned is not None and (pinned.model, pinned.fallback_model) == ("v1", None)


def test_image_modules_do_not_import_the_calendar_path():
    code = (
        "import sys\n"
        "import cec_vivisystem.image_intake, cec_vivisystem.image_extract\n"
        "banned = {'calendar_writer', 'calendar_reader', 'calendar_delete', 'confirmation',\n"
        "          'listener'}\n"
        "loaded = {m.split('.')[1] for m in sys.modules if m.startswith('cec_vivisystem.')}\n"
        "print(','.join(sorted(loaded & banned)))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            check=True, cwd=REPO)
    assert result.stdout.strip() == ""
