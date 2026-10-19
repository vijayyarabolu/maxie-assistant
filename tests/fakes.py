"""A fake Anthropic async client that plays back scripted turns, shaped like the SDK's objects."""

from __future__ import annotations

import itertools
from types import SimpleNamespace as NS

_ids = itertools.count(1)


def turn(text: str = "", tools: list[tuple[str, dict]] | None = None, stop: str | None = None,
         usage: dict | None = None, model: str | None = None, searches: int = 0) -> dict:
    """`searches`: server-side web searches the model ran in this turn."""
    return {"text": text, "tools": tools or [], "stop": stop, "usage": usage, "model": model, "searches": searches}


class FakeStream:
    def __init__(self, msg, chunks):
        self.msg, self.chunks = msg, chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self.chunks:
            yield NS(type="text", text=c)

    async def get_final_message(self):
        return self.msg


class FakeAnthropic:
    def __init__(self, turns: list[dict]):
        self.turns = list(turns)
        self.calls: list[dict] = []
        self.beta = NS(messages=NS(stream=self._stream))

    def _stream(self, **params):
        self.calls.append(params)
        t = self.turns.pop(0) if self.turns else turn("ok")
        content = []
        if t["text"]:
            content.append(NS(type="text", text=t["text"]))
        for _ in range(t.get("searches", 0)):
            content.append(NS(type="server_tool_use", id=f"srvtoolu_{next(_ids)}", name="web_search",
                              input={"query": "q"}))
            content.append(NS(type="web_search_tool_result", tool_use_id="x", content=[]))
        for name, args in t["tools"]:
            content.append(NS(type="tool_use", id=f"tu_{next(_ids)}", name=name, input=args))
        stop = t["stop"] or ("tool_use" if t["tools"] else "end_turn")
        usage = t["usage"] or {"input_tokens": 1000, "output_tokens": 100,
                               "cache_creation_input_tokens": 0, "cache_read_input_tokens": 3000,
                               "server_tool_use": {"web_search_requests": t.get("searches", 0)}}
        msg = NS(model=t["model"] or params["model"], stop_reason=stop, content=content, usage=usage)
        words = t["text"].split(" ")
        chunks = [w + (" " if i < len(words) - 1 else "") for i, w in enumerate(words)] if t["text"] else []
        return FakeStream(msg, chunks)


class FakeHaikuSync:
    """Sync client for HaikuRouter: returns one JSON answer."""

    def __init__(self, answer: str):
        self.answer = answer
        self.messages = NS(create=self._create)
        self.calls = 0

    def _create(self, **params):
        self.calls += 1
        return NS(content=[NS(type="text", text=self.answer)],
                  usage={"input_tokens": 400, "output_tokens": 30})
