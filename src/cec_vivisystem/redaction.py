"""Shared redaction of ID-like tokens before text leaves the Mini (Phase 32, ADR 0017).

Why: insurance documents and family questions carry policy, member and HKID
numbers. Anything sent to a cloud model, written into a knowledge tree or logged
must pass through ``redact_sensitive`` first.

Contract: returns ``(safe_text, redaction_count)``. Placeholders contain no
digits, so a second pass is a no-op (count 0). Money amounts with thousands
separators (``$1,560,000``), percentages, short counts and slash dates stay.

Do not log the matched values or "before" text; log the count only. Do not
widen the digit rule below 8 digits: benefit tables are full of 4–7 digit
amounts that must survive.
"""

from __future__ import annotations

import re

ID_PLACEHOLDER = "[ID-REDACTED]"
HKID_PLACEHOLDER = "[HKID-REDACTED]"
EMAIL_PLACEHOLDER = "[EMAIL-REDACTED]"
PHONE_PLACEHOLDER = "[PHONE-REDACTED]"

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# Label kept, value replaced: "Policy No.: GP-12345", "Member ID 12-3456-01".
_LABELLED_EN = re.compile(
    r"(?i)(\b(?:(?:policy|member|membership|certificate|cert|card|account|client|claim|"
    r"employee|staff|group|insured|plan)\s*(?:no\.?|number|num\.?|#|id)|id)\s*[:：#.]?\s*)"
    r"([A-Za-z0-9][A-Za-z0-9\-/]{3,})"
)
_LABELLED_ZH = re.compile(
    r"((?:保單|會員|成員|證書|保險卡|保健咭|保健卡|醫療卡|賬戶|帳戶|索償|員工|僱員|客戶|身份證)"
    r"(?:編號|號碼|號)\s*[:：]?\s*)([A-Za-z0-9][A-Za-z0-9\-/()（）]{3,})"
)
_HKID = re.compile(
    r"(?<![A-Za-z0-9])[A-Z]{1,2}\d{6}\s?(?:\(\s?[0-9A]\s?\)|（\s?[0-9A]\s?）|[0-9A])(?![A-Za-z0-9])"
)
# HK 8-digit numbers written with a separator, optionally with +852 / other codes.
_PHONE = re.compile(
    r"(?<![\d$,.])(?:\+\d{1,3}[\s-]?)?\d{4}[\s-]\d{4}(?![\d,])"
)
_INTL_PHONE = re.compile(r"(?<![\w$])\+\d{1,3}[\s-]?\d{2,4}(?:[\s-]?\d{2,4}){2,3}(?!\d)")
# Underscore is a word character, so \b would miss "0000000000_001".
_LONG_DIGITS = re.compile(r"(?<!\d)\d{8,}(?!\d)")


def redact_sensitive(text: str) -> tuple[str, int]:
    """Replace ID-like tokens; return the safe text and how many were replaced."""
    if not isinstance(text, str) or not text:
        return ("" if not isinstance(text, str) else text), 0
    count = 0

    def _sub(pattern: re.Pattern[str], placeholder: str, value: str) -> str:
        nonlocal count
        value, n = pattern.subn(placeholder, value)
        count += n
        return value

    def _labelled(pattern: re.Pattern[str], value: str) -> str:
        nonlocal count

        def repl(match: re.Match[str]) -> str:
            nonlocal count
            token = match.group(2)
            if not any(ch.isdigit() for ch in token):
                return match.group(0)
            count += 1
            return match.group(1) + ID_PLACEHOLDER

        return pattern.sub(repl, value)

    safe = _sub(_EMAIL, EMAIL_PLACEHOLDER, text)
    safe = _sub(_HKID, HKID_PLACEHOLDER, safe)
    safe = _labelled(_LABELLED_EN, safe)
    safe = _labelled(_LABELLED_ZH, safe)
    safe = _sub(_INTL_PHONE, PHONE_PLACEHOLDER, safe)
    safe = _sub(_PHONE, PHONE_PLACEHOLDER, safe)
    safe = _sub(_LONG_DIGITS, ID_PLACEHOLDER, safe)
    return safe, count
