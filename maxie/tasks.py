"""Every task as a live timeline: what he asked, the route and model, each step (tool, ok, ms),
the result, the cost, and its status. Written by the agent as it goes, read by the panel's Activity tab
and the PWA; updates ride the events stream as {"type": "task", ...}."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .db import DB

STATUSES = ("running", "paused", "done", "stopped", "error")
KEEP = 500  # tasks kept in the table; older rows are trimmed


@dataclass
class Step:
    name: str
    ok: bool
    ms: int
    at: float


@dataclass
class Task:
    id: int
    ts: float
    text: str
    device: str
    route: str = ""
    model: str | None = None
    status: str = "running"
    steps: list[Step] = field(default_factory=list)
    reply: str = ""
    cost: float = 0.0
    first_ms: float | None = None
    total_ms: float | None = None

    def dict(self) -> dict:
        return {"id": self.id, "ts": self.ts, "text": self.text, "device": self.device, "route": self.route,
                "model": self.model, "status": self.status, "steps": [s.__dict__ for s in self.steps],
                "reply": self.reply, "cost": round(self.cost, 6), "first_ms": self.first_ms, "total_ms": self.total_ms}


class Tasks:
    def __init__(self, db: DB, events=None, clock=time.time):
        self.db, self.events, self.clock = db, events, clock
        db.script("""CREATE TABLE IF NOT EXISTS tasks(
            id INTEGER PRIMARY KEY, ts REAL, text TEXT, device TEXT, route TEXT, model TEXT, status TEXT,
            steps TEXT, reply TEXT, cost REAL, first_ms REAL, total_ms REAL)""")
        self.current: Task | None = None
        self.last_id: int | None = None  # the newest task this timeline started (a background job links to it)
        self._t0 = 0.0
        self._last_step_at = 0.0

    # -- the agent calls these in order -----------------------------------------------------------

    def start(self, text: str, device: str) -> Task:
        now = self.clock()
        cur = self.db.execute("INSERT INTO tasks(ts,text,device,status,steps,reply,cost) VALUES (?,?,?,?,?,?,?)",
                              (now, text[:300], device, "running", "[]", "", 0.0))
        self.current = Task(cur.lastrowid, now, text[:300], device)
        self.last_id = cur.lastrowid
        self._t0 = self._last_step_at = time.perf_counter()
        self._save(self.current)
        self._trim()
        return self.current

    def routed(self, route: str, model: str | None) -> None:
        if t := self.current:
            t.route, t.model = route, model
            self._save(t)

    def step(self, name: str, ok: bool) -> None:
        if t := self.current:
            now = time.perf_counter()
            t.steps.append(Step(name, ok, int((now - self._last_step_at) * 1000), self.clock()))
            self._last_step_at = now
            self._save(t)

    def first_word(self) -> None:
        if (t := self.current) and t.first_ms is None:
            t.first_ms = round((time.perf_counter() - self._t0) * 1000)

    def finish(self, reply: str, cost: float, stopped: bool = False, error: bool = False) -> None:
        if t := self.current:
            t.reply, t.cost = reply[:2000], cost
            t.status = "error" if error else "stopped" if stopped else "done"
            t.total_ms = round((time.perf_counter() - self._t0) * 1000)
            self._save(t)
            self.current = None

    def set_status(self, status: str) -> None:
        if (t := self.current) and status in STATUSES:
            t.status = status
            self._save(t)

    # -- reading ------------------------------------------------------------------------------------

    def recent(self, limit: int = 30) -> list[dict]:
        rows = self.db.all("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["steps"] = json.loads(r["steps"] or "[]")
        return rows

    def get(self, task_id: int) -> dict | None:
        r = self.db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if r:
            r["steps"] = json.loads(r["steps"] or "[]")
        return r

    def _trim(self) -> None:
        """Keep the newest KEEP rows of the app's own tasks table."""
        self.db.execute("DELETE FROM tasks WHERE id <= (SELECT MAX(id) FROM tasks) - ?", (KEEP,))

    def _save(self, t: Task) -> None:
        self.db.execute("UPDATE tasks SET route=?, model=?, status=?, steps=?, reply=?, cost=?, first_ms=?, total_ms=? WHERE id=?",
                        (t.route, t.model, t.status, json.dumps([s.__dict__ for s in t.steps]), t.reply, t.cost,
                         t.first_ms, t.total_ms, t.id))
        if self.events:
            self.events.publish({"type": "task", **t.dict()})
