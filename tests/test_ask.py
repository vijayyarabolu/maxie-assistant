"""POST /api/ask for the iPhone's Action Button: short spoken reply, the full one on screen, test push."""

import re

from fastapi.testclient import TestClient

from maxie.hub.api import create_app
from maxie.spoken import MORE, spoken
from tests.fakes import FakeAnthropic, turn
from tests.test_api import TOKEN, H


def test_spoken_keeps_short_answers_and_cuts_long_ones():
    assert spoken("It's 9:41 AM.") == ("It's 9:41 AM.", False)
    long = "First thing. " * 40
    say, more = spoken(long)
    assert more and say.endswith(MORE) and len(say) <= 280 + len(MORE) + 1 and say.startswith("First thing. First thing.")
    giant = "word " * 100
    say, more = spoken(giant)
    assert more and say.endswith("… " + MORE) and len(say) < 300
    assert spoken("") == ("", False)


def test_ask_fast_path_and_phone_action(make_services):
    s = make_services()
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    r = c.post("/api/ask", json={"text": "what time is it"}, headers=H).json()
    assert re.fullmatch(r"It's \d+:\d\d [AP]M\.", r["reply"]) and r["full"] == r["reply"] and not r["more_on_screen"]
    assert r["first_ms"] is not None and r["first_ms"] < 300 and r["phone_action"] is None
    r = c.post("/api/ask", json={"text": "set a 5 minute timer"}, headers=H).json()
    assert r["phone_action"]["action"] == "timer" and r["reply"] == "5 minute timer started."
    conn = c.get("/api/connections", headers=H).json()
    assert conn["phone"]["last_ask"] is not None and conn["phone"]["pushes"] == 0


def test_ask_long_answer_says_more_on_screen(make_services):
    long = " ".join(f"Point {i} is important." for i in range(20))
    s = make_services(FakeAnthropic([turn(long)]))
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    r = c.post("/api/ask", json={"text": "explain the history of the roman empire in detail"}, headers=H).json()
    assert r["more_on_screen"] and r["reply"].endswith(MORE) and r["full"] == long
    hist = c.get("/api/history?conversation=phone", headers=H).json()
    assert hist[-1]["text"] == long  # the whole answer is on screen in the app


def test_push_test_needs_a_phone(make_services):
    s = make_services()
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    assert c.post("/api/push/test", headers=H).status_code == 409
    sent = []
    s.push._send = lambda sub, payload: sent.append(payload) or 201
    s.push.subscribe({"endpoint": "https://push.example/abc", "keys": {}})
    r = c.post("/api/push/test", headers=H).json()
    assert r == {"ok": True, "devices": 1} and "Notifications work" in sent[0]
