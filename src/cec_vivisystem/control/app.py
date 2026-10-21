"""Mini control web app (Phase 31a/31b; ADRs 0015, 0016).

Why: both parents need one calm place on the phone to see whether the family
bot is healthy and to fix the common problems: Google access expired, the
listener needs a restart, or a Slack channel is wrong.

Contract:
- ``create_app`` takes every I/O dependency (clock, process runner, Slack
  poster, Calendar client, OAuth HTTP) so the offline suite never reaches
  Slack, Google or launchd.
- Every request passes the Host allowlist (400) and the client-network
  allowlist (403) before any route runs. GET never changes state. Every POST
  needs the session CSRF token and a same-origin ``Origin``/``Referer``.
- Roles are checked on the server for each route. Sensitive actions need a
  password entered in the last 15 minutes.
- Read-only for Calendar and important dates. The app never writes Google
  Calendar, never resolves a confirmation and never starts a scheduled job;
  a static test enforces the Calendar part. A future Calendar write must create
  a normal pending confirmation and wait for an explicit "yes" in Slack.
- No background tasks. A listener restart happens only as part of a human
  action: an admin confirms the restart screen, or a person saves new Google
  access (the token is useless until the listener reloads ``.env``; ADR 0016,
  amended 2026-10-21). Never restart from a GET, a health reading or a timer.
- Secrets reach templates only as "Set" / "Not set".
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote
from zoneinfo import ZoneInfo

import jinja2
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from cec_vivisystem.calendar_reader import group_by_hkt_day, list_calendar_events
from cec_vivisystem.calendar_writer import (
    CalendarWriterConfigError,
    GoogleCalendarClient,
    GoogleCalendarConfig,
    is_google_auth_error,
    load_google_calendar_config,
    probe_google_client,
)
from cec_vivisystem.control import envfile, i18n, snapshot
from cec_vivisystem.control.audit import AuditLog
from cec_vivisystem.control.auth import (
    LANGS,
    SCRYPT_N,
    LoginLimiter,
    Session,
    SessionStore,
    User,
    UserStore,
    UserStoreError,
    tokens_match,
)
from cec_vivisystem.control.config import (
    ControlConfig,
    client_allowed,
    host_header_name,
)
from cec_vivisystem.control.launchd import (
    RestartUnavailable,
    Runner,
    kickstart_listener,
    print_service,
    subprocess_runner,
)
from cec_vivisystem.control.oauth_google import (
    OAuthAttempts,
    OAuthError,
    TokenHttp,
    UrllibTokenHttp,
    check_redirect,
    consent_url,
    exchange_code,
    issued_at_value,
    parse_redirect,
)
from cec_vivisystem.important_dates import JsonDirImportantDatesStore, next_occurrence
from cec_vivisystem.logging import get_logger
from cec_vivisystem.models import (
    CalendarListOutcome,
    CalendarListResult,
    ImportantDateKind,
)
from cec_vivisystem.status_channel import load_system_channel

logger = get_logger(__name__)

COMPONENT = "control"
FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
PACKAGE_DIR = Path(__file__).resolve().parent
FONT_FILE = PACKAGE_DIR.parents[2] / "assets" / "fonts" / "NotoSansTC-Regular.ttf"
CALENDAR_DAYS = 14
CALENDAR_CACHE = timedelta(seconds=60)
RESTART_GAP = timedelta(minutes=5)
# Brief SDK reconnects are normal; below this share of the day the card warns.
STABLE_PCT = 98.0
RESTART_WAIT = timedelta(minutes=2)
SESSION_MAX_AGE_S = 30 * 24 * 3600
CSP = (
    "default-src 'self'; frame-ancestors 'none'; form-action 'self'; "
    "base-uri 'none'; object-src 'none'"
)
# ``same-origin``, not ``no-referrer``: with ``no-referrer`` browsers send
# ``Origin: null`` on same-origin form posts, which defeats the Origin check
# (ADR 0015). Cross-origin requests, including Google, still get no referrer.
REFERRER_POLICY = "same-origin"

Clock = Callable[[], datetime]


class Poster(Protocol):
    def post(self, *, channel_id: str, text: str) -> object: ...


PosterFactory = Callable[[str], Poster]
CalendarFactory = Callable[[GoogleCalendarConfig], Any]


def _now_hkt() -> datetime:
    return datetime.now(tz=FAMILY_TZ)


def _slack_poster(bot_token: str) -> Poster:
    from cec_vivisystem.morning_recap import SlackWebPoster

    return SlackWebPoster(bot_token)


@dataclass(slots=True)
class ControlDeps:
    clock: Clock = _now_hkt
    runner: Runner = subprocess_runner
    poster_factory: PosterFactory = _slack_poster
    calendar_factory: CalendarFactory = GoogleCalendarClient
    token_http: TokenHttp = field(default_factory=UrllibTokenHttp)
    platform: str = sys.platform
    uid: int = field(default_factory=lambda: os.getuid() if hasattr(os, "getuid") else 0)
    scrypt_n: int = SCRYPT_N


class NotSignedIn(Exception):
    pass


class Forbidden(Exception):
    pass


class CsrfError(Exception):
    pass


class BadRequest(Exception):
    pass


@dataclass(slots=True)
class Flash:
    tone: str
    key: str
    values: dict[str, Any] = field(default_factory=dict)
    restart_prompt: bool = False


@dataclass(slots=True)
class RestartInfo:
    at: datetime
    actor: str
    old_instance: str | None
    old_pid: int | None
    reason: str = "manual"


class ControlState:
    """Process-local state; one control app per data directory."""

    def __init__(self, config: ControlConfig, deps: ControlDeps) -> None:
        self.config = config
        self.deps = deps
        self.users = UserStore(config.users_path, scrypt_n=deps.scrypt_n, clock=deps.clock)
        self.sessions = SessionStore()
        self.limiter = LoginLimiter()
        self.audit = AuditLog(config.audit_dir, clock=deps.clock)
        self.attempts = OAuthAttempts()
        self.paths = snapshot.DataPaths(config.data_dir, config.log_dir)
        self.flash: dict[str, Flash] = {}
        self.calendar_cache: tuple[datetime, date, str, CalendarListResult | None] | None = None
        self.calendar_client: tuple[str, Any] | None = None
        self.last_test: tuple[datetime, bool] | None = None
        self.last_restart: RestartInfo | None = None
        self.maintained_on: date | None = None

    def now(self) -> datetime:
        return i18n.local(self.deps.clock())

    def env(self) -> dict[str, str]:
        return envfile.read_env(self.config.env_path)

    @property
    def cookie_name(self) -> str:
        return "__Host-cec_session" if self.config.https else "cec_session"

    @property
    def pre_cookie_name(self) -> str:
        return "__Host-cec_pre" if self.config.https else "cec_pre"

    def maintain(self) -> None:
        """Audit purge and backup pruning once per HKT day (no scheduler)."""
        today = self.now().date()
        if self.maintained_on == today:
            return
        self.maintained_on = today
        self.audit.purge(now=self.now())
        envfile.prune_backups(self.config.backup_dir, now=self.now())


# --- middleware ---------------------------------------------------------------


class GuardMiddleware:
    """Host and client-network allowlists, security headers, request log."""

    def __init__(self, app: Any, state: ControlState) -> None:
        self.app = app
        self.state = state

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        request_id = uuid.uuid4().hex[:12]
        scope.setdefault("state", {})["request_id"] = request_id
        status = [500]
        https = self.state.config.https

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                status[0] = message["status"]
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = CSP
                headers["Referrer-Policy"] = REFERRER_POLICY
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Cache-Control"] = "no-store"
                headers["Cross-Origin-Opener-Policy"] = "same-origin"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                headers["X-Request-ID"] = request_id
                if https:
                    headers["Strict-Transport-Security"] = "max-age=31536000"
            await send(message)

        headers = Headers(scope=scope)
        client = scope.get("client")
        remote = client[0] if client else None
        try:
            if host_header_name(headers.get("host")) not in self.state.config.allowed_hosts:
                await PlainTextResponse("Bad request: host not allowed.\n", 400)(
                    scope, receive, send_wrapper
                )
            elif not client_allowed(remote, self.state.config.allowed_nets):
                await PlainTextResponse(
                    "Forbidden: this address is not on the family network.\n", 403
                )(scope, receive, send_wrapper)
            else:
                self.state.maintain()
                await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            log = logger.debug if path.startswith("/static/") else logger.info
            log(
                "control_request",
                component=COMPONENT,
                method=scope.get("method"),
                path=path,
                status=status[0],
                duration_ms=int((time.perf_counter() - started) * 1000),
                request_id=request_id,
                outcome="success" if status[0] < 400 else "failure",
            )


# --- helpers ------------------------------------------------------------------


def _state(request: Request) -> ControlState:
    return request.app.state.ctx


def _remote(request: Request) -> str | None:
    return request.client.host if request.client else None


def _request_id(request: Request) -> str | None:
    return request.scope.get("state", {}).get("request_id")


def safe_next(raw: str | None) -> str:
    text = (raw or "").strip()
    if (
        not text.startswith("/")
        or text.startswith(("//", "/signin"))
        or "\\" in text
        or "\n" in text
        or len(text) > 200
    ):
        return "/"
    return text


def _current(request: Request) -> tuple[Session, User] | None:
    state = _state(request)
    session = state.sessions.get(request.cookies.get(state.cookie_name), state.now())
    if session is None:
        return None
    user = state.users.get(session.user)
    if user is None:
        state.sessions.delete(session.sid)
        return None
    return session, user


def _require(request: Request, *, admin: bool = False) -> tuple[Session, User]:
    found = _current(request)
    if found is None:
        raise NotSignedIn()
    if admin and not found[1].is_admin:
        raise Forbidden()
    return found


def _check_origin(request: Request) -> None:
    host = request.headers.get("host", "")
    own = f"{request.url.scheme}://{host}"
    origin = request.headers.get("origin")
    if origin is not None:
        if origin != own:
            raise CsrfError()
        return
    referer = request.headers.get("referer")
    if referer is None or not (referer == own or referer.startswith(own + "/")):
        raise CsrfError()


async def _form(request: Request, session: Session | None) -> FormData:
    _check_origin(request)
    form = await request.form()
    expected = session.csrf if session else request.cookies.get(_state(request).pre_cookie_name)
    if not tokens_match(expected, str(form.get("csrf") or "")):
        raise CsrfError()
    return form


def _audit(request: Request, actor: str, action: str, outcome: str, **extra: Any) -> None:
    _state(request).audit.record(
        actor=actor,
        action=action,
        outcome=outcome,
        request_id=_request_id(request),
        remote_addr=_remote(request),
        user_agent=request.headers.get("user-agent"),
        **extra,
    )


def _password_check(
    request: Request, session: Session, user: User, form: FormData
) -> str | None:
    """None when fresh (or re-entered now); else the error copy key."""
    state = _state(request)
    now = state.now()
    if session.password_fresh(now):
        return None
    given = str(form.get("password") or "")
    if not given:
        return "reauth.body"
    if state.limiter.locked_until(user.name, _remote(request), now) is not None:
        return "reauth.wrong"
    if state.users.verify(user.name, given) is None:
        state.limiter.record_failure(user.name, _remote(request), now)
        _audit(request, user.name, "sign_in_failed", "failure", detail="reentry")
        return "reauth.wrong"
    session.password_at = now
    return None


def _set_flash(request: Request, session: Session, flash: Flash) -> None:
    _state(request).flash[session.sid] = flash


def _see_other(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _announce(state: ControlState, key: str, **values: Any) -> bool:
    """Post one EN + TC notice to the system-status channel. Never raises."""
    env = state.env()
    bot = (env.get("SLACK_BOT_TOKEN") or "").strip()
    channel = load_system_channel(env, component=COMPONENT)
    if not bot or not channel:
        logger.warning(
            "control_notice_skipped", component=COMPONENT, outcome="skipped", notice=key
        )
        return False
    try:
        state.deps.poster_factory(bot).post(channel_id=channel, text=i18n.both(key, **values))
    except Exception as exc:  # noqa: BLE001 — a notice must not undo the action
        logger.error(
            "control_notice_failed",
            component=COMPONENT,
            outcome="failure",
            notice=key,
            error_type=type(exc).__name__,
        )
        return False
    logger.info("control_notice_sent", component=COMPONENT, outcome="success", notice=key)
    return True


def _action_log(event: str, action: str, **fields: Any) -> None:
    logger.info(event, component=COMPONENT, action=action, **fields)


# --- rendering ------------------------------------------------------------------

_TONE_GLYPH = {"ok": "●", "warn": "▲", "error": "■", "unknown": "○", "info": "◆"}


def _templates() -> Jinja2Templates:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(PACKAGE_DIR / "templates")),
        autoescape=True,
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["glyph"] = lambda tone: _TONE_GLYPH.get(tone, "○")
    return Jinja2Templates(env=env)


def _lang_for(request: Request, user: User | None) -> str:
    if user is not None:
        return user.lang
    asked = request.query_params.get("lang")
    return asked if asked in LANGS else i18n.EN


def render(
    request: Request,
    template: str,
    /,
    *,
    status: int = 200,
    session: Session | None = None,
    user: User | None = None,
    nav: str | None = None,
    sub: str | None = None,
    title_key: str = "app.name",
    **context: Any,
) -> HTMLResponse:
    state = _state(request)
    lang = _lang_for(request, user)
    now = state.now()
    flash = state.flash.pop(session.sid, None) if session is not None else None
    other = i18n.TC if lang == i18n.EN else i18n.EN
    base = {
        "t": partial(i18n.text, lang),
        "t_other": partial(i18n.text, other),
        "lang": lang,
        "other_lang": other,
        "user": user,
        "is_admin": bool(user and user.is_admin),
        "csrf": session.csrf if session is not None else context.pop("csrf", ""),
        "path": request.url.path,
        "nav": nav,
        "sub": sub,
        "title_key": title_key,
        "demo": state.config.demo,
        "flash": flash,
        "now_label": f"{i18n.fmt_long_date(lang, now.date())} · {i18n.fmt_clock(now)} HKT",
        "updated_label": i18n.text(lang, "meta.updated", time=i18n.fmt_clock(now, seconds=True)),
        "refresh": context.pop("refresh", None),
    }
    return request.app.state.templates.TemplateResponse(
        request, template, {**base, **context}, status_code=status
    )


def _error_page(request: Request, status: int, key: str) -> HTMLResponse:
    found = None
    try:
        found = _current(request)
    except Exception:  # noqa: BLE001 — error pages must render even if auth fails
        found = None
    session, user = found if found else (None, None)
    return render(
        request,
        "error.html",
        status=status,
        session=session,
        user=user,
        title_key=f"err.title.{key}",
        message_key=f"err.{key}",
    )


# --- view models ------------------------------------------------------------------


def _days_text(lang: str, days: int) -> dict[str, Any]:
    return {"days": days, "unit": i18n.day_unit(lang, days)}


def listener_card(lang: str, view: snapshot.ListenerView, now: datetime) -> dict[str, Any]:
    t = partial(i18n.text, lang)
    when = partial(i18n.fmt_when, lang, now=now)
    chip = t("listener.chip.connected")
    body = t("listener.body.running")
    if view.state == "disconnected" and view.outage_since is not None:
        seconds = (now - view.outage_since).total_seconds()
        chip = t("listener.chip.down_from", time=when(view.outage_since))
        if view.tone == "error":
            body = t(
                "listener.body.disconnected",
                time=when(view.outage_since),
                duration=i18n.fmt_duration(lang, seconds),
            )
        else:
            body = t("listener.body.reconnecting", time=when(view.outage_since))
    elif view.state == "stopped" and view.written_at is not None:
        chip = t("listener.chip.stopped_at", time=when(view.written_at))
        body = t(
            "listener.body.stopped",
            time=when(view.written_at),
            duration=i18n.fmt_duration(lang, (now - view.written_at).total_seconds()),
        )
    elif view.state == "nodata":
        chip, body = t("listener.chip.nodata"), t("listener.body.nodata")
    elif view.state == "invalid":
        chip, body = t("listener.state.invalid"), t("listener.body.invalid")
    kv: list[tuple[str, str, bool]] = []
    if view.written_at is not None:
        same_day = view.written_at.date() == now.date()
        beat = i18n.fmt_clock(view.written_at, seconds=True) if same_day else when(view.written_at)
        kv.append((t("kv.heartbeat"), beat, False))
    if view.pid is not None:
        kv.append((t("kv.process"), f"PID {view.pid}", True))
    if view.started_at is not None:
        kv.append((t("kv.started"), when(view.started_at), False))
    return {
        "state": t(f"listener.state.{view.state}"),
        "tone": view.tone,
        "chip": chip,
        "body": body,
        "kv": kv,
    }


def connection_card(lang: str, view: snapshot.ConnectionView, now: datetime) -> dict[str, Any]:
    t = partial(i18n.text, lang)
    summary = view.summary
    segments = []
    if view.window_start is not None:
        for seg in view.segments:
            left, width = seg.fraction(view.window_start)
            min_width = 0.006 if seg.kind == "down" else 0.0
            segments.append(
                {"kind": seg.kind, "x": round(left * 1000, 2), "w": round(max(width, min_width) * 1000, 2)}
            )
    if summary is None:
        state, tone, kv = t("conn.state.nodata"), "unknown", []
    else:
        down_now = bool(view.segments) and view.segments[-1].kind == "down"
        if down_now:
            state, tone = t("conn.state.down"), "error"
        elif summary.connected_pct < STABLE_PCT:
            state, tone = t("conn.state.drops"), "warn"
        else:
            state, tone = t("conn.state.stable"), "ok"
        kv = [
            (t("kv.connected_pct"), f"{summary.connected_pct:.1f}%", False),
            (t("kv.disconnects"), str(summary.disconnects), False),
            (t("kv.longest"), i18n.fmt_duration(lang, summary.longest_s), False),
            (t("kv.restarts"), str(summary.restarts), False),
        ]
    outages = [
        {
            "time": i18n.fmt_when(lang, seg.start, now=now),
            "text": t("listener.event", duration=i18n.fmt_duration(lang, (seg.end - seg.start).total_seconds())),
        }
        for seg in view.outages
    ]
    return {"state": state, "tone": tone, "segments": segments, "kv": kv, "outages": outages}


def recap_card(lang: str, jobs: snapshot.JobsView, now: datetime) -> dict[str, Any]:
    t = partial(i18n.text, lang)
    state = jobs.recap
    if state == "missing" and now.hour < 7:
        state = "before"
    tone = {
        "posted": "ok", "pending": "warn", "failed": "error",
        "missing": "error", "before": "unknown", "unknown": "unknown",
    }[state]
    body = {
        "posted": t("recap.posted", time=i18n.fmt_clock(jobs.recap_at, seconds=True)) if jobs.recap_at else "",
        "pending": t("recap.pending"),
        "failed": t("recap.failed"),
        "missing": t("recap.missed"),
        "before": t("recap.before"),
        "unknown": t("recap.unknown"),
    }[state]
    return {"state": t(f"recap.state.{state}"), "tone": tone, "body": body}


def dates_job_card(lang: str, jobs: snapshot.JobsView) -> dict[str, Any]:
    t = partial(i18n.text, lang)
    if jobs.dates is None:
        return {"state": t("datesjob.state.none"), "tone": "unknown", "body": t("datesjob.none")}
    day, outcome = jobs.dates
    tone = {"posted": "ok", "nothing": "ok", "failed": "error"}[outcome]
    return {
        "state": t(f"datesjob.state.{outcome}"),
        "tone": tone,
        "body": t(f"datesjob.{outcome}", date=i18n.fmt_date(lang, day)),
    }


def google_card(
    lang: str, view: snapshot.TokenView, last_test: tuple[datetime, bool] | None, now: datetime
) -> dict[str, Any]:
    t = partial(i18n.text, lang)
    if view.state in ("ok", "warn"):
        state = t(f"google.state.{view.state}", **_days_text(lang, view.days_left or 0))
        chip = t("google.chip.expires", date=i18n.fmt_date(lang, view.expires_on))
        body = t("google.expires", date=i18n.fmt_date(lang, view.expires_on), **_days_text(lang, view.days_left or 0))
    elif view.state == "today":
        state, chip, body = t("google.state.today"), t("google.state.today"), t("google.today")
    elif view.state == "expired":
        state = t("google.state.expired")
        chip = t("google.chip.expired", date=i18n.fmt_date(lang, view.expires_on))
        body = t("google.expired", date=i18n.fmt_date(lang, view.expires_on))
    else:
        state, chip, body = t("google.state.unknown"), t("google.chip.unknown"), t("google.unknown")
    kv = []
    if view.issued_at is not None:
        kv.append((t("kv.issued"), i18n.fmt_when(lang, view.issued_at, now=now), False))
    kv.append((t("kv.mode"), t("google.mode", ttl=view.ttl_days), False))
    if last_test is None:
        kv.append((t("kv.last_test"), t("google.test.none"), False))
    else:
        at, ok = last_test
        key = "google.test.ok" if ok else "google.test.failed_at"
        kv.append((t("kv.last_test"), t(key, time=i18n.fmt_when(lang, at, now=now)), False))
    return {"state": state, "tone": view.tone, "chip": chip, "body": body, "kv": kv}


def alerts_card(lang: str, alert_state: Any, jobs: snapshot.JobsView, now: datetime) -> dict[str, Any]:
    t = partial(i18n.text, lang)
    is_open = snapshot.alert_open(alert_state)
    kv = [
        (t("kv.daily_status"), t("alerts.digest.posted" if jobs.digest_today else "alerts.digest.notyet"), False),
        (t("kv.policy"), t("alerts.policy"), False),
    ]
    if is_open and alert_state.outage_started_at is not None:
        body = t("alerts.open", time=i18n.fmt_when(lang, alert_state.outage_started_at, now=now))
    else:
        body = t("alerts.none")
    return {
        "state": t("alerts.state.open" if is_open else "alerts.state.none"),
        "tone": "error" if is_open else "ok",
        "body": body,
        "kv": kv,
    }


def _calendar(state: ControlState, env: Mapping[str, str], now: datetime) -> tuple[str, CalendarListResult | None, datetime | None]:
    """``(status, result, fetched_at)``; status ok | error | auth | config."""
    today = now.date()
    cached = state.calendar_cache
    if cached is not None and cached[1] == today and now - cached[0] < CALENDAR_CACHE:
        return cached[2], cached[3], cached[0]
    try:
        cfg = load_google_calendar_config(env)
    except CalendarWriterConfigError:
        return "config", None, None
    fingerprint = hashlib.sha256(
        f"{cfg.client_id}\0{cfg.refresh_token}\0{cfg.calendar_id}".encode()
    ).hexdigest()
    if state.calendar_client is None or state.calendar_client[0] != fingerprint:
        state.calendar_client = (fingerprint, state.deps.calendar_factory(cfg))
    start = datetime(today.year, today.month, today.day, tzinfo=FAMILY_TZ)
    result = list_calendar_events(
        time_min=start,
        time_max=start + timedelta(days=CALENDAR_DAYS),
        client=state.calendar_client[1],
        calendar_id=cfg.calendar_id,
    )
    if result.outcome == CalendarListOutcome.SUCCESS:
        status = "ok"
    elif is_google_auth_error(error_type=result.error_type, error_message=result.error_message):
        status = "auth"
    else:
        status = "error"
    state.calendar_cache = (now, today, status, result)
    return status, result, now


def calendar_days(lang: str, result: CalendarListResult, *, until: date | None = None) -> list[dict[str, Any]]:
    t = partial(i18n.text, lang)
    days = []
    for day, items in group_by_hkt_day(result).items():
        if until is not None and day >= until:
            break
        rows = []
        for item in items:
            start = i18n.local(item.start)
            if item.all_day:
                when, kind = t("calendar.allday"), "allday"
            elif start.date() < day:
                when, kind = t("calendar.continued"), "allday"
            else:
                end = i18n.local(item.end) if item.end else None
                when = i18n.fmt_clock(start)
                if end is not None and end.date() == start.date() and end > start:
                    when = f"{when}–{i18n.fmt_clock(end)}"
                kind = "timed"
            rows.append({
                "when": when,
                "kind": kind,
                "title": item.summary or t("calendar.untitled"),
                "who": ", ".join(item.participants),
            })
        other = i18n.TC if lang == i18n.EN else i18n.EN
        days.append({"label": i18n.fmt_date(lang, day), "label_other": i18n.fmt_date(other, day), "rows": rows})
    return days


def important_dates(state: ControlState, lang: str, today: date) -> tuple[list[dict[str, Any]], int, bool]:
    t = partial(i18n.text, lang)
    try:
        items = JsonDirImportantDatesStore(state.config.data_dir / "important_dates").list_all()
    except OSError:
        return [], 0, False
    upcoming, past = [], 0
    for item in items:
        occ = next_occurrence(item, today=today)
        if occ is None:
            past += 1
            continue
        upcoming.append((occ, item))
    upcoming.sort(key=lambda pair: (pair[0], pair[1].title))
    rows = []
    for occ, item in upcoming:
        delta = (occ - today).days
        if delta == 0:
            when = t("dates.today")
        elif delta == 1:
            when = t("dates.tomorrow")
        else:
            when = t("dates.in_days", days=delta)
        rows.append({
            "date": i18n.fmt_date(lang, occ),
            "title": item.title,
            "kind": t("dates.yearly" if item.kind == ImportantDateKind.YEARLY else "dates.oneoff"),
            "when": when,
            "who": ", ".join(item.participants),
        })
    return rows, past, True


def activity_rows(state: ControlState, lang: str, now: datetime, *, limit: int) -> list[dict[str, Any]]:
    t = partial(i18n.text, lang)
    rows = []
    for row in state.audit.recent(limit=limit):
        try:
            at = datetime.fromisoformat(row["at"])
        except (KeyError, ValueError):
            continue
        action = str(row.get("action") or "")
        label = i18n.catalog(lang).get(f"act.{action}", action)
        outcome = str(row.get("outcome") or "")
        detail = t(f"outcome.{outcome}") if f"outcome.{outcome}" in i18n.catalog(lang) else outcome
        if f"act.reason.{row.get('reason')}" in i18n.catalog(lang):
            label = f"{label} · {t('act.reason.' + str(row['reason']))}"
        if action == "channel_change" and row.get("target"):
            label = f"{label} · {t('channels.' + str(row['target']))}" if f"channels.{row['target']}" in i18n.catalog(lang) else label
        rows.append({
            "time": i18n.fmt_when(lang, at, now=now),
            "who": str(row.get("actor") or ""),
            "text": f"{label} · {detail}",
            "device": str(row.get("user_agent") or ""),
            "old": row.get("old"),
            "new": row.get("new"),
        })
    return rows


def banners(lang: str, lv: snapshot.ListenerView, tv: snapshot.TokenView, gcard: dict, lcard: dict,
            env: Mapping[str, str], jobs: snapshot.JobsView) -> list[dict[str, Any]]:
    t = partial(i18n.text, lang)
    found = []
    if lv.tone == "error":
        found.append({"tone": "error", "text": lcard["body"], "label": t("action.open_listener"), "href": "/system/listener"})
    if tv.state in ("warn", "today", "expired", "unknown"):
        found.append({"tone": "error" if tv.tone == "error" else ("warn" if tv.tone == "warn" else "info"),
                      "text": gcard["body"], "label": t("action.reauth"), "reauth": True})
    if jobs.pending:
        found.append({"tone": "warn", "text": t("overview.pending", count=len(jobs.pending)),
                      "label": t("action.open_jobs"), "href": "/system/jobs"})
    if not (env.get(envfile.CHANNEL_KEYS[2]) or "").strip():
        found.append({"tone": "info", "text": t("channels.fallback"), "label": t("sub.channels"), "href": "/settings/channels"})
    return found


# --- routes: sign in / out ---------------------------------------------------------


async def signin_page(request: Request) -> Response:
    state = _state(request)
    if _current(request) is not None:
        return _see_other("/")
    token = request.cookies.get(state.pre_cookie_name) or uuid.uuid4().hex + uuid.uuid4().hex
    try:
        no_users = not state.users.load()
    except UserStoreError:
        no_users = True
    notice = None
    if request.query_params.get("out"):
        notice = "signin.signed_out"
    elif request.query_params.get("expired"):
        notice = "session.expired"
    response = render(
        request,
        "signin.html",
        title_key="signin.title",
        csrf=token,
        error=None,
        notice=notice,
        no_users=no_users,
        next=safe_next(request.query_params.get("next")),
        username="",
    )
    _set_cookie(response, state.pre_cookie_name, token, state, max_age=3600)
    return response


def _set_cookie(response: Response, name: str, value: str, state: ControlState, *, max_age: int) -> None:
    response.set_cookie(
        name, value, max_age=max_age, path="/", httponly=True, samesite="strict",
        secure=state.config.https,
    )


async def signin_submit(request: Request) -> Response:
    state = _state(request)
    form = await _form(request, None)
    now = state.now()
    remote = _remote(request)
    name = str(form.get("username") or "").strip().lower()[:64]
    password = str(form.get("password") or "")
    nxt = safe_next(str(form.get("next") or ""))
    known = state.users.get(name) is not None
    actor = name if known else "unknown"
    error = None
    locked = state.limiter.locked_until(name if known else None, remote, now)
    user = None
    if locked is None:
        user = state.users.verify(name, password)
        if user is None:
            locked = state.limiter.record_failure(name if known else None, remote, now)
            error = "signin.error.invalid"
            logger.warning("control_sign_in", component=COMPONENT, outcome="failure", reason="invalid", user=actor)
            _audit(request, actor, "sign_in_failed", "failure")
            if locked is not None:
                logger.warning("control_lockout", component=COMPONENT, outcome="failure", user=actor)
                _audit(request, actor, "lockout", "refused")
    if locked is not None:
        logger.warning("control_sign_in", component=COMPONENT, outcome="failure", reason="locked", user=actor)
        response = render(
            request, "signin.html", status=429, title_key="signin.title",
            csrf=request.cookies.get(state.pre_cookie_name, ""), notice=None, no_users=False,
            error=i18n.text(_lang_for(request, None), "signin.error.locked", time=i18n.fmt_clock(locked)),
            next=nxt, username=name if known else "",
        )
        return response
    if user is None:
        return render(
            request, "signin.html", status=401, title_key="signin.title",
            csrf=request.cookies.get(state.pre_cookie_name, ""), notice=None, no_users=False,
            error=i18n.text(_lang_for(request, None), error or "signin.error.invalid"),
            next=nxt, username="",
        )
    state.limiter.reset(user.name, remote)
    session = state.sessions.create(user.name, now)
    logger.info("control_sign_in", component=COMPONENT, outcome="success", user=user.name)
    _audit(request, user.name, "sign_in", "success")
    response = _see_other(nxt)
    _set_cookie(response, state.cookie_name, session.sid, state, max_age=SESSION_MAX_AGE_S)
    response.delete_cookie(state.pre_cookie_name, path="/")
    return response


async def signout(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request)
    await _form(request, session)
    state.sessions.delete(session.sid)
    state.attempts.discard(session.sid)
    _audit(request, user.name, "sign_out", "success")
    logger.info("control_sign_out", component=COMPONENT, outcome="success", user=user.name)
    response = _see_other("/signin?out=1")
    response.delete_cookie(state.cookie_name, path="/")
    return response


async def set_language(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request)
    form = await _form(request, session)
    lang = str(form.get("lang") or "")
    if lang not in LANGS:
        raise BadRequest()
    state.users.set_lang(user.name, lang)
    return _see_other(safe_next(str(form.get("next") or "/")))


# --- routes: read-only screens ----------------------------------------------------------


async def overview(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request)
    lang, now, env = user.lang, state.now(), state.env()
    lv = snapshot.listener_view(state.paths, now=now)
    tv = snapshot.token_view(env, now=now)
    alert_state = snapshot.alert_view(state.paths)
    jobs = snapshot.jobs_view(state.paths, now=now, state=alert_state)
    cv = snapshot.connection_view(state.paths, now=now)
    lcard = listener_card(lang, lv, now)
    gcard = google_card(lang, tv, state.last_test, now)
    cal_status, result, _ = await run_in_threadpool(_calendar, state, env, now)
    days = []
    if result is not None and cal_status == "ok":
        days = calendar_days(lang, result, until=now.date() + timedelta(days=3))
    dates, _, _ = important_dates(state, lang, now.date())
    return render(
        request, "overview.html", session=session, user=user, nav="overview", title_key="overview.title",
        banners=banners(lang, lv, tv, gcard, lcard, env, jobs),
        listener=lcard, connection=connection_card(lang, cv, now), recap=recap_card(lang, jobs, now),
        datesjob=dates_job_card(lang, jobs), google=gcard, alerts=alerts_card(lang, alert_state, jobs, now),
        cal_status=cal_status, days=days, dates=dates[:4], activity=activity_rows(state, lang, now, limit=5),
    )


async def calendar_page(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request)
    now, env = state.now(), state.env()
    cal_status, result, fetched = await run_in_threadpool(_calendar, state, env, now)
    days = calendar_days(user.lang, result) if (result is not None and cal_status == "ok") else []
    return render(
        request, "calendar.html", session=session, user=user, nav="calendar", title_key="calendar.title",
        cal_status=cal_status, days=days,
        fetched=i18n.fmt_clock(fetched, seconds=True) if fetched else None,
    )


async def dates_page(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request)
    rows, past, ok = important_dates(state, user.lang, state.now().date())
    return render(
        request, "dates.html", session=session, user=user, nav="dates", title_key="dates.title",
        dates=rows, past=past, ok=ok,
    )


def _launchd_rows(state: ControlState, lang: str) -> tuple[list[tuple[str, str, bool]], str | None]:
    t = partial(i18n.text, lang)
    info = print_service(state.deps.runner, uid=state.deps.uid, platform=state.deps.platform)
    if not info.available:
        return [], t("launchd.unavailable")
    if info.error and not info.loaded:
        return [], t("launchd.notloaded") if info.error.startswith("exit") else t("launchd.error")
    rows = []
    if info.state:
        rows.append((t("kv.launchd_state"), info.state, False))
    if info.pid is not None:
        rows.append((t("kv.process"), f"PID {info.pid}", True))
    if info.last_exit:
        rows.append((t("kv.last_exit"), info.last_exit, True))
    if info.runs is not None:
        rows.append((t("kv.runs"), str(info.runs), False))
    return rows, None


async def listener_page(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request)
    lang, now, env = user.lang, state.now(), state.env()
    lv = snapshot.listener_view(state.paths, now=now)
    card = listener_card(lang, lv, now)
    t = partial(i18n.text, lang)
    if lv.instance_id:
        card["kv"].append((t("kv.instance"), lv.instance_id[:8], True))
    if lv.exit_reason:
        card["kv"].append((t("kv.exit_reason"), lv.exit_reason, True))
    if lv.stuck_retries:
        card["kv"].append((t("kv.stuck_retries"), str(lv.stuck_retries), False))
    launchd_rows, launchd_note = await run_in_threadpool(_launchd_rows, state, lang)
    retries = (env.get(envfile.STUCK_RETRIES_KEY) or "").strip() or "3"
    return render(
        request, "listener.html", session=session, user=user, nav="system", sub="listener",
        title_key="listener.title", listener=card,
        connection=connection_card(lang, snapshot.connection_view(state.paths, now=now), now),
        launchd_rows=launchd_rows, launchd_note=launchd_note, retries=retries,
        needs_password=not session.password_fresh(now), error=None,
    )


async def jobs_page(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request)
    lang, now = user.lang, state.now()
    t = partial(i18n.text, lang)
    alert_state = snapshot.alert_view(state.paths)
    jobs = snapshot.jobs_view(state.paths, now=now, state=alert_state)
    cv = snapshot.connection_view(state.paths, now=now)
    checker = (
        {"tone": "ok", "body": t("jobs.checker.last", time=i18n.fmt_when(lang, cv.last_check, now=now))}
        if cv.last_check and now - cv.last_check < timedelta(minutes=15)
        else {"tone": "error" if cv.last_check is None else "warn",
              "body": t("jobs.checker.none") if cv.last_check is None
              else t("jobs.checker.last", time=i18n.fmt_when(lang, cv.last_check, now=now))}
    )
    digest = {"tone": "ok" if jobs.digest_today else ("unknown" if now.hour < 9 else "warn"),
              "body": t("jobs.digest.posted" if jobs.digest_today else "jobs.digest.notyet")}
    pending = [{"kind": t(f"jobs.kind.{kind}"), "name": name} for kind, name in jobs.pending]
    return render(
        request, "jobs.html", session=session, user=user, nav="system", sub="jobs", title_key="jobs.title",
        recap=recap_card(lang, jobs, now), datesjob=dates_job_card(lang, jobs), checker=checker,
        digest=digest, pending=pending,
    )


async def activity_page(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request)
    lang, now = user.lang, state.now()
    cv = snapshot.connection_view(state.paths, now=now)
    alerts = [
        {"time": i18n.fmt_when(lang, at, now=now), "text": i18n.text(lang, f"act.{action}")}
        for at, action in cv.alerts
    ]
    return render(
        request, "activity.html", session=session, user=user, nav="system", sub="activity",
        title_key="activity.title", rows=activity_rows(state, lang, now, limit=100), alerts=alerts,
    )


def _channel_rows(lang: str, env: Mapping[str, str]) -> list[dict[str, Any]]:
    rows = []
    for key in envfile.CHANNEL_KEYS:
        value = (env.get(key) or "").strip()
        rows.append({"key": key, "name": i18n.text(lang, f"channels.{key}"), "value": value})
    return rows


async def channels_page(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request)
    env = state.env()
    presence = envfile.secret_presence(env)
    return render(
        request, "channels.html", session=session, user=user, nav="settings", sub="channels",
        title_key="channels.title", channels=_channel_rows(user.lang, env),
        fallback=not (env.get(envfile.CHANNEL_KEYS[2]) or "").strip(),
        secrets=[(key, presence[key]) for key in envfile.SECRET_KEYS],
    )


def _google_context(state: ControlState, session: Session, user: User, **extra: Any) -> dict[str, Any]:
    now, env = state.now(), state.env()
    lang = user.lang
    tv = snapshot.token_view(env, now=now)
    attempt = state.attempts.get(session.sid)
    if attempt is not None and attempt.expired(now):
        # Keep it: a late paste then gets "older than 10 minutes", not "no sign-in".
        attempt = None
    url = None
    if attempt is not None and (env.get("GOOGLE_CLIENT_ID") or "").strip():
        url = consent_url(env["GOOGLE_CLIENT_ID"].strip(), attempt)
    return {
        "google": google_card(lang, tv, state.last_test, now),
        "attempt": attempt is not None,
        "attempt_until": i18n.fmt_clock(attempt.expires_at) if attempt else None,
        "consent_url": url,
        "needs_password": not session.password_fresh(now),
        "error": None,
        "result": None,
        "restart": None,
        **extra,
    }


async def google_page(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request)
    return render(
        request, "google.html", session=session, user=user, nav="settings", sub="google",
        title_key="gpage.title", **_google_context(state, session, user),
    )


async def account_page(request: Request) -> Response:
    session, user = _require(request)
    return render(request, "account.html", session=session, user=user, nav="settings", sub="account",
                  title_key="account.title")


async def users_page(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request, admin=True)
    try:
        users = sorted(state.users.load().values(), key=lambda u: u.name)
    except UserStoreError:
        users = []
    return render(request, "users.html", session=session, user=user, nav="settings", sub="users",
                  title_key="users.title", users=users)


def _redirect(target: str) -> Callable[[Request], Any]:
    async def endpoint(request: Request) -> Response:
        _require(request)
        return _see_other(target)

    return endpoint


# --- routes: Google access ------------------------------------------------------------


async def google_test(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request)
    form = await _form(request, session)
    env = state.env()
    _action_log("control_action_started", "google_test", user=user.name)
    started = time.perf_counter()
    try:
        cfg = load_google_calendar_config(env)
    except CalendarWriterConfigError:
        ok = False
        _set_flash(request, session, Flash("error", "google.test.config"))
    else:
        client = state.deps.calendar_factory(cfg)
        ok = await run_in_threadpool(probe_google_client, client)
        _set_flash(request, session, Flash("ok" if ok else "error", "google.test.done_ok" if ok else "google.test.fail"))
    state.last_test = (state.now(), ok)
    _audit(request, user.name, "google_test", "success" if ok else "failure")
    _action_log("control_action_finished", "google_test", outcome="success" if ok else "failure",
                duration_ms=int((time.perf_counter() - started) * 1000))
    return _see_other(safe_next(str(form.get("next") or "/settings/google")))


async def google_start(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request)
    form = await _form(request, session)
    env = state.env()
    if not (env.get("GOOGLE_CLIENT_ID") or "").strip() or not (env.get("GOOGLE_CLIENT_SECRET") or "").strip():
        return render(request, "google.html", session=session, user=user, nav="settings", sub="google",
                      title_key="gpage.title", **_google_context(state, session, user, error="google.err.config"))
    problem = _password_check(request, session, user, form)
    if problem is not None:
        return render(request, "google.html", session=session, user=user, nav="settings", sub="google",
                      title_key="gpage.title",
                      **_google_context(state, session, user, needs_password=True,
                                        error=None if problem == "reauth.body" else problem))
    state.attempts.start(session.sid, user=user.name, now=state.now())
    _audit(request, user.name, "google_reauth_start", "success")
    _action_log("control_action_started", "google_reauth", user=user.name)
    return _see_other("/settings/google")


REAUTH_RESTART = "google_reauth"
_REAUTH_REFRESH = "5;url=/settings/google/restart"
_DISCARD_ON = frozenset({"state", "expired", "denied", "exchange", "no_refresh", "scope", "no_attempt"})


async def google_finish(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request)
    form = await _form(request, session)
    started = time.perf_counter()
    now = state.now()
    env = state.env()
    attempt = state.attempts.get(session.sid)

    def fail(kind: str, detail: str | None = None) -> Response:
        if kind in _DISCARD_ON:
            state.attempts.discard(session.sid)
        logger.error("control_oauth_failed", component=COMPONENT, outcome="failure",
                     error_class=kind, detail=detail)
        _audit(request, user.name, "google_reauth", "failure", detail=kind)
        _action_log("control_action_finished", "google_reauth", outcome="failure",
                    duration_ms=int((time.perf_counter() - started) * 1000))
        return render(request, "google.html", status=400, session=session, user=user, nav="settings",
                      sub="google", title_key="gpage.title",
                      **_google_context(state, session, user, error=f"google.err.{kind}"))

    client_id = (env.get("GOOGLE_CLIENT_ID") or "").strip()
    client_secret = (env.get("GOOGLE_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        return fail("config")
    try:
        params = parse_redirect(str(form.get("address") or ""))
        code = check_redirect(params, attempt, now=now)
        assert attempt is not None
        grant = await run_in_threadpool(
            partial(exchange_code, state.deps.token_http, client_id=client_id,
                    client_secret=client_secret, code=code, attempt=attempt)
        )
    except OAuthError as exc:
        return fail(exc.kind, exc.detail)
    issued = issued_at_value(now)
    try:
        await run_in_threadpool(
            partial(envfile.update_env_file, state.config.env_path,
                    {"GOOGLE_REFRESH_TOKEN": grant.refresh_token, "GOOGLE_REFRESH_TOKEN_ISSUED_AT": issued},
                    backup_dir=state.config.backup_dir, now=now)
        )
    except (OSError, envfile.EnvWriteError) as exc:
        logger.error("control_env_write_failed", component=COMPONENT, outcome="failure",
                     action="google_reauth", error_type=type(exc).__name__)
        return fail("write")
    state.attempts.discard(session.sid)
    state.calendar_cache = None
    new_env = state.env()
    probe_ok = False
    try:
        cfg = load_google_calendar_config(new_env)
        probe_ok = await run_in_threadpool(probe_google_client, state.deps.calendar_factory(cfg))
    except CalendarWriterConfigError:
        probe_ok = False
    state.last_test = (state.now(), probe_ok)
    tv = snapshot.token_view(new_env, now=now)
    expires = tv.expires_on or now.date()
    _audit(request, user.name, "google_reauth", "success")
    notice_ok = await run_in_threadpool(
        partial(_announce, state, "notice.google", user=user.name, time=i18n.fmt_clock(now),
                date=i18n.per_lang(day=expires))
    )
    _action_log("control_action_finished", "google_reauth", outcome="success", probe_ok=probe_ok,
                duration_ms=int((time.perf_counter() - started) * 1000))
    # The token is on disk; the listener reads .env only at start. Restart it
    # now for either role, even if the probe failed, and skip the manual
    # 5-minute gap: Save access is the confirmed human action, and a refusal
    # would leave Slack replies on the old token (ADR 0016 amendment). No
    # second password: the attempt is session-bound and at most 10 minutes old.
    code = await _perform_listener_restart(request, user, reason=REAUTH_RESTART)
    if code is None:
        phase, text = _restart_status(state, user.lang, copy="google.restart")
    else:
        phase, text = "failed", i18n.text(user.lang, "google.restart.failed", code=code)
    result = {
        "date": i18n.fmt_date(user.lang, expires),
        "probe_ok": probe_ok,
        "notice_ok": notice_ok,
    }
    return render(request, "google.html", session=session, user=user, nav="settings", sub="google",
                  title_key="gpage.title", refresh=_REAUTH_REFRESH if phase == "waiting" else None,
                  **_google_context(state, session, user, result=result,
                                    restart={"phase": phase, "message": text}))


async def google_restart_status(request: Request) -> Response:
    """Read-only restart progress after re-authorization, for both roles.

    The admin status page is admin-only; a member who saved access still needs
    to see whether Slack replies use it. Shows only a re-authorization restart.
    """
    state = _state(request)
    session, user = _require(request)
    info = state.last_restart
    if info is None or info.reason != REAUTH_RESTART:
        return _see_other("/settings/google")
    phase, text = _restart_status(state, user.lang, copy="google.restart")
    return render(request, "google.html", session=session, user=user, nav="settings", sub="google",
                  title_key="gpage.title", refresh=_REAUTH_REFRESH if phase == "waiting" else None,
                  **_google_context(state, session, user, restart={"phase": phase, "message": text}))


# --- routes: admin actions -------------------------------------------------------------


def _channel_key(raw: object) -> str:
    key = str(raw or "")
    if key not in envfile.CHANNEL_KEYS:
        raise BadRequest()
    return key


def _channel_ctx(user: User, env: Mapping[str, str], key: str, **extra: Any) -> dict[str, Any]:
    return {
        "key": key,
        "name": i18n.text(user.lang, f"channels.{key}"),
        "current": (env.get(key) or "").strip(),
        "value": (env.get(key) or "").strip(),
        "error": None,
        **extra,
    }


async def channel_edit(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request, admin=True)
    key = _channel_key(request.query_params.get("key"))
    return render(request, "channel_edit.html", session=session, user=user, nav="settings", sub="channels",
                  title_key="channels.title", **_channel_ctx(user, state.env(), key))


async def channel_preview(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request, admin=True)
    form = await _form(request, session)
    key = _channel_key(form.get("key"))
    env = state.env()
    value = str(form.get("value") or "").strip().upper()
    ctx = _channel_ctx(user, env, key, value=value)
    if not envfile.valid_channel_id(value):
        return render(request, "channel_edit.html", status=400, session=session, user=user, nav="settings",
                      sub="channels", title_key="channels.title", **{**ctx, "error": "channels.error.format"})
    if value == ctx["current"]:
        return render(request, "channel_edit.html", session=session, user=user, nav="settings",
                      sub="channels", title_key="channels.title", **{**ctx, "error": "channels.same"})
    return render(request, "channel_preview.html", session=session, user=user, nav="settings", sub="channels",
                  title_key="channels.title", needs_password=not session.password_fresh(state.now()), **ctx)


async def channel_save(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request, admin=True)
    form = await _form(request, session)
    key = _channel_key(form.get("key"))
    env = state.env()
    value = str(form.get("value") or "").strip().upper()
    ctx = _channel_ctx(user, env, key, value=value)
    if not envfile.valid_channel_id(value):
        return render(request, "channel_edit.html", status=400, session=session, user=user, nav="settings",
                      sub="channels", title_key="channels.title", **{**ctx, "error": "channels.error.format"})
    problem = _password_check(request, session, user, form)
    if problem is not None:
        return render(request, "channel_preview.html", session=session, user=user, nav="settings",
                      sub="channels", title_key="channels.title", needs_password=True,
                      password_error=None if problem == "reauth.body" else problem, **ctx)
    old = ctx["current"]
    _action_log("control_action_started", "channel_change", user=user.name, key=key)
    try:
        envfile.update_env_file(state.config.env_path, {key: value}, backup_dir=state.config.backup_dir,
                                now=state.now())
    except (OSError, envfile.EnvWriteError) as exc:
        logger.error("control_env_write_failed", component=COMPONENT, outcome="failure",
                     action="channel_change", error_type=type(exc).__name__)
        _audit(request, user.name, "channel_change", "failure", target=key, old=old or None, new=value)
        return render(request, "channel_edit.html", status=500, session=session, user=user, nav="settings",
                      sub="channels", title_key="channels.title", **{**ctx, "error": "channels.error.write"})
    _audit(request, user.name, "channel_change", "success", target=key, old=old or None, new=value)
    notice_ok = await run_in_threadpool(
        partial(_announce, state, "notice.channel", user=user.name, time=i18n.fmt_clock(state.now()),
                name=i18n.per_lang(f"channels.{key}"))
    )
    _action_log("control_action_finished", "channel_change", outcome="success", key=key, notice_ok=notice_ok)
    _set_flash(request, session, Flash("ok", "channels.saved", restart_prompt=True))
    return _see_other("/settings/channels")


async def channel_test_confirm(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request, admin=True)
    key = _channel_key(request.query_params.get("key"))
    return render(request, "channel_test.html", session=session, user=user, nav="settings", sub="channels",
                  title_key="channels.title", **_channel_ctx(user, state.env(), key))


async def channel_test_send(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request, admin=True)
    form = await _form(request, session)
    key = _channel_key(form.get("key"))
    env = state.env()
    channel = (env.get(key) or "").strip()
    bot = (env.get("SLACK_BOT_TOKEN") or "").strip()
    if not envfile.valid_channel_id(channel):
        _set_flash(request, session, Flash("error", "channels.error.format"))
        return _see_other("/settings/channels")
    if not bot:
        _set_flash(request, session, Flash("error", "channels.test.notoken"))
        return _see_other("/settings/channels")

    def send() -> bool:
        try:
            state.deps.poster_factory(bot).post(channel_id=channel, text=i18n.both("notice.test"))
        except Exception as exc:  # noqa: BLE001 — shown to the admin as failure copy
            logger.error("control_test_message_failed", component=COMPONENT, outcome="failure",
                         error_type=type(exc).__name__)
            return False
        return True

    ok = await run_in_threadpool(send)
    _audit(request, user.name, "channel_test", "success" if ok else "failure", target=key)
    _set_flash(request, session, Flash("ok" if ok else "error", "channels.test.sent" if ok else "channels.test.failed"))
    return _see_other("/settings/channels")


async def stuck_retries_save(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request, admin=True)
    form = await _form(request, session)
    raw = str(form.get("value") or "").strip()
    env = state.env()
    old = (env.get(envfile.STUCK_RETRIES_KEY) or "").strip() or None

    def again(error: str | None, *, needs_password: bool = False, status: int = 400) -> Response:
        now = state.now()
        lv = snapshot.listener_view(state.paths, now=now)
        rows, note = _launchd_rows(state, user.lang)
        return render(
            request, "listener.html", status=status, session=session, user=user, nav="system", sub="listener",
            title_key="listener.title", listener=listener_card(user.lang, lv, now),
            connection=connection_card(user.lang, snapshot.connection_view(state.paths, now=now), now),
            launchd_rows=rows, launchd_note=note, retries=raw or old or "3",
            needs_password=needs_password or not session.password_fresh(now), error=error,
        )

    if not raw.isdigit() or not 0 <= int(raw) <= 10:
        return again("listener.stuck.invalid")
    value = str(int(raw))
    if value == (old or "3") and old is not None:
        return again("listener.stuck.same", status=200)
    problem = _password_check(request, session, user, form)
    if problem is not None:
        return again(None if problem == "reauth.body" else problem, needs_password=True, status=200)
    try:
        envfile.update_env_file(state.config.env_path, {envfile.STUCK_RETRIES_KEY: value},
                                backup_dir=state.config.backup_dir, now=state.now())
    except (OSError, envfile.EnvWriteError) as exc:
        logger.error("control_env_write_failed", component=COMPONENT, outcome="failure",
                     action="stuck_retries", error_type=type(exc).__name__)
        _audit(request, user.name, "stuck_retries", "failure", old=old, new=value)
        return again("channels.error.write", status=500)
    _audit(request, user.name, "stuck_retries", "success", old=old, new=value)
    await run_in_threadpool(
        partial(_announce, state, "notice.stuck", user=user.name, n=value, time=i18n.fmt_clock(state.now()))
    )
    _set_flash(request, session, Flash("ok", "listener.stuck.saved", restart_prompt=True))
    return _see_other("/system/listener")


def _restart_refusal(state: ControlState, lang: str) -> str | None:
    if state.deps.platform != "darwin":
        return i18n.text(lang, "restart.unavailable")
    last = state.last_restart
    if last is not None and state.now() - last.at < RESTART_GAP:
        return i18n.text(lang, "restart.limit", time=i18n.fmt_clock(last.at + RESTART_GAP))
    return None


async def restart_confirm(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request, admin=True)
    return render(request, "restart_confirm.html", session=session, user=user, nav="system", sub="listener",
                  title_key="restart.title", refusal=_restart_refusal(state, user.lang),
                  needs_password=not session.password_fresh(state.now()), error=None)


def _restart_status(state: ControlState, lang: str, *, copy: str = "restart") -> tuple[str, str]:
    """``(phase, text)``; phase done | waiting | timeout. ``copy`` is the key prefix."""
    info = state.last_restart
    now = state.now()
    lv = snapshot.listener_view(state.paths, now=now)
    if info is not None and lv.instance_id and lv.instance_id != info.old_instance and lv.state == "running":
        return "done", i18n.text(lang, f"{copy}.done", time=i18n.fmt_clock(lv.started_at or now),
                                 pid=lv.pid if lv.pid is not None else "?")
    if info is None or now - info.at > RESTART_WAIT:
        return "timeout", i18n.text(lang, f"{copy}.timeout")
    return "waiting", i18n.text(lang, f"{copy}.waiting", time=i18n.fmt_clock(info.at))


async def _perform_listener_restart(request: Request, user: User, *, reason: str) -> str | None:
    """Kickstart the listener once; None on success, else a short failure code.

    Why: the confirmed manual restart and the restart right after new Google
    access must be one action: the fixed launchd argv, audit
    ``listener_restart``, the ``notice.restart`` status post and
    ``last_restart`` (status polling and the 5-minute gap).
    Contract: the caller owns the gate (role, confirmation, password). This
    helper never checks ``RESTART_GAP``: ``restart_submit`` checks it, and
    ``google_finish`` skips it on purpose because the new token is unused until
    the restart (ADR 0016 amendment). It still records ``last_restart``, so a
    manual restart within 5 minutes is refused. Do not call it from a GET, a
    health reading or a timer.
    """
    state = _state(request)
    before = snapshot.listener_view(state.paths, now=state.now())
    _action_log("control_action_started", "listener_restart", user=user.name, reason=reason)
    started = time.perf_counter()
    try:
        result = await run_in_threadpool(
            partial(kickstart_listener, state.deps.runner, uid=state.deps.uid, platform=state.deps.platform)
        )
        code: str | None = None if result.returncode == 0 else str(result.returncode)
    except RestartUnavailable:
        code = "unavailable"
    except Exception as exc:  # noqa: BLE001 — shown as the failure copy
        code = type(exc).__name__
    if code is not None:
        _audit(request, user.name, "listener_restart", "failure", detail=code, reason=reason)
        _action_log("control_action_finished", "listener_restart", outcome="failure", detail=code,
                    reason=reason, duration_ms=int((time.perf_counter() - started) * 1000))
        return code
    state.last_restart = RestartInfo(state.now(), user.name, before.instance_id, before.pid, reason)
    _audit(request, user.name, "listener_restart", "success", reason=reason)
    await run_in_threadpool(
        partial(_announce, state, "notice.restart", user=user.name, time=i18n.fmt_clock(state.now()))
    )
    _action_log("control_action_finished", "listener_restart", outcome="success", reason=reason,
                duration_ms=int((time.perf_counter() - started) * 1000))
    return None


async def restart_submit(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request, admin=True)
    form = await _form(request, session)
    refusal = _restart_refusal(state, user.lang)
    if refusal is not None:
        _audit(request, user.name, "listener_restart", "refused")
        return render(request, "restart_confirm.html", status=429 if state.deps.platform == "darwin" else 409,
                      session=session, user=user, nav="system", sub="listener", title_key="restart.title",
                      refusal=refusal, needs_password=False, error=None)
    problem = _password_check(request, session, user, form)
    if problem is not None:
        return render(request, "restart_confirm.html", session=session, user=user, nav="system",
                      sub="listener", title_key="restart.title", refusal=None, needs_password=True,
                      error=None if problem == "reauth.body" else problem)
    code = await _perform_listener_restart(request, user, reason="manual")
    if code is not None:
        return render(request, "restart_confirm.html", status=502, session=session, user=user, nav="system",
                      sub="listener", title_key="restart.title",
                      refusal=i18n.text(user.lang, "restart.failed", code=code), needs_password=False, error=None)
    phase, text = _restart_status(state, user.lang)
    return render(request, "restart_result.html", session=session, user=user, nav="system", sub="listener",
                  title_key="restart.title", phase=phase, message=text,
                  refresh="5;url=/system/listener/restart/status" if phase == "waiting" else None)


async def restart_status(request: Request) -> Response:
    state = _state(request)
    session, user = _require(request, admin=True)
    phase, text = _restart_status(state, user.lang)
    return render(request, "restart_result.html", session=session, user=user, nav="system", sub="listener",
                  title_key="restart.title", phase=phase, message=text,
                  refresh="5;url=/system/listener/restart/status" if phase == "waiting" else None)


# --- demo -----------------------------------------------------------------------------


async def demo_signin(request: Request) -> Response:
    """Demo only (loopback, synthetic data): sign in as the first user of a role."""
    state = _state(request)
    if not state.config.demo:
        raise HTTPException(404)
    role = request.path_params["role"]
    users = [u for u in state.users.load().values() if u.role == role]
    if not users:
        raise HTTPException(404)
    if request.query_params.get("lang") in LANGS:
        state.users.set_lang(users[0].name, request.query_params["lang"])
    session = state.sessions.create(users[0].name, state.now())
    if request.query_params.get("oauth"):
        state.attempts.start(session.sid, user=users[0].name, now=state.now())
    if request.query_params.get("stale"):
        session.password_at = state.now() - timedelta(hours=1)
    response = _see_other(safe_next(request.query_params.get("next")))
    _set_cookie(response, state.cookie_name, session.sid, state, max_age=SESSION_MAX_AGE_S)
    return response


async def font_file(request: Request) -> Response:
    if not FONT_FILE.is_file():
        raise HTTPException(404)
    return FileResponse(FONT_FILE, media_type="font/ttf")


# --- error handlers ------------------------------------------------------------------


async def _not_signed_in(request: Request, exc: Exception) -> Response:
    state = _state(request)
    had_cookie = bool(request.cookies.get(state.cookie_name))
    if request.method == "GET":
        target = f"/signin?next={quote(safe_next(request.url.path), safe='/')}"
        if had_cookie:
            target += "&expired=1"
        response = _see_other(target)
    else:
        response = render(request, "error.html", status=401, title_key="signin.title",
                          message_key="session.expired", signin_link=True)
    if had_cookie:
        response.delete_cookie(state.cookie_name, path="/")
    return response


async def _forbidden(request: Request, exc: Exception) -> Response:
    found = _current(request)
    if found:
        _audit(request, found[1].name, "forbidden", "refused", detail=request.url.path[:60])
    return _error_page(request, 403, "403")


async def _csrf(request: Request, exc: Exception) -> Response:
    logger.warning("control_csrf_rejected", component=COMPONENT, outcome="failure", path=request.url.path)
    return _error_page(request, 403, "csrf")


async def _bad_request(request: Request, exc: Exception) -> Response:
    return _error_page(request, 400, "400")


async def _http_error(request: Request, exc: Exception) -> Response:
    code = getattr(exc, "status_code", 500)
    if code == 404:
        return _error_page(request, 404, "404")
    if code == 405:
        return PlainTextResponse("Method not allowed.\n", 405)
    return _error_page(request, code if code in (400, 403) else 500, "400" if code == 400 else "500")


async def _server_error(request: Request, exc: Exception) -> Response:
    logger.error("control_request_failed", component=COMPONENT, outcome="failure",
                 path=request.url.path, error_type=type(exc).__name__)
    return _error_page(request, 500, "500")


# --- factory ---------------------------------------------------------------------------


def create_app(config: ControlConfig, deps: ControlDeps | None = None) -> Starlette:
    deps = deps if deps is not None else ControlDeps()
    state = ControlState(config, deps)
    routes = [
        Route("/signin", signin_page, methods=["GET"]),
        Route("/signin", signin_submit, methods=["POST"]),
        Route("/signout", signout, methods=["POST"]),
        Route("/lang", set_language, methods=["POST"]),
        Route("/", overview, methods=["GET"]),
        Route("/calendar", calendar_page, methods=["GET"]),
        Route("/dates", dates_page, methods=["GET"]),
        Route("/system", _redirect("/system/listener"), methods=["GET"]),
        Route("/system/listener", listener_page, methods=["GET"]),
        Route("/system/listener/restart", restart_confirm, methods=["GET"]),
        Route("/system/listener/restart", restart_submit, methods=["POST"]),
        Route("/system/listener/restart/status", restart_status, methods=["GET"]),
        Route("/system/listener/stuck-retries", stuck_retries_save, methods=["POST"]),
        Route("/system/jobs", jobs_page, methods=["GET"]),
        Route("/system/activity", activity_page, methods=["GET"]),
        Route("/settings", _redirect("/settings/channels"), methods=["GET"]),
        Route("/settings/channels", channels_page, methods=["GET"]),
        Route("/settings/channels/edit", channel_edit, methods=["GET"]),
        Route("/settings/channels/preview", channel_preview, methods=["POST"]),
        Route("/settings/channels/save", channel_save, methods=["POST"]),
        Route("/settings/channels/test", channel_test_confirm, methods=["GET"]),
        Route("/settings/channels/test", channel_test_send, methods=["POST"]),
        Route("/settings/google", google_page, methods=["GET"]),
        Route("/settings/google/test", google_test, methods=["POST"]),
        Route("/settings/google/start", google_start, methods=["POST"]),
        Route("/settings/google/finish", google_finish, methods=["POST"]),
        Route("/settings/google/restart", google_restart_status, methods=["GET"]),
        Route("/settings/account", account_page, methods=["GET"]),
        Route("/settings/users", users_page, methods=["GET"]),
        Route("/static/fonts/noto-sans-tc.ttf", font_file, methods=["GET"]),
        Mount("/static", app=StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static"),
    ]
    if config.demo:
        routes.append(Route("/demo/signin/{role}", demo_signin, methods=["GET"]))
    app = Starlette(
        routes=routes,
        middleware=[Middleware(GuardMiddleware, state=state)],
        exception_handlers={
            NotSignedIn: _not_signed_in,
            Forbidden: _forbidden,
            CsrfError: _csrf,
            BadRequest: _bad_request,
            HTTPException: _http_error,
            Exception: _server_error,
        },
    )
    app.state.ctx = state
    app.state.templates = _templates()
    state.maintain()
    return app
