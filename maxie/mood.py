"""Alive, not an app: Maxie reacts to real life.

- Good news in his words ("I got the offer", "nailed the interview") -> happy dance for a few seconds.
- A hard day ("rough day", "got rejected", "so tired of this") -> gentle: Maxie softens and leans in,
  and Maxie is told to be warm and brief.
- Late night (11 PM - 5 AM) -> Maxie suggests rest once, gently, and Maxie looks sleepy for a moment.
- A hello ("hi Maxie", "good morning") -> a wave.
- A long work stretch (talking to Maxie for 90+ minutes with no real break) -> once, a stretch break.
- Idle moments: very rarely, when nothing is happening, Maxie glances around or hops. At most three a
  day, two hours apart, never within 30 minutes of a conversation, never in quiet hours.
Everything here is local word matching: no model call, no cost.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

GOOD = re.compile(
    r"\b(?:got (?:the |an? )?(?:job|offer|role|internship|promotion|raise)|(?:i|we) got (?:in|it|hired|accepted)"
    r"|offer letter|signed the offer|passed (?:the|my)|nailed (?:it|the)|aced (?:it|the)|good news|great news"
    r"|went (?:really )?(?:well|great)|(?:i|we) won|got promoted|accepted (?:me|my)|moved to (?:the )?(?:next|final) round)\b",
    re.I)
LOW = re.compile(
    r"\b(?:rough|bad|terrible|awful|long|hard) day|got rejected|rejection|didn'?t get (?:it|the)|turned (?:me )?down"
    r"|ghosted|so tired|exhausted|burn(?:ed|t) out|stressed|anxious|overwhelmed|feel(?:ing)? (?:low|down|sad|awful|terrible)"
    r"|depressed|hopeless|giving up|can'?t do this|struggling|miserable\b",
    re.I)
NOT_GOOD = re.compile(r"\b(?:didn'?t|did not|never|no) (?:get|pass|win)\b", re.I)
HELLO = re.compile(r"^\W*(?:hi|hey|hello|hiya|yo|good (?:morning|afternoon|evening))\b[\s,]*(?:maxie|there)?\W*$", re.I)
LATE_FROM_H, LATE_UNTIL_H = 23, 5

GOOD_HOLD_S = 8.0
GENTLE_HOLD_S = 45.0
LATE_HOLD_S = 20.0
WAVE_HOLD_S = 4.0
GOOD_TONE = "They are sharing good news. Be genuinely glad with them, warm and brief."
LOW_TONE = "He sounds low right now. Be gentle, warm and brief: no lists, no pep talk, no advice unless he asks."
LATE_TONE = "It is late at night for them. Answer, then once and gently suggest getting some rest."


@dataclass(frozen=True)
class Reaction:
    state: str | None   # bot state to show after the reply (None: leave it)
    hold: float         # seconds before it returns to idle / sleep
    tone: str           # one line for Maxie about how to sound


def is_late(now: datetime) -> bool:
    return now.hour >= LATE_FROM_H or now.hour < LATE_UNTIL_H


def react(text: str, now: datetime) -> Reaction | None:
    """How Maxie (and Maxie's tone) should respond to what he just said."""
    if LOW.search(text) or NOT_GOOD.search(text):
        return Reaction("gentle", GENTLE_HOLD_S, LOW_TONE)
    if GOOD.search(text):
        return Reaction("happy", GOOD_HOLD_S, GOOD_TONE)
    if is_late(now):
        return Reaction("sleep", LATE_HOLD_S, LATE_TONE)
    if HELLO.match(text.strip()):
        return Reaction("wave", WAVE_HOLD_S, "")
    return None


MOMENT_KINDS = ("glance", "hop")
MOMENT_GAP_S = 2 * 3600
MOMENT_AFTER_TURN_S = 30 * 60
MOMENTS_PER_DAY = 3
MOMENT_CHANCE = 0.05  # per minute tick once allowed: about one every 20 minutes of eligible idle time
STRETCH_AFTER_S = 90 * 60   # this long working with Maxie ...
STRETCH_BREAK_S = 20 * 60   # ... with no gap this long between turns (a gap this long is a real break)
STRETCH_HOLD_S = 20.0


class Moments:
    """Rare idle personality moments, published as {"type": "moment", "kind": ...} on the events stream, and
    the stretch break after a long work stretch (the "stretch" state)."""

    def __init__(self, events, quiet: Callable[[], bool], enabled: Callable[[], bool] = lambda: True,
                 clock: Callable[[], float] = time.time, rand: Callable[[], float] = random.random):
        self.events, self.quiet, self.enabled, self.clock, self.rand = events, quiet, enabled, clock, rand
        self.last_turn = 0.0
        self.last_moment = 0.0
        self.work_since = 0.0  # when the current work stretch began (0: none, or its break was already shown)
        self._day, self._today = "", 0

    def note_turn(self) -> None:
        now = self.clock()
        if now - self.last_turn >= STRETCH_BREAK_S:
            self.work_since = now
        self.last_turn = now

    def _stretch_due(self, now: float) -> bool:
        return (self.work_since > 0 and now - self.last_turn < STRETCH_BREAK_S
                and now - self.work_since >= STRETCH_AFTER_S)

    def tick(self) -> str | None:
        now = self.clock()
        if not self.enabled() or self.quiet() or self.events.current()["state"] != "idle":
            return None
        if self._stretch_due(now):
            self.work_since = 0.0  # once per stretch; the next one starts after a real break
            self.events.set_state("stretch", STRETCH_HOLD_S)
            return "stretch"
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if day != self._day:
            self._day, self._today = day, 0
        if (self._today >= MOMENTS_PER_DAY or now - self.last_turn < MOMENT_AFTER_TURN_S
                or now - self.last_moment < MOMENT_GAP_S or self.rand() >= MOMENT_CHANCE):
            return None
        kind = MOMENT_KINDS[int(self.rand() * len(MOMENT_KINDS)) % len(MOMENT_KINDS)]
        self.last_moment, self._today = now, self._today + 1
        self.events.publish({"type": "moment", "kind": kind})
        return kind
