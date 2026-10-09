"""Phase 29 LaunchAgent templates rendered by the installer's --dry-run.

Runs only the dry-run path with a fake uv and HOME: no launchctl, no copies.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "install_launchagents.sh"
FAKE_UV = "/opt/fake/bin/uv"
PREFIX = [FAKE_UV, "run", "--frozen", "python"]

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")


@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> tuple[dict[str, dict], str, Path]:
    root = tmp_path_factory.mktemp("launchd")
    home = root / "home"
    out_dir = root / "rendered"
    env = {**os.environ, "HOME": str(home)}
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run", "--uv", FAKE_UV, "--render-dir", str(out_dir)],
        capture_output=True, text=True, env=env, check=True,
    )
    plists = {}
    for path in sorted(out_dir.glob("*.plist")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"__[A-Z_]+__", text), f"placeholder left in {path}"
        plists[path.stem] = plistlib.loads(path.read_bytes())
    return plists, proc.stdout, home


def test_dry_run_renders_four_agents_without_side_effects(rendered) -> None:
    plists, stdout, home = rendered
    assert set(plists) == {
        "com.cec.vivisystem.listener",
        "com.cec.vivisystem.morning-recap",
        "com.cec.vivisystem.important-dates",
        "com.cec.vivisystem.health-check",
    }
    assert not home.exists(), "dry run must not create LaunchAgents or log dirs"
    assert "dry run: no files copied and launchctl not invoked" in stdout
    lines = stdout.splitlines()
    kicks = [line for line in lines if "kickstart" in line]
    assert len(kicks) == 1, "only the listener is kickstarted"
    assert kicks[0].endswith("/com.cec.vivisystem.listener")
    bootstraps = [line for line in lines if "bootstrap" in line]
    assert bootstraps[0].endswith("com.cec.vivisystem.listener.plist")
    commands = [line for line in lines if "launchctl " in line and "dry run:" not in line]
    assert commands and all(line.startswith("would run: ") for line in commands)


def test_common_keys(rendered) -> None:
    plists, _, home = rendered
    for label, plist in plists.items():
        name = label.removeprefix("com.cec.vivisystem.")
        assert plist["Label"] == label
        assert plist["WorkingDirectory"] == str(REPO)
        assert plist["ProgramArguments"][:4] == PREFIX
        assert plist["StandardOutPath"] == "/dev/null"
        assert plist["StandardErrorPath"] == (
            f"{home}/Library/Logs/cec-vivisystem/{name}.err.log"
        )
        assert plist["EnvironmentVariables"]["TZ"] == "Asia/Hong_Kong"
        assert plist["EnvironmentVariables"]["PATH"].startswith("/opt/fake/bin:")
        assert isinstance(plist["RunAtLoad"], bool)


def test_listener_restarts_unconditionally(rendered) -> None:
    listener = rendered[0]["com.cec.vivisystem.listener"]
    assert listener["RunAtLoad"] is True
    assert listener["KeepAlive"] is True
    assert listener["ProgramArguments"][4:] == [
        "-c", "from cec_vivisystem.listener import main; main()",
    ]


@pytest.mark.parametrize(
    ("label", "hour", "module"),
    [
        ("com.cec.vivisystem.morning-recap", 7, "morning_recap"),
        ("com.cec.vivisystem.important-dates", 10, "important_dates"),
    ],
)
def test_scheduled_jobs_do_not_run_at_load(rendered, label, hour, module) -> None:
    plist = rendered[0][label]
    assert plist["StartCalendarInterval"] == {"Hour": hour, "Minute": 0}
    assert plist["RunAtLoad"] is False
    assert "KeepAlive" not in plist
    assert plist["ProgramArguments"][4:] == [
        "-c", f"from cec_vivisystem.{module} import main; main()",
    ]


def test_health_check_is_periodic_alert_only(rendered) -> None:
    plist = rendered[0]["com.cec.vivisystem.health-check"]
    assert plist["StartInterval"] == 600
    assert plist["RunAtLoad"] is False
    assert "KeepAlive" not in plist
    assert plist["ProgramArguments"][4:] == ["-m", "cec_vivisystem.health", "--alert"]


def test_relative_uv_is_rejected(tmp_path: Path) -> None:
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run", "--uv", "uv"],
        capture_output=True, text=True, env={**os.environ, "HOME": str(tmp_path)},
        check=False,
    )
    assert proc.returncode == 1
    assert "absolute uv path" in proc.stderr
