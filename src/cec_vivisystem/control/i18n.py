"""Copy catalog and HKT formatting for the control app (Phase 31).

Why: every screen exists in English (ASD-STE100 style, the default) and
Traditional Chinese (HK). Text lives in ``copy/en.json`` and
``copy/zh-Hant-HK.json`` so tests can enforce key parity, no empty strings,
short English sentences and no Simplified characters (ground rule 9).

Contract: templates and notices take text only from the catalog. Never add a
Simplified catalog or a Simplified fallback.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from cec_vivisystem.status_messages import (
    bilingual,
    en_date,
    en_duration,
    tc_date,
    tc_duration,
)

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
COPY_DIR = Path(__file__).resolve().parent / "copy"
EN = "en"
TC = "zh-Hant-HK"
LANGS = (EN, TC)


@cache
def catalog(lang: str) -> dict[str, str]:
    name = lang if lang in LANGS else EN
    return json.loads((COPY_DIR / f"{name}.json").read_text(encoding="utf-8"))


def text(lang: str, key: str, **values: Any) -> str:
    template = catalog(lang).get(key)
    if template is None:
        template = catalog(EN).get(key, key)
    return template.format_map(values) if values else template


def both(key: str, **values: Any) -> str:
    """English block, blank line, Traditional Chinese block (status channel).

    A value may be a ``{lang: text}`` mapping when it differs by language
    (a channel name or a date); plain values are shared.
    """

    def pick(lang: str) -> dict[str, Any]:
        return {k: (v.get(lang, v.get(EN)) if isinstance(v, dict) else v) for k, v in values.items()}

    return bilingual(text(EN, key, **pick(EN)), text(TC, key, **pick(TC)))


def per_lang(key: str | None = None, *, day: date | None = None) -> dict[str, str]:
    """``{lang: text}`` for ``both``: a catalog entry or a formatted date."""
    if day is not None:
        return {lang: fmt_date(lang, day) for lang in LANGS}
    assert key is not None
    return {lang: text(lang, key) for lang in LANGS}


def local(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=FAMILY_TZ)
    return value.astimezone(FAMILY_TZ)


def fmt_date(lang: str, day: date) -> str:
    return tc_date(day) if lang == TC else en_date(day)


def fmt_long_date(lang: str, day: date) -> str:
    if lang == TC:
        return f"{day.year}年{tc_date(day)}"
    return f"{en_date(day)} {day.year}"


def fmt_clock(value: datetime, *, seconds: bool = False) -> str:
    return f"{local(value):%H:%M:%S}" if seconds else f"{local(value):%H:%M}"


def fmt_when(lang: str, value: datetime, *, now: datetime) -> str:
    """``14:05`` today, else ``Tue 13 Oct 14:05`` / ``10月13日（星期二）14:05``."""
    moment = local(value)
    if moment.date() == local(now).date():
        return fmt_clock(moment)
    sep = "" if lang == TC else " "
    return f"{fmt_date(lang, moment.date())}{sep}{fmt_clock(moment)}"


def fmt_duration(lang: str, seconds: float) -> str:
    return tc_duration(seconds) if lang == TC else en_duration(seconds)


def day_unit(lang: str, days: int) -> str:
    return text(lang, "unit.day" if abs(days) == 1 else "unit.days")
