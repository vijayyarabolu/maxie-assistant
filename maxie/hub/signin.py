"""Signing in to the hub: a passcode for browsers, tokens for apps.

First run: the hub has no passcode, so the web app shows "Set a passcode". That page only works from this
computer (a loopback address), so nobody else on the network can claim a fresh hub. After that:

- A browser signs in with the passcode and gets a session cookie: HttpOnly, SameSite=Strict, Secure when
  the hub is served over HTTPS. 30 days from sign-in, rotated once a day while in use, revocable from
  Settings. Only a SHA-256 of each cookie is stored.
- Apps that can't show a sign-in page (the iPhone Shortcut, the Mac notch app, the voice helper) send
  `Authorization: Bearer <token>`. Settings makes these tokens, shows each one once, and can revoke them.
  Only a hash is stored here too.
- HUB_TOKEN from the environment or secrets.json is still accepted as a Bearer token. `make demo` uses it,
  and so does the Mac app once "Connect the Mac app" has written it to the Keychain.

The passcode is stored as an scrypt hash. Sign-in attempts are rate limited per client address.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import time
from collections.abc import Callable

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel

COOKIE = "maxie_session"
SESSION_DAYS = 30
ROTATE_AFTER_S = 24 * 3600
ATTEMPTS_PER_MINUTE = 10
MIN_PASSCODE = 6
OWNER = "owner"
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def hash_passcode(passcode: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(passcode.encode(), salt=salt, **SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def passcode_matches(passcode: str, stored: str) -> bool:
    try:
        kind, salt, _digest = stored.split("$")
        if kind != "scrypt":
            return False
        return hmac.compare_digest(hash_passcode(passcode, bytes.fromhex(salt)), stored)
    except ValueError:
        return False


def is_loopback(host: str | None) -> bool:
    if host in ("testclient", "localhost"):  # Starlette's TestClient, and a name some proxies pass on
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


class Sessions:
    """Signed-in browsers. Only a SHA-256 of each cookie is stored."""

    def __init__(self, db, clock: Callable[[], float] = time.time):
        self.db, self.clock = db, clock
        db.script("""CREATE TABLE IF NOT EXISTS signin_sessions (
            id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, email TEXT NOT NULL, name TEXT,
            created REAL NOT NULL, expires REAL NOT NULL, rotated REAL NOT NULL, device TEXT)""")

    def start(self, who: str = OWNER, device: str = "") -> str:
        raw, now = secrets.token_urlsafe(32), self.clock()
        self.db.execute("INSERT INTO signin_sessions VALUES (?,?,?,?,?,?,?,?)",
                        (secrets.token_hex(8), _hash(raw), who, "", now, now + SESSION_DAYS * 86400, now, device[:120]))
        return raw

    def check(self, raw: str) -> tuple[str, str | None] | None:
        """(who, a fresh cookie when this one is due to rotate) for a live session; None otherwise."""
        row = self.db.one("SELECT * FROM signin_sessions WHERE token_hash=?", (_hash(raw),))
        now = self.clock()
        if not row:
            return None
        if row["expires"] <= now:
            self.revoke(row["id"])
            return None
        if now - row["rotated"] < ROTATE_AFTER_S:
            return row["email"], None
        fresh = secrets.token_urlsafe(32)
        self.db.execute("UPDATE signin_sessions SET token_hash=?, rotated=? WHERE id=?", (_hash(fresh), now, row["id"]))
        return row["email"], fresh

    def seconds_left(self, raw: str) -> int:
        row = self.db.one("SELECT expires FROM signin_sessions WHERE token_hash=?", (_hash(raw),))
        return max(0, int(row["expires"] - self.clock())) if row else 0

    def end(self, raw: str) -> None:
        self.db.execute("DELETE FROM signin_sessions WHERE token_hash=?", (_hash(raw),))

    def end_all(self) -> None:
        self.db.execute("DELETE FROM signin_sessions")

    def revoke(self, session_id: str) -> bool:
        return self.db.execute("DELETE FROM signin_sessions WHERE id=?", (session_id,)).rowcount > 0

    def list(self) -> list[dict]:
        now = self.clock()
        return [{k: r[k] for k in ("id", "created", "expires", "device")}
                for r in self.db.all("SELECT * FROM signin_sessions WHERE expires > ? ORDER BY created DESC", (now,))]


class Tokens:
    """Bearer tokens for apps, made in Settings. The raw token is shown once; only its hash is kept."""

    PREFIX = "mx_"

    def __init__(self, db, clock: Callable[[], float] = time.time):
        self.db, self.clock = db, clock
        db.script("""CREATE TABLE IF NOT EXISTS hub_tokens (
            id TEXT PRIMARY KEY, label TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
            created REAL NOT NULL, last_used REAL)""")

    def create(self, label: str) -> tuple[str, str]:
        """(id, raw token)."""
        label = " ".join(label.split())[:60] or "App"
        raw, tid = self.PREFIX + secrets.token_urlsafe(30), secrets.token_hex(6)
        self.db.execute("INSERT INTO hub_tokens VALUES (?,?,?,?,NULL)", (tid, label, _hash(raw), self.clock()))
        return tid, raw

    def check(self, raw: str) -> bool:
        if not raw.startswith(self.PREFIX):
            return False
        row = self.db.one("SELECT id FROM hub_tokens WHERE token_hash=?", (_hash(raw),))
        if row:
            self.db.execute("UPDATE hub_tokens SET last_used=? WHERE id=?", (self.clock(), row["id"]))
        return row is not None

    def revoke(self, tid: str) -> bool:
        return self.db.execute("DELETE FROM hub_tokens WHERE id=?", (tid,)).rowcount > 0

    def list(self) -> list[dict]:
        return self.db.all("SELECT id, label, created, last_used FROM hub_tokens ORDER BY created DESC")


class Passcode:
    """The owner's passcode, as an scrypt hash in the hub database."""

    def __init__(self, db):
        self.db = db
        db.script("CREATE TABLE IF NOT EXISTS hub_auth(key TEXT PRIMARY KEY, value TEXT)")

    @property
    def is_set(self) -> bool:
        return self.db.one("SELECT value FROM hub_auth WHERE key='passcode'") is not None

    def set(self, passcode: str) -> None:
        if len(passcode) < MIN_PASSCODE:
            raise ValueError(f"use at least {MIN_PASSCODE} characters")
        self.db.execute("INSERT OR REPLACE INTO hub_auth VALUES ('passcode', ?)", (hash_passcode(passcode),))

    def matches(self, passcode: str) -> bool:
        row = self.db.one("SELECT value FROM hub_auth WHERE key='passcode'")
        return bool(row) and passcode_matches(passcode, row["value"])


def set_cookie(request: Request, response: Response, raw: str, max_age: int) -> None:
    response.set_cookie(COOKIE, raw, max_age=max_age, httponly=True, secure=request.url.scheme == "https",
                        samesite="strict", path="/")


class Attempts:
    """At most ATTEMPTS_PER_MINUTE passcode tries per client address."""

    def __init__(self, clock: Callable[[], float]):
        self.clock, self._seen = clock, {}

    def allow(self, who: str) -> bool:
        now = self.clock()
        recent = [t for t in self._seen.get(who, ()) if now - t < 60] + [now]
        self._seen = {**self._seen, who: recent}
        return len(recent) <= ATTEMPTS_PER_MINUTE


class PasscodeIn(BaseModel):
    passcode: str


class ChangeIn(BaseModel):
    old: str
    new: str


class TokenIn(BaseModel):
    label: str = "iPhone Shortcut"


def register(app, s, api, sessions: Sessions, tokens: Tokens, passcode: Passcode, clock: Callable[[], float],
             demo: bool = False, mac_keychain: Callable[[str], None] | None = None) -> None:
    attempts = Attempts(clock)

    def client(request: Request) -> str:
        return request.client.host if request.client else "?"

    def signed_in(request: Request) -> bool:
        raw = request.cookies.get(COOKIE)
        return bool(raw and sessions.check(raw))

    @app.get("/api/auth/state")
    def auth_state(request: Request):
        """For the sign-in page: is there a passcode yet, can this browser set it, is it signed in."""
        return {"passcode_set": passcode.is_set, "signed_in": signed_in(request),
                "can_set_up": is_loopback(client(request)), "demo": demo}

    @app.post("/api/auth/setup")
    def auth_setup(body: PasscodeIn, request: Request, response: Response):
        """First run only, and only from this computer."""
        if passcode.is_set:
            raise HTTPException(409, "a passcode is already set; sign in with it")
        if not is_loopback(client(request)):
            raise HTTPException(403, "set the first passcode on the computer that runs Maxie (open http://127.0.0.1:8780)")
        try:
            passcode.set(body.passcode)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        raw = sessions.start(OWNER, request.headers.get("user-agent", ""))
        set_cookie(request, response, raw, SESSION_DAYS * 86400)
        s.audit.log("hub", "passcode_set", {}, "first run", "ok")
        return {"ok": True}

    @app.post("/api/auth/login")
    def auth_login(body: PasscodeIn, request: Request, response: Response):
        if not attempts.allow(client(request)):
            raise HTTPException(429, "too many tries; wait a minute")
        if not passcode.is_set:
            raise HTTPException(409, "no passcode yet; set one first")
        if not passcode.matches(body.passcode):
            s.audit.log("hub", "signin_refused", {}, "wrong passcode", "refused")
            raise HTTPException(401, "that passcode isn't right")
        raw = sessions.start(OWNER, request.headers.get("user-agent", ""))
        set_cookie(request, response, raw, SESSION_DAYS * 86400)
        s.audit.log("hub", "signin", {}, "passcode", "session started")
        return {"ok": True}

    @app.post("/api/auth/logout")
    def auth_logout(request: Request, response: Response):
        raw = request.cookies.get(COOKIE)
        if raw:
            sessions.end(raw)
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")
        return {"ok": True}

    @app.post("/api/auth/passcode", dependencies=api)
    def auth_change(body: ChangeIn):
        """Change the passcode; every browser has to sign in again, apps keep their tokens."""
        if not passcode.matches(body.old):
            raise HTTPException(401, "the current passcode isn't right")
        try:
            passcode.set(body.new)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        sessions.end_all()
        s.audit.log("user", "passcode_changed", {}, "Settings", "every browser signed out")
        return {"ok": True}

    @app.get("/api/auth/sessions", dependencies=api)
    def auth_sessions():
        return {"sessions": sessions.list(), "tokens": tokens.list(), "mac_app": mac_keychain is not None}

    @app.delete("/api/auth/sessions/{session_id}", dependencies=api)
    def auth_revoke_session(session_id: str):
        if not sessions.revoke(session_id):
            raise HTTPException(404, "no such session")
        s.audit.log("user", "session_revoked", {"session": session_id}, "Settings", "ended")
        return {"ok": True}

    @app.post("/api/auth/tokens", dependencies=api)
    def auth_new_token(body: TokenIn):
        """A token for an app. The raw value is in this answer only; copy it now."""
        tid, raw = tokens.create(body.label)
        s.audit.log("user", "token_created", {"id": tid, "label": body.label}, "Settings", "shown once")
        return {"id": tid, "token": raw}

    @app.delete("/api/auth/tokens/{tid}", dependencies=api)
    def auth_revoke_token(tid: str):
        if not tokens.revoke(tid):
            raise HTTPException(404, "no such token")
        s.audit.log("user", "token_revoked", {"id": tid}, "Settings", "revoked")
        return {"ok": True}

    @app.post("/api/auth/mac-app", dependencies=api)
    def auth_mac_app():
        """macOS only: a token for the notch app and the voice helper, written to the login Keychain
        (service "maxie", account HUB_TOKEN), where they read it."""
        if mac_keychain is None:
            raise HTTPException(409, "the Mac app only runs on macOS")
        tid, raw = tokens.create("Mac app")
        try:
            mac_keychain(raw)
        except (OSError, RuntimeError, ValueError) as e:
            tokens.revoke(tid)
            raise HTTPException(500, f"couldn't write the Keychain: {e}") from e
        s.audit.log("user", "token_created", {"id": tid, "label": "Mac app"}, "Settings", "written to the Keychain")
        return {"ok": True, "id": tid}
