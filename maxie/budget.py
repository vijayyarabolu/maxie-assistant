"""Monthly API budget, counted from each response's `usage`.

At 80% the brief warns. At 100% Sonnet/Opus stop; Haiku keeps working for direct
actions and routing up to a small overdraft (5%), then everything stops and the
hub answers in plain text that the budget is reached.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .db import DB

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"

# USD per million tokens per model id: (input, output, cache read). Cache writes cost 1.25x input for the
# 5-minute TTL and 2x for the 1-hour TTL the brain uses (docs.anthropic.com, prompt caching pricing). Add a line here when a new model's real price is known; until then an unknown id is
# counted at the dearest known price of its tier x GUESS_MULT, so the cap never under-counts.
PRICES = {
    HAIKU: (0.10, 0.50, 0.01),
    SONNET: (2.00, 10.00, 0.20),
    OPUS: (4.00, 20.00, 0.20),
}
GUESS_MULT = 1.5
CACHE_WRITE_MULT = 1.25  # 5-minute TTL
CACHE_WRITE_1H_MULT = 2.0  # 1-hour TTL; a write without the breakdown is counted at this, so spend never under-counts
WEB_SEARCH_USD = 0.01  # per server-side web search request
HAIKU_OVERDRAFT = 1.05


def _get(usage, key: str) -> int:
    value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
    return int(value or 0)


def tier_of(model: str) -> str | None:
    low = (model or "").lower()
    return next((t for t in ("haiku", "sonnet", "opus") if t in low), None)


def price_guessed(model: str) -> bool:
    return model not in PRICES


def price_for(model: str) -> tuple[float, float, float]:
    """Known price, or the dearest known price in the same tier x GUESS_MULT (Opus if no tier)."""
    if model in PRICES:
        return PRICES[model]
    tier = tier_of(model)
    same = [v for k, v in PRICES.items() if tier_of(k) == tier] or [PRICES[OPUS]]
    inp, out, read = max(same, key=lambda v: v[1])
    return (inp * GUESS_MULT, out * GUESS_MULT, read * GUESS_MULT)


def _write_tokens(usage) -> float:
    """Cache-write tokens weighted by their TTL's price multiplier."""
    total = _get(usage, "cache_creation_input_tokens")
    split = usage.get("cache_creation") if isinstance(usage, dict) else getattr(usage, "cache_creation", None)
    if split is None:
        return total * CACHE_WRITE_1H_MULT
    five, hour = _get(split, "ephemeral_5m_input_tokens"), _get(split, "ephemeral_1h_input_tokens")
    rest = max(0, total - five - hour)
    return five * CACHE_WRITE_MULT + (hour + rest) * CACHE_WRITE_1H_MULT


def cost_usd(model: str, usage) -> float:
    inp, out, read = price_for(model)
    searches = 0
    stu = usage.get("server_tool_use") if isinstance(usage, dict) else getattr(usage, "server_tool_use", None)
    if stu is not None:
        searches = _get(stu, "web_search_requests")
    return (
        _get(usage, "input_tokens") * inp
        + _write_tokens(usage) * inp
        + _get(usage, "cache_read_input_tokens") * read
        + _get(usage, "output_tokens") * out
    ) / 1_000_000 + searches * WEB_SEARCH_USD


@dataclass
class BudgetStatus:
    today_usd: float
    month_usd: float
    cap_usd: float
    state: str  # "ok" | "warn" | "capped"

    @property
    def ratio(self) -> float:
        return self.month_usd / self.cap_usd if self.cap_usd else 1.0

    def line(self) -> str:
        msg = f"API spend: ${self.today_usd:.2f} today, ${self.month_usd:.2f} of ${self.cap_usd:.0f} this month"
        if self.state == "warn":
            msg += f" ({self.ratio:.0%}, heads up)"
        elif self.state == "capped":
            msg += " (budget reached: Sonnet/Opus paused until next month)"
        return msg


class Budget:
    def __init__(self, db: DB, cap_usd: float = 95.0, warn_ratio: float = 0.8,
                 tz: str = "America/Chicago", clock=time.time):
        self.db, self.cap_usd, self.warn_ratio, self.tz, self.clock = db, cap_usd, warn_ratio, ZoneInfo(tz), clock
        db.script("""CREATE TABLE IF NOT EXISTS usage(
            ts REAL, model TEXT, purpose TEXT, input INT, output INT, cache_write INT, cache_read INT, cost REAL)""")

    def record(self, model: str, usage, purpose: str = "chat") -> float:
        cost = cost_usd(model, usage)
        self.db.execute(
            "INSERT INTO usage VALUES (?,?,?,?,?,?,?,?)",
            (self.clock(), model, purpose, _get(usage, "input_tokens"), _get(usage, "output_tokens"),
             _get(usage, "cache_creation_input_tokens"), _get(usage, "cache_read_input_tokens"), cost),
        )
        return cost

    def _since(self, start: datetime) -> float:
        row = self.db.one("SELECT COALESCE(SUM(cost),0) AS s FROM usage WHERE ts >= ?", (start.timestamp(),))
        return float(row["s"])

    def status(self) -> BudgetStatus:
        now = datetime.fromtimestamp(self.clock(), self.tz)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month = self._since(day.replace(day=1))
        ratio = month / self.cap_usd if self.cap_usd else 1.0
        state = "capped" if ratio >= 1 else "warn" if ratio >= self.warn_ratio else "ok"
        return BudgetStatus(self._since(day), month, self.cap_usd, state)

    def allows(self, model: str) -> bool:
        """May we call this model right now?"""
        s = self.status()
        if s.state != "capped":
            return True
        return tier_of(model) == "haiku" and s.month_usd < self.cap_usd * HAIKU_OVERDRAFT
