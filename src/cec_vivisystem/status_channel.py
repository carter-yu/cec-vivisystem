"""System-status channel resolution (Phase 30, ADR 0014).

Operator and system messages (health alerts, daily status, Google token
reminders) go to ``CEC_HEALTH_ALERT_CHANNEL_ID`` (the private ``#vivi-status``
channel). Family posts (07:00 recap, 10:00 dates review, thread replies) stay
in the plans channel.

Contract: when the status channel is unset, fall back to the plans channel so
an operator message is never silently dropped, and log a WARNING every time so
the misconfiguration is visible. Do not remove the fallback to "keep
#family-plans clean": silence is worse than one misplaced notice.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from cec_vivisystem.logging import get_logger

logger = get_logger(__name__)

ENV_STATUS_CHANNEL = "CEC_HEALTH_ALERT_CHANNEL_ID"
ENV_PLANS_CHANNEL = "SLACK_FAMILY_PLANS_CHANNEL_ID"


def load_system_channel(
    env: Mapping[str, str] | None = None, *, component: str = "system"
) -> str:
    """Return the status channel, else the plans channel, else ``""``.

    Logs ``system_channel_fallback`` (WARNING) when the plans channel is used.
    Never logs token values.
    """
    source = env if env is not None else os.environ
    status = (source.get(ENV_STATUS_CHANNEL) or "").strip()
    if status:
        return status
    plans = (source.get(ENV_PLANS_CHANNEL) or "").strip()
    if plans:
        logger.warning(
            "system_channel_fallback",
            component=component,
            outcome="partial",
            missing=ENV_STATUS_CHANNEL,
            fallback=ENV_PLANS_CHANNEL,
        )
    return plans
