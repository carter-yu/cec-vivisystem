"""Read ``.env`` safely and change a few allowlisted keys (Phase 31b).

Why: the control app shows configuration and lets an admin change a channel or
the stuck-retry count, and lets either parent store a new Google refresh
token. ``.env`` also holds every secret, so mistakes here are expensive.

Contract:
- Reads go through ``dotenv_values``. Templates get secret *presence* only
  (``SECRET_KEYS`` → "Set" / "Not set"), never a value, never a partial mask.
- Writes change only ``WRITABLE_KEYS``. Every other line stays byte-identical;
  a missing key is appended. Values are validated (no newline, quote, ``#``
  or space), the old file is copied to ``data/control/env-backups/`` (mode
  0600, last 3, at most 30 days), and the new file replaces the old one
  atomically with its mode kept. If the write fails, the original file stays
  intact and older backups are not rotated.
- The listener and other agents read ``.env`` at their own start; a change
  takes effect after a restart (the checker and the 07:00/10:00 jobs: next run).
Do not widen ``WRITABLE_KEYS`` to a secret other than the Google token pair.
"""

from __future__ import annotations

import os
import re
import secrets
import tempfile
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path

SECRET_KEYS = (
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "SLACK_SIGNING_SECRET",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REFRESH_TOKEN",
    "XAI_API_KEY",
    "LLM_API_KEY",
)
CHANNEL_KEYS = (
    "SLACK_FAMILY_PLANS_CHANNEL_ID",
    "SLACK_LIFE_NOTES_CHANNEL_ID",
    "CEC_HEALTH_ALERT_CHANNEL_ID",
)
STUCK_RETRIES_KEY = "CEC_LISTENER_STUCK_RETRIES"
TOKEN_KEYS = ("GOOGLE_REFRESH_TOKEN", "GOOGLE_REFRESH_TOKEN_ISSUED_AT")
WRITABLE_KEYS = frozenset((*CHANNEL_KEYS, STUCK_RETRIES_KEY, *TOKEN_KEYS))
CHANNEL_ID = re.compile(r"^[CG][A-Z0-9]{8,}$")
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9._/:+\-~=]*$")
BACKUP_KEEP = 3
BACKUP_MAX_AGE = timedelta(days=30)
_BACKUP_NAME = re.compile(r"^env-\d{8}T\d{6}-[0-9a-f]{6}\.bak$")

Writer = Callable[[Path, bytes, int], None]
_write_lock = threading.Lock()


class EnvWriteError(Exception):
    """A rejected key/value or a failed write (never contains a value)."""


def read_env(path: Path) -> dict[str, str]:
    from dotenv import dotenv_values

    if not path.is_file():
        return {}
    return {key: (value or "") for key, value in dotenv_values(path).items()}


def secret_presence(values: Mapping[str, str]) -> dict[str, bool]:
    return {key: bool((values.get(key) or "").strip()) for key in SECRET_KEYS}


def valid_channel_id(value: str) -> bool:
    return bool(CHANNEL_ID.match(value or ""))


def _atomic_write_bytes(path: Path, data: bytes, mode: int) -> None:
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def apply_updates(original: bytes, updates: Mapping[str, str]) -> bytes:
    """Replace each key's active assignment lines; append missing keys."""
    text = original.decode("utf-8", errors="surrogateescape")
    lines = text.splitlines(keepends=True)
    done: set[str] = set()
    for index, line in enumerate(lines):
        for key, value in updates.items():
            match = re.match(rf"^(\s*)(export\s+)?{re.escape(key)}\s*=", line)
            if match is None:
                continue
            body = line.rstrip("\r\n")
            ending = line[len(body) :]
            lines[index] = f"{match.group(1)}{match.group(2) or ''}{key}={value}{ending}"
            done.add(key)
    missing = [key for key in updates if key not in done]
    if missing:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += "\n"
        lines.extend(f"{key}={updates[key]}\n" for key in missing)
    return "".join(lines).encode("utf-8", errors="surrogateescape")


def _write_backup(backup_dir: Path, original: bytes, now: datetime) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(backup_dir, 0o700)
    path = backup_dir / f"env-{now:%Y%m%dT%H%M%S}-{secrets.token_hex(3)}.bak"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def list_backups(backup_dir: Path) -> list[Path]:
    try:
        return sorted(p for p in backup_dir.iterdir() if _BACKUP_NAME.match(p.name))
    except OSError:
        return []


def prune_backups(backup_dir: Path, *, now: datetime, keep: int = BACKUP_KEEP) -> int:
    """Keep the newest ``keep`` backups younger than 30 days (class C)."""
    backups = list_backups(backup_dir)
    deleted = 0
    for index, path in enumerate(reversed(backups)):
        stamp = path.name[4:19]
        try:
            made = datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=now.tzinfo)
        except ValueError:
            made = now
        if index >= keep or now - made > BACKUP_MAX_AGE:
            try:
                path.unlink(missing_ok=True)
                deleted += 1
            except OSError:
                continue
    return deleted


def update_env_file(
    path: Path,
    updates: Mapping[str, str],
    *,
    backup_dir: Path,
    now: datetime,
    writer: Writer | None = None,
) -> Path | None:
    """Write allowlisted keys; return the backup path (None for a new file)."""
    if not updates:
        raise EnvWriteError("No change")
    for key, value in updates.items():
        if key not in WRITABLE_KEYS:
            raise EnvWriteError(f"Key {key} cannot be changed here")
        if not isinstance(value, str) or not _SAFE_VALUE.match(value):
            raise EnvWriteError(f"Value for {key} has characters that are not allowed")
    with _write_lock:
        try:
            original = path.read_bytes()
            mode = path.stat().st_mode & 0o777
        except FileNotFoundError:
            original, mode = None, 0o600
        new = apply_updates(original or b"", updates)
        backup = _write_backup(backup_dir, original, now) if original is not None else None
        try:
            (writer or _atomic_write_bytes)(path, new, mode)
        except OSError:
            if backup is not None:
                backup.unlink(missing_ok=True)
            raise
        prune_backups(backup_dir, now=now)
    return backup
