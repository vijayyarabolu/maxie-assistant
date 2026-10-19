"""Long-term memory: what you tell Maxie about yourself, people, projects and preferences.

SQLite is the store; the Memory page shows it as editable markdown (one fact per bullet,
grouped by topic). Rules:
- Only things he said. A fact the model wants to save must be backed by his own words
  in this conversation (`supported_by`), so an email or web page can't plant memories.
- Never stores passwords, keys, ID numbers, or health / immigration details.
- "forget X" deletes every fact that mentions X.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .db import DB

TOPICS = ("me", "people", "goals", "habits", "decisions", "preferences", "projects", "other")
DEFAULT_SOURCE = "you told me"
PAGE_SOURCE = "Memory page"

_SECRET = re.compile(
    r"(password|passcode|passwd|\bpin\b|api[_ -]?key|secret|token|sk-[a-z0-9]|ssn|social security"
    r"|\b\d{3}-\d{2}-\d{4}\b|\b(?:\d[ -]?){13,19}\b|routing number|account number|passport|driver'?s licen[cs]e)",
    re.I,
)
_HEALTH = re.compile(
    r"\b(diagnos\w*|medication|prescri\w*|therap\w*|depress\w*|anxiety|adhd|disease|illness|surgery"
    r"|doctor|hospital|symptom\w*|blood pressure|insulin|mental health|psychiatr\w*)\b", re.I)
_IMMIGRATION = re.compile(
    r"\b(visa|h-?1b|h-?4|f-?1|opt|stem opt|cpt|green card|i-?20|i-?94|i-?140|i-?485|uscis|immigration"
    r"|work permit|ead|citizenship|perm|status expires?)\b", re.I)

_STOP = set("a an the i im i'm my me is am are was to of and or in on at for with that this it he she they "
            "his her their be do does like likes about from by as".split())


def looks_secret(text: str) -> bool:
    """A password, key, card, SSN or ID number."""
    return bool(_SECRET.search(text))


def forbidden_reason(text: str) -> str | None:
    if _SECRET.search(text):
        return "secrets, passwords and ID numbers are never stored"
    if _HEALTH.search(text):
        return "health details are never stored"
    if _IMMIGRATION.search(text):
        return "immigration details are never stored"
    return None


TOPIC_HINTS = (
    ("decisions", re.compile(r"\b(?:decided|decision|chose|going with|settled on)\b")),
    ("goals", re.compile(r"\b(?:goal|want to|plan to|aim(?:ing)? to|by (?:next|end of))\b")),
    ("habits", re.compile(r"\b(?:every|usually|always|each (?:day|week|morning|night)|on (?:mon|tues|wednes|thurs|fri|satur|sun)days)\b")),
    ("preferences", re.compile(r"\b(?:prefer|like|likes|love|loves|hate|hates|favou?rite)\b")),
    ("projects", re.compile(r"\b(?:project|repo|app|site|portfolio|jobhunt|resumegen)\b")),
)


def guess_topic(fact: str) -> str:
    """Topic for a 'remember …' said out loud, from its words; 'people' when it starts with a name."""
    f = fact.lower()
    for topic, rx in TOPIC_HINTS:
        if rx.search(f):
            return topic
    first = fact.split()[0] if fact.split() else ""
    if first[:1].isupper() and first.lower() not in {"i", "i'm", "my", "the", "we"}:
        return "people"
    return "me" if re.match(r"^(?:i|i'm|my)\b", f) else "other"


def clean(text: str) -> str:
    return " ".join(text.split()).strip(" .")


def when(ts: float | None, tz: str | None = None) -> str:
    """'Oct 15' for a fact's date (with the year when it is not this year)."""
    if not ts:
        return "some time ago"
    zone = ZoneInfo(tz) if tz else None
    d = datetime.fromtimestamp(float(ts), zone)
    return d.strftime("%b %-d") if d.year == datetime.now(zone).year else d.strftime("%b %-d %Y")


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in _STOP and len(w) > 1}


def supported_by(fact: str, said: str, min_overlap: float = 0.6) -> bool:
    """Is the fact mostly made of words he actually said?"""
    fw = _words(fact)
    return bool(fw) and len(fw & _words(said)) / len(fw) >= min_overlap


class Memory:
    def __init__(self, db: DB, clock=time.time):
        self.db, self.clock = db, clock
        db.script("""CREATE TABLE IF NOT EXISTS memory(
            id INTEGER PRIMARY KEY, topic TEXT, text TEXT UNIQUE, created REAL)""")
        cols = {r["name"] for r in db.all("PRAGMA table_info(memory)")}
        if "source" not in cols:  # second brain v1: where each fact came from
            db.script("ALTER TABLE memory ADD COLUMN source TEXT")

    def remember(self, text: str, topic: str = "other", source: str = DEFAULT_SOURCE) -> tuple[bool, str]:
        text = clean(text)
        if not text:
            return False, "nothing to remember"
        if reason := forbidden_reason(text):
            return False, reason
        topic = topic if topic in TOPICS else "other"
        self.db.execute("INSERT OR IGNORE INTO memory(topic,text,created,source) VALUES (?,?,?,?)",
                        (topic, text, self.clock(), source))
        return True, f"Got it: {text}."

    def update(self, fact_id: int, text: str, topic: str | None = None) -> tuple[bool, str]:
        """Edit one fact from the Memory page; the source becomes the page and the date today."""
        text = clean(text)
        if not text:
            return False, "nothing to keep"
        if reason := forbidden_reason(text):
            return False, reason
        row = self.db.one("SELECT topic FROM memory WHERE id=?", (fact_id,))
        if not row:
            return False, "no such fact"
        topic = topic if topic in TOPICS else row["topic"]
        self.db.execute("UPDATE memory SET text=?, topic=?, source=?, created=? WHERE id=?",
                        (text, topic, PAGE_SOURCE, self.clock(), fact_id))
        return True, "Updated."

    def search(self, query: str, limit: int = 8) -> list[dict]:
        """Facts that share words with the question ("what did I decide about the car?"), best first."""
        q = _words(query)
        if not q:
            return []
        scored = [(len(q & _words(r["text"])), r["created"] or 0, r) for r in self.all()]
        scored = sorted((x for x in scored if x[0]), key=lambda x: (x[0], x[1]), reverse=True)
        return [r for _, _, r in scored[:limit]]

    def recall_line(self, query: str, tz: str | None = None) -> str:
        """One spoken answer: the matching facts, each with where and when it came from."""
        hits = self.search(query)
        if not hits:
            return f"I don't have anything about {query.strip(' ?.')}."
        return " ".join(f"{r['text']} ({r['source'] or DEFAULT_SOURCE}, {when(r['created'], tz)})." for r in hits[:3])

    def forget(self, what: str) -> int:
        what = what.strip(" .").lower()
        if not what:
            return 0
        ids = [r["id"] for r in self.all() if what in r["text"].lower()]
        for i in ids:
            self.db.execute("DELETE FROM memory WHERE id=?", (i,))
        return len(ids)

    def delete(self, fact_id: int) -> None:
        self.db.execute("DELETE FROM memory WHERE id=?", (fact_id,))

    def all(self) -> list[dict]:
        return self.db.all("SELECT id, topic, text, source, created FROM memory ORDER BY topic, id")

    def markdown(self) -> str:
        """Stable text (sorted, no timestamps) so it can sit in the prompt cache."""
        rows = self.all()
        parts = []
        for topic in TOPICS:
            facts = [r["text"] for r in rows if r["topic"] == topic]
            if facts:
                parts.append(f"## {topic}\n" + "\n".join(f"- {f}" for f in facts))
        return "\n\n".join(parts)

    def replace_from_markdown(self, md: str) -> list[str]:
        """The Memory page saves the whole document. Returns lines that were refused."""
        refused, facts, topic = [], [], "other"
        for line in md.splitlines():
            line = line.strip()
            if line.startswith("## "):
                topic = line[3:].strip().lower()
            elif line.startswith(("- ", "* ")):
                fact = line[2:].strip()
                if forbidden_reason(fact):
                    refused.append(fact)
                else:
                    facts.append((topic, fact))
        wanted = {clean(f): t for t, f in facts}
        for r in self.all():  # lines he removed go; unchanged lines keep their source and date
            if r["text"] not in wanted:
                self.delete(r["id"])
            elif wanted[r["text"]] != r["topic"] and wanted[r["text"]] in TOPICS:
                self.db.execute("UPDATE memory SET topic=? WHERE id=?", (wanted[r["text"]], r["id"]))
        for text, topic in wanted.items():
            self.remember(text, topic, PAGE_SOURCE)
        return refused
