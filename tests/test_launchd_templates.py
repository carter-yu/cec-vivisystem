"""LaunchAgent templates rendered by the installer (Phases 29 and 31).

Runs the dry-run path with a fake uv and HOME, and the install path only with
fake ``launchctl``, ``uname`` and ``sleep`` scripts on PATH: never real launchd.
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


def test_dry_run_renders_five_agents_without_side_effects(rendered) -> None:
    plists, stdout, home = rendered
    assert set(plists) == {
        "com.cec.vivisystem.listener",
        "com.cec.vivisystem.morning-recap",
        "com.cec.vivisystem.important-dates",
        "com.cec.vivisystem.health-check",
        "com.cec.vivisystem.control",
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
        prefix = PREFIX[:3] + ["--extra", "control", "python"] if name == "control" else PREFIX
        assert plist["ProgramArguments"][: len(prefix)] == prefix
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
    assert plist["StartInterval"] == 300  # Phase 30 alert policy v2 cadence
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


def test_control_app_agent_runs_the_server_with_the_extra(rendered) -> None:
    plist = rendered[0]["com.cec.vivisystem.control"]
    assert plist["ProgramArguments"] == [
        FAKE_UV, "run", "--frozen", "--extra", "control",
        "python", "-m", "cec_vivisystem.control", "serve",
    ]
    assert plist["RunAtLoad"] is True and plist["KeepAlive"] is True
    assert plist["ThrottleInterval"] == 30
    kicks = [line for line in rendered[1].splitlines() if "kickstart" in line]
    assert not any("com.cec.vivisystem.control" in line for line in kicks)


FAKE_LAUNCHCTL = r"""#!/bin/bash
# Fake launchctl: logs calls; "print" says loaded for N polls after bootout;
# "bootstrap" fails with EIO for configured labels a number of times.
state="$FAKE_STATE"
echo "$*" >> "$state/calls.log"
cmd="$1"; target="${!#}"; label="$(basename "${target%.plist}")"
case "$cmd" in
  bootout) echo "${PRINT_POLLS:-2}" > "$state/$label.polls" ;;
  print)
    n=$(cat "$state/$label.polls" 2>/dev/null || echo 0)
    if [[ "$n" -gt 0 ]]; then echo $((n - 1)) > "$state/$label.polls"; exit 0; fi
    exit 113 ;;
  bootstrap)
    left=$(cat "$state/$label.fail" 2>/dev/null || echo 0)
    if [[ "$left" -gt 0 ]]; then
      echo $((left - 1)) > "$state/$label.fail"
      echo "Bootstrap failed: 5: Input/output error" >&2
      exit 5
    fi ;;
esac
exit 0
"""


def _install(tmp_path, *, fail: dict[str, int] | None = None, print_polls: int = 2):
    bin_dir = tmp_path / "bin"
    state = tmp_path / "state"
    bin_dir.mkdir()
    state.mkdir()
    for name, body in (
        ("launchctl", FAKE_LAUNCHCTL),
        ("uname", "#!/bin/bash\necho Darwin\n"),
        ("sleep", "#!/bin/bash\necho \"$*\" >> \"$FAKE_STATE/sleeps.log\"\n"),
    ):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    for label, count in (fail or {}).items():
        (state / f"{label}.fail").write_text(str(count))
    env = {**os.environ, "HOME": str(tmp_path / "home"), "FAKE_STATE": str(state),
           "PRINT_POLLS": str(print_polls), "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    proc = subprocess.run(["bash", str(SCRIPT), "--uv", FAKE_UV], capture_output=True, text=True,
                          env=env, check=False)
    calls = (state / "calls.log").read_text().splitlines()
    return proc, calls


def test_install_waits_for_bootout_before_bootstrap(tmp_path) -> None:
    proc, calls = _install(tmp_path, print_polls=2)
    assert proc.returncode == 0, proc.stderr
    listener = [c for c in calls
                if c.endswith(("com.cec.vivisystem.listener", "com.cec.vivisystem.listener.plist"))]
    assert listener[:4] == [
        f"bootout gui/{os.getuid()}/com.cec.vivisystem.listener",
        f"print gui/{os.getuid()}/com.cec.vivisystem.listener",
        f"print gui/{os.getuid()}/com.cec.vivisystem.listener",
        f"print gui/{os.getuid()}/com.cec.vivisystem.listener",
    ]
    assert sum(c.startswith("bootstrap") for c in calls) == 5
    assert calls[-1] == f"kickstart -k gui/{os.getuid()}/com.cec.vivisystem.listener"


def test_install_retries_bootstrap_after_eio(tmp_path) -> None:
    proc, calls = _install(tmp_path, fail={"com.cec.vivisystem.listener": 2})
    assert proc.returncode == 0, proc.stderr
    assert sum("bootstrap" in c and "listener" in c for c in calls) == 3
    assert "bootstrap attempt 1/3 failed" in proc.stderr
    assert any(c.startswith("kickstart") for c in calls)


def test_install_continues_and_exits_nonzero_when_bootstrap_keeps_failing(tmp_path) -> None:
    proc, calls = _install(tmp_path, fail={"com.cec.vivisystem.listener": 9})
    assert proc.returncode == 1
    assert "error: could not load com.cec.vivisystem.listener" in proc.stderr
    assert "not loaded: com.cec.vivisystem.listener" in proc.stderr
    later = [c for c in calls if c.startswith("bootstrap") and "listener" not in c]
    assert len(later) == 4, "the other agents are still installed"
    assert not any(c.startswith("kickstart") for c in calls), "no kickstart for an unloaded listener"
