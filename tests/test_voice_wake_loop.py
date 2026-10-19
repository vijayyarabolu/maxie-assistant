"""The voice loop with Maxie's wake words: which forms are on, the speaker check, voice enrollment."""

from test_voice import make_loop, silence, speech


class FakeSpeaker:
    def __init__(self, enrolled=False, passes=True):
        self.enrolled, self.passes, self.checked, self.enrolled_with = enrolled, passes, 0, None

    def verify(self, audio):
        self.checked += 1
        return self.enrolled and self.passes

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


def test_another_voice_cannot_wake_once_he_enrolled():
    loop, hub = _run(["Hey Max what time is it"], speaker=FakeSpeaker(enrolled=True, passes=False))
    assert hub.chats == []


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
