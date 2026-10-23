"""Wake word → the notch hears "listen", end to end on the fake hub (`make fake-hub`, port 8781).

Each wake clip from tests/fixtures/voice is played into the real voice loop in real time (32 ms frames at wall-clock
pace) with the real silero VAD and Whisper; "listen" goes to the fake hub over HTTP and is timed when it arrives on
an /api/events stream, which is what the notch app follows. The clock starts at the end of the wake word (Whisper
word timestamps). Only the speakers and the speaker check are faked. `--code` points at another checkout (a worktree
of main) to measure the code from before.

Usage: nice -n 10 .venv/bin/python scripts/measure_voice_latency.py [--code DIR] [--tries 20] [--asleep] [--out f.json]
"""

from __future__ import annotations

import argparse
import json
import queue
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
HUB, TOKEN = "http://127.0.0.1:8781", "devtoken"
H = {"Authorization": f"Bearer {TOKEN}"}
NAME = re.compile(r"^\W*(max|maxi|maxie|maxy|maxine|maxim)\W*$", re.I)


class LiveEars:
    """The clip as a microphone would deliver it: one 512-sample frame every 32 ms, room hiss around it."""

    def __init__(self, audio: np.ndarray, rate: int, lead_s: float, tail_s: float):
        rng = np.random.default_rng(0)
        self.lead_s = lead_s
        self.audio = np.concatenate([rng.standard_normal(int(lead_s * rate)) * 1e-3, audio,
                                     rng.standard_normal(int(tail_s * rate)) * 1e-3]).astype(np.float32)
        self.q: queue.Queue = queue.Queue()
        self.started = 0.0
        threading.Thread(target=self._play, daemon=True).start()

    def _play(self) -> None:
        self.started = time.perf_counter()
        for i in range(len(self.audio) // 512):
            delay = self.started + (i + 1) * 512 / 16000 - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            self.q.put(self.audio[i * 512:(i + 1) * 512])
        while True:
            time.sleep(512 / 16000)
            self.q.put(np.zeros(512, np.float32))

    def frames(self):
        while True:
            yield self.q.get()

    def drain(self):
        pass


class Listener:
    """Follows /api/events like the notch does and stamps every "listen"."""

    def __init__(self, http):
        self.http, self.heard = http, []
        self.ready = threading.Event()
        threading.Thread(target=self._follow, daemon=True).start()
        self.ready.wait(5)

    def _follow(self) -> None:
        with self.http.stream("GET", f"{HUB}/api/events", headers=H, timeout=None) as r:
            for line in r.iter_lines():
                self.ready.set()
                if line.startswith("data: ") and json.loads(line[6:]).get("state") == "listen":
                    self.heard.append(time.perf_counter())


class HubShim:
    """What the voice loop asks of the hub; only `state` goes over the wire (that is what opens the panel)."""
    enroll_pending = test_pending = False

    def __init__(self, http):
        self.http = http

    def state(self, s):
        self.http.post(f"{HUB}/api/events/state", json={"state": s}, headers=H, timeout=3)

    def stop(self):
        pass

    def wake_requested(self):
        return False, []

    def voice_settings(self):
        return {"voice_muted": False, "speak_replies": True, "whisper": False}

    def muted(self):
        return False

    def chat(self, text, screen=False):
        yield {"type": "done", "text": "Okay."}

    def report_speaker(self, status):
        pass

    def notice(self, kind, text):
        pass

    def report_status(self, status):
        pass

    def report_test(self, result):
        pass


class Quiet:
    """Speakers and TTS that make no sound."""
    engine, voice, speed, playing = "none", "af_jessica", 1.0, False

    def play(self, samples, rate):
        return True

    def stop(self):
        pass

    def end(self, tail_s=0.3):
        pass

    def synth(self, text):
        return np.zeros(240, np.float32), 24000

    def sleep(self):
        pass

    def wake(self):
        pass

    def warm(self):
        pass


def his_voice():
    """Passes every clip, in the old API (verify) and the new one (judge)."""
    try:
        from maxie.voice.speaker import Verdict
    except ImportError:
        Verdict = None

    class Him:
        enrolled = True

        def verify(self, audio):
            return True

        def judge(self, audio, form):
            return Verdict(True, 0.5, 0.38, "his voice")

        def refresh(self):
            pass

        def unload(self):
            pass
    return Him()


def word_end(audio: np.ndarray) -> float | None:
    """When the name (Max / Maxie) ends in the clip, from Whisper's word timestamps."""
    import mlx_whisper
    out = mlx_whisper.transcribe(audio, path_or_hf_repo="mlx-community/whisper-base-mlx", language="en",
                                 word_timestamps=True, temperature=0.0)
    for seg in out.get("segments", []):
        for w in seg.get("words", []):
            if NAME.match(w["word"]):
                return float(w["end"])
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default=str(REPO))
    ap.add_argument("--tries", type=int, default=20)
    ap.add_argument("--asleep", action="store_true", help="start with the big models unloaded (10 idle minutes)")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    sys.path.insert(0, args.code)
    import httpx
    import soundfile as sf

    import maxie.voice.loop as loop_mod
    from maxie.voice.audio import VAD
    from maxie.voice.speech import STT
    loop_mod.FOLLOW_UP_S = 1.0  # don't wait 8 s for a command after each bare wake
    stt = STT()
    stt.warm()
    http = httpx.Client()
    listener = Listener(http)
    clips = sorted((REPO / "tests" / "fixtures" / "voice").glob("wake_*.wav"))
    rows = []
    for i in range(args.tries):
        clip = clips[i % len(clips)]
        audio, rate = sf.read(clip, dtype="float32")
        end = word_end(audio)
        if end is None:
            continue
        before = len(listener.heard)
        ears = LiveEars(audio, rate, lead_s=1.5, tail_s=4.0)
        quiet = Quiet()
        loop = loop_mod.VoiceLoop(ears=ears, vad=VAD(), stt=stt, tts=quiet, player=quiet, hub=HubShim(http),
                                  log=lambda m: None, speaker=his_voice(),
                                  idle_unload_s=0.0 if args.asleep else 600.0)
        threading.Thread(target=loop.run, daemon=True).start()
        deadline = time.perf_counter() + 10
        while len(listener.heard) == before and time.perf_counter() < deadline:
            time.sleep(0.005)
        loop.stop()
        word_done = ears.started + ears.lead_s + end
        took = (listener.heard[before] - word_done) * 1000 if len(listener.heard) > before else None
        rows.append({"clip": clip.stem, "ms": None if took is None else round(took)})
        print(f"{clip.stem:45s} {'MISSED' if took is None else f'{took:5.0f} ms'}", flush=True)
        time.sleep(1.5)  # the previous loop thread finishes its follow-up window
        if args.asleep:
            stt.wake()
    times = [r["ms"] for r in rows if r["ms"] is not None]
    summary = {"code": args.code, "asleep": args.asleep, "tries": len(rows), "woke": len(times),
               "p50_ms": round(float(np.percentile(times, 50))) if times else None,
               "p95_ms": round(float(np.percentile(times, 95))) if times else None}
    print(json.dumps(summary))
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))


if __name__ == "__main__":
    main()
