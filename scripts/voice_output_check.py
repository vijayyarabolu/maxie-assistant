"""what Maxie really plays, recorded and checked for clicks, clipping and gaps.

The real voice loop (VoiceLoop, Ahead, the Player, Kokoro af_jessica, Whisper for the stop watcher, the echo canceller)
plays 20 replies to a loopback output device and the same device's input records them: the "Microsoft Teams Audio"
driver on this Mac is a CoreAudio loopback (what is played to it comes back bit for bit, and nothing is heard in the
room). Every reply starts with the wake chime; 10 of them are cut short, 5 by ⌥⇧P (the hub's pause) and 5 by talking
over him in headphones (barge-in). On the speakers trials the mic hears his echo, so the stop watcher runs Whisper
while he talks, as it does live. `--stress` adds a thread that hogs the Python GIL and a second Kokoro synthesising
nonstop: the worst the Mac does to the player.

Checked on the recording, automatically:
- click: a sample-to-sample jump (second difference) far above the signal's own around it (>= CLICK_RATIO times the
  RMS of the second difference in the CLICK_WIN_MS around it) and above CLICK_MIN; jumps within 2 ms are one click.
- clip: a sample at or above CLIP_LEVEL (-0.01 dBFS), and the peak in dBFS.
- gap: more than GAP_MS of digital silence that the audio drops into or comes out of without a fade (the 1 ms on
  EDGE_MS on either side above EDGE_RMS): an underrun or a cut. A pause he means (faded) is listed apart, not counted.
- chime: each wake chime has CHIME_CLEAR_MS of silence before his next words, and its first and last 2 ms are soft.
Plus each stop's fade (ms from the stop to silence, read off what the player's callback handed the device), the
device's own underflow reports, and spectrogram sheets.

Usage: .venv/bin/python scripts/voice_output_check.py [--replies 20] [--stress] [--out DIR] [--label after]
       (the "before" numbers: the same script on 30d2ef39's maxie/voice, which it also knows how to tap)
       .venv/bin/python scripts/voice_output_check.py --analyze rec.wav   (just the checks on a recording)
       .venv/bin/python scripts/voice_output_check.py --calibrate         (the click detector on clean / broken audio)
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

LOOPBACK = "Microsoft Teams Audio"
REC_RATE = 48000
CLICK_RATIO = 8.0
CLICK_WIN_MS = 4.0
CLICK_MIN = 0.01
CLIP_LEVEL = 0.999
GAP_MS = 5.0
CHIME_CLEAR_MS = 40   # at least this much silence between the chime and what he says next
EDGE_MS = 3.0
EDGE_RMS = 0.01        # -40 dBFS: the EDGE_MS before (or after) a silence this loud = it was cut, not faded (a
                       # resampler smears a cut over ~1 ms; a 30-80 ms fade's last 3 ms are under -45 dBFS)
REPLIES = (
    "Nothing on your calendar today. I checked Google, Home, Work, invites, birthdays, and the India and US holiday "
    "calendars, and all of them are empty for Wednesday.",
    "Your next event is Maharishi Valmiki Jayanti, an all-day holiday on Monday, October 26. It's on the India "
    "holidays calendar. There are no personal meetings or appointments in the next thirty days.",
    "It's sixty one degrees and clear in Austin right now. Tomorrow is sunny with a high of seventy eight and a low "
    "of fifty five, and there is no rain in the forecast for the rest of the week.",
    "Wow! That's great news. Your offer from Stripe came in this morning, and the recruiter, Priya, wants to talk "
    "at two thirty. Should I put it on your calendar?",
    "You have three unread emails. One from Ananya about the design review, one from GitHub about pull request "
    "eighteen, and a receipt from Uber. Nothing looks urgent.",
    "Okay. I paused the music, turned the volume down to twenty percent, and set a timer for twenty five minutes. "
    "I'll tell you when it's done.",
    "Over the next two weeks there are five holidays: Maharishi Valmiki Jayanti on Monday, Karaka Chaturthi on "
    "Thursday, Halloween on Saturday, Daylight Saving Time ending on Sunday, and Day of the Dead on Monday.",
    "Sure. The quickest way to the airport right now is I thirty five south, about twenty two minutes with light "
    "traffic. Leave by four fifteen to make your five o'clock check in comfortably.",
    "Your battery is at eighty four percent, the Mac has been up for three days, and there are two updates "
    "waiting. I can install them tonight while you sleep, if you like.",
    "Done. I sent Rahul the notes from yesterday's call, with the action items at the top, and copied you. He "
    "usually replies within the hour.",
)


# ---------------------------------------------------------------------------------------------- the checks

def _moving_rms(x: np.ndarray, n: int) -> np.ndarray:
    c = np.concatenate([[0.0], np.cumsum(x.astype(np.float64) ** 2)])
    half = n // 2
    lo = np.clip(np.arange(len(x)) - half, 0, len(x))
    hi = np.clip(np.arange(len(x)) + half + 1, 0, len(x))
    return np.sqrt((c[hi] - c[lo]) / np.maximum(hi - lo, 1))


def clicks(x: np.ndarray, rate: int) -> list[int]:
    """Sample indices of clicks: a jump the audio around it doesn't explain."""
    if len(x) < 3:
        return []
    d2 = np.zeros(len(x))
    d2[1:-1] = x[2:] - 2 * x[1:-1] + x[:-2]
    local = _moving_rms(d2, int(rate * CLICK_WIN_MS / 1000))
    hits = np.where((np.abs(d2) > CLICK_MIN) & (np.abs(d2) > CLICK_RATIO * np.maximum(local, 1e-9)))[0]
    out: list[int] = []
    for i in hits:
        if not out or i - out[-1] > rate * 0.002:
            out.append(int(i))
    return out


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt((x.astype(np.float64) ** 2).mean())) if len(x) else 0.0


def silences(x: np.ndarray, rate: int, min_ms: float = GAP_MS) -> list[tuple[int, int, bool]]:
    """Runs of digital silence longer than min_ms: (start, end, abrupt). Abrupt = the EDGE_MS right before or after
    it is louder than EDGE_RMS (cut, not faded)."""
    z = np.concatenate([[0], (x == 0).astype(np.int8), [0]])
    d = np.diff(z)
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    edge = max(1, int(rate * EDGE_MS / 1000))
    out = []
    for s, e in zip(starts, ends, strict=True):
        if (e - s) * 1000 / rate <= min_ms:
            continue
        before = x[max(0, s - edge):s]
        after = x[e:e + edge]
        if s == 0 or e == len(x):  # the recording's own ends: only what's inside counts
            continue
        abrupt = max(_rms(before), _rms(after)) > EDGE_RMS
        out.append((int(s), int(e), abrupt))
    return out


def sounds(x: np.ndarray, rate: int, apart_ms: float = 20.0) -> list[tuple[int, int]]:
    """Stretches of sound separated by at least apart_ms of digital silence."""
    nz = np.flatnonzero(x)
    if not len(nz):
        return []
    cuts = np.where(np.diff(nz) > rate * apart_ms / 1000)[0]
    starts = np.concatenate([[nz[0]], nz[cuts + 1]])
    ends = np.concatenate([nz[cuts] + 1, [nz[-1] + 1]])
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def chimes(x: np.ndarray, rate: int) -> list[dict]:
    """Each wake chime (a 0.15-0.25 s sound whose energy is in its 520/660 Hz notes): the silence on either side of
    it (it must not touch his speech) and how loud its first and last 2 ms are (it fades in and out)."""
    out, parts = [], sounds(x, rate)
    for k, (a, b) in enumerate(parts):
        if not 0.15 <= (b - a) / rate <= 0.25:
            continue
        seg = x[a:b]
        spec = np.abs(np.fft.rfft(seg)) ** 2
        f = np.fft.rfftfreq(len(seg), 1 / rate)
        if spec[(f > 450) & (f < 750)].sum() < 0.8 * spec.sum():
            continue
        edge = int(rate * 0.002)
        out.append({"at_s": round(a / rate, 2),
                    "silence_before_ms": round((a - parts[k - 1][1]) * 1000 / rate) if k else None,
                    "silence_after_ms": round((parts[k + 1][0] - b) * 1000 / rate) if k + 1 < len(parts) else None,
                    "edges_dbfs": round(20 * np.log10(max(float(np.abs(np.r_[seg[:edge], seg[-edge:]]).max()),
                                                          1e-9)), 1)})
    return out


def check(x: np.ndarray, rate: int) -> dict:
    sil = silences(x, rate)
    peak = float(np.abs(x).max(initial=0))
    rings = chimes(x, rate)
    return {"seconds": round(len(x) / rate, 1), "clicks": clicks(x, rate),
            "chimes": len(rings), "chime_overlaps": sum(1 for c in rings if c["silence_after_ms"] is not None
                                                        and c["silence_after_ms"] < CHIME_CLEAR_MS),
            "chime_edges_dbfs": max((c["edges_dbfs"] for c in rings), default=None),
            "clips": int((np.abs(x) >= CLIP_LEVEL).sum()),
            "peak_dbfs": round(20 * np.log10(max(peak, 1e-9)), 2),
            "gaps": [(s, round((e - s) * 1000 / rate, 1)) for s, e, abrupt in sil if abrupt],
            "pauses": len([1 for *_, abrupt in sil if not abrupt])}


# ---------------------------------------------------------------------------------------------- spectrograms

def sheet(panels: list[tuple[str, np.ndarray]], rate: int, path: Path, marks: list[list[float]] | None = None) -> None:
    """Waveform over spectrogram (0-12 kHz, -110 to -20 dBFS), one panel per clip; a click is a bright vertical line."""
    from PIL import Image, ImageDraw
    from scipy.signal import spectrogram
    w, wave_h, spec_h, pad = 900, 70, 200, 22
    img = Image.new("RGB", (w, len(panels) * (wave_h + spec_h + pad)), "white")
    draw = ImageDraw.Draw(img)
    for k, (title, x) in enumerate(panels):
        y0 = k * (wave_h + spec_h + pad)
        draw.text((4, y0 + 2), title, fill="black")
        y0 += 14
        n = len(x)
        cols = np.array_split(np.abs(x), w) if n >= w else [np.abs(x)]
        for i, c in enumerate(cols):
            h = int(min(1.0, float(c.max(initial=0))) * (wave_h / 2 - 2))
            draw.line([(i, y0 + wave_h // 2 - h), (i, y0 + wave_h // 2 + h)], fill=(40, 90, 200))
        f, t, s = spectrogram(x, fs=rate, nperseg=512, noverlap=448, mode="magnitude")
        db = 20 * np.log10(np.maximum(s, 1e-9))
        db = np.clip((db + 110) / 90, 0, 1)[f <= 12000][::-1]
        heat = (np.stack([db ** 0.6, db ** 1.5, (1 - db) * db * 2.5], axis=-1) * 255).astype(np.uint8)
        spec = Image.fromarray(heat).resize((w, spec_h))
        img.paste(spec, (0, y0 + wave_h))
        for m in (marks[k] if marks else []):
            xm = int(m * w)
            draw.line([(xm, y0), (xm, y0 + wave_h + spec_h)], fill=(220, 0, 0))
    img.save(path)


# ---------------------------------------------------------------------------------------------- the run

class _WriteTap:
    """For the Player before §10 (blocking writes): keeps what was written and whether the device had run dry."""

    def __init__(self, stream, rate: int, log: list):
        self.stream, self.rate, self.log = stream, rate, log

    def write(self, data):
        under = self.stream.write(data)
        self.log.append((time.monotonic(), self.rate, np.array(data, dtype=np.float32).reshape(-1), bool(under)))
        return under

    def stop(self):
        self.stream.stop()

    def close(self):
        self.stream.close()


class TrialHub:
    """Streams this trial's reply like the hub (think, then a few words at a time); ⌥⇧P is `paused`."""

    def __init__(self):
        self.reply, self.paused, self.stops = "", False, 0

    def chat(self, text, screen=False):
        time.sleep(0.8)
        words = self.reply.split(" ")
        for i in range(0, len(words), 4):
            yield {"type": "text", "text": " ".join(words[i:i + 4]) + " "}
            time.sleep(0.06)
        yield {"type": "done", "text": self.reply}

    def is_paused(self):
        return self.paused

    def state(self, s):
        pass

    def notice(self, kind, text=""):
        pass

    def stop(self):
        self.stops += 1

    def pause(self, on):
        pass


def _stress(stop: threading.Event) -> None:
    from maxie.voice.speech import TTS

    def hog():
        while not stop.is_set():
            sum(i * i for i in range(20000))  # pure Python: holds the GIL in 5 ms slices

    def synth():
        tts = TTS(voice="af_heart")
        tts.warm()
        while not stop.is_set():
            tts.synth(REPLIES[0])

    for f in (hog, synth):
        threading.Thread(target=f, daemon=True).start()


def run(replies: int, interrupted: int, stress: bool, out: Path, label: str, seed: int = 8) -> dict:
    import sounddevice as sd
    import soundfile as sf
    from echo_room import ECHO_GAIN, Room, RoomMic, _clip
    from scipy.signal import fftconvolve, resample_poly

    from maxie.voice import aec
    from maxie.voice import audio as player_audio
    from maxie.voice.audio import VAD, Player
    from maxie.voice.loop import Turn, VoiceLoop
    from maxie.voice.speech import STT, TTS

    dev = next(i for i, d in enumerate(sd.query_devices()) if d["name"] == LOOPBACK)
    rng = np.random.default_rng(seed)
    room = Room(echo_gain=ECHO_GAIN)
    canceller = aec.create()
    barge = [False]

    echoes: deque = deque()  # appended on the device's thread (atomic, no lock), read by the echo thread

    def heard_by_the_mic(samples, rate):  # the device's thread: the canceller's reference, as in the voice helper
        if barge[0]:
            return
        if canceller:
            canceller.render(samples, rate)
        echoes.append((time.monotonic(), samples, rate))

    latency = getattr(player_audio, "OUT_LATENCY_S", 0.03)  # 30d2ef39's Player had no such setting

    def echo() -> None:  # the speakers trials: his echo in the room once the device has played it (not on its thread)
        to_16k, at_rate = None, None
        while True:
            if not echoes:
                time.sleep(0.005)
                continue
            t, samples, rate = echoes.popleft()
            if rate != at_rate:
                at_rate = rate
                to_16k = aec.Resampler(rate) if hasattr(aec, "Resampler") else (
                    lambda x, r=rate: resample_poly(x, 16000, r).astype(np.float32))
            y = to_16k(samples)
            if y.any():
                room.add(t + latency + 0.012, fftconvolve(y, room.ir).astype(np.float32) * room.echo_gain)

    threading.Thread(target=echo, daemon=True).start()

    written: list = []  # (when, rate, block, the device was late): exactly what went to the device
    out.mkdir(parents=True, exist_ok=True)
    raw = out / f"{label}.f32"
    in_process = "speaker" not in inspect.signature(Player).parameters  # 30d2ef39 and the first callback Player
    if in_process:
        player = Player(device=dev, on_write=heard_by_the_mic)
    else:  # the speaker process records too: the Teams loopback drops blocks between two processes
        player = Player(device=dev, on_write=heard_by_the_mic,
                        speaker=[sys.executable, "-m", "maxie.voice.output", "--record", f"{raw}:{dev}"])
    if getattr(player, "_remote", None) is not None:  # the speaker process: each block it played comes back
        played = player._played

        def tapped_remote(block, rate):
            written.append((time.monotonic(), rate, np.array(block, dtype=np.float32), False))
            played(block, rate)

        player._played = player._remote.on_block = tapped_remote
    elif hasattr(player, "_callback"):  # the realtime callback in this process (first version)
        callback = player._callback

        def tapped(outdata, frames, t, status):
            callback(outdata, frames, t, status)
            written.append((time.monotonic(), player._rate, outdata[:, 0].copy(),
                            bool(status and status.output_underflow)))

        player._callback = tapped
    else:  # the Player before §10 wrote to a blocking stream
        real_open = player._open
        player._open = lambda rate: _WriteTap(real_open(rate), rate, written)
    stops: list[float] = []
    real_stop = player.stop
    player.stop = lambda: (stops.append(time.monotonic()), real_stop())[1]
    plays: list[tuple[float, int]] = []
    real_play = player.play
    player.play = lambda s, r: (plays.append((time.monotonic(), len(s))), real_play(s, r))[1]

    stt, tts, him = STT(), TTS(voice="af_jessica", speed=1.05), TTS(voice="am_michael")
    stt.warm()
    tts.warm()
    over = _clip(him, "Actually, what about tomorrow?")
    hub = TrialHub()
    loop = VoiceLoop(ears=RoomMic(room, canceller.capture if canceller else None), vad=VAD(), stt=stt, tts=tts,
                     player=player, hub=hub, log=lambda m: None, barge_in_ok=lambda: barge[0], speaker=None)

    blocks: list[tuple[float, np.ndarray]] = []
    rec = sd.InputStream(device=dev, samplerate=REC_RATE, channels=1, dtype="float32",
                         callback=lambda d, n, t, s: blocks.append((time.monotonic(), d[:, 0].copy()))) \
        if in_process else None
    halt = threading.Event()
    if stress:
        _stress(halt)
        time.sleep(3.0)
    kinds = ["full"] * replies
    cut = [i for i in range(replies) if i % 2 == 1][:interrupted]
    for j, i in enumerate(cut):
        kinds[i] = "pause" if j % 2 == 0 else "barge"
    trials = []
    if rec:
        rec.start()
    time.sleep(0.5)
    for n, kind in enumerate(kinds):
        barge[0] = kind == "barge"
        room.echo_gain = 0.0 if barge[0] else ECHO_GAIN
        hub.reply, hub.paused = REPLIES[n % len(REPLIES)], False
        t0, plays0 = time.monotonic(), len(plays)
        loop._wake_now()
        if kind != "full":
            def interrupt(kind=kind, plays0=plays0):
                end = time.monotonic() + 15
                while len(plays) < plays0 + 3 and time.monotonic() < end:  # the chime, the filler, the first piece
                    time.sleep(0.02)
                time.sleep(float(rng.uniform(0.4, 2.5)))
                if kind == "pause":
                    hub.paused = True
                else:
                    room.add(time.monotonic(), over)
            threading.Thread(target=interrupt, daemon=True).start()
        turn = Turn(heard_at=time.monotonic())
        loop.handle("what's new", turn)
        loop._carry = []
        trials.append({"n": n + 1, "kind": kind, "t0": t0, "t1": time.monotonic(),
                       "stopped": any(t0 <= s <= time.monotonic() for s in stops)})
        time.sleep(1.2)
    time.sleep(0.5)
    if rec:
        rec.stop()
        rec.close()
    halt.set()

    if rec:
        first_t, first = blocks[0]
        rec_t0 = first_t - len(first) / REC_RATE
        audio = np.concatenate([b for _, b in blocks])
    else:
        player._remote.proc.stdin.close()
        player._remote.proc.wait(10)
        audio = np.fromfile(raw, dtype="<f4")
        rec_t0 = json.loads(Path(f"{raw}.json").read_text())["t0"]
        raw.unlink()
    sf.write(out / f"{label}.wav", audio, REC_RATE, subtype="FLOAT")
    whole = check(audio, REC_RATE)
    rows = []
    for tr in trials:
        a, b = int((tr["t0"] - rec_t0) * REC_RATE), int((tr["t1"] - rec_t0 + 0.6) * REC_RATE)
        r = check(audio[max(0, a):b], REC_RATE)
        rows.append({**{k: tr[k] for k in ("n", "kind", "stopped")}, "clicks": len(r["clicks"]), "clips": r["clips"],
                     "gaps": len(r["gaps"]), "pauses": r["pauses"], "peak_dbfs": r["peak_dbfs"]})
    fades = _stop_fades(written, stops)
    underruns = max(player.underruns, sum(1 for *_, u in written if u))
    report = {"label": label, "stress": stress, "replies": len(trials),
              "interrupted": sum(t["stopped"] for t in trials), "clicks": len(whole["clicks"]),
              "clips": whole["clips"], "gaps": len(whole["gaps"]), "chimes": whole["chimes"],
              "chime_overlaps": whole["chime_overlaps"], "chime_edges_dbfs": whole["chime_edges_dbfs"], "gaps_ms": [g for _, g in whole["gaps"]][:20],
              "pauses": whole["pauses"], "peak_dbfs": whole["peak_dbfs"], "underruns": underruns,
              "stop_fade_ms": fades, "player_rate": sorted({r for _, r, _, _ in written}),
              "click_times_s": [round(i / REC_RATE, 3) for i in whole["clicks"]][:40],
              "stop_at_s": [round(t - rec_t0, 3) for t in stops], "trials": rows}
    (out / f"{label}.json").write_text(json.dumps(report, indent=1))
    _sheets(audio, report["stop_at_s"], whole["clicks"], out, label)
    return report


def _stop_fades(written: list, stops: list[float]) -> list[float]:
    """For each stop: ms of sound the player wrote after it before its silent tail (the fade, plus whatever chunk
    was already being written)."""
    out = []
    for ts in stops:
        after = [(t, r, x) for t, r, x, _ in written if t >= ts]
        n, rate = 0, None
        for _, r, x in after:
            rate = r
            nz = np.nonzero(x)[0]
            if len(nz) == 0:
                break
            n += int(nz[-1]) + 1
            if nz[-1] + 1 < len(x):
                break
        if rate:
            out.append(round(n * 1000 / rate, 1))
    return out


def _sound_ends(audio: np.ndarray, near: int) -> int | None:
    """Where the sound around a stop ends: the first 30 ms of digital silence after it started (the clock that
    placed `near` is the recording's, off by the devices' latency)."""
    lo, hi = max(0, near - int(0.4 * REC_RATE)), min(len(audio), near + int(1.0 * REC_RATE))
    run = int(0.03 * REC_RATE)
    loud = np.flatnonzero(audio[lo:hi])
    if not len(loud):
        return None
    z = np.concatenate([[0], (audio[lo + loud[0]:hi] == 0).astype(np.int8), [0]])
    starts, ends = np.where(np.diff(z) == 1)[0], np.where(np.diff(z) == -1)[0]
    found = next((a for a, b in zip(starts, ends, strict=True) if b - a >= run), None)
    return None if found is None else lo + int(loud[0]) + int(found)


def _sheets(audio: np.ndarray, stop_at: list[float], found: list[int], out: Path, label: str) -> None:
    panels, marks = [], []
    for k, at in enumerate(stop_at):
        end = _sound_ends(audio, int(at * REC_RATE))
        if end is None:  # stopped between sentences: nothing was playing
            continue
        panels.append((f"{label}: stop {k + 1}, the 0.4 s before silence (red: silence)",
                       audio[max(0, end - int(0.4 * REC_RATE)):end + int(0.1 * REC_RATE)]))
        marks.append([0.8])
        if len(panels) == 4:
            break
    for c in found[:4]:
        panels.append((f"{label}: detected click at {c / REC_RATE:.2f} s (red), 40 ms",
                       audio[max(0, c - int(0.02 * REC_RATE)):c + int(0.02 * REC_RATE)]))
        marks.append([0.5])
    if panels:
        sheet(panels, REC_RATE, out / f"{label}-stops-clicks.png", marks)
    talk = np.where(np.abs(audio) > 0.05)[0]
    if len(talk):
        start = max(0, int(talk[0]) - int(0.1 * REC_RATE))
        sheet([(f"{label}: chime and the first reply, 6 s", audio[start:start + 6 * REC_RATE])], REC_RATE,
              out / f"{label}-reply.png")


def redraw(out: Path, label: str) -> None:
    """The sheets again from a run's recording and report."""
    import soundfile as sf
    audio, _ = sf.read(out / f"{label}.wav", dtype="float32")
    report = json.loads((out / f"{label}.json").read_text())
    _sheets(audio, report.get("stop_at_s", []), clicks(audio, REC_RATE), out, label)


# ---------------------------------------------------------------------------------------------- calibration

def calibrate() -> dict:
    """The detector on clean Kokoro (expect 0) and the same audio broken the ways a player breaks it (expect all)."""
    from scipy.signal import resample_poly

    from maxie.voice.speech import TTS
    tts = TTS(voice="af_jessica", speed=1.05)
    tts.warm()
    rng = np.random.default_rng(1)
    clean_hits, found, injected, gap_found = 0, 0, 0, 0
    for text in REPLIES:
        x, r = tts.synth(text)
        y = resample_poly(x, REC_RATE, r).astype(np.float32) * 0.8
        clean_hits += len(clicks(y, REC_RATE))
        for _ in range(3):  # a hard cut mid-word to silence and back (an underrun) and a 10 ms dropout
            loud = np.where(np.abs(y) > 0.05)[0]
            i = int(rng.choice(loud[(loud > REC_RATE) & (loud < len(y) - REC_RATE)]))
            broken = y.copy()
            broken[i:i + int(0.01 * REC_RATE)] = 0
            injected += 1
            found += bool([c for c in clicks(broken, REC_RATE) if abs(c - i) < REC_RATE * 0.012])
            gap_found += bool([g for g in silences(broken, REC_RATE) if g[2]])
    return {"clean_false_clicks": clean_hits, "injected_cuts": injected, "cuts_found_as_clicks": found,
            "cuts_found_as_gaps": gap_found}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replies", type=int, default=20)
    ap.add_argument("--interrupted", type=int, default=10)
    ap.add_argument("--stress", action="store_true")
    ap.add_argument("--out", default="data/logs/voice-output")
    ap.add_argument("--label", default="run")
    ap.add_argument("--analyze")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--redraw", action="store_true", help="the spectrogram sheets of --label again")
    args = ap.parse_args()
    if args.redraw:
        redraw(Path(args.out), args.label)
        return
    if args.calibrate:
        print(json.dumps(calibrate()))
        return
    if args.analyze:
        import soundfile as sf
        x, rate = sf.read(args.analyze, dtype="float32")
        r = check(x if x.ndim == 1 else x[:, 0], rate)
        print(json.dumps({**r, "clicks": len(r["clicks"])}))
        return
    report = run(args.replies, args.interrupted, args.stress, Path(args.out), args.label)
    print(json.dumps({k: v for k, v in report.items() if k != "trials"}))
    for row in report["trials"]:
        print(json.dumps(row))


if __name__ == "__main__":
    main()
