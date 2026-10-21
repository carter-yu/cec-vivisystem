"""Phase 31a: read-only control app (locked cases C1–C17).

Offline: Starlette TestClient, fixed HKT clock, temporary data dir, fake
Calendar client, fake poster and fake process runner.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta

import pytest

pytest.importorskip("starlette", reason="control extra not installed: uv sync --extra control")

from structlog.testing import capture_logs

from cec_vivisystem.control.audit import AuditLog
from cec_vivisystem.important_dates import JsonDirImportantDatesStore
from cec_vivisystem.models import ImportantDate, ImportantDateKind
from tests.control_support import (
    ADMIN,
    ENV_TEXT,
    HKT,
    MEMBER,
    NOW,
    PASSPHRASE,
    RefreshError,
    csrf_of,
    ev,
    make_harness,
    page_csrf,
    sign_in,
    write_json,
)

GET_ROUTES = [
    "/", "/calendar", "/dates", "/system/listener", "/system/jobs", "/system/activity",
    "/settings/channels", "/settings/google", "/settings/google/restart", "/settings/account",
]
ADMIN_GET_ROUTES = [
    "/settings/users", "/system/listener/restart", "/system/listener/restart/status",
    "/settings/channels/edit?key=CEC_HEALTH_ALERT_CHANNEL_ID",
    "/settings/channels/test?key=SLACK_FAMILY_PLANS_CHANNEL_ID",
]


@pytest.fixture
def h(tmp_path):
    return make_harness(tmp_path)


def _samples(h, rows):
    folder = h.config.data_dir / "health" / "samples"
    folder.mkdir(parents=True, exist_ok=True)
    by_day: dict[str, list[str]] = {}
    for row in rows:
        by_day.setdefault(row["checked_at"][:10], []).append(json.dumps(row))
    for day, lines in by_day.items():
        (folder / f"{day}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _healthy_samples(h, *, down_at: tuple[int, ...] = ()):
    rows, down_s, episodes = [], 0.0, 0
    t = NOW - timedelta(hours=24, minutes=5)
    i = 0
    while t <= NOW:
        down = i in down_at
        if down:
            down_s += 300
            episodes += 1
        rows.append({
            "checked_at": t.isoformat(), "status": "ok", "reason": "fresh", "pid": 4182,
            "instance_id": "aaaa1111bbbb2222cccc3333dddd4444", "connected": not down,
            "started_at": (NOW - timedelta(hours=30)).isoformat(), "outage_since": None,
            "disconnected_s": down_s, "disconnect_episodes": episodes,
            "alert_phase": "ok", "alert_action": "unchanged",
        })
        t += timedelta(minutes=5)
        i += 1
    _samples(h, rows)


# --- C1 / C2 / C3 / C4: overview states ---------------------------------------------


def test_c1_overview_healthy_in_english_and_traditional_chinese(h):
    _healthy_samples(h)
    write_json(h.config.data_dir / "morning_recap" / "2026-10-20.json",
               {"status": "posted", "posted_at": NOW.replace(hour=7, minute=0, second=6).isoformat()})
    (h.config.log_dir).mkdir(parents=True, exist_ok=True)
    (h.config.log_dir / "important_dates-2026-10-20.log").write_text(
        "event='important_dates_review_skipped' level='info'\n", encoding="utf-8")
    sign_in(h)
    page = h.client.get("/")
    assert page.status_code == 200
    body = page.text
    for words in ("Running", "Connected to Slack", "Stable", "Posted at 07:00:06.", "Ran",
                  "6 days left", "None open", "PID 4182"):
        assert words in body, words
    assert "●" in body and "▲" not in body.split("<main")[1].split("Next 3 days")[0]
    csrf = csrf_of(body)
    switched = h.client.post("/lang", data={"csrf": csrf, "lang": "zh-Hant-HK", "next": "/"})
    assert switched.status_code == 303
    tc = h.client.get("/").text
    for words in ("運行中", "已連接 Slack", "穩定", "已於 07:00:06 發送。", "尚餘 6 日", "沒有未處理"):
        assert words in tc, words
    assert '<html lang="zh-Hant-HK">' in tc


@pytest.mark.parametrize(
    ("payload", "state_word", "detail"),
    [
        ({"socket_mode_connected": False, "outage_since": (NOW - timedelta(minutes=25)).isoformat(),
          "disconnected_since": (NOW - timedelta(minutes=25)).isoformat()},
         "No Slack connection", "No Slack connection from 20:39 (25 minutes)."),
        ({"written_at": (NOW - timedelta(minutes=20)).isoformat()}, "Stopped",
         "The listener stopped at 20:44. The heartbeat is 20 minutes old."),
        (None, "No data", "No heartbeat data."),
        ("{garbage", "Data not valid", "The heartbeat data is not valid."),
    ],
)
def test_c2_listener_stale_missing_or_garbage_never_500(tmp_path, payload, state_word, detail):
    h = make_harness(tmp_path, write_heartbeat=False)
    path = h.config.data_dir / "health" / "listener.json"
    if isinstance(payload, dict):
        h.heartbeat(**payload)
    elif isinstance(payload, str):
        write_json(path, payload)
    sign_in(h)
    for route in ("/", "/system/listener"):
        page = h.client.get(route)
        assert page.status_code == 200
        assert state_word in page.text
    assert detail in h.client.get("/system/listener").text


@pytest.mark.parametrize(
    ("issued", "banner_class", "text"),
    [
        ("2026-10-15T09:00:00+08:00", "banner--warn", "Google access expires in 2 days (Thu 22 Oct)."),
        ("2026-10-13T09:00:00+08:00", "banner--error", "Google access expires today."),
        ("2026-10-08T09:00:00+08:00", "banner--error", "Google access stopped on Thu 15 Oct."),
        ("", "banner--info", "The issue date of Google access is not set."),
    ],
)
def test_c3_token_banner_states(tmp_path, issued, banner_class, text):
    env = ENV_TEXT.replace("GOOGLE_REFRESH_TOKEN_ISSUED_AT=2026-10-19T21:00:00+08:00",
                           f"GOOGLE_REFRESH_TOKEN_ISSUED_AT={issued}")
    h = make_harness(tmp_path, env_text=env)
    sign_in(h)
    body = h.client.get("/").text
    assert text in body
    banner = body.split(text)[0].rsplit('<div class="banner ', 1)[1]
    assert banner.startswith(banner_class)
    assert 'action="/settings/google/start"' in body


def test_c4_pending_recap_shows_reconciliation_note_and_no_run_button(h):
    write_json(h.config.data_dir / "morning_recap" / "2026-10-20.json", {"status": "pending"})
    sign_in(h)
    overview = h.client.get("/").text
    assert "The delivery is not confirmed. Do not start the recap again." in overview
    assert "Scheduled deliveries that are not confirmed: 1." in overview
    jobs = h.client.get("/system/jobs").text
    assert "ADR 0009" in jobs and "2026-10-20" in jobs
    for route in GET_ROUTES + ADMIN_GET_ROUTES:
        text = h.client.get(route).text
        actions = re.findall(r'action="([^"]+)"', text)
        assert not any(re.search(r"run|recap|job|reconcil", a) for a in actions), (route, actions)


# --- C5 / C6: calendar and important dates ----------------------------------------------


def test_c5_calendar_grouped_by_hkt_day_with_exclusive_end_and_all_day(h):
    day = datetime(2026, 10, 20, tzinfo=HKT)
    h.calendar.events = [
        ev("a", "School run", day.replace(hour=8, minute=15), day.replace(hour=8, minute=45), who=["Kid"]),
        ev("b", "Playgroup", day + timedelta(days=2), day + timedelta(days=3), all_day=True),
        ev("c", "Trip", day + timedelta(days=4), day + timedelta(days=6), all_day=True),
        ev("d", "Late show", day.replace(hour=23), day + timedelta(days=1, hours=1)),
    ]
    sign_in(h)
    body = h.client.get("/calendar").text
    labels = re.findall(r'class="day__label">([^<]+)<', body)
    assert labels == ["Tue 20 Oct", "Wed 21 Oct", "Thu 22 Oct", "Sat 24 Oct", "Sun 25 Oct"]
    assert "08:15–08:45" in body and "Kid" in body
    assert body.count(">Trip<") == 2  # two all-day days; exclusive end excludes 26 Oct
    assert ">Continued<" in body  # the late show continues after midnight
    assert body.count(">All day<") == 3
    assert "Calendar data from 21:04:12." in body
    h.client.get("/calendar")
    assert h.calendar.list_calls == 1  # 60-s cache
    h.clock.advance(seconds=61)
    h.client.get("/calendar")
    assert h.calendar.list_calls == 2


def test_c5_calendar_failure_and_auth_error_have_explicit_copy(tmp_path):
    h = make_harness(tmp_path)
    h.calendar.fail_with = RuntimeError("boom")
    sign_in(h)
    body = h.client.get("/calendar").text
    assert "The bot cannot read the calendar now. No calendar change was made." in body
    h2 = make_harness(tmp_path / "auth")
    h2.calendar.fail_with = RefreshError("invalid_grant")
    sign_in(h2)
    body = h2.client.get("/calendar").text
    assert "Google access stopped. No calendar change was made." in body
    assert 'href="/settings/google"' in body
    assert "Google access stopped" in h2.client.get("/").text


def test_c6_important_dates_ordered_by_next_occurrence_read_only(h):
    store = JsonDirImportantDatesStore(h.config.data_dir / "important_dates")
    created = NOW - timedelta(days=10)
    for date_id, title, month, day, kind, year in (
        ("leap", "Leap-day birthday", 2, 29, ImportantDateKind.YEARLY, None),
        ("soon", "Grandma's birthday", 10, 24, ImportantDateKind.YEARLY, None),
        ("today", "Anniversary", 10, 20, ImportantDateKind.YEARLY, None),
        ("past", "Old fair", 1, 5, ImportantDateKind.ONE_OFF, 2026),
        ("oneoff", "Open day", 11, 15, ImportantDateKind.ONE_OFF, 2026),
    ):
        store.save(ImportantDate(date_id, title, month, day, kind, created, year=year))
    sign_in(h)
    body = h.client.get("/dates").text
    titles = [html.unescape(t) for t in re.findall(r'<span class="t">[^<]+</span><span>([^<]+)', body)]
    assert titles == ["Anniversary", "Grandma's birthday", "Open day", "Leap-day birthday"]
    assert "Tue 29 Feb" in body and "Today" in body and "In 4 days" in body
    assert "Past one-time dates: 1." in body
    assert set(re.findall(r'action="([^"]+)"', body)) == {"/lang", "/signout"}


# --- C7: sign-in, lockout, cookies, sign-out ------------------------------------------------


def test_c7_unauthenticated_get_redirects_to_sign_in(h):
    response = h.client.get("/system/listener")
    assert response.status_code == 303
    assert response.headers["location"] == "/signin?next=/system/listener"


def test_c7_lockout_after_five_failures_with_fixed_clock(h):
    for _ in range(4):
        assert sign_in(h, password="wrong-password-1").status_code == 401
    fifth = sign_in(h, password="wrong-password-1")
    assert fifth.status_code == 429
    assert "You can sign in again after 21:19." in fifth.text
    blocked = sign_in(h)  # correct password is refused while locked
    assert blocked.status_code == 429
    h.clock.advance(minutes=15, seconds=1)
    ok = sign_in(h)
    assert ok.status_code == 303
    rows = h.state.audit.recent()
    assert [r["action"] for r in rows].count("sign_in_failed") == 5
    assert any(r["action"] == "lockout" for r in rows)


def test_c7_unknown_user_gets_the_same_generic_error(h):
    response = sign_in(h, name="nobody", password="whatever-password")
    assert response.status_code == 401
    assert "The user name or the password is incorrect." in response.text
    assert h.state.audit.recent()[0]["actor"] == "unknown"


def test_c7_cookie_flags_http_and_sign_out(h):
    response = sign_in(h)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("cec_session=")
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict")
    assert "Secure" not in cookie
    assert h.client.get("/").status_code == 200
    out = h.client.post("/signout", data={"csrf": page_csrf(h)})
    assert out.status_code == 303 and out.headers["location"] == "/signin?out=1"
    assert h.client.get("/").status_code == 303
    assert h.state.sessions.count() == 0


def test_c7_cookie_flags_https(tmp_path):
    h = make_harness(tmp_path, https=True)
    response = sign_in(h)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-cec_session=")
    assert "Secure" in cookie and "HttpOnly" in cookie and "Path=/" in cookie
    assert "Strict-Transport-Security" in h.client.get("/").headers


def test_c7_session_idle_expiry(h):
    sign_in(h)
    h.clock.advance(days=7, seconds=1)
    response = h.client.get("/")
    assert response.status_code == 303 and "expired=1" in response.headers["location"]


# --- C8 / C9 / C11: CSRF, origin, host, network, headers -------------------------------


def test_c8_csrf_and_origin_checks(h):
    sign_in(h)
    csrf = page_csrf(h)
    assert h.client.post("/lang", data={"lang": "en", "next": "/"}).status_code == 403
    assert h.client.post("/lang", data={"csrf": "x" * 40, "lang": "en"}).status_code == 403
    assert h.client.post("/lang", data={"csrf": csrf, "lang": "en"}).status_code == 303
    evil = h.client.post("/lang", data={"csrf": csrf, "lang": "en"}, headers={"Origin": "http://evil.example"})
    assert evil.status_code == 403 and "This form is too old." in evil.text
    null = h.client.post("/lang", data={"csrf": csrf, "lang": "en"}, headers={"Origin": "null"})
    assert null.status_code == 403
    h.client.headers.pop("Origin")
    no_origin = h.client.post("/lang", data={"csrf": csrf, "lang": "en"})
    assert no_origin.status_code == 403
    referer = h.client.post("/lang", data={"csrf": csrf, "lang": "en"},
                            headers={"Referer": "http://127.0.0.1:8730/settings/account"})
    assert referer.status_code == 303


def test_c8_sign_in_post_needs_the_pre_session_token(h):
    h.client.get("/signin")
    response = h.client.post("/signin", data={"username": ADMIN, "password": PASSPHRASE})
    assert response.status_code == 403


def test_c9_host_and_client_network_allowlists(h):
    bad_host = h.client.get("/signin", headers={"Host": "evil.example"})
    assert bad_host.status_code == 400
    assert h.client.get("/signin", headers={"Host": "mac-mini.local:8730"}).status_code == 200
    from starlette.testclient import TestClient

    outsider = TestClient(h.app, base_url="http://127.0.0.1:8730", client=("10.9.9.9", 4000))
    assert outsider.get("/signin").status_code == 403
    public = TestClient(h.app, base_url="http://127.0.0.1:8730", client=("8.8.8.8", 4000))
    assert public.get("/static/control.css").status_code == 403


def test_c11_security_headers_on_pages_redirects_and_errors(h):
    for response in (h.client.get("/signin"), h.client.get("/"), h.client.get("/nope"),
                     h.client.get("/static/control.css")):
        headers = response.headers
        csp = headers["content-security-policy"]
        assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
        assert "form-action 'self'" in csp and "base-uri 'none'" in csp
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "same-origin"
        assert headers["cache-control"] == "no-store"
        assert headers["x-frame-options"] == "DENY"


# --- C10: secrets never leave the server ------------------------------------------------


SENTINELS = {
    "SLACK_BOT_TOKEN": "SENTINEL-bot-7f3a",
    "SLACK_APP_TOKEN": "SENTINEL-app-1c9d",
    "SLACK_SIGNING_SECRET": "SENTINEL-sign-55e2",
    "GOOGLE_CLIENT_SECRET": "SENTINEL-gsecret-0b4c",
    "GOOGLE_REFRESH_TOKEN": "SENTINEL-refresh-9a8b",
    "XAI_API_KEY": "SENTINEL-xai-3d3d",
    "LLM_API_KEY": "SENTINEL-llm-6e6e",
}


def test_c10_sentinel_secrets_never_appear_in_pages_headers_logs_or_audit(tmp_path):
    env = ENV_TEXT
    for key, value in SENTINELS.items():
        env = re.sub(rf"^{key}=.*$", f"{key}={value}", env, flags=re.MULTILINE)
        if f"{key}=" not in env:
            env += f"{key}={value}\n"
    h = make_harness(tmp_path, env_text=env)
    with capture_logs() as logs:
        sign_in(h)
        seen = []
        for route in GET_ROUTES + ADMIN_GET_ROUTES + ["/signin", "/nope"]:
            response = h.client.get(route)
            seen.append((route, response.text, str(response.headers)))
        h.client.cookies.clear()
        sign_in(h, name=MEMBER)
        for route in GET_ROUTES:
            response = h.client.get(route)
            seen.append((route, response.text, str(response.headers)))
    channels = h.client.get("/settings/channels").text
    assert channels.count("Set</span>") >= len(SENTINELS)
    audit_text = "".join(p.read_text() for p in h.config.audit_dir.glob("*.jsonl"))
    blob = json.dumps(logs, default=str) + audit_text
    for route, body, headers in seen:
        for value in SENTINELS.values():
            assert value not in body, (route, value)
            assert value not in headers, (route, value)
            assert value[:12] not in body.replace("SENTINEL-", "")  # no partial masks
    for value in SENTINELS.values():
        assert value not in blob


# --- C12: audit ------------------------------------------------------------------------------


def test_c12_audit_records_sign_in_success_and_failure_fields(h):
    sign_in(h, password="wrong-password-1")
    sign_in(h)
    rows = h.state.audit.recent()
    assert [r["action"] for r in rows] == ["sign_in", "sign_in_failed"]
    for row in rows:
        assert set(row) >= {"at", "actor", "action", "target", "outcome", "request_id",
                            "remote_addr", "user_agent"}
        assert row["remote_addr"] == "192.168.1.20"
        assert row["user_agent"] == "iPhone · Safari"
        assert row["at"].endswith("+08:00")
    path = h.config.audit_dir / "2026-10.jsonl"
    assert path.is_file()


def test_c12_audit_purge_at_91_days(tmp_path):
    june_end = datetime(2026, 6, 30, 12, tzinfo=HKT)
    log = AuditLog(tmp_path, clock=lambda: june_end)
    log.record(actor="alex", action="sign_in", outcome="success")
    (tmp_path / "notes.txt").write_text("keep")
    assert log.purge(now=june_end + timedelta(days=90)) == 0
    assert log.purge(now=june_end + timedelta(days=91)) == 1
    assert not (tmp_path / "2026-06.jsonl").exists()
    assert (tmp_path / "notes.txt").exists()


def test_c12_audit_refuses_unknown_fields(tmp_path):
    log = AuditLog(tmp_path, clock=lambda: NOW)
    with pytest.raises(ValueError):
        log.record(actor="alex", action="x", outcome="success", token="nope")


# --- C15: launchctl -------------------------------------------------------------------------


def test_c15_launchctl_print_shown_and_never_kickstarted_by_reads(h):
    sign_in(h)
    for route in GET_ROUTES + ADMIN_GET_ROUTES:
        h.client.get(route)
    body = h.client.get("/system/listener").text
    assert "running" in body and "PID 4182" in body and ">75<" in body
    assert h.runner.calls and all(c[:2] == ["launchctl", "print"] for c in h.runner.calls)
    assert h.runner.calls[0] == ["launchctl", "print", "gui/501/com.cec.vivisystem.listener"]


def test_c15_not_macos_shows_not_available(tmp_path):
    h = make_harness(tmp_path, platform="linux")
    sign_in(h)
    assert "Not available on this computer." in h.client.get("/system/listener").text
    assert h.runner.calls == []


# --- C17: boundary logs ---------------------------------------------------------------------


def test_c17_request_logs_have_route_status_duration_and_no_query_or_cookie(h):
    with capture_logs() as logs:
        sign_in(h)
        h.client.get("/calendar?secret=abc123")
    requests = [e for e in logs if e["event"] == "control_request"]
    assert requests and all({"path", "status", "duration_ms", "method"} <= set(e) for e in requests)
    assert any(e["path"] == "/calendar" and e["status"] == 200 for e in requests)
    text = json.dumps(logs, default=str)
    assert "abc123" not in text and "cec_session" not in text
    sid = h.client.cookies.get("cec_session")
    assert sid and sid not in text
    assert any(e["event"] == "control_sign_in" and e["outcome"] == "success" for e in logs)


def test_errors_render_without_500(h):
    sign_in(h)
    assert h.client.get("/nope").status_code == 404
    assert "This page does not exist." in h.client.get("/nope").text
    assert h.client.post("/lang", data={"csrf": page_csrf(h), "lang": "zh-Hans"}).status_code == 400


def test_member_sees_no_admin_controls(h):
    sign_in(h, name=MEMBER)
    overview = h.client.get("/").text
    assert "/system/listener/restart" not in overview
    channels = h.client.get("/settings/channels").text
    assert "/settings/channels/edit" not in channels and "/settings/users" not in channels
    listener = h.client.get("/system/listener").text
    assert "stuck-retries" not in listener


def test_demo_sign_in_route_does_not_exist_outside_demo_mode(h):
    assert h.client.get("/demo/signin/admin").status_code == 404
    assert h.client.get("/").status_code == 303
