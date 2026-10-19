"""Maxie sleeps when HE sleeps, not by the clock.

One answer, `asleep()`, from real signals, cheapest first:
- What he says: "goodnight Max", "I'm going to sleep" -> asleep now. Typing, the wake word, the phone -> awake.
- What the notch app reports (POST /api/presence, every minute and on lock / display changes): screen locked,
  displays asleep, seconds since the last keyboard or mouse input, media playing.
- Asleep = said goodnight, OR (locked / displays asleep / no input for 30 min) AND no media playing AND it is his
  night (9 PM - 10 AM). Idle in the daytime is "away" and stays idle. A movie with no input is awake.
- No fresh report (the app isn't running): the old fixed quiet hours. Settings › Maxie can keep those for good.
Every sleep and wake is logged locally (SQLite), so Phase 4 can learn his real bedtime.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from zoneinfo import ZoneInfo

CLOSE_EYES_S = 4.0  # falling asleep: eyes close slowly, then sleep
STRETCH_S = 6.0  # waking: one stretch, then idle

IDLE_ASLEEP_S = 30 * 60
STALE_S = 10 * 60  # a report older than this says nothing about now
AWAY_IDLE_S = 10 * 60  # a screen task may run once he has left the Mac this long
GOODNIGHT_GRACE_S = 2 * 60  # after "goodnight Max", closing the lid or a last tap doesn't wake him
NIGHT_FROM_H, NIGHT_UNTIL_H = 21, 10  # until his own nights are known
LEARN_NIGHTS, LEARN_DAYS = 5, 14  # GOAL5 11b: his real night, from the last two weeks of the log
MARGIN_MIN = 120  # his night: two hours either side of his usual sleep and wake
NOON = 12 * 60
GOODNIGHT = re.compile(
    r"^\s*(?:good\s*night|g'?night|gn|night\s+night|nighty\s+night)\b"
    r"|\b(?:i'?m|i\s+am)\s+(?:going|off|heading)\s+to\s+(?:sleep|bed)\b"
    r"|\bgoing\s+to\s+(?:sleep|bed)\s+now\b",
    re.I)


def goodnight_said(text: str) -> bool:
    return bool(GOODNIGHT.search(text or ""))


@dataclass(frozen=True)
class Signals:
    locked: bool
    displays_asleep: bool
    idle_s: float
    media: bool
    at: float  # when the app reported them


class Presence:
    def __init__(self, db, settings, fallback_quiet: Callable[[], bool], clock: Callable[[], float] = time.time):
        self.db, self.settings, self.fallback_quiet, self.clock = db, settings, fallback_quiet, clock
        # (asleep, was) on every change, was None the first time; set by the hub: mascot, held pushes
        self.on_change: Callable[[bool, bool | None], None] = lambda asleep, was: None
        self._signals: Signals | None = None
        self._goodnight_at: float | None = None
        self._active_at: float = float("-inf")  # last thing he did that only a waking person does
        self._last: bool | None = None
        self._real = False  # the last answer came from him (signals, words), not the clock
        db.script("CREATE TABLE IF NOT EXISTS presence_log(ts REAL NOT NULL, kind TEXT NOT NULL)")

    # ---- what comes in

    def report(self, locked: bool, displays_asleep: bool, idle_s: float, media: bool) -> bool:
        now = self.clock()
        self._signals = Signals(bool(locked), bool(displays_asleep), max(0.0, float(idle_s)), bool(media), now)
        typed_since = self._goodnight_at is not None and now - idle_s > self._goodnight_at + GOODNIGHT_GRACE_S
        if typed_since and not locked and not displays_asleep:
            self._goodnight_at = None  # at the keyboard again, well after goodnight: he's up
        return self.refresh()

    def activity(self, source: str = "") -> bool:
        """Typing, the wake word, a tap, the phone: he is awake."""
        self._active_at = self.clock()
        self._goodnight_at = None
        return self.refresh()

    def heard(self, text: str) -> bool:
        """Every message he sends: "goodnight Max" puts Maxie to sleep, anything else is a sign of life."""
        if goodnight_said(text):
            self._goodnight_at = self.clock()
            return self.refresh()
        return self.activity("talk")

    # ---- the answer

    def night(self, ts: float | None = None) -> bool:
        t = datetime.fromtimestamp(self.clock() if ts is None else ts, ZoneInfo(self.settings.timezone))
        learned = self._learned()
        if learned is None:
            return t.hour >= NIGHT_FROM_H or t.hour < NIGHT_UNTIL_H
        sleep, wake = learned
        return sleep - MARGIN_MIN <= _from_noon(t) <= wake + MARGIN_MIN

    def _learned(self) -> tuple[float, float] | None:
        """His usual sleep and wake, as minutes from noon (so a night doesn't wrap), once 5 nights are logged."""
        since = self.clock() - LEARN_DAYS * 86400
        rows = self.db.all("SELECT ts, kind FROM presence_log WHERE ts >= ? ORDER BY ts", (since,))
        tz = ZoneInfo(self.settings.timezone)
        nights = [(a["ts"], b["ts"]) for a, b in zip(rows, rows[1:], strict=False)
                  if a["kind"] == "sleep" and b["kind"] == "wake" and b["ts"] - a["ts"] < 18 * 3600]
        if len(nights) < LEARN_NIGHTS:
            return None
        return (median(_from_noon(datetime.fromtimestamp(s, tz)) for s, _ in nights),
                median(_from_noon(datetime.fromtimestamp(w, tz)) for _, w in nights))

    def usual(self) -> tuple[str, str] | None:
        """("1:30", "9:30"): when he usually falls asleep and wakes, or None until Maxie has seen 5 nights."""
        learned = self._learned()
        return None if learned is None else (_clock(learned[0]), _clock(learned[1]))

    def asleep(self) -> bool:
        self._real = False
        if self.settings.sleep_mode == "fixed":
            return self.fallback_quiet()
        now = self.clock()
        self._real = True
        if self._goodnight_at is not None and self._active_at < self._goodnight_at:
            return True
        if now - self._active_at < IDLE_ASLEEP_S:
            return False
        s = self._signals
        if s is None or now - s.at > STALE_S:
            self._real = False
            return self.fallback_quiet()
        if s.media:
            return False
        idle = s.idle_s + (now - s.at)
        resting = s.locked or s.displays_asleep or idle >= IDLE_ASLEEP_S
        return resting and self.night()

    def away(self) -> bool:
        """He stepped away from an unlocked Mac: 10 quiet minutes, nothing playing. A locked Mac can't be
        clicked, so it isn't "away" for a screen task; a dimmed display is (the first click wakes it)."""
        s, now = self._signals, self.clock()
        if s is None or now - s.at > STALE_S or s.locked or s.media or now - self._active_at < AWAY_IDLE_S:
            return False
        return s.idle_s + (now - s.at) >= AWAY_IDLE_S

    def refresh(self) -> bool:
        """Re-reads asleep(); on a change, logs it and tells the hub (mascot state, held pushes)."""
        now_asleep = self.asleep()
        if now_asleep != self._last:
            self._log(now_asleep)
            was, self._last = self._last, now_asleep
            self.on_change(now_asleep, was)
        return now_asleep

    # ---- the log

    def _log(self, asleep: bool) -> None:
        """Real sleeps only (not the clock's quiet hours), and a wake only after a logged sleep."""
        last = self.db.all("SELECT kind FROM presence_log ORDER BY ts DESC LIMIT 1")
        slept = bool(last) and last[0]["kind"] == "sleep"
        if (asleep and self._real and not slept) or (not asleep and slept):
            self.db.execute("INSERT INTO presence_log VALUES (?,?)", (self.clock(), "sleep" if asleep else "wake"))

    def log(self, limit: int = 50) -> list[dict]:
        return self.db.all("SELECT ts, kind FROM presence_log ORDER BY ts LIMIT ?", (limit,))

    def last_night(self) -> str | None:
        """ "Slept 2:05 to 9:40": the latest sleep and the wake after it, if he woke in the last 18 hours."""
        rows = self.db.all("SELECT ts, kind FROM presence_log ORDER BY ts DESC LIMIT 2")
        if len(rows) < 2 or rows[0]["kind"] != "wake" or rows[1]["kind"] != "sleep":
            return None
        if self.clock() - rows[0]["ts"] > 18 * 3600:
            return None
        tz = ZoneInfo(self.settings.timezone)
        fmt = lambda ts: datetime.fromtimestamp(ts, tz).strftime("%-H:%M")  # noqa: E731
        return f"Slept {fmt(rows[1]['ts'])} to {fmt(rows[0]['ts'])}"


def _from_noon(t: datetime) -> int:
    return (t.hour * 60 + t.minute - NOON) % (24 * 60)


def _clock(from_noon: float) -> str:
    h, m = divmod(int(round(from_noon + NOON)) % (24 * 60), 60)
    return f"{h % 12 or 12}:{m:02d}"


def follow(presence: Presence, events, proactive) -> None:
    """What changes when he falls asleep or wakes: the mascot, and the pushes held while he slept."""
    def changed(asleep: bool, was: bool | None) -> None:
        if asleep and was is not None:
            events.set_state("closed", hold=CLOSE_EYES_S)  # then base: sleep
        elif not asleep and was:
            events.set_state("stretch", hold=STRETCH_S)  # then base: idle
            proactive.release_held()
        else:
            events.rest()
    presence.on_change = changed
