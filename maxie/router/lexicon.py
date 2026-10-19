"""Offline keyword classifier: the last fallback when neither Laya nor Haiku is reachable.

Deliberately simple and transparent. It is also what CI evaluates (no model downloads
in CI), so its accuracy is the floor; Laya's real accuracy is measured on the Mac.
"""

from __future__ import annotations

import math
import re

from .rules import normalize

KEYWORDS: dict[str, dict[str, float]] = {
    "calendar": {
        "calendar": 3, "meeting": 3, "meetings": 3, "schedule": 1.5, "appointment": 3, "event": 2, "events": 2,
        "agenda": 3, "free": 1.5, "busy": 2, "standup": 3, "1:1": 3, "invite": 2, "reschedule": 3,
        "booked": 2, "slot": 2, "call with": 2.5, "lunch with": 2.5, "dinner with": 2.5, "on my plate": 2,
        "next week": 1, "tomorrow": 0.7, "my week": 3, "my day": 2.5, "look like": 1, "free hours": 2, "first meeting": 3, "o'clock": 2, "this afternoon": 1, "block": 1.5, "what's on": 2, "whats on": 2,
    },
    "email": {
        "email": 3, "emails": 3, "mail": 2.5, "inbox": 3, "gmail": 2, "reply": 2.5, "replied": 2, "unread": 3,
        "draft": 2, "sent": 1.5, "message from": 2, "wrote": 1.5, "forward": 2, "newsletter": 2, "write back": 2.5,
        "respond to": 2, "answer": 0.5, "thread": 1.5, "subject": 1.5,
    },
    "jobhunt": {
        "job": 3, "jobs": 3, "application": 3, "applications": 3, "applied": 3, "apply": 2.5, "resume": 3,
        "resumes": 3, "recruiter": 3, "recruiters": 3, "sponsor": 3, "sponsorship": 3, "hiring": 2.5, "linkedin": 1.5,
        "follow-up": 2, "follow up": 2, "follow-ups": 2.5, "followups": 2.5, "offer": 1.5, "role": 1.5, "roles": 2,
        "jobhunt": 4, "resumegen": 4, "tracker": 2.5, "cover letter": 3, "jd": 3, "rejection": 2.5, "rejected": 2,
        "interview": 2.5, "interviews": 2.5, "company": 0.5, "position": 1.5, "openings": 2.5, "salary": 1.5,
    },
    "mac_action": {
        "open": 2.5, "launch": 3, "play": 2.5, "pause": 3, "volume": 3, "timer": 3.5, "mute": 3, "louder": 3,
        "quieter": 3, "song": 2, "music": 2, "playlist": 3, "spotify": 2, "do not disturb": 3, "dnd": 3, "focus": 1,
        "remind me": 2.5, "reminder": 2.5, "note": 1, "notes": 1, "find the file": 3, "find my": 1.5, "folder": 1.5,
        "file": 1.5, "battery": 3, "wifi": 3, "disk": 2.5, "screenshot": 3, "turn on": 1.5, "turn off": 1.5,
        "skip": 2, "put on": 2, "text": 1, "imessage": 2.5, "message to": 1.5, "fire up": 3, "app": 1.5,
    },
    "multi_step": {
        " and then ": 4, " then ": 2, "after that": 3, "compare": 2.5, "plan": 2, "research": 3, "write": 1.5,
        "build": 2.5, "fix": 2, "refactor": 3.5, "code": 1.5, "script": 2, "debug": 3, "prepare": 2, "prep": 1.5,
        "summarize": 1.5, "step by step": 3, "think hard": 3, "analyze": 2.5, "organize": 2, "figure out": 2,
        "go through": 2, "every": 1, "all of": 1, "and draft": 2.5, "and send": 2.5, "and add": 2, "pull request": 3,
        "claude code": 3, "repo": 2.5, "bug": 2.5, "tests": 2.5, "failing": 2.5, "and make": 1.5, "and put": 1.5,
    },
    "quick_question": {
        "what": 1, "who": 1.5, "when": 1, "where": 1.5, "why": 1.5, "how": 1, "define": 3, "meaning of": 3,
        "who wrote": 2, "who invented": 2.5, "who painted": 2.5, "who discovered": 2.5, "who founded": 2,
        "explain": 2, "tell me": 1, "how many": 2, "how much": 1.5, "capital of": 3, "weather": 3, "convert": 2.5,
        "difference between": 2.5, "is it": 1, "does": 1, "spell": 3, "translate": 3, "fact": 1, "temperature": 2.5,
        "rain": 2, "time in": 3, "synonym": 3, "?": 0.5,
    },
    "chitchat": {
        "hi": 3, "hello": 3, "hey": 1.5, "thanks": 3, "thank you": 3, "good morning": 3, "good night": 3,
        "how are you": 4, "i'm tired": 3, "im tired": 3, "i feel": 3, "feeling": 2, "lol": 3, "joke": 3, "bored": 3,
        "love you": 3, "you're": 1.5, "awesome": 2, "nice": 1.5, "cool": 1.5, "sad": 2.5, "rough day": 3,
        "long day": 3, "stressed": 3, "frustrated": 3, "excited": 2.5, "yay": 3, "ugh": 3, "miss": 1.5, "chat": 2,
        "who are you": 3, "your name": 3, "motivat": 2, "tired": 2, "exhausted": 3, "happy": 2,
        "tired of": 3, "sick of": 3, "fed up": 3, "i'm so": 2, "im so": 2, "i am so": 2,
    },
}
PRIOR = {"quick_question": 0.6}  # nothing matched -> most likely a plain question

# Several different things to do in one command (separate clauses) means a multi-step task.
VERBS = ("check", "draft", "find", "queue", "add", "research", "summarize", "prep", "write", "send", "make",
         "compare", "plan", "go through", "fix", "refactor", "open", "read", "search", "book", "email", "list",
         "look at", "block", "play", "launch", "reply", "schedule", "move", "put", "text", "remind")
# A statement about himself ("I finally...", "I don't feel like...") is talk, not a request.
SELF_TALK = re.compile(r"^(?:ugh |man |honestly |so )?i(?:'m| am| feel| don'?t feel| just| finally| got| can'?t| really)\b")


def _score(text: str, phrase: str) -> int:
    if phrase.startswith(" ") or not phrase[0].isalnum() or not phrase[-1].isalnum():
        return text.count(phrase)
    return len(re.findall(rf"(?<![\w']){re.escape(phrase)}", text))


def classify(text: str) -> tuple[str, float, dict[str, float]]:
    """(route, confidence, probabilities)."""
    t = f" {normalize(text)} "
    scores = {r: PRIOR.get(r, 0.0) + sum(w * min(_score(t, p), 2) for p, w in kw.items())
              for r, kw in KEYWORDS.items()}
    verbs = sum(1 for v in VERBS if re.search(rf"(?:^ |, | and | then ){v}\b", t))
    if verbs >= 2:  # two or more separate things to do: multi-step by definition
        scores["multi_step"] = max(scores.values()) + 3
    elif verbs == 1 and "," in t and " and " in t:
        scores["multi_step"] += 6
    if SELF_TALK.match(t.strip()) and "?" not in t:
        scores["chitchat"] += 4
    z = sum(math.exp(s) for s in scores.values())
    probs = {r: math.exp(s) / z for r, s in scores.items()}
    best = max(probs, key=probs.get)
    return best, probs[best], probs
