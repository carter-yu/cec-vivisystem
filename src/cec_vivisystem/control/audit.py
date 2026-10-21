"""Control-app audit trail (Phase 31; class B, 90 days).

Why: two people share one admin surface. Every sign-in, sign-out and action is
recorded so either parent can see who changed what, and when.

Contract: append-only JSON lines in ``data/control/audit/YYYY-MM.jsonl`` with
``at``, ``actor``, ``action``, ``target``, ``outcome``, ``request_id``,
``remote_addr``, a short user agent and, for channel edits, the old and new
channel IDs. A record never contains a passphrase, token, OAuth code, session
ID or ``.env`` secret; ``record`` refuses such keys. Monthly files whose last
day is older than 90 days are purged on start and once per day. A failed
append is logged and never blocks the action it describes.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from cec_vivisystem.logging import get_logger

logger = get_logger(__name__)

COMPONENT = "control"
FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
AUDIT_RETENTION = timedelta(days=90)
_MONTH_FILE = re.compile(r"^(\d{4})-(\d{2})\.jsonl$")
_EXTRA_KEYS = frozenset({"old", "new", "detail", "role", "reason"})
_UA_MAX = 40

Clock = Callable[[], datetime]


def short_user_agent(raw: str | None) -> str:
    """``iPhone · Safari``: device and browser family only, never the full string."""
    text = raw or ""
    device = "other"
    for needle, name in (
        ("iPhone", "iPhone"),
        ("iPad", "iPad"),
        ("Android", "Android"),
        ("Macintosh", "Mac"),
        ("Windows", "Windows"),
        ("Linux", "Linux"),
    ):
        if needle in text:
            device = name
            break
    browser = "other"
    for needle, name in (
        ("Edg/", "Edge"),
        ("Firefox/", "Firefox"),
        ("CriOS/", "Chrome"),
        ("Chrome/", "Chrome"),
        ("Safari/", "Safari"),
        ("python-httpx", "test client"),
        ("testclient", "test client"),
    ):
        if needle in text:
            browser = name
            break
    return f"{device} · {browser}"[:_UA_MAX]


class AuditLog:
    def __init__(self, root: Path, *, clock: Clock) -> None:
        self.root = Path(root)
        self._clock = clock
        self._lock = threading.Lock()

    def _path(self, day: date) -> Path:
        return self.root / f"{day.year:04d}-{day.month:02d}.jsonl"

    def record(
        self,
        *,
        actor: str,
        action: str,
        outcome: str,
        target: str | None = None,
        request_id: str | None = None,
        remote_addr: str | None = None,
        user_agent: str | None = None,
        **extra: Any,
    ) -> bool:
        unknown = set(extra) - _EXTRA_KEYS
        if unknown:
            # A new field must be added to the allowlist on purpose, after a
            # review that it can never carry a secret.
            raise ValueError(f"audit field not allowed: {sorted(unknown)}")
        now = self._clock().astimezone(FAMILY_TZ)
        line: dict[str, Any] = {
            "at": now.isoformat(timespec="seconds"),
            "actor": actor,
            "action": action,
            "target": target,
            "outcome": outcome,
            "request_id": request_id,
            "remote_addr": remote_addr,
            "user_agent": short_user_agent(user_agent),
        }
        line.update({k: v for k, v in extra.items() if v is not None})
        try:
            with self._lock:
                self.root.mkdir(parents=True, exist_ok=True)
                with self._path(now.date()).open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as exc:
            logger.error(
                "control_audit_write_failed",
                component=COMPONENT,
                outcome="failure",
                action=action,
                error_type=type(exc).__name__,
            )
            return False
        return True

    def recent(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Newest first from the current and previous monthly files."""
        today = self._clock().astimezone(FAMILY_TZ).date()
        first = today.replace(day=1)
        previous = (first - timedelta(days=1)).replace(day=1)
        rows: list[dict[str, Any]] = []
        for day in (previous, first):
            try:
                lines = self._path(day).read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                continue
            except OSError:
                continue
            for text in lines:
                try:
                    row = json.loads(text)
                except ValueError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("at"), str):
                    rows.append(row)
        # Lines are in append order; reversing first keeps newest-first order
        # for records written in the same second (the sort is stable).
        rows.reverse()
        rows.sort(key=lambda row: row["at"], reverse=True)
        return rows[:limit]

    def purge(self, *, now: datetime | None = None) -> int:
        """Delete monthly files whose last day is older than 90 days."""
        current = (now or self._clock()).astimezone(FAMILY_TZ).date()
        cutoff = current - AUDIT_RETENTION
        deleted = 0
        try:
            paths = list(self.root.glob("*.jsonl"))
        except OSError:
            return 0
        for path in paths:
            match = _MONTH_FILE.match(path.name)
            if match is None:
                continue
            year, month = int(match.group(1)), int(match.group(2))
            if not 1 <= month <= 12:
                continue
            next_month = date(year + (month == 12), month % 12 + 1, 1)
            if next_month - timedelta(days=1) < cutoff:
                try:
                    path.unlink(missing_ok=True)
                    deleted += 1
                except OSError:
                    continue
        if deleted:
            logger.info(
                "control_audit_purged",
                component=COMPONENT,
                outcome="success",
                deleted=deleted,
            )
        return deleted
