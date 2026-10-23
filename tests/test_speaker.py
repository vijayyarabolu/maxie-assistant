"""Speaker check: only his voice wakes Maxie with a bare "Max"."""

import numpy as np
import pytest

from maxie.voice.speaker import MODEL_PATH, SpeakerCheck, fbank

RATE = 16000


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def _fake_embedder(table):
    """Embeds a clip by its first sample value: lets tests pick voices without a model."""
    return lambda audio: _unit(table[int(audio[0])])


def test_fbank_has_kaldi_shape():
    # Arrange: 1 s of noise
    audio = np.random.default_rng(0).standard_normal(RATE).astype(np.float32) * 0.1

    # Act
    feats = fbank(audio)

    # Assert: 25 ms frames every 10 ms, 80 mel bins, mean-normalised over time
    assert feats.shape == (98, 80)
    assert np.allclose(feats.mean(axis=0), 0, atol=1e-4)


def test_not_enrolled_until_five_clips(tmp_path):
    # Arrange
    rng = np.random.default_rng(1)
    me = rng.standard_normal(256)
    check = SpeakerCheck(tmp_path / "voice.npz", embed=_fake_embedder({0: me}))

    # Act / Assert
    assert not check.enrolled
    with pytest.raises(ValueError):
        check.enroll([np.zeros(RATE, np.float32)] * 3)


def test_his_voice_passes_and_a_stranger_fails(tmp_path):
    # Arrange: his five clips are close to one direction, a stranger's is far from it
    rng = np.random.default_rng(2)
    me = rng.standard_normal(256)
    table = {i: me + 0.25 * rng.standard_normal(256) for i in range(6)}
    table[9] = rng.standard_normal(256)
    check = SpeakerCheck(tmp_path / "voice.npz", embed=_fake_embedder(table))
    clips = [np.full(RATE, i, np.float32) for i in range(5)]

    # Act
    check.enroll(clips)

    # Assert
    assert check.enrolled
    assert check.verify(np.full(RATE, 5, np.float32))
    assert not check.verify(np.full(RATE, 9, np.float32))


def test_enrollment_survives_a_restart_and_can_be_forgotten(tmp_path):
    # Arrange
    rng = np.random.default_rng(3)
    me = rng.standard_normal(256)
    table = {i: me + 0.2 * rng.standard_normal(256) for i in range(6)}
    SpeakerCheck(tmp_path / "voice.npz", embed=_fake_embedder(table)).enroll(
        [np.full(RATE, i, np.float32) for i in range(5)])

    # Act
    again = SpeakerCheck(tmp_path / "voice.npz", embed=_fake_embedder(table))

    # Assert
    assert again.enrolled and again.verify(np.full(RATE, 5, np.float32))
    again.forget()
    assert not again.enrolled and not (tmp_path / "voice.npz").exists()


@pytest.mark.skipif(not MODEL_PATH.exists(), reason="speaker model not downloaded (Mac only)")
def test_real_model_gives_unit_embeddings(tmp_path):
    check = SpeakerCheck(tmp_path / "voice.npz")
    audio = np.random.default_rng(4).standard_normal(RATE * 2).astype(np.float32) * 0.05
    e = check.embed(audio)
    assert e.shape == (256,) and abs(np.linalg.norm(e) - 1) < 1e-4


def _len_embedder(table, crop):
    """Like _fake_embedder, but a wake-length clip (the first second) embeds as its own entry: short clips of
    the same voice score lower against a print made from whole sentences, as they do with the real model."""
    return lambda audio: _unit(table[(int(audio[0]), len(audio) <= crop)])


def _voices(seed, short_noise=0.9):
    rng = np.random.default_rng(seed)
    me, stranger = rng.standard_normal(256), rng.standard_normal(256)
    table = {}
    for i in range(7):
        table[(i, False)] = me + 0.25 * rng.standard_normal(256)
        table[(i, True)] = me + short_noise * rng.standard_normal(256)
    table[(9, False)], table[(9, True)] = stranger, stranger + 0.3 * rng.standard_normal(256)
    table[(8, False)] = table[(8, True)] = rng.standard_normal(256)  # another stranger: the TV
    return table


def test_enrollment_sets_a_wake_threshold_from_the_wake_word_part(tmp_path):
    # Arrange: five 2.5 s sentences that start with the wake word ("Maxie" alone scored below 0.60)
    from maxie.voice.speaker import WAKE_CROP_S
    crop = int(WAKE_CROP_S * RATE)
    check = SpeakerCheck(tmp_path / "voice.npz", embed=_len_embedder(_voices(5), crop))

    # Act
    check.enroll([np.full(int(2.5 * RATE), i, np.float32) for i in range(5)])
    me_short, stranger_short = check.judge(np.full(crop, 6, np.float32), "maxie"), check.judge(np.full(crop, 9, np.float32), "maxie")

    # Assert: the bar for a short "Maxie" is lower than for a sentence, his passes, a stranger's doesn't
    assert check.wake_threshold < check.threshold
    assert me_short.woke and me_short.score >= me_short.threshold
    assert not stranger_short.woke and stranger_short.reason == "not your voice"


def test_an_old_enrollment_without_wake_clips_uses_the_measured_default(tmp_path):
    # Arrange: what his Oct 17 enrollment stored (prints + one threshold, nothing for short clips)
    from maxie.voice.speaker import WAKE_THRESHOLD_DEFAULT
    prints = np.stack([_unit(np.random.default_rng(i).standard_normal(256)) for i in range(5)])
    with (tmp_path / "voice.npz").open("wb") as f:
        np.savez(f, clips=prints, threshold=np.float32(0.6))

    # Act
    check = SpeakerCheck(tmp_path / "voice.npz", embed=lambda a: prints[0])

    # Assert
    assert check.threshold == pytest.approx(0.6)
    assert check.wake_threshold == pytest.approx(WAKE_THRESHOLD_DEFAULT)


def test_hey_forms_wake_whatever_they_score_and_the_score_is_logged(tmp_path):
    # Live on a real Mac: four of the user's own "Hey Maxie"s scored 0.06-0.18 on 0.4-0.6 s of audio, under the
    # 0.25 bar of the time, and were ignored. A short clip's print is noise: hey forms wake, the score is only logged
    check = SpeakerCheck(tmp_path / "voice.npz", embed=_len_embedder(_voices(6, short_noise=1.4), 16000))
    check.enroll([np.full(2 * RATE, i, np.float32) for i in range(5)])

    verdicts = [check.judge(np.full(16000, v, np.float32), form) for v, form in [(6, "hey maxie"), (8, "hey max")]]

    assert all(v.woke and v.reason == "hey form" for v in verdicts)
    rows = check.recent_scores()
    assert [r["form"] for r in rows] == ["hey maxie", "hey max"] and all(r["score"] is not None for r in rows)
    assert {"at", "form", "score", "threshold", "seconds", "woke", "reason"} <= set(rows[0])


def test_bare_names_wait_for_enrollment(tmp_path):
    check = SpeakerCheck(tmp_path / "voice.npz", embed=lambda a: np.ones(256, np.float32))
    verdict = check.judge(np.zeros(RATE, np.float32), "maxie")
    assert not verdict.woke and verdict.reason == "not enrolled"
    assert check.judge(np.zeros(RATE, np.float32), "hey max").woke


def test_his_hey_form_scores_make_the_wake_threshold_real(tmp_path):
    # Arrange: an old enrollment, then a dozen real "Hey Maxie" wakes from him that score ~0.38-0.46
    from maxie.voice.speaker import CALIBRATE_MIN, THRESHOLD_FLOOR, WAKE_THRESHOLD_DEFAULT
    me = _unit(np.random.default_rng(8).standard_normal(256))
    with (tmp_path / "voice.npz").open("wb") as f:
        np.savez(f, clips=np.stack([me] * 5), threshold=np.float32(0.6))
    rng = np.random.default_rng(9)
    clips = {}
    for i in range(CALIBRATE_MIN + 4):
        target = 0.38 + 0.08 * rng.random()
        other = _unit(rng.standard_normal(256) - (rng.standard_normal(256) @ me) * me)
        clips[i] = _unit(target * me + np.sqrt(1 - target ** 2) * other)
    check = SpeakerCheck(tmp_path / "voice.npz", embed=lambda a: clips[int(a[0])])

    # Act
    for i in clips:
        check.judge(np.full(RATE, i, np.float32), "hey maxie")

    # Assert: the bar moved under his real scores, never below the floor
    assert THRESHOLD_FLOOR <= check.wake_threshold < WAKE_THRESHOLD_DEFAULT
