"""Every calendar he has, in one answer.

"What's on my calendar?" used to read only his Google primary calendar through the API, while the Mac's Calendar
app also has iCloud Home / Work, invites and the holiday calendars (EventKit, through Maxie.app's bridge). Now one
tool reads both, merges what both have, and says which calendars it read and which it could not, so an empty day
sounds like "nothing on Home, Work or Google", never like a broken Maxie.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .apple import AppleUnavailable
from .registry import Registry, Tool, schema

GOOGLE = "Google (primary)"
MAX_DAYS = 31


def _google(cal, start: datetime, end: datetime) -> list[dict]:
    return [{"title": e.title, "start": e.start, "end": e.end, "all_day": "T00:00:00" in e.start and len(e.start) <= 19,
             "location": e.location, "calendar": GOOGLE} for e in cal.events(start, end)]


def _mac(apple, days: int) -> tuple[list[dict], list[str]]:
    body = apple.calendar(days)
    rows = [{**e, "all_day": str(e.get("all_day", "")).lower() == "true"} for e in body["events"]]
    names = sorted({f"{c['title']} ({c['account']})" if c.get("account") else c["title"] for c in body["calendars"]})
    return rows, names


def _key(e: dict) -> tuple[str, str]:
    """The same event seen twice (his Google calendar is also added to macOS): same title, same day and time."""
    return (e.get("title", "").strip().lower(), str(e.get("start", ""))[:16])


def all_events(cal, apple, now: datetime, days: float = 1) -> dict:
    days = max(1, min(MAX_DAYS, round(days)))
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    events, read, unread = [], [], []
    try:
        events += _google(cal, start, start + timedelta(days=days))
        read.append(GOOGLE)
    except Exception as e:  # one calendar failing must not hide the others; it is named instead  # noqa: BLE001
        unread.append(f"{GOOGLE}: {e}")
    if apple is not None:
        try:
            mac, names = _mac(apple, days)
            events += mac
            read += [f"Mac Calendar: {', '.join(names)}"] if names else ["Mac Calendar"]
        except AppleUnavailable as e:
            unread.append(f"Mac Calendar: {e.message()}")
    seen, merged = set(), []
    for e in sorted(events, key=lambda e: str(e.get("start", ""))):
        if _key(e) not in seen:
            seen.add(_key(e))
            merged.append(e)
    return {"events": merged, "from": start.date().isoformat(), "days": days, "calendars_read": read,
            "could_not_read": unread,
            "say": "Name the day of each event. If there are none, say which calendars you checked. If a calendar "
                   "could not be read, say so plainly."}


def register(reg: Registry, cal, apple, now=datetime.now) -> None:
    reg.add(Tool("calendar_events",
                 "The user's events for N days from the start of today, from every calendar they have: Google (primary) and "
                 "the Mac's Calendar app (iCloud Home / Work, invites, holidays). Says which calendars it read.",
                 schema({"days": {"type": "number"}}, []),
                 lambda a: all_events(cal, apple, now().astimezone(), float(a.get("days", 1))),
                 "read", untrusted_output=True))
