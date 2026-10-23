"""Speaker check: is this his voice?

WeSpeaker ResNet34-LM (VoxCeleb, CC-BY-4.0, 26 MB ONNX, ~21 ms per 1.5 s clip on the M3 CPU) turns a
speech segment into a 256-d voice print. He enrolls with five short phrases; a segment passes when its
cosine to his mean print clears a threshold set from his own clips. Only voice prints are stored (no
audio), on the Mac, and "Forget my voice" deletes them.

Which wakes need it: "Hey Max(ie)" is an explicit address and wakes for anyone; its score is only
logged (live, his own "Hey Maxie"s scored 0.06-0.18 on 0.4-0.6 s of audio: a print that short is noise, and a bar
on it silenced him again). A bare "Max" / "Maxie" needs his voice. A wake word is ~0.6 s, and a short clip scores lower against prints made from whole
sentences, so wakes use their own bar: set at enrollment from the first second of each phrase (they all start
with the wake word), else a measured default, then lowered to sit under his own logged "Hey Maxie" scores.
Every score is logged (`wake-scores.jsonl` beside the prints) so Settings › Voice can show why.

The model file is pinned by SHA-256 and loaded lazily (the voice helper unloads it when idle).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

MODEL_PATH = Path.home() / ".maxie" / "models" / "speaker" / "voxceleb_resnet34_LM.onnx"
MODEL_URL = ("https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM/resolve/"
             "f0c48c298fd835726c27956a5d617bad7115627e/voxceleb_resnet34_LM.onnx")
MODEL_SHA256 = "7bb2f06e9df17cdf1ef14ee8a15ab08ed28e8d0ef5054ee135741560df2ec068"

RATE = 16000
ENROLL_CLIPS = 5
FRAME_LEN, FRAME_SHIFT, N_FFT, N_MELS = 400, 160, 512, 80   # Kaldi fbank: 25 ms / 10 ms, 80 bins
PREEMPH, LOW_HZ = 0.97, 20.0
THRESHOLD_FLOOR, THRESHOLD_CEIL = 0.35, 0.60   # cosine bounds for the per-person threshold
THRESHOLD_MARGIN = 0.15                          # below his weakest enrollment match
WAKE_CROP_S = 1.0             # the wake word at the start of each enrollment phrase
WAKE_THRESHOLD_DEFAULT = 0.38  # enrollments without wake clips; measured on short clips
CALIBRATE_MIN = 8             # this many of his "Hey Maxie" scores since enrolling move the wake bar under them
CALIBRATE_MARGIN = 0.05       # below the 10th percentile of those scores
SCORES_KEEP = 500             # lines kept in wake-scores.jsonl
HEY_FORMS = ("hey max", "hey maxie")

Embedder = Callable[[np.ndarray], np.ndarray]


def _mel(hz: np.ndarray) -> np.ndarray:
    return 1127.0 * np.log1p(hz / 700.0)


def _mel_banks() -> np.ndarray:
    """Kaldi-style triangular filters, equally spaced on the mel scale, (N_MELS, N_FFT//2 + 1)."""
    fft_mel = _mel(np.arange(N_FFT // 2 + 1) * RATE / N_FFT)
    lo, hi = _mel(np.array(LOW_HZ)), _mel(np.array(RATE / 2))
    edges = np.linspace(lo, hi, N_MELS + 2)
    left, centre, right = edges[:-2, None], edges[1:-1, None], edges[2:, None]
    up = (fft_mel - left) / (centre - left)
    down = (right - fft_mel) / (right - centre)
    return np.clip(np.minimum(up, down), 0, None).astype(np.float32)


_BANKS = _mel_banks()
_WINDOW = (0.54 - 0.46 * np.cos(2 * np.pi * np.arange(FRAME_LEN) / (FRAME_LEN - 1))).astype(np.float32)


def fbank(audio: np.ndarray) -> np.ndarray:
    """80-bin log-mel features the way WeSpeaker was trained (Kaldi fbank, no dither), mean-normalised."""
    x = np.asarray(audio, dtype=np.float32) * 32768.0
    n = 1 + max(0, (len(x) - FRAME_LEN) // FRAME_SHIFT)
    idx = np.arange(FRAME_LEN)[None, :] + FRAME_SHIFT * np.arange(n)[:, None]
    frames = x[np.clip(idx, 0, len(x) - 1)]
    frames = frames - frames.mean(axis=1, keepdims=True)
    frames = np.concatenate([frames[:, :1] * (1 - PREEMPH), frames[:, 1:] - PREEMPH * frames[:, :-1]], axis=1)
    power = np.abs(np.fft.rfft(frames * _WINDOW, n=N_FFT)) ** 2
    feats = np.log(np.maximum(power @ _BANKS.T, np.finfo(np.float32).eps))
    return (feats - feats.mean(axis=0, keepdims=True)).astype(np.float32)


def _unit(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32).reshape(-1)
    return v / max(float(np.linalg.norm(v)), 1e-9)


def verify_model(path: Path = MODEL_PATH) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != MODEL_SHA256:
        raise RuntimeError(f"speaker model {path.name} has an unexpected SHA-256; delete it and download again")


@dataclass(frozen=True)
class Verdict:
    """One wake's speaker decision, for the log and Settings › Voice › Test wake word."""
    woke: bool
    score: float | None
    threshold: float
    reason: str   # "hey form" | "his voice" | "not your voice" | "not enrolled"


class SpeakerCheck:
    def __init__(self, store: Path, embed: Embedder | None = None, model_path: Path = MODEL_PATH):
        self.store = Path(store)
        self.scores_path = self.store.with_name("wake-scores.jsonl")
        self.model_path = model_path
        self._embed = embed
        self._session = None
        self._mtime = self._stamp()
        self.clips, self.threshold, self._enrolled_wake = self._load()
        self._wake_cache: float | None = None

    # --- the model -------------------------------------------------------------------------------
    def embed(self, audio: np.ndarray) -> np.ndarray:
        if self._embed is not None:
            return _unit(self._embed(audio))
        if self._session is None:
            import onnxruntime as ort
            verify_model(self.model_path)
            self._session = ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"])
        feats = fbank(audio)[None]
        return _unit(self._session.run(None, {"feats": feats})[0])

    def unload(self) -> None:
        self._session = None

    # --- his voice -------------------------------------------------------------------------------
    @property
    def enrolled(self) -> bool:
        return self.clips is not None

    def enroll(self, clips: Sequence[np.ndarray]) -> float:
        """Five clips of him saying a phrase. Returns the threshold chosen for him."""
        if len(clips) < ENROLL_CLIPS:
            raise ValueError(f"need {ENROLL_CLIPS} clips of your voice, got {len(clips)}")
        prints = np.stack([self.embed(c) for c in clips])
        wakes = np.stack([self.embed(c[:int(WAKE_CROP_S * RATE)]) for c in clips])
        others = [_unit(np.delete(prints, i, axis=0).mean(axis=0)) for i in range(len(prints))]
        loo = [float(prints[i] @ others[i]) for i in range(len(prints))]
        wake_loo = [float(wakes[i] @ others[i]) for i in range(len(prints))]
        threshold = _bar(min(loo))
        wake = min(_bar(min(wake_loo)), threshold)
        self.store.parent.mkdir(parents=True, exist_ok=True)
        with self.store.open("wb") as f:
            np.savez(f, clips=prints, threshold=np.float32(threshold), wake_threshold=np.float32(wake))
        self.clips, self.threshold, self._enrolled_wake, self._mtime = prints, threshold, wake, self._stamp()
        self._wake_cache = None
        return threshold

    def score(self, audio: np.ndarray) -> float:
        if not self.enrolled:
            return 0.0
        return float(self.embed(audio) @ _unit(self.clips.mean(axis=0)))

    def verify(self, audio: np.ndarray) -> bool:
        return self.enrolled and self.score(audio) >= self.threshold

    @property
    def wake_threshold(self) -> float:
        """The bar for a short wake clip: from enrollment (or the default), lowered under his own logged
        "Hey Maxie" scores once there are enough of them, never under the floor."""
        if self._wake_cache is None:
            base = self._enrolled_wake if self._enrolled_wake is not None else min(WAKE_THRESHOLD_DEFAULT, self.threshold)
            since = self._mtime or 0.0
            his = [r["score"] for r in self.recent_scores()
                   if r["form"] in HEY_FORMS and r.get("woke") and r.get("score") is not None and r.get("ts", 0) >= since]
            if len(his) >= CALIBRATE_MIN:
                base = min(base, float(np.percentile(his, 10)) - CALIBRATE_MARGIN)
            self._wake_cache = max(THRESHOLD_FLOOR, base)
        return self._wake_cache

    def judge(self, audio: np.ndarray, form: str) -> Verdict:
        """Should this wake word wake Maxie? A hey form always (its score is logged: it is how the bare-name bar
        learns his real voice); a bare name needs his voice."""
        score = self.score(audio) if self.enrolled else None
        bar = self.wake_threshold if self.enrolled else THRESHOLD_CEIL
        if form in HEY_FORMS:
            verdict = Verdict(True, score, bar, "hey form")
        elif score is None:
            verdict = Verdict(False, None, bar, "not enrolled")
        else:
            ok = score >= bar
            verdict = Verdict(ok, score, bar, "his voice" if ok else "not your voice")
        self._record(form, verdict, len(audio) / RATE)
        return verdict

    def recent_scores(self, n: int = SCORES_KEEP) -> list[dict]:
        if not self.scores_path.exists():
            return []
        rows = []
        for line in self.scores_path.read_text().splitlines()[-n:]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows

    def _record(self, form: str, v: Verdict, seconds: float) -> None:
        now = time.time()
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)), "ts": round(now, 3), "form": form,
               "score": None if v.score is None else round(v.score, 3), "threshold": round(v.threshold, 3),
               "seconds": round(seconds, 2), "woke": v.woke, "reason": v.reason}
        try:
            self.scores_path.parent.mkdir(parents=True, exist_ok=True)
            lines = self.scores_path.read_text().splitlines() if self.scores_path.exists() else []
            self.scores_path.write_text("\n".join([*lines[-(SCORES_KEEP - 1):], json.dumps(row)]) + "\n")
        except OSError as e:
            print(f"voice: could not log a wake score: {e}", flush=True)
        if v.score is not None and form in HEY_FORMS:
            self._wake_cache = None  # a new sample of his voice

    def refresh(self) -> None:
        """Pick up an enrollment or a "Forget my voice" made elsewhere (the hub deletes the file)."""
        stamp = self._stamp()
        if stamp != self._mtime:
            self._mtime = stamp
            self.clips, self.threshold, self._enrolled_wake = self._load()
            self._wake_cache = None

    def _stamp(self) -> float | None:
        return self.store.stat().st_mtime if self.store.exists() else None

    def forget(self) -> None:
        self.store.unlink(missing_ok=True)
        self.clips, self.threshold, self._enrolled_wake, self._mtime = None, THRESHOLD_CEIL, None, None
        self._wake_cache = None

    def _load(self) -> tuple[np.ndarray | None, float, float | None]:
        if not self.store.exists():
            return None, THRESHOLD_CEIL, None
        with np.load(self.store) as z:
            wake = float(z["wake_threshold"]) if "wake_threshold" in z.files else None
            return z["clips"].astype(np.float32), float(z["threshold"]), wake


def _bar(weakest: float) -> float:
    return float(np.clip(weakest - THRESHOLD_MARGIN, THRESHOLD_FLOOR, THRESHOLD_CEIL))
