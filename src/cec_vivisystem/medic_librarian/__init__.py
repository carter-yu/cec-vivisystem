"""AIA Medic librarian: offline knowledge tree + #jeff-aia-medic Q&A (Phase 32).

Read ADR 0017 first. Build offline (``python -m cec_vivisystem.medic_librarian
build``), answer only from the redacted tree, and never touch Calendar writes
or confirmations from this package.
"""

from cec_vivisystem.medic_librarian.handler import (
    DEFAULT_MEDIC_CHANNEL_ID,
    MedicResponder,
    handle_medic_message,
    load_medic_responder,
    resolve_medic_channel_id,
)

__all__ = [
    "DEFAULT_MEDIC_CHANNEL_ID",
    "MedicResponder",
    "handle_medic_message",
    "load_medic_responder",
    "resolve_medic_channel_id",
]
