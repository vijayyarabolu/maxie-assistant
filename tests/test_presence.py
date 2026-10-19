"""Maxie sleeps when HE sleeps, not by the clock. Presence comes from real signals the notch app
reports (lock, displays, input idle, media) and from what he does (typing, the wake word, the phone, "goodnight
Max"). With no signal at all, the old quiet hours still apply."""

from datetime import datetime

from fastapi.testclient import TestClient

from maxie.hub.api import create_app
from maxie.presence import goodnight_said
from tests.conftest import TZ

TOKEN = "t0ken"
H = {"Authorization": f"Bearer {TOKEN}"}


def at(clock, h, m=0, day=16):
    clock.set(datetime(2026, 10, day, h, m, tzinfo=TZ))


def report(s, idle_s=0, locked=False, displays_asleep=False, media=False):
    s.presence.report(locked=locked, displays_asleep=displays_asleep, idle_s=idle_s, media=media)


def test_awake_at_1_42_am_while_he_types(make_services, clock):
    s = make_services()
    at(clock, 1, 42)
    report(s, idle_s=4)
    assert not s.presence.asleep()
    assert s.events.current()["state"] == "idle", "the bug: he was talking and Maxie was asleep"


def test_2_am_locked_for_30_minutes_is_asleep(make_services, clock):
    s = make_services()
    at(clock, 1, 30)
    report(s, idle_s=0, locked=True)
    at(clock, 2, 0)
    assert s.presence.asleep()
    assert s.events.current()["state"] == "sleep"


def test_a_movie_at_2_am_keeps_him_awake(make_services, clock):
    s = make_services()
    at(clock, 2, 0)
    report(s, idle_s=40 * 60, media=True)
    assert not s.presence.asleep()


def test_idle_in_the_afternoon_is_away_not_asleep(make_services, clock):
    s = make_services()
    at(clock, 15, 0)
    report(s, idle_s=3600)
    assert not s.presence.asleep()
    assert s.events.current()["state"] == "idle"


def test_no_input_for_30_minutes_at_night_is_asleep(make_services, clock):
    s = make_services()
    at(clock, 23, 50, day=15)
    report(s, idle_s=31 * 60)
    assert s.presence.asleep()


def test_goodnight_max_sleeps_at_once(make_services, clock):
    s = make_services()
    at(clock, 22, 15, day=15)
    report(s, idle_s=2)
    s.presence.heard("goodnight Max")
    assert s.presence.asleep()
    assert s.events.current()["state"] in ("closed", "sleep"), "eyes close first, then sleep"


def test_goodnight_phrases():
    for t in ("goodnight Max", "Good night, Maxie", "gn max", "I'm going to sleep", "I am going to bed now", "night night"):
        assert goodnight_said(t), t
    for t in ("what's a good night out in Austin", "remind me to sleep at 11", "how did I sleep"):
        assert not goodnight_said(t), t


def test_any_input_wakes_him_and_delivers_what_was_held(make_services, clock):
    s = make_services()
    at(clock, 1, 0)
    report(s, locked=True)
    assert s.presence.asleep()
    s.proactive.check_job_inbox()  # alerts while he sleeps are held
    assert s.fakes["notifier"].sent == []
    at(clock, 1, 20)
    report(s, idle_s=1)  # he's back at the Mac in the middle of the night
    assert not s.presence.asleep()
    assert len(s.fakes["notifier"].sent) == 3, "held pushes go out when he WAKES, not at 07:00"
    assert s.events.current()["state"] == "stretch", "one stretch on waking, then idle"


def test_the_phone_counts_as_awake(make_services, clock):
    s = make_services()
    at(clock, 2, 0)
    report(s, locked=True)
    assert s.presence.asleep()
    s.presence.activity("phone")
    assert not s.presence.asleep()


def test_without_any_signal_the_old_quiet_hours_apply(make_services, clock):
    s = make_services()
    at(clock, 23, 30, day=15)
    assert s.presence.asleep()  # nothing reported: 23:00-07:00 as before
    at(clock, 15, 0)
    assert not s.presence.asleep()


def test_a_stale_report_falls_back_to_quiet_hours(make_services, clock):
    s = make_services()
    at(clock, 22, 0, day=15)
    report(s, idle_s=1)
    at(clock, 23, 30, day=15)  # the app stopped reporting 90 minutes ago
    assert s.presence.asleep()


def test_fixed_quiet_hours_is_the_old_behaviour(make_services, clock):
    s = make_services()
    s.settings.update({"sleep_mode": "fixed"})
    at(clock, 1, 42)
    report(s, idle_s=4)
    assert s.presence.asleep()


def test_sleep_and_wake_are_logged_and_the_brief_can_say_so(make_services, clock):
    s = make_services()
    at(clock, 2, 5)
    report(s, locked=True)
    at(clock, 9, 40)
    report(s, idle_s=0)
    assert [r["kind"] for r in s.presence.log()] == ["sleep", "wake"]
    sections = {x.name: x for x in s.proactive.brief().sections}
    assert "Sleep" not in sections, "only if he turns it on"
    s.settings.update({"brief_sleep": True})
    sections = {x.name: x for x in s.proactive.brief().sections}
    assert sections["Sleep"].lines == ["Slept 2:05 to 9:40"]


def test_the_notch_app_reports_over_the_api(make_services, clock):
    s = make_services()
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    at(clock, 2, 0)
    r = c.post("/api/presence", json={"locked": True, "displays_asleep": False, "idle_s": 1900, "media": False}, headers=H)
    assert r.status_code == 200 and r.json()["asleep"] is True
    assert c.post("/api/presence", json={"locked": "yes"}, headers=H).status_code == 422
    assert c.put("/api/settings", json={"sleep_mode": "whenever"}, headers=H).status_code == 400


def test_talking_to_maxie_counts_as_awake(make_services, clock):
    s = make_services()
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    at(clock, 2, 0)
    report(s, locked=True)
    assert s.presence.asleep()
    c.post("/api/chat", json={"text": "what time is it", "conversation": "phone", "device": "phone"}, headers=H)
    assert not s.presence.asleep()


def test_settings_maxie_chooses_how_he_sleeps(make_services):
    s = make_services()
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    got = c.get("/api/maxie", headers=H).json()
    assert got["sleep_mode"] == "presence" and got["brief_sleep"] is False
    assert got["options"]["sleep_mode"] == ["presence", "fixed"]
    assert c.put("/api/maxie", json={"sleep_mode": "fixed", "brief_sleep": True}, headers=H).status_code == 200
    assert (s.settings.sleep_mode, s.settings.brief_sleep) == ("fixed", True)
    assert c.put("/api/maxie", json={"sleep_mode": "whenever"}, headers=H).status_code == 400
    assert c.put("/api/maxie", json={"brief_sleep": "yes"}, headers=H).status_code == 400
    assert s.settings.sleep_mode == "fixed", "a bad PUT changes nothing"


def test_the_phone_coming_to_the_front_wakes_him(make_services, clock):
    s = make_services()
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    at(clock, 2, 0)
    report(s, locked=True)
    r = c.post("/api/presence/active", json={"source": "phone"}, headers=H)
    assert r.status_code == 200 and r.json()["asleep"] is False
    assert c.post("/api/presence/active", json={"source": "toaster"}, headers=H).status_code == 422
    assert [x["kind"] for x in c.get("/api/presence", headers=H).json()["log"]] == ["sleep", "wake"]


def test_late_night_whisper_stays_on_the_clock():
    from maxie.voice.loop import quiet_now
    assert quiet_now({"timezone": "America/Chicago"}, datetime(2026, 10, 16, 1, 42, tzinfo=TZ))


def test_a_last_tap_after_goodnight_does_not_wake_him(make_services, clock):
    s = make_services()
    at(clock, 23, 0, day=15)
    report(s, idle_s=1)
    s.presence.heard("goodnight Max")
    at(clock, 23, 1, day=15)
    report(s, idle_s=5)  # closing the lid, one last tap
    assert s.presence.asleep()
    at(clock, 23, 10, day=15)
    report(s, idle_s=2)  # still typing ten minutes later: he's up after all
    assert not s.presence.asleep()


def test_the_clocks_quiet_hours_are_not_logged_as_his_sleep(make_services, clock):
    s = make_services()
    at(clock, 23, 30, day=15)
    s.proactive.tick()  # no Mac signal: asleep by the clock
    assert s.presence.asleep()
    at(clock, 15, 0)
    s.proactive.tick()
    assert s.presence.log() == []


def test_goodnight_in_the_chat_answers_first_then_closes_his_eyes(make_services, clock):
    s = make_services()
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    at(clock, 23, 0, day=15)
    report(s, idle_s=1)
    c.post("/api/chat", json={"text": "goodnight Max", "conversation": "mac", "device": "mac"}, headers=H)
    assert s.presence.asleep()
    assert s.events.current()["state"] == "closed"


def log_nights(s, clock, nights=6, sleep=(1, 30), wake=(9, 30)):
    for d in range(nights):
        day = 8 + d
        s.db.execute("INSERT INTO presence_log VALUES (?,?)", (datetime(2026, 10, day, *sleep, tzinfo=TZ).timestamp(), "sleep"))
        s.db.execute("INSERT INTO presence_log VALUES (?,?)", (datetime(2026, 10, day, *wake, tzinfo=TZ).timestamp(), "wake"))


def test_his_real_bedtime_is_learned_from_the_log(make_services, clock):
    s = make_services()
    log_nights(s, clock)
    at(clock, 23, 0, day=15)
    assert s.presence.usual() == ("1:30", "9:30")
    report(s, locked=True)
    assert not s.presence.asleep(), "he sleeps around 1:30: locked at 11 PM is away, not asleep"
    at(clock, 23, 45, day=15)
    report(s, locked=True)
    assert s.presence.asleep(), "within two hours of his usual bedtime"


def test_too_few_nights_keep_the_default_night(make_services, clock):
    s = make_services()
    log_nights(s, clock, nights=3)
    at(clock, 22, 0, day=15)
    assert s.presence.usual() is None
    report(s, locked=True)
    assert s.presence.asleep()


def test_settings_maxie_shows_his_usual_night(make_services, clock):
    s = make_services()
    log_nights(s, clock)
    at(clock, 12, 0, day=15)
    c = TestClient(create_app(s, TOKEN, scheduler=False))
    assert c.get("/api/maxie", headers=H).json()["usual_sleep"] == "You usually sleep around 1:30 and wake around 9:30."
