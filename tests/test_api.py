import json

from fastapi.testclient import TestClient

from maxie.hub.api import create_app
from tests.fakes import FakeAnthropic, turn

TOKEN = "t0ken"
H = {"Authorization": f"Bearer {TOKEN}"}


def client_for(services):
    return TestClient(create_app(services, TOKEN, scheduler=False))


def ndjson(r):
    return [json.loads(line) for line in r.text.splitlines() if line]


def test_auth_and_health(make_services):
    c = client_for(make_services())
    assert c.post("/api/chat", json={"text": "hi"}).status_code == 401
    assert c.post("/api/chat", json={"text": "hi"}, headers={"Authorization": "Bearer nope"}).status_code == 401
    h = c.get("/health").json()
    assert h["ok"] and h["laya"] == "down" and h["google"].startswith("fake")
    assert c.get("/").status_code == 200 and "Maxie" in c.get("/").text  # the PWA


def test_chat_stream_direct_action(make_services):
    s = make_services()
    events = ndjson(client_for(s).post("/api/chat", json={"text": "open spotify"}, headers=H))
    assert events[0]["type"] == "route" and events[0]["direct"] and events[-1]["text"] == "Opening Spotify."
    assert {"type": "tool", "name": "open_app", "ok": True} in events  # direct actions show as steps too
    assert ("open_app", "Spotify") in s.fakes["mac"].calls


def test_shortcut_phone_action_runs_on_the_phone(make_services):
    s = make_services()
    r = client_for(s).post("/api/shortcut", json={"text": "set a 5 minute timer"}, headers=H).json()
    assert r["phone_action"]["action"] == "timer" and r["phone_action"]["args"]["minutes"] == 5
    assert r["reply"] == "5 minute timer started." and s.fakes["mac"].calls == []


def test_confirm_flow_end_to_end(make_services):
    client = FakeAnthropic([turn(tools=[("send_email", {"to": "mark@example.com", "subject": "slides", "body": "tonight"})]),
                            turn("Drafted it; it's waiting for your OK.")])
    s = make_services(client)
    c = client_for(s)
    r = c.post("/api/shortcut", json={"text": "email mark that i'll send the slides tonight"}, headers=H).json()
    assert r["confirmations"] and "waiting for your OK" in r["reply"]
    pending = c.get("/api/confirmations", headers=H).json()
    assert len(pending) == 1 and s.fakes["google"].sent == []
    assert c.post(f"/api/confirm/{pending[0]['id']}", json={"approve": True}, headers=H).json()["ok"]
    assert s.fakes["google"].sent == [{"to": "mark@example.com", "subject": "slides", "body": "tonight"}]
    assert c.post(f"/api/confirm/{pending[0]['id']}", json={"approve": True}, headers=H).status_code == 409
    audit = c.get("/api/audit", headers=H).json()
    assert audit[0]["actor"] == "user" and audit[0]["action"] == "send_email"


def test_memory_settings_budget_history(make_services):
    c = client_for(make_services())
    r = c.put("/api/memory", json={"markdown": "## me\n- I'm red-green color blind\n- my password is x"}, headers=H).json()
    assert r["refused"] == ["my password is x"] and "color blind" in r["markdown"]
    fid = c.get("/api/memory", headers=H).json()["facts"][0]["id"]
    assert c.delete(f"/api/memory/{fid}", headers=H).json()["ok"]
    assert c.put("/api/settings", json={"monthly_budget_usd": 50}, headers=H).json()["monthly_budget_usd"] == 50
    assert c.get("/api/budget", headers=H).json()["cap_usd"] == 50
    assert c.put("/api/settings", json={"hub_host": "0.0.0.0"}, headers=H).status_code == 400
    c.post("/api/chat", json={"text": "volume up", "conversation": "phone"}, headers=H)
    hist = c.get("/api/history?conversation=phone", headers=H).json()
    assert [m["role"] for m in hist] == ["user", "assistant"]


def test_routes_labels_and_push(make_services):
    s = make_services()
    c = client_for(s)
    c.post("/api/chat", json={"text": "check my inbox"}, headers=H)
    recent = c.get("/api/routes", headers=H).json()["recent"]
    assert c.post("/api/routes/label", json={"id": recent[0]["id"], "route": "email"}, headers=H).json()["ok"]
    assert c.post("/api/routes/recalibrate", headers=H).json() == {"ok": False, "reason": "Laya is not running"}
    assert c.post("/api/push/subscribe", json={"endpoint": "http://bad"}, headers=H).status_code == 400
    assert c.post("/api/push/subscribe", json={"endpoint": "https://web.push.apple.com/x", "keys": {}},
                  headers=H).json()["ok"]


def test_stop(make_services):
    s = make_services()
    gen = s.kill.generation()
    assert client_for(s).post("/api/stop", headers=H).json()["ok"] and s.kill.stopped_since(gen)
