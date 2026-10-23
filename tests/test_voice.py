"""The voice loop with fakes: wake phrase, segmenter, ack-first speech, follow-up window, stop, mute."""

import time

import numpy as np
import pytest

from maxie.voice.audio import FRAME, RATE, Segmenter, chime
from maxie.voice.loop import ECHO_GATE_S, REPEAT_S, Turn, VoiceLoop
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
        self.script, self.loop_ref, self.extra, self.yielded = list(script), loop_ref, 3, 0

    def frames(self):
        while self.script:
            self.yielded += 1
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

    def transcribe(self, audio, quick=False, wake=False, stop=False):
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
        self.state_frames, self.notices, self.ears = [], [], None

    def state(self, s):
        self.states.append(s)
        self.state_frames.append(self.ears.yielded if self.ears else 0)

    def pause(self, on):
        self.paused = getattr(self, "paused", []) + [on]

    def notice(self, kind, text=""):
        self.notices.append({"kind": kind, "text": text})

    def stop(self):
        self.stops += 1

    def muted(self):
        return self.muted_flag

    wake_flag, say_queue, speak_flag, wake_words, enroll_pending, test_pending = False, [], True, None, False, False

    def report_test(self, result):
        self.test_results = getattr(self, "test_results", []) + [result]

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
    ears = FakeEars(script, ref)
    hub.ears = ears
    loop = VoiceLoop(ears=ears, vad=FakeVAD(), stt=FakeSTT(stt_answers), tts=FakeTTS(),
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



class MicEars(FakeEars):
    """Ears with a real mic's switch: the loop closes it while muted."""

    def __init__(self, script, loop_ref, vad):
        super().__init__(script, loop_ref)
        self.vad, self.switches = vad, []

    def close_mic(self):
        self.switches.append(("close", self.vad.calls))

    def open_mic(self):
        self.switches.append(("open", self.vad.calls))


class CountingVAD(FakeVAD):
    calls = 0

    def prob(self, frame):
        self.calls += 1
        return super().prob(frame)


def test_muted_closes_the_mic_and_runs_nothing_per_frame_until_he_unmutes():
    # Arrange: paused or muted Maxie kept the mic open and ran the speech detector 31 times a second
    loop, hub = make_loop([], [], [])
    vad = CountingVAD()
    loop.vad, loop.ears = vad, MicEars(silence(0.5), [loop], vad)
    hub.muted_flag = True
    waits = []

    def wait(s):
        waits.append(s)
        loop.clock.t += s
        hub.muted_flag = len(waits) < 12  # unmuted after 12 s

    loop.wait = wait

    # Act
    loop.run()

    # Assert
    assert loop.ears.switches[:2] == [("close", 0), ("open", 0)], "closed at once; nothing ran while closed"
    assert 11 <= len(waits) <= 20 and set(waits) == {1.0}
    assert vad.calls > 0, "listening again after he unmuted"


class SlowHub(FakeHub):
    """Takes `delay` seconds before its first event, like a tool call does."""

    def __init__(self, events, delay):
        super().__init__(events)
        self.delay = delay

    def chat(self, text, screen=False):
        self.chats.append(text)
        time.sleep(self.delay)
        yield from self.events


def test_pause_while_she_speaks_stops_her_at_once():
    # ⌥⇧P, the menu or a click pauses him from anywhere; on the speakers that is the stop that always works
    loop, _ = make_loop(silence(3.0), [], [])
    loop.hub = SlowHub([{"type": "text", "text": "Your week has three meetings. "},
                        {"type": "done", "text": "Your week has three meetings."}], delay=1.2)
    asked = []
    loop.hub.is_paused = lambda: asked.append(1) or len(asked) >= 2
    loop.barge_in_ok = lambda: False
    turn = loop.run_turn(np.zeros(RATE, dtype=np.float32), loop.clock(), command="plan my week")
    assert loop._interrupted and loop.player.stopped == 1 and turn.ended


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


def test_the_words_that_interrupted_her_keep_the_speech_probabilities_they_had():
    # silero keeps state: run again over the carried frames after hearing what came later, "Actually," dipped at its
    # comma and the segment ended there. The barge-in watcher's own probabilities go along with the frames.
    loop, _ = make_loop(speech(0.4) + silence(1.0), [], [])
    loop._carry = [(np.zeros(FRAME, np.float32), 0.9)] * 20  # 0.64 s he said, scored by the watcher
    rerun = []
    loop.vad.prob = lambda frame: rerun.append(1) or float(frame[0])
    audio, _ = loop.listen_for_command(5.0)
    assert audio is not None and len(audio) >= 20 * FRAME + int(0.4 * RATE) - FRAME, "the carry and the rest, one segment"
    assert len(rerun) <= len(speech(0.4) + silence(1.0)) + 3, "the carried frames aren't scored again"


def test_ignores_its_own_words_heard_back_through_the_speakers():
    loop, hub = make_loop([], [], [{"type": "text", "text": "No, I can't see your screen."},
                                   {"type": "done", "text": "No, I can't see your screen."}])
    loop.handle("can you see my screen", Turn(heard_at=0.0))
    for echo in ["No, I can't see your screen.", "one moment", "I can't see your screen"]:
        loop.run_turn(np.zeros(RATE, dtype=np.float32), 0.0, command=echo)
    assert hub.chats == ["can you see my screen"]  # none of the echoes went to the hub
    loop.run_turn(np.zeros(RATE, dtype=np.float32), 0.0, command="what about my calendar")
    assert hub.chats[-1] == "what about my calendar"


def _over_the_speakers(heard):
    """Maxie answers through the built-in speakers while the mic hears 0.6 s of speech that transcribes as `heard`."""
    loop, _ = make_loop(speech(0.6) + silence(1.0), [heard], [])
    loop.hub = SlowHub([{"type": "ack", "text": "On it."}, {"type": "text", "text": "Your week has three meetings. "},
                        {"type": "done", "text": "Your week has three meetings."}], delay=0.4)
    loop.barge_in_ok = lambda: False
    loop.handle("plan my week", Turn(heard_at=loop.clock()))
    return loop


def test_on_the_built_in_speakers_her_own_voice_never_stops_her():
    loop = _over_the_speakers("On it.")
    assert not loop._interrupted and loop.player.stopped == 0


@pytest.mark.parametrize("heard", ["Stop.", "Maxie, stop", "be quiet"])
def test_on_the_built_in_speakers_a_stop_from_him_stops_her_and_the_task(heard):
    loop = _over_the_speakers(heard)
    assert loop._interrupted and loop.player.stopped == 1 and loop.hub.stops == 1
    assert loop._carry == [], "nothing heard over her becomes the next turn"


@pytest.mark.parametrize("heard", ["Your only talent.", "in all day Dacerra.", "you're breaking up, you're doing",
                                   "Maxie, wait", "no actually make it Friday"])
def test_on_the_built_in_speakers_nothing_but_stop_stops_her(heard):
    # Live: her own words misheard read as his, cut her off and became new questions
    loop = _over_the_speakers(heard)
    assert not loop._interrupted and loop.player.stopped == 0 and loop.hub.stops == 0


def test_on_the_built_in_speakers_maxie_then_a_breath_then_stop_is_checked_together():
    # "Maxie," and its "stop" a breath later: checked apart, "Maxie," alone was heard as nothing and the dulled
    # "stop" never started a piece of its own (half the stops in scripts/echo_room.py were missed)
    loop, _ = make_loop(speech(0.4) + silence(0.4) + speech(0.4) + silence(1.0), ["Maxie.", "Maxie, stop."], [])
    loop.hub = SlowHub([{"type": "text", "text": "Your week has three meetings. "},
                        {"type": "done", "text": "Your week has three meetings."}], delay=0.4)
    loop.barge_in_ok = lambda: False
    loop.handle("plan my week", Turn(heard_at=loop.clock()))
    assert loop._interrupted and loop.hub.stops == 1
    checked = [s for kind, s in loop.stt.calls if kind == "quick"]
    assert checked[-1] >= 0.9, f"the stop was heard with its \"Maxie,\" from his first word: {checked}"


def test_on_the_built_in_speakers_a_stop_after_her_words_in_the_window_stops_her():
    loop = _over_the_speakers("calendar is. Maxie, stop.")
    assert loop._interrupted and loop.hub.stops == 1


def test_on_the_built_in_speakers_a_stop_she_says_herself_is_her_echo():
    loop, _ = make_loop(speech(0.6) + silence(1.0), ["Stop by the store."], [])
    loop.hub = SlowHub([{"type": "text", "text": "Stop by the store at five. "},
                        {"type": "done", "text": "Stop by the store at five."}], delay=0.4)
    loop.barge_in_ok = lambda: False
    loop._said.append("Stop by the store at five.")
    loop.handle("what should I do today", Turn(heard_at=loop.clock()))
    assert not loop._interrupted


def test_on_the_built_in_speakers_a_stop_ends_the_exchange_and_the_rest_of_it_is_not_heard():
    # A stop can be heard before he finishes saying it; the rest ("...stop.") became a new question in the room
    loop, hub = make_loop(speech(0.6) + silence(1.0), ["Maxie, stop."], [])
    loop.hub = SlowHub([{"type": "text", "text": "Your week has three meetings. "},
                        {"type": "done", "text": "Your week has three meetings."}], delay=0.4)
    loop.barge_in_ok = lambda: False
    turn = loop.run_turn(np.zeros(RATE, dtype=np.float32), loop.clock(), command="plan my week")
    assert turn.ended, "no follow-up window after a stop"
    assert loop.deaf(loop.clock() + ECHO_GATE_S + 0.5), "deaf through the rest of his \"Maxie, stop\""


def test_on_the_built_in_speakers_the_mic_is_deaf_for_a_second_after_she_speaks():
    # Arrange: what the mic queued while she spoke is her own voice; so is the room right after
    loop, hub = make_loop(speech(0.4) + silence(1.5), ["Your only talent"], [{"type": "done", "text": "Dussehra."}])
    loop.barge_in_ok = lambda: False
    drained = []
    loop.ears.drain = lambda: drained.append(1)
    loop.handle("what's on my calendar", Turn(heard_at=loop.clock()))

    # Act
    deaf_now = loop.deaf(loop.clock())
    loop.clock.t += ECHO_GATE_S + 0.01

    # Assert
    assert drained and deaf_now, "dropped and deaf right after the reply"
    assert not loop.deaf(loop.clock()), "listening again after the gate"


def test_in_headphones_the_mic_is_never_deaf():
    loop, _ = make_loop([], [], [{"type": "done", "text": "Dussehra."}])
    loop.handle("what's on my calendar", Turn(heard_at=loop.clock()))
    assert not loop.deaf(loop.clock())


def test_the_chime_never_makes_the_mic_deaf():
    loop, _ = make_loop([], [], [])
    loop.barge_in_ok = lambda: False
    loop._wake_now()
    assert not loop.deaf(loop.clock()), "he is still saying his command"


def test_one_question_gets_one_answer():
    # Arrange: the same words again right after the answer (the room, or a double segment)
    loop, hub = make_loop([], [], [{"type": "done", "text": "Dussehra, all day."}])
    loop.run_turn(np.zeros(RATE, dtype=np.float32), 0.0, command="what's on my calendar")

    # Act
    loop.run_turn(np.zeros(RATE, dtype=np.float32), 0.0, command="What's on my calendar?")
    loop.clock.t += REPEAT_S + 1
    loop.run_turn(np.zeros(RATE, dtype=np.float32), 0.0, command="what's on my calendar")

    # Assert
    assert hub.chats == ["what's on my calendar", "what's on my calendar"], "asked again later: a new answer"


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


@pytest.mark.parametrize("raw, clean", [
    ("Maxie. Maxie. Maxie. Maxie. Max", "Maxie. Max"),  # the wake prompt made Whisper loop on 0.6 s of "Max is…"
    ("Hey Maxie, Hey Maxie, Hey Maxie, Hey Maxie,", "Hey Maxie,"),
    ("Hey Max, what time is it?", "Hey Max, what time is it?"),
    ("no no no", "no no no"),  # three is still what he said
])
def test_a_whisper_loop_collapses_to_one(raw, clean):
    from maxie.voice.speech import unloop
    assert unloop(raw) == clean


class SlowTTS(FakeTTS):
    def synth(self, text):
        time.sleep(0.3)
        return super().synth(text)


class SlowPlayer(FakePlayer):
    def play(self, samples, rate):
        time.sleep(0.3)
        return super().play(samples, rate)


def test_the_next_sentence_is_made_while_this_one_plays():
    # Kokoro takes ~0.5 s a sentence; made one after another, each reply sentence waited for it
    loop, _ = make_loop([], [], [{"type": "text", "text": "One. Two. "}, {"type": "text", "text": "Three."},
                                 {"type": "done", "text": "One. Two. Three."}])
    loop.tts, loop.player = SlowTTS(), SlowPlayer()

    t0 = time.perf_counter()
    loop.handle("count to three", Turn(heard_at=loop.clock()))
    took = time.perf_counter() - t0

    assert loop.tts.texts == ["One.", "Two.", "Three."] and len(loop.player.played) == 3
    assert took < 1.5, f"made while the one before played (one after another: 1.8 s), took {took:.2f} s"


def test_a_barge_in_drops_the_sentences_not_yet_spoken():
    loop, _ = make_loop([], [], [{"type": "text", "text": "One. Two. Three. "}, {"type": "done", "text": "One. Two. Three."}])
    loop.tts = SlowTTS()

    class CutPlayer(FakePlayer):
        def play(self, samples, rate):
            super().play(samples, rate)
            return False  # he talked over the first one

    loop.player = CutPlayer()
    loop.handle("count to three", Turn(heard_at=loop.clock()))
    assert len(loop.player.played) == 1


@pytest.mark.parametrize("text, pieces", [
    ("You have a mock interview at ten, then lunch with Sam at noon.",
     ["You have a mock interview at ten,", "then lunch with Sam at noon."]),
    ("The capital of France is Paris.", ["The capital of France is Paris."]),  # short: one piece
    ("Yes, that one.", ["Yes, that one."]),
    ("Well, it depends on how long the meeting with the recruiter runs today.",
     ["Well, it depends on how long the meeting with the recruiter runs today."]),  # a comma too early to help
])
def test_a_long_first_sentence_starts_with_its_first_clause(text, pieces):
    from maxie.voice.speech import first_pieces
    assert first_pieces(text) == pieces


def test_no_filler_once_the_answer_is_on_its_way():
    # the hub answered at once ("Opening Spotify."); Kokoro is still making it when the filler's time comes
    loop, _ = make_loop([], [], [{"type": "done", "text": "Opening Spotify."}])
    loop.tts = SlowTTS()
    loop.filler_after_s = 0.05
    loop.handle("open spotify", Turn(heard_at=loop.clock()))
    time.sleep(0.5)  # a filler that started would be made by now
    assert loop.tts.texts == ["Opening Spotify."]


def _hub_answering(answers):
    """A real loop `Hub` over a fake transport; `answers` are the /api/voice/wake bodies, then empty ones."""
    import httpx

    from maxie.voice.loop import Hub
    seen = []

    def handle(request):
        if request.url.path == "/api/voice/wake":
            seen.append(dict(request.url.params))
            body = answers.pop(0) if answers else {"wake": False, "say": [], "enroll": False, "test": False}
            return httpx.Response(200, json=body)
        return httpx.Response(200, json={})
    return Hub("http://hub", "t", http=httpx.Client(transport=httpx.MockTransport(handle))), seen


def test_the_loop_long_polls_the_hub_for_the_mic_button():
    # one waiting request instead of two a second; the press arrives the moment the hub has it
    hub, seen = _hub_answering([{"wake": True, "say": ["Hi."], "enroll": False, "test": True}])
    deadline = time.time() + 2
    got = (False, [])
    while time.time() < deadline and not got[0]:
        got = hub.wake_requested()
        time.sleep(0.02)
    hub.close()
    assert got == (True, ["Hi."]) and hub.test_pending
    assert float(seen[0]["wait"]) > 1


def test_an_old_hub_that_answers_at_once_is_asked_at_the_old_pace():
    hub, seen = _hub_answering([])
    hub.wake_requested()
    time.sleep(1.2)
    hub.close()
    assert len(seen) <= 4, f"{len(seen)} polls in 1.2 s: an old hub ignores `wait`, so pace them"


def test_a_failing_voice_still_ends_the_turn():
    # verifier: an error from Kokoro or the speakers skipped the turn's cleanup, so the notch stayed in "think"
    loop, hub = make_loop([], [], [{"type": "done", "text": "Nine."}])

    class BrokenTTS(FakeTTS):
        def synth(self, text):
            raise RuntimeError("kokoro failed")

    loop.tts = BrokenTTS()
    with pytest.raises(RuntimeError):
        loop.handle("what time is it", Turn(heard_at=loop.clock()))
    assert hub.states[-1] == "idle"


def test_the_filler_never_plays_once_the_answer_has_arrived():
    # the filler's own synthesis can outlast the wait for the answer: it checks again just before it plays
    loop, _ = make_loop([], [], [])
    loop.hub = SlowHub([{"type": "done", "text": "Nine."}], delay=0.1)
    loop.tts = SlowTTS()
    loop.filler_after_s = 0.05
    loop.handle("what time is it", Turn(heard_at=loop.clock()))
    time.sleep(0.5)
    assert len(loop.player.played) == 1  # the answer only


def _fake_whisper(monkeypatch, text="Maxie. Maxie. Maxie. Maxie.", delay=0.0):
    import sys
    import types
    state = {"now": 0, "most": 0}

    def transcribe(audio, **kw):
        state["now"] += 1
        state["most"] = max(state["most"], state["now"])
        time.sleep(delay)
        state["now"] -= 1
        return {"text": text}
    monkeypatch.setitem(sys.modules, "mlx_whisper", types.SimpleNamespace(transcribe=transcribe))
    return state


def test_whisper_runs_one_call_at_a_time(monkeypatch):
    # verifier: the barge-in watcher and the main loop could run MLX Whisper on two threads at once
    import threading

    from maxie.voice.speech import STT
    state = _fake_whisper(monkeypatch, delay=0.05)
    stt = STT()
    calls = [threading.Thread(target=stt.transcribe, args=(np.zeros(16000, np.float32),)) for _ in range(4)]
    for t in calls:
        t.start()
    for t in calls:
        t.join()
    assert state["most"] == 1


def test_only_wake_checks_collapse_a_loop(monkeypatch):
    # "no no no no" from him is what he said; the prompt-induced loop only happens on wake checks
    from maxie.voice.speech import STT
    _fake_whisper(monkeypatch, text="no no no no")
    stt = STT()
    audio = np.zeros(16000, np.float32)
    assert stt.transcribe(audio) == "no no no no"
    assert stt.transcribe(audio, quick=True) == "no"


@pytest.mark.parametrize("text, sleep", [("go to sleep", True), ("Go to sleep.", True), ("take a break", True),
                                         ("pause", True), ("stop listening", True), ("Maxie, go to sleep", True),
                                         ("put my Mac to sleep", False), ("pause the music", False),
                                         ("when should I go to sleep", False)])
def test_go_to_sleep_is_a_whole_command(text, sleep):
    from maxie.voice.speech import is_sleep
    assert is_sleep(text) is sleep


def test_go_to_sleep_pauses_maxie_through_the_hub():
    loop, hub = make_loop([], [], [])
    loop.handle("go to sleep", Turn(heard_at=loop.clock()))
    assert hub.paused == [True] and hub.chats == []
    assert any("Option" in t or "pause" in t.lower() for t in loop.tts.texts)


def test_in_a_quiet_room_the_speech_detector_runs_on_every_other_frame_and_on_every_frame_of_speech():
    # Arrange: silero 31 times a second was most of the voice helper's idle CPU
    loop, _ = make_loop([], [], [])
    vad = CountingVAD()
    loop.vad = vad
    seg = Segmenter()
    frames = [np.full(FRAME, p, dtype=np.float32) for p in silence(1.0) + speech(1.0)]

    # Act
    quiet = [loop._idle_prob(f, seg) for f in frames[:len(silence(1.0))]]
    quiet_calls = vad.calls
    for i, f in enumerate(frames[len(quiet):]):
        seg.feed(f, loop._idle_prob(f, seg), i * 0.032)

    # Assert
    assert quiet_calls == (len(quiet) + 1) // 2 and set(quiet) == {0.0}
    assert seg.in_speech, "speech still starts"
    assert vad.calls - quiet_calls >= len(speech(1.0)) - 1, "at most the first speech frame reuses a quiet one"


def test_a_restarted_hub_hears_the_mics_state_again():
    # after a hub restart Settings said "mic ok" while the lid was closed and the mic gave only zeros
    loop, hub = make_loop([], [], [])
    reports = []
    hub.report_status = reports.append
    loop._mic_silent = True
    hub.reconnected = True

    loop._poll_hub(loop.clock() + 1)

    assert reports == [{"mic": "silent", "last_wake": None}] and not hub.reconnected
