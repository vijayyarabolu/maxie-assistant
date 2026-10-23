"""Settings › Voice › "Test wake word": he says a wake word once; Maxie shows what it heard, the voice
score against the bar, and why it would or wouldn't wake, in words. Nothing runs: the panel stays shut."""

from __future__ import annotations

from .speaker import Verdict
from .wake import ALL_FORMS, detect

TEST_LISTEN_S = 8.0


def explain(text: str, verdict: Verdict | None, forms: frozenset[str], after_silence: bool) -> dict:
    """The result Settings shows. `verdict` is the speaker check's (None when nothing woke the grammar)."""
    out = {"heard": text, "form": "", "woke": False, "score": None, "threshold": None, "why": ""}
    if not text.strip():
        return {**out, "why": "I heard nothing. Is the right microphone picked above?"}
    any_form = detect(text, True, frozenset(ALL_FORMS))
    if not any_form.woke:
        return {**out, "why": "That doesn't start with “Hey Maxie”, “Maxie” or “Max”."}
    out["form"] = any_form.form
    if any_form.form not in forms:
        return {**out, "why": f"“{any_form.form.title()}” is turned off above."}
    if any_form.bare and not after_silence:
        return {**out, "why": "A bare name has to come first, after a short pause."}
    if verdict is not None:
        out.update(score=None if verdict.score is None else round(verdict.score, 3), threshold=round(verdict.threshold, 3))
    if verdict is None or verdict.reason == "hey form":
        voice = "" if out["score"] is None else f" Your voice scored {out['score']:.2f}."
        return {**out, "woke": True, "why": f"Would wake: “{any_form.form.title()}” works for anyone.{voice}"}
    if verdict.reason == "not enrolled":
        return {**out, "why": "A bare name needs your voice: teach Maxie your voice first, or say “Hey Maxie”."}
    if verdict.woke:
        return {**out, "woke": True, "why": f"Would wake: your voice scored {verdict.score:.2f}, the bar is {verdict.threshold:.2f}."}
    return {**out, "why": f"Your voice scored {verdict.score:.2f}, under the bar of {verdict.threshold:.2f}. "
                          "Re-train your voice, or say “Hey Maxie”."}
