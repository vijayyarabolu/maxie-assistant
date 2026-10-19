"""Watch me once, then do it.

The Mac app records one flow while he does it (clicks on named buttons and fields, typing, a few keys),
through Accessibility, and saves it here. Replaying is a "run" action, so it asks him first by default
(Settings › Rules). Once he approves, the hub hands the steps to the Mac app over the events stream
({"type": "flow_run"}); the app replays them and posts the result back.

Never stored: anything typed into a password field (the recorder skips secure fields) and anything that
looks like a secret, card or ID number (refused here as well). Steps are plain data: no scripts.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass

from .db import DB
from .hands.registry import Registry, Tool, schema
from .memory import looks_secret

MAX_STEPS = 80
MAX_TEXT = 300
NAME_MAX = 60
KINDS = ("open_app", "click", "type", "key")
KEYS = ("return", "tab", "escape", "space", "delete", "up", "down", "left", "right", "pageup", "pagedown",
        "home", "end", *"abcdefghijklmnopqrstuvwxyz0123456789")
MODS = ("cmd", "shift", "option", "control")
CLICK_FIELDS = ("app", "window", "role", "title", "label", "id")
RUN_TIMEOUT_S = 15 * 60
SCREEN_S_PER_STEP = 2  # a replayed step: find the control, press it, the 350 ms pause; rounded up to 10 s


class FlowError(ValueError):
    """A flow that can't be saved or found; the message is for him."""


@dataclass(frozen=True)
class Flow:
    id: int
    name: str
    steps: list[dict]
    created: float
    runs: int
    last_run: float | None
    last_result: str

    @property
    def apps(self) -> list[str]:
        seen: list[str] = []
        for s in self.steps:
            app = (s.get("name") or s.get("bundle")) if s["do"] == "open_app" else None
            if app and app not in seen:
                seen.append(app)
        return seen

    def line(self) -> str:
        n = len(self.steps)
        apps = self.apps
        where = f" in {', '.join(apps[:-1])} and {apps[-1]}" if len(apps) > 1 else (f" in {apps[0]}" if apps else "")
        return f"{n} step{'s' if n != 1 else ''}{where}"

    def seconds(self) -> int:
        """About how long a replay holds the screen: "Can I use the screen for ~20 s?"."""
        return max(10, -(-len(self.steps) * SCREEN_S_PER_STEP // 10) * 10)

    def dict(self) -> dict:
        return {"id": self.id, "name": self.name, "steps": self.steps, "created": self.created, "runs": self.runs,
                "last_run": self.last_run, "last_result": self.last_result, "line": self.line()}


def _text(v, field: str, limit: int = MAX_TEXT) -> str:
    if not isinstance(v, str):
        raise FlowError(f"{field} must be text")
    v = v.strip()
    if len(v) > limit:
        raise FlowError(f"{field} is longer than {limit} characters")
    return v


def clean_step(raw: dict) -> dict:
    """One recorded step, checked and trimmed to the fields the player uses."""
    if not isinstance(raw, dict):
        raise FlowError("each step must be an object")
    kind = raw.get("do")
    if kind not in KINDS:
        raise FlowError(f"unknown step {kind!r}")
    if kind == "open_app":
        bundle = _text(raw.get("bundle", ""), "bundle", 200)
        name = _text(raw.get("name", ""), "name", 100)
        if not (bundle or name):
            raise FlowError("open_app needs an app")
        return {"do": kind, "bundle": bundle, "name": name}
    if kind == "click":
        step = {"do": kind, **{f: _text(raw.get(f, ""), f, 200) for f in CLICK_FIELDS}}
        if not (step["title"] or step["label"] or step["id"]):
            raise FlowError("a click needs something to find the control by: its title, label or id")
        return step
    if kind == "type":
        if raw.get("secure"):
            raise FlowError("text typed into a password field is never stored")
        text = _text(raw.get("text", ""), "text")
        if not text:
            raise FlowError("empty typing step")
        if looks_secret(text):
            raise FlowError("that typing looks like a secret or an ID number, so it isn't stored")
        return {"do": kind, "text": text}
    key = raw.get("key")
    mods = raw.get("mods", [])
    if key not in KEYS:
        raise FlowError(f"unknown key {key!r}")
    if not isinstance(mods, list) or any(m not in MODS for m in mods):
        raise FlowError("modifiers must be cmd, shift, option or control")
    return {"do": kind, "key": key, "mods": sorted(set(mods), key=MODS.index)}


def clean_name(name) -> str:
    name = re.sub(r"\s+", " ", _text(name, "name", NAME_MAX))
    if not name:
        raise FlowError("give the flow a name")
    return name


class Flows:
    def __init__(self, db: DB, clock=time.time):
        self.db, self.clock = db, clock
        self.events = None  # maxie.events.Events, set by the hub: replays go to the Mac app over it
        self.audit = None
        self._runs: dict[str, dict] = {}
        self.recording = False  # "watch me once" is on: "stop watching" ends it (not Maxie's eyes)
        db.script("""CREATE TABLE IF NOT EXISTS flows(id INTEGER PRIMARY KEY, name TEXT UNIQUE COLLATE NOCASE,
                     steps TEXT, created REAL, runs INTEGER DEFAULT 0, last_run REAL, last_result TEXT DEFAULT '')""")

    @staticmethod
    def _flow(row: dict) -> Flow:
        return Flow(row["id"], row["name"], json.loads(row["steps"]), row["created"], row["runs"] or 0,
                    row["last_run"], row["last_result"] or "")

    def save(self, name, steps) -> Flow:
        name = clean_name(name)
        if not isinstance(steps, list) or not steps:
            raise FlowError("nothing was recorded")
        if len(steps) > MAX_STEPS:
            raise FlowError(f"a flow can have at most {MAX_STEPS} steps")
        cleaned = [clean_step(s) for s in steps]
        if self.find_exact(name):
            raise FlowError(f"there is already a flow called {name}")
        self.db.execute("INSERT INTO flows(name, steps, created) VALUES(?,?,?)", (name, json.dumps(cleaned), self.clock()))
        self.recording = False
        return self.find_exact(name)

    def all(self) -> list[Flow]:
        return [self._flow(r) for r in self.db.all("SELECT * FROM flows ORDER BY created DESC")]

    def get(self, flow_id: int) -> Flow | None:
        row = self.db.one("SELECT * FROM flows WHERE id=?", (flow_id,))
        return self._flow(row) if row else None

    def find_exact(self, name: str) -> Flow | None:
        row = self.db.one("SELECT * FROM flows WHERE name=?", (name.strip(),))
        return self._flow(row) if row else None

    def find(self, name: str) -> Flow:
        """By name as he says it: exact, then contained, then the most shared words."""
        name = re.sub(r"\s+", " ", re.sub(r"\b(?:the|my|flow)\b", " ", name.lower())).strip()
        flows = self.all()
        for f in flows:
            if f.name.lower() == name:
                return f
        inside = [f for f in flows if name and (name in f.name.lower() or f.name.lower() in name)]
        if len(inside) == 1:
            return inside[0]
        words = set(name.split())
        scored = sorted(((len(words & set(f.name.lower().split())), f) for f in flows), key=lambda x: -x[0])
        if scored and scored[0][0] and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return scored[0][1]
        known = ", ".join(f.name for f in flows) or "none yet"
        raise FlowError(f"I don't have a flow called {name or 'that'}. Flows: {known}.")

    def forget(self, flow_id: int) -> bool:
        return self.db.execute("DELETE FROM flows WHERE id=?", (flow_id,)).rowcount > 0

    def start(self, flow: Flow) -> str:
        """Hand the steps to the Mac app; returns the run id it reports back with."""
        if self.events is None:
            raise FlowError("the hub has no events stream")
        run = secrets.token_hex(6)
        self._runs = {k: v for k, v in self._runs.items() if self.clock() - v["started"] < RUN_TIMEOUT_S}
        self._runs[run] = {"flow": flow.id, "started": self.clock()}
        self.events.publish({"type": "flow_run", "run": run, "name": flow.name, "steps": flow.steps})
        return run

    def finish(self, run: str, ok: bool, done: int, error: str = "") -> Flow:
        info = self._runs.pop(run, None)
        if info is None:
            raise FlowError("no such run (or it timed out)")
        flow = self.get(info["flow"])
        if flow is None:
            raise FlowError("that flow was forgotten")
        result = "Done." if ok else f"Stopped at step {done + 1} of {len(flow.steps)}: {error[:200] or 'it went wrong'}."
        self.db.execute("UPDATE flows SET runs=runs+1, last_run=?, last_result=? WHERE id=?", (self.clock(), result, flow.id))
        if self.audit:
            self.audit.log("mac", "run_flow", {"name": flow.name}, "replay", "ok" if ok else f"error: {result}")
        if self.events:
            self.events.transcript("assistant", f"{flow.name}: {result}", final=True)
        return self.get(flow.id)

    def watch(self, on: bool) -> None:
        if self.events is None:
            raise FlowError("the hub has no events stream")
        self.recording = on
        self.events.publish({"type": "flow_watch", "on": on})


def register(reg: Registry, flows: Flows) -> None:
    def run_flow(a: dict) -> str:
        f = flows.find(a["name"])
        flows.start(f)
        return f"Replaying {f.name} on the Mac: {f.line()}."

    def list_flows(_: dict) -> str:
        fs = flows.all()
        if not fs:
            return "No flows yet. Say 'watch me' (or press ⌥⇧W), do it once, then name it."
        return "\n".join(f"{f.name}: {f.line()}" + (f", last run: {f.last_result}" if f.last_result else "") for f in fs)

    def watch_me(a: dict) -> str:
        on = bool(a.get("on", True))
        flows.watch(on)
        return "Watching. Do it once, then say 'done watching' or press ⌥⇧W." if on else "Stopped watching."

    s = {"type": "string"}
    def ask(a: dict) -> str:
        try:
            f = flows.find(a["name"])
        except FlowError:
            return f"Replay {a['name']} on your Mac"
        return f"Use the screen for ~{f.seconds()} s to replay {f.name}"

    reg.add(Tool("run_flow", "Replay a flow he taught by doing it once on the Mac ('watch me once'), by its name. "
                             "It uses the real screen, so it waits for the user's OK.",
                 schema({"name": s}), run_flow, kind="run", summary=ask, check=lambda a: flows.find(a["name"]),
                 screen=True))
    reg.add(Tool("list_flows", "The flows he taught by doing them once on the Mac, with their steps and last result.",
                 schema({}), list_flows))
    reg.add(Tool("watch_me", "Start (on: true) or stop (on: false) watching the user do something once on the Mac, "
                             "to replay later.", schema({"on": {"type": "boolean"}}), watch_me, kind="action"))
