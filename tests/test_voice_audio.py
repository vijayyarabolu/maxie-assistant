"""Playback without clicks or chirps: fades, one stream per reply, silent tail, warm chime."""
import threading
import time
import types

import numpy as np

from maxie.voice.audio import CHIME_DB, CHIME_S, TAIL_S, Player, chime, fade

RATE = 24000
MAX_JUMP = 0.1


class FakeDevice:
    """Stands in for the sounddevice output stream: pulls through the Player's callback a block at a time (20x faster
    than real time) and keeps every sample it was handed, in order. `hold`: (started, release) events, it waits after
    the first block with sound in it, like a device busy playing; `under_at`: blocks reported late (an underflow)."""

    def __init__(self, log, rate, callback, block=240, hold=None, under_at=()):
        self.rate, self.callback, self.block, self.hold, self.under_at = rate, callback, block, hold, set(under_at)
        self.chunks, self.closed, self.running = [], False, True
        log.append(self)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while self.running:
            buf = np.zeros((self.block, 1), np.float32)
            status = types.SimpleNamespace(output_underflow=len(self.chunks) + 1 in self.under_at)
            self.callback(buf, self.block, None, status)
            self.chunks.append(buf[:, 0].copy())
            if self.hold is not None and not self.hold[0].is_set() and buf.any():
                self.hold[0].set()
                self.hold[1].wait(1)
            time.sleep(self.block / self.rate / 20)

    def stop(self):
        self.running = False
        self.thread.join(1)

    def close(self):
        self.closed = True

    @property
    def audio(self):
        return np.concatenate(self.chunks) if self.chunks else np.zeros(0, dtype=np.float32)


def sound(a):
    """From the first to the last non-zero sample: what was played, without the device's idle blocks around it."""
    nz = np.nonzero(a)[0]
    return a[nz[0]:nz[-1] + 1] if len(nz) else a[:0]


def loud_sine(seconds, freq=440.0, amp=0.5):
    """Worst case for clicks: starts and ends at full amplitude (cos), like a chunk cut mid-word."""
    t = np.arange(int(RATE * seconds)) / RATE
    return (amp * np.cos(2 * np.pi * freq * t)).astype(np.float32)


def recording_player(**kw):
    streams = []
    return Player(open_stream=lambda rate, cb: FakeDevice(streams, rate, cb, **kw)), streams


def test_fade_returns_a_copy_that_starts_and_ends_at_zero():
    s = loud_sine(0.2)
    out = fade(s, RATE)
    assert out[0] == 0 and abs(out[-1]) < 1e-6 and s[0] == 0.5  # the input is untouched
    n = int(RATE * 0.01)
    assert np.allclose(out[n:-n], s[n:-n])  # only the 10 ms edges change


def test_a_full_reply_is_one_stream_with_no_jumps_and_a_silent_tail():
    player, streams = recording_player()
    for seconds in (0.4, 1.1, 0.7):  # ack + two sentences, each ending abruptly
        assert player.play(loud_sine(seconds), RATE)
    player.end()
    assert len(streams) == 1 and streams[0].closed  # no open/close between sentences
    audio = streams[0].audio
    tail = audio[-int(RATE * TAIL_S):]
    assert np.abs(tail).max() == 0.0
    assert np.abs(np.diff(audio)).max() <= MAX_JUMP  # no click anywhere, including the joins


def test_barge_in_fades_out_instead_of_cutting():
    started, release = threading.Event(), threading.Event()
    player, streams = recording_player(hold=(started, release))
    t = threading.Thread(target=lambda: player.play(loud_sine(3.0), RATE))
    t.start()
    started.wait(1)
    player.stop()
    release.set()
    t.join(2)
    player.end()
    audio = streams[0].audio
    assert len(sound(audio)) < RATE * 0.2  # it was cut short, at once
    assert np.abs(np.diff(audio)).max() <= MAX_JUMP and np.abs(audio[-int(RATE * TAIL_S):]).max() == 0


def test_end_without_playing_does_nothing_and_a_new_rate_reopens():
    player, streams = recording_player()
    player.end()
    assert streams == []
    player.play(loud_sine(0.1), 24000)
    player.play(loud_sine(0.1), 22050)  # Apple `say` fallback rate
    player.end()
    assert [s.rate for s in streams] == [24000, 22050] and all(s.closed for s in streams)


def test_chime_is_two_warm_notes_soft_and_smooth():
    c = chime(RATE)
    assert abs(len(c) / RATE - CHIME_S) < 0.002
    assert abs(float(np.abs(c).max()) - 10 ** (CHIME_DB / 20)) < 0.01  # -12 dBFS peak
    assert c[0] == 0 and abs(c[-1]) < 1e-6 and np.abs(np.diff(c)).max() < MAX_JUMP

    def dominant(a):
        spectrum = np.abs(np.fft.rfft(a * np.hanning(len(a)), n=RATE))
        return int(np.argmax(spectrum))

    half = len(c) // 2
    assert abs(dominant(c[:half]) - 520) <= 15 and abs(dominant(c[half:]) - 660) <= 15


def test_the_settings_test_line_is_the_one_the_voice_helper_caches():
    from pathlib import Path

    from maxie.voice.speech import TEST_LINE
    swift = Path(__file__).parents[1] / "mac/ClaudeNotch/Sources/Settings/VoiceSection.swift"
    assert f'"text": "{TEST_LINE}"' in swift.read_text()


def test_segmenter_reports_a_short_pause_and_the_audio_so_far():
    from maxie.voice.audio import FRAME, Segmenter
    seg = Segmenter()
    for i in range(20):
        seg.feed(np.full(FRAME, 0.9, np.float32), 0.9, i * 0.032)
    for i in range(7):
        seg.feed(np.zeros(FRAME, np.float32), 0.0, (20 + i) * 0.032)
    assert seg.in_speech and 0.2 <= seg.trailing_silence_s() < 0.6
    assert len(seg.snapshot()) == len(seg._frames) * FRAME


def test_the_mic_wakes_the_loop_every_64_ms_and_still_hands_it_32_ms_frames(monkeypatch):
    # the voice helper's ~4 % CPU was mostly waking 31 times a second, not the speech model (0.3 %)
    import sys
    import types

    from maxie.voice.audio import FRAME, Mic
    opened = {}

    class Stream:
        def __init__(self, **kw):
            opened.update(kw)

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=Stream))
    mic = Mic()
    block = np.arange(opened["blocksize"], dtype=np.float32).reshape(-1, 1)

    mic._cb(block, len(block), None, None)

    assert opened["blocksize"] == 2 * FRAME
    frames = [mic.q.get_nowait() for _ in range(2)]
    assert [len(f) for f in frames] == [FRAME, FRAME] and frames[1][0] == FRAME and mic.q.empty()


def test_a_closed_mic_stops_the_device_once_and_reopens_when_frames_are_wanted(monkeypatch):
    # muted, the device stops (macOS's orange dot goes); the panel's mic button still gets frames
    import sys
    import types

    from maxie.voice.audio import FRAME, Mic
    calls = []

    class Stream:
        def __init__(self, **kw):
            pass

        def stop(self):
            calls.append("stop")

        def start(self):
            calls.append("start")

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=Stream))
    mic = Mic()
    mic.close_mic()
    mic.close_mic()
    mic.q.put_nowait(np.zeros(FRAME, np.float32))

    frame = next(mic.frames())

    assert calls == ["stop", "start"] and len(frame) == FRAME and not mic.closed
