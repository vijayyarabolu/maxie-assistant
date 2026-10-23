"""Tool registry with confirm-before-risky built in.

A tool declares its `kind`:
  read    - looks something up (calendar, mail search, files). Runs freely.
  action  - harmless local action (open app, volume, timer, draft). Runs freely.
  send / delete / pay / submit / invite / run - risky: becomes a Confirmation, never runs
            until you approve it from the Mac panel, the web app or the phone.
Tools whose output comes from outside (mail, web, files) set `untrusted_output`, so their
results are wrapped as data and any later risky request in the same turn is flagged.
In the background a risky tool always waits for your tap, even when your rule says Allow, and
a `screen` tool (it drives the real keyboard and mouse) asks for the screen first, or waits until he steps
away when he allowed that.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field

from ..safety import RISKY_KINDS, AuditLog, Confirmation, Confirmations, in_background, wrap_untrusted


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable[[dict], object]
    kind: str = "read"
    untrusted_output: bool = False
    summary: Callable[[dict], str] | None = None  # one line for the confirm card
    check: Callable[[dict], None] | None = None  # raises when the call can't work, before anyone is asked
    screen: bool = False  # drives the real screen (keyboard, mouse, windows): needs you to say yes or be away

    @property
    def risky(self) -> bool:
        return self.kind in RISKY_KINDS

    def describe(self, args: dict) -> str:
        if self.summary:
            return self.summary(args)
        return f"{self.name}({json.dumps(args, sort_keys=True)})"


@dataclass
class TurnContext:
    actor: str = "claude"
    why: str = ""
    saw_untrusted: bool = False
    confirmations: list[Confirmation] = field(default_factory=list)
    on_device: frozenset[str] = frozenset()  # tools the asking device runs itself (the iPhone Shortcut)
    tone: str = ""  # how to sound this turn (maxie.mood): glad, gentle, or suggest rest late at night
    verdict: asyncio.Event | None = None  # an early answer (maxie.speculate): only reads until it is set


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    confirmation: Confirmation | None = None


def schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or list(properties),
            "additionalProperties": False}


class Registry:
    def __init__(self, audit: AuditLog, confirmations: Confirmations, rules=None):
        self.audit, self.confirmations = audit, confirmations
        self.rules = rules  # (name, kind) -> "allow" | "ask" | "block"; None = the kinds' defaults
        self.screen_when_away: Callable[[], bool] = lambda: False  # Settings: "Use the screen when I'm away"
        self._tools: dict[str, Tool] = {}

    def add(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def api_tools(self) -> list[dict]:
        """Tool definitions for the Messages API, in a stable order so the prompt cache holds."""
        return [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in (self._tools[n] for n in self.names())]

    def execute(self, name: str, args: dict, ctx: TurnContext) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(f"unknown tool {name}", is_error=True)
        rule = self.rules(name, tool.kind) if self.rules else ("ask" if tool.risky else "allow")
        background = in_background()
        if background and rule == "allow" and (tool.risky or tool.screen):
            rule = "ask"  # Maxie on its own never sends, buys, deletes or takes the screen without your tap
        if rule == "block":
            self.audit.log(ctx.actor, f"blocked:{name}", args, ctx.why, "blocked by your rules")
            return ToolResult(f"Not done: {name} is blocked by the user's rules (Settings › Rules). Tell them so; do not retry.",
                              is_error=True)
        if rule == "ask" and tool.check is not None:
            try:
                tool.check(args)
            except Exception as e:  # reported to the model / you like a failed call  # noqa: BLE001
                return ToolResult(f"{name} failed: {e}", is_error=True)
        if rule == "ask":
            later = tool.screen and background and self.screen_when_away()
            c = self.confirmations.request(name, args, tool.describe(args), ctx.saw_untrusted, screen=tool.screen,
                                           status="away" if later else "pending")
            ctx.confirmations.append(c)
            self.audit.log(ctx.actor, f"request:{name}", args, ctx.why, f"{c.status} confirmation {c.id}")
            if later:
                return ToolResult(f"Not done yet. It needs the screen, so it runs when the user steps away from the Mac "
                                  f"(id {c.id}): {c.summary}. Tell them so; do not retry.", confirmation=c)
            return ToolResult(
                f"Not done yet. Waiting for the user to confirm (id {c.id}): {c.summary}. "
                "Tell them what needs their OK; do not retry.",
                confirmation=c,
            )
        return self._run(tool, args, ctx)

    def _run(self, tool: Tool, args: dict, ctx: TurnContext) -> ToolResult:
        try:
            out = tool.fn(args)
        except Exception as e:  # a failing tool is reported, never crashes the hub  # noqa: BLE001
            self.audit.log(ctx.actor, tool.name, args, ctx.why, f"error: {e}")
            return ToolResult(f"{tool.name} failed: {e}", is_error=True)
        text = out if isinstance(out, str) else json.dumps(out, default=str)
        self.audit.log(ctx.actor, tool.name, args, ctx.why, "ok")
        if tool.untrusted_output:
            ctx.saw_untrusted = True
            text = wrap_untrusted(tool.name, text)
        return ToolResult(text)

    def resolve(self, cid: str, approve: bool) -> ToolResult:
        """The user's answer to a confirmation. Only the hub API calls this."""
        c = self.confirmations.get(cid)
        if c is None:
            return ToolResult("no such confirmation", is_error=True)
        if c.status not in ("pending", "away"):
            return ToolResult(f"confirmation is {c.status}", is_error=True)
        if not approve:
            self.confirmations.set_status(cid, "denied")
            self.audit.log("user", f"deny:{c.tool}", c.args, "", "denied")
            return ToolResult("Cancelled.")
        self.confirmations.set_status(cid, "approved")
        tool = self._tools[c.tool]
        return self._run(tool, c.args, TurnContext(actor="user", why=f"approved {cid}"))
