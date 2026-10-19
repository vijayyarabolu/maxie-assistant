"""Should Maxie warm Sonnet's cache too?

Haiku's cached prefix is rewritten every 55 minutes while he's awake (hub `keep_fast`). Whether doing the same for
Sonnet pays was a guess; this measures it. The run starts on its own: 7 days with `warm_sonnet` off, then 7 days
on, then it keeps the winner and says so in one line (a notification, Settings › Speed, `maxie
warm-report`).

Per waking day (any turn that day): Sonnet spend (chat + warm-ups, from `usage`) and the time to the model's
own first word on *first turns* (a Sonnet turn with no Sonnet turn in the hour before: the turns warming is for),
from `turns`. A day counts as "warm" when Sonnet really was warmed that day, so a flip by hand can't skew it.
The rule: the cheaper week wins; within 10 % on cost, the faster one wins; with fewer than 3 waking days on
either side, it stays off.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from .db import DB

NAME = "warm_sonnet"
WEEK_DAYS = 7
CLOSE = 0.10  # costs this close are a tie: speed decides
MIN_DAYS = 3  # waking days needed on each side
FIRST_GAP_S = 3600  # a Sonnet turn after an hour without one is a first turn (the prefix would have expired)
SONNET = "%sonnet%"


def models_to_warm(settings, models) -> list[str]:
    """What `keep_fast` warms every 55 minutes."""
    return [models.active("haiku")] + ([models.active("sonnet")] if settings.warm_sonnet else [])


def _p50(values: list[float]) -> float | None:
    return round(median(values)) if values else None


class WarmExperiment:
    def __init__(self, db: DB, settings, notify: Callable[[str], None] | None = None, audit=None,
                 clock: Callable[[], float] = time.time):
        self.db, self.settings, self.notify, self.audit, self.clock = db, settings, notify, audit, clock
        db.script("""CREATE TABLE IF NOT EXISTS experiments(
            name TEXT PRIMARY KEY, start REAL NOT NULL, phase TEXT NOT NULL, verdict TEXT NOT NULL DEFAULT '')""")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.settings.timezone)

    def _row(self) -> dict | None:
        return self.db.one("SELECT * FROM experiments WHERE name=?", (NAME,))

    def _flag(self, on: bool) -> None:
        self.settings.warm_sonnet = on
        self.settings.save()

    # ---- the schedule (every minute, from the proactive tick)

    def tick(self) -> None:
        row, now = self._row(), self.clock()
        if row is None:
            self.db.execute("INSERT INTO experiments(name,start,phase) VALUES (?,?,?)", (NAME, now, "off"))
            self._flag(False)
            self._log("started: 7 days without warming Sonnet")
            return
        if row["phase"] == "off" and now >= self._start_day(row) + timedelta(days=WEEK_DAYS).total_seconds():
            self.db.execute("UPDATE experiments SET phase='on' WHERE name=?", (NAME,))
            self._flag(True)
            self._log("week two: warming Sonnet every 55 min")
        elif row["phase"] == "on" and now >= self._start_day(row) + timedelta(days=2 * WEEK_DAYS).total_seconds():
            v = self.verdict()
            self.db.execute("UPDATE experiments SET phase='done', verdict=? WHERE name=?", (v["line"], NAME))
            self._flag(v["keep_on"])
            self._log(v["line"])
            if self.notify is not None:
                self.notify(v["line"])

    def _start_day(self, row: dict) -> float:
        """Midnight of the day it started: the weeks are whole days."""
        start = datetime.fromtimestamp(row["start"], self.tz)
        return start.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def _log(self, what: str) -> None:
        if self.audit is not None:
            self.audit.log("hub", "warm_experiment", {}, "GOAL5 §14", what)

    # ---- the numbers

    def status(self) -> dict:
        row = self._row()
        if row is None:
            return {"phase": "not started", "day": 0, "of": 2 * WEEK_DAYS, "verdict": "", "warm_sonnet": self.settings.warm_sonnet}
        n = int((self.clock() - self._start_day(row)) // 86400) + 1
        return {"phase": row["phase"], "day": min(n, 2 * WEEK_DAYS), "of": 2 * WEEK_DAYS, "start": row["start"],
                "verdict": row["verdict"], "warm_sonnet": self.settings.warm_sonnet}

    def days(self) -> list[dict]:
        """Every waking day since the start, oldest first."""
        row = self._row()
        if row is None:
            return []
        since, end = self._start_day(row), self._start_day(row) + 2 * WEEK_DAYS * 86400
        turns = self.db.all("SELECT ts, model, model_first_ms FROM turns WHERE ts >= ? AND ts < ? ORDER BY ts",
                            (since - FIRST_GAP_S, end))
        usage = self.db.all("SELECT ts, purpose, cost FROM usage WHERE ts >= ? AND ts < ? AND model LIKE ?",
                            (since, end, SONNET))
        firsts, last_sonnet = [], float("-inf")
        for t in turns:
            if "sonnet" not in (t["model"] or ""):
                continue
            if t["ts"] - last_sonnet > FIRST_GAP_S and t["ts"] >= since:
                firsts.append(t)
            last_sonnet = t["ts"]
        out = []
        for d in sorted({self._date(t["ts"]) for t in turns if t["ts"] >= since}):
            spend = [u for u in usage if self._date(u["ts"]) == d]
            warm_usd = sum(u["cost"] for u in spend if u["purpose"] == "warm")
            first_ms = [t["model_first_ms"] for t in firsts if self._date(t["ts"]) == d and t["model_first_ms"] is not None]
            out.append({"day": d.strftime("%a %b %-d"), "date": d.isoformat(),
                        "warm": any(u["purpose"] == "warm" for u in spend),
                        "sonnet_usd": round(sum(u["cost"] for u in spend), 4), "warm_usd": round(warm_usd, 4),
                        "sonnet_turns": sum(1 for t in turns if "sonnet" in (t["model"] or "") and self._date(t["ts"]) == d),
                        "first_turns": len(first_ms), "first_p50": _p50(first_ms), "first_ms": first_ms})
        return out

    def _date(self, ts: float):
        return datetime.fromtimestamp(ts, self.tz).date()

    @staticmethod
    def _side(days: list[dict]) -> dict:
        firsts = [ms for d in days for ms in d["first_ms"]]
        usd = sum(d["sonnet_usd"] for d in days)
        return {"days": len(days), "usd_per_day": round(usd / len(days), 4) if days else 0.0,
                "first_p50": _p50(firsts), "first_turns": len(firsts)}

    def verdict(self) -> dict:
        days = self.days()
        off, on = self._side([d for d in days if not d["warm"]]), self._side([d for d in days if d["warm"]])
        if off["days"] < MIN_DAYS or on["days"] < MIN_DAYS:
            line = (f"Not enough use to tell ({off['days']} cold and {on['days']} warm waking days); "
                    "Sonnet stays cold, as before.")
            return {"off": off, "on": on, "keep_on": False, "line": line}
        a, b = off["usd_per_day"], on["usd_per_day"]
        tie = abs(a - b) <= CLOSE * max(a, b, 1e-9)
        if tie and off["first_p50"] is not None and on["first_p50"] is not None:
            keep_on = on["first_p50"] < off["first_p50"]
        else:
            keep_on = b < a
        why = "about the same cost, so the faster one" if tie else "the cheaper one"
        speed = (f", first word {off['first_p50']} vs {on['first_p50']} ms"
                 if off["first_p50"] is not None and on["first_p50"] is not None else "")
        line = (f"Keep Sonnet {'warm' if keep_on else 'cold'}: ${a:.2f} a day cold vs ${b:.2f} warm{speed} "
                f"({why}).")
        return {"off": off, "on": on, "keep_on": keep_on, "line": line}

    def markdown(self) -> str:
        """The table and the verdict for PROGRESS5."""
        st, v = self.status(), self.verdict()
        lines = [f"Warm Sonnet experiment: {st['phase']}, day {st['day']} of {st['of']}.", "",
                 "| Day | Warm | Sonnet $ | of it warm-up $ | Sonnet turns | First turns | First word p50 |",
                 "|---|---|---|---|---|---|---|"]
        for d in self.days():
            p50 = f"{d['first_p50']} ms" if d["first_p50"] is not None else "-"
            lines.append(f"| {d['day']} | {'yes' if d['warm'] else 'no'} | {d['sonnet_usd']:.3f} | {d['warm_usd']:.3f} "
                         f"| {d['sonnet_turns']} | {d['first_turns']} | {p50} |")
        return "\n".join([*lines, "", st["verdict"] or v["line"]])
