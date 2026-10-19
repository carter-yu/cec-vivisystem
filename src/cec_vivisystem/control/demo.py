"""Local demo of the control app with synthetic data (Phase 31 screenshots).

Why: screenshots and design review need realistic screens without the Mini,
real ``.env`` values, Slack or Google.

Contract: the demo binds loopback only, builds a temporary data directory and
a synthetic ``.env`` (fake values, never read from the real one), uses fake
Slack, Calendar, OAuth and launchd doubles, and adds ``/demo/signin/<role>``
for one-click sign-in. ``serve`` never enables any of this. Synthetic names
and events only (ground rule 15).
"""

from __future__ import annotations

import ipaddress
import json
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cec_vivisystem.control.config import ControlConfig
from cec_vivisystem.control.launchd import CommandResult
from cec_vivisystem.models import CalendarListedEvent

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
DEMO_USERS = (("alex", "admin"), ("sam", "member"))
DEMO_PASSPHRASE = "demo-passphrase-not-secret"
_FAKE_ENV = """# Synthetic demo .env (not real values)
SLACK_BOT_TOKEN=demo-not-a-token
SLACK_APP_TOKEN=demo-not-a-token
SLACK_FAMILY_PLANS_CHANNEL_ID=C0DEMO0PLANS
SLACK_LIFE_NOTES_CHANNEL_ID=C0DEMO0NOTES
CEC_HEALTH_ALERT_CHANNEL_ID=G0DEMO0STATUS
GOOGLE_CLIENT_ID=demo-client.apps.example
GOOGLE_CLIENT_SECRET=demo-not-a-secret
GOOGLE_REFRESH_TOKEN=demo-not-a-token
GOOGLE_REFRESH_TOKEN_ISSUED_AT={issued}
GOOGLE_CALENDAR_ID=primary
CEC_LISTENER_STUCK_RETRIES=3
"""


class DemoRefused(ValueError):
    pass


def require_loopback(host: str) -> str:
    try:
        addr = ipaddress.ip_address("127.0.0.1" if host == "localhost" else host)
    except ValueError as exc:
        raise DemoRefused("demo binds a loopback IP only, for example 127.0.0.1") from exc
    if not addr.is_loopback:
        raise DemoRefused("demo binds a loopback IP only, for example 127.0.0.1")
    return str(addr)


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _at(now: datetime, *, days: int = 0, hours: float = 0, minutes: float = 0) -> datetime:
    return now + timedelta(days=days, hours=hours, minutes=minutes)


def build_fixture(root: Path, now: datetime) -> None:
    """Synthetic heartbeat, samples, markers, dates and audit under ``root``."""
    data = root / "data"
    health = data / "health"
    started = _at(now, hours=-14, minutes=-2)
    _write(health / "listener.json", {
        "written_at": _at(now, minutes=-0.4).isoformat(),
        "pid": 4182,
        "socket_mode_connected": True,
        "disconnected_since": None,
        "heartbeat_version": 2,
        "instance_id": "d3m0a1b2c3d4e5f6a7b8c9d0e1f2a3b4",
        "started_at": started.isoformat(),
        "outage_since": None,
        "last_connected_at": _at(now, minutes=-0.4).isoformat(),
        "disconnect_episodes": 3,
        "disconnected_s": 360.0,
        "last_outage_s": 240.0,
        "last_outage_ended_at": _at(now, hours=-6).isoformat(),
        "stuck_retries": 0,
    })
    _write(health / "alert_state.json", {
        "version": 2, "phase": "ok", "unhealthy_streak": 0, "healthy_streak": 12,
        "digest_posted_for": now.date().isoformat(), "outage_opens": [],
    })
    samples = health / "samples"
    samples.mkdir(parents=True, exist_ok=True)
    lines: dict[str, list[str]] = {}
    t = _at(now, hours=-25)
    episodes, down_s = 0, 0.0
    down_windows = [(-20.0, -19.92), (-6.1, -6.03), (-3.0, -2.95)]
    while t <= now:
        hours_ago = (t - now).total_seconds() / 3600
        down = any(a <= hours_ago <= b for a, b in down_windows)
        if down:
            down_s += 300.0
            episodes += 1
        row = {
            "checked_at": t.isoformat(), "status": "ok", "reason": "fresh", "pid": 4182,
            "instance_id": "d3m0a1b2c3d4e5f6a7b8c9d0e1f2a3b4", "connected": not down,
            "started_at": started.isoformat() if t >= started else None,
            "outage_since": None, "disconnected_s": down_s, "disconnect_episodes": episodes,
            "last_outage_s": 240.0 if episodes else None,
            "last_outage_ended_at": _at(now, hours=-19.9).isoformat() if episodes else None,
            "alert_phase": "ok", "alert_action": "unchanged",
        }
        lines.setdefault(t.date().isoformat(), []).append(json.dumps(row))
        t += timedelta(minutes=5)
    old = _at(now, days=-2, hours=-3)
    for action, offset in (("outage_sent", 0), ("recovered_sent", 35)):
        at = old + timedelta(minutes=offset)
        lines.setdefault(at.date().isoformat(), []).insert(0, json.dumps({
            "checked_at": at.isoformat(), "status": "ok", "reason": "fresh",
            "alert_phase": "ok", "alert_action": action,
        }))
    for day, rows in lines.items():
        (samples / f"{day}.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    recap_at = now.replace(hour=7, minute=0, second=6, microsecond=0)
    if now >= recap_at:
        _write(data / "morning_recap" / f"{now.date().isoformat()}.json", {
            "status": "posted", "posted_at": recap_at.isoformat(),
        })
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"important_dates-{now.date().isoformat()}.log").write_text(
        "event='important_dates_review_skipped' level='info'\n", encoding="utf-8"
    )
    dates = data / "important_dates"
    for date_id, title, month, day, kind, year in (
        ("d1", "Grandma's birthday", 10, 24, "yearly", None),
        ("d2", "Wedding anniversary", 11, 1, "yearly", None),
        ("d3", "School open day", 11, 15, "one_off", now.year),
        ("d4", "Swimming gala", 12, 6, "one_off", now.year),
        ("d5", "Leap-day birthday", 2, 29, "yearly", None),
    ):
        _write(dates / f"{date_id}.json", {
            "date_id": date_id, "title": title, "month": month, "day": day, "kind": kind,
            "year": year, "created_at": _at(now, days=-30).isoformat(), "participants": [],
            "raw_text": f"synthetic {title}",
        })
    audit = data / "control" / "audit"
    audit.mkdir(parents=True, exist_ok=True)
    rows = [
        {"at": _at(now, hours=-26).isoformat(timespec="seconds"), "actor": "alex", "action": "sign_in",
         "outcome": "success", "user_agent": "Mac · Safari"},
        {"at": _at(now, hours=-25.9).isoformat(timespec="seconds"), "actor": "alex",
         "action": "channel_change", "target": "CEC_HEALTH_ALERT_CHANNEL_ID", "outcome": "success",
         "old": "C0DEMO0OLD", "new": "G0DEMO0STATUS", "user_agent": "Mac · Safari"},
        {"at": _at(now, hours=-3).isoformat(timespec="seconds"), "actor": "sam", "action": "sign_in",
         "outcome": "success", "user_agent": "iPhone · Safari"},
        {"at": _at(now, hours=-2.9).isoformat(timespec="seconds"), "actor": "sam", "action": "google_test",
         "outcome": "success", "user_agent": "iPhone · Safari"},
    ]
    (audit / f"{now:%Y-%m}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )


@dataclass
class DemoCalendar:
    now: datetime

    def list_events(self, *, calendar_id: str, time_min: datetime, time_max: datetime) -> list[CalendarListedEvent]:
        day0 = time_min.astimezone(FAMILY_TZ).replace(hour=0, minute=0, second=0, microsecond=0)

        def at(days: int, hour: int, minute: int = 0) -> datetime:
            return day0 + timedelta(days=days, hours=hour, minutes=minute)

        return [
            CalendarListedEvent("e1", "School run", at(0, 8, 15), at(0, 8, 45), False, participants=["Kid"]),
            CalendarListedEvent("e2", "Swimming class", at(0, 16, 30), at(0, 17, 30), False, participants=["Kid"]),
            CalendarListedEvent("e3", "Parents' evening", at(1, 19), at(1, 20, 30), False, participants=["Parents"]),
            CalendarListedEvent("e4", "Playgroup", at(2, 0), at(3, 0), True),
            CalendarListedEvent("e5", "Dentist check-up", at(4, 10), at(4, 11), False, participants=["Parent B"]),
            CalendarListedEvent("e6", "Family trip", at(6, 0), at(8, 0), True, participants=["Family"]),
            CalendarListedEvent("e7", "Piano lesson", at(9, 17), at(9, 18), False, participants=["Kid"]),
        ]

    def warmup(self) -> None:
        return None


class DemoPoster:
    def post(self, *, channel_id: str, text: str) -> None:
        return None


class DemoTokenHttp:
    def post_form(self, url: str, data: object, *, timeout: float) -> tuple[int, dict]:
        return 200, {
            "refresh_token": "demo-refresh-token-not-real",
            "scope": "https://www.googleapis.com/auth/calendar.events",
        }


def demo_runner(argv: Sequence[str], *, timeout: float) -> CommandResult:
    if list(argv[:2]) == ["launchctl", "print"]:
        return CommandResult(0, "\tstate = running\n\tpid = 4182\n\truns = 3\n\tlast exit code = 75\n")
    return CommandResult(0)


def build_demo(host: str, port: int, *, now: datetime | None = None):
    from cec_vivisystem.control.app import ControlDeps, create_app
    from cec_vivisystem.control.auth import UserStore

    real = datetime.now(tz=FAMILY_TZ)
    # Show a calm evening (21:04 HKT) whatever the real time is, so every
    # daily job has a result on the screenshots; the clock still advances.
    current = now or real.replace(hour=21, minute=4, second=12, microsecond=0)
    offset = current - real
    root = Path(tempfile.mkdtemp(prefix="cec-control-demo-"))
    build_fixture(root, current)
    (root / ".env").write_text(
        _FAKE_ENV.format(issued=(current - timedelta(days=5, hours=2)).isoformat(timespec="seconds")),
        encoding="utf-8",
    )
    config = ControlConfig(
        host=host,
        port=port,
        allowed_nets=(ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128")),
        allowed_hosts=frozenset({"127.0.0.1", "localhost", "::1"}),
        data_dir=root / "data",
        env_path=root / ".env",
        log_dir=root / "logs",
        demo=True,
    )
    users = UserStore(config.users_path, scrypt_n=2**12)
    for name, role in DEMO_USERS:
        users.add(name, role, DEMO_PASSPHRASE)
    deps = ControlDeps(
        clock=lambda: datetime.now(tz=FAMILY_TZ) + offset,
        runner=demo_runner,
        poster_factory=lambda _token: DemoPoster(),
        calendar_factory=lambda _cfg: DemoCalendar(current),
        token_http=DemoTokenHttp(),
        platform="darwin",
        uid=501,
        scrypt_n=2**12,
    )
    return create_app(config, deps), root
