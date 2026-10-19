"""Rules: per action, Allow / Ask me / Block. Defaults are safe: anything that leaves the Mac
or changes something outside it (send, invite, pay, submit, delete, run) asks him; plain reads and local
actions are allowed. He changes a rule in Settings › Rules; every change is in the audit log."""

from __future__ import annotations

from .safety import RISKY_KINDS

ALLOW, ASK, BLOCK = "allow", "ask", "block"
CHOICES = (ALLOW, ASK, BLOCK)
GROUPS = {  # how the Rules page groups tools, by kind
    "Leaves the Mac": ("send", "invite", "pay", "submit"),
    "Changes things": ("delete", "run", "action"),
    "Reads": ("read",),
}


def default_for(kind: str) -> str:
    return ASK if kind in RISKY_KINDS else ALLOW


class Rules:
    """Backed by Settings.action_rules ({tool name: rule}); only overrides are stored."""

    def __init__(self, settings, audit=None):
        self.settings, self.audit = settings, audit

    def for_tool(self, name: str, kind: str) -> str:
        rule = self.settings.action_rules.get(name)
        return rule if rule in CHOICES else default_for(kind)

    def set(self, name: str, rule: str, kind: str) -> str:
        if rule not in CHOICES:
            raise ValueError(f"rule must be one of {', '.join(CHOICES)}")
        before = self.for_tool(name, kind)
        overrides = {k: v for k, v in self.settings.action_rules.items() if k != name}
        if rule != default_for(kind):
            overrides[name] = rule
        self.settings.action_rules = overrides
        self.settings.save()
        if self.audit and before != rule:
            self.audit.log("user", "rule_changed", {"tool": name}, "Settings › Rules", f"{before} -> {rule}")
        return rule

    def table(self, registry) -> list[dict]:
        """Every tool with its group, kind, current rule and default, for the Rules page."""
        rows = []
        for name in registry.names():
            t = registry.get(name)
            group = next((g for g, kinds in GROUPS.items() if t.kind in kinds), "Reads")
            rows.append({"name": name, "kind": t.kind, "group": group, "description": t.description,
                         "rule": self.for_tool(name, t.kind), "default": default_for(t.kind)})
        order = list(GROUPS)
        rows.sort(key=lambda r: (order.index(r["group"]), r["name"]))
        return rows
