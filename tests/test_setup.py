"""Settings › Setup: a status per part (ok, missing, failing, off, demo), Test buttons that never crash, pasted
keys that are checked, stored and used at once but never sent back, and Mac-only parts off with a note elsewhere."""

import json

import httpx
from fastapi.testclient import TestClient
from test_settings_api import TOKEN, Deps, H

from maxie.config import delete_secret, secret, secret_source, store_secret
from maxie.hub.api import create_app


class Models:
    def __init__(self, fail=False):
        self.fail = fail

    def list(self, **kw):
        if self.fail:
            raise RuntimeError("401 invalid x-api-key")
        return []


def hub(make_services, deps, system="linux", demo=False):
    s = make_services()
    s.mode["demo"] = demo
    app = create_app(s, TOKEN, scheduler=False, deps={"secret": deps.secret, "store": deps.store, "delete": deps.delete,
                                                     "run_cmd": deps.run_cmd, "system": system,
                                                     "http": httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))})
    return TestClient(app), s


def items(c):
    return {i["id"]: i for i in c.get("/api/setup", headers=H).json()["items"]}


def test_a_fresh_hub_says_what_is_missing_and_what_is_optional(make_services):
    c, _ = hub(make_services, Deps())
    it = items(c)
    assert it["anthropic"]["state"] == "missing" and "still work" in it["anthropic"]["line"]
    assert it["google"]["state"] == "missing" and it["google"]["optional"]
    assert it["jobs"]["state"] in ("off", "ok")
    for mac_only in ("mac", "voice", "notch"):
        assert it[mac_only]["state"] == "off" and it[mac_only]["mac_only"] and "Linux" in it[mac_only]["line"]


def test_on_a_mac_the_mac_parts_are_on(make_services):
    c, _ = hub(make_services, Deps(), system="darwin")
    assert items(c)["mac"]["state"] == "ok"


def test_a_pasted_key_is_checked_saved_used_at_once_and_never_returned(make_services):
    deps = Deps()
    c, s = hub(make_services, deps)
    bad = c.post("/api/setup/secret", json={"name": "ANTHROPIC_API_KEY", "value": "hello"}, headers=H)
    assert bad.status_code == 400 and "sk-ant-" in bad.json()["detail"]
    r = c.post("/api/setup/secret", json={"name": "ANTHROPIC_API_KEY", "value": "sk-ant-api03-abcdefghijkl"}, headers=H)
    assert r.status_code == 200 and "sk-ant-api03" not in r.text
    assert deps.secrets["ANTHROPIC_API_KEY"] == "sk-ant-api03-abcdefghijkl"
    assert s.brain.client is not None and s.router.haiku is not None  # no restart
    assert all("sk-ant" not in json.dumps(row) for row in s.audit.recent(20))
    assert c.get("/api/setup", headers=H).json()["keys"]["ANTHROPIC_API_KEY"] is True
    assert c.post("/api/setup/secret", json={"name": "HUB_TOKEN", "value": "x" * 20}, headers=H).status_code == 400


def test_the_test_button_reports_a_bad_key_as_failing(make_services):
    deps = Deps({"ANTHROPIC_API_KEY": "sk-ant-api03-abcdefghijkl"})
    c, s = hub(make_services, deps)
    s.anthropic_sync = type("Client", (), {"models": Models(fail=True)})()
    r = c.post("/api/setup/test/anthropic", headers=H).json()
    assert r["ok"] is False and "invalid x-api-key" in r["line"]
    assert {i["id"]: i for i in r["items"]}["anthropic"]["state"] == "failing"
    s.anthropic_sync = type("Client", (), {"models": Models()})()
    assert c.post("/api/setup/test/anthropic", headers=H).json()["ok"] is True
    assert items(c)["anthropic"]["state"] == "ok"


def test_tests_without_the_thing_say_so_instead_of_crashing(make_services):
    c, _ = hub(make_services, Deps())
    r = c.post("/api/setup/test/google", headers=H).json()
    assert r["ok"] is False and "No Google account" in r["line"]
    assert c.post("/api/setup/test/nothing", headers=H).status_code == 404


def test_the_demo_hub_says_demo_and_calls_nothing(make_services):
    c, _ = hub(make_services, Deps(), demo=True)
    it = items(c)
    assert it["anthropic"]["state"] == it["google"]["state"] == "demo"
    assert c.post("/api/setup/test/weather", headers=H).json()["line"].startswith("Demo hub")
    assert c.post("/api/google/connect", json={"label": "main"}, headers=H).status_code == 409


def test_secrets_file_is_private_and_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("MAXIE_DATA", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store_secret("ANTHROPIC_API_KEY", "sk-ant-from-file-123")
    f = tmp_path / "secrets.json"
    assert f.stat().st_mode & 0o777 == 0o600
    assert secret("ANTHROPIC_API_KEY") == "sk-ant-from-file-123" and secret_source("ANTHROPIC_API_KEY") == "file"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env-456")
    assert secret("ANTHROPIC_API_KEY") == "sk-ant-from-env-456" and secret_source("ANTHROPIC_API_KEY") == "env"
    assert delete_secret("ANTHROPIC_API_KEY") and not delete_secret("ANTHROPIC_API_KEY")
    assert "sk-ant" not in f.read_text()


def test_a_new_city_reaches_the_weather_tool_without_a_restart(make_services):
    from maxie.config import Settings
    from maxie.hands.weather import Weather
    st = Settings(weather_place="")
    w = Weather(settings=st, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    assert "No city is set yet" in w.forecast()
    st.weather_place, st.weather_lat = "Lisbon", 38.7
    assert w.place == "Lisbon" and w.lat == 38.7
