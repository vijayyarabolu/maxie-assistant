"""Voice that feels human: whisper mode in quiet hours, conversation mode for long talks."""
from datetime import datetime

import numpy as np
import pytest

from maxie.voice import loop as voice_loop
from maxie.voice.speech import conversation_start, is_done_talking
from tests.test_voice import make_loop, silence, speech


@pytest.mark.parametrize("text,rest", [("let's talk", ""), ("Let's chat.", ""), ("can we talk about my week", "my week"),
                                       ("conversation mode", ""), ("I need to talk", "")])
def test_asking_for_a_long_talk(text, rest):
    assert conversation_start(text) == (True, rest)


@pytest.mark.parametrize("text", ["what's the weather", "talk me through the plan", "let's go"])
def test_ordinary_commands_are_not_a_long_talk(text):
    assert conversation_start(text) == (False, "")


@pytest.mark.parametrize("text,done", [("that's all", True), ("Okay, thanks, that's it.", True), ("good night", True),
                                       ("bye", True), ("talk to you later", True),
                                       ("that's all I wanted to say about Riley's party", False), ("what time is it", False)])
def test_ending_a_long_talk(text, done):
    assert is_done_talking(text) is done


@pytest.mark.parametrize("hm,quiet", [("23:30", True), ("02:00", True), ("06:59", True), ("07:00", False), ("12:00", False),
                                       ("22:59", False)])
def test_quiet_hours_wrap_midnight(hm, quiet):
    h, m = map(int, hm.split(":"))
    assert voice_loop.quiet_now({"quiet_start": "23:00", "quiet_end": "07:00"}, datetime(2026, 10, 15, h, m)) is quiet


def test_quiet_hours_inside_one_day():
    s = {"quiet_start": "13:00", "quiet_end": "14:00"}
    assert voice_loop.quiet_now(s, datetime(2026, 10, 15, 13, 30)) and not voice_loop.quiet_now(s, datetime(2026, 10, 15, 14, 1))


class LoudTTS:
    engine, speed = "fake", 1.05

    def sleep(self):
        pass

    def wake(self):
        pass

    def synth(self, text):
        return np.full(1200, 0.8, dtype=np.float32), 24000


class PeakPlayer:
    def __init__(self):
        self.peaks, self.stopped, self.playing = [], 0, False

    def play(self, samples, rate):
        self.peaks.append(float(np.max(np.abs(samples))))
        return True

    def stop(self):
        self.stopped += 1


def test_whisper_mode_is_softer_and_slower():
    loop, _ = make_loop([], [], [])
    loop.tts, loop.player = LoudTTS(), PeakPlayer()
    loop.say("Hello.")
    loop.set_whisper(True)
    loop.say("Hello.")
    assert loop.player.peaks[1] == pytest.approx(loop.player.peaks[0] * voice_loop.WHISPER_GAIN)
    assert loop.tts.speed == voice_loop.WHISPER_SPEED
    loop.set_whisper(False)
    assert loop.tts.speed == voice_loop.NORMAL_SPEED


@pytest.fixture
def short_windows(monkeypatch):
    # The fake clock moves 0.01 s per reading, so windows are counted in readings, not real seconds.
    monkeypatch.setattr(voice_loop, "FOLLOW_UP_S", 1.0)
    monkeypatch.setattr(voice_loop, "CONVERSATION_S", 5.0)


def test_a_long_talk_waits_through_pauses_and_ends_on_thats_all(short_windows):
    # "Hey Max, let's talk", a long pause (past the normal follow-up window), a question, then "that's all".
    script = silence(0.2) + speech(1.5) + silence(0.8) + silence(6) + speech(1.0) + silence(0.8) + speech(1.0) + silence(0.8)
    loop, hub = make_loop(script, ["Hey Max let's talk", "Hey Max, let's talk.", "what should I cook tonight",
                                   "that's all"], [{"type": "done", "text": "Dal and rice."}])
    loop.listen_while_talking = False  # its watcher would read the scripted pause from the same fake mic
    loop.run()
    assert hub.chats == ["what should I cook tonight"]
    assert loop.tts.texts[0] == voice_loop.LISTENING and loop.tts.texts[-1] == "Okay. Talk soon."
    assert [t.ended for t in loop.turns] == [False, False, True] and not loop.conversing


def test_without_a_long_talk_the_same_pause_ends_the_follow_up(short_windows):
    script = silence(0.2) + speech(1.5) + silence(0.8) + silence(6) + speech(1.0) + silence(0.8)
    loop, hub = make_loop(script, ["Hey Max what time is it", "Hey Max, what time is it?", "and the weather"],
                          [{"type": "done", "text": "Nine."}])
    loop.listen_while_talking = False
    loop.run()
    assert hub.chats == ["what time is it?"], "the late follow-up needs the wake phrase again"


def test_a_long_talk_can_start_with_its_topic(short_windows):
    loop, hub = make_loop(silence(0.2) + speech(1.5) + silence(0.8), ["Hey Max can we talk about my week",
                                                                      "Hey Max, can we talk about my week?"],
                          [{"type": "done", "text": "Sure. How did it go?"}])
    loop.run()
    assert hub.chats == ["my week?"] and loop.turns[0].text == "my week?"


def test_short_answers_are_his_even_when_claude_just_said_them():
    loop, _ = make_loop([], [], [])
    loop.say("Should I send it? Yes or no.")
    loop.say(voice_loop.LISTENING)
    assert not loop.is_echo("yes") and not loop.is_echo("that's all")
    assert loop.is_echo("should I send it yes or no"), "a long piece of Maxie's own sentence is still an echo"
    assert loop.is_echo("Done."), "a fixed phrase heard back whole is still an echo"


def test_voice_and_speed_from_settings_maxie_apply_live():
    loop, _ = make_loop([], [], [])
    loop.tts = LoudTTS()
    loop.tts.voice = "af_heart"
    loop.set_voice("bm_fable", 1.2)
    assert (loop.tts.voice, loop.tts.speed) == ("bm_fable", 1.2)
    loop.set_whisper(True)
    assert loop.tts.speed == pytest.approx(1.2 * voice_loop.WHISPER_SPEED / voice_loop.NORMAL_SPEED, abs=1e-3)
    loop.set_voice("xx_bad", 1.0)
    assert loop.tts.voice == "bm_fable", "an unknown voice is ignored"
