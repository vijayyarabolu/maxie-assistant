"""Knows your Claude Code sessions, read-only.

Reads the transcripts Claude Code keeps in ~/.claude/projects/<project>/<session>.jsonl: the title, the
last thing you asked, the last thing Claude said, whether it is still working, finished, or waiting for you.
Plus the project's recent git log and its progress notes (the newest PROGRESS*.md at the project root or in
docs/ or Prompts/), so "how's the refactor going?" gets a three-line answer.
Never types into your terminals; never changes a transcript.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .registry import Registry, Tool, schema

ACTIVE_S = 3 * 60        # a transcript written in the last 3 minutes: still working
RECENT_DAYS = 3          # sessions older than this are not "your sessions right now"
MAX_SESSIONS = 6


@dataclass(frozen=True)
class Session:
    project: str          # folder name (maxie, jobhunt)
    path: str             # project folder
    title: str
    last_prompt: str
    last_reply: str
    updated: float        # epoch seconds of the last record
    status: str           # working | waiting for you | finished
    branch: str

    def line(self) -> str:
        ago = _ago(time.time() - self.updated)
        head = f"{self.project} ({self.title})" if self.title else self.project
        return f"{head}: {self.status}, {ago}. Last: {self.last_reply[:160]}" if self.last_reply else f"{head}: {self.status}, {ago}."


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} d ago"


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


@dataclass
class _Scan:
    """What one transcript said so far, and how far it was read. Transcripts only grow, so each read starts where
    the last stopped (the watcher re-parsed a 265 MB transcript every minute, most of the hub's idle CPU)."""
    offset: int = 0
    tail: bytes = b""     # the bytes just before `offset`: a rewritten file no longer has them there
    title: str = ""
    last_prompt: str = ""
    last_reply: str = ""
    branch: str = ""
    cwd: str = ""
    last_ts: float = 0.0
    last_role: str = ""
    waiting: bool = False

    def feed(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "ai-title":
            self.title = ev.get("aiTitle") or self.title
            return
        if t not in ("user", "assistant"):
            return
        msg = ev.get("message") or {}
        content = msg.get("content")
        text = _text(content).strip()
        self.cwd = ev.get("cwd") or self.cwd
        self.branch = ev.get("gitBranch") or self.branch
        if ts := _epoch(ev.get("timestamp")):
            self.last_ts = max(self.last_ts, ts)
        if t == "user" and text and not text.startswith("<") and not isinstance(content, list):
            self.last_prompt, self.last_role = text, "user"
        elif t == "user" and isinstance(content, list):
            if any(b.get("type") == "tool_result" for b in content if isinstance(b, dict)):
                self.last_role = "tool_result"
        elif t == "assistant":
            blocks = content if isinstance(content, list) else []
            self.waiting = any(b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion" for b in blocks if isinstance(b, dict))
            if text:
                self.last_reply, self.last_role = text, "assistant"
            elif any(b.get("type") == "tool_use" for b in blocks if isinstance(b, dict)):
                self.last_role = "tool_use"


TAIL_BYTES = 64
_scans: dict[str, _Scan] = {}


def _scan(path: Path) -> _Scan:
    """The transcript read up to its last whole line, from where the last read of it stopped."""
    scan = _scans.get(str(path))
    with open(path, "rb") as fh:
        if scan is not None and scan.offset:
            fh.seek(scan.offset - len(scan.tail))
            if fh.read(len(scan.tail)) != scan.tail:
                scan = None  # rewritten, not appended to: read it again
        if scan is None:
            scan = _Scan()
            fh.seek(0)
        for raw in fh:
            if raw.endswith(b"\n"):
                scan.offset += len(raw)
                scan.tail = (scan.tail + raw)[-TAIL_BYTES:]
            try:
                ev = json.loads(raw)
            except ValueError:  # a half-written last line (read again next time), or not JSON
                continue
            if isinstance(ev, dict):
                scan.feed(ev)
    _scans[str(path)] = scan
    return scan


def read_session(path: Path, now: float | None = None) -> Session | None:
    """Parse one transcript (only the fields we need, only what was added since the last read). None when it has
    no user turn."""
    now = now or time.time()
    try:
        scan = _scan(path)
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if not scan.last_prompt:
        return None
    if scan.waiting and scan.last_role in ("assistant", "tool_use"):
        status = "waiting for you"
    elif now - mtime < ACTIVE_S:
        status = "working"
    else:
        status = "finished"
    project = Path(scan.cwd).name if scan.cwd else path.parent.name.rsplit("-", 1)[-1]
    # newest = whichever is later: the last record's timestamp or the file's mtime (a write with an older
    # timestamp, or two sessions with equal ones, must not make the order depend on directory listing order)
    return Session(project, scan.cwd, scan.title, scan.last_prompt[:300], scan.last_reply[:600],
                   max(scan.last_ts, mtime), status, scan.branch)


def _epoch(ts: str | None) -> float:
    if not ts:
        return 0.0
    from datetime import datetime
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def recent_sessions(root: Path | None = None, now: float | None = None, limit: int = MAX_SESSIONS) -> list[Session]:
    """The newest transcript per project, newest first, from the last few days."""
    root = root or Path.home() / ".claude" / "projects"
    now = now or time.time()
    out = []
    if not root.is_dir():
        return out
    for project_dir in root.iterdir():
        files = sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if project_dir.is_dir() else []
        if not files or now - files[0].stat().st_mtime > RECENT_DAYS * 86400:
            continue
        if s := read_session(files[0], now):
            out.append(s)
    out.sort(key=lambda s: s.updated, reverse=True)
    return out[:limit]


def git_log(folder: str, n: int = 5) -> str:
    try:
        r = subprocess.run(["git", "-C", folder, "log", "--oneline", f"-{n}"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def progress_tail(folder: str, chars: int = 1500) -> str:
    """The end of the newest PROGRESS*.md (project root, docs/ or Prompts/), where the latest section is."""
    root = Path(folder)
    found = [p for d in (root, root / "docs", root / "Prompts") if d.is_dir() for p in d.glob("PROGRESS*.md")]
    files = sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0].read_text(errors="replace")[-chars:] if files else ""


def report(project: str | None = None, root: Path | None = None) -> dict:
    """Everything the brain needs to answer "how's X going?" in three lines."""
    sessions = recent_sessions(root)
    if project:
        p = project.lower()
        sessions = [s for s in sessions if p in s.project.lower() or p in s.title.lower() or p in s.last_prompt.lower()]
    out = {"sessions": [asdict(s) | {"line": s.line()} for s in sessions]}
    if sessions and sessions[0].path:
        out["git_log"] = git_log(sessions[0].path)
        out["progress"] = progress_tail(sessions[0].path)
    return out


class Watcher:
    """Notices a session finishing or waiting for you (the scheduler ticks it every minute)."""

    def __init__(self, notify, root: Path | None = None, clock=time.time):
        self.notify, self.root, self.clock = notify, root, clock
        self._seen: dict[str, str] = {}  # project -> last status

    def tick(self) -> list[str]:
        sent = []
        for s in recent_sessions(self.root, self.clock()):
            before = self._seen.get(s.project)
            self._seen[s.project] = s.status
            if before == "working" and s.status == "finished":
                body = s.last_reply.splitlines()[0][:140] if s.last_reply else "done"
                self.notify(f"Claude Code finished in {s.project}", body, tag=f"cc-{s.project}", mood="proud")
                sent.append(s.project)
            elif before in ("working", "finished", None) and s.status == "waiting for you" and before != "waiting for you":
                self.notify(f"Claude Code is waiting for you in {s.project}", s.last_reply.splitlines()[0][:140] if s.last_reply else "",
                            tag=f"cc-{s.project}", mood="")
                sent.append(s.project)
        return sent


def register(reg: Registry, root: Path | None = None) -> None:
    reg.add(Tool("claude_code_sessions",
                 "The user's Claude Code sessions, read-only: what each is doing, waiting for them, or finished, "
                 "with the project's recent git log and progress notes. Use for 'how's the refactor going', 'is Claude Code "
                 "done', 'what did the jobhunt session do'. Answer in three lines.",
                 schema({"project": {"type": "string", "description": "project or goal name to filter by, or empty"}}, []),
                 lambda a: report(a.get("project") or None, root), "read"))
