"""everything he sees or hears says Maxie. The name audit runs over the repo in `make test` / CI."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("audit_names", ROOT / "scripts" / "audit_names.py")
audit = importlib.util.module_from_spec(spec)
sys.modules["audit_names"] = audit  # dataclasses look their module up
spec.loader.exec_module(audit)


def test_the_repo_says_maxie():
    hits = audit.run(ROOT)
    assert hits == [], "\n".join(f"{h.path}:{h.line}: {h.text}" for h in hits)


def test_old_names_are_caught(tmp_path):
    rules = audit.load_allow("")
    for text in ("Where the bot lives", "the robot head", "a screen face", "Hey Claude, hi", "ear cups",
                 "the Sunset bot", "Claude bot", "an antenna"):
        assert audit.offends(text, "x.swift", rules), text


def test_maxie_and_classic_robot_pass():
    rules = audit.load_allow("*\t(?i)classic[ _-]?robot\n")
    for text in ("Maxie lives at the notch", "the Classic robot, built in", "testClassicRobotBorrows",
                 "the bottom edge", "a robotic voice? no: the bots folder"):
        offends = audit.offends(text, "x.swift", rules)
        assert not offends or "robotic" in text, text


def test_allow_rules_are_per_path():
    rules = audit.load_allow("web/mascot.js\t.*\n# a comment\n\ntests/test_voice.py\t(?i)hey claude.*# retired\n")
    assert not audit.offends("const antenna = new THREE.Group()", "web/mascot.js", rules)
    assert audit.offends("const antenna = new THREE.Group()", "web/maxie.js", rules)
    assert not audit.offends('is_wake("Hey Claude") == (False, "")  # retired', "tests/test_voice.py", rules)


def test_assets_prompts_and_json_are_skipped():
    assert audit.skipped("assets/mascot-classic/idle/meta.json")
    assert audit.skipped("web/manifest.json")
    assert not audit.skipped("web/maxie.js")
