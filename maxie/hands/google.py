"""Google: Gmail (one or more accounts), Calendar, Drive.

Accounts have labels (google_real.py): `main` is your own mail, Calendar and Drive (read, search, draft;
send only after you confirm); `job` is an optional read-only job-search inbox the brief scans for interviews
and recruiters; any other label is another read-only inbox.

The demo hub loads made-up fixtures (fixtures/google.json). The real clients in google_real.py implement
the same three Protocols.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .registry import Registry, Tool, schema

MAIN, JOB = "main", "job"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "google.json"


@dataclass
class Email:
    id: str
    account: str
    sender: str
    subject: str
    body: str
    date: str  # ISO 8601
    important: bool = False
    unread: bool = True


@dataclass
class Event:
    id: str
    title: str
    start: str  # ISO 8601 with offset
    end: str
    attendees: list[str] = field(default_factory=list)
    location: str = ""
    description: str = ""


class Mail(Protocol):
    def search(self, account: str, query: str = "", since: datetime | None = None) -> list[Email]: ...
    def get(self, email_id: str) -> Email | None: ...
    def draft(self, to: str, subject: str, body: str) -> str: ...
    def send(self, to: str, subject: str, body: str) -> str: ...


class Calendar(Protocol):
    def events(self, start: datetime, end: datetime) -> list[Event]: ...
    def create(self, title: str, start: str, end: str, attendees: list[str]) -> str: ...


class Drive(Protocol):
    def search(self, query: str) -> list[dict]: ...


def _matches(text: str, query: str) -> bool:
    return all(word in text.lower() for word in query.lower().split())


class FakeGoogle:
    """Mail + Calendar + Drive over fixture data. Records drafts/sends/creates for tests."""

    def __init__(self, data: dict | None = None):
        data = data if data is not None else json.loads(FIXTURES.read_text())
        self.emails = [Email(**e) for e in data.get("emails", [])]
        self.event_list = [Event(**e) for e in data.get("events", [])]
        self.files = data.get("drive", [])
        self.sent: list[dict] = []
        self.drafts: list[dict] = []
        self.created: list[dict] = []

    # Mail
    def search(self, account, query="", since=None):
        out = [e for e in self.emails if e.account == account
               and _matches(f"{e.sender} {e.subject} {e.body}", query)
               and (since is None or datetime.fromisoformat(e.date) >= since)]
        return sorted(out, key=lambda e: e.date, reverse=True)

    def get(self, email_id):
        return next((e for e in self.emails if e.id == email_id), None)

    def draft(self, to, subject, body):
        self.drafts.append({"to": to, "subject": subject, "body": body})
        return "draft saved in Gmail"

    def send(self, to, subject, body):
        self.sent.append({"to": to, "subject": subject, "body": body})
        return f"sent to {to}"

    # Calendar
    def events(self, start, end):
        return sorted((e for e in self.event_list
                       if start <= datetime.fromisoformat(e.start) < end), key=lambda e: e.start)

    def create(self, title, start, end, attendees):
        self.created.append({"title": title, "start": start, "end": end, "attendees": attendees})
        return f"created {title}"


class FakeDrive:
    def __init__(self, google: FakeGoogle):
        self.google = google

    def search(self, query):
        return [f for f in self.google.files if _matches(f["name"], query)]


def _brief_email(e: Email) -> dict:
    return {"id": e.id, "from": e.sender, "subject": e.subject, "date": e.date, "preview": e.body[:200]}


class NotConnectedError(RuntimeError):
    pass


class NotConnected:
    """Google on the live hub before you connect it: no fake data ever, just a clear answer.
    Stands in for Mail, Calendar and Drive; `connected` tells the brief to say "Gmail not connected"."""

    connected = False
    LINE = "Google isn't connected yet. Connect it in Settings, under Google."

    def _no(self, *_a, **_k):
        raise NotConnectedError(self.LINE)

    search = get = draft = send = events = create = _no


def register(reg: Registry, mail: Mail, cal: Calendar, drive: Drive, now=datetime.now) -> None:
    s = {"type": "string"}
    acct = {"type": "string",
            "description": "which inbox, by its label in Settings: main = your own mail; job = the job-search inbox "
                           "(read-only); other labels are other read-only inboxes. Default main."}

    def read(a):
        e = mail.get(a["id"])
        return asdict(e) if e else "not found"

    reg.add(Tool("search_email", "Search the user's email. Returns sender, subject, date, preview.",
                 schema({"account": acct, "query": s}, ["query"]),
                 lambda a: [_brief_email(e) for e in mail.search(a.get("account") or MAIN, a.get("query", ""))[:10]],
                 "read", untrusted_output=True))
    reg.add(Tool("read_email", "Read one email in full by id.", schema({"id": s}), read, "read", untrusted_output=True))
    reg.add(Tool("draft_email", "Save a Gmail draft in the main account (not sent).",
                 schema({"to": s, "subject": s, "body": s}),
                 lambda a: mail.draft(a["to"], a["subject"], a["body"]), "action"))
    reg.add(Tool("send_email", "Send an email from the main account. Always needs the user's confirmation.",
                 schema({"to": s, "subject": s, "body": s}),
                 lambda a: mail.send(a["to"], a["subject"], a["body"]), "send",
                 summary=lambda a: f"Send email to {a['to']}: \"{a['subject']}\""))
    reg.add(Tool("create_event", "Create a calendar event. Always needs the user's confirmation.",
                 schema({"title": s, "start": s, "end": s, "attendees": {"type": "array", "items": s}}),
                 lambda a: cal.create(a["title"], a["start"], a["end"], a.get("attendees", [])), "invite",
                 summary=lambda a: f"Add \"{a['title']}\" {a['start']}" + (
                     f" and invite {', '.join(a['attendees'])}" if a.get("attendees") else "")))
    reg.add(Tool("drive_search", "Search the user's Google Drive by file name.", schema({"query": s}),
                 lambda a: drive.search(a["query"])[:10], "read", untrusted_output=True))
