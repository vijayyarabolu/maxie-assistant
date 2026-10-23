"""Echo cancelling for the built-in speakers.

On the MacBook's own speakers the mic hears Maxie louder than him, so "Maxie, stop" over her was heard as her words
(3 of 7 in scripts/echo_room.py). WebRTC's AEC3 (livekit's AudioProcessingModule) takes out what she is playing:
the Player hands it every block the device takes (`render`, from the device's own thread), the mic hands it every
frame it hears (`capture`), and the frame that goes on to the speech detector and Whisper has her voice cut by ~30 dB. Measured offline with her real answers in
a simulated room: Whisper then heard 7 of 8 stops over her, and her echo alone as nothing. In the live loop (real
timing, scripts/echo_room.py --mode barge) about 6 of 10: where his "stop" lands on her loudest words, AEC3's
double-talk suppression takes him out with her.

Install: `uv pip install -p .venv livekit==1.1.21` (the voice helper runs on the venv's site-packages).

It only runs while she speaks and ACTIVE_S after (0.8 % of a core then); the rest of the time frames pass untouched,
so the idle cost stays where it was measured. Without livekit on the Mac there is no canceller (`create()` is None)
and the voice loop works as before.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque

import numpy as np

RATE = 16000
STEP = RATE // 100   # WebRTC works on 10 ms frames
ACTIVE_S = 1.5       # after her last sample is written: the device's buffer, the room, and the AEC's own tail
DELAY_MS = 40        # a hint: from a block reaching AEC3 (at the mic frame after the device took it) to its echo in
                     # the mic (the device's 50 ms buffer, the speakers, the air); AEC3 finds the rest itself
PENDING_MAX = 500    # blocks the device took, waiting for the mic's thread (~5 s: the mic is closed while muted)


def _i16(x: np.ndarray) -> bytes:
    return (np.clip(x, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


class Resampler:
    """Streaming rational resampling to `to` (16 kHz here): the low-pass keeps its state from piece to piece. The Player
    hands over one device block at a time (~10 ms), and resampling each block on its own left a seam at every one, so
    the reference no longer matched the echo."""

    def __init__(self, rate: int, to: int = RATE):
        from scipy.signal import firwin
        g = math.gcd(to, rate)
        self.rate, self.up, self.down = rate, to // g, rate // g
        m = max(self.up, self.down)
        h = firwin(24 * m + 1, 0.9 / m, window=("kaiser", 8.0)) * self.up
        self.taps = -(-len(h) // self.up)
        self.phases = np.zeros((self.up, self.taps))  # polyphase: row φ = h[φ], h[φ + up], h[φ + 2 up], ...
        for phase in range(self.up):
            self.phases[phase, :len(h[phase::self.up])] = h[phase::self.up]
        self.history = np.zeros(self.taps - 1)
        self.start = self.next = 0  # the first input sample of the next piece, the next output sample

    def __call__(self, samples: np.ndarray) -> np.ndarray:
        """Only the output samples are computed, each from its own phase of the filter: zero-stuffing and filtering
        everything took 120 ms per 10 ms block at 44.1 kHz. The same filter and output, as one stream."""
        x = np.asarray(samples, np.float64).reshape(-1)
        if self.up == self.down == 1:
            return x.astype(np.float32)
        buf = np.concatenate([self.history, x])
        end = -(-self.up * (self.start + len(x)) // self.down)
        at = np.arange(self.next, end) * self.down
        newest = at // self.up - self.start + len(self.history)  # each output's newest input sample, in buf
        y = np.einsum("ij,ij->i", buf[newest[:, None] - np.arange(self.taps)], self.phases[at % self.up])
        self.history = buf[len(buf) - len(self.history):]
        self.start, self.next = self.start + len(x), end
        return y.astype(np.float32)


class EchoCanceller:
    """`render` is called on the output device's realtime thread with each block it takes: it only queues the block
    (no lock, no work there: a lock shared with the mic stalled the device under load and Maxie went
    silent for up to 2 s). The mic's thread hands the queued blocks to AEC3 just before it cleans each frame: a block
    is taken at least the device's latency before it can be heard, so its reference is always in before its echo.
    `wanted()` (asked at most once a second, on the mic's thread): False in headphones, then nothing is cancelled."""

    def __init__(self, clock=time.monotonic, wanted=None):
        from livekit import rtc
        self.rtc, self.clock, self.wanted = rtc, clock, wanted
        self.apm = rtc.AudioProcessingModule(echo_cancellation=True, high_pass_filter=True)
        self.lock = threading.Lock()
        self._pending: deque[tuple[np.ndarray, int]] = deque(maxlen=PENDING_MAX)
        self._far = np.zeros(0, np.float32)
        self._far_rate, self._to_16k = None, None
        self._near = np.zeros(0, np.float32)
        self._out = np.zeros(0, np.float32)
        self.last_render = -math.inf
        self.active = False
        self._wanted, self._asked = True, -math.inf

    def render(self, samples: np.ndarray, rate: int) -> None:
        """What the device just took. Queued only (deque.append is atomic): this runs on its realtime thread."""
        self._pending.append((samples, rate))
        self.last_render = self.clock()

    def _feed_far(self) -> None:
        """The queued blocks into AEC3, in order (under self.lock, on the mic's thread)."""
        while self._pending:
            samples, rate = self._pending.popleft()
            if self._far_rate != rate:
                self._far_rate, self._to_16k = rate, Resampler(rate)
            buf = np.concatenate([self._far, self._to_16k(samples)])
            n = len(buf) // STEP * STEP
            for i in range(0, n, STEP):
                self.apm.process_reverse_stream(self.rtc.AudioFrame(_i16(buf[i:i + STEP]), RATE, 1, STEP))
            self._far = buf[n:]

    def _on(self) -> bool:
        if self.wanted is not None and self.clock() - self._asked >= 1.0:
            self._wanted, self._asked = bool(self.wanted()), self.clock()
        return self._wanted

    def capture(self, frame: np.ndarray) -> np.ndarray:
        """One mic frame with her echo taken out; the same frame when she hasn't spoken for ACTIVE_S."""
        with self.lock:
            if not self._on():
                self._pending.clear()
                self.last_render = -math.inf
            self._feed_far()
            if self.clock() - self.last_render > ACTIVE_S:
                if self.active:  # back to plain frames: what was buffered is ~10 ms of silence after her
                    self.active, self._near, self._out = False, np.zeros(0, np.float32), np.zeros(0, np.float32)
                return frame
            if not self.active:
                self.active, self._out = True, np.zeros(STEP, np.float32)  # 10 ms of lead so every frame is whole
            buf = np.concatenate([self._near, frame.astype(np.float32)])
            n = len(buf) // STEP * STEP
            done = []
            for i in range(0, n, STEP):
                self.apm.set_stream_delay_ms(DELAY_MS)
                f = self.rtc.AudioFrame(_i16(buf[i:i + STEP]), RATE, 1, STEP)
                self.apm.process_stream(f)
                done.append(np.frombuffer(bytes(f.data), dtype=np.int16).astype(np.float32) / 32767)
            self._near = buf[n:]
            self._out = np.concatenate([self._out, *done])
            out, self._out = self._out[:len(frame)], self._out[len(frame):]
            return out


def create(log=print, wanted=None) -> EchoCanceller | None:
    """The canceller, or None when livekit isn't installed (then barge-in on the speakers hears less)."""
    try:
        return EchoCanceller(wanted=wanted)
    except ImportError as e:
        log(f"voice: no echo canceller ({e}); on the built-in speakers only a clear \"stop\" stops Maxie")
        return None
