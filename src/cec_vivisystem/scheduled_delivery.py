"""Conservative scheduled delivery: reserve, post once, retain the Slack handle.

Only operational marker stores use these helpers. Callers must serialize runs;
atomic files do not provide a cross-process or Slack transaction (ADR 0009).
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from cec_vivisystem.logging import get_logger
from cec_vivisystem.models import SlackPostReceipt
from cec_vivisystem.storage import atomic_write_text

DeliveryKey = tuple[str | date, ...]
logger = get_logger(__name__)


class ReconciliationRequired(RuntimeError):
    """An existing attempt cannot safely be posted automatically."""


class DeliveryStore(Protocol):
    def read_delivery(self, key: DeliveryKey) -> dict | None: ...

    def write_delivery(self, key: DeliveryKey, payload: dict) -> None: ...


class JsonDeliveryStore:
    """Mixin for existing date/occurrence marker paths and retention."""

    def _path(self, *key: str | date) -> Path:
        raise NotImplementedError

    def read_delivery(self, key: DeliveryKey) -> dict | None:
        try:
            payload = json.loads(self._path(*key).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if not isinstance(payload, dict):
            raise ReconciliationRequired(
                "Invalid delivery marker; inspect before retry"
            )
        return payload

    def write_delivery(self, key: DeliveryKey, payload: dict) -> None:
        atomic_write_text(
            self._path(*key), json.dumps(payload, ensure_ascii=False, indent=2)
        )


class MemoryDeliveryStore:
    """Mixin sharing the existing in-memory marker dictionary."""

    def read_delivery(self, key: DeliveryKey) -> dict | None:
        return self._posted.get(key[0] if len(key) == 1 else key)

    def write_delivery(self, key: DeliveryKey, payload: dict) -> None:
        self._posted[key[0] if len(key) == 1 else key] = dict(payload)


def has_completed_delivery(store: DeliveryStore, key: DeliveryKey) -> bool:
    """Pending/damaged markers reach the guarded delivery boundary, never skip."""
    try:
        row = store.read_delivery(key)
        if row is None or row.get("status", "posted") != "posted":
            return False
        posted_at = row.get("posted_at")
        if not isinstance(posted_at, (str, datetime)):
            return False
        if isinstance(posted_at, str):
            datetime.fromisoformat(posted_at)
        if "status" in row:
            return all(
                isinstance(row.get(field), str) and row[field].strip()
                for field in ("slack_channel_id", "slack_ts")
            )
        return True
    except (OSError, ValueError, ReconciliationRequired):
        return False


class ScheduledPoster(Protocol):
    def post(self, *, channel_id: str, text: str) -> SlackPostReceipt: ...


def deliver_scheduled(
    *,
    store: DeliveryStore,
    records: list[tuple[DeliveryKey, dict]],
    poster: ScheduledPoster,
    channel_id: str,
    text: str,
    now: datetime,
    correlation_id: str,
) -> None:
    """Reserve the entire batch before I/O; any uncertainty blocks later runs."""
    attempt_id = str(uuid.uuid4())
    receipt = None
    try:
        # Check every member before mutation, including partial earlier batches.
        for key, _metadata in records:
            if store.read_delivery(key) is not None:
                raise ReconciliationRequired(
                    "Existing delivery attempt; inspect Slack and markers before retry"
                )
        batch_keys = [[str(part) for part in key] for key, _ in records]
        pending = []
        for key, metadata in records:
            payload = {
                **metadata,
                "status": "pending",
                "attempt_id": attempt_id,
                "attempted_at": now.isoformat(),
                "channel_id": channel_id,
                "batch_keys": batch_keys,
            }
            store.write_delivery(key, payload)
            pending.append((key, payload))
        receipt = poster.post(channel_id=channel_id, text=text)
        if (
            not isinstance(receipt, SlackPostReceipt)
            or not isinstance(receipt.channel_id, str)
            or not receipt.channel_id.strip()
            or not isinstance(receipt.ts, str)
            or not receipt.ts.strip()
        ):
            raise ReconciliationRequired("Slack response missing message handle")
        for key, payload in pending:
            store.write_delivery(
                key,
                {
                    **payload,
                    "status": "posted",
                    "posted_at": now.isoformat(),
                    "slack_channel_id": receipt.channel_id,
                    "slack_ts": receipt.ts,
                },
            )
    except Exception:
        # No bodies or credentials. A returned handle can aid manual recovery
        # even when the final marker could not be persisted.
        logger.error(
            "scheduled_delivery_reconciliation_required",
            component="scheduled_delivery",
            correlation_id=correlation_id,
            outcome="failure",
            attempt_id=attempt_id,
            channel_id=channel_id,
            slack_channel_id=(
                receipt.channel_id if isinstance(receipt, SlackPostReceipt) else None
            ),
            slack_ts=(receipt.ts if isinstance(receipt, SlackPostReceipt) else None),
        )
        raise
