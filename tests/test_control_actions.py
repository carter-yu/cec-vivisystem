"""Phase 31b: guarded control-app actions (locked cases A1–A12).

Channel edits, Google re-authorization, Test connection, stuck-retry edit and
listener restart. Fakes only: no Slack, Google, launchd or network.
"""

from __future__ import annotations

import json
import re
import stat
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("starlette", reason="control extra not installed: uv sync --extra control")

from structlog.testing import capture_logs

from cec_vivisystem.control import envfile
from cec_vivisystem.control.oauth_google import challenge_s256
from tests.control_support import (
    ENV_TEXT,
    MEMBER,
    PASSPHRASE,
    csrf_of,
    make_harness,
    page_csrf,
    sign_in,
)

KEY = "CEC_HEALTH_ALERT_CHANNEL_ID"


@pytest.fixture
def h(tmp_path):
    harness = make_harness(tmp_path)
    sign_in(harness)
    return harness


def _save_channel(h, value, key=KEY, **extra):
    return h.client.post("/settings/channels/save",
                         data={"csrf": page_csrf(h), "key": key, "value": value, **extra})


def _backups(h):
    return envfile.list_backups(h.config.backup_dir)


# --- A1 / A2 / A3: channel edits ---------------------------------------------------------------


def test_a1_valid_channel_edit_changes_only_that_key(h):
    before = h.env_path.read_bytes()
    preview = h.client.post("/settings/channels/preview",
                            data={"csrf": page_csrf(h), "key": KEY, "value": " g0newstatus1 "})
    assert preview.status_code == 200
    assert "The System status channel changes from G0TESTSTATUS to G0NEWSTATUS1." in preview.text
    assert h.env_path.read_bytes() == before  # preview never writes
    saved = _save_channel(h, "G0NEWSTATUS1")
    assert saved.status_code == 303 and saved.headers["location"] == "/settings/channels"
    after = h.env_path.read_bytes()
    old_lines, new_lines = before.splitlines(keepends=True), after.splitlines(keepends=True)
    assert len(old_lines) == len(new_lines)
    changed = [(a, b) for a, b in zip(old_lines, new_lines, strict=True) if a != b]
    assert changed == [(b"CEC_HEALTH_ALERT_CHANNEL_ID=G0TESTSTATUS\n", b"CEC_HEALTH_ALERT_CHANNEL_ID=G0NEWSTATUS1\n")]
    assert stat.S_IMODE(h.env_path.stat().st_mode) == 0o600
    [backup] = _backups(h)
    assert backup.read_bytes() == before
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    row = h.state.audit.recent()[0]
    assert (row["action"], row["target"], row["old"], row["new"], row["outcome"]) == (
        "channel_change", KEY, "G0TESTSTATUS", "G0NEWSTATUS1", "success")
    [(channel, text)] = h.poster.posts
    assert channel == "G0NEWSTATUS1"  # announced in the (new) system-status channel
    assert "changed the System status channel" in text and "G0" not in text
    assert "系統狀態頻道" in text
    page = h.client.get("/settings/channels").text
    assert "Saved. Restart the listener to use the new channel." in page
    assert 'href="/system/listener/restart"' in page


def test_a1_backups_keep_last_three(h):
    for value in ("G0AAAAAAAA", "G0BBBBBBBB", "G0CCCCCCCC", "G0DDDDDDDD"):
        h.clock.advance(seconds=2)
        assert _save_channel(h, value).status_code == 303
    backups = _backups(h)
    assert len(backups) == 3
    assert b"G0AAAAAAAA" in backups[0].read_bytes()


def test_a2_invalid_format_and_secret_keys_are_rejected(h):
    before = h.env_path.read_bytes()
    bad = h.client.post("/settings/channels/preview",
                        data={"csrf": page_csrf(h), "key": KEY, "value": "#vivi-status"})
    assert bad.status_code == 400
    assert "This text is not a Slack channel ID." in bad.text
    assert _save_channel(h, "x1").status_code == 400
    for key in ("SLACK_BOT_TOKEN", "GOOGLE_REFRESH_TOKEN", "LOG_LEVEL"):
        assert _save_channel(h, "C0123456789", key=key).status_code == 400
        assert h.client.get(f"/settings/channels/edit?key={key}").status_code == 400
    assert h.env_path.read_bytes() == before
    assert _backups(h) == []


def test_a3_env_write_failure_keeps_original_and_backups(h, monkeypatch):
    _save_channel(h, "G0FIRSTSAVE")
    existing = _backups(h)
    before = h.env_path.read_bytes()

    def boom(path, data, mode):
        raise OSError("disk full")

    monkeypatch.setattr(envfile, "_atomic_write_bytes", boom)
    with capture_logs() as logs:
        response = _save_channel(h, "G0SECONDSAVE")
    assert response.status_code == 500
    assert "The change was not saved. The .env file did not change." in response.text
    assert h.env_path.read_bytes() == before
    assert _backups(h) == existing
    assert any(e["event"] == "control_env_write_failed" for e in logs)


def test_a4_sensitive_action_after_16_minutes_needs_the_password(h):
    h.clock.advance(minutes=16)
    before = h.env_path.read_bytes()
    prompt = _save_channel(h, "G0NEWSTATUS1")
    assert prompt.status_code == 200 and 'name="password"' in prompt.text
    assert "You entered it more than 15 minutes ago." in prompt.text
    wrong = _save_channel(h, "G0NEWSTATUS1", password="not-the-password")
    assert "The password is incorrect." in wrong.text
    assert h.env_path.read_bytes() == before
    ok = _save_channel(h, "G0NEWSTATUS1", password=PASSPHRASE)
    assert ok.status_code == 303
    assert b"G0NEWSTATUS1" in h.env_path.read_bytes()
    # Fresh again: the next sensitive action does not ask.
    assert _save_channel(h, "G0NEWSTATUS2").status_code == 303


# --- A5 / A6 / A7: Google re-authorization -----------------------------------------------------


def _start(h):
    response = h.client.post("/settings/google/start", data={"csrf": page_csrf(h)})
    assert response.status_code == 303
    page = h.client.get("/settings/google").text
    url = re.search(r'href="(https://accounts\.google\.com/[^"]+)"', page).group(1)
    return page, parse_qs(urlsplit(url.replace("&amp;", "&")).query)


def _finish(h, address):
    return h.client.post("/settings/google/finish", data={"csrf": page_csrf(h, "/settings/google"),
                                                          "address": address})


def test_a5_oauth_start_builds_a_pkce_consent_url(h):
    page, params = _start(h)
    assert params["client_id"] == ["test-client.apps.example"]
    assert params["redirect_uri"] == ["http://127.0.0.1:53682/"]
    assert params["scope"] == ["https://www.googleapis.com/auth/calendar.events"]
    assert params["access_type"] == ["offline"] and params["prompt"] == ["consent"]
    assert params["response_type"] == ["code"]
    assert params["code_challenge_method"] == ["S256"]
    attempt = h.state.attempts.get(h.client.cookies.get("cec_session"))
    assert params["state"] == [attempt.state]
    assert params["code_challenge"] == [challenge_s256(attempt.verifier)]
    assert attempt.verifier not in page
    assert "This sign-in stays open until 21:14." in page
    assert 'target="_blank" rel="noopener noreferrer"' in page


def test_a6_oauth_paste_success_writes_tokens_and_hides_them(h):
    with capture_logs() as logs:
        _, params = _start(h)
        state = params["state"][0]
        response = _finish(h, f"http://127.0.0.1:53682/?state={state}&code=4/synthetic-code"
                              "&scope=https://www.googleapis.com/auth/calendar.events")
    assert response.status_code == 200
    assert "Google access is saved. It expires on Tue 27 Oct." in response.text
    assert "The test with the new access is OK." in response.text
    assert "Restart the listener to use the new access in Slack." in response.text
    values = envfile.read_env(h.env_path)
    assert values["GOOGLE_REFRESH_TOKEN"] == "1//synthetic-new-refresh-token"
    assert values["GOOGLE_REFRESH_TOKEN_ISSUED_AT"] == "2026-10-20T21:04:12+08:00"
    [(url, sent)] = h.http.calls
    assert url == "https://oauth2.googleapis.com/token"
    assert sent["code"] == "4/synthetic-code" and sent["grant_type"] == "authorization_code"
    assert sent["code_verifier"] and sent["client_secret"] == "test-client-secret"
    assert h.calendar.warmups == 1
    assert h.calendar.configs[-1].refresh_token == "1//synthetic-new-refresh-token"
    secret_bits = ("synthetic-new-refresh-token", "synthetic-code", "ya29", sent["code_verifier"])
    audit = "".join(p.read_text() for p in h.config.audit_dir.glob("*.jsonl"))
    for bit in secret_bits:
        assert bit not in response.text
        assert bit not in json.dumps(logs, default=str)
        assert bit not in audit
        assert all(bit not in text for _, text in h.poster.posts)
    assert h.state.audit.recent()[0]["action"] == "google_reauth"
    assert any("re-authorized Google access" in text for _, text in h.poster.posts)
    assert h.state.attempts.get(h.client.cookies.get("cec_session")) is None


def test_a6_member_success_asks_for_an_admin_restart(tmp_path):
    h = make_harness(tmp_path)
    sign_in(h, name=MEMBER)
    _, params = _start(h)
    response = _finish(h, f"http://127.0.0.1:53682/?state={params['state'][0]}&code=c1")
    assert "Ask the administrator to restart the listener." in response.text
    assert "/system/listener/restart" not in response.text.split("<main")[1]


@pytest.mark.parametrize(
    ("case", "copy"),
    [
        ("wrong_state", "This address is from a different sign-in."),
        ("expired", "This sign-in is older than 10 minutes."),
        ("denied", "Google did not give access."),
        ("no_code", "This address has no Google code."),
        ("garbage", "This text is not the address from Safari."),
        ("exchange_400", "Google did not accept this address."),
        ("no_refresh", "Google did not give long-term access."),
        ("scope", "Calendar access was not selected."),
    ],
)
def test_a7_oauth_errors_have_specific_copy_and_leave_env_unchanged(h, case, copy):
    before = h.env_path.read_bytes()
    _, params = _start(h)
    state = params["state"][0]
    address = f"http://127.0.0.1:53682/?state={state}&code=c1"
    if case == "wrong_state":
        address = "http://127.0.0.1:53682/?state=other&code=c1"
    elif case == "expired":
        h.clock.advance(minutes=10, seconds=1)
    elif case == "denied":
        address = f"http://127.0.0.1:53682/?error=access_denied&state={state}"
    elif case == "no_code":
        address = f"http://127.0.0.1:53682/?state={state}"
    elif case == "garbage":
        address = "hello"
    elif case == "exchange_400":
        h.http.status, h.http.payload = 400, {"error": "invalid_grant"}
    elif case == "no_refresh":
        h.http.payload.pop("refresh_token")
    elif case == "scope":
        address += "&scope=openid"
    with capture_logs() as logs:
        response = _finish(h, address)
    assert response.status_code == 400
    assert copy in response.text
    assert h.env_path.read_bytes() == before
    assert any(e["event"] == "control_oauth_failed" for e in logs)
    assert h.state.audit.recent()[0]["outcome"] == "failure"


def test_a7_finish_without_start_or_after_success_is_refused(h):
    response = _finish(h, "http://127.0.0.1:53682/?state=x&code=y")
    assert "No sign-in is open." in response.text
    assert h.http.calls == []


# --- Test connection ---------------------------------------------------------------------------------


def test_test_connection_records_result_without_token_values(h):
    response = h.client.post("/settings/google/test", data={"csrf": page_csrf(h), "next": "/"})
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert "Google access works." in h.client.get("/").text
    h.calendar.warmup_ok = False
    h.client.post("/settings/google/test", data={"csrf": page_csrf(h)})
    page = h.client.get("/settings/google").text
    assert "Google access does not work. Re-authorize Google access." in page
    assert "Failed · 21:04" in page
    outcomes = [r["outcome"] for r in h.state.audit.recent() if r["action"] == "google_test"]
    assert outcomes == ["failure", "success"]


# --- Stuck-retry setting -------------------------------------------------------------------------------


def test_stuck_retries_edit_validates_and_prompts_restart(h):
    csrf = page_csrf(h, "/system/listener")
    bad = h.client.post("/system/listener/stuck-retries", data={"csrf": csrf, "value": "11"})
    assert bad.status_code == 400 and "Enter a number from 0 to 10." in bad.text
    ok = h.client.post("/system/listener/stuck-retries", data={"csrf": csrf, "value": "5"})
    assert ok.status_code == 303
    assert envfile.read_env(h.env_path)["CEC_LISTENER_STUCK_RETRIES"] == "5"
    assert h.env_path.read_text().endswith("CEC_LISTENER_STUCK_RETRIES=5\n")
    page = h.client.get("/system/listener").text
    assert "Saved. Restart the listener to use the new value." in page
    row = h.state.audit.recent()[0]
    assert (row["action"], row.get("old"), row["new"]) == ("stuck_retries", None, "5")


# --- A8 / A9 / A10: restart ---------------------------------------------------------------------------


def test_a8_restart_confirmed_runs_fixed_argv_once(h):
    h.runner.on_kick = lambda: h.heartbeat(pid=5521, instance_id="eeee5555ffff6666",
                                           started_at=h.clock.now.isoformat())
    confirm = h.client.get("/system/listener/restart")
    assert "Replies stop for approximately 30 seconds." in confirm.text
    response = h.client.post("/system/listener/restart", data={"csrf": csrf_of(confirm.text)})
    assert response.status_code == 200
    assert h.runner.kicks == [["launchctl", "kickstart", "-k", "gui/501/com.cec.vivisystem.listener"]]
    assert "The listener started again at 21:04. New process: 5521." in response.text
    assert h.state.audit.recent()[0]["action"] == "listener_restart"
    assert any("restarted the listener" in text for _, text in h.poster.posts)


def test_a8_restart_waits_for_the_new_process(h):
    response = h.client.post("/system/listener/restart", data={"csrf": page_csrf(h)})
    assert "The app waits for the new process." in response.text
    assert '<meta http-equiv="refresh" content="5;url=/system/listener/restart/status">' in response.text
    h.heartbeat(pid=6001, instance_id="ffff0000", started_at=h.clock.now.isoformat())
    assert "New process: 6001." in h.client.get("/system/listener/restart/status").text
    h.state.last_restart.old_instance = "ffff0000"
    h.clock.advance(minutes=3)
    assert "No new listener process after 2 minutes." in h.client.get("/system/listener/restart/status").text


def test_a9_rate_limit_failure_and_non_macos(h, tmp_path):
    assert h.client.post("/system/listener/restart", data={"csrf": page_csrf(h)}).status_code == 200
    h.clock.advance(minutes=4)
    again = h.client.post("/system/listener/restart", data={"csrf": page_csrf(h)})
    assert again.status_code == 429
    assert "You can restart the listener again after 21:09." in again.text
    assert len(h.runner.kicks) == 1
    h.clock.advance(minutes=1, seconds=1)
    h.runner.kick_code = 113
    failed = h.client.post("/system/listener/restart", data={"csrf": page_csrf(h)})
    assert "launchctl gave error 113." in failed.text
    assert h.state.audit.recent()[0]["outcome"] == "failure"

    linux = make_harness(tmp_path / "linux", platform="linux")
    sign_in(linux)
    refused = linux.client.post("/system/listener/restart", data={"csrf": page_csrf(linux)})
    assert "Restart is not available on this computer." in refused.text
    assert linux.runner.calls == []


def test_a10_stale_health_never_triggers_a_restart(tmp_path):
    h = make_harness(tmp_path)
    h.heartbeat(written_at=h.clock.now.replace(hour=19).isoformat())
    sign_in(h)
    for route in ("/", "/system/listener", "/system/jobs", "/system/activity", "/settings/google"):
        assert h.client.get(route).status_code == 200
        h.clock.advance(minutes=10)
    assert h.runner.kicks == []
    inner = h.app
    assert type(inner.router.lifespan_context).__name__ == "_DefaultLifespan"


# --- A11: roles ---------------------------------------------------------------------------------------------


def test_a11_member_gets_403_for_admin_actions_but_can_reauthorize(tmp_path):
    h = make_harness(tmp_path)
    sign_in(h, name=MEMBER)
    csrf = page_csrf(h)
    forbidden = [
        h.client.get("/settings/channels/edit?key=" + KEY),
        h.client.post("/settings/channels/preview", data={"csrf": csrf, "key": KEY, "value": "G0NEWSTATUS1"}),
        h.client.post("/settings/channels/save", data={"csrf": csrf, "key": KEY, "value": "G0NEWSTATUS1"}),
        h.client.get("/settings/channels/test?key=" + KEY),
        h.client.post("/settings/channels/test", data={"csrf": csrf, "key": KEY}),
        h.client.get("/system/listener/restart"),
        h.client.post("/system/listener/restart", data={"csrf": csrf}),
        h.client.post("/system/listener/stuck-retries", data={"csrf": csrf, "value": "2"}),
        h.client.get("/settings/users"),
    ]
    assert [r.status_code for r in forbidden] == [403] * len(forbidden)
    assert "You do not have permission for this action." in forbidden[0].text
    assert h.runner.kicks == [] and h.poster.posts == []
    assert h.env_path.read_text() == ENV_TEXT
    assert h.client.post("/settings/google/test", data={"csrf": csrf}).status_code == 303
    assert h.client.post("/settings/google/start", data={"csrf": csrf}).status_code == 303


# --- A12: test message -------------------------------------------------------------------------------------


def test_a12_test_message_goes_to_the_selected_channel(h):
    confirm = h.client.get("/settings/channels/test?key=SLACK_FAMILY_PLANS_CHANNEL_ID")
    assert "All people in the channel can see it." in confirm.text
    sent = h.client.post("/settings/channels/test",
                         data={"csrf": csrf_of(confirm.text), "key": "SLACK_FAMILY_PLANS_CHANNEL_ID"})
    assert sent.status_code == 303
    [(channel, text)] = h.poster.posts
    assert channel == "C0TESTPLANS"
    assert text.startswith("Test message from the control app. No action is necessary.")
    assert "這是控制台的測試訊息" in text
    assert h.poster.tokens == ["xoxb-test-not-real"]
    assert "The test message is sent." in h.client.get("/settings/channels").text
    h.poster.fail = True
    h.client.post("/settings/channels/test", data={"csrf": page_csrf(h), "key": "SLACK_FAMILY_PLANS_CHANNEL_ID"})
    assert "The test message was not sent." in h.client.get("/settings/channels").text
