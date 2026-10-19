"""Shared offline fakes for the Phase 31 control-app tests.

No network, no launchd, no Slack, no Google: a fixed HKT clock, a temporary
data directory and a synthetic ``.env``. Synthetic names only (ground rule 15).
"""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from cec_vivisystem.control.config import ControlConfig
from cec_vivisystem.control.launchd import CommandResult
from cec_vivisystem.models import CalendarListedEvent

HKT = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 10, 20, 21, 4, 12, tzinfo=HKT)
BASE_URL = "http://127.0.0.1:8730"
CLIENT = ("192.168.1.20", 50000)
PASSPHRASE = "correct horse battery"
ADMIN, MEMBER = "alex", "sam"
LISTENER_PRINT = (
    "gui/501/com.cec.vivisystem.listener = {\n"
    "\tactive count = 1\n\tpath = /Users/x/Library/LaunchAgents/com.cec.vivisystem.listener.plist\n"
    "\tstate = running\n\n\tprogram = /opt/homebrew/bin/uv\n\truns = 2\n\tpid = 4182\n"
    "\tlast exit code = 75\n\n\tendpoints = {\n\t\tstate = active\n\t}\n}\n"
)
ENV_TEXT = """# synthetic test .env
LOG_LEVEL=INFO
SLACK_BOT_TOKEN=xoxb-test-not-real
SLACK_APP_TOKEN=xapp-test-not-real
SLACK_FAMILY_PLANS_CHANNEL_ID=C0TESTPLANS
SLACK_LIFE_NOTES_CHANNEL_ID=C0TESTNOTES
CEC_HEALTH_ALERT_CHANNEL_ID=G0TESTSTATUS
GOOGLE_CLIENT_ID=test-client.apps.example
GOOGLE_CLIENT_SECRET=test-client-secret
GOOGLE_REFRESH_TOKEN=old-refresh-token
GOOGLE_REFRESH_TOKEN_ISSUED_AT=2026-10-19T21:00:00+08:00
GOOGLE_CALENDAR_ID=primary
"""


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@dataclass
class FakeRunner:
    calls: list[list[str]] = field(default_factory=list)
    kick_code: int = 0
    raise_on_kick: BaseException | None = None
    on_kick: Any = None

    def __call__(self, argv: Sequence[str], *, timeout: float) -> CommandResult:
        self.calls.append(list(argv))
        if list(argv[:2]) == ["launchctl", "print"]:
            return CommandResult(0, LISTENER_PRINT)
        if self.raise_on_kick is not None:
            raise self.raise_on_kick
        if self.on_kick is not None:
            self.on_kick()
        return CommandResult(self.kick_code, "", "")

    @property
    def kicks(self) -> list[list[str]]:
        return [c for c in self.calls if "kickstart" in c]


@dataclass
class FakePoster:
    posts: list[tuple[str, str]] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)
    fail: bool = False

    def factory(self, token: str) -> FakePoster:
        self.tokens.append(token)
        return self

    def post(self, *, channel_id: str, text: str) -> None:
        if self.fail:
            raise RuntimeError("synthetic slack failure")
        self.posts.append((channel_id, text))


class RefreshError(Exception):
    """Same class name as google.auth's; the reader classifies by name."""


@dataclass
class FakeCalendar:
    events: list[CalendarListedEvent] = field(default_factory=list)
    fail_with: BaseException | None = None
    warmup_ok: bool = True
    list_calls: int = 0
    warmups: int = 0
    configs: list[Any] = field(default_factory=list)

    def factory(self, config: Any) -> FakeCalendar:
        self.configs.append(config)
        return self

    def list_events(self, *, calendar_id: str, time_min: datetime, time_max: datetime) -> list[CalendarListedEvent]:
        self.list_calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        return [e for e in self.events if (e.end or e.start) > time_min and e.start < time_max]

    def warmup(self) -> None:
        self.warmups += 1
        if not self.warmup_ok:
            raise RefreshError("invalid_grant: Token has been expired or revoked.")


@dataclass
class FakeTokenHttp:
    status: int = 200
    payload: dict = field(default_factory=lambda: {
        "access_token": "ya29.synthetic-access",
        "refresh_token": "1//synthetic-new-refresh-token",
        "scope": "https://www.googleapis.com/auth/calendar.events",
        "token_type": "Bearer",
    })
    calls: list[tuple[str, dict]] = field(default_factory=list)

    def post_form(self, url: str, data: Any, *, timeout: float) -> tuple[int, dict]:
        self.calls.append((url, dict(data)))
        return self.status, dict(self.payload)


def heartbeat(now: datetime = NOW, **overrides: Any) -> dict[str, Any]:
    payload = {
        "written_at": (now - timedelta(seconds=24)).isoformat(),
        "pid": 4182,
        "socket_mode_connected": True,
        "disconnected_since": None,
        "heartbeat_version": 2,
        "instance_id": "aaaa1111bbbb2222cccc3333dddd4444",
        "started_at": (now - timedelta(hours=14)).isoformat(),
        "outage_since": None,
        "last_connected_at": (now - timedelta(seconds=24)).isoformat(),
        "disconnect_episodes": 0,
        "disconnected_s": 0.0,
        "last_outage_s": None,
        "last_outage_ended_at": None,
        "stuck_retries": 0,
    }
    payload.update(overrides)
    return payload


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")


def ev(eid: str, title: str, start: datetime, end: datetime | None, *, all_day: bool = False,
       who: list[str] | None = None) -> CalendarListedEvent:
    return CalendarListedEvent(eid, title, start, end, all_day, participants=who or [])


@dataclass
class Harness:
    client: Any
    app: Any
    state: Any
    clock: Clock
    runner: FakeRunner
    poster: FakePoster
    calendar: FakeCalendar
    http: FakeTokenHttp
    root: Path
    config: ControlConfig

    @property
    def env_path(self) -> Path:
        return self.config.env_path

    def heartbeat(self, **overrides: Any) -> None:
        write_json(self.config.data_dir / "health" / "listener.json", heartbeat(self.clock.now, **overrides))


def make_config(root: Path, *, https: bool = False) -> ControlConfig:
    return ControlConfig(
        host="192.168.1.10",
        port=8730,
        allowed_nets=(
            ipaddress.ip_network("127.0.0.0/8"),
            ipaddress.ip_network("::1/128"),
            ipaddress.ip_network("192.168.1.0/24"),
        ),
        allowed_hosts=frozenset({"127.0.0.1", "localhost", "192.168.1.10", "mac-mini.local"}),
        data_dir=root / "data",
        env_path=root / ".env",
        log_dir=root / "logs",
        tls_cert=root / "cert.pem" if https else None,
        tls_key=root / "key.pem" if https else None,
    )


def make_harness(
    root: Path,
    *,
    env_text: str = ENV_TEXT,
    platform: str = "darwin",
    https: bool = False,
    write_heartbeat: bool = True,
    users: tuple[tuple[str, str], ...] = ((ADMIN, "admin"), (MEMBER, "member")),
) -> Harness:
    from starlette.testclient import TestClient

    from cec_vivisystem.control.app import ControlDeps, create_app
    from cec_vivisystem.control.auth import UserStore

    config = make_config(root, https=https)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    if env_text is not None:
        config.env_path.write_text(env_text, encoding="utf-8")
        config.env_path.chmod(0o600)
    clock = Clock()
    store = UserStore(config.users_path, scrypt_n=2**10, clock=clock)
    for name, role in users:
        store.add(name, role, PASSPHRASE)
    runner, poster, calendar, http = FakeRunner(), FakePoster(), FakeCalendar(), FakeTokenHttp()
    deps = ControlDeps(
        clock=clock,
        runner=runner,
        poster_factory=poster.factory,
        calendar_factory=calendar.factory,
        token_http=http,
        platform=platform,
        uid=501,
        scrypt_n=2**10,
    )
    app = create_app(config, deps)
    base = BASE_URL.replace("http://", "https://") if https else BASE_URL
    client = TestClient(
        app, base_url=base, client=CLIENT, follow_redirects=False,
        headers={"Origin": base, "User-Agent": "Mozilla/5.0 (iPhone) Safari/605"},
    )
    harness = Harness(client, app, app.state.ctx, clock, runner, poster, calendar, http, root, config)
    if write_heartbeat:
        harness.heartbeat()
    return harness


_CSRF = re.compile(r'name="csrf" value="([^"]+)"')


def csrf_of(html: str) -> str:
    match = _CSRF.search(html)
    assert match, "no csrf field in page"
    return match.group(1)


def sign_in(h: Harness, name: str = ADMIN, password: str = PASSPHRASE) -> Any:
    page = h.client.get("/signin")
    return h.client.post(
        "/signin", data={"csrf": csrf_of(page.text), "username": name, "password": password, "next": "/"}
    )


def page_csrf(h: Harness, path: str = "/settings/account") -> str:
    response = h.client.get(path)
    assert response.status_code == 200, (path, response.status_code)
    return csrf_of(response.text)
