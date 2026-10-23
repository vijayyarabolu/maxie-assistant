"""a real Stop. "Pause Maxie" stops listening, watching and his own work (background tasks, the
proactive scheduler, warm-ups) until he resumes; resuming puts back what he had on."""

import asyncio

from test_settings_api import Deps, H, client

from maxie import background
from maxie.proactive import run_scheduler


def test_pause_stops_listening_and_watching_and_resume_puts_them_back(make_services):
    # Arrange
    c, s = client(make_services, Deps())
    assert c.get("/api/pause", headers=H).json() == {"paused": False}

    # Act
    paused = c.post("/api/pause", json={"on": True}, headers=H).json()

    # Assert
    assert paused == {"paused": True}
    assert s.settings.paused and s.settings.voice_muted and not s.watch.watching
    assert s.events.current()["state"] == "closed", "Maxie closes his eyes"

    c.post("/api/pause", json={"on": False}, headers=H)
    assert not s.settings.paused and not s.settings.voice_muted and s.watch.watching


def test_resume_keeps_what_he_had_switched_off_before_the_pause(make_services):
    c, s = client(make_services, Deps())
    c.put("/api/settings", json={"voice_muted": True}, headers=H)
    c.post("/api/pause", json={"on": True}, headers=H)
    c.post("/api/pause", json={"on": False}, headers=H)
    assert s.settings.voice_muted, "he had listening off before: it stays off"
    assert s.watch.watching


def test_pausing_twice_still_resumes_to_what_he_had(make_services):
    c, s = client(make_services, Deps())
    c.post("/api/pause", json={"on": True}, headers=H)
    c.post("/api/pause", json={"on": True}, headers=H)  # the second pause must not record "muted" as his choice
    c.post("/api/pause", json={"on": False}, headers=H)
    assert not s.settings.voice_muted


async def test_the_background_worker_waits_while_paused(make_services, monkeypatch):
    # Arrange: a queued task, Maxie paused
    monkeypatch.setattr(background, "IDLE_S", 0.01)
    s = make_services()
    s.background.add("summarise my week")
    ran = []

    async def run_next(agent):
        ran.append(1)
        return None

    monkeypatch.setattr(s.background, "run_next", run_next)

    # Act
    task = asyncio.create_task(background.work(s.background, s.agent, paused=lambda: True))
    await asyncio.sleep(0.1)
    task.cancel()

    # Assert
    assert ran == []


async def test_the_proactive_scheduler_skips_its_ticks_while_paused():
    ticks = []

    class P:
        def tick(self):
            ticks.append(1)

    task = asyncio.create_task(run_scheduler(P(), every=0.01, paused=lambda: True))
    await asyncio.sleep(0.1)
    task.cancel()
    assert ticks == []


def test_pausing_stops_the_task_that_is_running(make_services):
    # ⌥⇧P while she answers: the answer's work stops too, not only the listening after it
    c, s = client(make_services, Deps())
    before = s.kill.generation()
    c.post("/api/pause", json={"on": True}, headers=H)
    assert s.kill.generation() == before + 1
