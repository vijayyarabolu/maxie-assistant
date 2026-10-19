"""Real Google: Gmail (any number of accounts), Calendar and Drive over REST. No Google SDK: httpx only.

Connecting an account ("Connect Google" in Settings) is the OAuth 2.0 authorization code flow with PKCE
(S256). The consent page redirects back to the hub's own port on this computer
(http://127.0.0.1:<port>/oauth/google/callback), which a "Desktop app" OAuth client allows without
registering anything. You bring your own OAuth client: Google does not allow an open source app to ship a
shared client secret, and Settings walks you through making one.

Each account gets a label and an access level:
- `main`: mail read + compose (drafts, and sending after your OK), Calendar events, Drive read-only.
- `job`: a read-only inbox the morning brief scans for interviews and recruiters.
- any other label: another read-only inbox.

Refresh tokens live in <data>/secrets.json (GOOGLE_ACCOUNTS, a JSON list); access tokens are refreshed a
minute before they expire. Disconnect revokes the token at Google and forgets it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlencode

import httpx

from .google import JOB, MAIN, Email, Event, NotConnectedError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR = "https://www.googleapis.com/calendar/v3"
DRIVE = "https://www.googleapis.com/drive/v3"
IDENTITY = ("openid", "email", "profile")  # "Connected as name@example.com" with the photo
USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
FULL, READ = "full", "read"
SCOPES = {
    FULL: ("https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.compose",
           "https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/drive.readonly", *IDENTITY),
    READ: ("https://www.googleapis.com/auth/gmail.readonly", *IDENTITY),
}
ACCOUNTS_KEY = "GOOGLE_ACCOUNTS"
LABEL_RE = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")
IMPORTANT_HINTS = ("interview", "offer", "urgent", "invoice", "payment", "appointment", "deadline")


def access_for(label: str) -> str:
    return FULL if label == MAIN else READ


# ---- Google said no

API_OFF_REASONS = {"accessNotConfigured", "SERVICE_DISABLED"}
SCOPE_REASONS = {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}
CONSOLE_LINK = re.compile(r"https://console\.(?:developers|cloud)\.google\.com/\S+?(?=\s|$)")


class GoogleRefused(Exception):
    """A 403 from one Google service, with why: the API is off in your Google Cloud project (`api_off`, and the
    page that turns it on), the sign-in didn't grant it (`scope`), or something else (`other`)."""

    def __init__(self, service: str, why: str, fix_url: str = ""):
        super().__init__(f"{service}: Google said no ({why})")
        self.service, self.why, self.fix_url = service, why, fix_url

    @property
    def line(self) -> str:
        """For Settings › Connections: what happened and the exact fix."""
        if self.why == "api_off":
            return (f"The Google {self.service} API is turned off in the Google Cloud project Maxie signs in with. "
                    "Enable it, wait a minute, and Maxie can read it.")
        if self.why == "scope":
            return (f"Your Google sign-in didn't give Maxie {self.service}. "
                    f"Sign in with Google again and leave {self.service} ticked.")
        return f"Google said no (403) to {self.service}. Signing in with Google again usually fixes it."

    def dict(self) -> dict:
        return {"service": self.service, "why": self.why, "line": self.line, "fix_url": self.fix_url}


def refusal(service: str, r: httpx.Response) -> GoogleRefused:
    """Reads Google's error body: `errors[].reason` and `details[].reason` say which 403 it is."""
    try:
        err = r.json().get("error", {}) or {}
    except ValueError:
        err = {}
    details = [d for d in err.get("details", []) if isinstance(d, dict)]
    reasons = {e.get("reason") for e in err.get("errors", []) if isinstance(e, dict)} | {d.get("reason") for d in details}
    if reasons & API_OFF_REASONS:
        link = next((d["metadata"]["activationUrl"] for d in details if d.get("metadata", {}).get("activationUrl")), "")
        found = CONSOLE_LINK.search(str(err.get("message", "")))
        return GoogleRefused(service, "api_off", link or (found.group(0) if found else ""))
    if reasons & SCOPE_REASONS:
        return GoogleRefused(service, "scope")
    return GoogleRefused(service, "other")


# ---- OAuth


class OAuth:
    def __init__(self, client_id: str, client_secret: str, http: httpx.Client | None = None):
        self.client_id, self.client_secret = client_id, client_secret
        self.http = http or httpx.Client(timeout=20)

    def start(self, account: str, redirect_uri: str, login_hint: str = "") -> tuple[str, str, str]:
        """(auth url, state, verifier) for an account label (its access level picks the scopes)."""
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        q = {"client_id": self.client_id, "redirect_uri": redirect_uri, "response_type": "code",
             "scope": " ".join(SCOPES[access_for(account)]), "code_challenge": challenge,
             "code_challenge_method": "S256", "state": state, "access_type": "offline", "prompt": "consent",
             "include_granted_scopes": "true"}
        if login_hint:
            q["login_hint"] = login_hint
        return f"{AUTH_URL}?{urlencode(q)}", state, verifier

    def exchange(self, code: str, verifier: str, redirect_uri: str) -> dict:
        r = self.http.post(TOKEN_URL, data={"client_id": self.client_id, "client_secret": self.client_secret,
                                            "code": code, "code_verifier": verifier, "redirect_uri": redirect_uri,
                                            "grant_type": "authorization_code"})
        r.raise_for_status()
        return r.json()

    def refresh(self, refresh_token: str) -> dict:
        r = self.http.post(TOKEN_URL, data={"client_id": self.client_id, "client_secret": self.client_secret,
                                            "refresh_token": refresh_token, "grant_type": "refresh_token"})
        if r.status_code in (400, 401) and _oauth_error(r) in ("invalid_grant", "unauthorized_client", "invalid_client"):
            raise GoogleAuthLost(_oauth_error(r))
        r.raise_for_status()
        return r.json()

    def revoke(self, token: str) -> bool:
        try:
            return self.http.post(REVOKE_URL, data={"token": token}).status_code == 200
        except httpx.HTTPError:
            return False


class GoogleAuthLost(Exception):
    """Google turned the refresh token away (revoked, expired, or the OAuth client changed). The fix is to
    connect the account again; Settings shows it as failing with a Reconnect button."""

    def __init__(self, reason: str):
        super().__init__(f"Google sign-in expired ({reason}). Reconnect the account in Settings.")
        self.reason = reason


def _oauth_error(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error", ""))
    except ValueError:
        return ""


class Token:
    """One account's access token, refreshed from the stored refresh token when it is about to expire."""

    def __init__(self, oauth: OAuth, stored: dict, clock=time.time):
        self.oauth, self.refresh_token, self.clock = oauth, stored["refresh_token"], clock
        self.email = stored.get("email", "")
        self.access, self.expires = stored.get("access_token", ""), float(stored.get("expires_at", 0))

    def bearer(self) -> dict:
        if not self.access or self.clock() > self.expires - 60:
            t = self.oauth.refresh(self.refresh_token)
            self.access, self.expires = t["access_token"], self.clock() + float(t.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self.access}"}


# ---- the three services


def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _body_text(payload: dict) -> str:
    if payload.get("mimeType", "").startswith("text/plain") and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"] + "==").decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        text = _body_text(part)
        if text:
            return text
    return ""


class GoogleMail:
    """`Mail` over the Gmail REST API, for every connected account (by label)."""

    def __init__(self, tokens: dict[str, Token], http: httpx.Client | None = None, clock=time.time):
        self.tokens, self.http, self.clock = tokens, http or httpx.Client(timeout=20), clock

    def _get(self, account: str, path: str, **params) -> dict:
        r = self.http.get(f"{GMAIL}/{path}", params=params, headers=self.tokens[account].bearer())
        r.raise_for_status()
        return r.json()

    def _post(self, account: str, path: str, body: dict) -> dict:
        r = self.http.post(f"{GMAIL}/{path}", json=body, headers=self.tokens[account].bearer())
        r.raise_for_status()
        return r.json()

    def _email(self, account: str, msg: dict) -> Email:
        ts = int(msg.get("internalDate", 0)) / 1000
        labels = msg.get("labelIds", [])
        subject = _header(msg, "Subject")
        sender = _header(msg, "From")
        return Email(id=f"{account}:{msg['id']}", account=account, sender=sender, subject=subject,
                     body=_body_text(msg.get("payload", {}))[:4000] or msg.get("snippet", ""),
                     date=datetime.fromtimestamp(ts).astimezone().isoformat(timespec="minutes"),
                     important="IMPORTANT" in labels or any(h in (subject + sender).lower() for h in IMPORTANT_HINTS),
                     unread="UNREAD" in labels)

    def search(self, account: str, query: str = "", since: datetime | None = None, limit: int = 20) -> list[Email]:
        if account not in self.tokens:
            return []
        q = query
        if since:
            q = f"{q} after:{int(since.timestamp())}".strip()
        listing = self._get(account, "messages", q=q or "in:inbox", maxResults=limit)
        out = []
        for ref in listing.get("messages", [])[:limit]:
            msg = self._get(account, f"messages/{ref['id']}", format="full")
            out.append(self._email(account, msg))
        return out

    def get(self, email_id: str) -> Email | None:
        account, _, mid = email_id.partition(":")
        if account not in self.tokens or not mid:
            return None
        try:
            return self._email(account, self._get(account, f"messages/{mid}", format="full"))
        except httpx.HTTPStatusError:
            return None

    @staticmethod
    def _raw(to: str, subject: str, body: str, sender: str) -> str:
        m = EmailMessage()
        m["To"], m["Subject"], m["From"] = to, subject, sender
        m.set_content(body)
        return base64.urlsafe_b64encode(m.as_bytes()).decode()

    def _main(self) -> str:
        if MAIN not in self.tokens:
            raise NotConnectedError("No main Google account is connected, so there is nothing to draft or send from. "
                                    "Connect one in Settings.")
        return MAIN

    def draft(self, to: str, subject: str, body: str) -> str:
        main = self._main()
        raw = self._raw(to, subject, body, self.tokens[main].email)
        d = self._post(main, "drafts", {"message": {"raw": raw}})
        return f"draft {d.get('id')} saved in Gmail ({self.tokens[main].email}); send it from there or say so"

    def send(self, to: str, subject: str, body: str) -> str:
        """Only ever called after your confirmation (the registry makes send_email a confirmation)."""
        main = self._main()
        raw = self._raw(to, subject, body, self.tokens[main].email)
        m = self._post(main, "messages/send", {"raw": raw})
        return f"sent to {to} (id {m.get('id')})"


class GoogleCalendar:
    def __init__(self, token: Token, http: httpx.Client | None = None, calendar_id: str = "primary"):
        self.token, self.http, self.cal = token, http or httpx.Client(timeout=20), calendar_id
        self.refused: GoogleRefused | None = None  # the last 403, for Settings › Connections; None once it answers

    def events(self, start: datetime, end: datetime) -> list[Event]:
        r = self.http.get(f"{CALENDAR}/calendars/{self.cal}/events", headers=self.token.bearer(),
                          params={"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true",
                                  "orderBy": "startTime", "maxResults": 50})
        if r.status_code == 403:
            self.refused = refusal("Calendar", r)
            raise self.refused
        r.raise_for_status()
        self.refused = None
        out = []
        for e in r.json().get("items", []):
            s, t = e.get("start", {}), e.get("end", {})
            when = s.get("dateTime") or s.get("date")
            until = t.get("dateTime") or t.get("date")
            if not when:
                continue
            out.append(Event(id=e.get("id", ""), title=e.get("summary", "(no title)"),
                             start=when if "T" in when else when + "T00:00:00",
                             end=until or "", attendees=[a.get("email", "") for a in e.get("attendees", [])],
                             location=e.get("location", ""),
                             description=(e.get("description") or e.get("hangoutLink") or "")[:500]))
        return out

    def create(self, title: str, start: str, end: str, attendees: list[str]) -> str:
        """Only after your confirmation when there are attendees (invites). Times are ISO 8601 with offset."""
        body: dict[str, Any] = {"summary": title, "start": {"dateTime": start}, "end": {"dateTime": end}}
        if attendees:
            body["attendees"] = [{"email": a} for a in attendees]
        r = self.http.post(f"{CALENDAR}/calendars/{self.cal}/events", headers=self.token.bearer(), json=body,
                           params={"sendUpdates": "all" if attendees else "none"})
        r.raise_for_status()
        return f"created \"{title}\" ({r.json().get('htmlLink', '')})"


class GoogleDrive:
    def __init__(self, token: Token, http: httpx.Client | None = None):
        self.token, self.http = token, http or httpx.Client(timeout=20)

    def search(self, query: str) -> list[dict]:
        safe = query.replace("\\", "\\\\").replace("'", "\\'")
        r = self.http.get(f"{DRIVE}/files", headers=self.token.bearer(),
                          params={"q": f"name contains '{safe}' and trashed = false", "pageSize": 10,
                                  "fields": "files(id,name,mimeType,modifiedTime,webViewLink)"})
        r.raise_for_status()
        return [{"name": f["name"], "type": f.get("mimeType", ""), "modified": f.get("modifiedTime", ""),
                 "link": f.get("webViewLink", "")} for f in r.json().get("files", [])]


# ---- connected accounts


def parse_client(text: str) -> tuple[str, str]:
    """(client_id, client_secret) from the JSON file Google Cloud gives you for an OAuth client."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError("That isn't the JSON file from Google Cloud (it doesn't parse).") from e
    block = (raw.get("installed") or raw.get("web")) if isinstance(raw, dict) else None
    client_id = str((block or {}).get("client_id", ""))
    client_secret = str((block or {}).get("client_secret", ""))
    if not client_id.endswith(".apps.googleusercontent.com") or not client_secret:
        raise ValueError("That file has no usable client_id and client_secret.")
    return client_id, client_secret


class Accounts:
    """The connected Google accounts, kept as one JSON list in the secret store."""

    def __init__(self, secret: Callable[[str], str | None], store: Callable[[str, str], None]):
        self.secret, self.store = secret, store

    def all(self) -> list[dict]:
        try:
            raw = json.loads(self.secret(ACCOUNTS_KEY) or "[]")
        except ValueError:
            return []
        return [a for a in raw if isinstance(a, dict) and a.get("label") and a.get("refresh_token")] \
            if isinstance(raw, list) else []

    def get(self, label: str) -> dict | None:
        return next((a for a in self.all() if a["label"] == label), None)

    def put(self, account: dict) -> None:
        rest = [a for a in self.all() if a["label"] != account["label"] and a.get("email") != account.get("email")]
        self.store(ACCOUNTS_KEY, json.dumps([*rest, account]))

    def remove(self, label: str) -> dict | None:
        gone = self.get(label)
        if gone:
            self.store(ACCOUNTS_KEY, json.dumps([a for a in self.all() if a["label"] != label]))
        return gone

    def mark(self, label: str, **fields) -> None:
        a = self.get(label)
        if a:
            self.put({**a, **fields})

    def public(self) -> list[dict]:
        """For Settings: who is connected, never a token."""
        return [{"label": a["label"], "email": a.get("email", ""), "name": a.get("name", ""),
                 "picture": a.get("picture", ""), "access": access_for(a["label"]), "error": a.get("error", "")}
                for a in sorted(self.all(), key=lambda a: (a["label"] != MAIN, a["label"] != JOB, a["label"]))]


def load_tokens(secret, client_id: str | None, client_secret: str | None, http: httpx.Client | None = None,
                clock=time.time) -> dict[str, Token]:
    """A token for every connected account. Empty when there is no OAuth client yet."""
    if not (client_id and client_secret):
        return {}
    oauth = OAuth(client_id, client_secret, http)
    return {a["label"]: Token(oauth, a, clock) for a in Accounts(secret, lambda *_: None).all()}


def whoami(http: httpx.Client, bearer: dict) -> str:
    r = http.get(f"{GMAIL}/profile", headers=bearer)
    r.raise_for_status()
    return r.json().get("emailAddress", "")


def userinfo(http: httpx.Client, bearer: dict) -> dict:
    """The account's name and photo for Settings; {} when Google doesn't say (never fails a sign-in)."""
    try:
        r = http.get(USERINFO, headers=bearer)
        r.raise_for_status()
        info = r.json()
    except (httpx.HTTPError, ValueError):
        return {}
    return {k: str(info[k]) for k in ("name", "picture", "email") if info.get(k)}


class Logins:
    """Consent pages in flight: state -> (label, verifier, redirect). Ten minutes to finish, one use each."""

    TTL_S = 600

    def __init__(self, clock=time.time):
        self.clock = clock
        self._pending: dict[str, tuple[str, str, str, float]] = {}

    def start(self, oauth: OAuth, label: str, redirect_uri: str, login_hint: str = "") -> str:
        url, state, verifier = oauth.start(label, redirect_uri, login_hint)
        now = self.clock()
        self._pending = {k: v for k, v in self._pending.items() if now - v[3] < self.TTL_S}
        self._pending[state] = (label, verifier, redirect_uri, now)
        return url

    def take(self, state: str) -> tuple[str, str, str] | None:
        hit = self._pending.pop(state, None)
        if hit is None or self.clock() - hit[3] >= self.TTL_S:
            return None
        return hit[0], hit[1], hit[2]


def finish_login(oauth: OAuth, accounts: Accounts, label: str, code: str, verifier: str, redirect_uri: str,
                 http: httpx.Client | None = None, clock=time.time) -> dict:
    """Swap the code for tokens, read who it is, store it. Returns the public view of the account."""
    tok = oauth.exchange(code, verifier, redirect_uri)
    if not tok.get("refresh_token"):
        raise PermissionError("Google sent no refresh token. Remove Maxie at myaccount.google.com/permissions "
                              "and connect again.")
    http = http or oauth.http
    bearer = {"Authorization": f"Bearer {tok['access_token']}"}
    info = userinfo(http, bearer)
    email = info.get("email") or whoami(http, bearer)
    granted = set(str(tok.get("scope", "")).split()) or set(SCOPES[access_for(label)])
    accounts.put({"label": label, "email": email.lower(), "name": info.get("name", ""),
                  "picture": info.get("picture", ""), "scopes": sorted(granted),
                  "refresh_token": tok["refresh_token"], "access_token": tok["access_token"],
                  "expires_at": clock() + float(tok.get("expires_in", 3600)), "error": ""})
    return next(a for a in accounts.public() if a["label"] == label)


def week_window(now: datetime) -> tuple[datetime, datetime]:
    return now, now + timedelta(days=7)


class LiveGoogle:
    """Mail, Calendar and Drive for the hub, rebuilt from the secret store whenever Settings connects or
    disconnects an account, so nothing needs a restart. With nothing connected every call says so plainly."""

    def __init__(self, secret, store, http: httpx.Client | None = None, clock=time.time):
        self.secret, self.http, self.clock = secret, http, clock
        self.accounts = Accounts(secret, store)
        self.refused: GoogleRefused | None = None
        self._key, self._built = None, None

    def client(self) -> tuple[str, str] | None:
        cid, csec = self.secret("GOOGLE_CLIENT_ID"), self.secret("GOOGLE_CLIENT_SECRET")
        return (cid, csec) if cid and csec else None

    def _parts(self):
        key = (self.secret("GOOGLE_CLIENT_ID"), self.secret(ACCOUNTS_KEY))
        if key != self._key:
            tokens = load_tokens(self.secret, *(self.client() or (None, None)), self.http, self.clock)
            main = tokens.get(MAIN)
            self._built = (GoogleMail(tokens, self.http, self.clock) if tokens else None,
                           GoogleCalendar(main, self.http) if main else None,
                           GoogleDrive(main, self.http) if main else None, tokens)
            self._key = key
        return self._built

    @property
    def connected(self) -> bool:
        return bool(self.labels())

    def labels(self) -> list[str]:
        return sorted(self._parts()[3])

    def _call(self, label: str | None, fn):
        try:
            return fn()
        except GoogleAuthLost as e:
            if label:
                self.accounts.mark(label, error=str(e))
            raise NotConnectedError(str(e)) from e

    # Mail
    def search(self, account, query="", since=None):
        mail = self._parts()[0]
        if mail is None:
            raise NotConnectedError("Google isn't connected yet. Connect it in Settings.")
        if account not in mail.tokens:
            return []
        return self._call(account, lambda: mail.search(account, query, since))

    def get(self, email_id):
        mail = self._parts()[0]
        return self._call(email_id.partition(":")[0], lambda: mail.get(email_id)) if mail else None

    def draft(self, to, subject, body):
        mail = self._parts()[0]
        if mail is None:
            raise NotConnectedError("Google isn't connected yet. Connect it in Settings.")
        return self._call(MAIN, lambda: mail.draft(to, subject, body))

    def send(self, to, subject, body):
        mail = self._parts()[0]
        if mail is None:
            raise NotConnectedError("Google isn't connected yet. Connect it in Settings.")
        return self._call(MAIN, lambda: mail.send(to, subject, body))

    # Calendar
    def _cal(self) -> GoogleCalendar:
        cal = self._parts()[1]
        if cal is None:
            raise NotConnectedError("Calendar needs a main Google account. Connect one in Settings.")
        return cal

    def events(self, start, end):
        cal = self._cal()
        try:
            return self._call(MAIN, lambda: cal.events(start, end))
        finally:
            self.refused = cal.refused

    def create(self, title, start, end, attendees):
        cal = self._cal()
        return self._call(MAIN, lambda: cal.create(title, start, end, attendees))

    # Drive (same method name as Mail.search, so it is its own object)
    def drive(self) -> LiveDrive:
        return LiveDrive(self)


class LiveDrive:
    def __init__(self, google: LiveGoogle):
        self.google = google

    def search(self, query):
        drive = self.google._parts()[2]
        if drive is None:
            raise NotConnectedError("Drive needs a main Google account. Connect one in Settings.")
        return self.google._call(MAIN, lambda: drive.search(query))
