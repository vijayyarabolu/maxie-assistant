"""The agent: one command in, a stream of events out.

route -> (direct action | local answer | brain) -> events for the Mac panel, phone and voice.
Events: route, ack (say this right away), text (stream), tool, confirm, done.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import background as bg
from . import mood
from .attachments import gather
from .brain import Brain
from .budget import SONNET
from .config import Settings
from .db import DB
from .events import Events
from .hands import ask_claude
from .hands.apple import AppleBridge, AppleUnavailable
from .hands.registry import Registry, TurnContext
from .latency import APPLE_MODEL, Latency, Turn, kind_of
from .memory import Memory, guess_topic
from .personality import ACKS, Wellbeing
from .router import Decision, Router
from .safety import KillSwitch
from .tasks import Tasks

HISTORY_TURNS = 10  # prior turns (user + assistant) sent to the brain
CONTEXT_GAP_S = 2 * 3600  # a turn older than this starts a fresh context (an old "it's 9:54" would look wrong)
# Direct actions the device does itself when the command came from it: the iOS Shortcut can run
# timers, music and apps; the web app can only open links. Everything else runs on the Mac.
DEVICE_ACTIONS = {"phone": {"timer", "music", "open_url", "open_app", "add_reminder", "draft_message"},
                  "pwa": {"open_url"}}


def done_line(action: str, args: dict) -> str:
    if action == "open_app":
        return f"Opening {args['name']}."
    if action == "open_url" and args.get("label"):
        return args["label"]
    if action == "add_reminder":
        return f"Reminder set: {args['text']}" + (f", {args['when']}." if args.get("when") else ".")
    if action == "draft_message":
        return f"Message to {args['to']} is ready. Tap send when it looks right."
    if action == "open_url":
        return "Opening " + args["url"].split("//")[-1].removeprefix("www.").split("/")[0] + "."
    if action == "music":
        cmd, q = args["command"], args.get("query", "")
        return {"play": f"Playing {q}." if q else "Playing.", "pause": "Paused.", "next": "Skipping.",
                "previous": "Going back."}[cmd]
    if action == "volume":
        lvl = str(args["level"])
        return {"up": "Turning it up.", "down": "Turning it down.", "mute": "Muted."}.get(lvl, f"Volume {lvl}.")
    if action == "timer":
        m = float(args["minutes"])
        span = f"{m * 60:.0f} second" if m < 1 else f"{m:g} minute"
        return f"{span} timer started" + (f" for {args['label']}." if args.get("label") else ".")
    if action == "do_not_disturb":
        return "Do Not Disturb is " + ("on." if args["on"] else "off.")
    if action == "open_file":
        return f"Opening {Path(str(args.get('path', ''))).expanduser().name or 'it'}."
    if action == "watch_me":
        return "Watching. Do it once, then say done watching, or press ⌥⇧W." if args.get("on", True) else \
            "Stopped watching."
    if action == "run_flow":
        return f"Replaying {args['name']} on your Mac."
    return "Done."


class Conversations:
    def __init__(self, db: DB, clock=time.time):
        self.db, self.clock = db, clock
        db.script("""CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY, conv TEXT, ts REAL, role TEXT, text TEXT, meta TEXT)""")

    def add(self, conv: str, role: str, text: str, meta: str = "") -> None:
        self.db.execute("INSERT INTO messages(conv,ts,role,text,meta) VALUES (?,?,?,?,?)",
                        (conv, self.clock(), role, text, meta))

    def history(self, conv: str, limit: int = HISTORY_TURNS) -> list[dict]:
        """The recent turns of one conversation (only those from the last two hours)."""
        rows = self.db.all("SELECT role, text FROM messages WHERE conv=? AND text != '' AND ts >= ? ORDER BY id DESC LIMIT ?",
                           (conv, self.clock() - CONTEXT_GAP_S, limit))[::-1]
        while rows and rows[0]["role"] != "user":  # must start with a user turn
            rows.pop(0)
        out: list[dict] = []
        for r in rows:  # merge same-role neighbours so roles alternate
            if out and out[-1]["role"] == r["role"]:
                out[-1]["content"] += "\n" + r["text"]
            else:
                out.append({"role": r["role"], "content": r["text"]})
        return out

    def recent(self, conv: str | None = None, limit: int = 100) -> list[dict]:
        if conv:
            return self.db.all("SELECT * FROM messages WHERE conv=? ORDER BY id DESC LIMIT ?", (conv, limit))[::-1]
        return self.db.all("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,))[::-1]


class Agent:
    def __init__(self, router: Router, brain: Brain, registry: Registry, memory: Memory, kill: KillSwitch,
                 conversations: Conversations, wellbeing: Wellbeing, settings: Settings, briefs=None,
                 events: Events | None = None, latency: Latency | None = None, tasks: Tasks | None = None,
                 clock=time.time):
        self.router, self.brain, self.registry, self.memory, self.kill = router, brain, registry, memory, kill
        self.conversations, self.wellbeing, self.settings, self.briefs = conversations, wellbeing, settings, briefs
        self.events = events
        self.latency = latency
        self.tasks = tasks
        self.clock = clock
        self.moments: mood.Moments | None = None  # rare idle moments; told about every turn (services wires it)
        self.apple: AppleBridge | None = None  # Maxie.app's bridge: the on-device model for text tasks
        self.watch = None  # maxie.watch.Watch, set by the hub: "stop watching" and replies to suggestions
        self.background: bg.Background | None = None  # "in the background, ..." queues the task

    def _reaction(self, text: str) -> mood.Reaction | None:
        return mood.react(text, datetime.fromtimestamp(self.clock(), ZoneInfo(self.settings.timezone)))

    async def handle(self, text: str, conv: str = "default", device: str = "mac",
                     images: list[str] | None = None, files: list[str] | None = None) -> AsyncIterator[dict]:
        """Run one command and mirror it to the live state stream (think -> talk -> rest)."""
        bus = self.events
        if bus is None:
            async for ev in self._timed(text, conv, device, images, files):
                yield ev
            return
        bus.transcript("user", text.strip(), final=True)
        bus.set_state("think")
        if self.moments is not None:
            self.moments.note_turn()
        reaction = self._reaction(text)
        talking = False
        try:
            async for ev in self._timed(text, conv, device, images, files):
                kind = ev["type"]
                if kind in ("text", "ack"):
                    if not talking:
                        bus.set_state("talk")
                        talking = True
                    if kind == "text":
                        bus.transcript("assistant", ev["text"])
                elif kind == "tool":
                    bus.step(ev["name"], ev["ok"])
                elif kind == "confirm":
                    bus.publish(ev)
                elif kind == "done":
                    bus.transcript("assistant", "", final=True)
                yield ev
        finally:
            bus.rest()
            if reaction and reaction.state:  # good news: a little dance; a hard day: gentle (maxie.mood)
                bus.set_state(reaction.state, reaction.hold)

    async def run_background(self, text: str, tasks: Tasks) -> AsyncIterator[dict]:
        """A queued task (maxie.background): its own Activity row, no panel transcript, no mascot, no
        latency sample, and a paused foreground task stays paused."""
        async for ev in self._timed(text, "background", "background", tasks=tasks, quiet=True):
            yield ev

    async def _timed(self, text: str, conv: str, device: str, images: list[str] | None = None,
                     files: list[str] | None = None, tasks: Tasks | None = None,
                     quiet: bool = False) -> AsyncIterator[dict]:
        """_handle, with the route / first word / done times written to the latency log and the task
        timeline (Activity) kept up to date step by step."""
        t0 = time.perf_counter()
        route: dict = {}
        first: float | None = None
        tasks = tasks or self.tasks
        latency = None if quiet else self.latency
        if tasks:
            tasks.start(text, device)
            if not quiet:
                self.kill.resume()  # a new task is never born paused
        try:
            async for ev in self._handle(text, conv, device, images, files):
                kind = ev["type"]
                if kind == "route":
                    route = ev
                    if tasks:
                        tasks.routed(ev["route"], ev["model"])
                elif kind in ("text", "ack") and first is None and ev.get("text", "").strip():
                    first = (time.perf_counter() - t0) * 1000
                    if tasks:
                        tasks.first_word()
                elif kind == "tool" and tasks:
                    tasks.step(ev["name"], ev["ok"])
                elif kind == "done":
                    if latency is not None and route:
                        model = ev.get("model") or route["model"]  # who really answered (Apple, or Haiku after it)
                        latency.record(Turn(kind_of(route["direct"], model), text, route["route"], route["source"],
                                                 model, route["latency_ms"], first, (time.perf_counter() - t0) * 1000,
                                                 float(ev.get("cost") or 0.0), ev.get("first_model_ms"),
                                                 ev.get("cache", "")))
                    if tasks:
                        tasks.finish(ev.get("text", ""), float(ev.get("cost") or 0.0), stopped=bool(ev.get("stopped")))
                yield ev
        except Exception:
            if tasks:
                tasks.finish("", 0.0, error=True)
            raise

    async def _handle(self, text: str, conv: str, device: str, images: list[str] | None = None,
                      files: list[str] | None = None) -> AsyncIterator[dict]:
        text = text.strip()
        history = self.conversations.history(conv)
        att = gather(images, files, self.settings.allowed_folders)
        if att.problem:
            self.conversations.add(conv, "user", text)
            self.conversations.add(conv, "assistant", att.problem)
            yield {"type": "text", "text": att.problem}
            yield {"type": "done", "text": att.problem, "model": None, "cost": 0.0}
            return
        self.conversations.add(conv, "user", " ".join([*att.markers, text]).strip())
        if (said := self.watch.answer(text) if self.watch is not None else None) is not None:  # GOAL5 Phase 4
            yield {"type": "route", "route": "watch", "source": "rules", "confidence": 1.0, "direct": True,
                   "model": None, "latency_ms": 0.0}
            self.conversations.add(conv, "assistant", said, "watch")
            yield {"type": "text", "text": said}
            yield {"type": "done", "text": said, "model": None, "cost": 0.0}
            return
        if self.background is not None and device != "background" and (task := bg.wants(text)):
            self.background.add(task)
            yield {"type": "route", "route": "background", "source": "rules", "confidence": 1.0, "direct": True,
                   "model": None, "latency_ms": 0.0}
            self.conversations.add(conv, "assistant", bg.ACK, "background")
            yield {"type": "text", "text": bg.ACK}
            yield {"type": "done", "text": bg.ACK, "model": None, "cost": 0.0}
            return
        if self.settings.ask_my_claude and ask_claude.wants(text) and not att.blocks:
            async for ev in self._ask_claude(text, conv):
                yield ev
            return
        if att.has_image:  # what's on the screen / this picture: Sonnet with vision, no router
            d = Decision(route="screen", source="rules", confidence=1.0, model=SONNET)
        elif att.blocks:
            d = Decision(route="multi_step", source="rules", confidence=1.0, model=SONNET)
        else:
            d = await asyncio.to_thread(self.router.route, text)
        if d.route == "text_task":
            async for ev in self._text_task(d, text, history, conv):
                yield ev
            return
        yield {"type": "route", "route": d.route, "source": d.source, "confidence": round(d.confidence, 3),
               "direct": d.direct, "model": None if d.direct else d.model, "latency_ms": round(d.latency_ms, 2)}

        care = self.wellbeing.check(text)
        if d.direct and d.action in DEVICE_ACTIONS.get(device, ()):
            self.registry.audit.log(device, d.action, d.args, "direct", "sent to device")
            yield {"type": "phone_action", "action": d.action, "args": d.args}
            reply = done_line(d.action, d.args)
        else:
            reply = await self._local(d) if d.direct else None
            if reply is None and d.direct and d.action and self.registry.get(d.action) is not None:
                reply, c = await self._direct_tool(d)
                if c is not None:  # his rules say "Ask me" for this action: a card, not a false "done"
                    yield {"type": "confirm", "id": c.id, "summary": c.summary, "after_untrusted": c.after_untrusted}
                yield {"type": "tool", "name": d.action, "ok": not reply.startswith("Couldn't do that")}
        if reply is not None:
            reply = f"{care} {reply}" if care else reply
            yield {"type": "text", "text": reply}
            yield {"type": "done", "text": reply, "model": None, "cost": 0.0}
        else:
            if care:
                yield {"type": "text", "text": care + " "}
            if ack := ACKS.get(d.route):
                yield {"type": "ack", "text": ack}
            reply = care + " " if care else ""
            reaction = self._reaction(text)
            ctx = TurnContext(why=text[:200], on_device=frozenset(DEVICE_ACTIONS.get(device, ())),
                              tone=reaction.tone if reaction else "")
            async for ev in self.brain.run(history, text, d.model, ctx, attachments=att.blocks):
                if ev["type"] == "done":
                    reply += ev["text"]
                yield ev
        self.conversations.add(conv, "assistant", reply.strip(), d.route)

    async def _text_task(self, d: Decision, text: str, history: list[dict], conv: str) -> AsyncIterator[dict]:
        """Summaries, rewrites, replies, dates: Apple's on-device model first ($0, private, ~2 s), Haiku when
        Apple Intelligence or the bridge isn't there."""
        yield {"type": "route", "route": d.route, "source": d.source, "confidence": round(d.confidence, 3),
               "direct": False, "model": APPLE_MODEL, "latency_ms": round(d.latency_ms, 2)}
        reply = await asyncio.to_thread(self._on_device, d.args)
        if reply:
            yield {"type": "text", "text": reply}
            yield {"type": "done", "text": reply, "model": APPLE_MODEL, "cost": 0.0}
        else:
            reply = ""
            async for ev in self.brain.run(history, text, self.router.models.active("haiku"), TurnContext(why=text[:200])):
                if ev["type"] == "done":
                    reply = ev["text"]
                yield ev
        self.conversations.add(conv, "assistant", reply.strip(), d.route)

    def _on_device(self, args: dict) -> str | None:
        if self.apple is None:
            return None
        try:
            return self.apple.fm(args["task"], args["text"], args.get("how", "")).strip() or None
        except AppleUnavailable:
            return None  # the reason is in the bridge's log; Haiku answers instead

    async def _ask_claude(self, text: str, conv: str) -> AsyncIterator[dict]:
        """His plan, the legit way: the Claude Code CLI with his login answers; nothing is billed to the API."""
        yield {"type": "route", "route": "ask_claude", "source": "rules", "confidence": 1.0, "direct": False,
               "model": "claude-code", "latency_ms": 0.0}
        if not ask_claude.available():
            msg = "The Claude Code CLI isn't installed on this Mac, so I can't ask your Claude. Install it and sign in once."
            yield {"type": "text", "text": msg}
            yield {"type": "done", "text": msg, "model": None, "cost": 0.0}
            self.conversations.add(conv, "assistant", msg, "ask_claude")
            return
        yield {"type": "ack", "text": ask_claude.ACK}
        yield {"type": "tool", "name": "asking your Claude", "ok": True}
        parts: list[str] = []
        async for ev in ask_claude.run(ask_claude.question(text)):
            if ev["type"] == "text":
                parts.append(ev["text"])
                yield ev
            else:
                if not ev["ok"] or not parts:
                    msg = ev["text"] or "Your Claude didn't answer."
                    parts = [msg]
                    yield {"type": "text", "text": msg}
                yield {"type": "tool", "name": "ask_my_claude", "ok": ev["ok"]}
        reply = "".join(parts).strip()
        yield {"type": "done", "text": reply, "model": "claude-code", "cost": 0.0}
        self.conversations.add(conv, "assistant", reply, "ask_claude")

    async def _local(self, d: Decision) -> str | None:
        """Answer without Claude. None means 'hand it to the brain after all'."""
        a, args = d.action, d.args
        if d.route == "control":
            if a == "stop":
                self.kill.stop()
                return "Stopped."
            if a == "mute_proactive":
                self.settings.proactive_muted = True
                self.settings.save()
                return "Notifications muted. Say unmute in Settings when you want them back."
            if a == "brief" and self.briefs:
                return await asyncio.to_thread(self.briefs.build, args.get("kind", "morning"))
            return None
        if d.route == "memory":
            if a == "remember":
                return self.memory.remember(args["fact"], guess_topic(args["fact"]))[1]
            if a == "forget":
                n = self.memory.forget(args["what"])
                return f"Forgot {n} thing{'s' if n != 1 else ''} about {args['what']}." if n else \
                    f"I didn't have anything about {args['what']}."
            if a == "recall":
                return self.memory.recall_line(args["q"], self.settings.timezone)
            if a == "show":
                md = self.memory.markdown()
                return ("Here's what I know:\n" + md) if md else "Nothing yet. Tell me things and I'll remember them."
        if d.route == "messages" and self.registry.get(a):
            r = await asyncio.to_thread(self.registry.execute, a, args, TurnContext(actor="router", why="direct"))
            return r.content
        if d.route == "quick_question":
            now = datetime.now(ZoneInfo(self.settings.timezone))
            if a == "time":
                return now.strftime("It's %-I:%M %p.")
            if a == "date":
                return now.strftime("It's %A, %B %-d.")
            if a == "math":
                return f"{args['result']}."
        return None

    async def _direct_tool(self, d: Decision):
        """A routed Mac action, through the registry so his rules apply. Returns (reply, confirmation)."""
        r = await asyncio.to_thread(self.registry.execute, d.action, d.args, TurnContext(actor="router", why="direct"))
        if r.confirmation is not None:
            return f"That needs your OK first: {r.confirmation.summary}.", r.confirmation
        return (f"Couldn't do that: {r.content}" if r.is_error else done_line(d.action, d.args)), None
