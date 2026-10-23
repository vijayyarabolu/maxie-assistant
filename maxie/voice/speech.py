"""Speech to text (Whisper on MLX), the wake phrase, sentence splitting, text to speech (Kokoro).

Wake words ("Hey Max", "Hey Maxie", "Max", "Maxie"): every VAD speech segment is transcribed locally
with Whisper (~220 ms on an M3) and matched by the grammar in `wake.py` (bare names only first after a
pause), then the speaker check in `speaker.py`. Runs only while someone is speaking, all on the Mac.

One Whisper model for both the wake check and the command: mlx-whisper keeps a single loaded model, so
alternating tiny and base reloads weights (and re-checks Hugging Face) on every call, which cost more
than base's extra 10 ms.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

import numpy as np

from .audio import level
from .wake import detect

STOP = re.compile(r"^\W*(?:(?:hey|ok|okay)?[\s,]*(?:maxie|maxi|maxy|maxey|max)[\s,]*)?(?:stop|shut up|be quiet|cancel|never mind|nevermind)\b", re.I)
# Conversation mode: a long talk keeps the mic open between replies until he says he is done.
CONVERSE = re.compile(r"^\W*(?:let'?s (?:talk|chat)|can we (?:talk|chat)|(?:i (?:want|need) to )?talk (?:with|to) me"
                      r"|i (?:want|need) to talk|conversation mode)\b[\s,.!?:]*(?:(?:about|for a bit|for a minute)\b[\s,]*)?", re.I)
DONE_TALKING = re.compile(r"^\W*(?:(?:ok(?:ay)?|thanks?|thank you)[\s,]*)*(?:that'?s (?:all|it)(?: for now)?|bye(?: for now)?|goodbye"
                          r"|good ?night|talk (?:to you )?later|end (?:the )?conversation|i'?m done(?: talking)?|we'?re done)\W*$", re.I)
# "Maxie, go to sleep": the whole command pauses Maxie; "put my Mac to sleep" is an ordinary turn.
SLEEP = re.compile(r"^\W*(?:(?:hey )?(?:maxie|maxi|maxy|max)\W+)?(?:go to (?:sleep|bed)|take a break|pause(?: yourself| maxie)?"
                   r"|stop listening)\W*$", re.I)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
MODELS = {"tiny": "mlx-community/whisper-tiny", "base": "mlx-community/whisper-base-mlx",
          "small": "mlx-community/whisper-small-mlx"}
KOKORO_DIR = Path(os.environ.get("KOKORO_DIR", Path.home() / ".maxie" / "models" / "kokoro"))
RATE_SILENCE = 16000
TEST_LINE = "Hi there. This is how I sound."  # Settings › Voice › Test; pre-synthesised so it plays at once
# Whisper's prompt for audio that may start with the wake word: it spells the name the way the grammar knows.
# Measured on the wake fixtures in simulated rooms: base misses 22/90 → 4/90, tiny (asleep) 26/54 →
# 6/90, still 0 false wakes on "maximum", "HBO Max", "Max is…".
WAKE_PROMPT = "Hey Maxie. Maxie, Max."
STOP_PROMPT = "Hey Maxie. Max. Maxie, stop."  # over her own voice: the one thing he'd say (7/8 vs 3/8)
WAKE_TOKENS = 32   # a wake check is a few words; caps a prompt-induced loop ("Maxie. Maxie. …" x75) and its cost
LOOP = re.compile(r"(\b.{2,40}?)(?:\s*\1){3,}", re.I | re.S)  # the same phrase 4+ times in a row


def unloop(text: str) -> str:
    """One copy of a phrase Whisper repeated 4+ times in a row (it does that on short audio with a prompt)."""
    return LOOP.sub(r"\1", text).strip()


def is_wake(text: str) -> tuple[bool, str]:
    """(heard a wake word at the start of a segment after a pause, the command after it)."""
    r = detect(text, after_silence=True)
    return r.woke, r.rest


def conversation_start(text: str) -> tuple[bool, str]:
    """(he wants a long talk, what he said after asking for it)."""
    m = CONVERSE.match(text or "")
    return (True, (text[m.end():] or "").strip()) if m else (False, "")


def is_done_talking(text: str) -> bool:
    return bool(DONE_TALKING.match(text or ""))


def is_sleep(text: str) -> bool:
    return bool(SLEEP.match(text or ""))


def is_stop(text: str) -> bool:
    return bool(STOP.match(text or "")) and len((text or "").split()) <= 5


def sentences(buffer: str, final: bool = False) -> tuple[list[str], str]:
    """Split streamed text into whole sentences to speak now, and the tail to keep."""
    parts = SENTENCE_END.split(buffer)
    if final:
        return [p.strip() for p in parts if p.strip()], ""
    if len(parts) <= 1:
        return [], buffer
    return [p.strip() for p in parts[:-1] if p.strip()], parts[-1]


FIRST_WORDS_MIN = 10    # a first sentence this long is spoken in two pieces...
CLAUSE_WORDS_MIN = 4    # ...at its first comma with at least this many words on each side
CLAUSE = re.compile(r"(?<=,)\s+")


def first_pieces(sentence: str) -> list[str]:
    """The reply's first sentence, split at its first comma when it is long: Kokoro makes the first clause in about
    half the time, so the first words come sooner and the rest is made while they play (maxie.voice.ahead)."""
    words = sentence.split()
    m = CLAUSE.search(sentence)
    if len(words) < FIRST_WORDS_MIN or m is None:
        return [sentence]
    head, tail = sentence[:m.start()], sentence[m.end():]
    if min(len(head.split()), len(tail.split())) < CLAUSE_WORDS_MIN:
        return [sentence]
    return [head, tail]


class STT:
    """Awake: one model (base) for the wake check and the command. Asleep (10 min idle): the wake check
    runs on tiny, which evicts base from mlx-whisper's single slot and frees its memory."""

    def __init__(self, wake_model: str = "base", command_model: str = "base"):
        self.wake_model, self.command_model = MODELS[wake_model], MODELS[command_model]
        self._awake_wake_model = self.wake_model
        self._one_at_a_time = threading.Lock()

    def sleep(self) -> None:
        self.wake_model = MODELS["tiny"]
        self.transcribe(np.zeros(RATE_SILENCE, dtype=np.float32), quick=True)  # loads tiny, drops base
        _clear_mlx_cache()

    def wake(self) -> None:
        self.wake_model = self._awake_wake_model

    def transcribe(self, audio: np.ndarray, quick: bool = False, wake: bool = False, stop: bool = False) -> str:
        """`quick`: the wake check (the wake model). `wake`: the command model on a segment that starts with the
        wake word. Both get the wake prompt; a follow-up command doesn't. `stop`: what he said over her, with the stop
        prompt."""
        import mlx_whisper
        extra = {"sample_len": WAKE_TOKENS} if quick else {}
        with self._one_at_a_time:  # the barge-in watcher runs on its own thread; MLX is not thread-safe
            out = mlx_whisper.transcribe(audio.astype(np.float32),
                                         path_or_hf_repo=self.wake_model if quick else self.command_model,
                                         language="en", fp16=True, condition_on_previous_text=False, temperature=0.0,
                                         initial_prompt=STOP_PROMPT if stop else WAKE_PROMPT if quick or wake else None,
                                         **extra)
        text = out.get("text") or ""
        return unloop(text) if quick or wake else text  # only the prompt makes Whisper loop

    def warm(self) -> None:
        self.transcribe(np.zeros(RATE_SILENCE, dtype=np.float32), quick=True)
        self.transcribe(np.zeros(RATE_SILENCE, dtype=np.float32))


def _clear_mlx_cache() -> None:
    import gc
    gc.collect()
    try:
        import mlx.core as mx
        mx.clear_cache()
    except (ImportError, AttributeError):
        pass


class TTS:
    """Kokoro (kokoro-onnx, voice from Settings `voice`, default af_heart); Apple `say` when the model
    files are missing. Returns (samples float32, rate). `sleep()` drops the ONNX session after 10 min
    idle (the cached acks stay, so the first word after a wake is still instant); `synth` reloads it."""

    def __init__(self, voice: str = "af_heart", speed: float = 1.05):
        self.voice, self.speed = voice, speed
        self.cache: dict[str, tuple[np.ndarray, int]] = {}  # the fixed acks, synthesised once at start
        self.kokoro = None
        self.available = (KOKORO_DIR / "kokoro-v1.0.onnx").exists() and (KOKORO_DIR / "voices-v1.0.bin").exists()
        self._load()

    def _load(self) -> None:
        if self.available and self.kokoro is None:
            from kokoro_onnx import Kokoro
            self.kokoro = Kokoro(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))

    def sleep(self) -> None:
        self.kokoro = None
        _clear_mlx_cache()

    def wake(self) -> None:
        self._load()

    @property
    def engine(self) -> str:
        return "kokoro" if self.available else "apple-say"

    def warm(self) -> None:
        """Pre-synthesise the acknowledgements so the first audio after a command is instant."""
        from ..personality import ACKS
        for text in list(ACKS.values()) + ["Stopped.", "I didn't catch that.", "Done.", "One moment.", TEST_LINE]:
            if text not in self.cache:
                self.cache[text] = self.synth(text)

    def synth(self, text: str) -> tuple[np.ndarray, int]:
        if not text.strip():
            return np.zeros(0, dtype=np.float32), 24000
        if text in self.cache:
            return self.cache[text]
        self._load()
        if self.kokoro:
            samples, rate = self.kokoro.create(text, voice=self.voice, speed=self.speed, lang="en-us")
        else:
            samples, rate = self._say(text)
        return level(np.asarray(samples, dtype=np.float32), int(rate)), int(rate)  # even, and never clipped

    @staticmethod
    def _say(text: str) -> tuple[np.ndarray, int]:
        import soundfile as sf
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            path = f.name
        try:
            subprocess.run(["say", "-o", path, "--data-format=LEI16@22050", text], check=True, timeout=60,
                           capture_output=True)
            data, rate = sf.read(path, dtype="float32")
            return (data[:, 0] if data.ndim > 1 else data), int(rate)
        finally:
            Path(path).unlink(missing_ok=True)
