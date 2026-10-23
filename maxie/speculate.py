"""An early answer. When the router has to ask Haiku what a turn is (~1.5 s), the quick-tier answer starts
at the same time with its events held back. The brain's answer depends on the model, never on the route, so when the
router picks that same model the held events are the real answer, ~1.5 s sooner; otherwise they are dropped.
Until the router agrees the early answer may only read: any other tool (an action, a send, a fact to remember, the
phone) waits on `TurnContext.verdict`, so a dropped answer never did anything."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from .hands.registry import TurnContext

_DONE = object()


class Early:
    """Runs one brain turn in a task, keeping its events until `release()` or `drop()`."""

    def __init__(self, run: AsyncIterator[dict], model: str, ctx: TurnContext):
        self.model, self.ctx = model, ctx
        self._held: asyncio.Queue = asyncio.Queue()
        self._task = asyncio.create_task(self._hold(run))

    async def _hold(self, run: AsyncIterator[dict]) -> None:
        try:
            async for ev in run:
                await self._held.put(ev)
        except Exception as e:  # raised again to whoever releases it, as in a normal turn  # noqa: BLE001
            await self._held.put(e)
            return
        await self._held.put(_DONE)

    async def release(self) -> AsyncIterator[dict]:
        """The router agreed: its tools may run now. Every held event, then the rest as they come."""
        if self.ctx.verdict is not None:
            self.ctx.verdict.set()
        while (ev := await self._held.get()) is not _DONE:
            if isinstance(ev, Exception):
                raise ev
            yield ev

    def drop(self) -> None:
        self._task.cancel()
