"""Speed is measured on every turn: the log, the summary, the API, and the routing shortcut."""

from datetime import datetime
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from maxie.brain import CACHE, Brain
from maxie.db import DB
from maxie.hub.api import create_app
from maxie.latency import Latency, Turn, kind_of
from maxie.router import Router
from maxie.router.core import LEXICON_TRUST
from tests.test_api import TOKEN, H


def test_kind_of():
    assert kind_of(True, None) == "action"
    assert kind_of(False, "claude-haiku-5-5") == "quick"
    assert kind_of(False, "claude-sonnet-5-5") == "heavy"


def test_summary_medians_and_targets(clock):
    lat = Latency(DB(), "America/Chicago", clock)
    for first in (20, 30, 40):
        lat.record(Turn("action", "open x", "mac_action", "rules", None, 5, first, first, 0))
    lat.record(Turn("quick", "capital?", "quick_question", "lexicon", "claude-haiku-5-5", 1, 650, 900, 0.0002))
    lat.record(Turn("quick", "fact", "quick_question", "lexicon", "claude-haiku-5-5", 1, 900, 1200, 0.0002))
    s = lat.summary()
    assert s["action"]["n"] == 3 and s["action"]["first_p50"] == 30 and s["action"]["ok"] and s["action"]["met"] == 3
    assert s["quick"]["n"] == 2 and s["quick"]["met"] == 1 and s["quick"]["first_p50"] == 775 and not s["quick"]["ok"]
    assert s["heavy"]["n"] == 0 and not s["heavy"]["ok"]
    assert lat.line() == "first word: action 30 ms · quick 775 ms (today)"
    clock.set(datetime(2026, 10, 16, 9, 0, tzinfo=ZoneInfo("America/Chicago")))  # tomorrow: today is empty, the week is not
    assert lat.summary()["action"]["n"] == 0 and lat.summary(7)["action"]["n"] == 3 and lat.line() == "no turns yet today"


def test_every_turn_lands_in_the_log_and_the_api(make_services):
    s = make_services()
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    c.post("/api/chat", json={"text": "open spotify"}, headers=H)
    c.post("/api/chat", json={"text": "what time is it"}, headers=H)
    r = c.get("/api/latency", headers=H).json()
    assert r["today"]["action"]["n"] == 2 and r["today"]["action"]["first_p50"] < 300 and r["targets"]["quick"] == 700
    assert r["recent"][0]["text"] == "what time is it" and r["recent"][0]["kind"] == "action"
    assert r["line"].startswith("first word: action")


def test_confident_lexicon_skips_the_haiku_round_trip():
    class NoHaiku:
        def classify(self, text):
            raise AssertionError("Haiku must not be asked when the lexicon is sure")
    r = Router(DB(), haiku=NoHaiku())
    d = r.route("what's the capital of France?")
    assert d.source == "lexicon" and d.route == "quick_question" and d.confidence >= LEXICON_TRUST and not d.direct
    assert "haiku" in d.model


def test_unsure_lexicon_still_asks_haiku():
    asked = []

    class Haiku:
        def classify(self, text):
            asked.append(text)
            return {"route": "calendar", "action": "none", "args": {}, "confidence": 0.9}
    r = Router(DB(), haiku=Haiku())
    d = r.route("ravi tomorrow")  # too thin for the keywords
    assert asked and d.source == "haiku" and d.route == "calendar"



async def test_warm_writes_the_hour_cache_without_generating(make_services):
    calls = []

    class Client:
        class messages:
            @staticmethod
            async def create(**p):
                calls.append(p)
                return NS(model=p["model"], usage={"input_tokens": 5, "output_tokens": 0,
                                                   "cache_creation_input_tokens": 4000, "cache_read_input_tokens": 0})
    s = make_services()
    brain = Brain(Client(), s.registry, s.budget, s.memory, s.kill, models=s.models)
    assert await brain.warm("claude-haiku-5-5")
    p = calls[0]
    assert p["max_tokens"] == 0 and "betas" not in p and "fallbacks" not in p
    assert p["system"][0]["cache_control"] == CACHE == {"type": "ephemeral", "ttl": "1h"}
    assert p["cache_control"] == CACHE and p["tools"][-1]["cache_control"] == CACHE
    assert s.budget.status().month_usd > 0  # the write is counted like any spend
    assert not await Brain(None, s.registry, s.budget, s.memory, s.kill).warm("claude-haiku-5-5")


def test_free_share_splits_apple_local_and_claude(clock):
    lat = Latency(DB(), "America/Chicago", clock)
    lat.record(Turn("quick", "summarize", "text_task", "rules", "apple-on-device", 1, 900, 1900, 0.0))
    lat.record(Turn("action", "time", "quick_question", "rules", None, 1, 5, 5, 0.0))
    lat.record(Turn("quick", "fact", "quick_question", "lexicon", "claude-haiku-5-5", 1, 650, 900, 0.0002))
    lat.record(Turn("heavy", "ask", "ask_claude", "rules", "claude-code", 1, 650, 900, 0.0))
    share = lat.share()
    assert {k: share[k] for k in ("apple", "local", "claude", "free_pct", "days")} == \
        {"apple": 1, "local": 1, "claude": 2, "free_pct": 50, "days": 7}
    assert len(share["daily"]) == 7 and share["daily"][-1] == {"day": "Thu", "apple": 1, "local": 1, "claude": 2}
    assert share["daily"][0] == {"day": "Fri", "apple": 0, "local": 0, "claude": 0}
    assert kind_of(True, "apple-on-device") == "quick" and kind_of(False, "apple-on-device") == "quick"
