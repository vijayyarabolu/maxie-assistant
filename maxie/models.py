"""Which Claude model each tier (haiku / sonnet / opus) uses, with automatic upgrades.

Today's ids are pinned in `budget.py` as the fallback. Once a day and on hub start, the Models API
(`GET /v1/models`) is listed; per tier the newest stable id by `created_at` is picked (preview, beta,
mythos and fable ids are skipped). A new id gets a smoke check (one tiny call, plus the Haiku router
on a few eval commands for the haiku tier, under a cent). Pass: that tier switches. Fail: it stays.
Active ids live in the DB, not in code; `brain.py`, `router/`, `hands/claude_code.py` read them here.
A 404 (model retired) on a real call falls back to the previous id and asks for a recheck.
Settings: `auto_upgrade_models` (default on) and `model_pins` (tier -> id, "pin current").
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .budget import HAIKU, OPUS, SONNET
from .db import DB

TIERS = ("haiku", "sonnet", "opus")
PINNED = {"haiku": HAIKU, "sonnet": SONNET, "opus": OPUS}
SKIP = ("preview", "beta", "mythos", "fable", "latest")
CHECK_EVERY = 24 * 3600
SHOW_UPGRADES_FOR = 7 * 86400  # how long "Upgraded X -> Y" stays in the panel and brief
SMOKE_PROMPT = "Reply with the single word OK."


def tier_of(model_id: str) -> str | None:
    low = (model_id or "").lower()
    return next((t for t in TIERS if t in low), None)


def is_stable(model_id: str) -> bool:
    low = model_id.lower()
    return low.startswith("claude-") and tier_of(low) is not None and not any(s in low for s in SKIP)


def pretty(model_id: str) -> str:
    """claude-sonnet-5-5 -> Sonnet 5.5; claude-sonnet-6-20270101 -> Sonnet 6 (20270101)."""
    tier = tier_of(model_id)
    if not tier:
        return model_id
    rest = [p for p in model_id.removeprefix("claude-").split("-") if p != tier]
    version = [p for p in rest if p.isdigit() and len(p) < 8]
    extra = [p for p in rest if p not in version]
    name = f"{tier.capitalize()} {'.'.join(version)}".strip()
    return name + (f" ({' '.join(extra)})" if extra else "")


@dataclass(frozen=True)
class ModelInfo:
    id: str
    created_at: float


@dataclass(frozen=True)
class Upgrade:
    tier: str
    old: str
    new: str
    at: float
    ok: bool
    note: str

    def line(self) -> str:
        if self.ok:
            return f"Upgraded {pretty(self.old)} → {pretty(self.new)}"
        return f"Kept {pretty(self.old)}: {pretty(self.new)} failed its check ({self.note})"


class Models:
    """Active model id per tier. Pins (Settings) win; then the DB; then the hardcoded fallback."""

    def __init__(self, db: DB, clock=time.time, pins: dict[str, str] | None = None, auto_upgrade: bool = True):
        self.db, self.clock = db, clock
        self.pins = dict(pins or {})
        self.auto_upgrade = auto_upgrade
        db.script("""CREATE TABLE IF NOT EXISTS models(
            tier TEXT PRIMARY KEY, active TEXT, previous TEXT, created_at REAL, since REAL);
            CREATE TABLE IF NOT EXISTS model_upgrades(
            id INTEGER PRIMARY KEY, ts REAL, tier TEXT, old TEXT, new TEXT, ok INT, note TEXT);
            CREATE TABLE IF NOT EXISTS model_meta(key TEXT PRIMARY KEY, value REAL)""")

    # ---- reading

    def active(self, tier: str) -> str:
        if tier not in TIERS:
            raise ValueError(f"unknown tier: {tier}")
        if pin := self.pins.get(tier):
            return pin
        row = self.db.one("SELECT active FROM models WHERE tier=?", (tier,))
        return row["active"] if row else PINNED[tier]

    def all_active(self) -> dict[str, str]:
        return {t: self.active(t) for t in TIERS}

    def _row(self, tier: str) -> dict | None:
        return self.db.one("SELECT * FROM models WHERE tier=?", (tier,))

    def recent_upgrades(self) -> list[Upgrade]:
        rows = self.db.all("SELECT * FROM model_upgrades WHERE ts >= ? ORDER BY ts",
                           (self.clock() - SHOW_UPGRADES_FOR,))
        return [Upgrade(r["tier"], r["old"], r["new"], r["ts"], bool(r["ok"]), r["note"]) for r in rows]

    def lines(self) -> list[str]:
        """What the panel and the morning brief show."""
        return [u.line() for u in self.recent_upgrades() if u.tier in TIERS]

    def last_checked(self) -> float:
        row = self.db.one("SELECT value FROM model_meta WHERE key='last_check'")
        return float(row["value"]) if row else 0.0

    def due(self) -> bool:
        return self.auto_upgrade and self.clock() - self.last_checked() >= CHECK_EVERY

    # ---- writing

    def _set(self, tier: str, new: str, created_at: float | None, previous: str | None) -> None:
        self.db.execute("INSERT OR REPLACE INTO models(tier,active,previous,created_at,since) VALUES (?,?,?,?,?)",
                        (tier, new, previous, created_at, self.clock()))

    def _log(self, tier: str, old: str, new: str, ok: bool, note: str) -> Upgrade:
        self.db.execute("INSERT INTO model_upgrades(ts,tier,old,new,ok,note) VALUES (?,?,?,?,?,?)",
                        (self.clock(), tier, old, new, int(ok), note))
        return Upgrade(tier, old, new, self.clock(), ok, note)

    def recheck_soon(self) -> None:
        self.db.execute("INSERT OR REPLACE INTO model_meta VALUES ('last_check', 0)")

    def pin_current(self) -> dict[str, str]:
        """Freeze today's ids: returns the pins to save in Settings (auto-upgrade off)."""
        self.pins = self.all_active()
        self.auto_upgrade = False
        return dict(self.pins)

    def check(self, list_models: Callable[[], list[ModelInfo]],
              smoke: Callable[[str, str], str | None],
              before_switch: Callable[[str, str], object] | None = None) -> list[Upgrade]:
        """List the API's models; per tier switch to the newest stable one that passes `smoke`.

        `smoke(tier, model_id)` returns None when the model is fine, else a short reason.
        Returns what changed (or failed); never raises on a bad list (the old ids keep working).
        """
        self.db.execute("INSERT OR REPLACE INTO model_meta VALUES ('last_check', ?)", (self.clock(),))
        try:
            listed = [m for m in list_models() if is_stable(m.id)]
        except Exception as e:  # the list is best effort; keep what we have  # noqa: BLE001
            self._log("all", "", "", False, f"models list failed: {e}")
            return []
        out = []
        for tier in TIERS:
            candidates = sorted((m for m in listed if tier_of(m.id) == tier),
                                key=lambda m: (-m.created_at, len(m.id)))
            if not candidates:
                continue
            best, current, row = candidates[0], self.active(tier), self._row(tier)
            if best.id == current or self.pins.get(tier):
                continue
            if row and row["created_at"] and best.created_at <= row["created_at"]:
                continue  # never move to something older than what we switched to on purpose
            if any(u.new == best.id and not u.ok for u in self.recent_upgrades()):
                continue  # failed its check this week; try again next week
            reason = smoke(tier, best.id)
            if reason:
                out.append(self._log(tier, current, best.id, False, reason))
                continue
            if before_switch:
                before_switch(tier, best.id)  # a backup first
            self._set(tier, best.id, best.created_at, current)
            out.append(self._log(tier, current, best.id, True, "passed smoke check"))
        return out

    def fallback(self, failed_id: str) -> str | None:
        """A call to `failed_id` got a 404: go back to the previous id (or the pinned one). Returns it."""
        tier = tier_of(failed_id)
        if not tier or self.active(tier) != failed_id:
            return None
        row = self._row(tier)
        previous = (row or {}).get("previous") or PINNED[tier]
        if previous == failed_id:
            previous = PINNED[tier]
        if previous == failed_id:
            return None
        self._set(tier, previous, None, None)
        self._log(tier, failed_id, previous, True, "model retired (404), fell back")
        self.recheck_soon()
        return previous


# ---- the real Anthropic client, wrapped so the rest of the code never imports the SDK


def list_from_api(client) -> list[ModelInfo]:
    """GET /v1/models through the sync SDK client."""
    page = client.models.list(limit=1000)
    out = []
    for m in page.data:
        created = getattr(m, "created_at", None)
        ts = created.timestamp() if hasattr(created, "timestamp") else float(created or 0)
        out.append(ModelInfo(m.id, ts))
    return out


def smoke_with_api(client, budget, eval_commands: list[dict] | None = None, min_route_accuracy: float = 0.6):
    """Builds the smoke check: one tiny call, plus the Haiku router on a few eval commands for haiku."""
    from .router.haiku import HaikuRouter

    def smoke(tier: str, model_id: str) -> str | None:
        try:
            msg = client.messages.create(model=model_id, max_tokens=5,
                                         messages=[{"role": "user", "content": SMOKE_PROMPT}])
        except Exception as e:  # any failure means "don't switch"  # noqa: BLE001
            return f"tiny call failed: {type(e).__name__}"
        budget.record(model_id, msg.usage, "smoke")
        if not any(getattr(b, "text", "") for b in msg.content):
            return "tiny call returned no text"
        if tier != "haiku" or not eval_commands:
            return None
        router = HaikuRouter(client, budget, model=model_id)
        right = 0
        for cmd in eval_commands:
            r = router.classify(cmd["text"])
            right += bool(r and r.get("route") == cmd["route"])
        acc = right / len(eval_commands)
        return None if acc >= min_route_accuracy else f"router eval {acc:.0%} < {min_route_accuracy:.0%}"

    return smoke
