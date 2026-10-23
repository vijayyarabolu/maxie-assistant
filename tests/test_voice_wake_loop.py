"""The voice loop with Maxie's wake words: which forms are on, the speaker check, voice enrollment."""

from test_voice import make_loop, silence, speech

from maxie.voice.speaker import HEY_FORMS, Verdict


class FakeSpeaker:
    """`passes`: a bare name in this voice clears the bar; a miss scores `miss_score` (0.3: a close call, him on a
    bad day; 0.02: clearly someone else). "Hey" forms pass unless `hey_passes` is False."""

    def __init__(self, enrolled=False, passes=True, hey_passes=True, miss_score=0.3):
        self.enrolled, self.passes, self.checked, self.enrolled_with = enrolled, passes, 0, None
        self.hey_passes, self.miss_score = hey_passes, miss_score

    def judge(self, audio, form):
        self.checked += 1
        if form in HEY_FORMS:
            if not self.enrolled or self.hey_passes:
                return Verdict(True, 0.3 if self.enrolled else None, 0.25, "hey form")
            return Verdict(False, 0.02, 0.25, "not your voice")
        if not self.enrolled:
            return Verdict(False, None, 0.6, "not enrolled")
        ok = self.passes
        return Verdict(ok, 0.5 if ok else self.miss_score, 0.38, "his voice" if ok else "not your voice")

    def enroll(self, clips):
        self.enrolled_with, self.enrolled = len(clips), True
        return 0.5

    def refresh(self):
        pass


ONE_COMMAND = silence(0.2) + speech(1.5) + silence(0.8) + silence(9)
DONE = [{"type": "done", "text": "Nine."}]


def _run(heard, speaker=None, wake_words=None):
    loop, hub = make_loop(ONE_COMMAND, heard, DONE)
    loop.speaker = speaker
    if wake_words is not None:
        hub.wake_words = wake_words
    loop.run()
    return loop, hub


def test_hey_max_wakes_without_enrollment():
    loop, hub = _run(["Hey Max what time is it", "Hey Max, what time is it?"])
    assert hub.chats == ["what time is it?"]


def test_bare_max_needs_his_enrolled_voice():
    # Arrange / Act: nobody enrolled, so a bare "Max" (TV, a friend) can't wake Maxie
    loop, hub = _run(["Max what time is it"], speaker=FakeSpeaker(enrolled=False))

    # Assert
    assert hub.chats == []


def test_bare_max_wakes_on_his_voice():
    speaker = FakeSpeaker(enrolled=True, passes=True)
    loop, hub = _run(["Max what time is it", "Max, what time is it?"], speaker=speaker)
    assert hub.chats == ["what time is it?"] and speaker.checked == 1


def test_hey_maxie_wakes_once_he_enrolled_even_when_the_score_is_low():
    # since enrolling, every "Hey Maxie" was dropped as "not your voice"
    loop, hub = _run(["Hey Maxie what time is it", "Hey Maxie, what time is it?"],
                     speaker=FakeSpeaker(enrolled=True, passes=False))
    assert hub.chats == ["what time is it?"]


def test_a_close_call_on_a_bare_name_says_so_in_the_notch():
    loop, hub = _run(["Max what time is it"], speaker=FakeSpeaker(enrolled=True, passes=False, miss_score=0.3))
    assert hub.chats == []
    assert [n["kind"] for n in hub.notices] == ["not_your_voice"]


def test_a_bare_name_that_scores_like_a_stranger_still_says_why():
    # Live: the user's own short clips scored 0.06-0.18, like a stranger's; a quiet ignore was silence again
    loop, hub = _run(["Max what time is it"], speaker=FakeSpeaker(enrolled=True, passes=False, miss_score=0.02))
    assert hub.chats == []
    assert [n["kind"] for n in hub.notices] == ["not_your_voice"]


def test_turned_off_wake_words_do_not_wake():
    loop, hub = _run(["Hey Maxie what time is it"], wake_words=["hey max"])
    assert hub.chats == []


def test_enrollment_records_five_phrases_and_reports_back():
    # Arrange: the hub asks for enrollment; he says five phrases with pauses
    script = silence(0.3) + (speech(1.2) + silence(0.8)) * 5 + silence(2)
    loop, hub = make_loop(script, ["one", "two", "three", "four", "five"], [])
    loop.speaker = FakeSpeaker()
    hub.enroll_pending = True

    # Act
    loop.run()

    # Assert
    assert loop.speaker.enrolled_with == 5
    assert hub.speaker_reports[-1]["enrolled"] is True
    assert hub.chats == []  # the phrases are not commands


def _frames_until_listen(script, heard):
    loop, hub = make_loop(script, heard, DONE)
    loop.run()
    return loop, hub, hub.state_frames[hub.states.index("listen")]


def test_wake_opens_the_panel_at_the_first_pause_not_when_the_command_ends():
    # Arrange: "Hey Maxie" (0.6 s), a 0.25 s breath, then the command (1.5 s) and the 0.6 s that ends the segment
    script = silence(0.2) + speech(0.6) + silence(0.25) + speech(1.5) + silence(0.8) + silence(9)

    # Act
    loop, hub, at = _frames_until_listen(script, ["Hey Maxie", "Hey Maxie, what's on my calendar"])

    # Assert: "listen" went out ~0.2 s after the wake word, long before the segment ended (~3 s in)
    from maxie.voice.audio import FRAME, RATE
    assert at * FRAME / RATE < 0.2 + 0.6 + 0.25 + 0.2
    assert hub.chats == ["what's on my calendar"]


def test_ordinary_talk_is_transcribed_once_at_the_start_only():
    # Arrange: four seconds of TV talk that isn't a wake word
    loop, hub = make_loop(silence(0.2) + speech(4.0) + silence(0.8) + silence(2), ["The weather tomorrow looks"], DONE)

    # Act
    loop.run()

    # Assert: one short peek, no transcription of the whole segment
    assert len(loop.stt.calls) == 1 and loop.stt.calls[0][1] <= 2.0
    assert hub.chats == [] and "listen" not in hub.states


def test_the_command_said_in_one_breath_is_transcribed_once_more_not_twice():
    loop, hub = make_loop(ONE_COMMAND, ["Hey Max what time", "Hey Max, what time is it?"], DONE)
    loop.run()
    assert [c[0] for c in loop.stt.calls] == ["quick", "full"]
    assert hub.chats == ["what time is it?"]


def test_woke_but_nothing_said_tells_him_in_the_notch():
    loop, hub = make_loop(silence(0.2) + speech(0.6) + silence(0.8) + silence(10), ["Maxie", "Maxie."], DONE)
    loop.speaker = FakeSpeaker(enrolled=True, passes=True)
    loop.run()
    assert hub.chats == [] and [n["kind"] for n in hub.notices] == ["didnt_catch"]


def test_the_wake_word_test_reports_what_it_heard_and_runs_nothing():
    # Arrange: Settings › Voice › Test wake word, then he says "Maxie"
    loop, hub = make_loop(silence(0.3) + speech(0.7) + silence(0.8) + silence(2), ["Maxie."], DONE)
    loop.speaker = FakeSpeaker(enrolled=True, passes=False)
    hub.test_pending = True

    # Act
    loop.run()

    # Assert: the score and the reason came back; no chime, no panel, no command
    result = hub.test_results[-1]
    assert result["heard"] == "Maxie." and result["woke"] is False and result["score"] == 0.3
    assert "listen" not in hub.states and hub.chats == [] and loop.player.played == []


def test_a_command_in_one_breath_opens_the_panel_before_he_finishes_it():
    # Arrange: "Hey Max what time is it" with no pause at all (2 s of speech), then silence
    script = silence(0.2) + speech(2.0) + silence(0.8) + silence(9)

    # Act: the first look (0.6 s in) heard only the greeting; the next one heard the name
    loop, hub, at = _frames_until_listen(script, ["Hey", "Hey Max what", "Hey Max, what time is it?"])

    # Assert: "listen" went out ~1.1 s into his speech, not after it ended (2.8 s)
    from maxie.voice.audio import FRAME, RATE
    assert at * FRAME / RATE < 0.2 + 1.3
    assert hub.chats == ["what time is it?"]


def test_a_bare_name_wakes_as_soon_as_the_pause_is_long_enough():
    # Arrange: "Max." then silence: the first look comes at a short breath (too short for a bare name), the next
    # exactly when the pause reaches BARE_PAUSE_S, not 0.3 s later
    from maxie.voice.audio import FRAME, RATE
    from maxie.voice.loop import BARE_PAUSE_S
    loop, hub = make_loop(silence(0.2) + speech(0.5) + silence(0.8) + silence(9), ["Max.", "Max."], DONE)
    loop.speaker = FakeSpeaker(enrolled=True, passes=True)

    # Act
    loop.run()
    at = hub.state_frames[hub.states.index("listen")] * FRAME / RATE

    # Assert: two looks, and "listen" within a frame or two of the pause reaching the bar
    assert len(loop.stt.calls) >= 2
    assert at <= 0.2 + 0.5 + BARE_PAUSE_S + 3 * FRAME / RATE
