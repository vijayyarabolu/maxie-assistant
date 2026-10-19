"""the Mac is Maxie's computer. Tasks run in the background without taking his screen:
apps open behind what he is doing, sends / buys / deletes from a background task wait for his tap, and a task
that truly needs the screen asks "Can I use the screen for ~20 s?" (or waits until he steps away, if he allowed
that)."""

import asyncio
import subprocess
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from maxie.background import wants
from maxie.hands import mac
from maxie.hands.registry import TurnContext
from maxie.hub.api import create_app
from maxie.safety import background, in_background
from tests.conftest import TZ
from tests.fakes import FakeAnthropic, turn

TOKEN = "t" * 32
H = {"Authorization": f"Bearer {TOKEN}"}
FLOW = [{"do": "open_app", "bundle": "com.apple.Safari", "name": "Safari"},
        {"do": "click", "app": "com.apple.Safari", "window": "Expenses", "role": "AXButton", "title": "New report"},
        {"do": "type", "text": "Team lunch"},
        {"do": "key", "key": "return", "mods": []}]


def drain(agen):
    async def go():
        return [ev async for ev in agen]
    return asyncio.run(go())


# ---- what counts as "do it in the background"


@pytest.mark.parametrize("text,task", [
    ("in the background, find 5 data engineer jobs in Austin", "find 5 data engineer jobs in Austin"),
    ("Hey Max, when you get a chance: summarize my unread mail", "summarize my unread mail"),
    ("look up flights to Denver in the background", "look up flights to Denver"),
    ("Max, in the meantime check the weather for Saturday.", "check the weather for Saturday"),
])
def test_background_words_are_understood(text, task):
    assert wants(text) == task


@pytest.mark.parametrize("text", ["what's in the background of this photo", "open Safari", "background", ""])
def test_other_words_are_not_background_tasks(text):
    assert wants(text) is None


# ---- the background flag


def test_background_flag_is_scoped_and_reaches_worker_threads():
    assert not in_background()
    with background():
        assert in_background()
        assert asyncio.run(asyncio.to_thread(in_background)), "tools run in a thread; the flag must follow"
    assert not in_background()


def test_apps_open_behind_his_work_in_the_background(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    m = mac.MacOS()
    m.open_app("Notes")
    with background():
        m.open_app("Notes")
        m.open_url("https://example.com")
        m.open_path("/tmp/x.pdf")
    assert calls[0] == ["open", "-a", "Notes"], "when he asks right now, the app comes to the front"
    assert calls[1:] == [["open", "-g", "-a", "Notes"], ["open", "-g", "https://example.com"], ["open", "-g", "/tmp/x.pdf"]]


def test_no_hand_brings_an_app_forward_in_its_scripts():
    """AppleScript without `activate`: Mail, Notes, Reminders and Music work behind his windows."""
    import inspect

    from maxie.hands import apple
    from maxie.hands import mac as mac_mod
    for mod in (mac_mod, apple):
        assert " activate" not in inspect.getsource(mod), mod.__name__


# ---- background tasks never send on their own


def test_a_send_from_the_background_waits_for_his_tap_even_when_his_rule_says_allow(make_services):
    s = make_services()
    s.rules.set("send_email", "allow", "send")
    with background():
        r = s.registry.execute("send_email", {"to": "a@b.co", "subject": "hi", "body": "x"}, TurnContext())
    assert r.confirmation is not None and "Not done yet" in r.content
    r2 = s.registry.execute("send_email", {"to": "a@b.co", "subject": "hi", "body": "x"}, TurnContext())
    assert r2.confirmation is None, "his allow rule still holds when he asks himself"


def test_blocked_stays_blocked_in_the_background(make_services):
    s = make_services()
    s.rules.set("send_email", "block", "send")
    with background():
        r = s.registry.execute("send_email", {"to": "a@b.co", "subject": "hi", "body": "x"}, TurnContext())
    assert r.is_error and r.confirmation is None


def test_reads_run_freely_in_the_background(make_services):
    s = make_services()
    with background():
        r = s.registry.execute("clock", {}, TurnContext())
    assert not r.is_error and r.confirmation is None


# ---- the screen


def test_a_flow_asks_for_the_screen_with_how_long(make_services):
    s = make_services()
    s.flows.save("Expense report", FLOW)
    s.rules.set("run_flow", "allow", "run")
    with background():
        r = s.registry.execute("run_flow", {"name": "expense report"}, TurnContext())
    c = r.confirmation
    assert c is not None and c.screen and c.status == "pending"
    assert c.summary == "Use the screen for ~10 s to replay Expense report"
    published = []
    s.events.publish = published.append
    assert not s.registry.resolve(c.id, True).is_error
    assert published and published[0]["type"] == "flow_run", "his yes plays it now"


def test_when_he_allowed_it_the_screen_task_waits_until_he_steps_away(make_services, clock):
    s = make_services()
    s.settings.screen_when_away = True
    s.flows.save("Expense report", FLOW)
    with background():
        c = s.registry.execute("run_flow", {"name": "expense report"}, TurnContext()).confirmation
    assert c.status == "away"
    published = []
    s.events.publish = published.append
    s.presence.report(locked=False, displays_asleep=False, idle_s=30, media=False)
    assert s.background.screen_tick() == 0, "he is at the Mac: wait"
    s.presence.report(locked=True, displays_asleep=True, idle_s=900, media=False)
    assert s.background.screen_tick() == 0, "nothing can click on a locked Mac"
    s.presence.report(locked=False, displays_asleep=False, idle_s=700, media=True)
    assert s.background.screen_tick() == 0, "a movie is playing"
    s.presence.report(locked=False, displays_asleep=False, idle_s=700, media=False)
    assert s.background.screen_tick() == 1
    assert [e["type"] for e in published if e["type"] == "flow_run"] == ["flow_run"]
    assert s.registry.confirmations.get(c.id).status == "approved"


def test_he_can_say_when_i_am_away_on_the_card(make_services):
    s = make_services()
    s.flows.save("Expense report", FLOW)
    c = s.registry.execute("run_flow", {"name": "expense report"}, TurnContext()).confirmation
    assert c.screen, "a live ask for the screen offers 'When I'm away' too"
    app = create_app(s, token=TOKEN, scheduler=False)
    with TestClient(app) as cl:
        assert cl.post(f"/api/confirm/{c.id}/away", headers=H).json()["status"] == "away"
        listed = cl.get("/api/confirmations", headers=H).json()
        assert [x["status"] for x in listed] == ["away"]
        assert cl.post(f"/api/confirm/{c.id}", json={"approve": False}, headers=H).status_code == 200
    assert s.registry.confirmations.get(c.id).status == "denied"


def test_a_screen_wait_expires_after_a_day(make_services, clock):
    s = make_services()
    s.settings.screen_when_away = True
    s.flows.save("Expense report", FLOW)
    with background():
        c = s.registry.execute("run_flow", {"name": "expense report"}, TurnContext()).confirmation
    clock.t += 25 * 3600
    assert s.registry.confirmations.get(c.id).status == "expired"


def test_only_away_tasks_can_be_moved_to_later(make_services):
    s = make_services()
    c = s.registry.execute("send_email", {"to": "a@b.co", "subject": "hi", "body": "x"}, TurnContext()).confirmation
    app = create_app(s, token=TOKEN, scheduler=False)
    with TestClient(app) as cl:
        assert cl.post(f"/api/confirm/{c.id}/away", headers=H).status_code == 400


# ---- the queue


def test_background_words_queue_the_task_and_answer_at_once(make_services):
    s = make_services()
    evs = drain(s.agent.handle("in the background, find 5 data engineer jobs in Austin"))
    done = [e for e in evs if e["type"] == "done"][-1]
    assert done["text"] == "On it, in the background. I'll tell you when it's done."
    q = s.background.recent()
    assert [(j["text"], j["status"], j["source"]) for j in q] == [("find 5 data engineer jobs in Austin", "queued", "you")]


def test_the_worker_runs_one_task_quietly_and_tells_him(make_services):
    client = FakeAnthropic([turn("Found 5 jobs: A, B, C, D, E.")])
    s = make_services(client=client)
    s.background.add("find 5 data engineer jobs in Austin")
    published = []
    s.events.publish = published.append
    job = asyncio.run(s.background.run_next(s.agent))
    assert job["status"] == "done" and job["reply"] == "Found 5 jobs: A, B, C, D, E."
    kinds = {e["type"] for e in published}
    assert "transcript" not in kinds and "state" not in kinds, "the panel and the mascot are left alone"
    assert any(e["type"] == "background" and e["status"] == "done" for e in published)
    pushed = s.fakes["notifier"].sent
    assert pushed and pushed[-1]["title"] == "Done in the background"
    assert s.background.recent()[0]["task_id"], "the steps are in the Activity timeline"
    assert s.latency.recent() == [], "background turns don't count toward his first-word times"
    assert asyncio.run(s.background.run_next(s.agent)) is None


def test_a_background_task_that_needs_his_ok_says_so(make_services):
    client = FakeAnthropic([turn("", tools=[("send_email", {"to": "a@b.co", "subject": "hi", "body": "x"})]),
                            turn("It's ready; it needs your OK to send.")])
    s = make_services(client=client)
    s.rules.set("send_email", "allow", "send")
    s.background.add("email a@b.co hi")
    published = []
    s.events.publish = published.append
    job = asyncio.run(s.background.run_next(s.agent))
    assert job["status"] == "waiting"
    assert any(e["type"] == "confirm" for e in published), "the card reaches the panel and the phone at once"
    assert len(s.registry.confirmations.pending()) == 1


def test_queued_tasks_can_be_cancelled_and_listed_over_the_api(make_services):
    s = make_services()
    app = create_app(s, token=TOKEN, scheduler=False)
    with TestClient(app) as cl:
        job = cl.post("/api/background", json={"text": "check the weather for Saturday"}, headers=H).json()
        assert job["status"] == "queued" and job["source"] == "you"
        assert cl.post("/api/background", json={"text": "  "}, headers=H).status_code == 422
        assert cl.get("/api/background", headers=H).json()[0]["id"] == job["id"]
        assert cl.delete(f"/api/background/{job['id']}", headers=H).json() == {"ok": True}
        assert cl.get("/api/background", headers=H).json()[0]["status"] == "cancelled"
        assert cl.delete(f"/api/background/{job['id']}", headers=H).status_code == 409


def test_a_failing_task_is_marked_and_the_worker_goes_on(make_services):
    s = make_services()

    class Broken:
        async def run_background(self, text, tasks):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    s.background.add("x")
    job = asyncio.run(s.background.run_next(Broken()))
    assert job["status"] == "error" and "boom" not in job["reply"]


# ---- what runs on its own: the Scheduled list


def test_scheduled_lists_what_maxie_does_on_his_own(make_services, clock):
    s = make_services()
    rows = {r["name"]: r for r in s.background.scheduled()}
    assert rows["Morning brief"]["when"] == "Every day at 8:00 AM" and rows["Morning brief"]["reads_only"]
    assert rows["Job inbox"]["when"] == "Every 5 minutes"
    assert rows["Meeting prep"]["when"] == "30 min before each meeting"
    assert rows["Keep Haiku fast"]["when"] == "Every 55 min while you're awake"
    assert "Keep Sonnet fast" not in rows
    s.settings.warm_sonnet = True
    assert "Keep Sonnet fast" in {r["name"] for r in s.background.scheduled()}
    assert all(r["reads_only"] for r in rows.values()), "nothing on the schedule sends, buys or deletes"


def test_scheduled_includes_his_routines(make_services, clock):
    s = make_services()
    s.db.execute("INSERT INTO watch_routines(key,apps,names,minute,weekdays,last_run) VALUES (?,?,?,?,?,?)",
                 ("k", '["com.x.jobhunt"]', '["jobhunt"]', 540, 1, ""))
    app = create_app(s, token=TOKEN, scheduler=False)
    with TestClient(app) as cl:
        rows = cl.get("/api/scheduled", headers=H).json()
    r = next(r for r in rows if r["name"] == "Routine")
    assert r["what"] == "Open jobhunt on weekdays at 9:00." and r["reads_only"] is False


def test_presence_away_needs_ten_quiet_minutes_unlocked(make_services, clock):
    s = make_services()
    p = s.presence
    assert not p.away(), "no report: not away"
    p.report(locked=False, displays_asleep=True, idle_s=601, media=False)
    assert p.away(), "the screen dimmed but nothing is locked"
    p.activity("tap")
    assert not p.away()
    clock.set(datetime(2026, 10, 15, 9, 0, tzinfo=TZ))
    p.report(locked=False, displays_asleep=False, idle_s=650, media=False)
    assert p.away()
    clock.t += 11 * 60
    assert not p.away(), "a stale report says nothing about now"
