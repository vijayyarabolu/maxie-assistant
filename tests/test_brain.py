from maxie.budget import HAIKU, OPUS, SONNET
from tests.fakes import FakeAnthropic, turn


async def collect(agen):
    return [e async for e in agen]


async def test_streams_text_and_records_cost(make_services):
    client = FakeAnthropic([turn("Paris is the capital.")])
    s = make_services(client)
    events = await collect(s.brain.run([], "capital of france?", HAIKU))
    assert "".join(e["text"] for e in events if e["type"] == "text") == "Paris is the capital."
    assert events[-1]["type"] == "done" and events[-1]["cost"] > 0
    assert s.budget.status().month_usd == events[-1]["cost"]


async def test_prompt_caching_layout(make_services):
    client = FakeAnthropic([turn("hi")])
    s = make_services(client)
    s.memory.remember("Jordan is my partner", "people")
    await collect(s.brain.run([], "hello", SONNET))
    p = client.calls[0]
    assert p["cache_control"] == {"type": "ephemeral", "ttl": "1h"}  # conversation tail, an hour (bursty use)
    assert all(b.get("cache_control") for b in p["system"])  # persona + memory
    assert "Jordan is my partner" in p["system"][1]["text"]
    assert p["tools"][-1].get("cache_control") and sum("cache_control" in t for t in p["tools"]) == 1
    assert p["tools"][0]["type"].startswith("web_search")
    names = [t["name"] for t in p["tools"][1:]]
    assert names == sorted(names)  # stable order keeps the cache warm
    assert p["messages"][-1]["content"][0]["text"].startswith("Now: ")  # time is outside the cached prefix
    assert p["fallbacks"] == "default" and p["output_config"] == {"effort": "medium"}


async def test_haiku_has_web_search_but_no_fallbacks_and_opus_thinks_hard(make_services):
    client = FakeAnthropic([turn("a"), turn("b")])
    s = make_services(client)
    await collect(s.brain.run([], "x", HAIKU))
    await collect(s.brain.run([], "x", OPUS))
    haiku, opus = client.calls
    assert "fallbacks" not in haiku and haiku["tools"][0]["name"] == "web_search" and "type" not in haiku["tools"][0]
    assert opus["tools"][0]["type"].startswith("web_search")
    assert haiku["output_config"]["effort"] == "low" and opus["output_config"]["effort"] == "high"


async def test_tool_loop(make_services):
    client = FakeAnthropic([turn("Checking.", tools=[("calendar_events", {"days": 1})]),
                            turn("You have a mock interview at 10.")])
    s = make_services(client)
    events = await collect(s.agent.handle("what's on my calendar today?"))
    assert [e["type"] for e in events][:2] == ["route", "ack"]
    assert {"type": "tool", "name": "calendar_events", "ok": True} in events
    result = client.calls[1]["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and "Mock interview" in result["content"]
    assert events[-1]["text"].endswith("mock interview at 10.")


async def test_budget_cap_stops_sonnet(make_services):
    client = FakeAnthropic([turn("never")])
    s = make_services(client)
    s.budget.record(SONNET, {"output_tokens": 10_000_000})  # $100 > $95
    events = await collect(s.agent.handle("check my inbox"))
    assert "budget is used up" in events[-1]["text"] and client.calls == []
    events = await collect(s.agent.handle("open spotify"))  # direct actions still work
    assert events[-1]["text"] == "Opening Spotify."


async def test_refusal_and_no_key(make_services):
    s = make_services(FakeAnthropic([turn("", stop="refusal")]))
    events = await collect(s.brain.run([], "x", SONNET))
    assert events[-1]["text"] == "I can't help with that one."
    s = make_services(None)
    events = await collect(s.agent.handle("check my inbox"))
    assert "no API key" in events[-1]["text"]


async def test_history_is_sent_and_alternates(make_services):
    client = FakeAnthropic([turn("first"), turn("second")])
    s = make_services(client)
    await collect(s.agent.handle("check my inbox"))
    await collect(s.agent.handle("and the job one?"))
    roles = [m["role"] for m in client.calls[1]["messages"]]
    assert roles == ["user", "assistant", "user"]


async def test_direct_actions_skip_claude(make_services):
    client = FakeAnthropic([])
    s = make_services(client)
    for text, reply in [("open spotify", "Opening Spotify."), ("set a 10 minute timer", "10 minute timer started."),
                        ("volume up", "Turning it up."), ("what's 12 x 7", "84.")]:
        events = await collect(s.agent.handle(text))
        assert events[-1]["text"] == reply
    assert client.calls == []
    assert ("open_app", "Spotify") in s.fakes["mac"].calls


async def test_server_side_web_search_shows_as_a_tool_event_and_is_billed(make_services):
    client = FakeAnthropic([turn("Bitcoin is about $81k.", searches=2)])
    s = make_services(client)
    events = await collect(s.brain.run([], "price of bitcoin?", HAIKU))
    assert [e for e in events if e["type"] == "tool"] == [{"type": "tool", "name": "web_search", "ok": True}] * 2
    assert events[-1]["cost"] >= 0.02  # two searches at a cent each, on top of the tokens


async def test_haiku_starts_without_the_server_search_and_escalates(make_services):
    client = FakeAnthropic([turn(tools=[("web_search", {"query": "chiefs score"})]), turn("They won 27-20.", searches=1)])
    s = make_services(client)
    events = await collect(s.brain.run([], "what was the chiefs score", "claude-haiku-5-5"))
    first, second = client.calls
    assert first["tools"][0] == {**first["tools"][0], "name": "web_search"} and "type" not in first["tools"][0]  # the stub
    assert second["tools"][0]["type"].startswith("web_search")  # the real one, same messages
    assert len(second["messages"]) == 1 and events[-1]["text"] == "They won 27-20."
    names = [e["name"] for e in events if e["type"] == "tool"]
    assert names == ["web_search_requested", "web_search"]
    sonnet = FakeAnthropic([turn("hi")])
    await collect(make_services(sonnet).brain.run([], "hello", SONNET))
    assert sonnet.calls[0]["tools"][0]["type"].startswith("web_search")  # Sonnet: the real tool from the start
