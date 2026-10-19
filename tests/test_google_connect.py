"""Settings › Google: your own OAuth client, then "Connect Google": authorization code + PKCE, back to the hub's
own port, as many accounts as you like by label, refresh, a clear "Reconnect" when Google says no, and Disconnect
that revokes. Google is a MockTransport; nothing leaves the test."""

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi.testclient import TestClient
from test_settings_api import TOKEN, Deps, H

from maxie.hands.google import JOB, MAIN
from maxie.hands.google_real import SCOPES, LiveGoogle, OAuth, access_for, parse_client, userinfo
from maxie.hub.api import create_app

GOOD_CLIENT = {"installed": {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-fake",
                             "redirect_uris": ["http://localhost"]}}


class FakeGoogle:
    """The token, userinfo, Gmail and revoke endpoints, enough for a whole connect, use and disconnect."""

    def __init__(self, email="alex@example.com", refresh_status=200):
        self.email, self.refresh_status, self.seen, self.revoked = email, refresh_status, [], []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.seen.append(req)
        path = req.url.path
        if path == "/token":
            form = parse_qs(req.content.decode())
            if form["grant_type"] == ["authorization_code"]:
                return httpx.Response(200, json={"access_token": "at1", "refresh_token": "rt-secret", "expires_in": 3600,
                                                 "scope": " ".join(SCOPES["full"])})
            if self.refresh_status != 200:
                return httpx.Response(self.refresh_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "at2", "expires_in": 3600})
        if path == "/revoke":
            self.revoked.append(parse_qs(req.content.decode())["token"][0])
            return httpx.Response(200)
        if path == "/v1/userinfo":
            return httpx.Response(200, json={"email": self.email, "name": "Alex Rivera", "picture": "https://p.example/a"})
        if path.endswith("/messages"):
            return httpx.Response(200, json={"messages": []})
        return httpx.Response(404)


def hub(make_services, deps, google):
    s = make_services()
    s.mode["demo"] = False  # the routes act like a real hub; services stay fake
    s.google = LiveGoogle(deps.secret, deps.store)
    http = httpx.Client(transport=httpx.MockTransport(google))
    app = create_app(s, TOKEN, scheduler=False, deps={"secret": deps.secret, "store": deps.store, "delete": deps.delete,
                                                     "run_cmd": deps.run_cmd, "http": http})
    return TestClient(app, base_url="http://127.0.0.1:8780"), s


def connect(c, label=MAIN):
    r = c.post("/api/google/connect", json={"label": label}, headers=H)
    assert r.status_code == 200, r.text
    return parse_qs(urlparse(r.json()["url"]).query)


def test_the_client_file_is_parsed_and_its_values_never_come_back(make_services):
    deps = Deps()
    c, _ = hub(make_services, deps, FakeGoogle())
    r = c.post("/api/google/client", json={"json_text": json.dumps(GOOD_CLIENT)}, headers=H)
    assert r.status_code == 200 and "GOCSPX" not in r.text
    assert deps.secrets["GOOGLE_CLIENT_ID"] == "123-abc.apps.googleusercontent.com"
    assert c.post("/api/google/client", json={"json_text": "{nope"}, headers=H).status_code == 400
    assert c.post("/api/google/client", json={"client_id": "x", "client_secret": "y"}, headers=H).status_code == 400
    assert parse_client(json.dumps({"web": GOOD_CLIENT["installed"]}))[0].endswith(".apps.googleusercontent.com")


def test_connect_needs_a_client_first_and_says_so(make_services):
    c, _ = hub(make_services, Deps(), FakeGoogle())
    r = c.post("/api/google/connect", json={"label": MAIN}, headers=H)
    assert r.status_code == 409 and "OAuth client" in r.json()["detail"]


def test_consent_is_pkce_s256_offline_minimal_and_comes_back_to_the_hubs_own_port(make_services):
    deps = Deps({"GOOGLE_CLIENT_ID": "cid.apps.googleusercontent.com", "GOOGLE_CLIENT_SECRET": "csec-123456"})
    c, _ = hub(make_services, deps, FakeGoogle())
    q = connect(c, JOB)
    assert q["redirect_uri"] == ["http://127.0.0.1:8780/oauth/google/callback"]
    assert q["code_challenge_method"] == ["S256"] and len(q["code_challenge"][0]) == 43
    assert q["access_type"] == ["offline"] and q["response_type"] == ["code"]
    scopes = set(q["scope"][0].split())
    assert "https://www.googleapis.com/auth/gmail.readonly" in scopes and not any("compose" in x or "drive" in x for x in scopes)
    assert {"openid", "email", "profile"} <= scopes
    assert access_for(MAIN) == "full" and access_for("school") == "read"


def test_connect_is_refused_from_another_device(make_services):
    deps = Deps({"GOOGLE_CLIENT_ID": "cid.apps.googleusercontent.com", "GOOGLE_CLIENT_SECRET": "csec-123456"})
    _, s = hub(make_services, deps, FakeGoogle())
    phone = TestClient(create_app(s, TOKEN, scheduler=False, deps={"secret": deps.secret, "store": deps.store}),
                       base_url="https://laptop.tailnet-example.ts.net:8780")
    r = phone.post("/api/google/connect", json={"label": MAIN}, headers=H)
    assert r.status_code == 409 and "computer that runs Maxie" in r.json()["detail"]


def test_the_callback_swaps_the_code_with_the_verifier_and_stores_the_account(make_services):
    deps = Deps({"GOOGLE_CLIENT_ID": "cid.apps.googleusercontent.com", "GOOGLE_CLIENT_SECRET": "csec-123456"})
    google = FakeGoogle()
    c, s = hub(make_services, deps, google)
    q = connect(c)
    r = c.get("/oauth/google/callback", params={"state": q["state"][0], "code": "the-code"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings.html?connected=main#google"
    swap = parse_qs(next(x for x in google.seen if x.url.path == "/token").content.decode())
    challenge = base64.urlsafe_b64encode(hashlib.sha256(swap["code_verifier"][0].encode()).digest()).rstrip(b"=").decode()
    assert challenge == q["code_challenge"][0] and swap["redirect_uri"] == q["redirect_uri"]
    stored = json.loads(deps.secrets["GOOGLE_ACCOUNTS"])
    assert stored[0]["label"] == "main" and stored[0]["email"] == "alex@example.com" and stored[0]["refresh_token"] == "rt-secret"
    setup = c.get("/api/setup", headers=H).json()
    assert setup["google"]["accounts"][0]["email"] == "alex@example.com" and "rt-secret" not in json.dumps(setup)
    google_item = next(i for i in setup["items"] if i["id"] == "google")
    assert google_item["state"] == "ok" and "alex@example.com (main)" in google_item["line"]
    assert s.google.labels() == ["main"]  # live: no restart
    again = c.get("/oauth/google/callback", params={"state": q["state"][0], "code": "the-code"})
    assert "expired or was already used" in again.text  # one use per state


def test_a_bad_or_cancelled_callback_saves_nothing(make_services):
    deps = Deps({"GOOGLE_CLIENT_ID": "cid.apps.googleusercontent.com", "GOOGLE_CLIENT_SECRET": "csec-123456"})
    c, _ = hub(make_services, deps, FakeGoogle())
    assert "expired" in c.get("/oauth/google/callback", params={"state": "made-up", "code": "x"}).text
    q = connect(c)
    assert "Nothing was saved" in c.get("/oauth/google/callback", params={"state": q["state"][0], "error": "access_denied"}).text
    assert "GOOGLE_ACCOUNTS" not in deps.secrets


def test_two_accounts_live_side_by_side_and_disconnect_revokes_one(make_services):
    deps = Deps({"GOOGLE_CLIENT_ID": "cid.apps.googleusercontent.com", "GOOGLE_CLIENT_SECRET": "csec-123456"})
    google = FakeGoogle()
    c, s = hub(make_services, deps, google)
    for label, email in ((MAIN, "alex@example.com"), (JOB, "alex.jobs@example.com")):
        google.email = email
        q = connect(c, label)
        c.get("/oauth/google/callback", params={"state": q["state"][0], "code": "c"}, follow_redirects=False)
    accounts = c.get("/api/connections", headers=H).json()["google"]["accounts"]
    assert sorted(accounts) == ["job", "main"] and accounts["job"]["email"] == "alex.jobs@example.com"
    assert c.post("/api/google/accounts/job/disconnect", headers=H).json() == {"ok": True, "revoked": True}
    assert google.revoked == ["rt-secret"] and s.google.labels() == ["main"]
    assert c.post("/api/google/accounts/job/disconnect", headers=H).status_code == 404


def test_a_revoked_refresh_token_becomes_reconnect_not_a_crash(clock):
    stash = {"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "csec",
             "GOOGLE_ACCOUNTS": json.dumps([{"label": "main", "email": "alex@example.com", "refresh_token": "old"}])}
    g = LiveGoogle(stash.get, stash.__setitem__, httpx.Client(transport=httpx.MockTransport(FakeGoogle(refresh_status=400))))
    try:
        g.search(MAIN, "")
        raise AssertionError("should have said reconnect")
    except Exception as e:  # noqa: BLE001
        assert type(e).__name__ == "NotConnectedError" and "Reconnect" in str(e)
    assert "Reconnect" in json.loads(stash["GOOGLE_ACCOUNTS"])[0]["error"]


def test_nothing_connected_says_so_plainly(clock):
    g = LiveGoogle({}.get, lambda *a: None)
    assert not g.connected
    try:
        g.search(MAIN, "")
    except Exception as e:  # noqa: BLE001
        assert "isn't connected yet" in str(e)


def test_userinfo_reads_name_and_photo_and_never_raises():
    ok = httpx.Client(transport=httpx.MockTransport(FakeGoogle()))
    assert userinfo(ok, {"Authorization": "Bearer at"})["name"] == "Alex Rivera"
    down = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert userinfo(down, {"Authorization": "Bearer at"}) == {}
    assert OAuth("cid", "csec").start(MAIN, "http://127.0.0.1:1/cb")[0].startswith("https://accounts.google.com/")
