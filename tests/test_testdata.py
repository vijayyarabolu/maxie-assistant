"""Test data never touches his memory: sandbox, live-folder guard, hub refusal, clear."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maxie import config, testdata
from maxie.db import DB
from maxie.hub.api import create_app

TOKEN = "t" * 32
H = {"Authorization": f"Bearer {TOKEN}"}
REPO_DATA = Path(config.__file__).resolve().parents[1] / "data"


def test_sandboxed_processes_cannot_open_the_live_folder(monkeypatch):
    monkeypatch.setenv("MAXIE_DATA", str(REPO_DATA))
    with pytest.raises(RuntimeError, match="never use the live data folder"):
        config.data_dir()


def test_sandbox_moves_the_process_to_a_temp_folder(monkeypatch):
    tmp = testdata.sandbox()
    assert not testdata.is_live(tmp) and config.data_dir() == tmp
    assert testdata.is_live(REPO_DATA) and testdata.is_live(REPO_DATA / "hub.db")


@pytest.mark.parametrize("module", ["evals_useful", "evals_live"])
def test_evals_build_their_services_outside_the_live_folder(module, monkeypatch):
    import importlib

    import maxie.hub.services as services

    mod = importlib.import_module(f"maxie.{module}")
    seen = {}

    class Built(Exception):
        pass

    def fake_build(*a, **kw):
        seen["data"], seen["db"] = config.data_dir(), kw.get("db_path")
        raise Built

    monkeypatch.setattr(config, "secret", lambda name: "sk-test")
    monkeypatch.setattr(services, "build", fake_build)
    monkeypatch.setenv("MAXIE_SANDBOX", "0")  # even unsandboxed to start with, the eval sandboxes itself
    monkeypatch.setenv("MAXIE_DATA", str(REPO_DATA))
    with pytest.raises(Built):
        mod.main([])
    assert seen["db"] == ":memory:" and not testdata.is_live(seen["data"])


def seeded() -> DB:
    db = DB()
    db.script("""
      CREATE TABLE messages(id INTEGER PRIMARY KEY, conv TEXT, ts REAL, role TEXT, text TEXT, meta TEXT);
      CREATE TABLE tasks(id INTEGER PRIMARY KEY, ts REAL, text TEXT, device TEXT);
      CREATE TABLE turns(id INTEGER PRIMARY KEY, ts REAL, text TEXT);
      CREATE TABLE route_log(id INTEGER PRIMARY KEY, ts REAL, text TEXT);
      CREATE TABLE memory(id INTEGER PRIMARY KEY, topic TEXT, text TEXT, created REAL, source TEXT);
      CREATE TABLE wins(id INTEGER PRIMARY KEY, ts REAL, kind TEXT, text TEXT);
      CREATE TABLE confirmations(id TEXT, ts REAL, tool TEXT, args TEXT, summary TEXT, after_untrusted INT, status TEXT);
      CREATE TABLE alerts_sent(key TEXT PRIMARY KEY, ts REAL);
      INSERT INTO messages(conv,ts,role,text) VALUES ('bench',10,'user','open finder'),('bench',11,'assistant','Opening Finder.'),
        ('mac',12,'user','what time is it'),('phone',50,'user','set a timer'),('phone',500,'user','call mom');
      INSERT INTO tasks(ts,text,device) VALUES (10,'open finder','bench'),(12,'what time is it','mac');
      INSERT INTO route_log(ts,text) VALUES (10,'open finder'),(12,'what time is it');
      INSERT INTO turns(ts,text) VALUES (10,'open finder'),(12,'what time is it');
      INSERT INTO memory(topic,text) VALUES ('people','Jordan likes sushi'),('me','I live in Springfield');
      INSERT INTO wins(ts,kind,text) VALUES (1,'reply','recruiter from Nimbus'),(2,'interview','interview at Vandelay');
      INSERT INTO confirmations VALUES ('c1',1,'send_email','{"to":"jordan@example.com"}','Send email to jordan@example.com',0,'denied');
      INSERT INTO alerts_sent VALUES ('mail:j1',1),('mail:18f2a9c0d1e2b3a4',2);
    """)
    return db


def test_clear_removes_only_test_leftovers():
    db = seeded()
    removed = testdata.clear(db)
    assert [r["text"] for r in db.all("SELECT text FROM messages ORDER BY id")] == ["what time is it", "set a timer", "call mom"]
    assert [r["text"] for r in db.all("SELECT text FROM memory")] == ["I live in Springfield"]
    assert [r["text"] for r in db.all("SELECT text FROM wins")] == ["interview at Vandelay"]
    assert [r["key"] for r in db.all("SELECT key FROM alerts_sent")] == ["mail:18f2a9c0d1e2b3a4"]
    assert [r["text"] for r in db.all("SELECT text FROM route_log")] == ["what time is it"]
    assert db.all("SELECT * FROM confirmations") == []
    assert removed == {"messages": 2, "tasks": 1, "route_log": 1, "turns": 1, "memory": 1, "wins": 1,
                       "confirmations": 1, "alerts_sent": 1}
    assert testdata.clear(db) == {}


def test_clear_with_a_window_takes_everything_in_it_from_those_conversations():
    db = seeded()
    testdata.clear(db, testdata.Window(40, 60, ("phone",)))
    assert [r["text"] for r in db.all("SELECT text FROM messages ORDER BY id")] == ["what time is it", "call mom"]


def test_the_live_hub_refuses_test_traffic_and_the_fake_hub_takes_it(make_services, monkeypatch, tmp_path):
    s = make_services()
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    assert c.post("/api/ask", json={"text": "what time is it"}, headers={**H, "X-Claude-Test": "1"}).status_code == 200
    monkeypatch.setattr(s.db, "path", str(REPO_DATA / "hub.db"))  # pretend this is the live database
    monkeypatch.delenv("MAXIE_FAKES", raising=False)
    live = TestClient(create_app(s, TOKEN, scheduler=False))
    assert live.post("/api/ask", json={"text": "what time is it"}, headers={**H, "X-Claude-Test": "1"}).status_code == 403
    assert live.post("/api/chat", json={"text": "hi", "conversation": "bench"}, headers=H).status_code == 403
    assert live.post("/api/ask", json={"text": "what time is it"}, headers=H).status_code == 200


def test_clear_endpoint_reports_counts(make_services):
    s = make_services()
    s.memory.remember("Jordan likes sushi", "people")
    r = TestClient(create_app(s, TOKEN, scheduler=False)).post("/api/testdata/clear", headers=H).json()
    assert r["removed"].get("memory") == 1 and r["total"] >= 1 and s.memory.all() == []


def test_clear_takes_what_fake_mail_became_and_leaves_real_names(tmp_path):
    from maxie.db import DB
    from maxie.testdata import clear
    db = DB(tmp_path / "h.db")
    db.script("""CREATE TABLE held(id INTEGER PRIMARY KEY, title TEXT, body TEXT, tag TEXT, mood TEXT);
                 CREATE TABLE tasks(id INTEGER PRIMARY KEY, ts REAL, text TEXT, device TEXT, reply TEXT);
                 CREATE TABLE messages(id INTEGER PRIMARY KEY, ts REAL, conv TEXT, role TEXT, text TEXT, source TEXT);""")
    db.execute("INSERT INTO held(title, body) VALUES ('Morning brief', 'Landlord Co about Rent receipt for October')")
    db.execute("INSERT INTO held(title, body) VALUES ('Morning brief', 'Morgan: Lunch on Friday')")  # a real Morgan stays
    db.execute("INSERT INTO tasks(text, reply) VALUES ('brief me', '3 important emails: Mallory about Urgent invoice')")
    db.execute("INSERT INTO messages(conv, role, text) VALUES ('mac', 'assistant', 'Recruiter mail from Nimbus')")
    db.execute("INSERT INTO messages(conv, role, text) VALUES ('mac', 'user', 'what did Nimbus say')")  # his words stay
    assert clear(db) == {"held": 1, "tasks": 1, "messages": 1}
    assert db.one("SELECT body FROM held")["body"] == "Morgan: Lunch on Friday"


def test_redact_report_cleans_replies_and_notes_but_keeps_scores():
    report = {"n": 1, "passed": 1, "score": 1.0, "results": [
        {"id": 1, "pass": True, "text": "Morgan about Coffee next week. AcmeAI wrote.", "why": "names Nimbus", "tools": ["x"]}]}
    out = testdata.redact_report(report)
    r = out["results"][0]
    assert "AcmeAI" not in r["text"] and "Coffee next week" not in r["text"] and "Nimbus" not in r["why"]
    assert (out["score"], r["pass"], r["tools"]) == (1.0, True, ["x"])
    assert "AcmeAI" in report["results"][0]["text"]  # the input is not changed
