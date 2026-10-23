from maxie import evals
from maxie.db import DB
from maxie.personality import LIFELINE, REACH_OUT, Wellbeing
from maxie.push import WebPush


def test_offline_evals_meet_baseline():
    import json
    db = DB()
    from maxie.budget import Budget
    report = evals.run(evals.build_router("offline", db, Budget(db)), Budget(db), evals.load())
    assert report["n"] == 120
    assert report["accuracy"] >= json.loads(evals.BASELINE.read_text())["offline"]
    # A wrong direct action is the dangerous kind of miss: there must be none.
    assert not [f for f in report["failures"] if f["got"][1] and f["got"][1] != f["expected"][1]]


def test_wellbeing(clock):
    w = Wellbeing(DB(), clock=clock)
    assert w.check("some days I want to die") == LIFELINE
    assert w.check("feeling hopeless") is None
    clock.t += 86400
    assert w.check("I'm so tired of this") is None
    clock.t += 86400
    assert w.check("struggling again") == REACH_OUT
    assert w.check("still struggling") is None  # once a day at most


def test_push_drops_dead_subscriptions():
    codes = {"https://a": 201, "https://b": 410}
    sent = []
    p = WebPush(DB(), None, None, send=lambda sub, payload: sent.append(sub["endpoint"]) or codes[sub["endpoint"]])
    p.subscribe({"endpoint": "https://a"})
    p.subscribe({"endpoint": "https://b"})
    p.push("t", "b")
    assert sorted(sent) == ["https://a", "https://b"]
    assert [s["endpoint"] for s in p.subscriptions()] == ["https://a"]


async def test_self_harm_message_always_shows_988(make_services):
    s = make_services()
    events = [e async for e in s.agent.handle("I want to kill myself")]
    assert "988" in "".join(e.get("text", "") for e in events)


def test_the_db_reads_whole_rows_while_another_thread_writes():
    # the early answer reads the budget while the router thread logs its route. Rows were fetched after
    # the lock was let go, so a read on one thread could come back empty or NULL mid-write on another.
    import threading

    db = DB()
    db.script("CREATE TABLE usage(cost REAL); CREATE TABLE log(t TEXT)")
    db.execute("INSERT INTO usage VALUES (1.5)")
    stop, bad = threading.Event(), []

    def write():  # the router thread: the same budget check, then its log line
        while not stop.is_set():
            db.one("SELECT COALESCE(SUM(cost),0) AS s FROM usage")
            db.execute("INSERT INTO log VALUES ('x')")

    t = threading.Thread(target=write)
    t.start()
    try:
        for _ in range(3000):
            row = db.one("SELECT COALESCE(SUM(cost),0) AS s FROM usage")
            if row is None or row["s"] != 1.5:
                bad.append(row)
    finally:
        stop.set()
        t.join()
    assert bad == []
