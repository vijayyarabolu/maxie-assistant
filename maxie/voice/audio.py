"""Microphone, voice activity detection, segmenting, playback and the chime. 16 kHz mono float32.

The segmenter is a small state machine fed one 32 ms frame at a time with its VAD probability, so it
is testable without a microphone. `Mic` and `Player` wrap sounddevice (PortAudio); `Player.stop()`
is what barge-in calls. Smooth and clear like Siri: scripts/voice_output_check.py records what the Player
really plays and counts clicks, clips and gaps.
"""

from __future__ import annotations

import functools
import logging
import math
import subprocess
import sys
import threading
import time
from collections import deque
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

    def trailing_silence_s(self) -> float:
        """Inside a segment: how long he has been quiet (a short pause, before the 0.6 s that ends it)."""
        return self._silence * FRAME / RATE if self.in_speech else 0.0

    def snapshot(self) -> np.ndarray:
        """The segment so far (the early wake check reads it before the segment ends)."""
        return np.concatenate(self._frames) if self._frames else np.zeros(0, np.float32)


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


MIC_BLOCK = 2  # frames per device callback: 64 ms


class Mic:
    """Frames from the default input device, as a blocking iterator. The device hands over MIC_BLOCK frames at a
    time (waking 31 times a second cost the voice helper more than the speech model did)."""

    def __init__(self, device=None, filter=None):
        import queue

        import sounddevice as sd
        self.q: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self.closed = False
        self.filter = filter  # each frame as it is heard (the echo canceller: maxie.voice.aec)
        self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=FRAME * MIC_BLOCK,
                                     device=device, callback=self._cb)

    def _cb(self, indata, frames, t, status):
        block = indata[:, 0]
        try:
            for i in range(0, len(block) - FRAME + 1, FRAME):
                frame = block[i:i + FRAME].copy()
                self.q.put_nowait(self.filter(frame) if self.filter else frame)
        except Exception:  # a full queue drops audio, never crashes the callback  # noqa: BLE001
            pass

    def __enter__(self):
        self.stream.start()
        return self

    def __exit__(self, *exc):
        self.stream.stop()
        self.stream.close()

    def close_mic(self) -> None:
        """Muted: the device stops (macOS's orange dot goes); `open_mic`, or asking for frames, starts it again."""
        if not self.closed:
            self.stream.stop()
            self.closed = True

    def open_mic(self) -> None:
        if self.closed:
            self.stream.start()
            self.closed = False

    def frames(self) -> Iterator[np.ndarray]:
        while True:
            if self.closed:
                self.open_mic()
            yield self.q.get()

    def drain(self) -> None:
        while not self.q.empty():
            self.q.get_nowait()


FADE_MS = 10          # every chunk fades in and out this long: it starts and ends on exact zero, so joins never click
STOP_FADE_MS = 50     # a stop (barge-in, "Maxie, stop", ⌥⇧P) fades him out over this long instead of cutting the wave
OUT_LATENCY_S = 0.05  # the device's buffer: what the realtime callback has to beat; a stop is heard this much later
STALL_S = 0.5         # the device, once open, took nothing for this long: it is gone; the speaker is restarted
PLAY_GRACE_S = 2.0    # a sentence may run this much over its own length before the speaker is restarted
PEAK_DBFS = -1.0      # nothing leaves the player louder (Kokoro peaks at -0.6 dBFS; resampled, +0.6: clipped)
SPEECH_DBFS = -21.0   # speech is levelled to this RMS over its voiced part: Kokoro af_jessica's own median
LEVEL_PEAK_DBFS = -1.5  # ...and its peak kept under this, so resampling's overshoot stays under PEAK_DBFS
VOICED_DB = -30.0     # 10 ms windows this far under the loudest one are pauses, left out of the RMS
RESAMPLE_TAPS = 64    # per side, per phase: a Kaiser (β 10) low-pass with ~104 dB image rejection (§10)
TAIL_S = 0.3          # silence written before the output stream closes at the end of a reply
CHIME_TAIL_S = 0.05   # after the wake chime: short, so listening starts right away
CHIME_S = 0.18
CHIME_NOTES = (520.0, 660.0)
CHIME_GLIDE_S = 0.03  # the first note glides into the second over this long (a step in pitch chirps)
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


def level(samples: np.ndarray, rate: int) -> np.ndarray:
    """Speech at SPEECH_DBFS (RMS of its voiced 10 ms windows), its peak never over LEVEL_PEAK_DBFS. A copy."""
    x = np.asarray(samples, dtype=np.float32)
    win = max(1, rate // 100)
    usable = len(x) // win * win
    if not usable or not np.any(x):
        return x.copy()
    rms = np.sqrt((x[:usable].reshape(-1, win).astype(np.float64) ** 2).mean(axis=1))
    voiced = rms[rms >= rms.max() * 10 ** (VOICED_DB / 20)]
    gain = min(10 ** (SPEECH_DBFS / 20) / float(np.sqrt((voiced ** 2).mean())),
               10 ** (LEVEL_PEAK_DBFS / 20) / float(np.abs(x).max()))
    return (x * gain).astype(np.float32)


def limit(samples: np.ndarray, dbfs: float = PEAK_DBFS) -> np.ndarray:
    """Scaled down (whole) when its peak is over `dbfs`: never clipped. The same array when it's under."""
    peak = float(np.abs(samples).max(initial=0.0))
    ceiling = 10 ** (dbfs / 20)
    return samples if peak <= ceiling else (samples * (ceiling / peak)).astype(np.float32)


@functools.lru_cache(maxsize=8)
def _lowpass(up: int, down: int) -> np.ndarray:
    from scipy.signal import firwin
    m = max(up, down)
    return firwin(2 * RESAMPLE_TAPS * m + 1, 0.96 / m, window=("kaiser", 10.0)) * up


def resample(samples: np.ndarray, rate: int, to: int) -> np.ndarray:
    """Polyphase resampling with a steep anti-imaging filter (scipy's default lets 12 kHz images through at -15 dB)."""
    x = np.asarray(samples, dtype=np.float32)
    if rate == to or not len(x):
        return x
    from scipy.signal import resample_poly
    g = math.gcd(rate, to)
    up, down = to // g, rate // g
    return resample_poly(x, up, down, window=_lowpass(up, down)).astype(np.float32)


class _Queue:
    """What is waiting to play, as the device's callback takes it: whole chunks go in at once, so the device never
    waits on Python to write the middle of a word. `taken` counts every frame handed to the device (or dropped)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.chunks: deque[np.ndarray] = deque()
        self.head = 0
        self.added = self.taken = 0
        self.fade_left = self.fade_len = 0  # a stop: frames of fade-out still to play
        self.drop_to = 0                    # ...then what was queued before the stop is dropped, up to here

    def add(self, samples: np.ndarray) -> int:
        with self.lock:
            self.chunks.append(samples)
            self.added += len(samples)
            return self.added

    def take(self, out: np.ndarray) -> None:
        with self.lock:
            n, i = len(out), 0
            while i < n and self.chunks:
                chunk = self.chunks[0]
                k = min(n - i, len(chunk) - self.head)
                out[i:i + k] = chunk[self.head:self.head + k]
                self._skip(k)
                i += k
            out[i:] = 0.0
            if self.fade_left:
                k = min(n, self.fade_left)
                at = self.fade_len - self.fade_left
                out[:k] *= _ramp(self.fade_len)[::-1][at:at + k]
                out[k:] = 0.0  # past the fade: what he was saying (dropped) or the closing silence (zeros anyway)
                self.fade_left -= k
                if not self.fade_left:
                    self._skip(max(0, self.drop_to - self.taken))

    def _skip(self, k: int) -> None:
        """k frames off the front."""
        self.taken += k
        while k and self.chunks:
            left = len(self.chunks[0]) - self.head
            step = min(k, left)
            self.head += step
            k -= step
            if self.head == len(self.chunks[0]):
                self.chunks.popleft()
                self.head = 0

    def stop(self, frames: int) -> None:
        with self.lock:
            if self.added > self.taken and not self.fade_left:
                self.fade_left = self.fade_len = frames
                self.drop_to = self.added


class _Remote:
    """The speaker process (maxie/voice/output.py), seen from here as the queue: `add` sends audio, `taken`
    follows the device, `stop(frames)` fades it out there; each block it plays comes back through `on_block`."""

    def __init__(self, cmd: list[str], on_block):
        self.cmd, self.on_block = cmd, on_block
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.rate = 48000
        self.added = self.taken = self.underflows = 0
        self.fade_left = 0
        self.opened = False  # the speaker process has the device open (opening can take a second: AirPods, cold)

    def start(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
            threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _send(self, data: bytes) -> None:
        with self.lock:
            proc = self.proc
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (BrokenPipeError, OSError, AttributeError) as e:
                logging.getLogger(__name__).warning("player: the speaker process is gone (%s); a new one starts "
                                                    "with the next sentence", e)
                if self.proc is proc:  # not the one abort() has started meanwhile
                    self.kill()

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def kill(self) -> None:
        """Gone at once: its pipe closes, so a write blocked on it and its reader thread both end."""
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            proc.kill()

    def _read(self, proc: subprocess.Popen) -> None:
        """Live at 3:33 PM: a reader thread that died left `taken` frozen and Maxie stuck speaking. Whatever goes
        wrong here, the process is killed, so the Player sees it gone and starts another."""
        from .output import read_message
        try:
            while (msg := read_message(proc.stdout)) is not None:
                kind, a, b, payload = msg
                if kind == b"T":
                    self.underflows, self.taken = a, b
                    try:
                        self.on_block(np.frombuffer(payload, dtype="<f4"), self.rate)
                    except Exception:  # the reference is optional; following the device is not  # noqa: BLE001
                        logging.getLogger(__name__).exception("player: handing a played block on failed")
                elif kind == b"O":
                    self.opened = True
                elif kind == b"C":
                    self.closed.set()
        except Exception:  # see above  # noqa: BLE001
            logging.getLogger(__name__).exception("player: the speaker's pipe broke")
        if proc.poll() is None:
            proc.kill()

    def open(self, rate: int, device) -> _Remote:
        from .output import message
        self.start()
        self.rate, self.added, self.taken, self.opened = rate, 0, 0, False
        self._send(message(b"O", rate, 0, ("" if device is None else str(device)).encode()))
        return self

    def add(self, samples: np.ndarray) -> int:
        from .output import message
        self.added += len(samples)
        self._send(message(b"A", 0, 0, np.ascontiguousarray(samples, dtype="<f4").tobytes()))
        return self.added

    def stop(self, frames: int) -> None:
        from .output import message
        self._send(message(b"S", frames))

    def close(self) -> None:
        from .output import message
        self.closed.clear()
        self._send(message(b"C"))
        if self.alive:
            self.closed.wait(STALL_S)


class Player:
    """Plays float32 audio. One output stream stays open for a whole reply (opening and closing one per sentence pops
    on the speakers), at the device's own rate: everything is resampled here with a steep filter instead of by
    CoreAudio, kept under PEAK_DBFS, and faded in and out, so chunks join on zero. Each chunk is queued whole in the
    speaker process (maxie/voice/output.py), whose realtime callback takes it from there: nothing in this
    process can keep the device waiting (written 50 ms at a time into PortAudio's 28 ms buffer, the device
    ran dry mid-word whenever Whisper or Kokoro kept the writing thread waiting, and clicked; a callback in this
    process still came late now and then, as long as Whisper or the echo canceller held the GIL).
    `stop()` (barge-in, "Maxie, stop", ⌥⇧P) fades him out over STOP_FADE_MS from where he is, and holds until `end()`:
    a stop that lands while the next sentence is still being made isn't lost. `end()` plays a short silence and
    closes. One chunk plays at a time: the chime never plays over him."""

    def __init__(self, device=None, open_stream=None, on_write=None, out_rate: int | None = None,
                 speaker: list[str] | None = None):
        self.device = device
        self._open = open_stream  # tests: (rate, callback) -> a started stream with .stop() / .close()
        self._native = open_stream is None  # a real device: played at its own rate, from the speaker process
        self._remote = None
        if self._native or speaker:
            self._remote = _Remote(speaker or [sys.executable, "-m", "maxie.voice.output"], self._played)
            self._remote.start()  # now: the first chime doesn't wait for it
        self.out_rate = out_rate
        self.on_write = on_write  # (samples, rate) as the device takes them: the echo canceller's reference
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._moved = threading.Event()
        self._q = _Queue()
        self._out = None
        self._rate: int | None = None
        self.playing = False
        self.underruns = 0
        self.failed = False  # the speaker failed in this reply: nothing more plays until `end()`
        self.failures = 0

    def _device_rate(self) -> int:
        import sounddevice as sd
        try:
            return int(sd.query_devices(self.device, kind="output")["default_samplerate"])
        except Exception:  # no device info: the rate every Mac output supports  # noqa: BLE001
            return 48000

    def _target(self, rate: int) -> int:
        if self.out_rate:
            return self.out_rate
        if self._native:
            return self._rate or self._device_rate()
        return rate

    def _callback(self, outdata, frames, time_info, status) -> None:
        """CoreAudio's realtime thread: copy the next block out, and hand the canceller exactly that block, at the
        moment the device takes it. Fed from another thread, the reference came late whenever Whisper kept that
        thread waiting (just when he says "stop" over her): the canceller then took him out with her, and "Maxie,
        stop" was heard 21 times in 40 instead of 22 in 30."""
        if status and status.output_underflow:
            self.underruns += 1
        block = outdata[:, 0]
        self._q.take(block)
        if self.on_write is not None:
            try:
                self.on_write(block.copy(), self._rate or 48000)  # a copy: the canceller never touches what plays
            except Exception:  # nothing may break the device's thread; the canceller is optional  # noqa: BLE001
                pass
        self._moved.set()

    def _played(self, block: np.ndarray, rate: int) -> None:
        """A block the speaker process played (its reader thread here)."""
        self.underruns = self._remote.underflows
        if self.on_write is not None:
            self.on_write(block.copy(), rate)  # the canceller's reference: exactly what played
        self._moved.set()

    def _stream(self, rate: int):
        if self._out is not None and self._out is self._remote and not self._remote.alive:
            self._out = self._rate = None  # the speaker process died: open a new one
        if self._out is not None and self._rate != rate:
            self._close()
        if self._out is None:
            self._rate = rate
            if self._remote is not None:
                self._q = self._out = self._remote.open(rate, self.device)
            else:
                self._q = _Queue()
                self._out = self._open(rate, self._callback)
        return self._out

    def play(self, samples: np.ndarray, rate: int) -> bool:
        """Blocks until played. Returns False when stopped (now, or earlier in this reply)."""
        with self._lock:
            if self._stop.is_set():
                return False
            self.playing = True
            try:
                to = self._target(rate)
                audio = fade(limit(resample(samples, rate, to)), to)
                self._stream(to)
                under = self.underruns
                self._play_out(audio, to)
                if self.underruns > under:
                    logging.getLogger(__name__).warning("player: the device ran dry mid-sentence")
                return not self._stop.is_set()
            finally:
                self.playing = False

    def _play_out(self, audio: np.ndarray, rate: int, stoppable: bool = True) -> None:
        """Queue it whole, then follow the device; returns when it has all played, a stop has faded it out, or the
        speaker failed (then it is restarted: live at 3:33 PM a frozen speaker kept Maxie "speaking" for minutes)."""
        end = self._q.add(audio)
        if stoppable and self._stop.is_set():  # stopped while it was being queued
            self._q.stop(int(rate * STOP_FADE_MS / 1000))
        q, last, still = self._q, self._q.taken, time.monotonic()
        deadline = still + len(audio) / rate + PLAY_GRACE_S
        while not self.failed:
            taken = q.taken
            if taken >= end:
                return
            now = time.monotonic()
            if taken != last or (q is self._remote and not q.opened):  # still opening: not a stall (the deadline holds)
                last, still = taken, now
            if q is self._remote and not self._remote.alive:
                self.abort("the speaker process died mid-sentence")
            elif now - still > STALL_S:
                self.abort("the output device stopped taking audio")
            elif now > deadline:
                self.abort("a sentence ran 2 s over its length")
            self._moved.clear()
            self._moved.wait(0.01)

    def abort(self, why: str) -> None:
        """The speaker failed: the rest of this reply is dropped (play() returns False until `end()`), and a new
        speaker process starts at once, so the next chime plays. Any thread may call it (the voice loop's watchdog)."""
        logging.getLogger(__name__).warning("player: %s; restarting the speaker", why)
        self.failed = True
        self.failures += 1
        self._stop.set()
        out, self._out, self._rate = self._out, None, None
        self._moved.set()
        if self._remote is not None:
            self._remote.kill()
            self._remote.start()
        elif out is not None:
            try:
                out.stop()
                out.close()
            except Exception as e:  # it is being dropped anyway  # noqa: BLE001
                logging.getLogger(__name__).warning("player: closing the failed stream: %s", e)

    def end(self, tail_s: float = TAIL_S) -> None:
        """The reply is over: silence, then close, so the device never stops on a non-zero sample. A stop is over."""
        with self._lock:
            self._close(tail_s)
            self._stop.clear()
            self.failed = False

    def _close(self, tail_s: float = TAIL_S) -> None:
        out, rate = self._out, self._rate
        if out is None or rate is None:
            return
        try:
            self._play_out(np.zeros(int(rate * tail_s), dtype=np.float32), rate, stoppable=False)  # after any fade
            if self.failed:  # abort() dropped it already
                return
            if out is self._remote:
                out.close()
            else:
                out.stop()
                out.close()
        except Exception as e:  # a dead device must not kill the voice loop  # noqa: BLE001
            logging.getLogger(__name__).warning("player: closing the output stream failed: %s", e)
        finally:
            self._out, self._rate = None, None

    def stop(self) -> None:
        """Fade out what plays, and play nothing more until `end()`. Between replies (no stream) it does nothing:
        a watcher that stops late must not silence the next one. While a sentence is being prepared, it holds."""
        if self._out is not None or self.playing:
            self._stop.set()  # play() fades out at once what it queues next
            if self._out is not None:
                self._q.stop(int((self._rate or 48000) * STOP_FADE_MS / 1000))


def chime(rate: int = 24000) -> np.ndarray:
    """The wake chime: two warm notes (520 then 660 Hz) gliding into each other, 180 ms, peak -12 dBFS.
    Continuous phase, the pitch moves on a raised cosine (no step), soft attack, long smooth release to exact zero."""
    t = np.arange(int(rate * CHIME_S)) / rate
    glide = np.clip((t - (CHIME_S - CHIME_GLIDE_S) / 2) / CHIME_GLIDE_S, 0.0, 1.0)
    freq = CHIME_NOTES[0] + (CHIME_NOTES[1] - CHIME_NOTES[0]) * (0.5 - 0.5 * np.cos(math.pi * glide))
    phase = 2 * math.pi * np.cumsum(freq) / rate
    tone = np.sin(phase) + 0.12 * np.sin(2 * phase)
    attack = 0.5 - 0.5 * np.cos(math.pi * np.minimum(1.0, t / 0.015))
    release = np.clip((CHIME_S - t) / 0.09, 0.0, 1.0) ** 2
    shaped = tone * attack * release
    peak = float(np.abs(shaped).max()) or 1.0
    return fade(shaped * (10 ** (CHIME_DB / 20) / peak), rate, ms=5)


def now() -> float:
    return time.monotonic()
