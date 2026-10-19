"""Test data never touches his memory.

- Tests and evals run on a throwaway database and data folder (`sandbox()`); with MAXIE_SANDBOX=1,
  `config.data_dir()` refuses the live folders outright.
- The live hub refuses test traffic: a request marked `X-Claude-Test`, or a conversation / device named
  like a test (TEST_SOURCES), gets 403. The fake hub (MAXIE_FAKES=1) takes it.
- `clear()` removes what test traffic left behind in a live database (Settings › Backups › Clear test
  data): conversations and tasks from test sources, facts and wins that name fixture people or companies,
  alerts for fixture mail ids, confirmations to example.com. Spend (real money) and the audit log stay.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .db import DB

TEST_SOURCES = ("test", "tests", "eval", "evals", "bench", "mactest", "fake", "default")
# Names and companies that exist only in the fake Google / jobhunt fixtures and the eval sets.
FIXTURE_WORDS = ("AcmeAI", "Nimbus", "Hooli", "Umbrella", "Initech", "Riley", "Mallory", "Landlord Co",
                 "example.com", "sushi", "ML roles", "Corolla")
FIXTURES_GOOGLE = Path(__file__).resolve().parents[1] / "fixtures" / "google.json"


def fixture_phrases() -> tuple[str, ...]:
    """The fixture names plus the distinctive subjects of the fake Google mail ("Rent receipt for October"):
    what a brief built from fake data says. Generic ones ("Your weekly digest", "50% off") are left out, so
    nothing real matches."""
    import json
    try:
        data = json.loads(FIXTURES_GOOGLE.read_text())
    except (OSError, ValueError):
        data = {}
    subjects = [e.get("subject", "") for e in data.get("emails", [])]
    distinct = [x.rstrip("?!. ") for x in subjects if len(x) >= 12 and "%" not in x and not x.startswith("Your ")]
    return tuple(dict.fromkeys([*FIXTURE_WORDS, *distinct]))


def redact(text: str) -> str:
    """Fixture names and subjects out of a saved eval note: reports keep scores, not fake data."""
    for phrase in sorted(fixture_phrases(), key=len, reverse=True):
        text = re.sub(re.escape(phrase), "(fixture)", text, flags=re.I)
    return text


def redact_report(report: dict) -> dict:
    """Every saved word of a report (replies and grader notes) goes through redact()."""
    def clean(r: dict) -> dict:
        return {k: redact(v) if isinstance(v, str) else v for k, v in r.items()}
    return {**report, "results": [clean(r) for r in report.get("results", [])]}


FIXTURE_ALERT = re.compile(r"^(?:mail|prep|meeting):[a-z]\d+$")  # fake ids (j1, e2); real ones are long hex
TEST_HEADER = "x-claude-test"


def is_test_source(name: str | None) -> bool:
    return (name or "").strip().lower() in TEST_SOURCES


def live_dirs() -> tuple[Path, ...]:
    return ((Path(__file__).resolve().parents[1] / "data").resolve(), (Path.home() / ".maxie").resolve())


def is_live(path: Path) -> bool:
    p = path.resolve()
    return any(p == d or d in p.parents for d in live_dirs())


def sandbox() -> Path:
    """Point this process at a fresh temp data folder (with a copy of his settings, read-only use) and
    forbid the live folders from here on. Evals call this first."""
    live_settings = next((d / "settings.json" for d in live_dirs() if (d / "settings.json").exists()), None)
    tmp = Path(tempfile.mkdtemp(prefix="maxie-sandbox-"))
    if live_settings is not None:
        shutil.copy(live_settings, tmp / "settings.json")
    os.environ["MAXIE_DATA"] = str(tmp)
    os.environ["MAXIE_SANDBOX"] = "1"
    return tmp


@dataclass(frozen=True)
class Window:
    """A time range whose traffic was all tests (used once, for the 2026-10-15 sweep)."""
    start: float
    end: float
    convs: tuple[str, ...] = ()


def _in(values: list) -> str:
    return ",".join("?" * len(values)) or "NULL"


def clear(db: DB, window: Window | None = None) -> dict[str, int]:
    """Remove test leftovers from a live database. Returns rows removed per table."""
    src = list(TEST_SOURCES)
    like = " OR ".join("text LIKE ?" for _ in FIXTURE_WORDS)
    words = [f"%{w}%" for w in FIXTURE_WORDS]
    removed: dict[str, int] = {}

    tables = {r["name"] for r in db.all("SELECT name FROM sqlite_master WHERE type='table'")}

    def drop(table: str, where: str, args: list) -> None:
        if table not in tables:  # a module that never ran here made no table
            return
        n = db.execute(f"DELETE FROM {table} WHERE {where}", args).rowcount
        removed[table] = removed.get(table, 0) + n

    test_texts = [r["text"] for r in db.all(f"SELECT text FROM messages WHERE role='user' AND conv IN ({_in(src)})", src)]
    if window:
        wc = list(window.convs)
        span = [window.start, window.end]
        test_texts += [r["text"] for r in db.all(
            f"SELECT text FROM messages WHERE role='user' AND ts BETWEEN ? AND ? AND conv IN ({_in(wc)})", [*span, *wc])]
        drop("messages", f"ts BETWEEN ? AND ? AND conv IN ({_in(wc)})", [*span, *wc])
        for table in ("tasks", "turns", "route_log"):
            drop(table, "ts BETWEEN ? AND ?", span)
    drop("messages", f"conv IN ({_in(src)})", src)
    drop("tasks", f"device IN ({_in(src)})", src)
    if test_texts:
        texts = list(dict.fromkeys(test_texts))
        drop("route_log", f"text IN ({_in(texts)})", texts)
        drop("turns", f"text IN ({_in(texts)})", texts)
    drop("memory", like, words)
    drop("wins", like, words)
    # What fake mail turned into: briefs held for later, Activity tasks, Claude's replies.
    phrases = [f"%{w}%" for w in fixture_phrases()]

    def drop_like(table: str, cols: tuple[str, ...], extra: str = "") -> None:
        if table not in tables:
            return
        have = {r["name"] for r in db.all(f"PRAGMA table_info({table})")}
        cols = tuple(c for c in cols if c in have)
        if cols:
            like_any = " OR ".join(f"{c} LIKE ?" for c in cols for _ in phrases)
            drop(table, f"{extra}({like_any})", phrases * len(cols))

    drop_like("held", ("title", "body"))
    drop_like("tasks", ("text", "reply"))
    drop_like("messages", ("text",), "role='assistant' AND ")
    drop("confirmations", "summary LIKE ? OR args LIKE ?", ["%example.com%", "%example.com%"])
    fake_alerts = [r["key"] for r in db.all("SELECT key FROM alerts_sent") if FIXTURE_ALERT.match(r["key"])] \
        if "alerts_sent" in tables else []
    if fake_alerts:
        drop("alerts_sent", f"key IN ({_in(fake_alerts)})", fake_alerts)
    return {k: v for k, v in removed.items() if v}
