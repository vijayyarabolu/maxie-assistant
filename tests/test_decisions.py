"""every structured decision is a typed choice; each decider (rules, lexicon, Laya…) is scored per
decision type on labelled examples and goes live only at >= 95% precision with enough labels."""

from fastapi.testclient import TestClient

from maxie.hub.api import create_app
from maxie.router.decisions import TYPES, benchmark, rules_yes_no, score
from tests.test_api import TOKEN, H

YES_NO = [("yes", "yes"), ("yeah go ahead", "yes"), ("sure", "yes"), ("do it", "yes"),
          ("no", "no"), ("nope", "no"), ("not now", "no"), ("don't", "no"), ("cancel that", "no")]


def test_rules_answer_plain_yes_and_no_and_stay_quiet_otherwise():
    assert rules_yes_no("Yeah, go ahead.") == ("yes", 1.0)
    assert rules_yes_no("nope") == ("no", 1.0)
    assert rules_yes_no("hmm what was the address again") is None


def test_a_perfect_decider_with_enough_labels_goes_live():
    # Arrange
    truth = dict(YES_NO)

    # Act
    s = score("oracle", YES_NO, lambda t: (truth[t], 0.99), min_examples=5)

    # Assert
    assert (s.accuracy, s.coverage, s.answered, s.live) == (1.0, 1.0, 9, True)
    assert s.p50_ms >= 0


def test_a_coin_flip_never_goes_live():
    s = score("coin", YES_NO, lambda t: ("yes", 0.9), min_examples=5)
    assert not s.live and s.accuracy < 0.95


def test_too_few_labels_keeps_even_a_good_decider_off():
    truth = dict(YES_NO)
    s = score("oracle", YES_NO, lambda t: (truth[t], 0.99), min_examples=200)
    assert s.accuracy == 1.0 and not s.live


def test_a_decider_that_is_down_counts_as_unanswered():
    s = score("laya", YES_NO, lambda t: None, min_examples=5)
    assert (s.answered, s.coverage, s.live) == (0, 0.0, False)


def test_benchmark_picks_the_fastest_live_decider():
    import time
    truth = dict(YES_NO)

    def slow(t):
        time.sleep(0.002)
        return truth[t], 0.99

    report = benchmark("yes_no", YES_NO, {"slow": slow, "fast": lambda t: (truth[t], 0.99), "bad": lambda t: ("no", 0.5)},
                       min_examples=5)
    assert report["best"] == "fast"
    assert [d["decider"] for d in report["deciders"]] == ["slow", "fast", "bad"]


def test_every_type_has_choices_and_a_question():
    for name, t in TYPES.items():
        assert t.criteria and t.instructions and t.min_examples > 0, name
    assert {"route", "action", "yes_no"} <= set(TYPES)


def test_api_runs_the_benchmark_without_laya_and_saves_it(make_services):
    s = make_services()
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    assert c.get("/api/decisions", headers=H).json()["types"] == []
    r = c.post("/api/decisions/run", headers=H).json()
    types = {t["type"]: t for t in r["types"]}
    assert set(types) == {"route", "action", "yes_no"}
    route = {d["decider"]: d for d in types["route"]["deciders"]}
    assert route["laya"]["note"] == "Laya is not running" and route["laya"]["answered"] == 0
    assert route["rules"]["answered"] > 0 and route["lexicon"]["answered"] > 0
    assert c.get("/api/decisions", headers=H).json()["types"] == r["types"]
