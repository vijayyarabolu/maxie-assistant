"""`python -m maxie.voice [--bench file.wav]`: run the voice loop, or time the pipeline on a file."""

from __future__ import annotations

import argparse
import functools
import sys
import time

from ..config import Settings, data_dir, secret
from . import aec
from .audio import RATE, VAD, Mic, Player, Segmenter
from .loop import Hub, VoiceLoop
from .speaker import MODEL_PATH, SpeakerCheck
from .speech import STT, TTS, is_wake


def bench(path: str, stt: STT, tts: TTS) -> int:
    """Wake detection + STT + first TTS chunk on a recorded or synthesised utterance."""
    import numpy as np
    import soundfile as sf
    audio, rate = sf.read(path, dtype="float32")
    if audio.ndim > 1:
        audio = audio[:, 0]
    if rate != RATE:
        idx = np.linspace(0, len(audio) - 1, int(len(audio) * RATE / rate))
        audio = np.interp(idx, np.arange(len(audio)), audio).astype(np.float32)
    vad, seg = VAD(), Segmenter()
    segments = []
    for i in range(0, len(audio) - 512, 512):
        out = seg.feed(audio[i:i + 512], vad.prob(audio[i:i + 512]), i / RATE)
        if out is not None:
            segments.append(out)
    if seg.in_speech:
        segments.append(np.concatenate(seg._frames))
    print(f"{len(audio) / RATE:.2f}s of audio, {len(segments)} speech segment(s)")
    stt.warm()  # the loop runs warm; cold numbers include model loading and MLX compilation
    tts.warm()
    for s in segments:
        t0 = time.perf_counter()
        quick = stt.transcribe(s, quick=True)
        t1 = time.perf_counter()
        woke, rest = is_wake(quick)
        full = stt.transcribe(s)
        t2 = time.perf_counter()
        samples, r = tts.synth("Let me check that for you.")  # not a cached ack: a real synthesis
        t3 = time.perf_counter()
        print(f"  wake ({stt.wake_model.split('/')[-1]}): \"{quick}\" ({(t1 - t0) * 1000:.0f} ms) "
              f"wake={woke} rest=\"{rest}\"")
        print(f"  {stt.command_model.split('/')[-1]}: \"{full}\" ({(t2 - t1) * 1000:.0f} ms)")
        print(f"  tts first chunk ({tts.engine}): {(t3 - t2) * 1000:.0f} ms, {len(samples) / r:.2f}s of audio")
        print(f"  end of speech -> first audio: wake path {(t1 - t0 + t3 - t2) * 1000:.0f} ms, "
              f"command path {(t2 - t1 + t3 - t2) * 1000:.0f} ms")
    return 0


def log(message: str) -> None:
    """voice.log lines carry the time, so a wake can be lined up with what he did."""
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}", flush=True)


def open_mic(name: str, factory=None):
    """The mic from Settings (voice_input_device) or, when it is unplugged or misnamed, the default one:
    a bad name must never crash-loop the launchd agent."""
    factory = factory or Mic
    if name:
        try:
            return factory(device=name)
        except Exception as e:  # sounddevice raises ValueError/PortAudioError for a missing device  # noqa: BLE001
            log(f"voice: mic {name!r} not available ({e}); using the default input")
    return factory(device=None)


def _not_on_speakers() -> bool:
    """Barge-in needs headphones: through the built-in speakers the mic hears Maxie's own voice."""
    import sounddevice as sd
    try:
        return "speaker" not in str(sd.query_devices(kind="output")["name"]).lower()
    except Exception:  # no output device at all: never interrupt  # noqa: BLE001
        return False


def _speaker_check():
    """His voice prints (Settings › Voice). None when the model file isn't on this Mac: then only
    "Hey Max" / "Hey Maxie" wake Maxie."""
    if not MODEL_PATH.exists():
        log(f"voice: no speaker model at {MODEL_PATH}; bare \"Max\" stays off")
        return None
    check = SpeakerCheck(data_dir() / "voice" / "speaker.npz")
    log(f"voice: speaker check {'on (enrolled), wake bar ' + format(check.wake_threshold, '.2f') if check.enrolled else 'ready, not enrolled'}")
    return check


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="maxie.voice")
    ap.add_argument("--bench", help="time the pipeline on this audio file instead of listening")
    ap.add_argument("--hub", default=None)
    ap.add_argument("--command-model", default="base", choices=["tiny", "base", "small"])
    args = ap.parse_args(argv)
    settings = Settings.load()
    stt, tts = STT(wake_model=args.command_model, command_model=args.command_model), TTS(voice=settings.voice, speed=settings.voice_speed)
    if args.bench:
        return bench(args.bench, stt, tts)
    token = secret("HUB_TOKEN")
    if not token:
        print("No HUB_TOKEN yet: in Settings, Access, press Connect the Mac app (or set HUB_TOKEN)", file=sys.stderr)
        return 1
    hub = Hub(args.hub or f"http://{settings.hub_host}:{settings.hub_port}", token)
    log(f"voice: stt {stt.command_model}, tts {tts.engine} ({settings.voice}); warming up")
    stt.warm()
    tts.warm()
    # her voice out of the mic while she speaks on the built-in speakers; in headphones there is no echo
    canceller = aec.create(log, wanted=lambda: not _not_on_speakers())
    mic_filter = canceller.capture if canceller else None
    with open_mic(settings.voice_input_device, factory=functools.partial(Mic, filter=mic_filter)) as mic:
        loop = VoiceLoop(ears=mic, vad=VAD(), stt=stt, tts=tts, hub=hub,
                         player=Player(on_write=canceller.render if canceller else None),
                         log=log, barge_in_ok=_not_on_speakers,
                         speaker=_speaker_check(), wake_forms=frozenset(settings.wake_words))
        try:
            loop.run()
        except KeyboardInterrupt:
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
