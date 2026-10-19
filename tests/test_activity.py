"""Activity view + rules: per-action Allow / Ask / Block, and every task as a timeline that can
be paused, resumed and cancelled mid-way."""

import asyncio

from fastapi.testclient import TestClient

from maxie.hands.registry import TurnContext
from maxie.hub.api import create_app
from maxie.rules import Rules, default_for
from maxie.tasks import Tasks
from tests.fakes import FakeAnthropic, turn
from tests.test_api import TOKEN, H


def test_defaults_are_safe(make_services):
    s = make_services()
    table = {r["name"]: r for r in s.rules.table(s.registry)}
    assert table["send_email"]["rule"] == "ask" and table["send_email"]["group"] == "Leaves the Mac"
    assert table["create_event"]["rule"] == "ask" and table["delegate_to_claude_code"]["rule"] == "ask"
    assert table["open_app"]["rule"] == "allow" and table["open_app"]["group"] == "Changes things"
    assert table["weather"]["rule"] == "allow" and table["weather"]["group"] == "Reads"
    assert default_for("pay") == "ask" and default_for("read") == "allow"


def test_block_and_allow_change_what_a_tool_does(make_services):
    s = make_services()
    ctx = TurnContext(why="t")
    s.rules.set("send_email", "block", "send")
    r = s.registry.execute("send_email", {"to": "a@b.c", "subject": "s", "body": "b"}, ctx)
    assert r.is_error and "blocked" in r.content and s.fakes["google"].sent == [] and r.confirmation is None
    assert s.audit.recent(1)[0]["action"] == "blocked:send_email"
    s.rules.set("send_email", "allow", "send")
    r = s.registry.execute("send_email", {"to": "a@b.c", "subject": "s", "body": "b"}, ctx)
    assert not r.is_error and s.fakes["google"].sent and r.confirmation is None
    s.rules.set("open_app", "ask", "action")
    r = s.registry.execute("open_app", {"name": "Spotify"}, ctx)
    assert r.confirmation is not None and s.fakes["mac"].calls == []
    assert s.settings.action_rules == {"send_email": "allow", "open_app": "ask"}  # only overrides are stored
    s.rules.set("open_app", "allow", "action")
    assert "open_app" not in s.settings.action_rules


def test_rules_api(make_services):
    s = make_services()
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    r = c.get("/api/rules", headers=H).json()
    assert r["choices"] == ["allow", "ask", "block"] and any(x["name"] == "send_email" for x in r["rules"])
    assert c.put("/api/rules", json={"name": "send_email", "rule": "block"}, headers=H).json()["ok"]
    assert c.put("/api/rules", json={"name": "send_email", "rule": "maybe"}, headers=H).status_code == 400
    assert c.put("/api/rules", json={"name": "nope", "rule": "allow"}, headers=H).status_code == 404
    audit = c.get("/api/audit", headers=H).json()
    assert audit[0]["action"] == "rule_changed" and audit[0]["outcome"] == "ask -> block"


def test_every_turn_is_a_task_with_steps(make_services):
    s = make_services()
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    c.post("/api/chat", json={"text": "open spotify"}, headers=H)
    r = c.get("/api/tasks", headers=H).json()
    assert r["current"] is None and not r["paused"]
    t = r["tasks"][0]
    assert t["text"] == "open spotify" and t["route"] == "mac_action" and t["status"] == "done" and t["device"] == "mac"
    assert t["steps"][0]["name"] == "open_app" and t["steps"][0]["ok"] and t["reply"] == "Opening Spotify."
    assert t["first_ms"] is not None and t["total_ms"] >= t["first_ms"] and t["cost"] == 0.0


def test_task_events_ride_the_stream(make_services):
    s = make_services()
    seen = []

    async def go():
        sub = s.events.subscribe()
        await anext(sub)
        async for _ in s.agent.handle("what time is it"):
            pass
        while True:
            try:
                seen.append(await asyncio.wait_for(anext(sub), timeout=0.2))
            except TimeoutError:
                break
    asyncio.run(go())
    tasks = [e for e in seen if e["type"] == "task"]
    assert tasks and tasks[0]["status"] == "running" and tasks[-1]["status"] == "done" and tasks[-1]["reply"].startswith("It's")


async def test_pause_holds_between_steps_and_cancel_ends_it(make_services):
    client = FakeAnthropic([turn(tools=[("weather", {"days": 1})]), turn("Sunny.")])
    s = make_services(client)
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    events = []

    async def run():
        async for ev in s.agent.handle("what's the weather like and should I bike"):
            events.append(ev)
            if ev["type"] == "tool":  # first step done: pause from the panel
                task = s.tasks.current.id
                assert c.post(f"/api/tasks/{task}/pause", headers=H).json()["status"] == "paused"
                assert s.kill.paused and c.get("/api/tasks", headers=H).json()["paused"]
                await asyncio.sleep(0.5)
                assert len(client.calls) == 1  # no second model call while paused
                assert c.post(f"/api/tasks/{task}/cancel", headers=H).json()["ok"]
    await run()
    assert events[-1]["type"] == "done" and events[-1].get("stopped")
    assert s.tasks.recent(1)[0]["status"] == "stopped" and not s.kill.paused
    assert c.post("/api/tasks/999/pause", headers=H).status_code == 409
    assert c.post("/api/tasks/1/dance", headers=H).status_code == 400


def test_tasks_store_trims_and_reads(clock):
    from maxie.db import DB
    t = Tasks(DB(), clock=clock)
    for i in range(3):
        t.start(f"task {i}", "mac")
        t.routed("quick_question", "claude-haiku-5-5")
        t.step("clock", True)
        t.finish("ok", 0.001)
    rows = t.recent(2)
    assert [r["text"] for r in rows] == ["task 2", "task 1"] and rows[0]["steps"][0]["name"] == "clock"
    assert t.get(1)["status"] == "done" and t.get(99) is None


def test_rules_reject_bad_values(make_services):
    s = make_services()
    try:
        Rules(s.settings).set("x", "nope", "read")
    except ValueError as e:
        assert "allow, ask, block" in str(e)
    else:
        raise AssertionError("bad rule accepted")
