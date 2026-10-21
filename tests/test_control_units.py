"""Phase 31 control-app units: config, accounts, .env writer, launchd, OAuth,
CLI, copy catalogs (C13), the static Calendar guard (C14) and import isolation
(C16). These run without the ``control`` extra except where marked.
"""

from __future__ import annotations

import ipaddress
import json
import re
import stat
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from cec_vivisystem.control import envfile, i18n
from cec_vivisystem.control.__main__ import run as cli_run
from cec_vivisystem.control.auth import (
    LoginLimiter,
    SessionStore,
    UserStore,
    UserStoreError,
    hash_passphrase,
    verify_passphrase,
)
from cec_vivisystem.control.config import (
    ControlConfigError,
    client_allowed,
    host_header_name,
    load_control_config,
    parse_allowed_nets,
    parse_bind,
)
from cec_vivisystem.control.demo import DemoRefused, require_loopback
from cec_vivisystem.control.launchd import (
    CommandResult,
    RestartUnavailable,
    kickstart_argv,
    kickstart_listener,
    parse_launchctl_print,
    print_service,
)
from cec_vivisystem.control.oauth_google import (
    OAuthAttempts,
    OAuthError,
    challenge_s256,
    check_redirect,
    consent_url,
    exchange_code,
    parse_redirect,
)
from cec_vivisystem.parser import contains_simplified_markers
from tests.control_support import LISTENER_PRINT, NOW, FakeTokenHttp

REPO = Path(__file__).resolve().parents[1]
CONTROL = REPO / "src" / "cec_vivisystem" / "control"


# --- config -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ("127.0.0.1", 8730)),
        ("192.168.1.20:8730", ("192.168.1.20", 8730)),
        ("10.0.0.5", ("10.0.0.5", 8730)),
        ("[::1]:9000", ("::1", 9000)),
        ("localhost:8731", ("127.0.0.1", 8731)),
    ],
)
def test_bind_accepts_loopback_and_private(raw, expected):
    assert parse_bind(raw) == expected


@pytest.mark.parametrize("raw", ["0.0.0.0:8730", "[::]:8730", "8.8.8.8:8730", "mac-mini.local:8730",
                                 "192.168.1.20:99999", "224.0.0.1:8730"])
def test_bind_refuses_all_interfaces_public_and_names(raw):
    with pytest.raises(ControlConfigError):
        parse_bind(raw)


def test_allowed_nets_default_and_explicit():
    nets = parse_allowed_nets(None, host="192.168.1.20")
    assert ipaddress.ip_network("192.168.1.0/24") in nets
    assert ipaddress.ip_network("127.0.0.0/8") in nets
    assert parse_allowed_nets(None, host="127.0.0.1") == parse_allowed_nets("127.0.0.0/8,::1/128", host="x")
    assert parse_allowed_nets("192.168.50.0/24", host="192.168.1.20") == (
        ipaddress.ip_network("192.168.50.0/24"),)
    for bad in ("0.0.0.0/0", "8.8.8.0/24", "10.0.0.0/8", "nonsense"):
        with pytest.raises(ControlConfigError):
            parse_allowed_nets(bad, host="192.168.1.20")


def test_client_allowed_and_host_header():
    nets = parse_allowed_nets(None, host="192.168.1.20")
    assert client_allowed("192.168.1.77", nets)
    assert client_allowed("::ffff:192.168.1.77", nets)
    assert client_allowed("127.0.0.1", nets)
    assert not client_allowed("192.168.2.1", nets)
    assert not client_allowed("testclient", nets) and not client_allowed(None, nets)
    assert host_header_name("Mac-Mini.local:8730") == "mac-mini.local"
    assert host_header_name("[::1]:8730") == "::1"
    assert host_header_name("192.168.1.20") == "192.168.1.20"


def test_load_control_config_paths_and_tls_pairing(tmp_path):
    config = load_control_config({"CEC_CONTROL_BIND": "192.168.1.20:8730",
                                  "CEC_CONTROL_HOSTS": "Mac-Mini.local"}, root=tmp_path)
    assert config.users_path == tmp_path / "data" / "control" / "users.json"
    assert config.env_path == tmp_path / ".env"
    assert "mac-mini.local" in config.allowed_hosts and "192.168.1.20" in config.allowed_hosts
    assert not config.https
    with pytest.raises(ControlConfigError):
        load_control_config({"CEC_CONTROL_TLS_CERT": "/x.pem"}, root=tmp_path)


# --- accounts -------------------------------------------------------------------------


def test_scrypt_hash_roundtrip_and_format():
    encoded = hash_passphrase("a long passphrase", n=2**10)
    assert encoded.startswith("scrypt$1024$8$1$")
    assert verify_passphrase("a long passphrase", encoded)
    assert not verify_passphrase("a long passphrasE", encoded)
    assert not verify_passphrase("x", "garbage")


def test_user_store_file_modes_validation_and_cli(tmp_path):
    store = UserStore(tmp_path / "control" / "users.json", scrypt_n=2**10)
    store.add("alex", "admin", "twelve chars ok")
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
    raw = json.loads(store.path.read_text())
    assert set(raw["users"]["alex"]) == {"role", "password_hash", "lang", "created_at"}
    assert "twelve chars ok" not in store.path.read_text()
    for name, role, pw in (("alex", "admin", "twelve chars ok"), ("Bad Name", "admin", "twelve chars ok"),
                           ("sam", "owner", "twelve chars ok"), ("sam", "member", "short")):
        with pytest.raises(UserStoreError):
            store.add(name, role, pw)
    assert store.verify("ALEX", "twelve chars ok").name == "alex"
    assert store.verify("alex", "wrong") is None and store.verify("nobody", "x" * 12) is None
    store.set_lang("alex", "zh-Hant-HK")
    assert store.get("alex").lang == "zh-Hant-HK"


def test_cli_adduser_passwd_users_deluser(tmp_path):
    out: list[str] = []
    answers = iter(["passphrase-one-1", "passphrase-one-1", "a", "b", "passphrase-two-2", "passphrase-two-2"])
    env = {"CEC_CONTROL_BIND": "127.0.0.1:8730"}

    def run(*args):
        return cli_run(list(args), env=env, prompt=lambda _p: next(answers), out=out.append)

    import cec_vivisystem.control.config as config_module

    original = config_module.repo_root
    config_module.repo_root = lambda: tmp_path
    try:
        assert run("adduser", "carter", "--role", "admin") == 0
        assert run("passwd", "carter") == 1  # the two entries differ
        assert run("passwd", "carter") == 0
        assert run("users") == 0
        assert run("deluser", "carter") == 0
        assert run("deluser", "carter") == 1
    finally:
        config_module.repo_root = original
    assert "carter\tadmin\ten" in out
    assert not any("passphrase-" in line for line in out)
    assert cli_run(["serve"], env={"CEC_CONTROL_BIND": "0.0.0.0:8730"}, out=out.append) == 2


def test_login_limiter_locks_account_and_address():
    limiter = LoginLimiter()
    for i in range(4):
        assert limiter.record_failure("alex", "192.168.1.5", NOW + timedelta(seconds=i)) is None
    until = limiter.record_failure("alex", "192.168.1.5", NOW + timedelta(seconds=5))
    assert until == NOW + timedelta(minutes=15, seconds=5)
    assert limiter.locked_until("sam", "192.168.1.5", NOW) == until  # the address is locked
    assert limiter.locked_until("alex", "192.168.1.9", NOW) == until  # the account is locked
    assert limiter.locked_until("alex", "192.168.1.5", until + timedelta(seconds=1)) is None


def test_sessions_expire_idle_and_absolute():
    store = SessionStore()
    session = store.create("alex", NOW)
    assert len(session.sid) >= 43 and session.sid != session.csrf
    for day in (6, 12, 18, 24, 30):  # used every 6 days: idle never expires
        assert store.get(session.sid, NOW + timedelta(days=day)) is session
    assert store.get(session.sid, NOW + timedelta(days=30, seconds=1)) is None  # absolute 30 days
    other = store.create("sam", NOW)
    assert store.get(other.sid, NOW + timedelta(days=7, seconds=1)) is None
    assert not other.password_fresh(NOW + timedelta(minutes=16))


# --- .env writer -------------------------------------------------------------------------


def test_apply_updates_is_line_preserving():
    original = (b"# comment\r\nexport SLACK_FAMILY_PLANS_CHANNEL_ID=C0OLD0000\r\n"
                b"A=1 # inline\nCEC_HEALTH_ALERT_CHANNEL_ID = G0OLD0000\n# CEC_LISTENER_STUCK_RETRIES=3\nZ=9")
    new = envfile.apply_updates(original, {
        "SLACK_FAMILY_PLANS_CHANNEL_ID": "C0NEW0000",
        "CEC_HEALTH_ALERT_CHANNEL_ID": "G0NEW0000",
        "CEC_LISTENER_STUCK_RETRIES": "4",
    })
    assert new == (b"# comment\r\nexport SLACK_FAMILY_PLANS_CHANNEL_ID=C0NEW0000\r\n"
                   b"A=1 # inline\nCEC_HEALTH_ALERT_CHANNEL_ID=G0NEW0000\n"
                   b"# CEC_LISTENER_STUCK_RETRIES=3\nZ=9\nCEC_LISTENER_STUCK_RETRIES=4\n")


def test_update_env_file_rejects_keys_and_values(tmp_path):
    path = tmp_path / ".env"
    path.write_text("A=1\n")
    for updates in ({"SLACK_BOT_TOKEN": "x"}, {"LOG_LEVEL": "DEBUG"},
                    {"CEC_HEALTH_ALERT_CHANNEL_ID": "G0X\nSLACK_BOT_TOKEN=y"},
                    {"GOOGLE_REFRESH_TOKEN": "a b"}, {"GOOGLE_REFRESH_TOKEN": "a#b"}, {}):
        with pytest.raises(envfile.EnvWriteError):
            envfile.update_env_file(path, updates, backup_dir=tmp_path / "b", now=NOW)
    assert path.read_text() == "A=1\n"


def test_backups_pruned_by_count_and_age(tmp_path):
    path = tmp_path / ".env"
    path.write_text("A=1\n")
    path.chmod(0o640)
    backups = tmp_path / "b"
    for i in range(5):
        envfile.update_env_file(path, {"CEC_LISTENER_STUCK_RETRIES": str(i)}, backup_dir=backups,
                                now=NOW + timedelta(seconds=i))
    assert len(envfile.list_backups(backups)) == 3
    assert stat.S_IMODE(path.stat().st_mode) == 0o640  # mode kept
    assert envfile.prune_backups(backups, now=NOW + timedelta(days=31)) == 3


def test_secret_presence_flags_only():
    flags = envfile.secret_presence({"SLACK_BOT_TOKEN": "xoxb-1", "XAI_API_KEY": "  "})
    assert flags["SLACK_BOT_TOKEN"] is True and flags["XAI_API_KEY"] is False
    assert set(flags.values()) <= {True, False}


# --- launchd -------------------------------------------------------------------------------


def test_parse_launchctl_print_top_level_fields():
    info = parse_launchctl_print(LISTENER_PRINT)
    assert (info.state, info.pid, info.last_exit, info.runs) == ("running", 4182, "75", 2)
    assert parse_launchctl_print("garbage").state is None


def test_print_service_guards_and_kickstart_argv():
    calls = []

    def runner(argv, *, timeout):
        calls.append(list(argv))
        return CommandResult(113, "", "Could not find service")

    assert print_service(runner, uid=501, platform="linux").available is False
    assert calls == []
    missing = print_service(runner, uid=501, platform="darwin")
    assert missing.loaded is False and missing.error == "exit 113"
    assert kickstart_argv(501) == ["launchctl", "kickstart", "-k", "gui/501/com.cec.vivisystem.listener"]
    with pytest.raises(RestartUnavailable):
        kickstart_listener(runner, uid=501, platform="linux")
    assert all("kickstart" not in c for c in calls)


# --- OAuth ---------------------------------------------------------------------------------


def test_pkce_s256_matches_rfc7636_example():
    assert challenge_s256("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == (
        "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")


def test_attempts_single_session_and_ttl():
    attempts = OAuthAttempts()
    first = attempts.start("s1", user="alex", now=NOW)
    second = attempts.start("s1", user="alex", now=NOW)
    assert attempts.get("s1") is second and first.state != second.state
    assert 43 <= len(second.verifier) <= 128
    assert not second.expired(NOW + timedelta(minutes=10))
    assert second.expired(NOW + timedelta(minutes=10, seconds=1))
    url = consent_url("cid", second)
    assert "code_challenge_method=S256" in url and second.verifier not in url


def test_parse_and_check_redirect():
    attempts = OAuthAttempts()
    attempt = attempts.start("s", user="alex", now=NOW)
    params = parse_redirect(f"http://127.0.0.1:53682/?state={attempt.state}&code=4/abc&scope=x "
                            "https://www.googleapis.com/auth/calendar.events")
    assert check_redirect(params, attempt, now=NOW) == "4/abc"
    assert parse_redirect(f"?state={attempt.state}&code=z")["code"] == "z"
    with pytest.raises(OAuthError) as exc:
        parse_redirect("")
    assert exc.value.kind == "bad_address"
    with pytest.raises(OAuthError) as exc:
        check_redirect(params, None, now=NOW)
    assert exc.value.kind == "no_attempt"


def test_exchange_code_maps_failures():
    attempt = OAuthAttempts().start("s", user="alex", now=NOW)
    http = FakeTokenHttp()
    grant = exchange_code(http, client_id="c", client_secret="s", code="k", attempt=attempt)
    assert grant.refresh_token.startswith("1//") and "synthetic" not in repr(grant)

    class Down:
        def post_form(self, url, data, *, timeout):
            raise TimeoutError()

    with pytest.raises(OAuthError) as exc:
        exchange_code(Down(), client_id="c", client_secret="s", code="k", attempt=attempt)
    assert exc.value.kind == "network"
    http.payload["refresh_token"] = "bad token with spaces"
    with pytest.raises(OAuthError) as exc:
        exchange_code(http, client_id="c", client_secret="s", code="k", attempt=attempt)
    assert exc.value.kind == "no_refresh"


def test_demo_refuses_non_loopback():
    assert require_loopback("127.0.0.1") == "127.0.0.1"
    assert require_loopback("localhost") == "127.0.0.1"
    for host in ("0.0.0.0", "192.168.1.20", "example.com"):
        with pytest.raises(DemoRefused):
            require_loopback(host)


# --- C13: copy catalogs ------------------------------------------------------------------

# Common Simplified forms beyond the parser's marker set; Traditional only.
_EXTRA_SIMPLIFIED = set("设机开连线录务应数据页网络验证输码户认权统态错误发时间刚个对实过还复绝择这检签删动")
_STE_BANNED = re.compile(r"\b(please|ensure|verify|via)\b", re.IGNORECASE)


def test_c13_catalog_parity_non_empty_traditional_and_short_sentences():
    en, tc = i18n.catalog("en"), i18n.catalog("zh-Hant-HK")
    assert set(en) == set(tc)
    for key in en:
        assert en[key].strip() and tc[key].strip(), key
        assert set(re.findall(r"{(\w+)}", en[key])) == set(re.findall(r"{(\w+)}", tc[key])), key
        assert not contains_simplified_markers(tc[key]), key
        assert not (_EXTRA_SIMPLIFIED & set(tc[key])), (key, _EXTRA_SIMPLIFIED & set(tc[key]))
        assert not _STE_BANNED.search(en[key]), key
        for sentence in re.split(r"(?<=[.!?])\s+", en[key]):
            assert len(sentence.split()) <= 20, (key, sentence)
    assert "zh-Hans" not in i18n.LANGS


def test_c13_templates_take_text_from_the_catalog():
    cjk = re.compile(r"[㐀-鿿]")
    for path in (CONTROL / "templates").glob("*.html"):
        leftovers = set(cjk.findall(path.read_text(encoding="utf-8"))) - set("繁體中文")
        assert not leftovers, (path.name, leftovers)


def test_notices_are_bilingual_with_per_language_values():
    text = i18n.both("notice.channel", user="alex", time="21:04",
                     name=i18n.per_lang("channels.CEC_HEALTH_ALERT_CHANNEL_ID"))
    english, chinese = text.split("\n\n")
    assert "System status channel" in english and "系統狀態頻道" in chinese


# --- C14: static guard -------------------------------------------------------------------


def test_c14_control_package_never_references_calendar_writes_or_confirmations():
    banned = ("write_calendar_create", "write_calendar_series_create", "write_calendar_delete",
              "create_event", "delete_event", "resolve_confirmation", "cec_vivisystem.confirmation")
    for path in CONTROL.rglob("*"):
        if path.suffix not in {".py", ".html", ".json"}:
            continue
        text = path.read_text(encoding="utf-8")
        for word in banned:
            assert word not in text, (path.name, word)
        if path.name != "launchd.py":
            for word in ('"kickstart"', "import subprocess", "subprocess.", "os.system", "shell=True"):
                assert word not in text, (path.name, word)


# --- C16: import isolation ---------------------------------------------------------------


def test_c16_listener_and_jobs_never_import_web_packages():
    code = (
        "import sys\n"
        "import cec_vivisystem.listener, cec_vivisystem.health, cec_vivisystem.morning_recap\n"
        "import cec_vivisystem.important_dates, cec_vivisystem.google_token_reminder\n"
        "import cec_vivisystem.control, cec_vivisystem.control.auth, cec_vivisystem.control.envfile\n"
        "bad = sorted({m.split('.')[0] for m in sys.modules} & {'starlette', 'uvicorn', 'jinja2', 'multipart'})\n"
        "print(','.join(bad))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                            cwd=REPO)
    assert result.stdout.strip() == ""


# --- WCAG 2.2 AA contrast of the shipped CSS tokens (design note §8.2) ---------------


def _tokens(css: str, block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})", block))


def _luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _ratio(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


TEXT_PAIRS = [
    ("ink", "paper"), ("ink-2", "paper"), ("ink-3", "paper"), ("ink", "surface"), ("ink-2", "surface"),
    ("ink-3", "surface"), ("accent", "paper"), ("accent", "surface"), ("on-accent", "accent"),
    ("on-error", "error"), ("ok", "ok-bg"), ("warn", "warn-bg"), ("error", "error-bg"),
    ("info", "info-bg"), ("ink", "warn-bg"), ("ink", "error-bg"), ("ink-2", "warn-bg"),
    ("ink-2", "error-bg"), ("ink-2", "ok-bg"), ("ink-2", "info-bg"), ("error", "surface"),
]
UI_PAIRS = [("rule-strong", "paper"), ("rule-strong", "surface"), ("focus", "paper"), ("focus", "surface")]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_shipped_css_tokens_meet_wcag_aa(theme):
    css = (CONTROL / "static" / "control.css").read_text(encoding="utf-8")
    root = css.split(":root {", 1)[1].split("}", 1)[0]
    dark = css.split("@media (prefers-color-scheme: dark)", 1)[1].split("}\n}", 1)[0]
    tokens = _tokens(css, root)
    if theme == "dark":
        tokens.update(_tokens(css, dark))
    for fg, bg in TEXT_PAIRS:
        assert _ratio(tokens[fg], tokens[bg]) >= 4.5, (theme, fg, bg, round(_ratio(tokens[fg], tokens[bg]), 2))
    for fg, bg in UI_PAIRS:
        assert _ratio(tokens[fg], tokens[bg]) >= 3.0, (theme, fg, bg)
