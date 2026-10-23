"""Wake words for Maxie: "Hey Max", "Hey Maxie", "Max", "Maxie"."""

import pytest

from maxie.voice.wake import ALL_FORMS, WakeResult, detect


@pytest.mark.parametrize("text, form, rest", [
    ("Hey Max, what's on my calendar?", "hey max", "what's on my calendar?"),
    ("hey maxie", "hey maxie", ""),
    ("Hey Maxi, open Spotify.", "hey maxie", "open Spotify."),
    ("Okay Max what time is it", "hey max", "what time is it"),
    ("Max what's on my calendar", "max", "what's on my calendar"),
    ("Max, what's on my calendar?", "max", "what's on my calendar?"),
    ("Max.", "max", ""),
    ("Maxie, play some music", "maxie", "play some music"),
    ("Maxey turn the volume down", "maxie", "turn the volume down"),
])
def test_wakes_on_all_four_forms(text, form, rest):
    # Arrange / Act
    r = detect(text, after_silence=True)

    # Assert
    assert r == WakeResult(True, form, rest)


@pytest.mark.parametrize("text", [
    "maximum volume please",
    "max out the volume",
    "Max is coming over tonight",
    "Max's car is outside",
    "max speed on the fan",
    "I watched it on HBO Max",
    "Mad Max is a great movie",
    "the max I can do is ten",
    "Maximilian called you",
    "Max and I went to lunch",
    "Max was right about that",
    "",
])
def test_ignores_max_as_an_ordinary_word(text):
    assert not detect(text, after_silence=True).woke


def test_bare_name_needs_silence_before_it():
    # Arrange: the segment was cut mid-speech (12 s overflow), so "Max" may be mid-sentence
    text = "Max what's the weather"

    # Act / Assert
    assert not detect(text, after_silence=False).woke
    assert detect("Hey Max what's the weather", after_silence=False).woke


def test_only_enabled_forms_wake():
    enabled = {"hey max", "hey maxie"}
    assert not detect("Max what time is it", after_silence=True, enabled=enabled).woke
    assert detect("Hey Max what time is it", after_silence=True, enabled=enabled).woke
    assert not detect("Hey Maxie", after_silence=True, enabled={"max"}).woke


def test_bare_forms_are_marked_for_the_speaker_check():
    assert detect("Max open notes", after_silence=True).bare
    assert not detect("Hey Max open notes", after_silence=True).bare


def test_all_forms_listed():
    assert ALL_FORMS == ("hey max", "hey maxie", "max", "maxie")


@pytest.mark.parametrize("text, form, rest", [
    # how Whisper wrote his real wake words in a room (scripts: fixtures through simulated rooms)
    ("Maxie was on my calendar.", "maxie", "was on my calendar."),  # "what's" heard as "was"
    ("Hey Maxine.", "hey maxie", ""),
    ("Maxim, what's on my calendar?", "maxie", "what's on my calendar?"),
    ("Maxine, what's up my calendar?", "maxie", "what's up my calendar?"),
])
def test_wakes_on_the_way_whisper_spells_him_in_a_room(text, form, rest):
    assert detect(text, after_silence=True) == WakeResult(True, form, rest)


@pytest.mark.parametrize("text, form", [("Game Max.", "max"), ("He max what time is it?", "max"), ("Game Maxie.", "maxie")])
def test_a_misheard_hey_counts_as_a_bare_name(text, form):
    # A garbled greeting is not proof he addressed Maxie: it needs his voice and a pause before it, like a bare name
    r = detect(text, after_silence=True)
    assert r.woke and r.form == form and r.bare
    assert not detect(text, after_silence=False).woke


@pytest.mark.parametrize("text", ["He maxed out his card", "Maxim is a magazine", "The game maxes out at ten"])
def test_the_new_spellings_do_not_wake_on_ordinary_words(text):
    # "Maxim is…" is ordinary talk about it; the others never start with the name at all
    assert not detect(text, after_silence=True).woke


@pytest.mark.parametrize("text, only", [("Max", True), ("Maxie. Max", True), ("Maxie, Max.", True), ("", True),
                                        ("what time is it", False), ("Max is late", False)])
def test_a_rest_of_only_names_is_the_prompt_echoing(text, only):
    from maxie.voice.wake import only_names
    assert only_names(text) is only


@pytest.mark.parametrize("text", ["Maxie is going to be late again.", "Maxie is a great movie.", "Maxie and I went out"])
def test_the_prompt_turning_max_into_maxie_does_not_make_talk_a_wake(text):
    assert not detect(text, after_silence=True).woke
