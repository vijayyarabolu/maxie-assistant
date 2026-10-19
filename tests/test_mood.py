"""Alive, not an app: Maxie reacts to real life, and Claude's tone follows."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from maxie import mood
from tests.fakes import FakeAnthropic, turn

TZ = ZoneInfo("America/Chicago")
NOON = datetime(2026, 10, 15, 12, 0, tzinfo=TZ)
MIDNIGHT = datetime(2026, 10, 15, 23, 40, tzinfo=TZ)


@pytest.mark.parametrize("text", ["I got the offer!", "nailed the interview today", "Good news: we got in",
                                  "I moved to the final round", "got promoted"])
def test_good_news_is_a_little_dance(text):
    r = mood.react(text, NOON)
    assert r and r.state == "happy" and r.hold == mood.GOOD_HOLD_S and "glad" in r.tone


@pytest.mark.parametrize("text", ["rough day", "I got rejected again", "I didn't get the job", "feeling low",
                                  "so tired of this", "I'm overwhelmed"])
def test_a_hard_day_is_gentle(text):
    r = mood.react(text, NOON)
    assert r and r.state == "gentle" and "gentle" in r.tone


def test_bad_news_wins_over_good_words():
    assert mood.react("I didn't get the offer", NOON).state == "gentle"


def test_late_night_suggests_rest_and_the_bot_is_sleepy():
    r = mood.react("what's the weather tomorrow", MIDNIGHT)
    assert r and r.state == "sleep" and r.hold == mood.LATE_HOLD_S and "rest" in r.tone


@pytest.mark.parametrize("text", ["hi Maxie", "Hey maxie!", "good morning", "hello there"])
def test_a_hello_gets_a_wave(text):
    r = mood.react(text, NOON)
    assert r and r.state == "wave" and r.hold == mood.WAVE_HOLD_S and r.tone == ""


def test_a_hello_inside_a_request_is_not_a_greeting():
    assert mood.react("say hi to mom on WhatsApp", NOON) is None


@pytest.mark.parametrize("text", ["what's the weather", "open Spotify", "a long list of groceries"])
def test_ordinary_words_change_nothing(text):
    assert mood.react(text, NOON) is None


class Bus:
    def __init__(self, state="idle"):
        self.state, self.sent, self.states = state, [], []

    def current(self):
        return {"state": self.state}

    def publish(self, ev):
        self.sent.append(ev)

    def set_state(self, state, hold=None):
        self.states.append((state, hold))


def moments(bus, clock, quiet=False, enabled=True, roll=0.0):
    return mood.Moments(bus, quiet=lambda: quiet, enabled=lambda: enabled, clock=clock, rand=lambda: roll)


def test_moments_are_rare_and_spaced_out():
    t = [NOON.timestamp()]
    bus = Bus()
    m = moments(bus, lambda: t[0])
    assert m.tick() is not None
    t[0] += 60
    assert m.tick() is None, "two hours apart"
    for _ in range(2):
        t[0] += mood.MOMENT_GAP_S
        assert m.tick() is not None
    t[0] += mood.MOMENT_GAP_S
    assert m.tick() is None, "three a day at most"
    assert [e["type"] for e in bus.sent] == ["moment"] * 3


@pytest.mark.parametrize("why", ["quiet", "off", "busy", "just talked", "unlucky"])
def test_no_moment_when_it_would_be_annoying(why):
    t = NOON.timestamp()
    bus = Bus("think" if why == "busy" else "idle")
    m = moments(bus, lambda: t, quiet=why == "quiet", enabled=why != "off", roll=0.5 if why == "unlucky" else 0.0)
    if why == "just talked":
        m.note_turn()
    assert m.tick() is None and bus.sent == []


def working(m, t, minutes):
    """He talks to Maxie every 10 minutes for this long."""
    for _ in range(minutes // 10):
        m.note_turn()
        t[0] += 600


def test_a_long_work_stretch_brings_a_stretch_break():
    t = [NOON.timestamp()]
    bus = Bus()
    m = moments(bus, lambda: t[0], roll=0.99)  # no random moments: only the stretch
    working(m, t, 80)
    assert m.tick() is None, "not long enough yet"
    working(m, t, 20)
    assert m.tick() == "stretch"
    assert bus.states == [("stretch", mood.STRETCH_HOLD_S)]
    working(m, t, 30)
    assert m.tick() is None, "once per stretch"


def test_a_real_break_resets_the_stretch():
    t = [NOON.timestamp()]
    m = moments(Bus(), lambda: t[0], roll=0.99)
    working(m, t, 60)
    t[0] += mood.STRETCH_BREAK_S
    working(m, t, 50)
    assert m.tick() is None


@pytest.mark.parametrize("why", ["quiet", "off", "busy", "gone"])
def test_no_stretch_break_when_it_would_be_annoying(why):
    t = [NOON.timestamp()]
    bus = Bus("think" if why == "busy" else "idle")
    m = moments(bus, lambda: t[0], quiet=why == "quiet", enabled=why != "off", roll=0.99)
    working(m, t, 120)
    if why == "gone":
        t[0] += mood.STRETCH_BREAK_S
    assert m.tick() is None and bus.states == []


async def test_good_news_makes_the_bot_happy_and_tells_claude(make_services, clock):
    clock.set(NOON)
    client = FakeAnthropic([turn("That's wonderful, congratulations!")])
    s = make_services(client)
    [e async for e in s.agent.handle("I got the offer from the design studio today!")]
    assert s.events.current()["state"] == "happy"
    said = [b["text"] for b in client.calls[0]["messages"][-1]["content"] if b["type"] == "text"]
    assert any(t.startswith("How to sound this time:") and "glad" in t for t in said)


async def test_a_hard_day_makes_the_bot_gentle(make_services, clock):
    clock.set(NOON)
    s = make_services(FakeAnthropic([turn("I'm sorry. That sounds really hard.")]))
    [e async for e in s.agent.handle("rough day, I got rejected again")]
    assert s.events.current()["state"] == "gentle"


async def test_ordinary_turns_carry_no_tone(make_services, clock):
    clock.set(NOON)
    client = FakeAnthropic([turn("Here you go.")])
    s = make_services(client)
    [e async for e in s.agent.handle("explain how tides work")]
    said = [b["text"] for b in client.calls[0]["messages"][-1]["content"] if b["type"] == "text"]
    assert not any(t.startswith("How to sound") for t in said)
    assert s.events.current()["state"] == "idle"


def test_the_hub_wires_moments_into_the_minute_tick(make_services):
    s = make_services()
    assert s.agent.moments is s.proactive.moments is not None
