"""The real Google client against responses shaped like the Gmail, Calendar and Drive REST APIs."""

import base64
import json
from datetime import datetime

import httpx

from maxie.hands.google import JOB, MAIN
from maxie.hands.google_real import (
    GoogleCalendar,
    GoogleDrive,
    GoogleMail,
    OAuth,
    Token,
    load_tokens,
)


def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


MESSAGE = {"id": "m1", "internalDate": "1792000000000", "labelIds": ["INBOX", "UNREAD"], "snippet": "hi",
           "payload": {"mimeType": "multipart/alternative",
                       "headers": [{"name": "From", "value": "Recruiter <r@acme.ai>"},
                                   {"name": "Subject", "value": "Interview with AcmeAI"}],
                       "parts": [{"mimeType": "text/plain", "body": {"data": b64("Can you do Tuesday 2pm?")}}]}}


def transport(seen):
    def handle(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, dict(req.url.params), req.headers.get("authorization")))
        p = req.url.path
        if p == "/token":
            return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
        if p.endswith("/messages") and req.method == "GET":
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        if p.endswith("/messages/m1"):
            return httpx.Response(200, json=MESSAGE)
        if p.endswith("/drafts"):
            return httpx.Response(200, json={"id": "d9"})
        if p.endswith("/messages/send"):
            body = json.loads(req.content)
            raw = base64.urlsafe_b64decode(body["raw"] + "==").decode()
            assert "To: sam@example.com" in raw and "Subject: hello" in raw
            return httpx.Response(200, json={"id": "sent1"})
        if "/calendars/primary/events" in p and req.method == "GET":
            return httpx.Response(200, json={"items": [
                {"id": "e1", "summary": "Standup", "start": {"dateTime": "2026-10-15T09:00:00-05:00"},
                 "end": {"dateTime": "2026-10-15T09:30:00-05:00"}, "attendees": [{"email": "a@b.c"}],
                 "hangoutLink": "https://meet/x"},
                {"id": "e2", "summary": "Holiday", "start": {"date": "2026-10-16"}, "end": {"date": "2026-10-17"}}]})
        if "/calendars/primary/events" in p and req.method == "POST":
            return httpx.Response(200, json={"id": "new", "htmlLink": "https://cal/new"})
        if p == "/drive/v3/files":
            return httpx.Response(200, json={"files": [{"id": "f", "name": "resume.pdf", "mimeType": "application/pdf",
                                                          "modifiedTime": "2026-10-01T00:00:00Z",
                                                          "webViewLink": "https://drive/f"}]})
        return httpx.Response(404)
    return httpx.MockTransport(handle)


def tokens(seen, clock):
    http = httpx.Client(transport=transport(seen))
    oauth = OAuth("cid", "csec", http)
    stored = {"refresh_token": "r", "email": "alex@example.com", "access_token": "", "expires_at": 0}
    return {MAIN: Token(oauth, stored, clock),
            JOB: Token(oauth, {**stored, "email": "alex.jobs@example.com"}, clock)}, http


def test_mail_search_read_draft_send(clock):
    seen = []
    toks, http = tokens(seen, clock)
    mail = GoogleMail(toks, http, clock)
    found = mail.search(JOB, "interview")
    assert seen[0][1] == "/token"  # expired access token was refreshed first
    assert seen[1][3] == "Bearer fresh"
    assert [e.subject for e in found] == ["Interview with AcmeAI"]
    e = found[0]
    assert e.id == "job:m1" and e.account == JOB and e.unread and e.important and "Tuesday" in e.body
    assert e.date.startswith("2026-10-")
    assert mail.get("job:m1").subject == "Interview with AcmeAI" and mail.get("nope") is None
    assert mail.draft("x@y.z", "s", "b").startswith("draft d9 saved")
    assert mail.send("sam@example.com", "hello", "see you").startswith("sent to sam@example.com")
    assert mail.search("unknown") == []


def test_calendar_and_drive(clock):
    seen = []
    toks, http = tokens(seen, clock)
    cal = GoogleCalendar(toks[MAIN], http)
    evs = cal.events(datetime(2026, 10, 15), datetime(2026, 10, 17))
    assert [(e.title, e.start, e.end) for e in evs] == [
        ("Standup", "2026-10-15T09:00:00-05:00", "2026-10-15T09:30:00-05:00"),
        ("Holiday", "2026-10-16T00:00:00", "2026-10-17")]
    assert evs[0].attendees == ["a@b.c"] and "meet" in evs[0].description
    assert cal.create("Coffee", "2026-10-20T10:00:00-05:00", "2026-10-20T11:00:00-05:00", ["s@x.y"]).startswith("created")
    post = [s for s in seen if s[0] == "POST" and "events" in s[1]][0]
    assert post[2]["sendUpdates"] == "all"
    assert GoogleDrive(toks[MAIN], http).search("resume")[0]["name"] == "resume.pdf"


def test_load_tokens_from_the_secret_store(clock):
    accounts = [{"label": "main", "refresh_token": "r", "email": "alex@example.com"},
                {"label": "job", "email": "no-token@example.com"}, "not a dict"]
    stash = {"GOOGLE_ACCOUNTS": json.dumps(accounts)}
    toks = load_tokens(stash.get, "cid", "csec", clock=clock)
    assert list(toks) == [MAIN] and toks[MAIN].email == "alex@example.com"
    assert load_tokens(stash.get, None, None) == {}
    assert load_tokens({"GOOGLE_ACCOUNTS": "not json"}.get, "cid", "csec") == {}


def test_oauth_start_is_pkce_offline_and_scoped():
    url, state, verifier = OAuth("cid", "csec").start(JOB, "http://127.0.0.1:1/")
    assert "code_challenge_method=S256" in url and "access_type=offline" in url and state and len(verifier) > 40
    assert "gmail.readonly" in url and "gmail.compose" not in url  # job inbox is read-only
