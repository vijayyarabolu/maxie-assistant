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
