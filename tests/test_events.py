import asyncio
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from maxie.events import Events
from maxie.hub.api import create_app
from tests.conftest import TZ
from tests.fakes import FakeAnthropic, turn

H = {"Authorization": "Bearer t"}


async def drain(sub, n):
    return [await asyncio.wait_for(anext(sub), 1) for _ in range(n)]


async def test_subscriber_gets_current_then_live_events(clock):
    ev = Events(clock=clock)
    sub = ev.subscribe()
    assert (await anext(sub))["state"] == "idle"
    ev.set_state("think")
    ev.transcript("assistant", "hi")
    got = await drain(sub, 2)
    assert got[0]["state"] == "think" and got[1] == {"type": "transcript", "role": "assistant", "text": "hi", "final": False}


def test_celebration_holds_then_returns_to_base(clock):
    quiet = {"on": False}
    ev = Events(quiet=lambda: quiet["on"], clock=clock)
    ev.set_state("happy")
    ev.rest()  # a reply ending doesn't cut the celebration short
    assert ev.current()["state"] == "happy" and ev.current()["hold"] == 8
    clock.t += 9
    assert ev.current()["state"] == "idle"
    quiet["on"] = True
    assert ev.current()["state"] == "sleep"


async def test_a_command_goes_think_talk_rest(make_services):
    s = make_services(FakeAnthropic([turn("You have a mock interview at 10.")]))
    sub = s.events.subscribe()
    await anext(sub)
    [e async for e in s.agent.handle("what's on my calendar today?")]
    got = []
    while True:
        try:
            got.append(await asyncio.wait_for(anext(sub), 0.2))
        except TimeoutError:
            break
    states = [g["state"] for g in got if g["type"] == "state"]
    assert states == ["think", "talk", "idle"]
    said = "".join(g["text"] for g in got if g["type"] == "transcript" and g["role"] == "assistant")
    assert said == "You have a mock interview at 10."


def test_interview_alert_makes_the_bot_happy_and_applying_makes_it_proud(make_services, clock):
    s = make_services()
    s.proactive.check_job_inbox()
    assert s.events.current()["state"] == "happy"
    clock.t += 10
    s.proactive.check_applications()  # first look: baseline, no celebration
    assert s.events.current()["state"] == "idle"
    s.fakes["jobs"].data["applications"].append({"company": "Hooli", "date": "2026-10-15"})
    assert s.proactive.check_applications() == 1
    assert s.events.current()["state"] == "proud"
    assert any(w["kind"] == "applied" for w in s.proactive.wins.since(1))


def test_quiet_hours_put_it_to_sleep(make_services, clock):
    s = make_services()
    clock.set(datetime(2026, 10, 15, 23, 30, tzinfo=TZ))
    s.proactive.tick()
    assert s.events.current()["state"] == "sleep"


def test_events_endpoint(make_services):
    s = make_services()
    c = TestClient(create_app(s, "t", scheduler=False))
    assert c.get("/api/events?once=1").status_code == 401
    r = c.get("/api/events?once=1", headers=H)
    assert r.headers["content-type"].startswith("text/event-stream")
    assert json.loads(r.text.removeprefix("data: ").strip())["state"] == "idle"
    assert c.post("/api/events/state", json={"state": "listen"}, headers=H).json()["state"] == "listen"
    assert c.post("/api/events/state", json={"state": "dance"}, headers=H).status_code == 400


@pytest.mark.parametrize("state", ["wave", "closed", "stretch"])
def test_maxie_states_are_known(state):
    ev = Events()
    ev.set_state(state, 3)
    assert ev.current()["state"] == state


def test_a_held_sleep_shows_even_outside_quiet_hours():
    t = [1000.0]
    ev = Events(clock=lambda: t[0])
    ev.set_state("sleep", 20)
    assert ev.current()["state"] == "sleep"
    t[0] += 21
    assert ev.current()["state"] == "idle"
