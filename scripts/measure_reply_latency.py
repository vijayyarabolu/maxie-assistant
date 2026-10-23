"""He stops talking → Maxie's first spoken word, on the voice side, end to end.

Each same-breath clip ("Hey Max, what time is it", "Maxie, what's on my calendar") is played into the real voice loop
in real time with the real silero VAD, Whisper and Kokoro. The hub is a stub that streams a two-sentence answer after
`--model-ms` (what the hub takes to its first word: measure that with scripts/measure_chat_latency.py), and the
speakers are silent but take as long as the audio would. The clock starts when his speech ends in the clip. Reports
the first word (the "One moment." filler counts) and the first word of the answer. `--code` points at another
checkout (a worktree of main) to measure the code from before. Nothing touches the hub, the screen or the speakers.

Usage: nice -n 10 .venv/bin/python scripts/measure_reply_latency.py [--code DIR] [--tries 12] [--model-ms 0] [--out f.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from measure_voice_latency import LiveEars, his_voice  # noqa: E402

SPEECH_LEVEL = 0.02   # the clip's last sample above this is where his speech ends
ANSWER = ["The capital of France is Paris. ", "It sits on the Seine, in the north of the country."]
BETWEEN_S = 0.15      # Haiku's second sentence arrives this long after the first


class Hub:
    """What the voice loop asks of the hub, offline; `chat` streams ANSWER after the model's delay."""
    enroll_pending = test_pending = False

    def __init__(self, model_s: float):
        self.model_s = model_s

    def chat(self, text, screen=False):
        time.sleep(self.model_s)
        for i, piece in enumerate(ANSWER):
            if i:
                time.sleep(BETWEEN_S)
            yield {"type": "text", "text": piece}
        yield {"type": "done", "text": "".join(ANSWER)}

    def voice_settings(self):
        return {"voice_muted": False, "speak_replies": True, "whisper": False}

    def wake_requested(self):
        return False, []

    def __getattr__(self, name):  # state, stop, notice, report_*: nothing to do offline
        return lambda *a, **k: False


class Speakers:
    """Silent, but each play takes as long as its audio. Stamps every play; tells the chime and the filler apart."""

    def __init__(self, filler: np.ndarray):
        self.filler, self.plays, self.playing = filler, [], False

    def play(self, samples, rate):
        from maxie.voice.audio import chime
        kind = "chime" if len(samples) == len(chime(rate)) else "filler" if len(samples) == len(self.filler) else "answer"
        self.plays.append((time.perf_counter(), kind))
        time.sleep(len(samples) / rate)
        return True

    def stop(self):
        pass

    def end(self, tail_s=0.3):
        pass


def speech_end(audio: np.ndarray, rate: int) -> float:
    loud = np.flatnonzero(np.abs(audio) > SPEECH_LEVEL)
    return float(loud[-1]) / rate if len(loud) else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default=str(REPO))
    ap.add_argument("--tries", type=int, default=12)
    ap.add_argument("--model-ms", type=float, default=0.0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    sys.path.insert(0, args.code)
    import soundfile as sf

    import maxie.voice.loop as loop_mod
    from maxie.voice.audio import VAD
    from maxie.voice.speech import STT, TTS
    loop_mod.FOLLOW_UP_S = 1.0  # don't keep listening 8 s after each answer
    stt, tts = STT(), TTS()
    stt.warm()
    tts.warm()
    filler = tts.synth(loop_mod.FILLER)[0]
    clips = sorted(c for c in (REPO / "tests" / "fixtures" / "voice").glob("wake_*.wav")
                   if "what" in c.stem)
    rows = []
    for i in range(args.tries):
        clip = clips[i % len(clips)]
        audio, rate = sf.read(clip, dtype="float32")
        ears = LiveEars(audio, rate, lead_s=1.5, tail_s=6.0)
        speakers = Speakers(filler)
        loop = loop_mod.VoiceLoop(ears=ears, vad=VAD(), stt=stt, tts=tts, player=speakers, hub=Hub(args.model_ms / 1000),
                                  log=lambda m: None, speaker=his_voice())
        threading.Thread(target=loop.run, daemon=True).start()
        deadline = time.perf_counter() + 12
        while not any(k == "answer" for _, k in speakers.plays) and time.perf_counter() < deadline:
            time.sleep(0.01)
        loop.stop()
        done = ears.started + ears.lead_s + speech_end(audio, rate)
        words = [t for t, k in speakers.plays if k != "chime"]
        answer = [t for t, k in speakers.plays if k == "answer"]
        row = {"clip": clip.stem, "first_word_ms": round((words[0] - done) * 1000) if words else None,
               "answer_ms": round((answer[0] - done) * 1000) if answer else None,
               "filler": any(k == "filler" for _, k in speakers.plays)}
        rows.append(row)
        print(f"{clip.stem:45s} first word {row['first_word_ms']} ms, answer {row['answer_ms']} ms"
              f"{', after the filler' if row['filler'] else ''}", flush=True)
        time.sleep(2.0)  # the loop thread finishes its follow-up window

    def pct(key: str, q: float) -> int | None:
        xs = [r[key] for r in rows if r[key] is not None]
        return round(float(np.percentile(xs, q))) if xs else None
    summary = {"code": args.code, "model_ms": args.model_ms, "tries": len(rows),
               "first_word_p50_ms": pct("first_word_ms", 50), "first_word_p95_ms": pct("first_word_ms", 95),
               "answer_p50_ms": pct("answer_ms", 50), "answer_p95_ms": pct("answer_ms", 95),
               "fillers": sum(r["filler"] for r in rows)}
    print(json.dumps(summary))
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))


if __name__ == "__main__":
    main()
