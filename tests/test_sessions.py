"""Knows his Claude Code sessions, read-only: transcript parsing, status, the tool, the watcher."""

import json
import os
import time
from pathlib import Path

from maxie.hands import sessions
from maxie.proactive import FakeNotifier


def rec(kind, content, ts="2026-10-15T07:00:00Z", **extra):
    base = {"type": kind, "timestamp": ts, "cwd": "/home/alex/code/maxie", "gitBranch": "p4",
            "message": {"role": kind, "content": content}}
    return json.dumps({**base, **extra})


def write(path: Path, lines: list[str], age_s: float = 0.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    t = time.time() - age_s
    os.utime(path, (t, t))


def test_read_session_finished(tmp_path):
    f = tmp_path / "-home-alex-code-maxie" / "abc.jsonl"
    write(f, [json.dumps({"type": "ai-title", "aiTitle": "GOAL4 overnight"}),
              rec("user", "Do Prompts/GOAL4-product.md tonight"),
              rec("assistant", [{"type": "tool_use", "name": "Bash", "input": {}}]),
              rec("user", [{"type": "tool_result", "content": "ok"}]),
              rec("assistant", [{"type": "text", "text": "Done: the design pass is in.\nSee ux4."}], ts="2026-10-15T07:10:00Z")], age_s=600)
    s = sessions.read_session(f)
    assert s.project == "maxie" and s.title == "GOAL4 overnight" and s.branch == "p4"
    assert s.last_prompt == "Do Prompts/GOAL4-product.md tonight" and s.last_reply.startswith("Done: the design pass")
    assert s.status == "finished" and "finished" in s.line() and "GOAL4 overnight" in s.line()


def test_working_and_waiting(tmp_path):
    f = tmp_path / "-home-alex-code-jobhunt" / "x.jsonl"
    write(f, [rec("user", "fix the feed"), rec("assistant", [{"type": "tool_use", "name": "Bash", "input": {}}])], age_s=10)
    assert sessions.read_session(f).status == "working"
    write(f, [rec("user", "fix the feed"),
              rec("assistant", [{"type": "text", "text": "Which one?"}, {"type": "tool_use", "name": "AskUserQuestion", "input": {}}])], age_s=10)
    assert sessions.read_session(f).status == "waiting for you"
    assert sessions.read_session(tmp_path / "missing.jsonl") is None
    write(f, [rec("assistant", [{"type": "text", "text": "no user turn"}])])
    assert sessions.read_session(f) is None  # no prompt: not a session he started


def test_recent_sessions_newest_per_project_and_report(tmp_path):
    a = tmp_path / "-home-alex-code-maxie"
    write(a / "old.jsonl", [rec("user", "old one"), rec("assistant", [{"type": "text", "text": "old"}])], age_s=5000)
    write(a / "new.jsonl", [rec("user", "how about GOAL4"), rec("assistant", [{"type": "text", "text": "new"}])], age_s=100)
    jh = "/home/alex/code/jobhunt"
    write(tmp_path / "-home-alex-code-jobhunt" / "j.jsonl",
          [rec("user", "jobhunt feed", cwd=jh), rec("assistant", [{"type": "text", "text": "j"}], cwd=jh)], age_s=200)
    write(tmp_path / "-stale" / "s.jsonl", [rec("user", "ancient")], age_s=10 * 86400)
    got = sessions.recent_sessions(tmp_path)
    assert [s.project for s in got] == ["maxie", "jobhunt"] and got[0].last_prompt == "how about GOAL4"
    r = sessions.report("goal4", tmp_path)
    assert len(r["sessions"]) == 1 and r["sessions"][0]["project"] == "maxie" and "line" in r["sessions"][0]
    assert "git_log" in r  # the real repo: a few commits
    assert sessions.report("nothing-like-this", tmp_path) == {"sessions": []}


def test_watcher_pushes_on_finish_and_wait(tmp_path):
    f = tmp_path / "-home-alex-code-maxie" / "s.jsonl"
    write(f, [rec("user", "build it"), rec("assistant", [{"type": "tool_use", "name": "Bash", "input": {}}])], age_s=5)
    n = FakeNotifier()
    w = sessions.Watcher(n.push, tmp_path)
    assert w.tick() == [] and n.sent == []  # first sight: working, nothing to say
    write(f, [rec("user", "build it"), rec("assistant", [{"type": "text", "text": "All green, PR opened.\nDetails…"}])], age_s=400)
    assert w.tick() == ["maxie"] and n.sent[-1]["title"] == "Claude Code finished in maxie"
    assert n.sent[-1]["body"] == "All green, PR opened." and n.sent[-1]["mood"] == "proud"
    assert w.tick() == []  # said once
    write(f, [rec("user", "build it"), rec("assistant", [{"type": "text", "text": "Merge or not?"}, {"type": "tool_use", "name": "AskUserQuestion", "input": {}}])], age_s=5)
    assert w.tick() == ["maxie"] and n.sent[-1]["title"].startswith("Claude Code is waiting for you")


def test_tool_is_registered_read_only(make_services):
    s = make_services()
    t = s.registry.get("claude_code_sessions")
    assert t and t.kind == "read" and not t.risky


def test_a_growing_transcript_is_read_from_where_the_last_read_stopped(tmp_path, monkeypatch):
    # Arrange: the watcher re-parsed a 265 MB transcript every minute
    f = tmp_path / "-home-alex-code-maxie" / "big.jsonl"
    write(f, [rec("user", "do GOAL8"), rec("assistant", [{"type": "text", "text": "on it"}])])
    assert sessions.read_session(f).last_reply == "on it"
    parsed = []
    real = sessions.json.loads
    monkeypatch.setattr(sessions.json, "loads", lambda raw: parsed.append(raw) or real(raw))

    # Act: one more record, then a half-written one
    with f.open("a") as fh:
        fh.write(rec("assistant", [{"type": "text", "text": "done"}]) + "\n" + '{"type": "assist')
    s = sessions.read_session(f)

    # Assert
    assert s.last_reply == "done" and s.last_prompt == "do GOAL8", "what came before is kept"
    assert len(parsed) == 2, "only the new lines are parsed"
    with f.open("a") as fh:
        fh.write('ant", "message": {"content": [{"type": "text", "text": "finished"}]}}\n')
    assert sessions.read_session(f).last_reply == "finished", "the half-written line is read once it is whole"


def test_a_rewritten_transcript_is_read_again_from_the_start(tmp_path):
    f = tmp_path / "-home-alex-code-jobhunt" / "x.jsonl"
    write(f, [rec("user", "first"), rec("assistant", [{"type": "text", "text": "a long first answer " * 5}])])
    assert sessions.read_session(f).last_prompt == "first"
    write(f, [rec("user", "second"), rec("assistant", [{"type": "text", "text": "a much longer second answer " * 9}])])
    s = sessions.read_session(f)
    assert s.last_prompt == "second" and s.last_reply.startswith("a much longer")
