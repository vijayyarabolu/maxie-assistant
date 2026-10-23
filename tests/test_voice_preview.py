"""hear a voice before choosing it. "Hi Alex, I'm Maxie." in any Kokoro voice and speed, as a clean
24 kHz WAV (10 ms fades, no chirp), cached per (voice, speed); Kokoro loads only when needed and unloads when idle."""

import io
import time
import wave

import numpy as np
import pytest
from test_settings_api import Deps, H, client

from maxie.voice import preview as vp
from maxie.voice.audio import PEAK_DBFS, SPEECH_DBFS


class Engine:
    """Stands in for Kokoro: a loud tone (so a missing fade shows), at whatever rate it is given."""

    def __init__(self, rate=24000):
        self.rate, self.calls, self.loaded, self.unloaded = rate, [], 0, 0

    def load(self):
        self.loaded += 1

    def unload(self):
        self.unloaded += 1

    def say(self, text, voice, speed):
        self.calls.append((text, voice, speed))
        t = np.arange(int(self.rate * 0.5)) / self.rate
        return (0.8 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), self.rate


def samples(data: bytes) -> tuple[np.ndarray, int, int, int]:
    with wave.open(io.BytesIO(data)) as w:
        raw = w.readframes(w.getnframes())
        return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32767, w.getframerate(), w.getnchannels(), w.getsampwidth()


def test_a_clean_24k_mono_wav_with_soft_edges(tmp_path):
    p = vp.VoicePreview(tmp_path, engine=Engine(), name="Alex")
    x, rate, channels, width = samples(p.wav("bm_fable", 1.2))
    assert (rate, channels, width) == (24000, 1, 2)
    fade = int(24000 * vp.FADE_S)
    assert abs(x[0]) < 0.01 and abs(x[-1]) < 0.01, "starts and ends at silence: no click"
    assert np.abs(x[:fade]).max() < np.abs(x[fade:2 * fade]).max() + 1e-6
    rms = 20 * np.log10(np.sqrt((x[fade:-fade] ** 2).mean()))
    assert abs(rms - SPEECH_DBFS) < 0.5 and np.abs(x).max() < 10 ** (PEAK_DBFS / 20), "levelled like the voice helper"


def test_says_hi_by_name_and_caches_per_voice_and_speed(tmp_path):
    e = Engine()
    p = vp.VoicePreview(tmp_path, engine=e, name="Alex")
    a = p.wav("af_heart", 1.05)
    assert p.wav("af_heart", 1.05) == a and len(e.calls) == 1, "cached"
    p.wav("af_heart", 1.2)
    p.wav("am_onyx", 1.05)
    assert [c[1:] for c in e.calls] == [("af_heart", 1.05), ("af_heart", 1.2), ("am_onyx", 1.05)]
    assert e.calls[0][0] == "Hi Alex, I'm Maxie."
    assert len(list(tmp_path.glob("*.wav"))) == 3
    assert vp.VoicePreview(tmp_path, engine=Engine(), name="Alex").wav("af_heart", 1.05) == a, "cache survives restarts"


def test_other_rates_are_resampled_to_24k(tmp_path):
    x, rate, _, _ = samples(vp.VoicePreview(tmp_path, engine=Engine(rate=22050), name="V").wav("af_sky", 1.0))
    assert rate == 24000 and abs(len(x) - 12000) <= 2


@pytest.mark.parametrize("voice,speed", [("xx_nobody", 1.0), ("af_heart", 3.0), ("af_heart", 0.1), ("../etc", 1.0)])
def test_only_real_voices_and_speeds(tmp_path, voice, speed):
    with pytest.raises(ValueError):
        vp.VoicePreview(tmp_path, engine=Engine(), name="V").wav(voice, speed)


def test_kokoro_loads_lazily_and_unloads_when_idle(tmp_path):
    e = Engine()
    p = vp.VoicePreview(tmp_path, engine=e, name="V", idle_s=0.05)
    assert e.loaded == 0
    p.wav("af_heart", 1.0)
    assert e.loaded == 1
    deadline = time.monotonic() + 3  # under `taskpolicy -b` the idle timer can fire late; a fixed 0.2 s sleep was flaky
    while e.unloaded == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert e.unloaded == 1, "unloaded after idle"
    p.wav("af_heart", 1.0)
    assert e.loaded == 1, "a cached sample needs no model"


def test_preview_route_returns_wav_and_checks_input(make_services, tmp_path):
    c, s = client(make_services, Deps())
    s.voice_preview = vp.VoicePreview(tmp_path, engine=Engine(), name="Alex")
    r = c.post("/api/voice/preview", json={"voice": "bf_lily", "speed": 0.9}, headers=H)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert samples(r.content)[1] == 24000
    assert c.post("/api/voice/preview", json={"voice": "nope", "speed": 1}, headers=H).status_code == 400
    assert c.post("/api/voice/preview", json={"voice": "bf_lily", "speed": 1}).status_code == 401
