"""How fast Maxie answers, measured on every turn and shown in Settings › Speed.

Three kinds, three targets:
  action  - rules, no model (open an app, a timer, the time): first word < 300 ms
  quick   - Haiku streaming (a fact, a chat line): first word < 700 ms
  heavy   - Sonnet / Opus (mail, research, multi-step): a spoken ack within 700 ms, then the answer streams
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .db import DB

TARGET_MS = {"action": 300, "quick": 700, "heavy": 700}
KINDS = ("action", "quick", "heavy")
APPLE_MODEL = "apple-on-device"  # Apple's on-device model through Maxie.app's bridge: $0, private


@dataclass(frozen=True)
class Turn:
    kind: str
    text: str
    route: str
    source: str
    model: str | None
    route_ms: float
    first_ms: float | None
    total_ms: float
    cost: float
    model_first_ms: float | None = None  # the model's own first word (not the spoken ack): GOAL5 Phase 5 §14
    cache: str = ""  # "warm": the turn's first call read the cached prefix; "cold": it had to write it


def kind_of(direct: bool, model: str | None) -> str:
    if model == APPLE_MODEL:
        return "quick"
    if direct or not model:
        return "action"
    return "quick" if "haiku" in model else "heavy"


class Latency:
    def __init__(self, db: DB, timezone: str = "America/Chicago", clock=time.time):
        self.db, self.tz, self.clock = db, ZoneInfo(timezone), clock
        db.script("""CREATE TABLE IF NOT EXISTS turns(
            id INTEGER PRIMARY KEY, ts REAL, kind TEXT, text TEXT, route TEXT, source TEXT, model TEXT,
            route_ms REAL, first_ms REAL, total_ms REAL, cost REAL)""")
        db.add_columns("turns", {"model_first_ms": "REAL", "cache": "TEXT"})

    def record(self, t: Turn) -> None:
        self.db.execute("INSERT INTO turns(ts,kind,text,route,source,model,route_ms,first_ms,total_ms,cost,"
                        "model_first_ms,cache) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (self.clock(), t.kind, t.text[:120], t.route, t.source, t.model, round(t.route_ms, 1),
                         None if t.first_ms is None else round(t.first_ms, 1), round(t.total_ms, 1), t.cost,
                         None if t.model_first_ms is None else round(t.model_first_ms, 1), t.cache))

    def summary(self, days: int = 1) -> dict:
        """Per kind: n, median / p95 first word, median total, how many met the target. `days`=1 is today."""
        rows = self.db.all("SELECT kind, first_ms, total_ms FROM turns WHERE ts >= ?", (self._start(days),))
        out = {}
        for kind in KINDS:
            firsts = [r["first_ms"] for r in rows if r["kind"] == kind and r["first_ms"] is not None]
            totals = [r["total_ms"] for r in rows if r["kind"] == kind]
            target = TARGET_MS[kind]
            out[kind] = {"n": len(totals), "target_ms": target,
                         "first_p50": _p(firsts, 50), "first_p95": _p(firsts, 95), "total_p50": _p(totals, 50),
                         "met": sum(1 for f in firsts if f <= target), "ok": bool(firsts) and _p(firsts, 50) <= target}
        return out

    def share(self, days: int = 7) -> dict:
        """Who answered: Apple's on-device model (free), this Mac with no model (free), or Claude (paid, or
        his plan through "Ask my Claude"). Shown in Settings › Speed."""
        start = self._start(days)
        rows = self.db.all("SELECT ts, model FROM turns WHERE ts >= ?", (start,))
        total = _who(rows)
        free_pct = round(100 * (total["apple"] + total["local"]) / len(rows)) if rows else 0
        first = datetime.fromtimestamp(start, self.tz).date()
        daily = []
        for i in range(days):
            day = first + timedelta(days=i)
            daily.append({"day": day.strftime("%a"),
                          **_who([r for r in rows if datetime.fromtimestamp(r["ts"], self.tz).date() == day])})
        return {**total, "free_pct": free_pct, "days": days, "daily": daily}

    def _start(self, days: int) -> float:
        now = datetime.fromtimestamp(self.clock(), self.tz)
        return (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def recent(self, limit: int = 20) -> list[dict]:
        return self.db.all("SELECT * FROM turns ORDER BY id DESC LIMIT ?", (limit,))

    def line(self) -> str:
        """One line for the panel: "first word: actions 24 ms · quick 610 ms · heavy ack 540 ms (today)"."""
        s = self.summary()
        parts = [f"{k if k != 'heavy' else 'heavy ack'} {s[k]['first_p50']:.0f} ms" for k in KINDS if s[k]["n"]]
        return ("first word: " + " · ".join(parts) + " (today)") if parts else "no turns yet today"


def _who(rows: list[dict]) -> dict:
    apple = sum(1 for r in rows if r["model"] == APPLE_MODEL)
    local = sum(1 for r in rows if not r["model"])
    return {"apple": apple, "local": local, "claude": len(rows) - apple - local}


def _p(values: list[float], pct: int) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    qs = statistics.quantiles(values, n=100, method="inclusive")
    return float(qs[min(max(pct, 1), 99) - 1])
