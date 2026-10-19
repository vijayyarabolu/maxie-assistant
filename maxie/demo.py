"""The demo hub's stand-in for Claude: a small script shaped like the Anthropic SDK, so `make demo` can be
tried with no API key and no network. It picks one fixture tool by keyword, then sums up what came back.

It is honest about what it is: every reply starts with "Demo:". Nothing here is used outside the demo hub.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace as NS

# keyword -> (tool, arguments)
PICKS = (
    (("mail", "email", "inbox"), ("search_email", {"account": "main", "query": ""})),
    (("interview", "recruiter"), ("search_email", {"account": "job", "query": "interview"})),
    (("calendar", "meeting", "schedule", "today"), ("calendar_events", {"days": 1})),
    (("weather", "rain", "umbrella", "cold", "warm"), ("weather", {"days": 2})),
    (("drive", "file", "resume", "doc"), ("drive_search", {"query": "resume"})),
    (("job", "jobs", "apply", "application"), ("job_search_status", {})),
)
USAGE = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


def _last_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m["role"] != "user":
            continue
        content = m["content"]
        if isinstance(content, str):
            return content
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        if texts:
            return texts[-1]
    return ""


def _tool_results(messages: list[dict]) -> list[str]:
    last = messages[-1]["content"] if messages else []
    if not isinstance(last, list):
        return []
    return [str(b.get("content", "")) for b in last if isinstance(b, dict) and b.get("type") == "tool_result"]


def _summary(raw: str) -> str:
    inner = re.search(r"<untrusted_data[^>]*>\s*(.*?)\s*</untrusted_data>", raw, re.S)
    raw = inner.group(1) if inner else raw
    try:
        data = json.loads(raw)
    except ValueError:
        return raw[:400]
    if isinstance(data, list) and data and isinstance(data[0], dict):
        keys = ("subject", "title", "name", "company")
        lines = [next((str(d[k]) for k in keys if d.get(k)), "") for d in data[:4]]
        joined = "; ".join(x for x in lines if x)
        return joined if joined[-1:] in ".?!" else joined + "."
    return str(data)[:400]


class _Stream:
    def __init__(self, msg):
        self.msg = msg

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for b in self.msg.content:
            if b.type == "text":
                for i, word in enumerate(b.text.split(" ")):
                    yield NS(type="text", text=word if i == 0 else " " + word)

    async def get_final_message(self):
        return self.msg


class DemoAnthropic:
    """Enough of AsyncAnthropic for the brain: beta.messages.stream, messages.create, models.list."""

    def __init__(self, tools: set[str] | None = None):
        self.tools = tools
        self.beta = NS(messages=NS(stream=self._stream))
        self.messages = NS(create=self._create)
        self.models = NS(list=self._list)

    async def _create(self, **params):
        return NS(model=params.get("model", ""), content=[], usage=USAGE, stop_reason="end_turn")

    async def _list(self, **params):
        return NS(data=[])

    def _stream(self, **params):
        messages = params.get("messages", [])
        model = params.get("model", "")
        results = _tool_results(messages)
        if results:
            text = "Demo: here is what the fixture data says. " + " ".join(_summary(r) for r in results)
            return _Stream(NS(model=model, stop_reason="end_turn", content=[NS(type="text", text=text)], usage=USAGE))
        said = _last_text(messages).lower()
        names = {t["name"] for t in params.get("tools", []) if isinstance(t, dict) and "name" in t}
        for words, (tool, args) in PICKS:
            if any(w in said for w in words) and (not names or tool in names):
                block = NS(type="tool_use", id=f"demo_{tool}", name=tool, input=args)
                return _Stream(NS(model=model, stop_reason="tool_use", content=[block], usage=USAGE))
        text = ("Demo: I'm the demo hub, so I'm not calling Claude. Try \"what's in my inbox\", \"what's on my "
                "calendar\", \"will it rain\" or \"open Spotify\". Add an Anthropic key in Settings for real answers.")
        return _Stream(NS(model=model, stop_reason="end_turn", content=[NS(type="text", text=text)], usage=USAGE))
