"""Phone that acts: from the iPhone, timers, music, apps, reminders, message drafts and
directions come back as phone actions the Shortcut runs; on the Mac the same words use the Mac's apps."""
import pytest
from fastapi.testclient import TestClient

from maxie.hub.api import create_app
from maxie.router.rules import match
from tests.fakes import FakeAnthropic, turn

TOKEN = "t" * 32
H = {"Authorization": f"Bearer {TOKEN}"}


@pytest.mark.parametrize("text,action,args", [
    ("Remind me to call Mom at 6 pm", "add_reminder", {"text": "call Mom", "when": "at 6 pm"}),
    ("remind me to drink water", "add_reminder", {"text": "drink water", "when": ""}),
    ("Remind me to pay rent tomorrow morning.", "add_reminder", {"text": "pay rent", "when": "tomorrow morning"}),
    ("Text Jordan I'm on my way", "draft_message", {"to": "Jordan", "text": "I'm on my way"}),
    ("tell jordan that dinner is at 8", None, None),  # lowercase name (typed): left to Claude
    ("Tell Jordan that dinner is at 8", "draft_message", {"to": "Jordan", "text": "dinner is at 8"}),
    ("Directions to Costco", "open_url", {"url": "https://maps.apple.com/?daddr=Costco", "label": "Directions to Costco."}),
    ("navigate to Union Station Madison", "open_url",
     {"url": "https://maps.apple.com/?daddr=Union%20Station%20Madison", "label": "Directions to Union Station Madison."}),
])
def test_phone_ready_rules(text, action, args):
    hit = match(text)
    if action is None:
        assert hit is None or hit.action != "draft_message"
    else:
        assert hit is not None and (hit.action, hit.args) == (action, args)


@pytest.mark.parametrize("text", ["tell me a joke", "Tell me about Rust", "message from Morgan about rent",
                                  "remind me what I decided about the Corolla"])
def test_not_hijacked(text):
    hit = match(text)
    assert hit is None or hit.action not in ("draft_message", "add_reminder")


def ask(s, text):
    return TestClient(create_app(s, TOKEN, scheduler=False)).post("/api/ask", json={"text": text}, headers=H).json()


def test_from_the_iphone_the_phone_does_it_and_the_mac_does_not(make_services):
    s = make_services()
    r = ask(s, "Remind me to call Mom at 6 pm")
    assert r["phone_action"]["action"] == "add_reminder" and r["reply"] == "Reminder set: call Mom, at 6 pm."
    r = ask(s, "Text Jordan I'm on my way")
    assert r["phone_action"]["args"] == {"to": "Jordan", "text": "I'm on my way"}
    assert r["reply"].startswith("Message to Jordan is ready")
    r = ask(s, "Directions to Costco")
    assert r["phone_action"]["args"]["url"].startswith("https://maps.apple.com/") and r["reply"] == "Directions to Costco."
    assert s.fakes["mac"].calls == []


async def test_on_the_mac_the_same_words_use_the_mac(make_services):
    s = make_services()
    out = [e async for e in s.agent.handle("Remind me to call Mom at 6 pm")]
    assert out[-1]["text"] == "Reminder set: call Mom, at 6 pm." and not any(e["type"] == "phone_action" for e in out)
    assert s.fakes["mac"].calls[0][0] == "add_reminder"


async def test_when_claude_picks_a_phone_tool_on_a_phone_turn_it_goes_to_the_phone(make_services):
    client = FakeAnthropic([turn(tools=[("timer", {"minutes": 12, "label": "pasta"})]), turn("Twelve minutes, on your phone.")])
    s = make_services(client)
    out = [e async for e in s.agent.handle("can you time my pasta, about twelve minutes", device="phone")]
    acts = [e for e in out if e["type"] == "phone_action"]
    assert acts == [{"type": "phone_action", "action": "timer", "args": {"minutes": 12, "label": "pasta"}}]
    assert s.fakes["mac"].calls == []
