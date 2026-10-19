"""Habits from what he does: pure functions over the kept app switches, no storage.

A habit is an app he first opens at about the same time on at least 3 days, on most of the days he uses it at
all. Apps whose times sit within 20 minutes of each other become one habit: "You open jobhunt and Gmail every
weekday morning at 9"
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from statistics import median
from zoneinfo import ZoneInfo

MIN_DAYS = 3
WINDOW_MIN = 45  # a day counts if the first open is this close to his usual time
SHARE = 0.6  # ...on at least this share of the days he opened the app at all
PAIR_MIN = 20  # habits this close in time are one routine
ROUND_MIN = 15
MAX_APPS = 3


@dataclass(frozen=True)
class Habit:
    apps: tuple[str, ...]  # bundle ids, in the order he opens them
    names: tuple[str, ...]
    minute: int  # minute of the day, rounded to 15
    weekdays_only: bool
    days: int
    usual: float = 0.0  # the unrounded minute: who comes first in a routine

    @property
    def key(self) -> str:
        return f"open:{'+'.join(self.apps)}@{self.minute}:{'weekdays' if self.weekdays_only else 'daily'}"

    @property
    def text(self) -> str:
        every = "every weekday" if self.weekdays_only else "every"
        return f"You open {join(self.names)} {every} {part_of_day(self.minute)} at {spoken_time(self.minute)}"  # a Memory fact: no full stop

    @property
    def routine(self) -> str:
        days = "on weekdays" if self.weekdays_only else "every day"
        return f"Open {join(self.names)} {days} at {self.minute // 60}:{self.minute % 60:02d}."


def join(names: tuple[str, ...] | list[str]) -> str:
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def part_of_day(minute: int) -> str:
    h = minute // 60
    return "morning" if 4 <= h < 12 else "afternoon" if h < 17 else "evening" if h < 21 else "night"


def spoken_time(minute: int) -> str:
    h, m = divmod(minute, 60)
    h12 = h % 12 or 12
    return f"{h12}" if m == 0 else f"{h12}:{m:02d}"


def first_opens(rows: list[dict], tz: ZoneInfo) -> tuple[dict[str, dict[date, int]], dict[str, str]]:
    """app -> {day: minute of the first open that day}, and app -> its latest name."""
    firsts: dict[str, dict[date, int]] = defaultdict(dict)
    names: dict[str, str] = {}
    for r in sorted(rows, key=lambda r: r["ts"]):
        t = datetime.fromtimestamp(r["ts"], tz)
        firsts[r["app"]].setdefault(t.date(), t.hour * 60 + t.minute)
        names[r["app"]] = r["name"] or r["app"]
    return firsts, names


def single(app: str, name: str, days: dict[date, int]) -> Habit | None:
    if len(days) < MIN_DAYS:
        return None
    usual = median(days.values())
    close = [d for d, m in days.items() if abs(m - usual) <= WINDOW_MIN]
    if len(close) < MIN_DAYS or len(close) / len(days) < SHARE:
        return None
    rounded = int(round(usual / ROUND_MIN) * ROUND_MIN) % (24 * 60)
    return Habit((app,), (name,), rounded, all(d.weekday() < 5 for d in close), len(close), usual)


def merge(habits: list[Habit]) -> list[Habit]:
    """Habits a few minutes apart, with the same days, are one routine (up to 3 apps)."""
    out: list[Habit] = []
    for h in sorted(habits, key=lambda h: (h.usual, h.apps)):
        last = out[-1] if out else None
        if (last and last.weekdays_only == h.weekdays_only and abs(h.minute - last.minute) <= PAIR_MIN
                and len(last.apps) < MAX_APPS):
            out[-1] = Habit(last.apps + h.apps, last.names + h.names, last.minute, last.weekdays_only,
                            min(last.days, h.days), last.usual)
        else:
            out.append(h)
    return out


def mine(rows: list[dict], tz: ZoneInfo) -> list[Habit]:
    """rows: {"ts", "app", "name"} app switches, oldest first or not."""
    firsts, names = first_opens(rows, tz)
    found = [h for app, days in firsts.items() if (h := single(app, names[app], days)) is not None]
    return merge(found)
