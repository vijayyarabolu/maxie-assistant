"""a typed question waited ~1.5 s for the router's Haiku call before Maxie started answering. When the
router has to ask a model, the quick answer now starts at the same time and is kept only when the router agrees;
until then it may read, but anything else (an action, a fact to remember) waits for the router's verdict."""

import asyncio
import json
import time

from maxie.hands.registry import TurnContext
from maxie.models import HAIKU, SONNET
from tests.fakes import FakeAnthropic, FakeHaikuSync, turn

ROUTER_S = 0.3
OPEN_ENDED = "tell me something fun about otters"  # no rule, the lexicon unsure: the router asks Haiku


class SlowHaiku(FakeHaikuSync):
    def _create(self, **params):
        time.sleep(ROUTER_S)
        return super()._create(**params)


def says(route: str) -> SlowHaiku:
    return SlowHaiku(json.dumps({"route": route, "action": "none", "args_json": "{}", "confidence": 0.9}))


async def collect(agen):
    return [e async for e in agen]


async def test_the_answer_starts_while_the_router_decides_and_is_kept_when_it_agrees(make_services):
    # Arrange
    client = FakeAnthropic([turn("Otters hold hands while they sleep.")])
    s = make_services(client, anthropic_sync=says("chitchat"))

    # Act
    t0 = time.perf_counter()
    events = await collect(s.agent.handle(OPEN_ENDED))
    took = time.perf_counter() - t0

    # Assert
    assert events[0]["type"] == "route" and events[0]["route"] == "chitchat"
    assert events[-1]["text"] == "Otters hold hands while they sleep."
    assert len(client.calls) == 1 and client.calls[0]["model"] == HAIKU
    assert took < ROUTER_S * 1.8, "the answer ran during the router call, not after it"


async def test_a_router_that_wants_another_model_drops_the_early_answer(make_services):
    # Arrange: the early Haiku answer, then the real one on Sonnet
    client = FakeAnthropic([turn("early haiku guess"), turn("Here is the plan.")])
    s = make_services(client, anthropic_sync=says("multi_step"))

    # Act
    events = await collect(s.agent.handle(OPEN_ENDED))

    # Assert
    texts = "".join(e["text"] for e in events if e["type"] == "text")
    assert "early haiku guess" not in texts
    assert events[-1]["text"] == "Here is the plan." and events[-1]["model"] == SONNET
    assert s.conversations.history("default")[-1]["content"] == "Here is the plan."


async def test_an_action_waits_for_the_router_and_then_runs_once(make_services):
    # Arrange: the early answer reaches for open_app before the router has decided
    client = FakeAnthropic([turn("", tools=[("open_app", {"name": "Spotify"})]), turn("Opened Spotify.")])
    s = make_services(client, anthropic_sync=says("chitchat"))

    # Act
    events = await collect(s.agent.handle(OPEN_ENDED))

    # Assert
    assert [c for c in s.fakes["mac"].calls if c[0] == "open_app"] == [("open_app", "Spotify")]
    assert events[-1]["text"] == "Opened Spotify." and len(client.calls) == 2


async def test_a_dropped_early_answer_never_ran_its_action(make_services):
    # Arrange: the early answer wants open_app, the router wants Sonnet, and Sonnet just answers
    client = FakeAnthropic([turn("", tools=[("open_app", {"name": "Spotify"})]), turn("Here is the plan.")])
    s = make_services(client, anthropic_sync=says("multi_step"))

    # Act
    events = await collect(s.agent.handle(OPEN_ENDED))

    # Assert
    assert not [c for c in s.fakes["mac"].calls if c[0] == "open_app"]
    assert events[-1]["text"] == "Here is the plan."


async def test_reads_run_before_the_verdict(make_services):
    client = FakeAnthropic([turn("", tools=[("calendar_events", {"days": 1})]), turn("A mock interview at 10.")])
    s = make_services(client)
    ctx = TurnContext(verdict=asyncio.Event())  # never set
    events = await asyncio.wait_for(collect(s.brain.run([], "what's on today?", HAIKU, ctx)), 2)
    assert events[-1]["text"] == "A mock interview at 10."


async def test_remembering_a_fact_waits_for_the_verdict(make_services):
    client = FakeAnthropic([turn("", tools=[("remember_fact", {"topic": "people", "fact": "Max is my cat"})])])
    s = make_services(client)
    ctx = TurnContext(verdict=asyncio.Event())
    task = asyncio.ensure_future(collect(s.brain.run([], "Max is my cat", HAIKU, ctx)))
    await asyncio.sleep(0.1)
    assert not task.done() and s.memory.all() == []
    ctx.verdict.set()
    await asyncio.wait_for(task, 2)
    assert len(s.memory.all()) == 1


async def test_no_early_answer_when_the_router_needs_no_model(make_services):
    # Arrange: "check my inbox" is the lexicon's: Sonnet answers, no Haiku guess spent
    client = FakeAnthropic([turn("Two new emails.")])
    s = make_services(client, anthropic_sync=says("chitchat"))

    # Act
    await collect(s.agent.handle("check my inbox"))

    # Assert
    assert [c["model"] for c in client.calls] == [SONNET]


async def test_a_client_that_leaves_before_the_answer_drops_the_early_one(make_services):
    # verifier: leaving between the route event and the release left the early answer running (or waiting forever)
    import asyncio
    client = FakeAnthropic([turn("", tools=[("open_app", {"name": "Spotify"})])])
    s = make_services(client, anthropic_sync=says("chitchat"))
    stream = s.agent.handle(OPEN_ENDED)
    async for ev in stream:
        if ev["type"] == "route":
            break
    await stream.aclose()
    await asyncio.sleep(0.05)
    holding = [t for t in asyncio.all_tasks() if "Early._hold" in repr(t.get_coro()) and not t.done()]
    assert holding == []
    assert not [c for c in s.fakes["mac"].calls if c[0] == "open_app"]
