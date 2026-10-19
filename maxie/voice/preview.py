"""Hear a voice before choosing it: "Hi there, I'm Maxie." in any Kokoro voice and speed.

A clean 16-bit mono 24 kHz WAV with 10 ms fades (no click or chirp), cached on disk per (voice, speed, line).
Kokoro is loaded only when a sample isn't cached yet and unloaded after a short idle, so the hub doesn't keep a
300 MB model around for a settings screen.
"""

from __future__ import annotations

import hashlib
import io
import threading
import wave
from pathlib import Path

import numpy as np

from ..maxie_look import VOICE_SPEED, VOICES

RATE = 24000
FADE_S = 0.010
IDLE_S = 120.0
VOICE_IDS = frozenset(v["id"] for v in VOICES)


class KokoroEngine:
    """Kokoro (kokoro-onnx) on this Mac; Apple `say` when the model files are missing."""

    def __init__(self) -> None:
        self.kokoro = None

    def load(self) -> None:
        from .speech import KOKORO_DIR
        if self.kokoro is None and (KOKORO_DIR / "kokoro-v1.0.onnx").exists():
            from kokoro_onnx import Kokoro
            self.kokoro = Kokoro(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))

    def unload(self) -> None:
        self.kokoro = None

    def say(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, int]:
        if self.kokoro is None:
            from .speech import TTS
            return TTS._say(text)
        samples, rate = self.kokoro.create(text, voice=voice, speed=speed, lang="en-us")
        return np.asarray(samples, dtype=np.float32), int(rate)


def to_wav(x: np.ndarray, rate: int) -> bytes:
    """24 kHz mono int16 with linear 10 ms fades in and out."""
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    if rate != RATE and len(x):
        n = max(1, round(len(x) * RATE / rate))
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    fade = min(int(RATE * FADE_S), len(x) // 2)
    if fade:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        x = x.copy()
        x[:fade] *= ramp
        x[-fade:] *= ramp[::-1]
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class VoicePreview:
    def __init__(self, folder: Path, engine=None, name: str = "", idle_s: float = IDLE_S):
        self.folder, self.engine, self.idle_s = Path(folder), engine or KokoroEngine(), idle_s
        self.line = f"Hi {name or 'there'}, I'm Maxie."
        self._lock = threading.Lock()
        self._loaded = False
        self._timer: threading.Timer | None = None

    def wav(self, voice: str, speed: float) -> bytes:
        if voice not in VOICE_IDS:
            raise ValueError(f"unknown voice {voice!r}")
        lo, hi = VOICE_SPEED
        if not isinstance(speed, int | float) or not lo <= speed <= hi:
            raise ValueError(f"speed must be between {lo} and {hi}")
        tag = hashlib.sha1(self.line.encode()).hexdigest()[:8]
        path = self.folder / f"{voice}_{float(speed):.2f}_{tag}.wav"
        if path.exists():
            return path.read_bytes()
        with self._lock:
            if not self._loaded:
                self.engine.load()
                self._loaded = True
            samples, rate = self.engine.say(self.line, voice, round(float(speed), 2))
            self._schedule_unload()
        data = to_wav(samples, rate)
        self.folder.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        part.write_bytes(data)
        part.replace(path)
        return data

    def _schedule_unload(self) -> None:
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(self.idle_s, self._unload)
        self._timer.daemon = True
        self._timer.start()

    def _unload(self) -> None:
        with self._lock:
            if self._loaded:
                self.engine.unload()
                self._loaded = False
