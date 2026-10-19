"""Every structured decision is a typed choice: which route, which direct action, yes or no.

Each decider (rules, the keyword lexicon, Laya, later Apple on-device) is scored per decision type on labelled
examples: how often it answers, how often it is right, and how fast (p50). A decider goes live for a type only
when its calibrated thresholds reach >= 95% precision on at least `min_examples` labels; then the fastest live
one answers first and Haiku stays the last resort. Shown in Settings › Speed.
"""

from __future__ import annotations

import json
import re
import statistics
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from . import lexicon, rules
from .calibrate import NEVER, calibrate, load_labels
from .laya import ACTION_CRITERIA, ROUTE_CRITERIA, LayaClient

Predict = Callable[[str], "tuple[str, float] | None"]
TARGET = 0.95
HERE = Path(__file__).resolve().parent
STARTER_LABELS = HERE / "starter_labels.jsonl"
YES_NO_LABELS = HERE / "yes_no_labels.jsonl"
EVAL_LABELS = HERE.parents[1] / "evals" / "commands.jsonl"
LAYA_DOWN = "Laya is not running"
RULES_NOTE = "exact patterns: always on when they match"


@dataclass(frozen=True)
class DecisionType:
    name: str
    instructions: str
    criteria: dict[str, str]
    min_examples: int


TYPES = {
    "route": DecisionType("route", "What kind of request is this?", ROUTE_CRITERIA, 200),
    "action": DecisionType("action", "Which direct Mac action is it, if any?", ACTION_CRITERIA, 100),
    "yes_no": DecisionType("yes_no", "Is he saying yes or no to what Maxie asked?",
                           {"yes": "agrees: yes, go ahead, sure, do it", "no": "declines: no, cancel, not now, stop"}, 40),
}


@dataclass(frozen=True)
class DeciderScore:
    decider: str
    n: int
    answered: int
    accuracy: float  # of the answers it gave, how many were right
    coverage: float  # share of examples it answers at >= 95% precision (its calibrated thresholds)
    p50_ms: float
    live: bool
    note: str = ""


YES = re.compile(r"^(?:yes|yeah|yep|yup|sure|ok(?:ay)?|go ahead|do it|please do|sounds good|send it|confirm)\b", re.I)
NO = re.compile(r"^(?:no|nope|nah|not now|don'?t|do not|cancel|stop|never ?mind|hold on|wait)\b", re.I)


def rules_yes_no(text: str) -> tuple[str, float] | None:
    t = text.strip()
    if NO.match(t):
        return "no", 1.0
    if YES.match(t):
        return "yes", 1.0
    return None


def score(name: str, examples: list[tuple[str, str]], predict: Predict, min_examples: int,
          target: float = TARGET) -> DeciderScore:
    answers: dict[str, tuple[str, float] | None] = {}
    times = []
    for text, _ in examples:
        t0 = time.perf_counter()
        answers[text] = predict(text)
        times.append((time.perf_counter() - t0) * 1000)
    given = [(answers[t], label) for t, label in examples if answers[t] is not None]
    cal = calibrate(examples, answers.get, target, min_support=max(2, min(5, min_examples // 40)),
                    min_examples=min_examples)
    confident = [(a, label) for a, label in given if a[1] >= cal.threshold(a[0], NEVER)]
    right = sum(a[0] == label for a, label in given)
    return DeciderScore(name, len(examples), len(given), round(right / len(given), 4) if given else 0.0,
                        round(len(confident) / len(examples), 4) if examples else 0.0,
                        round(statistics.median(times), 3) if times else 0.0, cal.calibrated and bool(confident))


def benchmark(type_name: str, examples: list[tuple[str, str]], deciders: dict[str, Predict | None],
              min_examples: int | None = None, notes: dict[str, str] | None = None) -> dict:
    need = min_examples if min_examples is not None else TYPES[type_name].min_examples
    notes = notes or {}
    scores = [replace(score(name, examples, p, need), note=notes.get(name, "")) if p is not None
              else DeciderScore(name, len(examples), 0, 0.0, 0.0, 0.0, False, notes.get(name, ""))
              for name, p in deciders.items()]
    live = sorted((s for s in scores if s.live), key=lambda s: s.p50_ms)
    return {"type": type_name, "n": len(examples), "deciders": [asdict(s) for s in scores],
            "best": live[0].decider if live else None}


# --- the labelled sets and the deciders we have ---------------------------------------------------------

def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def _dedupe(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: dict[str, str] = {}
    for text, label in pairs:
        seen.setdefault(text, label)
    return list(seen.items())


def label_sets(his_routes: list[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    evals = _jsonl(EVAL_LABELS)
    routes = load_labels(STARTER_LABELS) + [(r["text"], r["route"]) for r in evals if r["route"] in ROUTE_CRITERIA]
    actions = [(r["text"], r.get("action") if r.get("action") in ACTION_CRITERIA else "none") for r in evals]
    return {"route": _dedupe(his_routes + routes), "action": _dedupe(actions),
            "yes_no": _dedupe([(r["text"], r["label"]) for r in _jsonl(YES_NO_LABELS)])}


def _rules_route(text: str) -> tuple[str, float] | None:
    hit = rules.match(text)
    return (hit.route, 1.0) if hit else None


def _lexicon_route(text: str) -> tuple[str, float]:
    route, conf, _ = lexicon.classify(text)
    return route, conf


def _rules_action(text: str) -> tuple[str, float] | None:
    hit = rules.match(text) or rules.extract(text)
    if hit is None or hit.route != "mac_action":
        return None
    return ("open_app" if hit.action == "open_url" else hit.action if hit.action in ACTION_CRITERIA else "none"), 1.0


def run_all(his_routes: list[tuple[str, str]], laya: LayaClient | None) -> dict:
    """Scores every decider on every decision type. Laya does the heavy part in its own (low-priority)
    process; when it isn't running, its rows say so and nothing else changes."""
    up = laya is not None and laya.healthy()
    memo: dict[str, object] = {}

    def laya_full(text: str):
        if text not in memo:
            memo[text] = laya.classify(text) if laya else None
        return memo[text]

    def laya_route(text):
        r = laya_full(text)
        return (r.route, r.route_p) if r else None

    def laya_action(text):
        r = laya_full(text)
        return (r.action, r.action_p) if r else None

    def laya_yes_no(text):
        t = TYPES["yes_no"]
        return laya.decide(text, t.instructions, t.criteria) if laya else None

    sets = label_sets(his_routes)
    notes = {"rules": RULES_NOTE, **({} if up else {"laya": LAYA_DOWN})}
    deciders = {
        "route": {"rules": _rules_route, "lexicon": _lexicon_route, "laya": laya_route if up else None},
        "action": {"rules": _rules_action, "laya": laya_action if up else None},
        "yes_no": {"rules": rules_yes_no, "laya": laya_yes_no if up else None},
    }
    return {"updated": time.time(),
            "types": [benchmark(name, sets[name], deciders[name], notes=notes) for name in TYPES]}
