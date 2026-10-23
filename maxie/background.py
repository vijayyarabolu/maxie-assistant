"""The Mac is Maxie's computer: tasks run in the background without taking his screen.

- "In the background, find 5 data engineer jobs in Austin" (or "... when you get a chance") queues the task and
  answers at once. One worker runs queued tasks one at a time, inside `safety.background()`: apps open behind
  his windows (`open -g`), AppleScript never activates an app, anything that sends, buys or deletes waits for
  his tap whatever his rules say, and the panel and the mascot are left alone. The steps land in Activity;
  the answer arrives as a notification (held while he sleeps).
- A task that truly needs the real screen (a replayed flow) asks "Can I use the screen for ~20 s?". He can say
  yes, no, or "When I'm away": it then runs once he has left an unlocked Mac for 10 minutes with nothing
  playing (`Presence.away`). Settings › Rules › "Use the screen when I'm away" makes that the default.
- `scheduled()` is what Maxie does on his own, for Activity › Scheduled: every item reads only, except his
  routines (they open apps, in the background).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from datetime import datetime

from .db import DB
from .safety import background
from .tasks import Tasks

ACK = "On it, in the background. I'll tell you when it's done."
KEEP = 200  # rows kept in the queue table
IDLE_S = 3.0  # the worker looks for work this often
_MAX = r"(?:(?:hey\s+)?max(?:ie)?[,!]?\s+)?"
_LEAD = re.compile(_MAX + r"(?:in the background|when you (?:get a chance|can)|in the meantime)\s*[,:]?\s+(?P<task>.+)$",
                   re.I)
_TAIL = re.compile(_MAX + r"(?P<task>.+?),?\s+in the background$", re.I)
TITLES = {"done": "Done in the background", "waiting": "Needs your OK", "error": "Couldn't finish"}


def wants(text: str) -> str | None:
    """The task, when he asked for it to happen in the background; else None."""
    t = (text or "").strip().rstrip(".!")
    m = _LEAD.match(t) or _TAIL.match(t)
    task = m["task"].strip(" ,:") if m else ""
    return task if len(task) >= 3 else None


def _clock_line(hhmm: str) -> str:
    return datetime.strptime(hhmm, "%H:%M").strftime("%-I:%M %p")


class Background:
    def __init__(self, db: DB, events=None, notify: Callable[[str, str], None] | None = None, registry=None,
                 presence=None, settings=None, watch=None, audit=None, clock=time.time):
        self.db, self.events, self.notify, self.clock = db, events, notify, clock
        self.registry, self.presence, self.settings, self.watch, self.audit = registry, presence, settings, watch, audit
        db.script("""CREATE TABLE IF NOT EXISTS background_queue(
            id INTEGER PRIMARY KEY, ts REAL, text TEXT, source TEXT, status TEXT, reply TEXT, cost REAL,
            started REAL, finished REAL, task_id INTEGER)""")
        db.execute("UPDATE background_queue SET status='stopped', reply='The hub restarted while this ran.' "
                   "WHERE status='running'")

    # ---- the queue

    def add(self, text: str, source: str = "you") -> dict:
        cur = self.db.execute("INSERT INTO background_queue(ts,text,source,status,reply,cost) VALUES (?,?,?,?,?,?)",
                              (self.clock(), text.strip()[:500], source, "queued", "", 0.0))
        self._trim()
        return self._changed(cur.lastrowid)

    def _trim(self) -> None:
        """Keep the newest KEEP rows of the app's own queue table."""
        self.db.execute("DELETE FROM background_queue WHERE id <= (SELECT MAX(id) FROM background_queue) - ?", (KEEP,))

    def get(self, job_id: int) -> dict | None:
        return self.db.one("SELECT * FROM background_queue WHERE id=?", (job_id,))

    def recent(self, limit: int = 30) -> list[dict]:
        return self.db.all("SELECT * FROM background_queue ORDER BY id DESC LIMIT ?", (limit,))

    def cancel(self, job_id: int) -> bool:
        """Only a task that hasn't started; a running one stops with "Max, stop"."""
        cur = self.db.execute("UPDATE background_queue SET status='cancelled', finished=? WHERE id=? AND status='queued'",
                              (self.clock(), job_id))
        if cur.rowcount:
            self._changed(job_id)
        return cur.rowcount > 0

    def _next(self) -> dict | None:
        return self.db.one("SELECT * FROM background_queue WHERE status='queued' ORDER BY id LIMIT 1")

    def _set(self, job_id: int, **cols) -> dict:
        names = ", ".join(f"{k}=?" for k in cols)
        self.db.execute(f"UPDATE background_queue SET {names} WHERE id=?", (*cols.values(), job_id))
        return self._changed(job_id)

    def _changed(self, job_id: int) -> dict:
        job = self.get(job_id)
        if self.events is not None and job is not None:
            self.events.publish({"type": "background", **job})
        return job

    async def run_next(self, agent) -> dict | None:
        """Run the oldest queued task to its end, quietly. None when nothing is queued."""
        job = self._next()
        if job is None:
            return None
        self._set(job["id"], status="running", started=self.clock())
        tasks = Tasks(self.db, self.events, clock=self.clock)
        reply, cost, waiting, failed = "", 0.0, False, False
        try:
            with background():
                async for ev in agent.run_background(job["text"], tasks):
                    if ev["type"] == "confirm":
                        waiting = True
                        if self.events is not None:
                            self.events.publish(ev)  # the card shows in the panel and on the phone at once
                    elif ev["type"] == "done":
                        reply, cost = ev.get("text", ""), float(ev.get("cost") or 0.0)
        except Exception as e:  # one bad task must not stop the worker  # noqa: BLE001
            failed = True
            reply = "It went wrong part way; nothing was sent."
            if self.audit is not None:
                self.audit.log("maxie", "background_error", {"id": job["id"]}, job["text"][:200], str(e)[:200])
        status = "error" if failed else "waiting" if waiting else "done"
        done = self._set(job["id"], status=status, reply=reply[:2000], cost=round(cost, 6), finished=self.clock(),
                         task_id=tasks.last_id)
        if self.notify is not None:
            self.notify(TITLES[status], f"{done['text']}: {done['reply']}"[:400])
        return done

    # ---- the screen

    def screen_tick(self) -> int:
        """Run one screen task he said to do when he's away, if he is away now. Returns how many ran."""
        if self.registry is None or self.presence is None or not self.presence.away():
            return 0
        for c in self.registry.confirmations.waiting_away():
            r = self.registry.resolve(c.id, True)
            if self.audit is not None:
                self.audit.log("maxie", "screen_while_away", {"id": c.id}, c.summary, "error" if r.is_error else "ok")
            return 1
        return 0

    # ---- what runs on its own

    def scheduled(self) -> list[dict]:
        st = self.settings
        rows = [
            ("Morning brief", f"Every day at {_clock_line(st.morning_brief_at)}", "Your day: calendar, mail and jobs."),
            ("Evening wrap", f"Every day at {_clock_line(st.evening_wrap_at)}", "What got done, and tomorrow's first thing."),
            ("Job inbox", "Every 5 minutes", "Interview, recruiter and rejection mail, the minute it lands."),
            ("Meeting prep", "30 min before each meeting", "Who's in it, and what to lead with."),
            ("Applications", "Every minute", "Counts today's applications in jobhunt; a win at your goal."),
        ]
        if st.watching:
            rows.append(("Learn habits", "Once a day", "From what Maxie saw while watching; habits go to Memory."))
        rows.append(("Keep Haiku fast", "Every 55 min while you're awake", "Keeps quick answers fast, about a cent a day."))
        if st.warm_sonnet:
            rows.append(("Keep Sonnet fast", "Every 55 min while you're awake",
                         "Keeps bigger answers fast; an experiment measures if it pays."))
        out = [{"name": n, "when": w, "what": what, "reads_only": True} for n, w, what in rows]
        routines = self.watch.schedule() if self.watch is not None else []
        return out + [{"name": "Routine", **r, "reads_only": False} for r in routines]


async def work(bg: Background, agent, on_error: Callable[[str], None] = print,
               paused: Callable[[], bool] = lambda: False) -> None:
    """The hub's worker: a screen task when he's away, else the next queued task, else a short nap. Nothing while
    he has Maxie paused: the queue waits."""
    while True:
        if paused():
            await asyncio.sleep(IDLE_S)
            continue
        try:
            await asyncio.to_thread(bg.screen_tick)
            if await bg.run_next(agent) is not None:
                continue
        except Exception as e:  # the worker never dies  # noqa: BLE001
            on_error(f"background worker: {e}")
        await asyncio.sleep(IDLE_S)
