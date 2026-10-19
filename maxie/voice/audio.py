"""Microphone, voice activity detection, segmenting, playback and the chime. 16 kHz mono float32.

The segmenter is a small state machine fed one 32 ms frame at a time with its VAD probability, so it
is testable without a microphone. `Mic` and `Player` wrap sounddevice (PortAudio); `Player.stop()`
is what barge-in calls.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

import numpy as np

RATE = 16000
FRAME = 512  # 32 ms, what silero-vad wants at 16 kHz
START_FRAMES = 2            # speech probability above START_P this many frames in a row starts a segment
START_P, END_P = 0.5, 0.35
END_SILENCE_S = 0.6         # this much silence ends a segment
MAX_SEGMENT_S = 12.0
PRE_ROLL_FRAMES = 8         # audio kept from before the segment started (the first syllable)


@dataclass
class Segmenter:
    """Feed (frame, speech probability); get a finished speech segment back when one ends."""
    in_speech: bool = False
    _run: int = 0
    _silence: int = 0
    _frames: list[np.ndarray] = field(default_factory=list)
    _pre: list[np.ndarray] = field(default_factory=list)
    started_at: float = 0.0
    started_after_silence: bool = True  # False when the segment before was cut at MAX_SEGMENT_S mid-speech
    _split: bool = False
    _quiet: int = 0

    def feed(self, frame: np.ndarray, p: float, now: float) -> np.ndarray | None:
        if not self.in_speech:
            self._pre = (self._pre + [frame])[-PRE_ROLL_FRAMES:]
            self._run = self._run + 1 if p >= START_P else 0
            self._quiet = self._quiet + 1 if p < END_P else 0
            if self._quiet * FRAME / RATE >= END_SILENCE_S:
                self._split = False  # a real pause after a mid-speech cut
            if self._run >= START_FRAMES:
                self.in_speech, self._silence, self.started_at = True, 0, now
                self.started_after_silence = not self._split
                self._frames = list(self._pre)
            return None
        self._frames.append(frame)
        self._silence = self._silence + 1 if p < END_P else 0
        too_long = now - self.started_at >= MAX_SEGMENT_S
        paused = self._silence * FRAME / RATE >= END_SILENCE_S
        if paused or too_long:
            out = np.concatenate(self._frames)
            self.in_speech, self._run, self._silence, self._frames = False, 0, 0, []
            self._split = not paused
            return out
        return None

    def seconds_of_speech(self) -> float:
        return len(self._frames) * FRAME / RATE if self.in_speech else 0.0


class VAD:
    """silero-vad over onnxruntime. One instance per stream (it keeps state between frames)."""

    def __init__(self):
        from silero_vad import load_silero_vad
        self.model = load_silero_vad(onnx=True)

    def prob(self, frame: np.ndarray) -> float:
        import torch
        return float(self.model(torch.from_numpy(frame), RATE).item())

    def reset(self) -> None:
        self.model.reset_states()


class Mic:
    """Frames from the default input device, as a blocking iterator."""

    def __init__(self, device=None):
        import queue

        import sounddevice as sd
        self.q: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=FRAME, device=device,
                                     callback=self._cb)

    def _cb(self, indata, frames, t, status):
        try:
            self.q.put_nowait(indata[:, 0].copy())
        except Exception:  # a full queue drops audio, never crashes the callback  # noqa: BLE001
            pass

    def __enter__(self):
        self.stream.start()
        return self

    def __exit__(self, *exc):
        self.stream.stop()
        self.stream.close()

    def frames(self) -> Iterator[np.ndarray]:
        while True:
            yield self.q.get()

    def drain(self) -> None:
        while not self.q.empty():
            self.q.get_nowait()


FADE_MS = 10          # every chunk fades in and out this long, so nothing clicks
TAIL_S = 0.3          # silence written before the output stream closes at the end of a reply
CHIME_TAIL_S = 0.05   # after the wake chime: short, so listening starts right away
CHIME_S = 0.18
CHIME_NOTES = (520.0, 660.0)
CHIME_DB = -12.0


def fade(samples: np.ndarray, rate: int, ms: float = FADE_MS) -> np.ndarray:
    """A copy with a raised-cosine fade in and out; the first and last samples are exactly 0."""
    out = np.array(samples, dtype=np.float32, copy=True)
    n = min(int(rate * ms / 1000), len(out) // 2)
    if n > 1:
        ramp = _ramp(n)
        out[:n] *= ramp
        out[-n:] *= ramp[::-1]
    return out


def _ramp(n: int) -> np.ndarray:
    return (0.5 - 0.5 * np.cos(np.linspace(0.0, math.pi, n))).astype(np.float32)


class Player:
    """Plays float32 audio. One output stream stays open for a whole reply (opening and closing one per
    sentence pops on the speakers); every chunk is faded; `end()` writes a short silence and closes.
    `stop()` cuts it (barge-in, "Max, stop") with a 10 ms fade-out instead of a hard cut."""

    def __init__(self, device=None, open_stream=None):
        self.device = device
        self._open = open_stream or self._sd_stream
        self._stop = threading.Event()
        self._out = None
        self._rate: int | None = None
        self.playing = False

    def _sd_stream(self, rate: int):
        import sounddevice as sd
        out = sd.OutputStream(samplerate=rate, channels=1, dtype="float32", device=self.device)
        out.start()
        return out

    def _stream(self, rate: int):
        if self._out is not None and self._rate != rate:
            self.end()
        if self._out is None:
            self._out, self._rate = self._open(rate), rate
        return self._out

    def play(self, samples: np.ndarray, rate: int) -> bool:
        """Blocks until written. Returns False when stopped early."""
        self._stop.clear()
        self.playing = True
        audio = fade(samples, rate)
        chunk = rate // 20  # 50 ms
        try:
            out = self._stream(rate)
            for i in range(0, len(audio), chunk):
                if self._stop.is_set():
                    n = min(int(rate * FADE_MS / 1000), len(audio) - i)
                    if n > 1:
                        out.write(_column(audio[i:i + n] * _ramp(n)[::-1]))
                    return False
                out.write(_column(audio[i:i + chunk]))
            return True
        finally:
            self.playing = False

    def end(self, tail_s: float = TAIL_S) -> None:
        """The reply is over: silence, then close, so the device never stops on a non-zero sample."""
        out, rate = self._out, self._rate
        self._out, self._rate = None, None
        if out is None or rate is None:
            return
        try:
            out.write(_column(np.zeros(int(rate * tail_s), dtype=np.float32)))
            out.stop()
            out.close()
        except Exception as e:  # a dead device must not kill the voice loop  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning("player: closing the output stream failed: %s", e)

    def stop(self) -> None:
        self._stop.set()


def _column(a: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(a, dtype=np.float32).reshape(-1, 1)


def chime(rate: int = 24000) -> np.ndarray:
    """The wake chime: two warm notes (520 then 660 Hz) gliding into each other, 180 ms, peak -12 dBFS.
    Continuous phase (no jump between the notes), soft attack, long smooth release to exact zero."""
    t = np.arange(int(rate * CHIME_S)) / rate
    freq = np.where(t < CHIME_S / 2, CHIME_NOTES[0], CHIME_NOTES[1])
    phase = 2 * math.pi * np.cumsum(freq) / rate
    tone = np.sin(phase) + 0.12 * np.sin(2 * phase)
    attack = np.minimum(1.0, t / 0.015)
    release = np.clip((CHIME_S - t) / 0.09, 0.0, 1.0) ** 2
    shaped = tone * attack * release
    peak = float(np.abs(shaped).max()) or 1.0
    return fade(shaped * (10 ** (CHIME_DB / 20) / peak), rate, ms=5)


def now() -> float:
    return time.monotonic()
