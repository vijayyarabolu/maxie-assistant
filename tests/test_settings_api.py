"""Endpoints behind Settings: account (key status, never the key), connections, place lookup, voices,
restore. Secret store writes, shell commands and HTTP are injected fakes."""

import json

import httpx
from fastapi.testclient import TestClient

from maxie.hub.api import create_app

TOKEN = "t0ken"
H = {"Authorization": f"Bearer {TOKEN}"}
SERVE = ("https://laptop.tailnet-example.ts.net (tailnet only)\n|-- / proxy http://127.0.0.1:8765\n\n"
         "https://laptop.tailnet-example.ts.net:8780 (tailnet only)\n|-- / proxy http://127.0.0.1:8780\n")


class Deps:
    def __init__(self, secrets=None, serve_out=SERVE):
        self.secrets, self.written, self.commands, self.serve_out = dict(secrets or {}), [], [], serve_out

    def secret(self, name):
        return self.secrets.get(name)

    def store(self, account, value):
        self.written.append((account, value))
        self.secrets[account] = value

    def delete(self, account):
        had = account in self.secrets
        self.secrets.pop(account, None)
        self.written.append((account, None))
        return had

    def run_cmd(self, args):
        self.commands.append(args)
        if args[:3] == ["tailscale", "serve", "status"]:
            return 0, self.serve_out
        return 0, "ok"


def geo_transport(req: httpx.Request) -> httpx.Response:
    assert req.url.host == "geocoding-api.open-meteo.com" and req.url.params["name"] == "Madison"
    return httpx.Response(200, json={"results": [{"name": "Madison", "admin1": "Wisconsin", "country_code": "US",
                                                  "latitude": 39.1, "longitude": -94.58, "timezone": "America/Chicago"}]})


def client(make_services, deps, http=None):
    s = make_services()
    app = create_app(s, TOKEN, scheduler=False, deps={"secret": deps.secret, "store": deps.store,
                                                     "delete": deps.delete, "run_cmd": deps.run_cmd,
                                                     "http": http or httpx.Client(transport=httpx.MockTransport(geo_transport))})
    return TestClient(app), s


def test_account_shows_key_status_and_spend_but_never_the_key(make_services):
    deps = Deps({"ANTHROPIC_API_KEY": "sk-ant-secret-123"})
    c, s = client(make_services, deps)
    a = c.get("/api/account", headers=H).json()
    assert a["api_key"] is True and a["cap_usd"] == 95.0 and "month_usd" in a and "today_usd" in a
    assert "sk-ant" not in json.dumps(a)
    r = c.post("/api/account/key", json={"key": "sk-ant-new-4567890123"}, headers=H)
    assert r.status_code == 200 and r.json()["restart_needed"] is False  # used at once, no restart
    assert deps.written == [("ANTHROPIC_API_KEY", "sk-ant-new-4567890123")]
    assert all("sk-ant" not in json.dumps(row) for row in s.audit.recent(20))
    assert c.post("/api/account/key", json={"key": "nope"}, headers=H).status_code == 400


def test_connections_report_google_jobhunt_and_tailscale(make_services):
    deps = Deps({"GOOGLE_CLIENT_ID": "id", "GOOGLE_CLIENT_SECRET": "s", "JOBHUNT_TOKEN": "t"})
    c, _ = client(make_services, deps)
    r = c.get("/api/connections", headers=H).json()
    g = {k: v for k, v in r["google"].items() if k not in ("accounts", "list")}  # accounts: test_google_connect.py
    assert g == {"client": True, "login_status": "", "refused": []}
    assert r["jobhunt"]["token"] is True and r["resumegen"]["token"] is False
    assert r["tailscale"] == {"served": True, "url": "https://laptop.tailnet-example.ts.net:8780"}


def test_tailscale_expose_runs_the_serve_command_once(make_services):
    deps = Deps(serve_out="")
    c, _ = client(make_services, deps)
    assert c.get("/api/connections", headers=H).json()["tailscale"] == {"served": False, "url": None}
    r = c.post("/api/connections/tailscale", headers=H)
    assert r.status_code == 200
    assert ["tailscale", "serve", "--bg", "--https=8780", "http://127.0.0.1:8780"] in deps.commands


def test_place_lookup_sets_city_coordinates_and_timezone(make_services):
    c, s = client(make_services, Deps())
    r = c.post("/api/place", json={"query": "Madison"}, headers=H)
    assert r.status_code == 200 and r.json()["place"] == "Madison, Wisconsin"
    assert (s.settings.weather_place, s.settings.weather_lat, s.settings.weather_lon, s.settings.timezone) == \
        ("Madison, Wisconsin", 39.1, -94.58, "America/Chicago")


def test_voices_list_and_restore(make_services, tmp_path):
    c, s = client(make_services, Deps())
    v = c.get("/api/voice/voices", headers=H).json()
    assert v["current"] == "af_heart" and any(x["id"] == "af_heart" for x in v["voices"])
    s.backup.list_backups = lambda: ["2026-10-14T03-00", "2026-10-13T03-00"]
    assert c.get("/api/backup/list", headers=H).json()["backups"] == ["2026-10-14T03-00", "2026-10-13T03-00"]
    s.backup.restore = lambda name, into: [into / "claude" / "hub.sqlite"]
    r = c.post("/api/backup/restore", json={"name": "2026-10-14T03-00", "folder": str(tmp_path / "out")}, headers=H)
    assert r.status_code == 200 and r.json()["databases"] == [str(tmp_path / "out" / "claude" / "hub.sqlite")]
    assert c.post("/api/backup/restore", json={"name": "../x", "folder": str(tmp_path)}, headers=H).status_code == 400


def test_resources_report_ram_and_cpu_per_service(make_services):
    deps = Deps()
    s = make_services()
    pids = {"maxie.hub.api": [11], "maxie.voice": [22], "deploy/laya_serve.py": []}
    app = create_app(s, TOKEN, scheduler=False, deps={
        "secret": deps.secret, "store": deps.store, "delete": deps.delete,
        "run_cmd": deps.run_cmd, "http": httpx.Client(transport=httpx.MockTransport(geo_transport)),
        "pids_fn": lambda pattern, exact: [33] if exact == "Maxie" else pids.get(pattern, []),
        "ps_fn": lambda p: [(p[0], {11: 93.0, 22: 1259.0, 33: 103.0}[p[0]], {11: 0.3, 22: 1.8, 33: 1.1}[p[0]])]})
    r = TestClient(app).get("/api/resources", headers=H).json()
    by = {x["name"]: x for x in r["services"]}
    assert by["voice"]["rss_mb"] == 1259.0 and by["voice"]["cpu_pct"] == 1.8 and by["laya"]["running"] is False
    assert r["total_rss_mb"] == 93.0 + 1259.0 + 103.0 and "laya off" in r["line"] and "voice 1259 MB / 2%" in r["line"]


def test_resources_hub_sees_its_own_pid(monkeypatch):
    """The snapshot runs inside the hub: its own pid must count, or Settings says "hub off"."""
    import os
    import subprocess
    import sys

    from maxie.hub import resources

    class R:
        stdout = "4242\n"  # macOS pgrep never lists its parent, so the hub is missing from its own probe

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    monkeypatch.setattr(sys, "argv", ["/x/maxie/hub/api.py"])
    assert resources._pids("maxie.hub.api", None) == [4242, os.getpid()]
    assert resources._pids("maxie.voice", None) == [4242]
