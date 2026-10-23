"""Routes the Mac voice helper and Settings › Voice use: the panel's mic button, texts to speak, his voice
enrollment for the speaker check, and voice samples to hear before choosing.

The voice loop owns the microphone, so the hub only passes requests to it (`/api/voice/wake` is polled
twice a second) and keeps what it reports back. His voice prints live in `s.speaker_store` (no audio);
"Forget my voice" deletes the file and the loop notices on its next wake check.

never just silence. The loop's notices ("Didn't catch that", "That didn't sound like you") go to the
notch as `voice_notice` events, and the poll doubles as a heartbeat: when it stops for ALIVE_S the notch hears
`{"type": "voice", "alive": false}` ("Voice service stopped").
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi import HTTPException, Response
from pydantic import BaseModel

from ..config import data_dir
from ..voice.preview import VoicePreview


class SayIn(BaseModel):
    text: str


class PreviewIn(BaseModel):
    voice: str
    speed: float = 1.05


class NoticeIn(BaseModel):
    kind: str
    text: str = ""


class StatusIn(BaseModel):
    mic: str = "ok"
    last_wake: dict | None = None


NOTICE_KINDS = frozenset({"not_your_voice", "not_enrolled", "didnt_catch", "mic_silent"})
ALIVE_S = 5.0        # this long without a poll means the voice helper stopped
LONG_POLL_S = 4.0    # a poll waits up to this long for something to do (under ALIVE_S: it is stamped each end)
WATCH_EVERY_S = 2.0
TEST_FIELDS = ("heard", "form", "woke", "score", "threshold", "why", "ms")
now = time.monotonic  # tests swap it


class SpeakerIn(BaseModel):
    enrolled: bool
    threshold: float | None = None
    message: str = ""


def register(app, s, api) -> None:
    requests = {"wake": False, "say": [], "enroll": False, "test": False}
    pending = threading.Event()  # something is waiting for the loop: a long poll answers at once
    lock = threading.Lock()
    wake_test = {"state": "idle", "result": None}
    speaker = {"message": "", "threshold": None, "enrolling": False}
    helper = {"polled": None, "mic": "ok", "last_wake": None, "alive": None, "watching": False, "held": 0}

    def alive() -> bool:
        """A poll in the last ALIVE_S, or one the hub is holding right now (it is stamped only at its two ends)."""
        return helper["held"] > 0 or (helper["polled"] is not None and now() - helper["polled"] <= ALIVE_S)

    def check() -> None:
        """Tell the notch when the voice helper stops answering, or comes back (once per change)."""
        is_alive = alive()
        if helper["alive"] is not None and is_alive != helper["alive"]:
            s.events.publish({"type": "voice", "alive": is_alive, "mic": helper["mic"]})
        helper["alive"] = is_alive

    def watch() -> None:
        while True:
            time.sleep(WATCH_EVERY_S)
            try:
                check()
            except Exception as e:  # the watcher must outlive one bad check  # noqa: BLE001
                print(f"voice watch: {e}", flush=True)

    app.state.voice_check = check

    def store() -> Path:
        return Path(s.speaker_store)

    def speaker_status() -> dict:
        return {"enrolled": store().exists(), "threshold": speaker["threshold"], "message": speaker["message"],
                "enrolling": speaker["enrolling"]}

    def ask(**kw) -> None:
        with lock:
            for k, v in kw.items():
                requests[k] = requests[k] + [v] if k == "say" else v
        pending.set()

    @app.get("/api/voice/wake", dependencies=api)
    def voice_wake_get(wait: float = 0.0):
        """The voice loop polls this: a pending wake (listen now), texts to speak and an enrollment request;
        cleared once read. With `wait`, it holds the poll until one comes (up to LONG_POLL_S): the mic button
        reaches the loop at once and an idle helper asks every few seconds, not twice a second."""
        helper["polled"] = now()
        if wait > 0:
            with lock:
                helper["held"] += 1
            try:
                pending.wait(min(wait, LONG_POLL_S))
            finally:
                with lock:
                    helper["held"] -= 1
        with lock:
            out = {"wake": requests["wake"], "say": list(requests["say"]), "enroll": requests["enroll"],
                   "test": requests["test"]}
            requests.update(wake=False, say=[], enroll=False, test=False)
            pending.clear()
        helper["polled"] = now()
        if not helper["watching"]:  # a voice helper exists: watch it from now on
            helper["watching"] = True
            threading.Thread(target=watch, daemon=True, name="voice-watch").start()
        return out

    @app.post("/api/voice/notice", dependencies=api)
    def voice_notice(body: NoticeIn):
        """The loop heard something and won't act on it: the notch says why, in words."""
        if body.kind not in NOTICE_KINDS:
            raise HTTPException(400, "unknown notice")
        s.events.publish({"type": "voice_notice", "kind": body.kind, "text": body.text[:120]})
        return {"ok": True}

    @app.post("/api/voice/test", dependencies=api)
    def voice_test_start():
        """Settings › Voice › "Test wake word": the loop listens for one wake word and says what it made of it."""
        wake_test.update(state="listening", result=None)
        ask(test=True)
        return wake_test

    @app.post("/api/voice/test/result", dependencies=api)
    def voice_test_result(body: dict):
        wake_test.update(state="done", result={k: body.get(k) for k in TEST_FIELDS})
        return {"ok": True}

    @app.get("/api/voice/test", dependencies=api)
    def voice_test_get():
        return wake_test

    @app.post("/api/voice/status", dependencies=api)
    def voice_status_report(body: StatusIn):
        helper.update(mic="silent" if body.mic == "silent" else "ok", last_wake=body.last_wake)
        return {"ok": True}

    @app.get("/api/voice/status", dependencies=api)
    def voice_status():
        """Settings › Voice and the notch: is the helper running, does the mic send sound, the last wake and why."""
        seen = helper["polled"]
        return {"alive": alive(), "seen_s_ago": None if seen is None else round(now() - seen, 1),
                "mic": helper["mic"], "last_wake": helper["last_wake"], "muted": bool(s.settings.voice_muted)}

    @app.post("/api/voice/wake", dependencies=api)
    def voice_wake_post():
        ask(wake=True)
        return {"ok": True}

    @app.post("/api/voice/say", dependencies=api)
    def voice_say(body: SayIn):
        ask(say=body.text[:500])
        return {"ok": True}

    @app.post("/api/voice/preview", dependencies=api)
    def voice_preview(body: PreviewIn):
        """Settings › Maxie: "Hi there, I'm Maxie." in that voice and speed, as a WAV the app plays."""
        if getattr(s, "voice_preview", None) is None:
            s.voice_preview = VoicePreview(data_dir() / "voice-preview", name=s.settings.user_name)
        try:
            return Response(s.voice_preview.wav(body.voice, body.speed), media_type="audio/wav")
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/voice/enroll", dependencies=api)
    def voice_enroll():
        """Settings › Voice › "Teach Maxie your voice": the loop reads five phrases out and records him."""
        ask(enroll=True)
        speaker.update(enrolling=True, message="Listen to Maxie and repeat each phrase.")
        s.audit.log("user", "voice_enroll", {}, "Settings › Voice", "requested")
        return {"ok": True}

    @app.post("/api/voice/speaker", dependencies=api)
    def voice_speaker_report(body: SpeakerIn):
        speaker.update(enrolling=False, message=body.message[:200], threshold=body.threshold)
        return speaker_status()

    @app.get("/api/voice/speaker", dependencies=api)
    def voice_speaker_get():
        return speaker_status()

    @app.post("/api/voice/speaker/forget", dependencies=api)
    def voice_speaker_forget():
        store().unlink(missing_ok=True)
        speaker.update(message="Your voice was forgotten.", threshold=None, enrolling=False)
        s.audit.log("user", "voice_forget", {}, "Settings › Voice", "deleted")
        return speaker_status()
