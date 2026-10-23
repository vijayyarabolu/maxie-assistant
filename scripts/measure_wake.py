"""Measure Maxie's wake words on synthetic speech.

1. Speaker check: enroll one Kokoro voice as "him" (5 phrases), then score held-out phrases from that voice
   (should pass) and from every other voice (should fail).
2. Background audio: TV-like talk from the other voices, full of "max" as an ordinary word and characters
   called Max ("Max, come here!"), run through the real pipeline (silero VAD → segmenter → Whisper base →
   wake grammar → speaker check). Counts false wakes per hour, grammar alone and with the speaker check.
3. Recall: "him" saying the four wake forms with commands.

Usage: .venv/bin/python scripts/measure_wake.py [--minutes 30] [--out report.json]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maxie.voice.audio import FRAME, RATE, VAD, Segmenter  # noqa: E402
from maxie.voice.speaker import SpeakerCheck  # noqa: E402
from maxie.voice.speech import STT, TTS  # noqa: E402
from maxie.voice.wake import detect  # noqa: E402

HIM = "am_michael"
ENROLL = ["Hey Max, what's on my calendar today?", "Maxie, play something calm.", "Max, remind me to call mom at six.",
          "Hey Maxie, how's the weather this afternoon?", "Max, open my notes from yesterday."]
HELD_OUT = ["Max, what time is it?", "Hey Max, any new messages?", "Maxie, turn the volume down.",
            "Hey Maxie, find the address Riley sent me.", "Max what's next on my list."]
TV = [
    "Max, come here right now!", "Hey Max, you made it!", "Max is going to be late again tonight.",
    "I told Max about the party.", "We'll stream it on HBO Max this weekend.", "Mad Max is still the best action movie.",
    "Turn it up to the max.", "That's the max we can spend.", "The maximum temperature today is seventy two.",
    "Max out your retirement contributions early.", "Max and Ruby are on after the news.", "Maxine, have you seen my keys?",
    "Hey, Mac users get the update first.", "Max's dog ran across the yard.", "This blender has a max speed of ten.",
    "Max said he would bring the snacks.", "Okay everyone, settle down, the show is starting.",
    "In other news, the city council approved the new park.", "Stocks closed higher on Friday after a quiet week.",
    "Thanks for watching, and we'll see you next time.", "The weather tomorrow looks sunny with a light breeze.",
    "Coming up after the break, our chef makes the perfect pasta.", "He looked at her and said, I can't do this anymore.",
    "Max, Max, wake up, we're going to miss the bus!", "Maximum effort, that's what coach always says.",
    "Who's ready for the final round?", "Our next guest needs no introduction.", "Please welcome, Max Verstappen!",
    "Max was right about the plan all along.", "Set the oven to its max setting and wait.",
]
OTHER_VOICES = ["af_heart", "af_bella", "af_nicole", "af_sarah", "am_adam", "am_eric", "bf_emma", "bm_george",
                "af_sky", "am_liam", "bf_isabella", "bm_lewis"]


def resample(x: np.ndarray, rate: int) -> np.ndarray:
    if rate == RATE:
        return x.astype(np.float32)
    t_new = np.arange(int(len(x) * RATE / rate)) / RATE
    return np.interp(t_new, np.arange(len(x)) / rate, x).astype(np.float32)


def say(tts: TTS, text: str, voice: str) -> np.ndarray:
    tts.voice = voice
    samples, rate = tts.synth(text)
    return resample(samples, rate)


def speaker_report(tts: TTS, check: SpeakerCheck) -> dict:
    threshold = check.enroll([say(tts, t, HIM) for t in ENROLL])
    his = [check.score(say(tts, t, HIM)) for t in HELD_OUT]
    others = [check.score(say(tts, t, v)) for v in OTHER_VOICES for t in HELD_OUT[:2]]
    return {"threshold": round(threshold, 3), "his_scores": [round(s, 3) for s in his],
            "his_pass": sum(s >= threshold for s in his) / len(his),
            "others_max": round(max(others), 3), "others_pass": sum(s >= threshold for s in others) / len(others)}


def background(tts: TTS, minutes: float, rng: random.Random) -> np.ndarray:
    chunks, total = [], 0
    while total < minutes * 60 * RATE:
        clip = say(tts, rng.choice(TV), rng.choice(OTHER_VOICES))
        gap = np.zeros(int(rng.uniform(0.2, 1.5) * RATE), np.float32)
        chunks += [clip * rng.uniform(0.5, 1.0), gap]
        total += len(clip) + len(gap)
    return np.concatenate(chunks)


def run_pipeline(audio: np.ndarray, stt: STT, check: SpeakerCheck) -> dict:
    vad, seg = VAD(), Segmenter()
    grammar, verified, segments, heard = {}, {}, 0, []
    for i in range(0, len(audio) - FRAME, FRAME):
        frame = audio[i:i + FRAME]
        out = seg.feed(frame, vad.prob(frame), i / RATE)
        if out is None:
            continue
        segments += 1
        text = stt.transcribe(out, quick=True)
        r = detect(text, after_silence=True)
        if r.woke:
            grammar[r.form] = grammar.get(r.form, 0) + 1
            ok = check.judge(out, r.form).woke  # hey forms wake anyone, bare names need his voice
            heard.append({"text": text, "form": r.form, "speaker_ok": ok})
            if ok:
                verified[r.form] = verified.get(r.form, 0) + 1
    return {"segments": segments, "grammar": grammar, "with_speaker_check": verified, "examples": heard[:15]}


def non_speech(rng: random.Random, seconds: float = 60) -> np.ndarray:
    """Things a VAD can let through that aren't words: hiss, a hum, clicks, a little tune. With the wake prompt
    Whisper must not "hear" Maxie in them."""
    n, t = int(seconds * RATE), np.arange(int(seconds * RATE)) / RATE
    g = np.random.default_rng(rng.randrange(1 << 30))
    hiss = g.standard_normal(n) * 0.05
    hum = 0.2 * np.sin(2 * np.pi * 120 * t) * (1 + 0.5 * np.sin(2 * np.pi * 0.5 * t))
    clicks = np.zeros(n)
    clicks[g.integers(0, n, 400)] = 0.8
    notes = 0.2 * np.sin(2 * np.pi * np.repeat(g.choice([262, 294, 330, 392, 440], int(seconds * 4)), RATE // 4)[:n] * t)
    quiet = np.zeros(RATE // 2)
    return np.concatenate([x for part in (hiss, hum, clicks, notes) for x in (part, quiet)]).astype(np.float32)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    rng = random.Random(7)
    tts, stt = TTS(), STT()
    with tempfile.TemporaryDirectory() as tmp:
        check = SpeakerCheck(Path(tmp) / "voice.npz")
        t0 = time.time()
        report = {"speaker": speaker_report(tts, check)}
        bg = background(tts, args.minutes, rng)
        hours = len(bg) / RATE / 3600
        fw = run_pipeline(bg, stt, check)
        report["background"] = {"hours": round(hours, 3), **fw,
                                "grammar_per_hour": round(sum(fw["grammar"].values()) / hours, 2),
                                "verified_per_hour": round(sum(fw["with_speaker_check"].values()) / hours, 2)}
        recall_audio = np.concatenate([np.concatenate([say(tts, t, HIM), np.zeros(RATE, np.float32)]) for t in HELD_OUT])
        rec = run_pipeline(np.concatenate([np.zeros(RATE, np.float32), recall_audio]), stt, check)
        noise = run_pipeline(non_speech(rng), stt, check)
        report["non_speech"] = {"minutes": 4, "segments": noise["segments"], "grammar": noise["grammar"],
                                "examples": noise["examples"]}
        report["recall"] = {"said": len(HELD_OUT), "woke": sum(rec["with_speaker_check"].values()), "examples": rec["examples"]}
        report["seconds"] = round(time.time() - t0)
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text)


if __name__ == "__main__":
    main()
