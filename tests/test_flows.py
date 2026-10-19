"""Watch me once, then do it: flows are checked, never hold secrets, and replay only after his OK."""
import pytest
from fastapi.testclient import TestClient

from maxie.flows import FlowError, clean_step
from maxie.hub.api import create_app
from maxie.router.rules import match

TOKEN = "t" * 32
H = {"Authorization": f"Bearer {TOKEN}"}
STEPS = [
    {"do": "open_app", "bundle": "com.apple.Safari", "name": "Safari"},
    {"do": "click", "app": "com.apple.Safari", "window": "Expenses", "role": "AXButton", "title": "New report"},
    {"do": "type", "text": "Team lunch"},
    {"do": "key", "key": "return", "mods": []},
]


@pytest.mark.parametrize("step,why", [
    ({"do": "type", "text": "hunter2", "secure": True}, "password field"),
    ({"do": "type", "text": "card 4111 1111 1111 1111"}, "secret"),
    ({"do": "type", "text": "my password is x"}, "secret"),
    ({"do": "click", "role": "AXButton"}, "find the control"),
    ({"do": "key", "key": "f13"}, "unknown key"),
    ({"do": "key", "key": "a", "mods": ["hyper"]}, "modifiers"),
    ({"do": "shell", "cmd": "rm"}, "unknown step"),
])
def test_bad_or_secret_steps_are_refused(step, why):
    with pytest.raises(FlowError, match=why):
        clean_step(step)


def test_steps_keep_only_what_the_player_uses():
    assert clean_step({"do": "key", "key": "s", "mods": ["shift", "cmd", "cmd"], "x": 1}) == \
        {"do": "key", "key": "s", "mods": ["cmd", "shift"]}


def test_save_find_and_forget(make_services):
    flows = make_services().flows
    f = flows.save("Expense report", STEPS)
    assert f.line() == "4 steps in Safari"
    assert flows.find("the expense report").id == f.id
    assert flows.find("expense").id == f.id
    with pytest.raises(FlowError, match="already"):
        flows.save("expense report", STEPS)
    with pytest.raises(FlowError, match="Flows: Expense report"):
        flows.find("taxes")
    assert flows.forget(f.id) and flows.all() == []


@pytest.mark.parametrize("text,action,args", [
    ("watch me once", "watch_me", {"on": True}),
    ("Hey Max, watch me do this", "watch_me", {"on": True}),
    ("done watching", "watch_me", {"on": False}),
    ("replay the expense report flow", "run_flow", {"name": "expense report"}),
    ("run my morning setup flow again", "run_flow", {"name": "morning setup"}),
])
def test_words_route_to_flows(text, action, args):
    hit = match(text)
    assert hit is not None and (hit.action, hit.args) == (action, args)


def capture(s, monkeypatch):
    sent = []
    real = s.events.publish
    monkeypatch.setattr(s.events, "publish", lambda ev: (sent.append(ev), real(ev)))
    return sent


async def test_replay_waits_for_his_ok_then_goes_to_the_mac(make_services, monkeypatch):
    s = make_services()
    s.flows.save("Expense report", STEPS)
    sent = capture(s, monkeypatch)
    out = [e async for e in s.agent.handle("replay the expense report flow")]
    confirm = next(e for e in out if e["type"] == "confirm")
    assert confirm["summary"] == "Use the screen for ~10 s to replay Expense report"
    assert out[-1]["text"].startswith("That needs your OK first")
    assert not any(e["type"] == "flow_run" for e in sent), "nothing runs before he says yes"
    s.registry.resolve(confirm["id"], True)
    run = next(e for e in sent if e["type"] == "flow_run")
    assert run["name"] == "Expense report" and run["steps"][2] == {"do": "type", "text": "Team lunch"}
    done = s.flows.finish(run["run"], ok=False, done=2, error="couldn't find New report")
    assert done.runs == 1 and done.last_result == "Stopped at step 3 of 4: couldn't find New report."
    assert sent[-1] == {"type": "transcript", "role": "assistant", "text": f"Expense report: {done.last_result}",
                        "final": True}


async def test_an_unknown_flow_is_not_put_to_him(make_services):
    s = make_services()
    out = [e async for e in s.agent.handle("run the taxes flow")]
    assert not any(e["type"] == "confirm" for e in out)
    assert "I don't have a flow called taxes" in out[-1]["text"]


async def test_watch_me_tells_the_mac_app(make_services, monkeypatch):
    s = make_services()
    sent = capture(s, monkeypatch)
    out = [e async for e in s.agent.handle("watch me once")]
    assert out[-1]["text"].startswith("Watching.")
    [e async for e in s.agent.handle("done watching")]
    assert [e["on"] for e in sent if e["type"] == "flow_watch"] == [True, False]


async def test_ask_me_rule_on_a_mac_action_shows_a_card_not_a_false_done(make_services):
    s = make_services()
    s.settings.action_rules = {"open_app": "ask"}
    out = [e async for e in s.agent.handle("open Notes")]
    assert any(e["type"] == "confirm" for e in out)
    assert out[-1]["text"].startswith("That needs your OK first") and s.fakes["mac"].calls == []


def test_flows_api(make_services):
    s = make_services()
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    r = c.post("/api/flows", json={"name": "Expense report", "steps": STEPS}, headers=H)
    assert r.status_code == 200 and r.json()["line"] == "4 steps in Safari"
    fid = r.json()["id"]
    bad = c.post("/api/flows", json={"name": "Login", "steps": [{"do": "type", "text": "x", "secure": True}]}, headers=H)
    assert bad.status_code == 400 and "password field" in bad.json()["detail"]
    assert [f["name"] for f in c.get("/api/flows", headers=H).json()["flows"]] == ["Expense report"]
    run = c.post(f"/api/flows/{fid}/run", headers=H).json()["run"]
    r = c.post(f"/api/flows/runs/{run}", json={"ok": True, "done": 4}, headers=H)
    assert r.json()["last_result"] == "Done." and r.json()["runs"] == 1
    assert c.post(f"/api/flows/runs/{run}", json={"ok": True}, headers=H).status_code == 404
    assert c.delete(f"/api/flows/{fid}", headers=H).json() == {"ok": True}
    assert c.delete(f"/api/flows/{fid}", headers=H).status_code == 404
