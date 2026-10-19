"""Haiku routing, used when Laya is down. One small structured-output call (~$0.0001)."""

from __future__ import annotations

import json

from ..budget import HAIKU, Budget
from .laya import ACTION_CRITERIA, ROUTE_CRITERIA

SYSTEM = (
    "You route voice commands for a personal assistant on a Mac. Pick the route and, only for a direct "
    "Mac action, the action and its arguments. Routes:\n"
    + "\n".join(f"- {k}: {v}" for k, v in ROUTE_CRITERIA.items())
    + "\nActions (only for mac_action):\n"
    + "\n".join(f"- {k}: {v}" for k, v in ACTION_CRITERIA.items())
    + "\nArgs: open_app {name} or {url}; music {command: play|pause|next|previous, query}; "
    "volume {level: 0-100|up|down|mute}; timer {minutes, label}."
)
FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "route": {"type": "string", "enum": list(ROUTE_CRITERIA)},
            "action": {"type": "string", "enum": list(ACTION_CRITERIA)},
            "args_json": {"type": "string", "description": "JSON object of action args, or {}"},
            "confidence": {"type": "number"},
        },
        "required": ["route", "action", "args_json", "confidence"],
        "additionalProperties": False,
    },
}


class HaikuRouter:
    def __init__(self, client, budget: Budget, models=None, model: str | None = None):
        self.client, self.budget, self.models, self._model = client, budget, models, model

    @property
    def model(self) -> str:
        return self._model or (self.models.active("haiku") if self.models else HAIKU)

    def classify(self, text: str) -> dict | None:
        """{route, action, args, confidence} or None if not allowed / failed."""
        model = self.model
        if self.client is None or not self.budget.allows(model):
            return None
        try:
            msg = self.client.messages.create(
                model=model, max_tokens=300, system=SYSTEM,
                messages=[{"role": "user", "content": text}],
                output_config={"effort": "low", "format": FORMAT},
            )
        except Exception:  # any API failure means "fall back to the lexicon"  # noqa: BLE001
            return None
        self.budget.record(model, msg.usage, "route")
        try:
            out = json.loads(next(b.text for b in msg.content if b.type == "text"))
            out["args"] = json.loads(out.pop("args_json") or "{}")
        except (StopIteration, ValueError, KeyError):
            return None
        return out
