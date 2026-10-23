"""Write the wake-word fixture clips to tests/fixtures/voice/: 16 kHz mono 16-bit WAV.

Positives: the four wake forms, two with a command in the same breath, in three voices (Kokoro am_michael, macOS
Aman (en_IN) and Daniel (en_GB)). Negatives: "max" as an ordinary word, in two voices. Echo: one of Maxie's own
sentences in her voice (af_jessica), as the mic hears it from the speakers.

Usage: .venv/bin/python scripts/make_voice_fixtures.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maxie.voice.speech import TTS  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "voice"
RATE = 16000
POSITIVE = ["Hey Maxie.", "Hey Max.", "Maxie.", "Max.", "Maxie, what's on my calendar?", "Hey Max, what time is it?"]
NEGATIVE = ["The maximum is ten degrees.", "We'll watch it on HBO Max tonight.", "Max is going to be late again.",
            "Turn it up to the max.", "Mad Max is a great movie."]
ECHO = "Your next meeting is at three with the design team."
VOICES = {"kokoro:am_michael": "michael", "say:Aman": "aman", "say:Daniel": "daniel"}
NEGATIVE_VOICES = ("kokoro:am_michael", "say:Aman")


def resample(x: np.ndarray, rate: int) -> np.ndarray:
    t = np.arange(int(len(x) * RATE / rate)) / RATE
    return np.interp(t, np.arange(len(x)) / rate, x).astype(np.float32)


def speak(tts: TTS, voice: str, text: str) -> np.ndarray:
    engine, name = voice.split(":")
    if engine == "kokoro":
        tts.voice = name
        samples, rate = tts.synth(text)
        return resample(samples, rate)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "say.wav"
        subprocess.run(["say", "-v", name, "-o", str(path), "--data-format=LEI16@16000", text], check=True)
        data, rate = sf.read(path, dtype="float32")
    return resample(data if data.ndim == 1 else data[:, 0], rate)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def write(name: str, audio: np.ndarray) -> None:
    peak = float(np.abs(audio).max()) or 1.0
    sf.write(OUT / f"{name}.wav", (audio / peak * 0.5).astype(np.float32), RATE, subtype="PCM_16")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tts = TTS()
    for voice, short in VOICES.items():
        for text in POSITIVE:
            write(f"wake_{short}_{slug(text)}", speak(tts, voice, text))
    for voice in NEGATIVE_VOICES:
        for text in NEGATIVE:
            write(f"not_{VOICES[voice]}_{slug(text)}", speak(tts, voice, text))
    write("echo_jessica", speak(tts, "kokoro:af_jessica", ECHO))
    print(f"{len(list(OUT.glob('*.wav')))} clips in {OUT}")


if __name__ == "__main__":
    main()
