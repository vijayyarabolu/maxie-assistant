"""How do short wake words score against a long-phrase enrollment? ("Maxie" alone failed the 0.60 bar.)

For a few Kokoro voices as "him": enroll on the five Settings phrases, then score short wake words ("Maxie",
"Hey Max", ...) in his voice and in other voices, clean and through a simulated room (reverb, distance, hiss).
Prints [min, median, max] per group; the wake bar default in voice/speaker.py comes from this.

Usage: nice -n 15 .venv/bin/python scripts/measure_wake_bar.py [--out report.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from measure_wake import ENROLL, OTHER_VOICES, say  # noqa: E402

from maxie.voice.speaker import SpeakerCheck  # noqa: E402
from maxie.voice.speech import TTS  # noqa: E402

RATE = 16000
SHORT = ["Maxie.", "Max.", "Hey Max.", "Hey Maxie.", "Maxie, what time is it?"]
HIMS = ["am_michael", "bf_emma", "am_eric"]


def room(x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """A laptop mic across a room: a short reverb tail, 0.15-0.6x level, low-frequency hiss at 12-25 dB SNR."""
    ir_len = int(0.35 * RATE)
    ir = rng.standard_normal(ir_len) * np.exp(-np.arange(ir_len) / (0.06 * RATE))
    ir[0] = 6.0
    ir /= np.abs(ir).sum() / 3
    y = np.convolve(x, ir)[: len(x)] * rng.uniform(0.15, 0.6)
    noise = rng.standard_normal(len(y)).cumsum()
    noise -= np.convolve(noise, np.ones(400) / 400, "same")
    noise *= np.sqrt(np.mean(y ** 2)) / (np.sqrt(np.mean(noise ** 2)) + 1e-9) / 10 ** (rng.uniform(12, 25) / 20)
    return (y + noise).astype(np.float32)


def spread(v: list[float]) -> list[float]:
    return [round(min(v), 3), round(float(np.median(v)), 3), round(max(v), 3)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    rng, tts, cache, out = np.random.default_rng(7), TTS(), {}, {}

    def clip(text: str, voice: str) -> np.ndarray:
        if (text, voice) not in cache:
            cache[(text, voice)] = say(tts, text, voice)
        return cache[(text, voice)]

    with tempfile.TemporaryDirectory() as tmp:
        for him in HIMS:
            check = SpeakerCheck(Path(tmp) / f"{him}.npz")
            check.enroll([clip(t, him) for t in ENROLL])
            others = [v for v in OTHER_VOICES if v != him][:6]
            out[him] = {
                "own_clean": spread([check.score(clip(t, him)) for t in SHORT]),
                "own_room": spread([check.score(room(clip(t, him), rng)) for t in SHORT for _ in range(3)]),
                "others_clean": spread([check.score(clip(t, v)) for v in others for t in SHORT[:4]]),
                "others_room": spread([check.score(room(clip(t, v), rng)) for v in others for t in SHORT[:4]]),
                "wake_bar_from_enrollment": round(check.wake_threshold, 3),
            }
            print(him, out[him], flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
