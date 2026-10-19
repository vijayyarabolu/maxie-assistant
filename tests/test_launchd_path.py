"""the hub runs under launchd with the PATH from its plist, not his Terminal's. A command that lives
in /usr/sbin (taskpolicy, networksetup) was "not found" there, and `claude` found a stale Homebrew copy instead of
the one he updates in ~/.local/bin. Every command the package spawns must resolve to the same file under both."""

import ast
import plistlib
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLISTS = sorted((ROOT / "deploy").glob("dev.maxie.*.plist"))
HOME = str(Path.home())
LOGIN_PATH = ":".join([f"{HOME}/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin",
                       "/sbin"])
NOT_COMMANDS = {"route"}  # a JSON schema's field list (router/haiku.py) that happens to name /sbin/route


def launchd_path(plist: Path) -> str:
    env = plistlib.loads(plist.read_bytes()).get("EnvironmentVariables", {})
    return env.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin").replace("__HOME__", HOME)


def spawned_commands() -> set[str]:
    """The first word of every list or tuple of strings in the package that names a real program."""
    names = set()
    for f in (ROOT / "maxie").rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.List | ast.Tuple) and node.elts:
                first = node.elts[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str) and "/" not in first.value \
                        and first.value not in NOT_COMMANDS and first.value and shutil.which(first.value, path=LOGIN_PATH):
                    names.add(first.value)
    return names


def test_every_plist_has_a_path_with_sbin_and_his_local_bin():
    for plist in (p for p in PLISTS if "<key>PATH</key>" in p.read_text()):  # logrotate runs a plain sh script
        dirs = launchd_path(plist).split(":")
        assert f"{HOME}/.local/bin" == dirs[0] and "/usr/sbin" in dirs, plist.name


def test_install_fills_in_home():
    assert "__HOME__" in (ROOT / "deploy" / "install.sh").read_text()


@pytest.mark.skipif(sys.platform != "darwin", reason="the services run on his Mac")
def test_the_scan_sees_the_spawned_commands():
    assert {"say", "open", "osascript", "shortcuts", "security"} <= spawned_commands()


@pytest.mark.skipif(sys.platform != "darwin", reason="the services run on his Mac")
def test_every_spawned_command_is_the_same_program_under_launchd():
    hub = launchd_path(ROOT / "deploy" / "dev.maxie.hub.plist")
    wrong = {c: (shutil.which(c, path=hub), shutil.which(c, path=LOGIN_PATH)) for c in spawned_commands()
             if shutil.which(c, path=hub) != shutil.which(c, path=LOGIN_PATH)}
    assert not wrong, f"launchd finds a different (or no) program: {wrong}"
