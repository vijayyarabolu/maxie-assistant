"""Shorten a reply for the iPhone's Speak Text: a few sentences out loud, the rest stays on screen."""

from __future__ import annotations

import re

MAX_SPOKEN_CHARS = 280
MAX_SPOKEN_SENTENCES = 3
MORE = "More on screen."

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def spoken(reply: str) -> tuple[str, bool]:
    """(what to say, whether the full answer is longer than what is said)."""
    text = " ".join(reply.split())
    if not text:
        return "", False
    if len(text) <= MAX_SPOKEN_CHARS and len(_SENTENCE.split(text)) <= MAX_SPOKEN_SENTENCES:
        return text, False
    out: list[str] = []
    for sentence in _SENTENCE.split(text):
        if out and (len(" ".join([*out, sentence])) > MAX_SPOKEN_CHARS or len(out) >= 2):
            break
        out.append(sentence)
    said = " ".join(out)
    if len(said) > MAX_SPOKEN_CHARS:  # one giant sentence: cut at a word
        said = said[:MAX_SPOKEN_CHARS].rsplit(" ", 1)[0] + "…"
    return f"{said} {MORE}", True
