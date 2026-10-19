"""The voice loop with fakes: wake phrase, segmenter, ack-first speech, follow-up window, stop, mute."""

import time

import numpy as np

from maxie.voice.audio import FRAME, RATE, Segmenter, chime
from maxie.voice.loop import Turn, VoiceLoop
from maxie.voice.speech import is_stop, is_wake, sentences


def test_wake_phrase_and_its_misspellings():
    assert is_wake("Hey Max, what time is it?") == (True, "what time is it?")
    assert is_wake("hey maxi open spotify") == (True, "open spotify")
    assert is_wake("Maxie.") == (True, "")
    assert is_wake("okay max, brief me") == (True, "brief me")
    assert is_wake("the maximum is ten") == (False, "")
    assert is_wake("Hey Claude, what time is it?") == (False, "")  # retired
    assert is_wake("") == (False, "")


def test_stop_phrases():
    assert is_stop("Max, stop") and is_stop("stop") and is_stop("hey maxie stop") and is_stop("never mind")
    assert not is_stop("stop the timer and start a new one for ten minutes please")
    assert not is_stop("what time is it")


def test_sentences_stream_and_final():
    ready, tail = sentences("On it. Checking your calendar now. You have")
    assert ready == ["On it.", "Checking your calendar now."] and tail == "You have"
    assert sentences("still going", final=False) == ([], "still going")
    assert sentences("still going", final=True) == (["still going"], "")


def test_segmenter_cuts_on_silence_and_keeps_pre_roll():
    seg = Segmenter()
    frame = np.ones(FRAME, dtype=np.float32)
    t, out = 0.0, None
    for p in [0.0] * 5 + [0.9] * 30 + [0.0] * 25:  # 0.96 s of speech then 0.8 s silence
        got = seg.feed(frame * p, p, t)
        out = got if got is not None else out
        t += FRAME / RATE
    assert out is not None
    assert 30 * FRAME <= len(out) <= (30 + 8 + 20) * FRAME  # speech + pre-roll + the trailing silence
    assert not seg.in_speech


class FakeEars:
    """Frames whose VAD probability is scripted, then silence; stops the loop when the script runs out."""

    def __init__(self, script, loop_ref):
        self.script, self.loop_ref, self.extra = list(script), loop_ref, 3

    def frames(self):
        while self.script:
            yield np.full(FRAME, self.script.pop(0), dtype=np.float32)
        while self.extra > 0:
            self.extra -= 1
            yield np.zeros(FRAME, dtype=np.float32)
        if self.loop_ref:
            self.loop_ref[0].stop()
        while True:
            yield np.zeros(FRAME, dtype=np.float32)

    def drain(self):
        pass


class FakeVAD:
    def prob(self, frame):
        return float(frame[0])


class FakeSTT:
    def __init__(self, answers):
        self.answers, self.calls, self.sleeps, self.wakes = list(answers), [], 0, 0

    def sleep(self):
        self.sleeps += 1

    def wake(self):
        self.wakes += 1

    def transcribe(self, audio, quick=False):
        self.calls.append(("quick" if quick else "full", round(len(audio) / RATE, 2)))
        return self.answers.pop(0) if self.answers else ""


class FakeTTS:
    engine = "fake"

    def __init__(self):
        self.texts, self.sleeps, self.wakes = [], 0, 0

    def sleep(self):
        self.sleeps += 1

    def wake(self):
        self.wakes += 1

    def synth(self, text):
        self.texts.append(text)
        return np.zeros(int(0.05 * 24000), dtype=np.float32), 24000


class FakePlayer:
    def __init__(self):
        self.played, self.stopped, self.playing = [], 0, False

    def play(self, samples, rate):
        self.played.append(len(samples))
        return True

    def stop(self):
        self.stopped += 1


class FakeHub:
    def __init__(self, events):
        self.events, self.states, self.stops, self.chats, self.muted_flag = events, [], 0, [], False

    def state(self, s):
        self.states.append(s)

    def stop(self):
        self.stops += 1

    def muted(self):
        return self.muted_flag

    wake_flag, say_queue, speak_flag, wake_words, enroll_pending = False, [], True, None, False

    def voice_settings(self):
        vs = {"voice_muted": self.muted_flag, "speak_replies": self.speak_flag}
        return vs if self.wake_words is None else {**vs, "wake_words": self.wake_words}

    def report_speaker(self, status):
        self.speaker_reports = getattr(self, "speaker_reports", []) + [status]

    def wake_requested(self):
        wake, self.wake_flag = self.wake_flag, False
        say, self.say_queue = list(self.say_queue), []
        return wake, say

    def chat(self, text, screen=False):
        self.chats.append(text)
        self.screens = getattr(self, "screens", []) + [screen]
        yield from self.events


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        self.t += 0.01
        return self.t


def speech(seconds):
    return [0.9] * int(seconds * RATE / FRAME)


def silence(seconds):
    return [0.0] * int(seconds * RATE / FRAME)


def make_loop(script, stt_answers, hub_events):
    ref = []
    hub = FakeHub(hub_events)
    loop = VoiceLoop(ears=FakeEars(script, ref), vad=FakeVAD(), stt=FakeSTT(stt_answers), tts=FakeTTS(),
                     player=FakePlayer(), hub=hub, clock=Clock(), log=lambda m: None)
    ref.append(loop)
    return loop, hub


def test_handle_speaks_ack_first_then_sentences_and_sets_states():
    loop, hub = make_loop([], [], [{"type": "ack", "text": "On it."}, {"type": "text", "text": "It is 9 PM. "},
                                   {"type": "text", "text": "Time for"}, {"type": "text", "text": " dinner."},
                                   {"type": "done", "text": "It is 9 PM. Time for dinner."}])
    turn = Turn(heard_at=loop.clock())
    loop.handle("what time is it", turn)
    assert hub.chats == ["what time is it"]
    assert len(loop.player.played) == 3  # "On it.", "It is 9 PM.", "Time for dinner."
    assert turn.first_audio > turn.heard_at and turn.reply_done > turn.first_audio
    assert hub.states[:2] == ["think", "talk"] and hub.states[-1] == "idle"


def test_stop_phrase_kills_the_task():
    loop, hub = make_loop([], [], [])
    turn = Turn(heard_at=0)
    loop.handle("Max, stop", turn)
    assert hub.stops == 1 and turn.stopped and loop.player.stopped == 1 and hub.chats == []


def test_wake_with_command_in_one_breath_then_follow_up_then_quiet():
    # "Hey Max, what time is it" (1.5 s), a pause, the follow-up "and the weather" (1 s), then quiet
    script = silence(0.2) + speech(1.5) + silence(0.8) + speech(1.0) + silence(0.8) + silence(9)
    loop, hub = make_loop(script, ["Hey Max what time is it", "Hey Max, what time is it?", "and the weather"],
                          [{"type": "done", "text": "Nine."}])
    loop.run()
    assert [t.text for t in loop.turns] == ["what time is it?", "and the weather"]
    assert hub.chats == ["what time is it?", "and the weather"]
    assert hub.states[0] == "listen" and hub.states[-1] == "idle"
    assert loop.player.played[0] == len(chime())  # the chime came first
    assert loop.stt.calls[0][0] == "quick"  # the wake check used the tiny model


def test_muted_ignores_the_wake_phrase():
    loop, hub = make_loop(speech(1.0) + silence(1.0), ["Hey Max"], [])
    hub.muted_flag = True
    loop.run()
    assert loop.turns == [] and hub.chats == [] and loop.stt.calls == []


class SlowHub(FakeHub):
    """Takes `delay` seconds before its first event, like a tool call does."""

    def __init__(self, events, delay):
        super().__init__(events)
        self.delay = delay

    def chat(self, text, screen=False):
        self.chats.append(text)
        time.sleep(self.delay)
        yield from self.events


def test_filler_ack_when_the_hub_is_slow_to_speak():
    loop, _ = make_loop([], [], [])
    # a full second of slow hub: under `taskpolicy -b` the filler's timer can start late (0.3 s was flaky)
    loop.hub = SlowHub([{"type": "done", "text": "Heavy rain."}], delay=1.0)
    loop.filler_after_s = 0.05
    turn = Turn(heard_at=loop.clock())
    loop.handle("what is the weather", turn)
    assert loop.tts.texts == ["One moment.", "Heavy rain."]
    assert turn.first_audio - turn.heard_at < 1.0


def test_no_filler_when_the_hub_acks_in_time():
    loop, _ = make_loop([], [], [{"type": "ack", "text": "On it."}, {"type": "done", "text": "Done."}])
    loop.filler_after_s = 1.0  # the ack is instant; a wide window keeps this steady on a busy (low-priority) run
    loop.handle("open spotify", Turn(heard_at=loop.clock()))
    assert loop.tts.texts == ["On it.", "Done."]


def test_barge_in_keeps_the_words_that_interrupted():
    # while Maxie is still answering he says something (0.6 s): playback stops and those words are
    # not lost: the next listen starts from them
    loop, _ = make_loop(speech(0.6) + silence(1.0), [], [])
    loop.hub = SlowHub([{"type": "ack", "text": "On it."}, {"type": "done", "text": "A long answer."}], delay=0.4)
    loop.handle("plan my week", Turn(heard_at=loop.clock()))
    assert loop._interrupted and loop.player.stopped == 1
    audio, _ = loop.listen_for_command(5.0)
    assert audio is not None and np.sum(audio > 0.5) >= 0.6 * RATE - FRAME  # the whole 0.6 s he said


def test_ignores_its_own_words_heard_back_through_the_speakers():
    loop, hub = make_loop([], [], [{"type": "text", "text": "No, I can't see your screen."},
                                   {"type": "done", "text": "No, I can't see your screen."}])
    loop.handle("can you see my screen", Turn(heard_at=0.0))
    for echo in ["No, I can't see your screen.", "one moment", "I can't see your screen"]:
        loop.run_turn(np.zeros(RATE, dtype=np.float32), 0.0, command=echo)
    assert hub.chats == ["can you see my screen"]  # none of the echoes went to the hub
    loop.run_turn(np.zeros(RATE, dtype=np.float32), 0.0, command="what about my calendar")
    assert hub.chats[-1] == "what about my calendar"


def test_no_barge_in_when_the_output_is_the_built_in_speakers():
    loop, _ = make_loop(speech(0.6) + silence(1.0), [], [])
    loop.hub = SlowHub([{"type": "ack", "text": "On it."}, {"type": "done", "text": "A long answer."}], delay=0.4)
    loop.barge_in_ok = lambda: False
    loop.handle("plan my week", Turn(heard_at=loop.clock()))
    assert not loop._interrupted and loop.player.stopped == 0


def test_panel_mic_button_wakes_the_loop_without_the_phrase():
    # a wake request from the hub (the panel's mic button): chime, listen for the command, no wake phrase
    loop, hub = make_loop(silence(0.3) + speech(1.0) + silence(0.8) + silence(9), ["what time is it"],
                          [{"type": "done", "text": "Nine."}])
    hub.wake_flag = True
    loop.run()
    assert hub.chats == ["what time is it"] and loop.player.played[0] == len(chime())
    assert loop.stt.calls == [("full", loop.stt.calls[0][1])]  # no quick wake check, straight to the command


def test_say_requests_from_the_hub_are_spoken():
    loop, hub = make_loop(silence(1.0), [], [])
    hub.say_queue = ["Testing one two."]
    loop.run()
    assert loop.tts.texts == ["Testing one two."]


def test_speak_replies_off_answers_in_text_only():
    loop, hub = make_loop(silence(0.3) + speech(1.0) + silence(0.8) + silence(9), ["Hey Max what time is it"],
                          [{"type": "done", "text": "Nine."}])
    hub.speak_flag = False
    loop.run()
    assert hub.chats == ["what time is it"] and loop.tts.texts == []  # the panel shows it; nothing is spoken
    assert loop.player.played == [len(chime())]  # the chime still confirms the wake


def test_models_unload_after_idle_and_reload_on_wake():
    # quiet for longer than idle_unload_s: the big models go; "Hey Max" brings them back once
    loop, hub = make_loop(silence(1.0) + speech(1.0) + silence(0.8) + silence(9), ["Hey Max what time is it"],
                          [{"type": "done", "text": "Nine."}])
    loop.idle_unload_s = 0.2  # the fake clock moves 10 ms per frame
    loop.run()
    assert loop.stt.sleeps == 1 and loop.tts.sleeps == 1
    assert loop.stt.wakes == 1 and loop.tts.wakes == 1 and hub.chats == ["what time is it"]


def test_missing_mic_falls_back_to_the_default_input():
    from maxie.voice.__main__ import open_mic
    made = []

    def factory(device=None):
        if device == "Gone USB Mic":
            raise ValueError("no such device")
        made.append(device)
        return "mic"
    assert open_mic("Gone USB Mic", factory) == "mic" and made == [None]
    assert open_mic("", factory) == "mic" and made == [None, None]
    assert open_mic("AirPods", lambda device=None: f"mic:{device}") == "mic:AirPods"
