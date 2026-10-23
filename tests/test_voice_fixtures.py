"""Real audio through the real ears: silero VAD, Whisper base on MLX and the wake grammar, on recorded
clips of every wake form in three voices, "max" as an ordinary word, and Maxie's own voice heard back.
Only the hub, the speakers and the speaker check are faked. Mac only (MLX); skipped elsewhere."""

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("mlx_whisper")
pytest.importorskip("silero_vad")
sf = pytest.importorskip("soundfile")

from test_voice import FakeHub, FakePlayer, FakeTTS  # noqa: E402
from test_voice_wake_loop import FakeSpeaker  # noqa: E402

from maxie.voice.audio import FRAME, RATE, VAD  # noqa: E402
from maxie.voice.loop import VoiceLoop  # noqa: E402
from maxie.voice.speech import STT  # noqa: E402

CLIPS = Path(__file__).parent / "fixtures" / "voice"
WAKES = sorted(CLIPS.glob("wake_*.wav"))
NOT_WAKES = sorted(CLIPS.glob("not_*.wav"))
COMMANDS = {"what-s-on-my-calendar": "calendar", "what-time-is-it": "time"}


class WavEars:
    """A clip as the mic would hand it over: room hiss before and after, 32 ms frames, then the loop stops."""

    def __init__(self, audio: np.ndarray, loop_ref: list, lead_s: float = 0.6, tail_s: float = 9.5):
        rng = np.random.default_rng(0)
        hiss = lambda s: (rng.standard_normal(int(s * RATE)) * 1e-3).astype(np.float32)  # noqa: E731
        self.audio = np.concatenate([hiss(lead_s), audio, hiss(tail_s)])
        self.loop_ref, self.yielded = loop_ref, 0

    def frames(self):
        while (self.yielded + 1) * FRAME <= len(self.audio):
            self.yielded += 1
            yield self.audio[(self.yielded - 1) * FRAME:self.yielded * FRAME]
        self.loop_ref[0].stop()
        while True:
            yield np.zeros(FRAME, np.float32)

    def drain(self):
        pass


@pytest.fixture(scope="module")
def stt():
    s = STT()
    s.warm()
    return s


def _run(path: Path, stt: STT):
    audio, rate = sf.read(path, dtype="float32")
    assert rate == RATE
    ref, hub = [], FakeHub([{"type": "done", "text": "Okay."}])
    ears = WavEars(audio, ref)
    hub.ears = ears
    loop = VoiceLoop(ears=ears, vad=VAD(), stt=stt, tts=FakeTTS(), player=FakePlayer(), hub=hub,
                     clock=lambda: ears.yielded * FRAME / RATE,  # audio time: the follow-up window ends with the clip
                     log=lambda m: None, speaker=FakeSpeaker(enrolled=True, passes=True))
    ref.append(loop)
    loop.run()
    return loop, hub


@pytest.mark.parametrize("clip", WAKES, ids=lambda p: p.stem)
def test_every_wake_form_wakes_in_every_voice(clip, stt):
    # Act
    loop, hub = _run(clip, stt)

    # Assert: woke, and a command said in the same breath ran without a second wake
    assert "listen" in hub.states, f"{clip.stem}: heard {loop.stt and [t.text for t in loop.turns]}"
    for key, word in COMMANDS.items():
        if key in clip.stem:
            assert hub.chats and word in hub.chats[0].lower(), f"{clip.stem}: ran {hub.chats}"


@pytest.mark.parametrize("clip", NOT_WAKES, ids=lambda p: p.stem)
def test_max_as_an_ordinary_word_never_wakes(clip, stt):
    loop, hub = _run(clip, stt)
    assert "listen" not in hub.states and hub.chats == []


def test_maxie_hearing_her_own_sentence_from_the_speakers_is_not_a_command(stt):
    # Arrange: Maxie just said this sentence; the mic hears it back in her voice
    audio, _ = sf.read(CLIPS / "echo_jessica.wav", dtype="float32")
    hub = FakeHub([])
    loop = VoiceLoop(ears=None, vad=None, stt=stt, tts=FakeTTS(), player=FakePlayer(), hub=hub, log=lambda m: None)
    loop._said.append("Your next meeting is at three with the design team.")

    # Act
    turn = loop.run_turn(audio, 0.0)

    # Assert
    assert hub.chats == [] and turn.text
