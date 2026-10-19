"""Control-app accounts, sign-in limits and sessions (Phase 31, ADR 0015).

Why: the app runs over HTTP on the home LAN, so guests on the Wi-Fi can reach
the sign-in page. Two accounts made by CLI on the Mini (no web sign-up),
stdlib scrypt hashes, a 5-failure lockout and short password re-entry for
sensitive actions keep a guessed or borrowed session from changing anything.

Contract:
- ``users.json`` holds names, roles, language and scrypt hashes only, mode 0600
  (local credentials under ground rule 13; never committed, never logged).
- Sessions and CSRF tokens live in memory: an app restart signs everyone out.
- Roles are enforced on the server for each route; the template only hides
  buttons. Do not add a role check that trusts a form field or a cookie value.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cec_vivisystem.storage import atomic_write_text

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
ROLES = (ROLE_ADMIN, ROLE_MEMBER)
LANGS = ("en", "zh-Hant-HK")
MIN_PASSPHRASE_CHARS = 12
MAX_PASSPHRASE_CHARS = 256
FAILURE_LIMIT = 5
LOCK_WINDOW = timedelta(minutes=15)
SESSION_IDLE = timedelta(days=7)
SESSION_ABSOLUTE = timedelta(days=30)
PASSWORD_REENTRY = timedelta(minutes=15)
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")

Clock = Callable[[], datetime]


class UserStoreError(Exception):
    """Users file problem (message never contains a hash or passphrase)."""


def validate_username(name: str) -> str:
    value = (name or "").strip().lower()
    if not _NAME.match(value):
        raise UserStoreError(
            "User name: 2-32 characters, a-z first, then a-z, 0-9, '-' or '_'"
        )
    return value


def validate_passphrase(passphrase: str) -> None:
    if len(passphrase) < MIN_PASSPHRASE_CHARS:
        raise UserStoreError(f"Passphrase: use {MIN_PASSPHRASE_CHARS} or more characters")
    if len(passphrase) > MAX_PASSPHRASE_CHARS:
        raise UserStoreError("Passphrase is too long")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _scrypt(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        passphrase.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=256 * 1024 * 1024,
        dklen=32,
    )


def hash_passphrase(passphrase: str, *, n: int = SCRYPT_N) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(passphrase, salt, n, SCRYPT_R, SCRYPT_P)
    return f"scrypt${n}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_passphrase(passphrase: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = encoded.split("$")
        if scheme != "scrypt":
            return False
        actual = _scrypt(passphrase, _unb64(salt), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, _unb64(digest))


@dataclass(frozen=True, slots=True)
class User:
    name: str
    role: str
    password_hash: str
    lang: str = "en"
    created_at: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


class UserStore:
    """``data/control/users.json``; re-read on every call (two accounts)."""

    def __init__(self, path: Path, *, scrypt_n: int = SCRYPT_N, clock: Clock | None = None) -> None:
        self.path = Path(path)
        self._n = scrypt_n
        self._clock = clock or (lambda: datetime.now(tz=FAMILY_TZ))
        self._lock = threading.Lock()
        # Verifying against a dummy hash keeps "no such user" as slow as a
        # wrong passphrase, so timing does not reveal account names.
        self._dummy = hash_passphrase(secrets.token_urlsafe(16), n=scrypt_n)

    def load(self) -> dict[str, User]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise UserStoreError(f"Users file is not readable ({type(exc).__name__})") from exc
        users = raw.get("users") if isinstance(raw, dict) else None
        if not isinstance(users, dict):
            raise UserStoreError("Users file has no 'users' object")
        result: dict[str, User] = {}
        for name, item in users.items():
            if not isinstance(item, dict):
                continue
            role, digest = item.get("role"), item.get("password_hash")
            if role not in ROLES or not isinstance(digest, str):
                continue
            lang = item.get("lang") if item.get("lang") in LANGS else "en"
            result[name] = User(
                name=name,
                role=role,
                password_hash=digest,
                lang=lang,
                created_at=str(item.get("created_at") or ""),
            )
        return result

    def get(self, name: str) -> User | None:
        try:
            return self.load().get(name)
        except UserStoreError:
            return None

    def _save(self, users: dict[str, User]) -> None:
        payload = {
            "version": 1,
            "users": {
                u.name: {
                    "role": u.role,
                    "password_hash": u.password_hash,
                    "lang": u.lang,
                    "created_at": u.created_at,
                }
                for u in sorted(users.values(), key=lambda item: item.name)
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        # atomic_write_text publishes a mkstemp sibling, which is already 0600.
        atomic_write_text(self.path, json.dumps(payload, indent=2) + "\n")
        os.chmod(self.path, 0o600)

    def add(self, name: str, role: str, passphrase: str) -> User:
        name = validate_username(name)
        if role not in ROLES:
            raise UserStoreError("Role must be admin or member")
        validate_passphrase(passphrase)
        with self._lock:
            users = self.load()
            if name in users:
                raise UserStoreError(f"User {name} exists; use passwd to change the passphrase")
            user = User(
                name=name,
                role=role,
                password_hash=hash_passphrase(passphrase, n=self._n),
                created_at=self._clock().isoformat(timespec="seconds"),
            )
            users[name] = user
            self._save(users)
        return user

    def set_password(self, name: str, passphrase: str) -> None:
        validate_passphrase(passphrase)
        with self._lock:
            users = self.load()
            if name not in users:
                raise UserStoreError(f"No user {name}")
            users[name] = replace(users[name], password_hash=hash_passphrase(passphrase, n=self._n))
            self._save(users)

    def remove(self, name: str) -> None:
        with self._lock:
            users = self.load()
            if users.pop(name, None) is None:
                raise UserStoreError(f"No user {name}")
            self._save(users)

    def set_lang(self, name: str, lang: str) -> None:
        if lang not in LANGS:
            raise UserStoreError("Unknown language")
        with self._lock:
            users = self.load()
            if name in users and users[name].lang != lang:
                users[name] = replace(users[name], lang=lang)
                self._save(users)

    def verify(self, name: str, passphrase: str) -> User | None:
        user = self.get((name or "").strip().lower())
        if user is None or len(passphrase) > MAX_PASSPHRASE_CHARS:
            verify_passphrase(passphrase[:MAX_PASSPHRASE_CHARS], self._dummy)
            return None
        return user if verify_passphrase(passphrase, user.password_hash) else None


class LoginLimiter:
    """5 failures in 15 min lock the account and the source address for 15 min."""

    def __init__(self) -> None:
        self._failures: dict[str, list[datetime]] = {}
        self._locked: dict[str, datetime] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _keys(user: str | None, remote: str | None) -> list[str]:
        keys = []
        if user:
            keys.append(f"user:{user}")
        if remote:
            keys.append(f"ip:{remote}")
        return keys

    def locked_until(self, user: str | None, remote: str | None, now: datetime) -> datetime | None:
        with self._lock:
            latest = None
            for key in self._keys(user, remote):
                until = self._locked.get(key)
                if until is not None and until <= now:
                    self._locked.pop(key, None)
                    self._failures.pop(key, None)
                    until = None
                if until is not None and (latest is None or until > latest):
                    latest = until
            return latest

    def record_failure(self, user: str | None, remote: str | None, now: datetime) -> datetime | None:
        """Count one failure; return the lock end when this failure locks."""
        locked = None
        with self._lock:
            for key in self._keys(user, remote):
                recent = [t for t in self._failures.get(key, []) if now - t < LOCK_WINDOW]
                recent.append(now)
                self._failures[key] = recent
                if len(recent) >= FAILURE_LIMIT:
                    self._locked[key] = now + LOCK_WINDOW
                    locked = now + LOCK_WINDOW
        return locked

    def reset(self, user: str | None, remote: str | None) -> None:
        with self._lock:
            for key in self._keys(user, remote):
                self._failures.pop(key, None)
                self._locked.pop(key, None)


@dataclass(slots=True)
class Session:
    sid: str = field(repr=False)
    user: str
    csrf: str = field(repr=False)
    created_at: datetime
    last_seen: datetime
    password_at: datetime

    def password_fresh(self, now: datetime) -> bool:
        return now - self.password_at <= PASSWORD_REENTRY


class SessionStore:
    """In-memory sessions with 256-bit IDs; idle 7 days, absolute 30 days."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, user: str, now: datetime) -> Session:
        session = Session(
            sid=secrets.token_urlsafe(32),
            user=user,
            csrf=secrets.token_urlsafe(32),
            created_at=now,
            last_seen=now,
            password_at=now,
        )
        with self._lock:
            self._sessions[session.sid] = session
        return session

    def get(self, sid: str | None, now: datetime) -> Session | None:
        if not sid:
            return None
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return None
            if now - session.last_seen > SESSION_IDLE or now - session.created_at > SESSION_ABSOLUTE:
                self._sessions.pop(sid, None)
                return None
            session.last_seen = now
            return session

    def delete(self, sid: str | None) -> None:
        if sid:
            with self._lock:
                self._sessions.pop(sid, None)

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)


def tokens_match(expected: str | None, given: str | None) -> bool:
    if not expected or not given:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), given.encode("utf-8"))
