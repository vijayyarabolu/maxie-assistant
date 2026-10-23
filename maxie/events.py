"""Live agent state for the phone's header bot and the Mac notch (`GET /api/events`, SSE).

States are the mascot's: idle, listen, think, talk, happy, proud, sleep, gentle (a hard day: see mood.py),
wave (a hello), closed (eyes closed: not watching), stretch (a stretch break after a long work stretch).
- think while a command is being worked on, talk while the reply streams, then back to base.
- happy (interview/recruiter alert) and proud (an application marked applied) hold for a few
  seconds, then return to base.
- base is sleep while he sleeps (maxie.presence), closed while Maxie isn't watching (maxie.watch),
  idle otherwise.
Transcript chunks ride the same stream so the notch panel can show the live conversation.
Publishing is thread-safe (the scheduler runs in a worker thread); each subscriber gets its own queue.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from collections.abc import AsyncIterator, Callable

STATES = ("idle", "listen", "think", "talk", "happy", "proud", "sleep", "gentle", "wave", "closed", "stretch")
HEARTBEAT_S = 15.0  # a comment line this often keeps proxies and the Mac's URLSession from closing a quiet stream
HOLD_SECONDS = {"happy": 8.0, "proud": 8.0, "gentle": 45.0}


class Events:
    def __init__(self, quiet: Callable[[], bool] = lambda: False, clock=time.time):
        self.quiet, self.clock = quiet, clock
        self.eyes_closed: Callable[[], bool] = lambda: False  # set by maxie.watch
        self._state, self._until = "idle", 0.0
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()

    def base(self) -> str:
        return "sleep" if self.quiet() else "closed" if self.eyes_closed() else "idle"

    def current(self) -> dict:
        state = self._state
        if self._until and self.clock() >= self._until:
            state, self._state, self._until = self.base(), self.base(), 0.0
        if state in ("idle", "sleep", "closed") and not self._until:  # a held sleep (late night) shows even outside quiet hours
            state = self.base()
        hold = max(0.0, self._until - self.clock()) if self._until else 0
        return {"type": "state", "state": state, "base": self.base(), "hold": round(hold, 1)}

    def set_state(self, state: str, hold: float | None = None) -> None:
        if state not in STATES:
            raise ValueError(f"unknown state {state}")
        hold = HOLD_SECONDS.get(state, 0.0) if hold is None else hold
        with self._lock:
            if state in ("idle", "sleep") and self._until and self.clock() < self._until:
                return  # a celebration is showing; it ends on its own
            self._state, self._until = state, (self.clock() + hold if hold else 0.0)
        self.publish({"type": "state", "state": state, "base": self.base(), "hold": hold})

    def rest(self) -> None:
        """Back to idle / sleep (end of a reply, or quiet hours starting or ending)."""
        self.set_state(self.base())

    def transcript(self, role: str, text: str, final: bool = False) -> None:
        self.publish({"type": "transcript", "role": role, "text": text, "final": final})

    def step(self, name: str, ok: bool = True) -> None:
        self.publish({"type": "step", "name": name, "ok": ok})

    def publish(self, ev: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(_put, q, ev)
            except RuntimeError:  # loop closed
                self._drop(q)

    def _drop(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs = [(lp, x) for lp, x in self._subs if x is not q]

    async def subscribe(self) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self._lock:
            self._subs.append((asyncio.get_running_loop(), q))
        try:
            yield self.current()
            while True:
                yield await q.get()
        finally:
            self._drop(q)


def _put(q: asyncio.Queue, ev: dict) -> None:
    if q.full():  # a slow client loses old events, never blocks the agent
        q.get_nowait()
    q.put_nowait(ev)


async def sse(events: Events, heartbeat_s: float = HEARTBEAT_S, once: bool = False) -> AsyncIterator[str]:
    """`GET /api/events` as SSE lines: the current state, then every event, a keep-alive in quiet stretches.
    The pending read survives a keep-alive: `wait_for(anext(sub))` would cancel the subscription on its first
    timeout and end the stream every quiet 15 s (the notch showed "offline" every 16 s)."""
    sub = events.subscribe()
    pending: asyncio.Future | None = None
    try:
        yield f"data: {json.dumps(await anext(sub))}\n\n"
        if once:
            return
        while True:
            pending = pending or asyncio.ensure_future(anext(sub))
            done, _ = await asyncio.wait({pending}, timeout=heartbeat_s)
            if not done:
                yield ": keep-alive\n\n"
                continue
            try:
                ev, pending = pending.result(), None
            except StopAsyncIteration:  # the subscription closed (hub stopping): end the stream cleanly
                return
            yield f"data: {json.dumps(ev, default=str)}\n\n"
    finally:
        if pending is not None:  # the client left mid-wait: the read must end before the subscription closes
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        await sub.aclose()
