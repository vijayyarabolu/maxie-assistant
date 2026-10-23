"""the Mac's own apps through AppleScript: music control that never launches Music, and an app that
never answers (a macOS permission question that can't show while the Mac is locked) said in words."""

import subprocess

import pytest

from maxie.hands import mac


def test_pausing_music_never_launches_it():
    # telling a closed Music to pause launched it, and with the Mac locked that hung 15 s
    script = mac.music_if_running("pause")
    assert script.startswith('if application "Music" is running then') and "nothing was playing" in script


def test_an_app_that_never_answers_is_said_in_words(monkeypatch):
    def hang(*a, **k):
        raise subprocess.TimeoutExpired("osascript", mac.OSA_TIMEOUT_S)

    monkeypatch.setattr(mac.subprocess, "run", hang)
    with pytest.raises(RuntimeError, match="Automation"):
        mac._osa('tell application "Music" to pause')
