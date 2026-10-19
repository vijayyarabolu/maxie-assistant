"""Maxie runs only its own "Maxie …" shortcuts (never his others) with input, and reads the output."""

import subprocess

import pytest

from maxie.db import DB
from maxie.hands.registry import Registry
from maxie.hands.shortcuts import PREFIX, ShortcutMissing, Shortcuts, register
from maxie.safety import AuditLog, Confirmations


class FakeCLI:
    def __init__(self, names, answer="Thursday the 22nd.", code=0):
        self.names, self.answer, self.code, self.calls = names, answer, code, []

    def __call__(self, args, **kw):
        self.calls.append(args)
        if args[:2] == ["shortcuts", "list"]:
            return subprocess.CompletedProcess(args, 0, "\n".join(self.names) + "\n", "")
        with open(args[args.index("--input-path") + 1]) as f:
            self.calls.append(("input", f.read()))
        with open(args[args.index("--output-path") + 1], "w") as f:
            f.write(self.answer if self.code == 0 else "")
        return subprocess.CompletedProcess(args, self.code, "", "" if self.code == 0 else "boom")


def test_lists_only_maxies_own_shortcuts():
    cli = FakeCLI(["Words", "Maxie Ask Apple", "Morning", "Maxie Focus"])
    assert Shortcuts(run=cli).installed() == ["Maxie Ask Apple", "Maxie Focus"]


def test_runs_a_maxie_shortcut_with_input_and_reads_its_output():
    # Arrange
    cli = FakeCLI(["Maxie Ask Apple"])

    # Act
    out = Shortcuts(run=cli).run("Maxie Ask Apple", "When is the offsite?")

    # Assert
    assert out == "Thursday the 22nd."
    assert ("input", "When is the offsite?") in cli.calls


def test_never_runs_his_other_shortcuts():
    cli = FakeCLI(["Words"])
    with pytest.raises(ValueError):
        Shortcuts(run=cli).run("Words", "x")
    assert not any(isinstance(c, list) and c[:2] == ["shortcuts", "run"] for c in cli.calls)


def test_a_missing_shortcut_says_how_to_make_it():
    with pytest.raises(ShortcutMissing) as e:
        Shortcuts(run=FakeCLI([])).run(f"{PREFIX}Ask Apple", "x")
    assert "Shortcuts app" in e.value.message()


def test_a_failing_shortcut_is_reported():
    with pytest.raises(RuntimeError):
        Shortcuts(run=FakeCLI(["Maxie Ask Apple"], code=1)).run("Maxie Ask Apple", "x")


def test_ask_apple_tool_answers_or_says_what_is_missing():
    db = DB()
    reg = Registry(AuditLog(db), Confirmations(db))
    register(reg, Shortcuts(run=FakeCLI([])))
    assert "Maxie Ask Apple" in reg.get("ask_apple").fn({"question": "hi"})
    reg2 = Registry(AuditLog(db), Confirmations(db))
    register(reg2, Shortcuts(run=FakeCLI(["Maxie Ask Apple"])))
    assert reg2.get("ask_apple").fn({"question": "hi"}) == "Thursday the 22nd."
