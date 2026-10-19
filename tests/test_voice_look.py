""""Maxie, look" / "take a screenshot" sends the turn with a picture of his screen.
The Mac app takes it (it holds Screen Recording) and hands it over its bridge; the hub attaches it; Maxie answers
out loud as usual."""

import pytest

from maxie.hands.apple import AppleUnavailable, FakeApple
from maxie.voice.loop import Turn, wants_screen
from tests.test_api import H, client_for, ndjson
from tests.test_voice import make_loop

LOOKS = ["look", "look at this", "take a screenshot", "take a screen shot", "screenshot", "what's on my screen",
         "What is on my screen?", "look at my screen", "Maxie, look", "can you look at this", "look here"]
NOT_LOOKS = ["look up the weather in Austin", "take a note: buy milk", "look for flights to Denver", "what time is it?",
             "that look is nice", "screenshots folder size"]


@pytest.mark.parametrize("text", LOOKS)
def test_look_phrases_want_the_screen(text):
    assert wants_screen(text)


@pytest.mark.parametrize("text", NOT_LOOKS)
def test_other_commands_do_not(text):
    assert not wants_screen(text)


def test_a_spoken_look_goes_to_the_hub_with_the_screen():
    loop, hub = make_loop([], [], [{"type": "done", "text": "That's a failing pytest run."}])
    loop.handle("take a screenshot", Turn(heard_at=0))
    assert hub.chats == ["take a screenshot"] and hub.screens == [True]
    loop.handle("what time is it", Turn(heard_at=0))
    assert hub.screens == [True, False]


def capture(s):
    """Replaces the agent's turn with one that records what it was given."""
    seen = {}

    async def handle(text, conversation, device, images=(), files=()):
        seen.update(text=text, images=list(images))
        yield {"type": "done", "text": "ok"}
    s.agent.handle = handle
    return seen


def test_the_hub_attaches_the_macs_screenshot(make_services):
    s = make_services()
    s.agent.apple = FakeApple(screen="iVBORfake")
    seen = capture(s)
    r = client_for(s).post("/api/chat", json={"text": "what's this?", "conversation": "voice", "screen": True}, headers=H)
    assert ndjson(r)[-1]["type"] == "done"
    assert seen["images"] == ["iVBORfake"] and seen["text"] == "what's this?"


def test_no_screenshot_still_answers_and_says_why(make_services):
    s = make_services()
    s.agent.apple = FakeApple(error=AppleUnavailable("screen"))
    seen = capture(s)
    r = client_for(s).post("/api/chat", json={"text": "look", "conversation": "voice", "screen": True}, headers=H)
    assert ndjson(r)[-1]["type"] == "done" and seen["images"] == []
    assert "couldn't take a screenshot" in seen["text"].lower()


def test_a_normal_turn_never_takes_one(make_services):
    s = make_services()
    s.agent.apple = FakeApple(screen="iVBORfake")
    seen = capture(s)
    client_for(s).post("/api/chat", json={"text": "hi", "conversation": "voice"}, headers=H)
    assert seen["images"] == [] and ("screen",) not in s.agent.apple.calls
