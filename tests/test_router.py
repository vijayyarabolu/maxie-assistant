import json

import httpx
import pytest

from maxie.budget import HAIKU, OPUS, SONNET, Budget
from maxie.db import DB
from maxie.router import Router
from maxie.router.calibrate import Calibration, calibrate
from maxie.router.haiku import HaikuRouter
from maxie.router.laya import LayaClient
from maxie.router.rules import calc, match
from tests.fakes import FakeHaikuSync


@pytest.mark.parametrize("text,route,action,args", [
    ("open spotify", "mac_action", "open_app", {"name": "Spotify"}),
    ("Hey Max, set a 10 minute timer for pasta.", "mac_action", "timer", {"minutes": 10.0, "label": "pasta"}),
    ("set a 30 second timer", "mac_action", "timer", {"minutes": 0.5, "label": ""}),
    ("volume to 30", "mac_action", "volume", {"level": "30"}),
    ("pause", "mac_action", "music", {"command": "pause"}),
    ("Max, stop.", "control", "stop", {}),
    ("Remember that Jordan likes sushi", "memory", "remember", {"fact": "Jordan likes sushi"}),
    ("What did I decide about the Corolla?", "memory", "recall", {"q": "the Corolla"}),
    ("what do you know about Jordan", "memory", "recall", {"q": "Jordan"}),
    ("do you remember my gym plan?", "memory", "recall", {"q": "my gym plan"}),
    ("what do you know about me", "memory", "show", {}),
    ("what's 15% of 80", "quick_question", "math", {"result": "12", "expr": "what's 15% of 80"}),
])
def test_rules(text, route, action, args):
    hit = match(text)
    assert (hit.route, hit.action, hit.args) == (route, action, args)


def test_rules_leave_the_rest_alone():
    for text in ["open my resume", "what's the capital of France", "play a game with me", "email Mark"]:
        assert match(text) is None, text


def test_calc_is_arithmetic_only():
    assert calc("2 + 3 * 4") == 14
    assert calc("__import__('os')") is None
    assert calc("9 ** 9 ** 9") is None


def laya_transport(route="mac_action", route_p=0.97, action="music", risk=0.1, fail=False):
    def handler(request: httpx.Request):
        if fail:
            return httpx.Response(503)
        assert request.headers["authorization"] == "Bearer k"
        body = json.loads(request.content)
        assert set(body["questions"]) == {"route", "action", "risk"}
        return httpx.Response(200, json={"model": "laya", "answers": {
            "route": {"type": "choice", "choice": route, "probabilities": {route: route_p, "chitchat": 1 - route_p}},
            "action": {"type": "choice", "choice": action, "probabilities": {action: 0.9, "none": 0.1}},
            "risk": {"type": "score", "score": risk * 2, "probabilities": {"0": 0.9, "1": 0.1, "2": 0.0}},
        }})
    return httpx.MockTransport(handler)


def laya(**kw):
    return LayaClient("http://laya", "k", client=httpx.Client(transport=laya_transport(**kw)))


def calibrated_router(**kw):
    """A router whose calibration trusts mac_action above 0.9 (what his labels would produce)."""
    r = Router(DB(), laya=laya(**kw))
    r.calibration = Calibration(calibrated=False, thresholds={"mac_action": 0.9})
    return r


def test_laya_confident_harmless_runs_direct():
    d = calibrated_router().route("i wanna listen to jazz")
    assert (d.source, d.route, d.action, d.args) == ("laya", "mac_action", "music", {"command": "play", "query": "jazz"})


def test_laya_low_confidence_goes_to_claude():
    r = calibrated_router(route_p=0.6)
    d = r.route("i wanna listen to jazz")
    # below the threshold Laya's route is not trusted: the lexicon (no Haiku here) decides, and Laya's
    # guess is still logged for calibration
    assert d.source == "lexicon" and d.action is None and d.model in (HAIKU, SONNET)
    assert r.recent(1)[0]["laya_route"] == "mac_action" and r.recent(1)[0]["laya_p"] == 0.6


def test_laya_uncalibrated_route_is_never_trusted():
    r = Router(DB(), laya=laya(route_p=0.99))
    d = r.route("i wanna listen to jazz")
    assert d.source != "laya" and r.recent(1)[0]["laya_p"] == 0.99


def test_laya_risky_goes_to_claude():
    d = calibrated_router(risk=0.9).route("i wanna listen to jazz")
    assert d.action is None


def test_uncalibrated_only_harmless_actions():
    r = calibrated_router(action="none")
    assert r.route("i want to focus, turn on dnd please ok").action is None  # do_not_disturb not harmless-listed
    r.calibration = Calibration(calibrated=True, n=250, thresholds={"mac_action": 0.9})
    assert "do_not_disturb" in r.allowed_direct()


def test_laya_down_falls_back_to_haiku_then_lexicon():
    db = DB()
    budget = Budget(db)
    haiku = HaikuRouter(FakeHaikuSync(json.dumps({"route": "calendar", "action": "none", "args_json": "{}",
                                                   "confidence": 0.9})), budget)
    d = Router(db, laya=laya(fail=True), haiku=haiku).route("am i busy at noon")
    assert d.source == "haiku" and d.route == "calendar"
    assert budget.status().month_usd > 0  # the Haiku call was counted
    d = Router(DB(), laya=laya(fail=True)).route("am i busy at noon")
    assert d.source == "lexicon"


def test_model_tiers():
    r = Router(DB())
    assert r.route("hello there").model == HAIKU
    assert r.route("check my inbox").model == SONNET
    assert r.route("think hard about my offer").model == OPUS


def test_route_log_and_labels():
    r = Router(DB())
    r.route("check my inbox")
    row = r.recent(1)[0]
    r.label(row["id"], "email")
    assert r.labelled() == [("check my inbox", "email")]
    with pytest.raises(ValueError):
        r.label(row["id"], "nope")


def test_calibrate_picks_thresholds_and_needs_200():
    examples = [(f"a{i}", "email") for i in range(150)] + [(f"b{i}", "calendar") for i in range(60)]

    def predict(text):  # over-confident: half its 0.9 guesses are wrong "email"
        i = int(text[1:])
        if text[0] == "a":
            return "email", 0.99
        return ("calendar", 0.97) if i < 30 else ("email" if i % 2 else "calendar", 0.9)

    cal = calibrate(examples, predict)
    assert cal.calibrated and cal.n == 210
    assert cal.thresholds["email"] == 0.99  # wrong "email" guesses sit at 0.9
    assert cal.thresholds["calendar"] == 0.9
    assert not calibrate(examples[:100], predict).calibrated
