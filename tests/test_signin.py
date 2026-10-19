"""Signing in: a passcode set on first run (only from this computer), then a strict session cookie that rotates
and lasts 30 days; app tokens made in Settings for the iPhone Shortcut and the Mac app; HUB_TOKEN still works."""

import json

import pytest
from fastapi.testclient import TestClient

from maxie.hub.api import create_app
from maxie.hub.signin import (
    COOKIE,
    ROTATE_AFTER_S,
    SESSION_DAYS,
    hash_passcode,
    is_loopback,
    passcode_matches,
)

PASS = "correct horse"


class Now:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def now():
    return Now()


def hub(make_services, now, token=None, **deps):
    s = make_services()
    app = create_app(s, token, scheduler=False, deps={"clock": now, "secret": lambda n: None, **deps})
    return TestClient(app), s


def set_up(c):
    r = c.post("/api/auth/setup", json={"passcode": PASS})
    assert r.status_code == 200
    return r


def test_scrypt_hash_round_trips_and_never_holds_the_passcode():
    stored = hash_passcode(PASS)
    assert stored.startswith("scrypt$") and PASS not in stored
    assert passcode_matches(PASS, stored) and not passcode_matches("wrong", stored)
    assert not passcode_matches(PASS, "garbage") and not passcode_matches(PASS, "md5$aa$bb")


def test_first_run_sets_a_passcode_and_signs_this_browser_in(make_services, now):
    c, _ = hub(make_services, now)
    assert c.get("/api/settings").status_code == 401
    st = c.get("/api/auth/state").json()
    assert st == {"passcode_set": False, "signed_in": False, "can_set_up": True, "demo": False}
    r = set_up(c)
    cookie = r.headers["set-cookie"]
    assert f"{COOKIE}=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert f"Max-Age={SESSION_DAYS * 86400}" in cookie
    assert c.get("/api/settings").status_code == 200
    assert c.post("/api/auth/setup", json={"passcode": "another one"}).status_code == 409  # only once


def test_the_first_passcode_can_only_be_set_from_this_computer(make_services, now):
    assert is_loopback("127.0.0.1") and is_loopback("::1") and not is_loopback("100.64.0.7")
    s = make_services()
    app = create_app(s, None, scheduler=False, deps={"clock": now, "secret": lambda n: None})
    far = TestClient(app, client=("100.64.0.7", 5000))
    assert far.get("/api/auth/state").json()["can_set_up"] is False
    assert far.post("/api/auth/setup", json={"passcode": PASS}).status_code == 403


def test_short_passcodes_are_refused(make_services, now):
    c, _ = hub(make_services, now)
    assert c.post("/api/auth/setup", json={"passcode": "123"}).status_code == 400


def test_signing_in_with_the_passcode_and_a_wrong_one(make_services, now):
    c, _ = hub(make_services, now)
    set_up(c)
    other = TestClient(c.app)
    assert other.post("/api/auth/login", json={"passcode": "nope nope"}).status_code == 401
    assert other.get("/api/settings").status_code == 401
    assert other.post("/api/auth/login", json={"passcode": PASS}).status_code == 200
    assert other.get("/api/settings").status_code == 200


def test_too_many_tries_wait_a_minute(make_services, now):
    c, _ = hub(make_services, now)
    set_up(c)
    codes = [c.post("/api/auth/login", json={"passcode": "nope nope"}).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429
    now.t += 61
    assert c.post("/api/auth/login", json={"passcode": PASS}).status_code == 200


def test_the_cookie_rotates_and_the_old_one_dies(make_services, now):
    c, _ = hub(make_services, now)
    first = set_up(c).cookies[COOKIE]
    now.t += ROTATE_AFTER_S + 1
    r = c.get("/api/settings")
    fresh = r.cookies.get(COOKIE)
    assert r.status_code == 200 and fresh and fresh != first
    stale = TestClient(c.app, cookies={COOKIE: first})
    assert stale.get("/api/settings").status_code == 401


def test_a_session_ends_after_30_days(make_services, now):
    c, _ = hub(make_services, now)
    raw = set_up(c).cookies[COOKIE]
    now.t += SESSION_DAYS * 86400 + 1
    assert TestClient(c.app, cookies={COOKIE: raw}).get("/api/settings").status_code == 401


def test_app_tokens_are_shown_once_work_as_bearer_and_can_be_revoked(make_services, now):
    c, s = hub(make_services, now)
    set_up(c)
    made = c.post("/api/auth/tokens", json={"label": "iPhone Shortcut"}).json()
    raw = made["token"]
    assert raw.startswith("mx_")
    listed = c.get("/api/auth/sessions").json()["tokens"]
    assert [t["label"] for t in listed] == ["iPhone Shortcut"] and raw not in json.dumps(listed)
    app_only = TestClient(c.app)
    assert app_only.get("/api/settings", headers={"Authorization": f"Bearer {raw}"}).status_code == 200
    assert app_only.get("/api/settings", headers={"Authorization": "Bearer mx_made_up"}).status_code == 401
    assert c.delete(f"/api/auth/tokens/{made['id']}").status_code == 200
    assert app_only.get("/api/settings", headers={"Authorization": f"Bearer {raw}"}).status_code == 401
    assert raw not in json.dumps(s.audit.recent(50))


def test_hub_token_still_works(make_services, now):
    c, _ = hub(make_services, now, token="fixed-token")
    assert c.get("/api/settings", headers={"Authorization": "Bearer fixed-token"}).status_code == 200
    assert c.get("/api/settings", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_changing_the_passcode_signs_every_browser_out_but_keeps_app_tokens(make_services, now):
    c, _ = hub(make_services, now)
    set_up(c)
    raw = c.post("/api/auth/tokens", json={"label": "Shortcut"}).json()["token"]
    assert c.post("/api/auth/passcode", json={"old": "wrong one", "new": "new passcode"}).status_code == 401
    assert c.post("/api/auth/passcode", json={"old": PASS, "new": "new passcode"}).status_code == 200
    assert c.get("/api/settings").status_code == 401
    assert TestClient(c.app).get("/api/settings", headers={"Authorization": f"Bearer {raw}"}).status_code == 200
    assert c.post("/api/auth/login", json={"passcode": "new passcode"}).status_code == 200


def test_signing_out_ends_this_session(make_services, now):
    c, _ = hub(make_services, now)
    set_up(c)
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/settings").status_code == 401


def test_revoking_a_browser_from_settings(make_services, now):
    c, _ = hub(make_services, now)
    set_up(c)
    now.t += 5
    other = TestClient(c.app)
    other.post("/api/auth/login", json={"passcode": PASS})
    sessions = c.get("/api/auth/sessions").json()["sessions"]
    assert len(sessions) == 2  # newest first: the other browser
    assert c.delete(f"/api/auth/sessions/{sessions[0]['id']}").status_code == 200
    assert other.get("/api/settings").status_code == 401
    assert c.get("/api/settings").status_code == 200


def test_the_mac_app_button_writes_the_keychain_only_where_there_is_one(make_services, now):
    c, _ = hub(make_services, now, mac_keychain=None)
    set_up(c)
    assert c.get("/api/auth/sessions").json()["mac_app"] is False
    assert c.post("/api/auth/mac-app").status_code == 409
    written = []
    c2, _ = hub(make_services, now, mac_keychain=written.append)
    set_up(c2)
    assert c2.post("/api/auth/mac-app").status_code == 200
    assert written and written[0].startswith("mx_")
    assert TestClient(c2.app).get("/api/settings", headers={"Authorization": f"Bearer {written[0]}"}).status_code == 200


def test_the_sign_in_and_settings_pages_load_without_signing_in(make_services, now):
    c, _ = hub(make_services, now)
    for page in ("/signin.html", "/settings.html", "/settings.js"):
        r = c.get(page)
        assert r.status_code == 200, page
    assert "passcode" in c.get("/signin.html").text.lower()
