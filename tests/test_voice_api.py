"""Hub routes for voice: notices for the notch, the helper's status (alive, mic, last wake)."""

from test_settings_api import Deps, H, client

from maxie.hub import voice_api


def _published(s):
    seen = []
    real = s.events.publish
    s.events.publish = lambda ev: (seen.append(ev), real(ev))
    return seen


def test_a_notice_from_the_voice_loop_reaches_the_notch(make_services):
    # Arrange
    c, s = client(make_services, Deps())
    seen = _published(s)

    # Act
    r = c.post("/api/voice/notice", json={"kind": "not_your_voice", "text": "That didn't sound like you."}, headers=H)

    # Assert
    assert r.status_code == 200
    assert seen == [{"type": "voice_notice", "kind": "not_your_voice", "text": "That didn't sound like you."}]


def test_unknown_notice_kinds_are_refused(make_services):
    c, _ = client(make_services, Deps())
    assert c.post("/api/voice/notice", json={"kind": "<script>", "text": "x"}, headers=H).status_code == 400


def test_status_shows_alive_mic_and_the_last_wake(make_services, monkeypatch):
    # Arrange: the helper polls, then reports the last wake decision
    t = [1000.0]
    monkeypatch.setattr(voice_api, "now", lambda: t[0])
    c, s = client(make_services, Deps())
    assert c.get("/api/voice/status", headers=H).json()["alive"] is False  # never seen
    c.get("/api/voice/wake", headers=H)
    wake = {"heard": "Maxie", "form": "maxie", "score": 0.47, "threshold": 0.42, "woke": True, "reason": "his voice"}
    c.post("/api/voice/status", json={"mic": "ok", "last_wake": wake}, headers=H)

    # Act
    live = c.get("/api/voice/status", headers=H).json()
    t[0] += voice_api.ALIVE_S + 1
    later = c.get("/api/voice/status", headers=H).json()

    # Assert
    assert live["alive"] is True and live["mic"] == "ok" and live["last_wake"]["score"] == 0.47
    assert later["alive"] is False


def test_the_notch_hears_when_the_voice_helper_stops_and_comes_back(make_services, monkeypatch):
    # Arrange
    t = [1000.0]
    monkeypatch.setattr(voice_api, "now", lambda: t[0])
    c, s = client(make_services, Deps())
    seen = _published(s)
    c.get("/api/voice/wake", headers=H)
    check = c.app.state.voice_check

    # Act: quiet past ALIVE_S, then it polls again
    check()
    t[0] += voice_api.ALIVE_S + 1
    check()
    check()
    c.get("/api/voice/wake", headers=H)
    check()

    # Assert: one event per change, not per check
    assert [e["alive"] for e in seen if e["type"] == "voice"] == [False, True]


def test_wake_word_test_goes_to_the_loop_once_and_its_result_comes_back(make_services):
    # Arrange
    c, _ = client(make_services, Deps())

    # Act
    started = c.post("/api/voice/test", headers=H).json()
    polls = [c.get("/api/voice/wake", headers=H).json()["test"], c.get("/api/voice/wake", headers=H).json()["test"]]
    c.post("/api/voice/test/result", json={"heard": "Maxie.", "woke": True, "why": "Would wake.", "score": 0.5}, headers=H)
    done = c.get("/api/voice/test", headers=H).json()

    # Assert
    assert started["state"] == "listening" and polls == [True, False]
    assert done["state"] == "done" and done["result"]["heard"] == "Maxie."


def test_a_long_poll_answers_the_moment_the_mic_button_is_pressed(make_services):
    # the helper asked twice a second; it now waits on the hub, which answers as soon as there is something
    import threading
    import time

    # Arrange
    c, _ = client(make_services, Deps())
    got = {}

    def poll():
        t0 = time.perf_counter()
        got["body"] = c.get("/api/voice/wake?wait=3", headers=H).json()
        got["s"] = time.perf_counter() - t0

    t = threading.Thread(target=poll)
    t.start()
    time.sleep(0.3)

    # Act
    c.post("/api/voice/wake", headers=H)
    t.join(5)

    # Assert
    assert got["body"]["wake"] is True
    assert got["s"] < 1.5, "answered on the press, not at the end of the wait"


def test_a_long_poll_with_nothing_to_do_returns_empty_after_the_wait(make_services):
    import time
    c, _ = client(make_services, Deps())
    t0 = time.perf_counter()
    body = c.get("/api/voice/wake?wait=0.3", headers=H).json()
    assert body == {"wake": False, "say": [], "enroll": False, "test": False}
    assert time.perf_counter() - t0 >= 0.3


def test_a_long_poll_never_waits_past_what_keeps_the_helper_alive(make_services):
    assert voice_api.LONG_POLL_S < voice_api.ALIVE_S


def test_the_helper_is_alive_while_the_hub_holds_its_poll(make_services, monkeypatch):
    # a held poll stamps only when it starts and ends: a slow hub must not make a waiting helper look stopped
    import threading
    import time

    # Arrange
    t = [1000.0]
    monkeypatch.setattr(voice_api, "now", lambda: t[0])
    c, _ = client(make_services, Deps())
    poll = threading.Thread(target=lambda: c.get("/api/voice/wake?wait=1", headers=H))
    poll.start()
    time.sleep(0.3)

    # Act: the clock jumps past the alive window while the poll is held, then again after it ends
    t[0] += voice_api.ALIVE_S + 5
    held = c.get("/api/voice/status", headers=H).json()["alive"]
    poll.join(5)
    t[0] += voice_api.ALIVE_S + 5
    after = c.get("/api/voice/status", headers=H).json()["alive"]

    # Assert
    assert held is True and after is False
