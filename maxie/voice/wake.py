"""Maxie's wake words: "Hey Max", "Hey Maxie", "Max", "Maxie".

Runs on the Whisper transcript of one VAD speech segment. "Hey Max(ie)" wakes at the start of any
segment. A bare "Max"/"Maxie" is an ordinary word too (maximum, HBO Max, a friend called Max), so it
wakes only as the FIRST word of a segment that began after silence, and never when the next word makes
it ordinary ("max out", "Max is…", "Max's…"). Bare forms are marked `bare` so the loop can require his
voice (speaker check) before acting on them.

the spellings come from his wake words run through Whisper in simulated rooms ("Maxine", "Maxim"). A
garbled greeting ("Game Max", "He max") is not proof he addressed Maxie, so it counts as a bare name: his voice
and a pause before it. "Maxie was…" is Whisper hearing "Maxie, what's…", so "was" doesn't make it ordinary; the
wake prompt also turns "Max is…" into "Maxie is…", so every other ordinary next word still does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ALL_FORMS = ("hey max", "hey maxie", "max", "maxie")

# How Whisper writes the two names.
_MAXIE_SURE = r"(?:maxie|maxi|maxy|maxey|maxee|macsy)"
_MAXIE = rf"(?:{_MAXIE_SURE}|maxine|maxim)"
_MAX = r"(?:max|macs)"
_GREETING = r"(?:hey|hi|ok|okay|yo)"
_MISHEARD_HEY = r"(?:he|game|hay|k)"
_HEY = re.compile(rf"^\W*{_GREETING}[\s,]+(?P<name>{_MAXIE}|{_MAX})\b(?!['’]s)[\s,.!?:]*", re.I)
_BARE = re.compile(rf"^\W*(?:{_MISHEARD_HEY}[\s,]+)?(?P<name>{_MAXIE}|{_MAX})\b(?!['’]s)(?P<sep>[\s,.!?:]*)", re.I)

# After a bare name, these first words mean he is talking ABOUT Max/max, not TO Maxie.
_ORDINARY_NEXT = frozenset("""
out it speed level volume power size value amount number capacity limit
is was and or has had will would said says told thinks wants likes went goes got
""".split())


_NAME_WORD = re.compile(rf"(?:{_MAXIE}|{_MAX}|hey)", re.I)


def only_names(text: str) -> bool:
    """Nothing but the name (or nothing): on short audio Whisper echoes its wake prompt ("Maxie. Max") instead of
    the words that follow, so a rest like this says nothing yet."""
    return all(_NAME_WORD.fullmatch(w) for w in re.findall(r"[\w']+", text or ""))


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
    misheard_whats = next_word == "was" and re.fullmatch(_MAXIE_SURE, m["name"], re.I) is not None
    if form not in enabled or (next_word in _ORDINARY_NEXT and not paused and not misheard_whats):
        return NO_WAKE
    return WakeResult(True, form, rest)
