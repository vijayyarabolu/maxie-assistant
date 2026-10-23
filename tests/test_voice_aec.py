"""on the built-in speakers the mic hears Maxie louder than him. The echo canceller takes out what she is
playing while she plays it, and leaves the mic untouched the rest of the time."""

import sys
import time
import types

import numpy as np
import pytest

from maxie.voice import aec
from maxie.voice.audio import FRAME, Mic, Player

pytest.importorskip("livekit")


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _voice(seconds: float, seed: int) -> np.ndarray:
    """Speech-like sound: noise shaped by a syllable-rate envelope."""
    rng = np.random.default_rng(seed)
    n = int(seconds * aec.RATE)
    t = np.arange(n) / aec.RATE
    return (rng.standard_normal(n) * 0.2 * (0.5 + 0.5 * np.sin(2 * np.pi * 4 * t))).astype(np.float32)


def test_her_echo_is_cut_while_she_speaks():
    # Arrange: what she plays comes back 30 ms later, a little quieter
    clock = Clock()
    c = aec.EchoCanceller(clock=clock)
    far = _voice(4.0, 1)
    delay = int(0.03 * aec.RATE)
    near = np.concatenate([np.zeros(delay, np.float32), far * 0.6])[:len(far)]

    # Act: 64 ms at a time, the way the Player writes and the mic hears
    out = []
    for i in range(0, len(far) - FRAME + 1, FRAME):
        c.render(far[i:i + FRAME], aec.RATE)
        out.append(c.capture(near[i:i + FRAME]))
        clock.t += FRAME / aec.RATE
    out = np.concatenate(out)

    # Assert: after a second to learn the room, the echo is well down
    later = slice(2 * aec.RATE, len(out))
    cut_db = 10 * np.log10(np.mean(near[later] ** 2) / (np.mean(out[later] ** 2) + 1e-12))
    assert cut_db > 15, cut_db
    assert all(len(f) == FRAME for f in np.split(out, len(out) // FRAME)), "every frame stays a whole frame"


def test_the_mic_is_untouched_when_she_is_quiet():
    clock = Clock()
    c = aec.EchoCanceller(clock=clock)
    frame = _voice(FRAME / aec.RATE, 2)
    assert c.capture(frame) is frame, "nothing played yet"
    c.render(_voice(0.1, 3), 24000)
    clock.t += aec.ACTIVE_S + 0.1
    assert c.capture(frame) is frame and not c.active, "back to plain frames after her tail"


def test_the_player_hands_the_canceller_every_block_the_device_takes():
    heard = []

    from test_voice_audio import FakeDevice

    devices = []
    player = Player(open_stream=lambda rate, cb: FakeDevice(devices, rate, cb),
                    on_write=lambda samples, rate: heard.append((samples, rate)))
    player.play(np.ones(24000, np.float32), 24000)
    player.end()
    reference = np.concatenate([s for s, _ in heard])
    assert {r for _, r in heard} == {24000}
    assert np.array_equal(reference, devices[0].audio[:len(reference)]), "block for block, what the device took"
    assert len(reference) >= 24000 + int(24000 * 0.3)


def test_the_mic_cleans_each_frame_as_it_is_heard(monkeypatch):
    class Stream:
        def __init__(self, **kw):
            pass

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=Stream))
    mic = Mic(filter=lambda f: f * 0)
    mic._cb(np.ones((FRAME * 2, 1), np.float32), FRAME * 2, None, None)
    assert [float(np.abs(mic.q.get_nowait()).sum()) for _ in range(2)] == [0.0, 0.0]


def test_without_livekit_there_is_no_canceller(monkeypatch):
    monkeypatch.setitem(sys.modules, "livekit", None)
    said = []
    assert aec.create(said.append) is None and "no echo canceller" in said[0]


def test_in_headphones_the_canceller_lets_every_frame_through():
    # asked on the mic's thread at most once a second, never on the device's
    clock, on, asked = Clock(), [False], []
    c = aec.EchoCanceller(clock=clock, wanted=lambda: asked.append(1) or on[0])
    frame = _voice(FRAME / aec.RATE, 7)
    c.render(_voice(0.5, 3), 24000)
    assert c.capture(frame) is frame and not c._pending, "in headphones: untouched, and her blocks dropped"
    on[0] = True
    c.render(_voice(0.5, 3), 24000)
    assert c.capture(frame) is frame and len(asked) == 1, "not asked again within the second"
    clock.t += 1.0
    c.render(_voice(0.5, 3), 24000)
    assert c.capture(frame) is not frame and len(asked) == 2, "on the speakers: cancelled"


def test_render_only_queues_on_the_devices_thread():
    c = aec.EchoCanceller(clock=Clock())
    with c.lock:  # the mic's thread is busy cleaning a frame
        c.render(_voice(0.1, 3), 24000)  # must not wait for it
    assert len(c._pending) == 1


@pytest.mark.parametrize("rate", [48000, 44100, 24000])
def test_the_reference_is_resampled_as_one_stream_without_seams(rate):
    # the Player hands over ~10 ms at a time; resampled piece by piece, every piece left a seam and the
    # reference stopped matching the echo ("Maxie, stop" over her heard 3 times in 10 instead of 6-8)
    t = np.arange(rate) / rate
    x = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    pieces = aec.Resampler(rate)
    joined = np.concatenate([pieces(x[i:i + rate // 100]) for i in range(0, len(x), rate // 100)])
    assert len(joined) == 16000 and np.allclose(joined, aec.Resampler(rate)(x), atol=1e-6)


@pytest.mark.parametrize("rate", [48000, 44100, 24000])
def test_the_reference_resampler_keeps_up_with_any_device_rate(rate):
    # a 44.1 kHz device: zero-stuffing 160× and a 10k-tap convolution took 120 ms per 10 ms block on the mic's thread
    pieces, block = aec.Resampler(rate), np.zeros(rate // 100, np.float32)
    started = time.perf_counter()
    for _ in range(100):
        pieces(block)
    assert time.perf_counter() - started < 0.2, "a second of audio in well under a second"
