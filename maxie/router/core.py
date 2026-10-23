"""The Router ties the four stages together and logs every decision for calibration."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..budget import SONNET
from ..db import DB
from ..models import Models
from . import lexicon, rules
from .calibrate import NEVER, Calibration
from .haiku import HaikuRouter
from .laya import LayaClient

ROUTES = ("mac_action", "quick_question", "calendar", "email", "jobhunt", "multi_step", "chitchat",
          "memory", "control")
HARMLESS = {"open_app", "open_url", "music", "volume", "timer"}  # direct even before calibration
CALIBRATED_EXTRA = {"do_not_disturb"}  # direct only once thresholds come from his data
THINK_HARD = re.compile(r"\bthink (?:hard|harder|deeply|carefully)\b|\bultrathink\b", re.I)
# The keyword classifier is right 96%+ of the time at this confidence on the eval set (measured 2026-10-15),
# and costs 0 ms; below it, Laya / Haiku decide. This keeps a network round trip off the first word.
LEXICON_TRUST = 0.6


@dataclass
class Decision:
    route: str
    source: str  # rules | laya | haiku | lexicon
    confidence: float = 1.0
    action: str | None = None  # set => run directly, no Claude call
    args: dict = field(default_factory=dict)
    risk: float = 0.0
    model: str = SONNET  # who answers when it goes to the brain
    latency_ms: float = 0.0

    @property
    def direct(self) -> bool:
        return self.action is not None


def pick_tier(route: str, text: str) -> str:
    """Haiku for quick turns and plain Mac actions (one tool call), Sonnet for real tasks, Opus on "think hard"."""
    if THINK_HARD.search(text):
        return "opus"
    return "haiku" if route in ("quick_question", "chitchat", "mac_action") else "sonnet"


class Router:
    def __init__(self, db: DB, laya: LayaClient | None = None, haiku: HaikuRouter | None = None,
                 calibration_path: Path | None = None, models: Models | None = None):
        self.db, self.laya, self.haiku = db, laya, haiku
        self.models = models or Models(db)
        self.calibration_path = calibration_path
        self.calibration = Calibration.load(calibration_path) if calibration_path else Calibration()
        db.script("""CREATE TABLE IF NOT EXISTS route_log(
            id INTEGER PRIMARY KEY, ts REAL, text TEXT, route TEXT, action TEXT, source TEXT,
            confidence REAL, label TEXT)""")
        cols = {r["name"] for r in db.all("PRAGMA table_info(route_log)")}
        if "laya_route" not in cols:  # Laya's own guess, kept beside the decision for calibration
            db.script("ALTER TABLE route_log ADD COLUMN laya_route TEXT; ALTER TABLE route_log ADD COLUMN laya_p REAL")
        self._laya_guess: tuple[str, float] | None = None

    def allowed_direct(self) -> set[str]:
        return HARMLESS | CALIBRATED_EXTRA if self.calibration.calibrated else HARMLESS

    def needs_model(self, text: str) -> bool:
        """Routing this will wait on Haiku (~1.5 s): no rule matches and the lexicon is unsure."""
        return self.haiku is not None and rules.match(text) is None and lexicon.classify(text)[1] < LEXICON_TRUST

    def early_model(self, text: str) -> str:
        """Who answers if the router says it is a quick turn: the model an early answer starts on."""
        return self.models.active(pick_tier("quick_question", text))

    def route(self, text: str) -> Decision:
        t0 = time.perf_counter()
        d = self._route(text)
        d.latency_ms = (time.perf_counter() - t0) * 1000
        if not d.direct and d.route not in ("memory", "control"):
            d.model = self.models.active(pick_tier(d.route, text))
        guess = self._laya_guess or (None, None)
        self.db.execute("INSERT INTO route_log(ts,text,route,action,source,confidence,laya_route,laya_p) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (time.time(), text, d.route, d.action, d.source, d.confidence, guess[0], guess[1]))
        return d

    def _direct(self, d: Decision, text: str, confident: bool) -> Decision:
        """Attach a direct action when it's a confident, harmless Mac action we can parse."""
        if d.route == "mac_action" and confident and d.risk < 0.34:
            hit = rules.extract(text)
            if hit and hit.action in self.allowed_direct():
                d.action, d.args = hit.action, hit.args
        return d

    def _route(self, text: str) -> Decision:
        self._laya_guess = None
        hit = rules.match(text)
        if hit:  # exact patterns that only ever produce harmless actions: run directly
            return Decision(hit.route, "rules", 1.0, hit.action, hit.args)

        route, conf, _ = lexicon.classify(text)
        if conf >= LEXICON_TRUST:
            return self._direct(Decision(route, "lexicon", conf), text, conf >= 0.7)

        # Laya answers in ~180 ms for free, but its checkpoints are over-confident (42-60% raw on
        # our labels, measured 2026-10-14). Its route counts only above the per-route threshold
        # that calibration found on his labelled commands (precision >= 95%); with no calibration
        # yet, that threshold is never reached and Haiku routes instead. Every guess is logged.
        if self.laya and (r := self.laya.classify(text)):
            self._laya_guess = (r.route, r.route_p)
            if r.route_p >= self.calibration.threshold(r.route, default=NEVER):
                d = Decision(r.route, "laya", r.route_p, risk=r.risk)
                return self._direct(d, text, r.action != "none")

        if self.haiku and (h := self.haiku.classify(text)):
            route = h["route"] if h["route"] in ROUTES else "multi_step"
            d = Decision(route, "haiku", float(h.get("confidence", 0.5)))
            if route == "mac_action" and h["action"] in self.allowed_direct():
                d.action, d.args = h["action"], h["args"]
                if d.action == "open_app" and "url" in d.args:
                    d.action = "open_url"
            return d

        return self._direct(Decision(route, "lexicon", conf), text, conf >= 0.7)

    # --- labels for calibration -------------------------------------------------------------------

    def label(self, log_id: int, route: str) -> None:
        if route not in ROUTES:
            raise ValueError(f"unknown route {route}")
        self.db.execute("UPDATE route_log SET label=? WHERE id=?", (route, log_id))

    def recent(self, limit: int = 50) -> list[dict]:
        return self.db.all("SELECT * FROM route_log ORDER BY id DESC LIMIT ?", (limit,))

    def labelled(self) -> list[tuple[str, str]]:
        return [(r["text"], r["label"]) for r in self.db.all("SELECT text,label FROM route_log WHERE label IS NOT NULL")]
