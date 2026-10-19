"""Watch and learn (Mac only). Off until you turn it on in Settings; private by design.

The notch app sends app switches, window titles and who he is messaging (never what), a minute at a time. Each
one is checked again here before it's kept: no banking, health or password apps, no browser titles (a private
window can't be told apart), nothing that looks like a code, a card or a login, nothing from apps he excludes.
Everything stays on this Mac for 30 days, and "Forget today" drops today's. Once a day the kept app switches
become habits (maxie.habits) in Memory, and now and then one becomes a question: "You open jobhunt and
Gmail every weekday morning at 9: want me to do it?" A yes makes it a routine; the apps open in the background
at that time, only while he's up.

"Stop watching", "close your eyes", or a plain "no" to a suggestion close Maxie's eyes: nothing is kept until
"watch again". While his eyes are closed, the mascot shows it everywhere (Events base state "closed").
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from . import habits as hb
from .memory import forbidden_reason

KEEP_DAYS = 30
KINDS = ("app", "title", "message")
MAX_TEXT = 120
ROUTINE_LATE_MIN = 30  # a routine still runs if the Mac wakes up to this late
SUGGEST_FROM_H, SUGGEST_UNTIL_H = 10, 20
ASKED_FOR_S = 6 * 3600  # how long a "no" still answers the last suggestion
SOURCE = "watched"

NEVER_APPS = re.compile(
    r"(?:^com\.apple\.(?:Passwords|keychainaccess|Health)\b|1password|agilebits|bitwarden|lastpass|dashlane|keeper"
    r"|\bchase\b|bankofamerica|wellsfargo|capitalone|americanexpress|\bamex\b|schwab|fidelity|vanguard|robinhood"
    r"|coinbase|venmo|paypal|\bzelle\b|\bmint\b|creditkarma|\bbank|banking|\bhealth|mychart|medical|pharmacy)",
    re.I)
NEVER_CATEGORIES = ("public.app-category.finance", "public.app-category.healthcare-fitness", "public.app-category.medical")
BROWSERS = re.compile(r"^(?:com\.apple\.Safari|com\.google\.Chrome(?:\.beta|\.canary)?|org\.mozilla\.\w+"
                      r"|company\.thebrowser\.\w+|com\.microsoft\.edgemac\w*|com\.brave\.Browser\w*"
                      r"|com\.operasoftware\.\w+|com\.vivaldi\.\w+|com\.kagi\.\w+)$")
SENSITIVE_TITLE = re.compile(
    r"private (?:browsing|window)|incognito|inprivate|password|passcode|verification|one-time|\botp\b"
    r"|sign[ -]?in|log[ -]?in|\bbank|credit card|card ending|\bchase\b|paypal|venmo|mychart|patient|pharmacy"
    r"|prescription|therapy|\bhealth|medical|\d{4,}", re.I)

STOP = re.compile(r"^\W*(?:(?:hey\s+)?max(?:ie)?\W+)?(?:please\s+)?(?:stop (?:watching|looking|learning)"
                  r"|don'?t watch(?: me)?|close your eyes|no more watching)\b", re.I)
START = re.compile(r"^\W*(?:(?:hey\s+)?max(?:ie)?\W+)?(?:(?:you can\s+)?(?:watch|look|learn) again"
                   r"|you can watch(?: now)?|open your eyes|start watching)\b", re.I)
NO = re.compile(r"^\W*(?:no|nope|nah|no thanks|no thank you|not now)\W*$", re.I)
YES = re.compile(r"^\W*(?:yes|yeah|yep|sure|ok(?:ay)?|do it|yes please|please do)\W*$", re.I)


def command(text: str) -> str | None:
    """'stop' or 'start' for what he said about watching; None for anything else (a plain "no" included)."""
    if STOP.search(text or ""):
        return "stop"
    if START.search(text or ""):
        return "start"
    return None


@dataclass(frozen=True)
class Observation:
    kind: str
    app: str
    name: str
    detail: str


def admit(item: dict, excluded: list[str]) -> Observation | None:
    """The one gate: what may be kept from one item the Mac sent, cleaned, or None."""
    kind, app = str(item.get("kind", "")), str(item.get("app", "")).strip()[:MAX_TEXT]
    name = " ".join(str(item.get("name", "")).split())[:MAX_TEXT]
    detail = " ".join(str(item.get("detail", "")).split())[:MAX_TEXT]
    if kind not in KINDS or not app:
        return None
    lowered = {e.strip().lower() for e in excluded}
    if app.lower() in lowered or name.lower() in lowered:
        return None
    if NEVER_APPS.search(app) or NEVER_APPS.search(name) or item.get("category") in NEVER_CATEGORIES:
        return None
    if BROWSERS.match(app):
        if kind != "app":
            return None  # that he used a browser is fine; what he looked at is not
        detail = ""
    if detail and (SENSITIVE_TITLE.search(detail) or forbidden_reason(detail)):
        return None
    return Observation(kind, app, name, detail)


class Watch:
    def __init__(self, db, settings, memory, events, asleep: Callable[[], bool], clock: Callable[[], float] = time.time,
                 recording: Callable[[], bool] = lambda: False):
        self.db, self.settings, self.memory, self.events = db, settings, memory, events
        self.asleep, self.clock = asleep, clock
        self.recording = recording  # "watch me once" is on (maxie.flows): "stop watching" is about that
        self._learned_on = ""
        db.script("""CREATE TABLE IF NOT EXISTS watch_obs(ts REAL NOT NULL, kind TEXT, app TEXT, name TEXT, detail TEXT);
                     CREATE INDEX IF NOT EXISTS watch_obs_ts ON watch_obs(ts);
                     CREATE TABLE IF NOT EXISTS watch_suggestions(id INTEGER PRIMARY KEY, key TEXT UNIQUE, text TEXT,
                         habit TEXT, status TEXT, created REAL);
                     CREATE TABLE IF NOT EXISTS watch_routines(id INTEGER PRIMARY KEY, key TEXT UNIQUE, apps TEXT,
                         names TEXT, minute INTEGER, weekdays INTEGER, last_run TEXT)""")
        events.eyes_closed = lambda: not self.watching

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.settings.timezone)

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), self.tz)

    # ---- eyes

    @property
    def watching(self) -> bool:
        return bool(self.settings.watching)

    def set_watching(self, on: bool) -> None:
        self.settings.update({"watching": bool(on)}).save()
        self.events.rest()  # eyes open or closed, everywhere at once

    def answer(self, text: str) -> str | None:
        """His words about watching, or a yes / no to the last suggestion; None when it's not about that."""
        cmd = command(text)
        if cmd == "stop" and self.recording():
            return None  # the flow recorder's "stop watching" (router.rules WATCH_OFF)
        if cmd == "stop":
            self.set_watching(False)
            return "Okay, eyes closed. I'm not watching. Say \"watch again\" when you want me to learn."
        if cmd == "start":
            self.set_watching(True)
            return "Eyes open. I'll learn from what you do, on this Mac only, and forget it after 30 days."
        asked = self._asked()
        if asked and NO.match(text or ""):
            self._set_status(asked["id"], "dismissed")
            self.set_watching(False)
            return "Okay, I won't. Eyes closed too: say \"watch again\" any time."
        if asked and YES.match(text or ""):
            return self.accept(asked["id"])
        return None

    # ---- what comes in

    def observe(self, items: list[dict]) -> int:
        if not self.watching:
            return 0
        now = self.clock()
        kept = [o for i in items if (o := admit(i, list(self.settings.watch_excluded))) is not None]
        for o in kept:
            self.db.execute("INSERT INTO watch_obs VALUES (?,?,?,?,?)", (now, o.kind, o.app, o.name, o.detail))
        self.db.execute("DELETE FROM watch_obs WHERE ts < ?", (now - KEEP_DAYS * 86400,))
        return len(kept)

    def count(self, since: float = 0.0) -> int:
        return self.db.one("SELECT COUNT(*) AS n FROM watch_obs WHERE ts >= ?", (since,))["n"]

    def _today_start(self) -> float:
        return self.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def today(self) -> int:
        return self.count(self._today_start())

    def forget_today(self) -> int:
        n = self.today()
        self.db.execute("DELETE FROM watch_obs WHERE ts >= ?", (self._today_start(),))
        self.learn()
        return n

    # ---- habits, suggestions, routines

    def learn(self) -> list[hb.Habit]:
        """Habits from the last 30 days into Memory ("habits", source "watched"); stale ones leave it."""
        since = self.clock() - KEEP_DAYS * 86400
        rows = self.db.all("SELECT ts, app, name FROM watch_obs WHERE kind='app' AND ts >= ?", (since,))
        found = hb.mine(rows, self.tz)
        texts = {h.text for h in found}
        for f in self.memory.all():
            if f.get("source") == SOURCE and f["text"] not in texts:
                self.memory.delete(f["id"])
        for h in found:
            self.memory.remember(h.text, "habits", source=SOURCE)
        self._learned_on = self.now().strftime("%Y-%m-%d")
        return found

    def _asked(self) -> dict | None:
        return self.db.one("SELECT * FROM watch_suggestions WHERE status='asked' AND created >= ? ORDER BY id DESC LIMIT 1",
                           (self.clock() - ASKED_FOR_S,))

    def _set_status(self, sid: int, status: str) -> None:
        self.db.execute("UPDATE watch_suggestions SET status=? WHERE id=?", (status, sid))

    def suggest(self) -> dict | None:
        """The next habit worth asking about, once ever, one question at a time."""
        if self.db.one("SELECT id FROM watch_suggestions WHERE status='asked'"):
            return None
        rows = self.db.all("SELECT ts, app, name FROM watch_obs WHERE kind='app'")
        for h in hb.mine(rows, self.tz):
            if self.db.one("SELECT id FROM watch_suggestions WHERE key=?", (h.key,)):
                continue
            text = h.text + ": want me to do it?"
            habit = {"apps": h.apps, "names": h.names, "minute": h.minute, "weekdays": h.weekdays_only}
            cur = self.db.execute("INSERT INTO watch_suggestions(key,text,habit,status,created) VALUES (?,?,?,?,?)",
                                  (h.key, text, json.dumps(habit), "asked", self.clock()))
            return {"id": cur.lastrowid, "text": text}
        return None

    def accept(self, sid: int) -> str | None:
        row = self.db.one("SELECT * FROM watch_suggestions WHERE id=?", (sid,))
        if row is None:
            return None
        h = json.loads(row["habit"])
        self.db.execute("INSERT OR IGNORE INTO watch_routines(key,apps,names,minute,weekdays,last_run) VALUES (?,?,?,?,?,?)",
                        (row["key"], json.dumps(h["apps"]), json.dumps(h["names"]), h["minute"], int(h["weekdays"]), ""))
        self._set_status(sid, "accepted")
        return "Done. " + self._routine_line(h["names"], h["minute"], bool(h["weekdays"]))

    def dismiss(self, sid: int) -> bool:
        if self.db.one("SELECT id FROM watch_suggestions WHERE id=?", (sid,)) is None:
            return False
        self._set_status(sid, "dismissed")
        return True

    @staticmethod
    def _routine_line(names: list[str], minute: int, weekdays: bool) -> str:
        return hb.Habit(tuple(names), tuple(names), minute, weekdays, 0).routine

    def routines(self) -> list[str]:
        return [self._routine_line(json.loads(r["names"]), r["minute"], bool(r["weekdays"]))
                for r in self.db.all("SELECT * FROM watch_routines ORDER BY minute")]

    def schedule(self) -> list[dict]:
        """His routines for the Scheduled list: {"when": "Weekdays at 9:00", "what": "Open jobhunt on weekdays at 9:00."}."""
        return [{"when": f"{'Weekdays' if r['weekdays'] else 'Every day'} at {r['minute'] // 60}:{r['minute'] % 60:02d}",
                 "what": self._routine_line(json.loads(r["names"]), r["minute"], bool(r["weekdays"]))}
                for r in self.db.all("SELECT * FROM watch_routines ORDER BY minute")]

    def habits(self) -> list[str]:
        return [f["text"] for f in self.memory.all() if f.get("source") == SOURCE]

    def pending(self) -> dict | None:
        row = self._asked()
        return {"id": row["id"], "text": row["text"]} if row else None

    # ---- every minute

    def tick(self) -> None:
        now = self.now()
        if self._learned_on != now.strftime("%Y-%m-%d"):
            self.learn()
        if self.asleep():
            return
        self._run_routines(now)
        if self.watching and SUGGEST_FROM_H <= now.hour < SUGGEST_UNTIL_H and not self._asked_today(now):
            if sug := self.suggest():
                self.events.transcript("assistant", sug["text"], final=True)
                self.events.publish({"type": "suggestion", "sid": sug["id"], "text": sug["text"]})

    def _asked_today(self, now: datetime) -> bool:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        return self.db.one("SELECT id FROM watch_suggestions WHERE created >= ?", (start,)) is not None

    def _run_routines(self, now: datetime) -> None:
        today, minute = now.strftime("%Y-%m-%d"), now.hour * 60 + now.minute
        for r in self.db.all("SELECT * FROM watch_routines"):
            due = r["minute"] <= minute < r["minute"] + ROUTINE_LATE_MIN
            if not due or r["last_run"] == today or (r["weekdays"] and now.weekday() >= 5):
                continue
            self.db.execute("UPDATE watch_routines SET last_run=? WHERE id=?", (today, r["id"]))
            self.events.publish({"type": "open_apps", "apps": json.loads(r["apps"])})
