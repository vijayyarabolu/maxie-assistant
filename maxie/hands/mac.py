"""Direct Mac actions: `open`, AppleScript/JXA, Shortcuts, Spotlight.

`MacActions` is the interface. `FakeMac` records calls (cloud + tests).
`MacOS` is the real one; its AppleScript is drafted here and verified on the Mac
. Everything here is harmless or a draft he sends himself.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from typing import Protocol

from ..safety import in_background
from .registry import Registry, Tool, schema

CLIPBOARD_MAX = 4000  # characters handed to the model


class MacActions(Protocol):
    def open_app(self, name: str) -> str: ...
    def open_url(self, url: str) -> str: ...
    def open_path(self, path: str) -> str: ...
    def music(self, command: str, query: str = "") -> str: ...
    def volume(self, level: str) -> str: ...
    def timer(self, minutes: float, label: str = "") -> str: ...
    def dnd(self, on: bool) -> str: ...
    def find_files(self, query: str, folders: list[str]) -> list[str]: ...
    def add_reminder(self, text: str, when: str = "") -> str: ...
    def add_note(self, title: str, body: str) -> str: ...
    def draft_message(self, to: str, text: str) -> str: ...
    def status(self, what: str) -> str: ...
    def clipboard(self) -> str: ...


class FakeMac:
    def __init__(self, files: list[str] | None = None):
        self.calls: list[tuple] = []
        self.files = files or ["~/Documents/resume_ai.pdf", "~/Documents/notes/interview-prep.md"]

    def _rec(self, *call) -> str:
        self.calls.append(call)
        return "done"

    def open_app(self, name): return self._rec("open_app", name)
    def open_url(self, url): return self._rec("open_url", url)
    def open_path(self, path): return self._rec("open_path", path)
    def music(self, command, query=""): return self._rec("music", command, query)
    def volume(self, level): return self._rec("volume", level)
    def timer(self, minutes, label=""): return self._rec("timer", minutes, label)
    def dnd(self, on): return self._rec("dnd", on)
    def add_reminder(self, text, when=""): return self._rec("add_reminder", text, when)
    def add_note(self, title, body): return self._rec("add_note", title, body)
    def draft_message(self, to, text): return self._rec("draft_message", to, text)
    def status(self, what): return self._rec("status", what)
    def clipboard(self): return "fake clipboard text"

    def find_files(self, query, folders):
        self.calls.append(("find_files", query))
        return [f for f in self.files if query.lower() in f.lower()]


def _open(*args: str) -> None:
    """`open`; in the background with -g, so the app opens behind his windows and never takes focus."""
    subprocess.run(["open", "-g", *args] if in_background() else ["open", *args], check=True, timeout=10)


OSA_TIMEOUT_S = 15
STUCK = ("The app didn't answer. If macOS is asking whether Maxie may control it, allow that in System Settings › "
         "Privacy & Security › Automation (the question can't show while the Mac is locked).")


def _osa(script: str) -> str:
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=OSA_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(STUCK) from e
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "osascript failed")
    return out.stdout.strip() or "done"


MUSIC_CLOSED = "Music isn't open, so nothing was playing."


def music_if_running(verb: str) -> str:
    """Pause / skip only a Music that is open: telling a closed Music to pause launched it, and with the screen
    locked the launch hung until the 15 s timeout."""
    return (f'if application "Music" is running then\n  tell application "Music" to {verb}\n  return "done"\n'
            f'else\n  return "{MUSIC_CLOSED}"\nend if')


def _q(s: str) -> str:
    """Quote a string for AppleScript."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


STATUS_COMMANDS = {  # the only shell the hub runs: fixed commands, no arguments from the model
    "battery": ["pmset", "-g", "batt"],
    "disk": ["df", "-h", "/"],
    "uptime": ["uptime"],
    "wifi": ["/usr/sbin/networksetup", "-getairportnetwork", "en0"],
}


class MacOS:
    """Real actions through open, osascript and shortcuts (macOS only)."""

    def open_app(self, name):
        _open("-a", name)
        return f"opened {name}"

    def open_url(self, url):
        if not url.startswith(("https://", "http://")):
            raise ValueError("only http(s) links")
        _open(url)
        return f"opened {url}"

    def open_path(self, path):
        _open(path)
        return f"opened {path}"

    def music(self, command, query=""):
        if command == "play" and query:
            # TODO(mac): Music search-and-play needs a Shortcut ("Maxie Play") for Apple Music catalog items.
            return _osa(f'tell application "Music" to play (first track of playlist "Library" whose name contains {_q(query)})')
        verbs = {"play": "play", "pause": "pause", "next": "next track", "previous": "previous track"}
        if command == "play":
            return _osa(f'tell application "Music" to {verbs[command]}')
        return _osa(music_if_running(verbs[command]))

    def volume(self, level):
        if level == "mute":
            return _osa("set volume with output muted")
        if level in ("up", "down"):
            step = 12 if level == "up" else -12
            return _osa(f"set volume output volume ((output volume of (get volume settings)) + {step}) without output muted")
        return _osa(f"set volume output volume {int(level)} without output muted")

    def timer(self, minutes, label=""):
        seconds = int(float(minutes) * 60)
        msg = label or f"{minutes:g} minute timer"
        note = f"display notification {_q(msg)} with title \"Maxie\" sound name \"Glass\""
        subprocess.Popen(["sh", "-c", f"sleep {seconds}; osascript -e {shlex.quote(note)}; say {shlex.quote(msg + ' is done')}"])
        return f"timer set for {minutes:g} min"

    def dnd(self, on):
        # TODO(mac): needs Shortcuts named "Maxie Focus On" / "Maxie Focus Off" (Focus has no AppleScript API).
        name = "Maxie Focus On" if on else "Maxie Focus Off"
        subprocess.run(["shortcuts", "run", name], check=True, timeout=15)
        return "Do Not Disturb " + ("on" if on else "off")

    def find_files(self, query, folders):
        hits: list[str] = []
        for folder in folders:
            out = subprocess.run(["mdfind", "-onlyin", str(Path(folder).expanduser()), query],
                                 capture_output=True, text=True, timeout=10)
            hits += out.stdout.splitlines()
        return hits[:20]

    def add_reminder(self, text, when=""):
        return _osa(f'tell application "Reminders" to make new reminder with properties {{name:{_q(text)}, body:{_q(when)}}}')

    def add_note(self, title, body):
        return _osa(f'tell application "Notes" to make new note with properties {{name:{_q(title)}, body:{_q(body)}}}')

    def draft_message(self, to, text):
        # Opens a draft; you press send yourself.
        from urllib.parse import quote
        subprocess.run(["open", f"sms:{quote(to)}&body={quote(text)}"], check=True, timeout=10)
        return f"draft to {to} is open, tap send when ready"

    def clipboard(self):
        out = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
        return out.stdout[:CLIPBOARD_MAX]

    def status(self, what):
        return subprocess.run(STATUS_COMMANDS[what], capture_output=True, text=True, timeout=10).stdout.strip()


class MacOnly(RuntimeError):
    """A Mac action on a hub that isn't running on macOS."""


MAC_ONLY_LINE = ("That needs macOS. This hub runs on {system}, so the Mac actions (apps, music, volume, timers, "
                 "Notes, Reminders) are off. Everything else still works.")


class NoMac:
    """Stands in for the Mac on Linux or Windows: every action says plainly that it's Mac only."""

    def __init__(self, system: str = sys.platform):
        self.system = {"linux": "Linux", "win32": "Windows"}.get(system, system)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def off(*_a, **_k):
            raise MacOnly(MAC_ONLY_LINE.format(system=self.system))
        return off


def default_mac() -> MacActions:
    return MacOS() if sys.platform == "darwin" else NoMac()


def in_allowed(path: str, folders: list[str]) -> bool:
    p = Path(path).expanduser().resolve()
    return any(p.is_relative_to(Path(f).expanduser().resolve()) for f in folders)


# Actions the router may run with no Claude call, even before Laya is calibrated.
HARMLESS = {"open_app", "music", "volume", "timer"}


def register(reg: Registry, mac: MacActions, allowed_folders: list[str]) -> None:
    def open_file(a):
        if not in_allowed(a["path"], allowed_folders):
            raise PermissionError("that folder isn't on the allowed list (Settings)")
        return mac.open_path(a["path"])

    s, n = {"type": "string"}, {"type": "number"}
    reg.add(Tool("open_app", "Open a Mac app by name.", schema({"name": s}), lambda a: mac.open_app(a["name"]), "action"))
    reg.add(Tool("open_url", "Open a web link in the browser.", schema({"url": s}), lambda a: mac.open_url(a["url"]), "action"))
    reg.add(Tool("open_file", "Open a file from an allowed folder.", schema({"path": s}), open_file, "action"))
    reg.add(Tool("music", "Control Apple Music: play (optionally a song/artist/playlist), pause, next, previous.",
                 schema({"command": {"type": "string", "enum": ["play", "pause", "next", "previous"]},
                         "query": s}, ["command"]),
                 lambda a: mac.music(a["command"], a.get("query", "")), "action"))
    reg.add(Tool("volume", "Set Mac volume: 0-100, 'up', 'down' or 'mute'.", schema({"level": s}),
                 lambda a: mac.volume(str(a["level"])), "action"))
    reg.add(Tool("timer", "Start a timer.", schema({"minutes": n, "label": s}, ["minutes"]),
                 lambda a: mac.timer(float(a["minutes"]), a.get("label", "")), "action"))
    reg.add(Tool("do_not_disturb", "Turn Do Not Disturb on or off.", schema({"on": {"type": "boolean"}}),
                 lambda a: mac.dnd(bool(a["on"])), "action"))
    reg.add(Tool("find_files", "Spotlight search for files in the allowed folders.", schema({"query": s}),
                 lambda a: mac.find_files(a["query"], allowed_folders), "read", untrusted_output=True))
    reg.add(Tool("add_reminder", "Add a reminder.", schema({"text": s, "when": s}, ["text"]),
                 lambda a: mac.add_reminder(a["text"], a.get("when", "")), "action"))
    reg.add(Tool("add_note", "Create a note in Notes.", schema({"title": s, "body": s}),
                 lambda a: mac.add_note(a["title"], a["body"]), "action"))
    reg.add(Tool("draft_message", "Open an iMessage draft. The user sends it.", schema({"to": s, "text": s}),
                 lambda a: mac.draft_message(a["to"], a["text"]), "action"))
    reg.add(Tool("clipboard", "Read the text the user has copied (the clipboard).", schema({}),
                 lambda a: mac.clipboard() or "(the clipboard is empty or not text)", "read", untrusted_output=True))
    reg.add(Tool("mac_status", "Battery, disk, uptime or wifi status.",
                 schema({"what": {"type": "string", "enum": sorted(STATUS_COMMANDS)}}),
                 lambda a: mac.status(a["what"]), "read"))
