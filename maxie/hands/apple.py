"""Apple data on this Mac, read locally and free: Messages, Calendar, Reminders, Contacts,
Notes, Mail, Spotlight, and Apple's on-device model.

macOS only grants these to an app, never to a child process like the hub, so
Maxie.app serves them on 127.0.0.1:8783 (the "Apple bridge", hub token required) and this module is its
client. "Max, find the address Riley texted me" is answered here with no model at all: the bridge returns
Riley's recent messages, a pattern pulls out the address, and the reply says when he sent it.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

from .registry import Registry, Tool, schema

BRIDGE_URL = "http://127.0.0.1:8783"
SEARCH_DAYS = 365

WHY = {
    "bridge_down": "Maxie.app isn't running, so I can't reach your Mac's apps. Open Maxie and ask again.",
    "full_disk_access": "To search your Messages, turn on Full Disk Access for Maxie in Settings › Permissions.",
    "contacts": "Turn on Contacts for Maxie in Settings › Permissions so I know who's who.",
    "calendars": "Turn on Calendars for Maxie in Settings › Permissions.",
    "reminders": "Turn on Reminders for Maxie in Settings › Permissions.",
    "automation": "Let Maxie use that app: Settings › Permissions › Automation.",
    "fm_unavailable": "Apple Intelligence isn't available on this Mac right now.",
    "screen": "Turn on Screen Recording for Maxie in Settings › Permissions.",
}


class AppleUnavailable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason

    def message(self) -> str:
        return WHY.get(self.reason, f"I couldn't reach that on your Mac ({self.reason}).")


class AppleBridge(Protocol):
    def messages(self, person: str, query: str = "", days: int = SEARCH_DAYS) -> list[dict]: ...
    def contacts(self, query: str) -> list[dict]: ...
    def events(self, days: int = 1) -> list[dict]: ...
    def calendar(self, days: int = 1) -> dict: ...
    def reminders(self) -> list[dict]: ...
    def notes(self, query: str) -> list[dict]: ...
    def mail(self, query: str) -> list[dict]: ...
    def draft_mail(self, to: str, subject: str, body: str) -> str: ...
    def files(self, query: str) -> list[dict]: ...
    def fm(self, task: str, text: str, how: str = "") -> str: ...
    def screenshot(self) -> str: ...


# --- pulling one thing out of a message (no model) ----------------------------------------------------

_SUFFIX = (r"St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Ct|Court|Way|Pl|Place|Pkwy|Parkway"
           r"|Ter|Terrace|Cir|Circle|Hwy|Highway|Loop|Trail|Trl")
PATTERNS = {
    "address": re.compile(
        rf"\b\d{{1,6}}\s+(?:[NSEW]\.?\s+)?(?:[A-Za-z0-9][\w'.-]*\s+){{0,4}}?(?:{_SUFFIX})\b\.?"
        rf"(?:,?\s*(?:Apt|Apartment|Unit|Suite|Ste|#)\.?\s*\w+)?"
        rf"(?:,\s*[A-Z][A-Za-z .'-]+?,\s*[A-Z]{{2}}(?:\s+\d{{5}}(?:-\d{{4}})?)?)?", re.I),
    "phone": re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}\b"),
    "link": re.compile(r"https?://[^\s<>\"']+"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    "code": re.compile(r"\b\d{4,8}\b"),
}
NOUN = {"address": "an address", "phone": "a phone number", "link": "a link", "email": "an email", "code": "a code"}


def extract(kind: str, text: str) -> str | None:
    m = PATTERNS[kind].search(text or "")
    return m.group(0).rstrip(".,;:!?)") if m else None


def find_in_messages(bridge: AppleBridge, who: str, kind: str, tz: str) -> str:
    """The newest message from `who` that contains the thing, said with its date."""
    try:
        msgs = bridge.messages(who)
    except AppleUnavailable as e:
        return e.message()
    for m in sorted(msgs, key=lambda m: m.get("date", 0), reverse=True):
        if m.get("from_me"):
            continue
        value = extract(kind, m.get("text", ""))
        if value:
            day = datetime.fromtimestamp(m["date"], ZoneInfo(tz)).strftime("%b %-d")
            return f"{m.get('sender') or who} texted you this on {day}: {value}."
    return f"I didn't find {NOUN[kind]} from {who} in your Messages."


# --- the bridge ----------------------------------------------------------------------------------------

class BridgeHTTP:
    def __init__(self, token: str, base: str = BRIDGE_URL, http: httpx.Client | None = None):
        self.base, self.http = base.rstrip("/"), http or httpx.Client(timeout=15)
        self.h = {"Authorization": f"Bearer {token}"}

    def _call(self, method: str, path: str, **kw) -> dict:
        try:
            r = self.http.request(method, f"{self.base}{path}", headers=self.h, **kw)
        except httpx.HTTPError as e:
            raise AppleUnavailable("bridge_down") from e
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code != 200:
            raise AppleUnavailable(str(body.get("error") or f"http {r.status_code}"))
        return body

    def messages(self, person, query="", days=SEARCH_DAYS):
        return self._call("GET", "/messages", params={"person": person, "q": query, "days": days})["messages"]

    def screenshot(self):
        """One screenshot of the screen, taken by Maxie.app (it holds Screen Recording): a JPEG data URL."""
        return self._call("GET", "/screen")["image"]

    def contacts(self, query):
        return self._call("GET", "/contacts", params={"q": query})["contacts"]

    def events(self, days=1):
        return self._call("GET", "/calendar", params={"days": days})["events"]

    def calendar(self, days=1):
        """{"events": [...], "calendars": [{"title", "account"}]}: the calendars too, so Maxie can say what she read."""
        body = self._call("GET", "/calendar", params={"days": days})
        return {"events": body.get("events", []), "calendars": body.get("calendars", [])}

    def reminders(self):
        return self._call("GET", "/reminders")["reminders"]

    def notes(self, query):
        return self._call("GET", "/notes", params={"q": query})["notes"]

    def mail(self, query):
        return self._call("GET", "/mail", params={"q": query})["mail"]

    def draft_mail(self, to, subject, body):
        return self._call("POST", "/mail/draft", json={"to": to, "subject": subject, "body": body})["result"]

    def files(self, query):
        return self._call("GET", "/files", params={"q": query})["files"]

    def fm(self, task, text, how=""):
        return self._call("POST", "/fm", json={"task": task, "text": text, "how": how}, timeout=30)["text"]


class FakeApple:
    """For tests and the fake hub: messages keyed by the person's first name, lower case."""

    def __init__(self, messages: dict | None = None, error: AppleUnavailable | None = None, fm_reply: str = "",
                 screen: str = "", events: list[dict] | None = None, calendars: list[dict] | None = None):
        self.by_person, self.error, self.fm_reply, self.screen = dict(messages or {}), error, fm_reply, screen
        self.event_rows, self.calendar_rows = list(events or []), list(calendars or [])
        self.calls: list[tuple] = []

    def _check(self, *call) -> None:
        self.calls.append(call)
        if self.error:
            raise self.error

    def messages(self, person, query="", days=SEARCH_DAYS):
        self._check("messages", person)
        return list(self.by_person.get(person.lower().split()[0], []))

    def contacts(self, query):
        self._check("contacts", query)
        return []

    def events(self, days=1):
        self._check("events", days)
        return list(self.event_rows)

    def calendar(self, days=1):
        self._check("calendar", days)
        return {"events": list(self.event_rows), "calendars": list(self.calendar_rows)}

    def reminders(self):
        self._check("reminders")
        return []

    def notes(self, query):
        self._check("notes", query)
        return []

    def mail(self, query):
        self._check("mail", query)
        return []

    def draft_mail(self, to, subject, body):
        self._check("draft_mail", to, subject)
        return "Draft saved in Mail."

    def files(self, query):
        self._check("files", query)
        return []

    def fm(self, task, text, how=""):
        self._check("fm", task)
        if not self.fm_reply:
            raise AppleUnavailable("fm_unavailable")
        return self.fm_reply

    def screenshot(self):
        self._check("screen")
        if not self.screen:
            raise AppleUnavailable("screen")
        return self.screen


# --- tools ---------------------------------------------------------------------------------------------

def _lines(rows: list[dict], fields: tuple[str, ...]) -> str:
    return "\n".join(" · ".join(str(r.get(f, "")) for f in fields if r.get(f)) for r in rows) or "Nothing found."


def register(reg: Registry, bridge: AppleBridge, tz: str) -> None:
    s = {"type": "string"}
    kinds = {"type": "string", "enum": sorted(PATTERNS)}

    def guarded(fn):
        def run(a):
            try:
                return fn(a)
            except AppleUnavailable as e:
                return e.message()
        return run

    reg.add(Tool("find_in_messages", "Find an address, phone number, link, email or code someone texted the user (local Messages).",
                 schema({"who": s, "kind": kinds}), lambda a: find_in_messages(bridge, a["who"], a["kind"], tz),
                 "read", untrusted_output=True))
    reg.add(Tool("messages_search", "Search the user's iMessages/SMS with one person (local, read-only).",
                 schema({"who": s, "query": s}, ["who"]),
                 guarded(lambda a: _lines(bridge.messages(a["who"], a.get("query", "")), ("sender", "text"))),
                 "read", untrusted_output=True))
    reg.add(Tool("contacts_find", "Look someone up in the user's Contacts (phones, emails).", schema({"query": s}),
                 guarded(lambda a: _lines(bridge.contacts(a["query"]), ("name", "phones", "emails"))), "read"))
    reg.add(Tool("apple_calendar", "The user's events from the Mac's Calendar (iCloud, Exchange, Google accounts added to macOS).",
                 schema({"days": {"type": "integer"}}, []),
                 guarded(lambda a: _lines(bridge.events(int(a.get("days", 1))), ("start", "title", "location"))), "read"))
    reg.add(Tool("apple_reminders", "His open reminders.", schema({}, []),
                 guarded(lambda a: _lines(bridge.reminders(), ("title", "due"))), "read"))
    reg.add(Tool("notes_search", "Search the user's Apple Notes.", schema({"query": s}),
                 guarded(lambda a: _lines(bridge.notes(a["query"]), ("title", "snippet"))), "read", untrusted_output=True))
    reg.add(Tool("mail_search", "Search Mail.app (all accounts set up on the Mac).", schema({"query": s}),
                 guarded(lambda a: _lines(bridge.mail(a["query"]), ("from", "subject", "date"))), "read", untrusted_output=True))
    reg.add(Tool("mail_draft", "Save a draft in Mail.app (not sent).", schema({"to": s, "subject": s, "body": s}),
                 guarded(lambda a: bridge.draft_mail(a["to"], a["subject"], a["body"])), "action"))
    reg.add(Tool("spotlight", "Spotlight search across the Mac (files, mail, notes).", schema({"query": s}),
                 guarded(lambda a: _lines(bridge.files(a["query"]), ("name", "path"))), "read", untrusted_output=True))
