"""Maxie's wake words: "Hey Max", "Hey Maxie", "Max", "Maxie".

Runs on the Whisper transcript of one VAD speech segment. "Hey Max(ie)" wakes at the start of any
segment. A bare "Max"/"Maxie" is an ordinary word too (maximum, HBO Max, a friend called Max), so it
wakes only as the FIRST word of a segment that began after silence, and never when the next word makes
it ordinary ("max out", "Max is…", "Max's…"). Bare forms are marked `bare` so the loop can require his
voice (speaker check) before acting on them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ALL_FORMS = ("hey max", "hey maxie", "max", "maxie")

# How Whisper writes the two names.
_MAXIE = r"(?:maxie|maxi|maxy|maxey|maxee|macsy)"
_MAX = r"(?:max|macs)"
_GREETING = r"(?:hey|hi|ok|okay|yo)"
_HEY = re.compile(rf"^\W*{_GREETING}[\s,]+(?P<name>{_MAXIE}|{_MAX})\b(?!['’]s)[\s,.!?:]*", re.I)
_BARE = re.compile(rf"^\W*(?P<name>{_MAXIE}|{_MAX})\b(?!['’]s)(?P<sep>[\s,.!?:]*)", re.I)

# After a bare name, these first words mean he is talking ABOUT Max/max, not TO Maxie.
_ORDINARY_NEXT = frozenset("""
out it speed level volume power size value amount number capacity limit
is was and or has had will would said says told thinks wants likes went goes got
""".split())


@dataclass(frozen=True)
class WakeResult:
    woke: bool
    form: str = ""   # one of ALL_FORMS
    rest: str = ""   # the command said in the same breath

    @property
    def bare(self) -> bool:
        return self.form in ("max", "maxie")


NO_WAKE = WakeResult(False)


def _form(name: str, greeting: bool) -> str:
    base = "maxie" if re.fullmatch(_MAXIE, name, re.I) else "max"
    return f"hey {base}" if greeting else base


def detect(text: str, after_silence: bool, enabled: set[str] | frozenset[str] = frozenset(ALL_FORMS)) -> WakeResult:
    """Did this segment start with an enabled wake form? `after_silence`: the segment began after a pause
    (not a mid-sentence split), which a bare name needs."""
    text = text or ""
    m = _HEY.match(text)
    if m:
        form = _form(m["name"], greeting=True)
        return WakeResult(True, form, text[m.end():].strip()) if form in enabled else NO_WAKE
    m = _BARE.match(text)
    if not m or not after_silence:
        return NO_WAKE
    form = _form(m["name"], greeting=False)
    rest = text[m.end():].strip()
    next_word = re.sub(r"\W", "", rest.split()[0]).lower() if rest else ""
    paused = "," in m["sep"] or "." in m["sep"]
    if form not in enabled or (next_word in _ORDINARY_NEXT and not paused):
        return NO_WAKE
    return WakeResult(True, form, rest)
