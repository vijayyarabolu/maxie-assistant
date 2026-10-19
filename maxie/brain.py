"""The brain: Claude over the Messages API, with tools, streaming, prompt caching and the budget cap.

Models: Haiku 5.5 for quick turns, Sonnet 5.5 for real tasks, Opus 5.5 when he says "think hard"
(the router picks). Caching: tools, the persona and the memory block are stable prefixes with
cache breakpoints; the conversation tail uses automatic caching; the current time goes in the
user turn so it never breaks the cache. Sonnet/Opus get web search (server tool) and the
server-side refusal fallback.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import datetime
from zoneinfo import ZoneInfo

from .budget import PRICES, Budget, tier_of
from .hands.registry import Registry, TurnContext, schema
from .maxie_look import style_line
from .memory import TOPICS, Memory, supported_by
from .models import Models
from .personality import SYSTEM_PROMPT
from .safety import KillSwitch

EFFORT = {"haiku": "low", "opus": "high"}  # Sonnet: medium
# The stable prefix (persona, memory, tools) is cached for an hour: his use is bursty, so one 2x write per
# hour beats a 1.25x write after every 5-minute gap, and the first word after a pause stays fast.
CACHE = {"type": "ephemeral", "ttl": "1h"}
MAX_TOKENS = 8000
MAX_STEPS = 8
WEB_SEARCH = {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}
# Quick turns (Haiku) start without the server search tool: it adds ~450 ms to the first word (measured
# 2026-10-15, medians 1141 vs 683 ms). A stub with the same name lets the model ask for it; the turn is
# then redone with the real tool, so live facts still come from a search and never from memory.
WEB_STUB = {"name": "web_search", "description": "Search the web for live facts: news, prices, scores, "
            "anything recent or outside what you know. Use it instead of guessing.",
            "input_schema": schema({"query": {"type": "string"}})}
REMEMBER_TOOL = {
    "name": "remember_fact",
    "description": "Save something the user told you about themselves: people, goals, habits, decisions, preferences, projects.",
    "input_schema": schema({"fact": {"type": "string"},
                            "topic": {"type": "string", "enum": list(TOPICS)}}),
}


def _read_tokens(usage) -> int:
    value = usage.get("cache_read_input_tokens") if isinstance(usage, dict) else getattr(usage, "cache_read_input_tokens", 0)
    return int(value or 0)


def _now_line(tz=None) -> str:
    return datetime.now(tz).astimezone().strftime("Now: %A %B %-d %Y, %-I:%M %p %Z")


def context_line(settings, clock=None) -> str:
    """Time, date, timezone, city and work hours, on every turn, so none of them is ever guessed."""
    tz = ZoneInfo(settings.timezone)
    now = clock() if clock else datetime.now(tz)
    if not isinstance(now, datetime):  # the hub's clock is epoch seconds
        now = datetime.fromtimestamp(float(now), tz)
    when = now.astimezone(tz).strftime("%A %B %-d %Y, %-I:%M %p %Z")
    who = f"The user is {settings.user_name}" if settings.user_name else "The user hasn't given a name"
    where = f" in {settings.weather_place}" if settings.weather_place else " (no city set)"
    line = f"Now: {when} ({settings.timezone}). {who}{where}; work hours {settings.work_start}-{settings.work_end}."
    style = style_line(getattr(settings, "personality", "friend"), getattr(settings, "chattiness", "normal"))
    return f"{line}\nStyle (Settings › Maxie): {style}" if style else line


class Brain:
    def __init__(self, client, registry: Registry, budget: Budget, memory: Memory, kill: KillSwitch,
                 now_line=_now_line, models: Models | None = None):
        self.client, self.registry, self.budget, self.memory, self.kill = client, registry, budget, memory, kill
        self.now_line = now_line
        self.models = models

    def _system(self) -> list[dict]:
        blocks = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": CACHE}]
        if md := self.memory.markdown():
            blocks.append({"type": "text", "text": "What you know about the user (from them):\n\n" + md,
                           "cache_control": CACHE})
        return blocks

    def _tools(self, model: str, web: bool = True) -> list[dict]:
        tools = [WEB_SEARCH if web else WEB_STUB]  # the server tool, or the stub that asks for it
        custom = sorted(self.registry.api_tools() + [REMEMBER_TOOL], key=lambda t: t["name"])
        custom[-1] = {**custom[-1], "cache_control": CACHE}
        return tools + custom

    @staticmethod
    def web_first(model: str) -> bool:
        """Sonnet / Opus turns carry the real search tool from the start; Haiku turns ask for it."""
        return tier_of(model) != "haiku"

    def _params(self, model: str, messages: list, web: bool | None = None) -> dict:
        web = self.web_first(model) if web is None else web
        p = {"model": model, "max_tokens": MAX_TOKENS, "system": self._system(), "tools": self._tools(model, web),
             "messages": messages, "cache_control": CACHE,
             "output_config": {"effort": EFFORT.get(tier_of(model) or "", "medium")}}
        if tier_of(model) != "haiku":
            p |= {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        return p

    async def warm(self, model: str) -> bool:
        """Write the cached prefix for `model` without generating (max_tokens 0): the next real turn
        starts from a warm cache. Haiku always; Sonnet only with Settings `warm_sonnet`. Whether warming
        Sonnet pays is not assumed: maxie.warm_experiment measures it (7 days off, 7 on) and keeps
        the winner. Returns False when there is no client or the API refused."""
        if self.client is None:
            return False
        p = {k: v for k, v in self._params(model, [{"role": "user", "content": "warm"}]).items()
             if k not in ("betas", "fallbacks")}
        try:
            msg = await self.client.messages.create(**{**p, "max_tokens": 0})
        except Exception:  # a failed warm-up only means the first real turn is slower  # noqa: BLE001
            return False
        self.budget.record(msg.model if msg.model in PRICES else model, msg.usage, "warm")
        return True

    def _remember(self, args: dict, said: str) -> tuple[str, bool]:
        fact = str(args.get("fact", ""))
        if not supported_by(fact, said):
            return "Not saved: only save what the user said, in their words.", True
        ok, msg = self.memory.remember(fact, str(args.get("topic", "other")))
        return msg, not ok

    async def run(self, history: list[dict], text: str, model: str,
                  ctx: TurnContext | None = None, attachments: list[dict] | None = None) -> AsyncIterator[dict]:
        """Yields events: text (streamed), tool, confirm, done. `history` is prior text turns;
        `attachments` are image/text content blocks (screenshot, pasted image, dropped file) for this turn."""
        ctx = ctx or TurnContext(why=text[:200])
        if self.client is None:
            msg = "I can't reach Claude: no API key is set yet (add an Anthropic key in Settings). Direct actions still work."
            yield {"type": "text", "text": msg}
            yield {"type": "done", "text": msg, "model": None, "cost": 0.0}
            return
        if self.budget.status().state == "capped":
            msg = "This month's API budget is used up, so I can only do direct Mac actions until it resets."
            yield {"type": "text", "text": msg}
            yield {"type": "done", "text": msg, "model": None, "cost": 0.0}
            return
        said = " ".join([m["content"] for m in history if m["role"] == "user"] + [text])
        # The tone line rides in the user turn, not the system prompt, so the cached prefix stays warm.
        tone = [{"type": "text", "text": f"How to sound this time: {ctx.tone}"}] if ctx.tone else []
        messages = [*history, {"role": "user", "content": [{"type": "text", "text": self.now_line()}, *tone,
                                                             *(attachments or []),
                                                             {"type": "text", "text": text}]}]
        gen, spoken, cost = self.kill.generation(), [], 0.0
        web = self.web_first(model)
        t0, first_ms, cache = time.perf_counter(), None, ""  # the model's first word, warm or cold
        for _ in range(MAX_STEPS):
            while self.kill.paused and not self.kill.stopped_since(gen):  # Activity › Pause: hold between steps
                await asyncio.sleep(0.2)
            if self.kill.stopped_since(gen):
                yield {"type": "done", "text": "".join(spoken), "model": model, "cost": cost, "stopped": True}
                return
            try:
                async with self.client.beta.messages.stream(**self._params(model, messages, web)) as stream:
                    async for event in stream:
                        if self.kill.stopped_since(gen):
                            break
                        if event.type == "text":
                            if first_ms is None:
                                first_ms = round((time.perf_counter() - t0) * 1000, 1)
                            spoken.append(event.text)
                            yield {"type": "text", "text": event.text}
                    final = await stream.get_final_message()
            except Exception as e:  # only a retired model (404) is handled here  # noqa: BLE001
                if getattr(e, "status_code", None) != 404 or not self.models or spoken:
                    raise
                back = self.models.fallback(model)  # previous id, and a models recheck is queued
                if not back:
                    raise
                yield {"type": "tool", "name": f"model_fallback:{back}", "ok": True}
                model = back
                continue  # retry once with the old id (a second 404 raises)
            billed = final.model if final.model in PRICES else model
            cost += self.budget.record(billed, final.usage, "chat")
            if not cache:
                cache = "warm" if _read_tokens(final.usage) > 0 else "cold"
            for b in final.content:  # server-side searches run inside the API call: show them like any tool
                if b.type == "server_tool_use":
                    yield {"type": "tool", "name": b.name, "ok": True}

            if final.stop_reason == "refusal":
                msg = "I can't help with that one."
                yield {"type": "text", "text": msg}
                spoken.append(msg)
                break
            if final.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": final.content})
                continue
            tool_uses = [b for b in final.content if b.type == "tool_use"]
            if final.stop_reason != "tool_use" or not tool_uses:
                break
            if not web and any(b.name == "web_search" for b in tool_uses):
                web = True  # it wants live facts: redo this step with the real search tool
                yield {"type": "tool", "name": "web_search_requested", "ok": True}
                continue

            results = []
            for block in tool_uses:
                args = block.input if isinstance(block.input, dict) else {}
                if block.name == "remember_fact":
                    content, is_error = self._remember(args, said)
                elif block.name in ctx.on_device:  # asked from the iPhone: its Shortcut does it there
                    self.registry.audit.log("phone", block.name, args, ctx.why, "sent to device")
                    yield {"type": "phone_action", "action": block.name, "args": args}
                    content, is_error = "Sent to the iPhone; the phone does it now.", False
                else:
                    r = await asyncio.to_thread(self.registry.execute, block.name, args, ctx)
                    content, is_error = r.content, r.is_error
                    if r.confirmation:
                        c = r.confirmation
                        yield {"type": "confirm", "id": c.id, "summary": c.summary, "after_untrusted": c.after_untrusted}
                yield {"type": "tool", "name": block.name, "ok": not is_error}
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": content,
                                "is_error": is_error})
            messages.append({"role": "assistant", "content": final.content})
            messages.append({"role": "user", "content": results})
        else:
            msg = " I stopped after several steps; tell me if you want me to keep going."
            spoken.append(msg)
            yield {"type": "text", "text": msg}
        yield {"type": "done", "text": "".join(spoken).strip(), "model": model, "cost": round(cost, 6),
               "first_model_ms": first_ms, "cache": cache}
