"""'Ask my Claude': run the official Claude Code CLI on this Mac with his own login (his plan, personal use)
and stream the answer back. Triggered only by his words ("ask my Claude", "use my plan", "think deeply").

Never drives the Claude desktop app or the claude.ai website (automated access is against the terms and
risks his account). The CLI runs with his settings sources off (no hooks, no plugins, no MCP) and no tools,
in an empty folder, so the answer is just the model's: nothing on the Mac is read or changed.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from collections.abc import AsyncIterator, Callable
from pathlib import Path

TRIGGER = re.compile(r"\b(?:ask (?:my|your) claude|use my (?:plan|subscription|max plan)|think (?:deeply|really hard))\b", re.I)
STRIP = re.compile(r"^(?:hey |ok |okay )?(?:claude[,]? )?(?:please )?(?:ask (?:my|your) claude|use my (?:plan|subscription|max plan)|think (?:deeply|really hard))"
                   r"[,:]?\s*(?:and |to |about |on )?", re.I)
ACK = "Asking your Claude…"
TIMEOUT_S = 10 * 60
MAX_OUTPUT_CHARS = 20_000


def wants(text: str) -> bool:
    return bool(TRIGGER.search(text))


def question(text: str) -> str:
    """The question without the trigger words ("ask my Claude: what's a monad" -> "what's a monad")."""
    q = STRIP.sub("", text.strip()).strip(" ,:.")
    return q or text.strip()


def command(prompt: str) -> list[str]:
    return ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--setting-sources", "", "--tools", "", "--no-session-persistence"]


def available() -> bool:
    return shutil.which("claude") is not None


def parse(line: str) -> dict | None:
    """One stream-json line -> {"type": "text", "text"} | {"type": "result", ...} | None."""
    try:
        ev = json.loads(line)
    except ValueError:
        return None
    if ev.get("type") == "stream_event":
        delta = ev.get("event", {}).get("delta") or {}
        if delta.get("type") == "text_delta" and delta.get("text"):
            return {"type": "text", "text": delta["text"]}
        return None
    if ev.get("type") == "result":
        return {"type": "result", "ok": ev.get("subtype") == "success", "text": ev.get("result") or "",
                "model": "claude-code", "ms": ev.get("duration_ms"), "turns": ev.get("num_turns"),
                "is_error": bool(ev.get("is_error"))}
    return None


async def run(prompt: str, workdir: Path | None = None, timeout: float = TIMEOUT_S,
              spawn: Callable | None = None) -> AsyncIterator[dict]:
    """Stream text chunks then one result. `spawn` is injectable (tests): async (cmd, cwd) -> process."""
    workdir = workdir or Path.home() / ".maxie" / "ask-claude"
    workdir.mkdir(parents=True, exist_ok=True)
    spawn = spawn or _spawn
    try:
        proc = await spawn(command(prompt), workdir)
    except OSError as e:
        yield {"type": "result", "ok": False, "text": f"Couldn't start the Claude Code CLI: {e}", "model": "claude-code"}
        return
    got_result, chars = False, 0
    try:
        async with asyncio.timeout(timeout):
            async for raw in proc.stdout:
                ev = parse(raw.decode("utf-8", "replace"))
                if ev is None:
                    continue
                if ev["type"] == "text":
                    chars += len(ev["text"])
                    if chars > MAX_OUTPUT_CHARS:
                        continue
                else:
                    got_result = True
                yield ev
        await proc.wait()
    except TimeoutError:
        proc.kill()
        yield {"type": "result", "ok": False, "text": "Your Claude took longer than ten minutes; I stopped it.",
               "model": "claude-code"}
        return
    if not got_result:
        yield {"type": "result", "ok": False, "model": "claude-code",
               "text": "Your Claude didn't answer (is the CLI logged in? run `claude` once in Terminal)."}


async def _spawn(cmd: list[str], cwd: Path):
    return await asyncio.create_subprocess_exec(*cmd, cwd=str(cwd), stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL, stdin=asyncio.subprocess.DEVNULL)
