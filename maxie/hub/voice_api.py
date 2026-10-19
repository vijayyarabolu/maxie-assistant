"""Routes the Mac voice helper and Settings › Voice use: the panel's mic button, texts to speak, his voice
enrollment for the speaker check, and voice samples to hear before choosing.

The voice loop owns the microphone, so the hub only passes requests to it (`/api/voice/wake` is polled
twice a second) and keeps what it reports back. His voice prints live in `s.speaker_store` (no audio);
"Forget my voice" deletes the file and the loop notices on its next wake check.
"""

from __future__ import annotations

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


class SpeakerIn(BaseModel):
    enrolled: bool
    threshold: float | None = None
    message: str = ""


def register(app, s, api) -> None:
    requests = {"wake": False, "say": [], "enroll": False}
    speaker = {"message": "", "threshold": None, "enrolling": False}

    def store() -> Path:
        return Path(s.speaker_store)

    def speaker_status() -> dict:
        return {"enrolled": store().exists(), "threshold": speaker["threshold"], "message": speaker["message"],
                "enrolling": speaker["enrolling"]}

    @app.get("/api/voice/wake", dependencies=api)
    def voice_wake_get():
        """The voice loop polls this: a pending wake (listen now), texts to speak and an enrollment request;
        cleared once read."""
        out = {"wake": requests["wake"], "say": list(requests["say"]), "enroll": requests["enroll"]}
        requests.update(wake=False, say=[], enroll=False)
        return out

    @app.post("/api/voice/wake", dependencies=api)
    def voice_wake_post():
        requests["wake"] = True
        return {"ok": True}

    @app.post("/api/voice/say", dependencies=api)
    def voice_say(body: SayIn):
        requests["say"].append(body.text[:500])
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
        requests["enroll"] = True
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
