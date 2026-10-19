"""Playback without clicks or chirps: fades, one stream per reply, silent tail, warm chime."""
import threading

import numpy as np

from maxie.voice.audio import CHIME_DB, CHIME_S, TAIL_S, Player, chime, fade

RATE = 24000
MAX_JUMP = 0.1


class RecordingStream:
    """Stands in for the sounddevice output stream: keeps every sample written, in order."""

    def __init__(self, log, rate):
        self.log, self.rate, self.chunks, self.closed = log, rate, [], False
        log.append(self)

    def write(self, a):
        assert not self.closed
        self.chunks.append(np.asarray(a, dtype=np.float32).reshape(-1))

    def stop(self):
        pass

    def close(self):
        self.closed = True

    @property
    def audio(self):
        return np.concatenate(self.chunks) if self.chunks else np.zeros(0, dtype=np.float32)


def loud_sine(seconds, freq=440.0, amp=0.5):
    """Worst case for clicks: starts and ends at full amplitude (cos), like a chunk cut mid-word."""
    t = np.arange(int(RATE * seconds)) / RATE
    return (amp * np.cos(2 * np.pi * freq * t)).astype(np.float32)


def recording_player():
    streams = []
    return Player(open_stream=lambda rate: RecordingStream(streams, rate)), streams


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
    player, streams = recording_player()
    started, release = threading.Event(), threading.Event()

    class Slow(RecordingStream):  # a real device blocks while it plays; hold after the first chunk
        def write(self, a):
            super().write(a)
            started.set()
            release.wait(1)

    player._open = lambda rate: Slow(streams, rate)
    t = threading.Thread(target=lambda: player.play(loud_sine(3.0), RATE))
    t.start()
    started.wait(1)
    player.stop()
    release.set()
    t.join(2)
    player.end()
    audio = streams[0].audio
    assert len(audio) < RATE * 3 + RATE * TAIL_S  # it was cut short
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
