"""smooth and clear output. Stops fade out (30-80 ms), chunks join on zero, a clean resampler to the
device's own rate, nothing over -1 dBFS, levelled speech, the echo canceller never touches what he plays, the chime
never plays over him, and a stop isn't lost between sentences."""

import threading
import time

import numpy as np
import pytest
from test_voice_audio import FakeDevice, sound

from maxie.voice.audio import (
    LEVEL_PEAK_DBFS,
    PEAK_DBFS,
    SPEECH_DBFS,
    STOP_FADE_MS,
    TAIL_S,
    Player,
    chime,
    fade,
    level,
    resample,
)
from maxie.voice.loop import Turn

RATE = 24000


def player(out_rate=None, on_write=None, **kw):
    streams = []
    p = Player(open_stream=lambda rate, cb: FakeDevice(streams, rate, cb, **kw), out_rate=out_rate, on_write=on_write)
    return p, streams


def sine(seconds, freq=440.0, amp=0.5, rate=RATE):
    t = np.arange(int(rate * seconds)) / rate
    return (amp * np.cos(2 * np.pi * freq * t)).astype(np.float32)


def stopped_mid_word(p, seconds=3.0):
    """Plays a long loud sine and stops it once the device has its first block of it."""
    t = threading.Thread(target=lambda: p.play(sine(seconds), RATE))
    t.start()
    return t


def test_a_stop_fades_him_out_over_30_to_80_ms_with_no_jump():
    started, release = threading.Event(), threading.Event()
    p, streams = player(hold=(started, release))
    t = stopped_mid_word(p)
    started.wait(1)
    p.stop()
    release.set()
    t.join(2)
    p.end()
    audio, block = streams[0].audio, streams[0].block
    held = next(i for i, c in enumerate(streams[0].chunks) if c.any())  # the block playing when he was stopped
    fade = np.concatenate(streams[0].chunks[held + 1:])
    fade = fade[:np.nonzero(fade)[0][-1] + 1]
    tail = audio[np.nonzero(audio)[0][-1] + 1:]
    assert 30 <= STOP_FADE_MS <= 80 and abs(len(fade) / RATE * 1000 - STOP_FADE_MS) <= block / RATE * 1000
    assert np.abs(np.diff(audio)).max() <= np.abs(np.diff(sine(0.1))).max() + 1e-6, "no step anywhere"
    assert np.abs(fade[-24:]).max() < 0.002 and len(tail) >= int(RATE * TAIL_S) - block and not tail.any()


def test_a_stop_between_sentences_holds_until_the_reply_is_over():
    # Under load: the stop landed while the next sentence was still being made, and play() forgot it
    p, streams = player()
    assert p.play(sine(0.2), RATE)
    p.stop()
    assert not p.play(sine(0.2), RATE), "the next sentence doesn't start"
    p.end()
    assert p.play(sine(0.2), RATE), "the next reply plays"
    p.end()
    one = int(RATE * 0.2) - 2  # a faded chunk starts and ends on exact zero
    assert len(sound(streams[0].audio)) == one and len(sound(streams[1].audio)) == one


def test_a_watcher_that_stops_after_the_reply_ended_doesnt_silence_the_next_one():
    p, _ = player()
    p.play(sine(0.1), RATE)
    p.end()
    p.stop()  # the pause watcher's last poll, after the reply's end
    assert p.play(chime(RATE), RATE)
    p.end()


def test_the_device_gets_its_own_rate_through_a_clean_resampler():
    p, streams = player(out_rate=48000)
    p.play(sine(1.0, freq=11000.0, amp=0.3), RATE)  # Kokoro's top octave: its image lands at 13 kHz
    p.end()
    assert [s.rate for s in streams] == [48000]
    y = sound(streams[0].audio)[2400:48000 - 2400]
    spec = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    f = np.fft.rfftfreq(len(y), 1 / 48000)
    image = 20 * np.log10(spec[(f > 12500)].max() / spec.max())
    assert image < -90, f"the 13 kHz image is {image:.0f} dB under the tone"


def test_nothing_leaves_the_player_over_minus_1_dbfs():
    # Kokoro peaks at -0.6 dBFS; resampled to 48 kHz its overshoot reached +0.6 dBFS and clipped
    hot = np.clip(np.random.default_rng(3).standard_normal(RATE) * 0.5, -0.99, 0.99).astype(np.float32)
    p, streams = player(out_rate=48000)
    p.play(hot, RATE)
    p.end()
    assert np.abs(streams[0].audio).max() <= 10 ** (PEAK_DBFS / 20) + 1e-6


def test_speech_is_levelled_and_its_pauses_dont_count():
    words = np.concatenate([sine(0.5, amp=0.9), np.zeros(RATE, np.float32), sine(0.5, amp=0.9)])
    out = level(words, RATE)
    voiced = out[np.abs(words) > 0]
    assert abs(20 * np.log10(np.sqrt((voiced ** 2).mean())) - SPEECH_DBFS) < 0.2
    assert np.abs(out).max() <= 10 ** (LEVEL_PEAK_DBFS / 20) + 1e-6
    spiky = sine(0.5, amp=0.05)
    spiky[100] = 0.99  # a peak far over the speech: the peak wins, never clipped
    assert np.abs(level(spiky, RATE)).max() <= 10 ** (LEVEL_PEAK_DBFS / 20) + 1e-6
    assert level(np.zeros(100, np.float32), RATE).max() == 0


def test_resample_keeps_length_and_tone():
    y = resample(sine(1.0, freq=1000.0), RATE, 44100)
    assert len(y) == 44100 and abs(int(np.argmax(np.abs(np.fft.rfft(y)))) - 1000) <= 1
    x = sine(0.1)
    assert resample(x, RATE, RATE) is x


def test_the_echo_canceller_cannot_change_what_he_plays_and_hears_all_of_it():
    fed = []

    def canceller(samples, rate):
        fed.append(samples.copy())
        samples.fill(0.0)  # whatever it does to its reference

    p, streams = player(on_write=canceller)
    p.play(sine(0.5), RATE)
    p.end()
    played = streams[0].audio
    assert np.abs(played).max() > 0.4, "the canceller got its own copy"
    reference = np.concatenate(fed)
    assert np.allclose(sound(reference), sound(played)), "its reference is exactly what played"


def test_the_chime_never_plays_over_him():
    p, streams = player()
    reply = sine(0.5, freq=300.0)
    t = threading.Thread(target=lambda: p.play(reply, RATE))
    t.start()
    time.sleep(0.005)
    p.play(chime(RATE), RATE)  # the wake path, while a reply is still playing
    t.join(2)
    p.end()
    expected = np.concatenate([fade(reply, RATE), fade(chime(RATE), RATE)])
    assert np.allclose(sound(streams[0].audio), sound(expected), atol=1e-6), "the chime only after him, whole"


def test_the_chime_glides_from_note_to_note_without_a_step():
    from scipy.signal import hilbert
    c = chime(RATE).astype(np.float64)
    spec = np.fft.rfft(c, n=8 * len(c))
    f = np.fft.rfftfreq(8 * len(c), 1 / RATE)
    spec[(f < 350) | (f > 850)] = 0  # the fundamental alone (its 2nd harmonic ripples the pitch track)
    fundamental = np.fft.irfft(spec)[:len(c)]
    inst = np.diff(np.unwrap(np.angle(hilbert(fundamental)))) * RATE / (2 * np.pi)
    ms = RATE // 1000
    smooth = np.convolve(inst[int(0.03 * RATE):int(0.13 * RATE)], np.ones(ms) / ms, mode="valid")
    assert np.abs(np.diff(smooth[::ms])).max() < 20, "pitch moves at most 20 Hz per ms (the old step: 64)"
    assert c[0] == 0 and abs(c[-1]) < 1e-9


def test_a_device_that_runs_dry_mid_sentence_is_counted_and_logged(caplog):
    p, _ = player(under_at={5})
    p.play(sine(0.5), RATE)
    p.end()
    assert p.underruns == 1 and sum("mid-sentence" in r.message for r in caplog.records) == 1


def test_a_stop_while_the_next_sentence_is_being_made_drops_it():
    from test_voice import FakePlayer, SlowTTS, make_loop
    loop, _ = make_loop([], [], [{"type": "text", "text": "One. Two. "}, {"type": "done", "text": "One. Two."}])
    loop.tts = SlowTTS()

    class StoppedAfterOne(FakePlayer):
        def play(self, samples, rate):
            threading.Timer(0.1, lambda: setattr(loop, "_interrupted", True)).start()  # ⌥⇧P while "Two." is made
            return super().play(samples, rate)

    loop.player = StoppedAfterOne()
    loop.handle("count", Turn(heard_at=loop.clock()))
    assert len(loop.player.played) == 1


@pytest.mark.parametrize("rate", [24000, 22050])
def test_tts_output_is_levelled_whatever_the_engine(rate, monkeypatch):
    from maxie.voice import speech
    tts = speech.TTS.__new__(speech.TTS)
    tts.cache, tts.kokoro, tts.available, tts.voice, tts.speed = {}, None, False, "af_jessica", 1.05
    monkeypatch.setattr(speech.TTS, "_say", staticmethod(lambda text: (sine(0.5, amp=0.99, rate=rate), rate)))
    out, r = tts.synth("Hello there.")
    assert r == rate and np.abs(out).max() <= 10 ** (LEVEL_PEAK_DBFS / 20) + 1e-6


def test_the_speaker_process_plays_stops_and_hands_back_what_it_played():
    import sys
    reference = []
    p = Player(speaker=[sys.executable, "-m", "maxie.voice.output", "--null"], out_rate=48000,
               on_write=lambda samples, rate: reference.append(samples))
    assert p.play(sine(0.3), RATE), "played to the end"
    t = threading.Thread(target=lambda: p.play(sine(3.0), RATE))
    t.start()
    time.sleep(0.3)
    started = time.monotonic()
    p.stop()
    t.join(3)
    assert time.monotonic() - started < 0.5, "a stop fades him out at once, there too"
    p.end()
    assert p.play(chime(RATE), RATE), "the next reply plays"
    p.end()
    played = np.concatenate(reference)
    assert 0.3 * 48000 < np.count_nonzero(played) < 1.0 * 48000, "the first sentence whole, the second cut"
    assert np.abs(np.diff(played)).max() < 0.05, "no step anywhere: the stop faded"
    p._remote.proc.stdin.close()


def test_a_message_split_across_pipe_reads_is_read_whole():
    # the voice helper reads the speaker's pipe unbuffered: a 1.9 KB block can come back a few bytes at a time
    import io

    from maxie.voice.output import message, read_message

    class Dribble(io.RawIOBase):
        def __init__(self, data):
            self.data = data

        def read(self, n=-1):
            out, self.data = self.data[:min(n, 7)], self.data[min(n, 7):]
            return out

    block = sine(0.01, rate=48000)
    stream = Dribble(message(b"T", 2, 480, block.tobytes()) + message(b"C"))
    kind, a, b, payload = read_message(stream)
    assert (kind, a, b) == (b"T", 2, 480) and np.array_equal(np.frombuffer(payload, "<f4"), block)
    assert read_message(stream)[0] == b"C" and read_message(stream) is None


def test_a_stop_while_his_first_sentence_is_being_prepared_is_kept(monkeypatch):
    # "Maxie, stop" while the first sentence is still being resampled, before the device is open
    from maxie.voice import audio
    p, streams = player()
    real = audio.resample
    monkeypatch.setattr(audio, "resample", lambda *a: (p.stop(), real(*a))[1])
    assert not p.play(sine(1.0), RATE), "it reports the stop"
    p.end()
    assert np.count_nonzero(streams[0].audio) < 0.1 * streams[0].rate, "faded out at once, not played whole"


def test_a_dead_speaker_process_doesnt_hold_him_up():
    import sys
    p = Player(speaker=[sys.executable, "-c", "pass"], out_rate=48000)
    started = time.monotonic()
    p.play(sine(0.3), RATE)
    p.play(sine(0.3), RATE)
    p.end()
    assert time.monotonic() - started < 1.5, "no 2 s stall per sentence waiting on a device that's gone"


# Live at 3:33 PM: a played block came back to the voice helper in pieces, its reader thread died, every sentence
# then waited out a stall and still "played", and Maxie stayed speaking, logging each "Hey Maxie" as "over her".

DRIBBLER = """
import sys, time
from maxie.voice.output import message, read_message
taken, out = 0, sys.stdout.buffer
while (msg := read_message(sys.stdin.buffer)) is not None:
    kind, a, b, payload = msg
    if kind == b"A":
        taken += len(payload) // 4
        data = message(b"T", 0, taken, payload)
        for i in range(0, len(data), 997):  # the block comes back in pieces, as a pipe may hand it over
            out.write(data[i:i + 997]); out.flush(); time.sleep(0.0005)
    elif kind == b"C":
        out.write(message(b"C")); out.flush()
"""
SWALLOW = """
import sys
from maxie.voice.output import message, read_message
while (msg := read_message(sys.stdin.buffer)) is not None:
    if msg[0] == b"O":  # the device opens, then never takes a block: a dead device
        sys.stdout.buffer.write(message(b"O")); sys.stdout.buffer.flush()
"""
HUNG = "import time\ntime.sleep(60)"  # reads nothing: its pipe fills and the voice helper's write blocks


def test_the_voice_helper_reads_a_block_that_arrives_in_pieces():
    import sys
    reference = []
    p = Player(speaker=[sys.executable, "-c", DRIBBLER], out_rate=48000,
               on_write=lambda samples, rate: reference.append(samples))
    assert p.play(sine(0.5), RATE) and p.play(sine(0.5), RATE)
    p.end()
    played = np.concatenate(reference)
    assert p.failures == 0 and p._remote.alive, "the reader thread is still following the speaker"
    assert len(played) == int((1.0 + TAIL_S) * 48000) and np.abs(played[:48000]).max() > 0.1


def test_a_speaker_process_killed_mid_sentence_is_dropped_within_a_second_and_restarted():
    import sys
    p = Player(speaker=[sys.executable, "-m", "maxie.voice.output", "--null"], out_rate=48000)
    result = {}
    t = threading.Thread(target=lambda: result.update(ok=p.play(sine(3.0), RATE)))
    t.start()
    time.sleep(0.3)
    dead, killed = p._remote.proc, time.monotonic()
    dead.kill()
    t.join(3)
    assert time.monotonic() - killed < 1.0 and result["ok"] is False, "the rest of the reply is dropped at once"
    p.end()
    assert p.failures == 1 and p._remote.alive and p._remote.proc is not dead, "a new speaker, already running"
    assert p.play(sine(0.2), RATE), "the next sentence (the chime) plays on it"
    p.end()
    p._remote.kill()


def test_a_device_that_stops_taking_audio_is_given_up_within_a_second():
    import sys
    p = Player(speaker=[sys.executable, "-c", SWALLOW], out_rate=48000)
    started = time.monotonic()
    assert not p.play(sine(3.0), RATE), "reported as cut short, so the loop stops speaking"
    assert time.monotonic() - started < 1.0
    assert not p.play(sine(1.0), RATE), "nothing more of this reply"
    p.end()
    assert time.monotonic() - started < 1.5 and p.failures == 1 and not p.failed
    p._remote.kill()


def test_a_failed_speaker_ends_the_reply_within_a_second_and_the_next_wake_chimes():
    from test_voice import FakePlayer, make_loop

    class Failing(FakePlayer):
        failed = False

        def play(self, samples, rate):
            if self.failed or not self.played:  # the first sentence finds the speaker gone
                self.failed = True
                self.played.append(0)
                return False
            return super().play(samples, rate)

        def end(self, tail_s=TAIL_S):
            self.failed = False

    def slow_reply(text, screen=False):  # one sentence, then the hub works on a tool for 5 s
        yield {"type": "ack", "text": "On it."}
        time.sleep(5)
        yield {"type": "done", "text": "Done."}

    loop, hub = make_loop([], [], [])
    loop.player, hub.chat = Failing(), slow_reply
    started = time.monotonic()
    loop.handle("what's on my calendar", Turn(heard_at=loop.clock()))
    assert time.monotonic() - started < 1.0 and hub.states[-1] == "idle", "out of speaking within 1 s"
    loop._wake_now()  # "Hey Maxie" again
    assert loop.player.played[-1] > 0 and hub.states[-1] == "listen", "the chime plays, he's listening"


def test_a_stuck_player_never_keeps_him_speaking_past_the_reply_plus_2_s():
    import sys

    from test_voice import make_loop

    from maxie.voice.loop import SPEAK_GRACE_S
    loop, hub = make_loop([], [], [{"type": "ack", "text": "On it."}, {"type": "done", "text": "On it."}])
    loop.tts.synth = lambda text: (sine(1.0), RATE)  # 1 s of him: 192 KB, more than the stuck pipe takes
    loop.player = Player(speaker=[sys.executable, "-c", HUNG], out_rate=48000)
    started = time.monotonic()
    loop.handle("hello", Turn(heard_at=loop.clock()))
    assert time.monotonic() - started < 1.0 + SPEAK_GRACE_S + TAIL_S + 0.5, "the watchdog cut it"
    assert hub.states[-1] == "idle" and loop.player.failures >= 1 and loop.player._remote.alive
    loop.player._remote.kill()


SLOW_OPEN = DRIBBLER.replace('    if kind == b"A":', '''    if kind == b"O":
        time.sleep(1.5)  # AirPods, or a cold device under load: opening takes a while
        out.write(message(b"O")); out.flush()
    if kind == b"A":''')


def test_a_device_that_is_slow_to_open_isnt_taken_for_a_stuck_one():
    # the crash test's first run: a cold open under load took over 0.9 s, and the chime was dropped as "stuck"
    import sys
    p = Player(speaker=[sys.executable, "-c", SLOW_OPEN], out_rate=48000)
    assert p.play(chime(RATE), RATE), "the chime (35 KB: its write never waits) played once the device was open"
    p.end()
    assert p.failures == 0
    p._remote.kill()
