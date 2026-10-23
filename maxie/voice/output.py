"""The voice helper's speaker, in a process of its own.

The device's realtime callback is Python, so it needs the GIL. In the voice helper, Whisper, Kokoro, the speech
detector and the echo canceller hold it for long enough, now and then, that the callback came too late and the
speakers went silent mid-word (scripts/voice_output_check.py under load: up to 430 clicks and 2 s gaps in 20 replies).
Here the only Python is that callback and the thread reading the pipe, so nothing keeps it waiting.

`python -m maxie.voice.output [--null]` reads commands on stdin and reports on stdout, each one a header
(HEAD: kind, a, b, payload bytes) and a payload:
  in:  O open (a = rate, payload = device name or index, empty = default)    A audio (payload = float32 samples)
       S stop (a = fade frames)    C close (after what is queued has played)
  out: O opened (the device is open: from here on, a block that doesn't come is a stall, not a slow open)
       T taken (a = underflows so far, b = frames the device has taken or dropped, payload = the block it played:
       the echo canceller's reference)    C closed
`--null` plays to nothing, in real time (for tests). `--record PATH:DEVICE` also records that input device for as long
as it runs (scripts/voice_output_check.py: the Teams loopback only comes back whole when the same process plays and
records); PATH gets float32 samples at 48 kHz, PATH.json when the first one was heard.
"""

from __future__ import annotations

import struct
import sys
import threading
import time
from collections import deque

import numpy as np

from .audio import OUT_LATENCY_S, _Queue

HEAD = struct.Struct("<cIQI")
SEND_EVERY_S = 0.005
KINDS, SAMPLES = frozenset(b"OASCT"[i:i + 1] for i in range(5)), (b"A", b"T")
MAX_PAYLOAD = 64 << 20  # a minute of float32 at 48 kHz is 11 MB
PLAYED_MAX = 500  # blocks (~5 s) kept while the voice helper isn't reading
NULL_BLOCK_S = 0.01


def _read(stream, n: int) -> bytes:
    """n bytes, or fewer at the end: an unbuffered pipe returns whatever has arrived, a block can come in pieces."""
    data = b""
    while len(data) < n and (more := stream.read(n - len(data))):
        data += more
    return data


def read_message(stream) -> tuple[bytes, int, int, bytes] | None:
    head = _read(stream, HEAD.size)
    if len(head) < HEAD.size:
        return None
    kind, a, b, n = HEAD.unpack(head)
    if kind not in KINDS or n > MAX_PAYLOAD or (kind in SAMPLES and n % 4):
        raise ValueError(f"speaker pipe out of step: {kind!r}, {n} bytes")
    payload = _read(stream, n)
    if len(payload) < n:
        return None
    return kind, a, b, payload


def message(kind: bytes, a: int = 0, b: int = 0, payload: bytes = b"") -> bytes:
    return HEAD.pack(kind, a, b, len(payload)) + payload


class Speaker:
    def __init__(self, out, null: bool = False):
        self.out, self.null = out, null
        self.lock = threading.Lock()  # the pipe out (never taken by the callback)
        self.q = _Queue()
        self.stream = None
        self.underflows = 0
        self.played: deque[tuple[np.ndarray, int]] = deque(maxlen=PLAYED_MAX)  # the callback appends: atomic
        threading.Thread(target=self._send, daemon=True).start()

    def callback(self, outdata, frames, time_info, status) -> None:
        if status and status.output_underflow:
            self.underflows += 1
        block = outdata[:, 0]
        self.q.take(block)
        self.played.append((block.copy(), self.q.taken))

    def _send(self) -> None:
        while True:
            time.sleep(SEND_EVERY_S)
            while self.played:
                block, taken = self.played.popleft()
                self._write(message(b"T", self.underflows, taken, block.astype("<f4").tobytes()))

    def _write(self, data: bytes) -> None:
        with self.lock:
            try:
                self.out.write(data)
                self.out.flush()
            except (BrokenPipeError, ValueError):
                pass

    def open(self, rate: int, device: str) -> None:
        self.close()
        self.q = _Queue()
        if self.null:
            self.stream = _Null(rate, self.callback)
            return
        import sounddevice as sd
        dev = int(device) if device.isdigit() else (device or None)
        self.stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32", device=dev,
                                      latency=OUT_LATENCY_S, callback=self.callback)
        self.stream.start()

    def close(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None

    def run(self, inp) -> None:
        while (msg := read_message(inp)) is not None:
            kind, a, _, payload = msg
            if kind == b"O":
                self.open(a, payload.decode())
                self._write(message(b"O"))
            elif kind == b"A":
                self.q.add(np.frombuffer(payload, dtype="<f4").copy())
            elif kind == b"S":
                self.q.stop(a)
            elif kind == b"C":
                time.sleep(SEND_EVERY_S * 2)  # the last blocks go back first
                self.close()
                self._write(message(b"C"))
        self.close()


class _Null:
    """No device: takes a block every NULL_BLOCK_S, like one."""

    def __init__(self, rate: int, callback):
        self.rate, self.callback, self.running = rate, callback, True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        n, t = max(1, int(self.rate * NULL_BLOCK_S)), time.monotonic()
        while self.running:
            buf = np.zeros((n, 1), np.float32)
            self.callback(buf, n, None, None)
            t += NULL_BLOCK_S
            time.sleep(max(0.0, t - time.monotonic()))

    def stop(self) -> None:
        self.running = False

    def close(self) -> None:
        pass


def _record(spec: str):
    """For tests: the loopback's input, kept until the speaker stops."""
    import json

    import sounddevice as sd
    path, _, device = spec.rpartition(":")
    blocks: list[tuple[float, np.ndarray]] = []
    stream = sd.InputStream(device=int(device) if device.isdigit() else device, samplerate=48000, channels=1,
                            dtype="float32", callback=lambda d, n, t, s: blocks.append((time.monotonic(), d[:, 0].copy())))
    stream.start()

    def save() -> None:
        stream.stop()
        stream.close()
        if blocks:
            first_t, first = blocks[0]
            np.concatenate([b for _, b in blocks]).astype("<f4").tofile(path)
            with open(path + ".json", "w") as f:
                json.dump({"t0": first_t - len(first) / 48000}, f)
    return save


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    save = _record(argv[argv.index("--record") + 1]) if "--record" in argv else None
    Speaker(sys.stdout.buffer, null="--null" in argv).run(sys.stdin.buffer)
    if save:
        save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
