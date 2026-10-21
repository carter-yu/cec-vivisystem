"""Google re-authorization for the existing Desktop OAuth client (ADR 0016).

Why: the consent screen stays in Testing, so the refresh token expires every
7 days and a parent re-authorizes about once a week, usually from an iPhone.
Google retired the copy-a-code (out-of-band) flow, and the device flow does not
allow Calendar scopes. The Desktop client still accepts a loopback redirect, so
Google sends Safari to ``http://127.0.0.1:53682/?code=…&state=…``; nothing
listens there, Safari shows "cannot connect", and the parent pastes that
address back into the app. The app then swaps the code for tokens itself.

Contract:
- PKCE S256 plus a single-use ``state``; an attempt belongs to one session and
  lives in memory for 10 minutes.
- Scope is ``calendar.events`` only, ``access_type=offline`` and
  ``prompt=consent`` (Google returns a refresh token only with consent).
- The token exchange goes through an injectable ``TokenHttp``; tests never
  reach Google. The code, the verifier and the tokens are never logged,
  audited or rendered; errors carry a short category only.
- This module replaces the private consent helper script (not in this repo);
  any helper should call these functions instead of copying them.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from cec_vivisystem.calendar_writer import GOOGLE_SCOPES

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = GOOGLE_SCOPES[0]
# Any loopback port is valid for a Desktop client. Nothing listens here: the
# browser's "cannot connect" page is where the parent copies the address.
REDIRECT_URI = "http://127.0.0.1:53682/"
ATTEMPT_TTL = timedelta(minutes=10)
EXCHANGE_TIMEOUT_S = 15.0
_TOKEN_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._/-~+="
)


class OAuthError(Exception):
    """A failed step. ``kind`` selects the STE message; never holds secrets."""

    def __init__(self, kind: str, detail: str | None = None) -> None:
        super().__init__(kind)
        self.kind = kind
        self.detail = detail


def new_verifier() -> str:
    return secrets.token_urlsafe(64)[:86]


def challenge_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


@dataclass(slots=True)
class OAuthAttempt:
    state: str
    verifier: str = field(repr=False)
    created_at: datetime
    user: str
    redirect_uri: str = REDIRECT_URI

    def expired(self, now: datetime) -> bool:
        return now - self.created_at > ATTEMPT_TTL

    @property
    def expires_at(self) -> datetime:
        return self.created_at + ATTEMPT_TTL


class OAuthAttempts:
    """In-memory attempts keyed by session; one per session, single use."""

    def __init__(self) -> None:
        self._by_session: dict[str, OAuthAttempt] = {}
        self._lock = threading.Lock()

    def start(self, session_key: str, *, user: str, now: datetime) -> OAuthAttempt:
        attempt = OAuthAttempt(
            state=secrets.token_urlsafe(24),
            verifier=new_verifier(),
            created_at=now,
            user=user,
        )
        with self._lock:
            self._by_session[session_key] = attempt
        return attempt

    def get(self, session_key: str) -> OAuthAttempt | None:
        with self._lock:
            return self._by_session.get(session_key)

    def discard(self, session_key: str) -> None:
        with self._lock:
            self._by_session.pop(session_key, None)


def consent_url(client_id: str, attempt: OAuthAttempt) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": attempt.redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "code_challenge": challenge_s256(attempt.verifier),
        "code_challenge_method": "S256",
        "state": attempt.state,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def parse_redirect(pasted: str) -> dict[str, str]:
    """Query parameters from the pasted address (first value of each)."""
    text = (pasted or "").strip()
    if not text or len(text) > 4096:
        raise OAuthError("bad_address")
    query = urllib.parse.urlsplit(text).query if "://" in text else text.lstrip("?")
    params = {key: values[0] for key, values in urllib.parse.parse_qs(query).items() if values}
    if not params:
        raise OAuthError("bad_address")
    return params


def check_redirect(
    params: Mapping[str, str], attempt: OAuthAttempt | None, *, now: datetime
) -> str:
    """Return the authorization code, or raise ``OAuthError`` with a kind."""
    if attempt is None:
        raise OAuthError("no_attempt")
    if attempt.expired(now):
        raise OAuthError("expired")
    state = params.get("state", "")
    if not secrets.compare_digest(state.encode(), attempt.state.encode()):
        raise OAuthError("state")
    error = params.get("error")
    if error:
        raise OAuthError("denied", detail=error[:40])
    code = params.get("code", "")
    if not code:
        raise OAuthError("no_code")
    scope = params.get("scope")
    if scope is not None and SCOPE not in scope.split():
        raise OAuthError("scope")
    return code


class TokenHttp(Protocol):
    def post_form(
        self, url: str, data: Mapping[str, str], *, timeout: float
    ) -> tuple[int, dict]: ...


class UrllibTokenHttp:
    """Stdlib HTTPS POST; returns ``(status, json_body)``. Never logs the body."""

    def post_form(self, url: str, data: Mapping[str, str], *, timeout: float) -> tuple[int, dict]:
        body = urllib.parse.urlencode(dict(data)).encode("ascii")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            payload = {}
        return status, payload if isinstance(payload, dict) else {}


@dataclass(frozen=True, slots=True)
class TokenGrant:
    refresh_token: str = field(repr=False)
    scope: str
    refresh_expires_in: int | None = None


def exchange_code(
    http: TokenHttp,
    *,
    client_id: str,
    client_secret: str,
    code: str,
    attempt: OAuthAttempt,
    timeout: float = EXCHANGE_TIMEOUT_S,
) -> TokenGrant:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": attempt.redirect_uri,
        "code_verifier": attempt.verifier,
    }
    try:
        status, payload = http.post_form(TOKEN_URL, data, timeout=timeout)
    except (OSError, TimeoutError) as exc:
        raise OAuthError("network", detail=type(exc).__name__) from exc
    if status != 200:
        error = payload.get("error") if isinstance(payload.get("error"), str) else None
        raise OAuthError("exchange", detail=(error or f"http_{status}")[:40])
    token = payload.get("refresh_token")
    if not isinstance(token, str) or not token or not set(token) <= _TOKEN_CHARS:
        raise OAuthError("no_refresh")
    scope = payload.get("scope") if isinstance(payload.get("scope"), str) else SCOPE
    if SCOPE not in scope.split():
        raise OAuthError("scope")
    expires_in = payload.get("refresh_token_expires_in")
    return TokenGrant(
        refresh_token=token,
        scope=scope,
        refresh_expires_in=expires_in if isinstance(expires_in, int) else None,
    )


def issued_at_value(now: datetime) -> str:
    """HKT ISO value for ``GOOGLE_REFRESH_TOKEN_ISSUED_AT``."""
    return now.astimezone(FAMILY_TZ).isoformat(timespec="seconds")


ClientFactory = Callable[[object], object]
