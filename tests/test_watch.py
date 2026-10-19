"""watch and learn. On by default (his choice), private by design: only app switches, safe window
titles and who he messages land here, on the Mac, for 30 days. Habits become Memory and, now and then, a
suggestion. "Stop watching" closes Maxie's eyes until he says "watch again"."""

from datetime import datetime

from fastapi.testclient import TestClient

from maxie.hub.api import create_app
from maxie.watch import admit
from tests.conftest import TZ

TOKEN = "t0ken"
H = {"Authorization": f"Bearer {TOKEN}"}
JOBHUNT = {"kind": "app", "app": "com.google.Chrome.app.jobhunt", "name": "jobhunt"}
GMAIL = {"kind": "app", "app": "com.google.Chrome.app.gmail", "name": "Gmail"}


def at(clock, day, h, m=0):
    clock.set(datetime(2026, 10, day, h, m, tzinfo=TZ))


def seen(s, clock, day, h, m, *items):
    h, m = h + m // 60, m % 60
    at(clock, day, h, m)
    return s.watch.observe([dict(i) for i in items])


def a_week_of_mornings(s, clock, days=(12, 13, 14, 15, 16)):
    """Mon 12 to Fri 16 Oct: jobhunt then Gmail a little before or after 9."""
    for n, day in enumerate(days):
        seen(s, clock, day, 8, 55 + n * 2, JOBHUNT)
        seen(s, clock, day, 9, 1 + n, GMAIL)


# ---- what is never kept


def test_safe_observations_are_kept_as_is():
    o = admit({"kind": "title", "app": "com.microsoft.VSCode", "name": "Code", "detail": "watch.py, maxie"}, [])
    assert o is not None and (o.app, o.name, o.detail) == ("com.microsoft.VSCode", "Code", "watch.py, maxie")


def test_banking_health_and_password_apps_are_never_kept():
    for app, category in (("com.chase.sig.Chase", ""), ("com.1password.1password", ""), ("com.apple.Passwords", ""),
                          ("com.example.mybank", "public.app-category.finance"),
                          ("com.example.clinic", "public.app-category.healthcare-fitness"),
                          ("com.example.rx", "public.app-category.medical")):
        assert admit({"kind": "app", "app": app, "name": "X", "category": category}, []) is None, app


def test_browser_titles_are_dropped_because_private_windows_cannot_be_told_apart():
    o = admit({"kind": "title", "app": "com.apple.Safari", "name": "Safari", "detail": "flights to austin - Google Search"}, [])
    assert o is None
    o = admit({"kind": "app", "app": "com.google.Chrome", "name": "Google Chrome"}, [])
    assert o is not None and o.detail == "", "that he used a browser is fine; what he looked at is not"


def test_sensitive_titles_are_dropped():
    for title in ("Private Browsing", "New Incognito Tab", "Your verification code is 482913", "Chase - Sign in",
                  "MyChart - Test results", "password reset", "Card ending 4242"):
        assert admit({"kind": "title", "app": "com.apple.mail", "name": "Mail", "detail": title}, []) is None, title


def test_apps_he_excludes_are_never_kept():
    assert admit({"kind": "app", "app": "com.tinyspeck.slackmacgap", "name": "Slack"}, ["com.tinyspeck.slackmacgap"]) is None
    assert admit({"kind": "app", "app": "com.tinyspeck.slackmacgap", "name": "Slack"}, ["slack"]) is None, "by name too"


def test_junk_is_refused():
    assert admit({"kind": "keystrokes", "app": "x", "name": "X"}, []) is None
    assert admit({"kind": "app", "app": "", "name": ""}, []) is None
    o = admit({"kind": "title", "app": "com.apple.TextEdit", "name": "TextEdit", "detail": "notes " * 100}, [])
    assert o is not None and len(o.detail) <= 120


def test_nothing_is_kept_while_he_has_said_stop(make_services, clock):
    s = make_services()
    s.watch.set_watching(False)
    assert seen(s, clock, 16, 9, 0, JOBHUNT) == 0
    assert s.watch.count() == 0


def test_thirty_days_then_gone(make_services, clock):
    s = make_services()
    clock.set(datetime(2026, 9, 10, 9, tzinfo=TZ))
    s.watch.observe([JOBHUNT])
    at(clock, 16, 9)
    s.watch.observe([GMAIL])
    assert s.watch.count() == 1, "the September one is older than 30 days"


def test_forget_today(make_services, clock):
    s = make_services()
    seen(s, clock, 15, 9, 0, JOBHUNT)
    seen(s, clock, 16, 9, 0, GMAIL)
    seen(s, clock, 16, 10, 0, JOBHUNT)
    assert s.watch.forget_today() == 2
    assert s.watch.count() == 1


# ---- habits and suggestions


def test_a_week_of_mornings_becomes_a_habit_in_memory(make_services, clock):
    s = make_services()
    a_week_of_mornings(s, clock)
    habits = s.watch.learn()
    assert [h.text for h in habits] == ["You open jobhunt and Gmail every weekday morning at 9"]
    facts = [f for f in s.memory.all() if f["topic"] == "habits"]
    assert [f["text"] for f in facts] == ["You open jobhunt and Gmail every weekday morning at 9"]
    assert facts[0]["source"] == "watched"


def test_two_mornings_are_not_a_habit(make_services, clock):
    s = make_services()
    a_week_of_mornings(s, clock, days=(15, 16))
    assert s.watch.learn() == []


def test_scattered_times_are_not_a_habit(make_services, clock):
    s = make_services()
    for day, h in ((12, 8), (13, 11), (14, 15), (15, 19), (16, 22)):
        seen(s, clock, day, h, 0, JOBHUNT)
    assert s.watch.learn() == []


def test_the_suggestion_asks_and_yes_makes_it_a_routine(make_services, clock):
    s = make_services()
    a_week_of_mornings(s, clock)
    s.watch.learn()
    sug = s.watch.suggest()
    assert sug["text"] == "You open jobhunt and Gmail every weekday morning at 9: want me to do it?"
    assert s.watch.suggest() is None, "one at a time, and never the same one twice"
    s.watch.accept(sug["id"])
    opened = []
    s.events.publish = lambda ev: opened.append(ev) if ev.get("type") == "open_apps" else None
    clock.set(datetime(2026, 10, 19, 9, 0, tzinfo=TZ))  # Monday
    s.presence.activity("mac")
    s.watch.tick()
    s.watch.tick()
    assert opened == [{"type": "open_apps", "apps": ["com.google.Chrome.app.jobhunt", "com.google.Chrome.app.gmail"]}], \
        "once that morning, in the background"
    clock.set(datetime(2026, 10, 17, 9, 0, tzinfo=TZ))  # Saturday
    opened.clear()
    s.watch.tick()
    assert opened == []


def test_no_routine_while_he_sleeps(make_services, clock):
    s = make_services()
    a_week_of_mornings(s, clock)
    s.watch.learn()
    s.watch.accept(s.watch.suggest()["id"])
    opened = []
    s.events.publish = lambda ev: opened.append(ev) if ev.get("type") == "open_apps" else None
    clock.set(datetime(2026, 10, 19, 9, 0, tzinfo=TZ))
    s.presence.report(locked=True, displays_asleep=True, idle_s=3 * 3600, media=False)
    s.watch.tick()
    assert opened == []


# ---- eyes


def test_stop_watching_closes_his_eyes_until_watch_again(make_services):
    s = make_services()
    assert s.watch.watching and s.events.current()["state"] == "idle"
    reply = s.watch.answer("stop watching")
    assert reply and "eyes" in reply.lower()
    assert not s.watch.watching and not s.settings.watching
    assert s.events.current()["state"] == "closed"
    assert s.watch.answer("watch again")
    assert s.watch.watching and s.events.current()["state"] == "idle"


def test_no_to_a_suggestion_closes_his_eyes(make_services, clock):
    s = make_services()
    a_week_of_mornings(s, clock)
    s.watch.learn()
    s.watch.suggest()
    assert s.watch.answer("No") is not None
    assert not s.watch.watching
    assert s.watch.suggest() is None, "that one is dismissed"


def test_a_plain_no_otherwise_is_not_ours():
    from maxie.watch import command
    assert command("no") is None
    assert command("no thanks") is None
    assert command("stop watching") == "stop"
    assert command("Maxie, close your eyes") == "stop"
    assert command("don't watch me") == "stop"
    assert command("watch again") == "start"
    assert command("you can watch now") == "start"
    assert command("watch me once") is None, "that's the flow recorder"


def test_typed_stop_watching_goes_through_the_chat(make_services):
    s = make_services()
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    r = c.post("/api/chat", json={"text": "stop watching", "conversation": "mac", "device": "mac"}, headers=H)
    assert '"route": "watch"' in r.text
    assert not s.watch.watching
    assert s.events.current()["state"] == "closed"


# ---- the API


def test_the_api(make_services, clock):
    s = make_services()
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    got = c.get("/api/watch", headers=H).json()
    assert got["watching"] is True and got["excluded"] == [] and got["kept_days"] == 30
    assert c.put("/api/watch", json={"watching": False}, headers=H).json()["watching"] is False
    assert c.put("/api/watch", json={"watching": "no"}, headers=H).status_code == 422
    assert c.put("/api/watch", json={"watching": True, "excluded": ["com.tinyspeck.slackmacgap"]}, headers=H).status_code == 200
    at(clock, 16, 9)
    r = c.post("/api/watch/observe", json={"items": [JOBHUNT, {"kind": "app", "app": "com.tinyspeck.slackmacgap", "name": "Slack"}]},
               headers=H)
    assert r.json() == {"kept": 1}
    assert c.post("/api/watch/observe", json={"items": [{"kind": "app"}]}, headers=H).status_code == 422
    assert c.post("/api/watch/forget-today", headers=H).json() == {"forgot": 1}
    assert c.get("/api/watch", headers=H).json()["today"] == 0


def test_suggestions_over_the_api(make_services, clock):
    s = make_services()
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    a_week_of_mornings(s, clock)
    s.watch.learn()
    sug = s.watch.suggest()
    got = c.get("/api/watch", headers=H).json()
    assert got["habits"] == ["You open jobhunt and Gmail every weekday morning at 9"]
    assert got["suggestion"]["id"] == sug["id"]
    assert c.post(f"/api/watch/suggestions/{sug['id']}", json={"answer": "yes"}, headers=H).status_code == 200
    assert c.get("/api/watch", headers=H).json()["routines"] == ["Open jobhunt and Gmail on weekdays at 9:00."]
    assert c.post("/api/watch/suggestions/999", json={"answer": "yes"}, headers=H).status_code == 404


def test_yes_in_the_chat_takes_the_suggestion(make_services, clock):
    s = make_services()
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    a_week_of_mornings(s, clock)
    s.watch.learn()
    s.watch.suggest()
    r = c.post("/api/chat", json={"text": "yes", "conversation": "mac", "device": "mac"}, headers=H)
    assert "Open jobhunt and Gmail on weekdays at 9:00." in r.text
    assert s.watch.routines() == ["Open jobhunt and Gmail on weekdays at 9:00."]
    assert s.watch.watching, "a yes keeps his eyes open"


def test_the_suggestion_reaches_the_panel_in_the_day(make_services, clock):
    s = make_services()
    a_week_of_mornings(s, clock)
    sent = []
    s.events.publish = lambda ev: sent.append(ev)
    clock.set(datetime(2026, 10, 16, 11, 0, tzinfo=TZ))
    s.presence.activity("mac")
    s.watch.tick()
    kinds = [e["type"] for e in sent]
    assert "suggestion" in kinds and "transcript" in kinds
    sent.clear()
    s.watch.tick()
    assert [e for e in sent if e["type"] == "suggestion"] == [], "once a day at most"


def test_stop_watching_while_recording_a_flow_stops_the_recording(make_services):
    s = make_services()
    s.flows.watch(True)  # "watch me once"
    assert s.watch.answer("stop watching") is None, "the router's flow rule takes it"
    assert s.watch.watching
    s.flows.watch(False)
    assert s.watch.answer("stop watching") is not None
    assert not s.watch.watching
