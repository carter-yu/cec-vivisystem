"""Phase 33b image intake: trigger, filters, guarded Slack download, prepare.

Kill switch ``CEC_IMAGE_EXTRACT`` defaults to ``off``. When off, callers must
not invoke this module for live traffic (today's behaviour exactly). When
``xai``, only PNG/JPEG files attached with message text containing ``加event``
are fetched. Bytes stay in memory and are dropped after the model call.
"""

from __future__ import annotations

import io
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from cec_vivisystem.logging import get_logger
from cec_vivisystem.models import InboundFile, InboundMessage

COMPONENT = "image_intake"
logger = get_logger(__name__)

TRIGGER = "加event"
ALLOWED_MODES = frozenset({"xai"})
DEFAULT_MODE = "off"
MAX_FILES = 3
MAX_BYTES = 10 * 1024 * 1024
MAX_LONG_SIDE = 2048
MAX_MEGAPIXELS = 40
FETCH_TIMEOUT_S = 15.0
ALLOWED_HOST = "files.slack.com"
ALLOWED_MIMETYPES = frozenset({"image/png", "image/jpeg"})
ALLOWED_FILETYPES = frozenset({"png", "jpg", "jpeg"})

REPLY_FORMAT = (
    "只支援 PNG 或者 JPEG 圖片。/ Only PNG or JPEG images are supported.\n"
    "未有改動日曆。/ No calendar change was made."
)
REPLY_TOO_LARGE = (
    "張圖太大（上限 10 MB）。/ The image is too large (limit 10 MB).\n"
    "未有改動日曆。/ No calendar change was made."
)
REPLY_TOO_MANY = (
    "每次最多 3 張圖，請分開傳送。/ Send at most 3 images per message.\n"
    "未有改動日曆。/ No calendar change was made."
)
REPLY_DOWNLOAD = (
    "未能下載張圖，請再試一次。/ Could not download the image. Please try again.\n"
    "未有改動日曆。/ No calendar change was made."
)
REPLY_UNREADABLE = (
    "讀唔到呢張圖。/ Could not read this image.\n"
    "未有改動日曆。/ No calendar change was made."
)
REPLY_NO_EVENTS = (
    "張圖入面搵唔到有日期同時間嘅活動。/ No dated events found in the image.\n"
    "未有改動日曆。/ No calendar change was made."
)


@dataclass(frozen=True, slots=True)
class PreparedImage:
    """In-memory image ready for a vision call. Never written to disk."""

    data: bytes
    mimetype: str  # image/png or image/jpeg
    width: int
    height: int
    source_index: int  # 1-based index among accepted attachments


class ImageIntakeError(Exception):
    """User-visible intake failure. ``reply`` is Traditional Chinese + English."""

    def __init__(self, code: str, reply: str) -> None:
        super().__init__(code)
        self.code = code
        self.reply = reply


class SlackFileFetcher(Protocol):
    def fetch(self, url: str, *, size_hint: int | None = None) -> bytes: ...


def load_image_extract_mode(env: Mapping[str, str] | None = None) -> str:
    """``off`` (default) or ``xai``. Unknown values warn and stay off."""
    source = env if env is not None else os.environ
    raw = (source.get("CEC_IMAGE_EXTRACT") or "").strip().casefold()
    if not raw or raw == "off":
        return DEFAULT_MODE
    if raw in ALLOWED_MODES:
        return raw
    logger.warning(
        "image_extract_mode_unknown",
        component=COMPONENT,
        outcome="partial",
        value_length=len(raw),
    )
    return DEFAULT_MODE


def image_extract_enabled(env: Mapping[str, str] | None = None) -> bool:
    return load_image_extract_mode(env) != DEFAULT_MODE


def has_image_trigger(text: str) -> bool:
    return TRIGGER in (text or "")


def normalize_plans_file_event(raw: object) -> InboundMessage | None:
    """Map a plans Slack message that may carry files.

    Accepts plain messages and ``file_share``. Bot messages, other subtypes,
    thread replies and missing channel/user are skipped (``None``). Text may
    be empty here; the caller still requires ``加event`` before fetching.
    Does not replace ``normalize_slack_event`` (medic and life-notes keep that).
    """
    if not isinstance(raw, dict):
        return None
    if raw.get("type") not in (None, "message"):
        return None
    if raw.get("bot_id") or raw.get("bot_profile"):
        return None
    subtype = raw.get("subtype")
    if subtype not in (None, "file_share"):
        return None
    # v1: no images in threads
    thread_ts = raw.get("thread_ts") if isinstance(raw.get("thread_ts"), str) else None
    ts = raw.get("ts") if isinstance(raw.get("ts"), str) else None
    if thread_ts and ts and thread_ts != ts:
        return None
    channel = raw.get("channel")
    user = raw.get("user")
    text = raw.get("text") if isinstance(raw.get("text"), str) else ""
    if not isinstance(channel, str) or not channel:
        return None
    if not isinstance(user, str) or not user:
        return None
    files = tuple(_parse_file(entry) for entry in (raw.get("files") or [])
                  if isinstance(entry, dict))
    files = tuple(f for f in files if f is not None)
    event_id = raw.get("client_msg_id") or raw.get("event_ts") or raw.get("ts")
    if event_id is not None and not isinstance(event_id, str):
        event_id = str(event_id)
    import uuid
    return InboundMessage(
        text=text,
        channel_id=channel,
        user_id=user,
        slack_event_id=event_id,
        correlation_id=str(uuid.uuid4()),
        ts=ts,
        thread_ts=thread_ts,
        files=files,
    )


def _parse_file(entry: dict) -> InboundFile | None:
    file_id = entry.get("id")
    if not isinstance(file_id, str) or not file_id:
        return None
    size = entry.get("size")
    size_i = size if isinstance(size, int) else None
    return InboundFile(
        id=file_id,
        name=entry.get("name") if isinstance(entry.get("name"), str) else None,
        mimetype=entry.get("mimetype") if isinstance(entry.get("mimetype"), str) else None,
        filetype=entry.get("filetype") if isinstance(entry.get("filetype"), str) else None,
        size=size_i,
        url_private_download=(
            entry.get("url_private_download")
            if isinstance(entry.get("url_private_download"), str)
            else (entry.get("url_private") if isinstance(entry.get("url_private"), str) else None)
        ),
        mode=entry.get("mode") if isinstance(entry.get("mode"), str) else None,
        is_external=bool(entry.get("is_external")),
        file_access=(
            entry.get("file_access") if isinstance(entry.get("file_access"), str) else None
        ),
        original_w=entry.get("original_w") if isinstance(entry.get("original_w"), int) else None,
        original_h=entry.get("original_h") if isinstance(entry.get("original_h"), int) else None,
    )


def select_image_files(files: tuple[InboundFile, ...] | list[InboundFile]) -> list[InboundFile]:
    """Filter to hosted PNG/JPEG under the size cap. Raises ImageIntakeError."""
    if len(files) > MAX_FILES:
        raise ImageIntakeError("too_many", REPLY_TOO_MANY)
    accepted: list[InboundFile] = []
    for item in files:
        if item.is_external or item.mode == "external":
            continue
        if item.file_access == "check_file_info":
            continue
        mime = (item.mimetype or "").casefold()
        ftype = (item.filetype or "").casefold()
        if mime not in ALLOWED_MIMETYPES and ftype not in ALLOWED_FILETYPES:
            raise ImageIntakeError("format", REPLY_FORMAT)
        if item.size is not None and item.size > MAX_BYTES:
            raise ImageIntakeError("too_large", REPLY_TOO_LARGE)
        if not item.url_private_download:
            raise ImageIntakeError("download_failed", REPLY_DOWNLOAD)
        accepted.append(item)
    if not accepted and files:
        raise ImageIntakeError("format", REPLY_FORMAT)
    return accepted


def prepare_image(data: bytes, *, source_index: int, declared_mime: str | None = None) -> PreparedImage:
    """EXIF-orient, RGB, strip metadata, resize. Raises ImageIntakeError."""
    from PIL import Image, ImageOps

    if len(data) > MAX_BYTES:
        raise ImageIntakeError("too_large", REPLY_TOO_LARGE)
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            width, height = img.size
            if width * height > MAX_MEGAPIXELS * 1_000_000:
                raise ImageIntakeError("unreadable", REPLY_UNREADABLE)
            if max(width, height) > MAX_LONG_SIDE:
                img.thumbnail((MAX_LONG_SIDE, MAX_LONG_SIDE), Image.Resampling.LANCZOS)
                width, height = img.size
            rgb = img.convert("RGB")
            fmt = "JPEG"
            mime = "image/jpeg"
            if (declared_mime or "").casefold() == "image/png" or (
                img.format or ""
            ).upper() == "PNG":
                # Screenshots stay PNG (lossless text).
                fmt = "PNG"
                mime = "image/png"
                out = io.BytesIO()
                rgb.save(out, format=fmt, optimize=True)
            else:
                out = io.BytesIO()
                rgb.save(out, format=fmt, quality=85, optimize=True)
            prepared = out.getvalue()
    except ImageIntakeError:
        raise
    except Exception:  # noqa: BLE001 — any decode failure is unreadable
        raise ImageIntakeError("unreadable", REPLY_UNREADABLE) from None
    if len(prepared) > MAX_BYTES:
        raise ImageIntakeError("too_large", REPLY_TOO_LARGE)
    logger.info(
        "image_prepare_completed",
        component=COMPONENT,
        outcome="success",
        source_index=source_index,
        width=width,
        height=height,
        bytes_after=len(prepared),
        mimetype=mime,
    )
    return PreparedImage(
        data=prepared, mimetype=mime, width=width, height=height, source_index=source_index
    )


class StdlibSlackFileFetcher:
    """GET ``url_private_download`` with the bot token. Host-checked first."""

    def __init__(self, *, bot_token: str, timeout_s: float = FETCH_TIMEOUT_S) -> None:
        self._token = bot_token
        self._timeout_s = timeout_s

    def fetch(self, url: str, *, size_hint: int | None = None) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
            logger.warning(
                "image_fetch_refused_host",
                component=COMPONENT,
                outcome="failure",
                scheme=parsed.scheme,
                host_present=bool(parsed.hostname),
            )
            raise ImageIntakeError("download_failed", REPLY_DOWNLOAD)
        if size_hint is not None and size_hint > MAX_BYTES:
            raise ImageIntakeError("too_large", REPLY_TOO_LARGE)
        request = Request(
            url,
            headers={"Authorization": f"Bearer {self._token}"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self._timeout_s) as response:
                final = urlparse(response.geturl())
                if final.scheme != "https" or final.hostname != ALLOWED_HOST:
                    raise ImageIntakeError("download_failed", REPLY_DOWNLOAD)
                status = getattr(response, "status", None) or response.getcode()
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ImageIntakeError("too_large", REPLY_TOO_LARGE)
                    chunks.append(chunk)
        except ImageIntakeError:
            raise
        except HTTPError as exc:
            logger.error(
                "image_fetch_failed",
                component=COMPONENT,
                outcome="failure",
                http_status=exc.code,
                error_type=type(exc).__name__,
            )
            raise ImageIntakeError("download_failed", REPLY_DOWNLOAD) from None
        except (URLError, TimeoutError, OSError) as exc:
            logger.error(
                "image_fetch_failed",
                component=COMPONENT,
                outcome="failure",
                error_type=type(exc).__name__,
            )
            raise ImageIntakeError("download_failed", REPLY_DOWNLOAD) from None
        data = b"".join(chunks)
        logger.info(
            "image_fetch_completed",
            component=COMPONENT,
            outcome="success",
            http_status=status,
            bytes=len(data),
        )
        return data


def intake_images(
    message: InboundMessage,
    *,
    fetcher: SlackFileFetcher,
) -> list[PreparedImage]:
    """Filter, download and prepare every accepted attachment."""
    if not has_image_trigger(message.text):
        logger.info(
            "image_intake_skipped",
            component=COMPONENT,
            outcome="ignored",
            reason="no_trigger",
            file_count=len(message.files),
        )
        return []
    if not message.files:
        return []
    selected = select_image_files(message.files)
    logger.info(
        "image_intake_started",
        component=COMPONENT,
        outcome="success",
        file_count=len(message.files),
        accepted_count=len(selected),
        mimetypes=",".join(sorted({(f.mimetype or "?") for f in selected})) or None,
    )
    prepared: list[PreparedImage] = []
    for index, item in enumerate(selected, start=1):
        assert item.url_private_download is not None
        raw = fetcher.fetch(item.url_private_download, size_hint=item.size)
        prepared.append(prepare_image(raw, source_index=index, declared_mime=item.mimetype))
    return prepared
