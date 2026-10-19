"""'Ask my Claude': the official CLI on his plan, streamed back; only on his words, never the website."""

import json

from fastapi.testclient import TestClient

from maxie.hands import ask_claude
from maxie.hub.api import create_app
from tests.test_api import TOKEN, H, ndjson


def test_trigger_words_and_question():
    assert ask_claude.wants("ask my Claude what a monad is")
    assert ask_claude.wants("hey claude, use my plan: plan a 3 day trip to Austin")
    assert ask_claude.wants("think deeply about whether I should take the offer")
    assert not ask_claude.wants("what time is it") and not ask_claude.wants("think hard about this")
    assert ask_claude.question("ask my Claude what a monad is") == "what a monad is"
    assert ask_claude.question("Hey Claude, use my plan: plan a 3 day trip to Austin") == "plan a 3 day trip to Austin"
    assert ask_claude.question("think deeply, should I take the offer?") == "should I take the offer?"


def test_command_keeps_his_mac_out_of_it():
    cmd = ask_claude.command("q")
    assert cmd[:3] == ["claude", "-p", "q"] and "--output-format" in cmd and "stream-json" in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == "" and cmd[cmd.index("--tools") + 1] == ""
    assert "--no-session-persistence" in cmd and "--dangerously-skip-permissions" not in cmd


def test_parse_stream_json_lines():
    delta = json.dumps({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}}})
    assert ask_claude.parse(delta) == {"type": "text", "text": "hi"}
    assert ask_claude.parse(json.dumps({"type": "stream_event", "event": {"type": "message_start"}})) is None
    assert ask_claude.parse(json.dumps({"type": "assistant", "message": {}})) is None
    r = ask_claude.parse(json.dumps({"type": "result", "subtype": "success", "result": "hi", "duration_ms": 12, "num_turns": 1}))
    assert r["ok"] and r["text"] == "hi" and r["model"] == "claude-code" and r["ms"] == 12
    assert ask_claude.parse("not json") is None


class FakeProc:
    def __init__(self, lines):
        self.lines = lines
        self.killed = False

    @property
    def stdout(self):
        async def gen():
            for line in self.lines:
                yield (line + "\n").encode()
        return gen()

    async def wait(self):
        return 0

    def kill(self):
        self.killed = True


def lines(*texts, result=True):
    out = [json.dumps({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}}}) for t in texts]
    if result:
        out.append(json.dumps({"type": "result", "subtype": "success", "result": "".join(texts), "duration_ms": 5, "num_turns": 1}))
    return out


async def test_run_streams_text_then_result(tmp_path):
    seen = []

    async def spawn(cmd, cwd):
        seen.append((cmd, cwd))
        return FakeProc(lines("Hel", "lo"))
    evs = [ev async for ev in ask_claude.run("q", workdir=tmp_path / "ask", spawn=spawn)]
    assert [e["type"] for e in evs] == ["text", "text", "result"] and evs[-1]["ok"] and evs[-1]["text"] == "Hello"
    assert seen[0][1] == tmp_path / "ask" and (tmp_path / "ask").is_dir()


async def test_run_without_a_result_or_without_the_cli(tmp_path):
    async def silent(cmd, cwd):
        return FakeProc(lines("partial", result=False))
    evs = [ev async for ev in ask_claude.run("q", workdir=tmp_path, spawn=silent)]
    assert evs[-1]["type"] == "result" and not evs[-1]["ok"] and "logged in" in evs[-1]["text"]

    async def missing(cmd, cwd):
        raise FileNotFoundError("claude")
    evs = [ev async for ev in ask_claude.run("q", workdir=tmp_path, spawn=missing)]
    assert evs == [{"type": "result", "ok": False, "text": "Couldn't start the Claude Code CLI: claude", "model": "claude-code"}]


def test_agent_streams_his_claude_and_bills_nothing(make_services, monkeypatch):
    async def fake_run(prompt, **kw):
        assert prompt == "what's a monad"
        yield {"type": "text", "text": "A monad is "}
        yield {"type": "text", "text": "a box."}
        yield {"type": "result", "ok": True, "text": "A monad is a box.", "model": "claude-code"}
    monkeypatch.setattr(ask_claude, "run", fake_run)
    monkeypatch.setattr(ask_claude, "available", lambda: True)
    s = make_services()
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    evs = ndjson(c.post("/api/chat", json={"text": "ask my Claude what's a monad"}, headers=H))
    assert evs[0]["route"] == "ask_claude" and evs[0]["model"] == "claude-code"
    assert evs[1] == {"type": "ack", "text": ask_claude.ACK}
    assert evs[-1]["text"] == "A monad is a box." and evs[-1]["model"] == "claude-code" and evs[-1]["cost"] == 0.0
    assert {"type": "tool", "name": "ask_my_claude", "ok": True} in evs
    assert s.budget.status().month_usd == 0.0
    assert c.get("/api/history?conversation=default", headers=H).json()[-1]["text"] == "A monad is a box."
    assert c.get("/api/latency", headers=H).json()["recent"][0]["kind"] == "heavy"


def test_off_switch_and_missing_cli(make_services, monkeypatch):
    monkeypatch.setattr(ask_claude, "available", lambda: False)
    s = make_services()
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    evs = ndjson(c.post("/api/chat", json={"text": "ask my claude what's a monad"}, headers=H))
    assert evs[0]["route"] == "ask_claude" and "isn't installed" in evs[-1]["text"]
    s.settings.ask_my_claude = False
    evs = ndjson(c.post("/api/chat", json={"text": "ask my claude what's a monad"}, headers=H))
    assert evs[0]["route"] != "ask_claude"


def test_the_switch_is_a_setting(make_services):
    c = TestClient(create_app(make_services(), TOKEN, scheduler=False))
    assert c.get("/api/settings", headers=H).json()["ask_my_claude"] is True
    assert c.put("/api/settings", json={"ask_my_claude": False}, headers=H).json()["ask_my_claude"] is False
