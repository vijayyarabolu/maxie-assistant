"""Apple data, read locally (Messages, Calendar, Contacts…) through Maxie.app's bridge."""

import json

import pytest
from fastapi.testclient import TestClient

from maxie.hands.apple import AppleUnavailable, FakeApple, extract, find_in_messages
from maxie.hub.api import create_app
from maxie.router.rules import match

TOKEN = "t0ken"
H = {"Authorization": f"Bearer {TOKEN}"}
OCT_3 = 1791043200.0   # 2026-10-03 12:00 UTC
OCT_9 = 1791561600.0   # 2026-10-09 12:00 UTC

RILEY = [
    {"text": "Dinner at mine on Saturday?", "date": OCT_9, "from_me": False, "sender": "Riley Reyes"},
    {"text": "My new place is 1234 Oak St, Springfield, IL 66210. Park on the street.", "date": OCT_3,
     "from_me": False, "sender": "Riley Reyes"},
    {"text": "call me at (312) 555-0142 when you're close", "date": OCT_3 - 60, "from_me": False, "sender": "Riley Reyes"},
    {"text": "here's the menu https://example.com/menu", "date": OCT_3 - 120, "from_me": False, "sender": "Riley Reyes"},
]


@pytest.mark.parametrize("kind, text, value", [
    ("address", "My new place is 1234 Oak St, Springfield, IL 66210. Park on the street.",
     "1234 Oak St, Springfield, IL 66210"),
    ("address", "meet at 55 W 3rd Avenue apt 4", "55 W 3rd Avenue apt 4"),
    ("phone", "call me at (312) 555-0142 when you're close", "(312) 555-0142"),
    ("link", "here's the menu https://example.com/menu", "https://example.com/menu"),
    ("email", "write to riley.k@example.org please", "riley.k@example.org"),
    ("code", "your code is 482913", "482913"),
])
def test_extracts_the_thing_he_asked_for(kind, text, value):
    assert extract(kind, text) == value


def test_extract_finds_nothing_in_plain_chat():
    assert extract("address", "Dinner at mine on Saturday?") is None


def test_finds_the_address_riley_texted_with_its_date():
    # Arrange
    apple = FakeApple(messages={"riley": RILEY})

    # Act
    reply = find_in_messages(apple, "Riley", "address", tz="America/Chicago")

    # Assert
    assert reply == "Riley Reyes texted you this on Oct 3: 1234 Oak St, Springfield, IL 66210."


def test_says_so_when_nothing_matches():
    apple = FakeApple(messages={"riley": RILEY})
    assert find_in_messages(apple, "Riley", "email", tz="America/Chicago") == \
        "I didn't find an email from Riley in your Messages."
    assert find_in_messages(apple, "Morgan", "address", tz="America/Chicago") == \
        "I didn't find an address from Morgan in your Messages."


def test_explains_the_missing_permission():
    apple = FakeApple(error=AppleUnavailable("full_disk_access"))
    reply = find_in_messages(apple, "Riley", "address", tz="America/Chicago")
    assert "Full Disk Access" in reply and "Settings › Permissions" in reply


@pytest.mark.parametrize("text, kind, who", [
    ("find the address Riley texted me", "address", "Riley"),
    ("Max, find the address Riley texted me", "address", "Riley"),
    ("what's the number Morgan sent me", "phone", "Morgan"),
    ("what was the link Sam messaged me yesterday", "link", "Sam"),
    ("get me the code Mom texted", "code", "Mom"),
])
def test_rule_routes_find_in_messages_without_a_model(text, kind, who):
    hit = match(text)
    assert (hit.route, hit.action, hit.args) == ("messages", "find_in_messages", {"kind": kind, "who": who})


def test_rule_leaves_sending_alone():
    hit = match("text Riley the address")
    assert hit is None or hit.action != "find_in_messages"


def test_chat_answers_from_messages_with_no_model_call(make_services):
    # Arrange
    s = make_services()
    s.fakes["apple"].by_person = {"riley": RILEY}
    c = TestClient(create_app(s, TOKEN, scheduler=False))

    # Act
    lines = c.post("/api/chat", json={"text": "Max, find the address Riley texted me"}, headers=H).text.splitlines()
    done = [json.loads(x) for x in lines if x.strip()][-1]

    # Assert
    assert done["type"] == "done" and "1234 Oak St" in done["text"] and "Oct 3" in done["text"]
    assert done.get("cost", 0) == 0


# --- Apple first for text tasks: summaries and rewrites on the Mac's own model, $0 -----------------------

LETTER = ("Hi team, the offsite moved from Friday to Thursday the 22nd because the venue double-booked. "
          "Bring laptops and the Q4 numbers.")


@pytest.mark.parametrize("text, task", [
    (f"summarize this: {LETTER}", "summarize"),
    (f"Max, can you summarise this for me:\n{LETTER}", "summarize"),
    (f"tl;dr: {LETTER}", "summarize"),
    (f"rewrite this so it sounds friendlier: {LETTER}", "rewrite"),
    (f"make this shorter: {LETTER}", "rewrite"),
    (f"draft a reply to this: {LETTER}", "reply"),
    (f"pull out the dates: {LETTER}", "extract"),
])
def test_rule_spots_a_text_task_and_keeps_the_text(text, task):
    hit = match(text)
    assert (hit.route, hit.action) == ("text_task", task)
    assert hit.args == {"task": task, "text": LETTER, "how": hit.args["how"]}


@pytest.mark.parametrize("text", [
    "summarize my inbox",
    "rewrite my resume",
    "reply to Riley: sure, 7 works for me tonight, see you at the restaurant then",
    "summarize this: ok",
])
def test_rule_leaves_other_asks_to_the_brain(text):
    hit = match(text)
    assert hit is None or hit.route != "text_task"


def _chat(c, text):
    lines = c.post("/api/chat", json={"text": text}, headers=H).text.splitlines()
    return [json.loads(x) for x in lines if x.strip()]


def test_summary_is_answered_by_apple_for_free(make_services):
    # Arrange
    s = make_services()
    s.fakes["apple"].fm_reply = "The offsite is now Thursday the 22nd. Bring laptops and Q4 numbers."
    c = TestClient(create_app(s, TOKEN, scheduler=False))

    # Act
    events = _chat(c, f"summarize this: {LETTER}")

    # Assert
    done = events[-1]
    assert done["text"] == "The offsite is now Thursday the 22nd. Bring laptops and Q4 numbers."
    assert done["model"] == "apple-on-device" and done["cost"] == 0
    assert ("fm", "summarize") in s.fakes["apple"].calls
    turn = s.latency.recent(1)[0]
    assert (turn["model"], turn["kind"], turn["cost"]) == ("apple-on-device", "quick", 0)


def test_summary_falls_back_to_haiku_when_apple_cant(make_services):
    # Arrange: Apple Intelligence off on this Mac
    from maxie.budget import HAIKU
    from tests.fakes import FakeAnthropic, turn
    client = FakeAnthropic([turn("Offsite moved to Thursday the 22nd.")])
    s = make_services(client)
    c = TestClient(create_app(s, TOKEN, scheduler=False))

    # Act
    done = _chat(c, f"summarize this: {LETTER}")[-1]

    # Assert
    assert done["text"].strip() == "Offsite moved to Thursday the 22nd."
    assert client.calls[0]["model"] == HAIKU and done["cost"] > 0
    assert s.latency.recent(1)[0]["model"] == HAIKU


def test_speed_shows_who_answered_free_and_paid(make_services):
    s = make_services()
    s.fakes["apple"].fm_reply = "Short."
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    _chat(c, f"summarize this: {LETTER}")
    _chat(c, "what time is it")
    share = c.get("/api/latency", headers=H).json()["share"]
    assert {k: share[k] for k in ("apple", "local", "claude", "free_pct")} == {"apple": 1, "local": 1, "claude": 0, "free_pct": 100}
    assert share["daily"][-1]["apple"] == 1
