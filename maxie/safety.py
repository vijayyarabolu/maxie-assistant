"""Safety: audit log, confirmations, kill switch, and the prompt-injection guard.

The rules:
- Risky tools (send, delete, pay, submit, invite) never run straight from the model.
  They become a pending Confirmation that only you can approve, through the hub API
  with his token. No tool can approve a confirmation.
- Text from emails, web pages and files is wrapped as untrusted data before the model
  sees it, and anything risky requested after reading it is flagged on the confirm card.
- Every action is written to the audit log (what, when, why, outcome).
- "Max, stop" bumps the kill switch; running loops check it between steps.
- Work Maxie does on his own runs inside `background()`: apps open
  behind his windows, anything risky waits for his tap whatever his rules say, and the screen is asked for.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from .db import DB

RISKY_KINDS = {"send", "delete", "pay", "submit", "invite", "run"}
_BACKGROUND: ContextVar[bool] = ContextVar("maxie_background", default=False)


def in_background() -> bool:
    """True inside work Maxie started on his own; asyncio.to_thread carries it into tool threads."""
    return _BACKGROUND.get()


@contextmanager
def background() -> Iterator[None]:
    token = _BACKGROUND.set(True)
    try:
        yield
    finally:
        _BACKGROUND.reset(token)


def wrap_untrusted(source: str, text: str) -> str:
    """Mark external content as data. The system prompt tells the model to never follow it."""
    text = text.replace("</untrusted_data>", "</ untrusted_data>")  # can't close the wrapper early
    return f'<untrusted_data source="{source}">\n{text}\n</untrusted_data>'


class AuditLog:
    def __init__(self, db: DB, clock=time.time):
        self.db, self.clock = db, clock
        db.script("""CREATE TABLE IF NOT EXISTS audit(
            id INTEGER PRIMARY KEY, ts REAL, actor TEXT, action TEXT, args TEXT, why TEXT, outcome TEXT)""")

    def log(self, actor: str, action: str, args: dict | None = None, why: str = "", outcome: str = "") -> None:
        self.db.execute("INSERT INTO audit(ts,actor,action,args,why,outcome) VALUES (?,?,?,?,?,?)",
                        (self.clock(), actor, action, json.dumps(args or {}, sort_keys=True), why, outcome))

    def recent(self, limit: int = 100) -> list[dict]:
        rows = self.db.all("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["args"] = json.loads(r["args"])
        return rows


@dataclass
class Confirmation:
    id: str
    tool: str
    args: dict
    summary: str
    after_untrusted: bool
    status: str = "pending"  # pending | away (run when he steps away) | approved | denied | expired
    screen: bool = False  # it drives the real screen: "Can I use the screen for ~20 s?"


class Confirmations:
    TTL_SECONDS = 30 * 60
    AWAY_TTL_SECONDS = 24 * 3600  # a screen task waiting for him to step away

    def __init__(self, db: DB, clock=time.time):
        self.db, self.clock = db, clock
        db.script("""CREATE TABLE IF NOT EXISTS confirmations(
            id TEXT PRIMARY KEY, ts REAL, tool TEXT, args TEXT, summary TEXT, after_untrusted INT, status TEXT)""")
        db.add_columns("confirmations", {"screen": "INTEGER NOT NULL DEFAULT 0", "away_at": "REAL"})

    def request(self, tool: str, args: dict, summary: str, after_untrusted: bool, screen: bool = False,
                status: str = "pending") -> Confirmation:
        c = Confirmation(secrets.token_urlsafe(8), tool, args, summary, after_untrusted, status, screen)
        now = self.clock()
        self.db.execute("INSERT INTO confirmations(id,ts,tool,args,summary,after_untrusted,status,screen,away_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (c.id, now, tool, json.dumps(args), summary, int(after_untrusted), status, int(screen),
                         now if status == "away" else None))
        return c

    def get(self, cid: str) -> Confirmation | None:
        r = self.db.one("SELECT * FROM confirmations WHERE id=?", (cid,))
        if not r:
            return None
        status, now = r["status"], self.clock()
        stale = (status == "pending" and now - r["ts"] > self.TTL_SECONDS) or \
                (status == "away" and now - (r["away_at"] or r["ts"]) > self.AWAY_TTL_SECONDS)
        if stale:
            status = "expired"
            self.set_status(cid, status)
        return Confirmation(r["id"], r["tool"], json.loads(r["args"]), r["summary"], bool(r["after_untrusted"]), status,
                            bool(r["screen"]))

    def _with(self, status: str) -> list[Confirmation]:
        ids = [r["id"] for r in self.db.all("SELECT id FROM confirmations WHERE status=? ORDER BY ts", (status,))]
        return [c for c in (self.get(i) for i in ids) if c and c.status == status]

    def pending(self) -> list[Confirmation]:
        return self._with("pending")

    def waiting_away(self) -> list[Confirmation]:
        """Screen tasks he said to run when he steps away, oldest first."""
        return self._with("away")

    def set_status(self, cid: str, status: str) -> None:
        self.db.execute("UPDATE confirmations SET status=? WHERE id=?", (status, cid))

    def later(self, cid: str) -> Confirmation:
        """"When I'm away" on a screen card. Raises ValueError for anything else."""
        c = self.get(cid)
        if c is None or not c.screen or c.status not in ("pending", "away"):
            raise ValueError("only a waiting screen task can run later")
        self.db.execute("UPDATE confirmations SET status='away', away_at=? WHERE id=?", (self.clock(), cid))
        return self.get(cid)


class KillSwitch:
    """A generation counter. A task remembers the generation it started in; stop() bumps it."""

    def __init__(self):
        self._gen = 0
        self._lock = threading.Lock()
        self._running = threading.Event()  # cleared = paused between steps
        self._running.set()

    def generation(self) -> int:
        return self._gen

    def stop(self) -> None:
        with self._lock:
            self._gen += 1
        self._running.set()  # a paused task wakes up and sees it was stopped

    @property
    def paused(self) -> bool:
        return not self._running.is_set()

    def pause(self) -> None:
        self._running.clear()

    def resume(self) -> None:
        self._running.set()

    def stopped_since(self, gen: int) -> bool:
        return self._gen != gen
