"""Settings › Voice › Test wake word: what Maxie heard, the score, and why it did or didn't wake."""

import pytest

from maxie.voice.speaker import Verdict
from maxie.voice.wake import ALL_FORMS
from maxie.voice.wake_test import explain

HIS = Verdict(True, 0.55, 0.42, "his voice")
LOW = Verdict(False, 0.31, 0.42, "not your voice")
HEY = Verdict(True, 0.36, 0.42, "hey form")


@pytest.mark.parametrize("text, verdict, forms, after_silence, woke, words", [
    ("Hey Maxie.", HEY, ALL_FORMS, True, True, "would wake"),
    ("Maxie, what's on my calendar", HIS, ALL_FORMS, True, True, "0.55"),
    ("Maxie.", LOW, ALL_FORMS, True, False, "0.31"),
    ("Maxie.", Verdict(False, None, 0.6, "not enrolled"), ALL_FORMS, True, False, "teach"),
    ("Max.", HIS, ("hey max", "hey maxie", "maxie"), True, False, "turned off"),
    ("Max.", HIS, ALL_FORMS, False, False, "pause"),
    ("The maximum is ten.", None, ALL_FORMS, True, False, "doesn't start"),
    ("", None, ALL_FORMS, True, False, "nothing"),
])
def test_explains_each_outcome_in_words(text, verdict, forms, after_silence, woke, words):
    # Act
    out = explain(text, verdict, frozenset(forms), after_silence)

    # Assert
    assert out["woke"] is woke
    assert words in out["why"].lower()
    assert out["heard"] == text


def test_the_score_and_the_bar_are_reported():
    out = explain("Maxie.", LOW, frozenset(ALL_FORMS), True)
    assert out["score"] == 0.31 and out["threshold"] == 0.42 and out["form"] == "maxie"
