"""Maxie's real voice loop in a simulated MacBook room, for when the real mic can't be used (the lid is
closed: the built-in mic is cut off in hardware). Everything is the real thing except the air: silero, Whisper,
Kokoro (af_jessica), the Player and the VoiceLoop. What Maxie plays comes back into the "mic" in real time, loud
and with the room's reverb, over the room's noise, exactly as on the built-in speakers; his questions (another
Kokoro voice) are mixed in at his level.

Each trial: "Hey Maxie, what's on my calendar?", then the answer streams from a stand-in hub. Pass = he woke, one
question reached the hub (0 echo turns, 0 repeats), the whole answer was spoken (0 cut-offs), at most one filler
and only before the answer. `--mode barge`: "Maxie, stop" 3 s into the answer must stop it, and no new turn.
`--mode headphones`: no echo; "actually, what about tomorrow" over the answer interrupts and becomes the next turn.
`--mode crash`: the real speaker process, into the silent "Microsoft Teams Audio" loopback (nothing is heard, no
echo); the first 3 answers have their player crash mid-sentence, as live at 3:33 PM (its reader thread fails), killed,
and frozen. Each must leave "speaking" within 1 s; then --trials more "Hey Maxie" must wake him and be answered whole.
`--hub live`: the real hub and brain answer (their own conversation, CONVERSATION), and every question must leave
exactly one of his messages in the hub's history: no fake user bubbles.

Usage: .venv/bin/python scripts/echo_room.py [--mode speakers|barge|headphones|crash] [--hub stand-in|live] [--trials 20]
       [--out f.json]
"""

from __future__ import annotations

import argparse
import json
import queue
import re
import signal
import threading
import time

import numpy as np
from scipy.signal import fftconvolve, resample_poly

from maxie.config import secret
from maxie.voice import aec
from maxie.voice.audio import FRAME, OUT_LATENCY_S, RATE, VAD, Player
from maxie.voice.loop import FILLER, FOLLOW_UP_S, Hub, VoiceLoop
from maxie.voice.speech import STT, TTS

ECHO_GAIN = 0.6         # the MacBook's speakers into its own mic: about as loud as he is at arm's length
ECHO_DELAY_S = 0.012
RT60_S = 0.35
NOISE_DBFS = -42.0      # his room at night, measured 2026-10-21 00:12
USER_GAIN = 0.5
CONVERSATION = "goal8-full-flow"
LOOPBACK = "Microsoft Teams Audio"  # --mode crash: plays here, silently
CRASHES = ("reader", "kill", "stuck")
THINK_S = 0.8           # the stand-in hub "thinks" this long: past the 0.5 s filler
ANSWERS = (
    "Nothing on your calendar today. I checked Google, Home, Work, invites, birthdays, and the India and US holiday "
    "calendars, and all of them are empty for Wednesday.",
    "Your next event is Maharishi Valmiki Jayanti, an all-day holiday on Monday, October 26. It's on the India "
    "holidays calendar. There are no personal meetings or appointments in the next thirty days.",
    "No meetings next week. Monday, October 26 has Maharishi Valmiki Jayanti, an all-day holiday. Thursday, "
    "October 29 has Karaka Chaturthi, also a holiday. Nothing else is on any of your calendars.",
    "Over the next two weeks there are five holidays: Maharishi Valmiki Jayanti on Monday, Karaka Chaturthi on "
    "Thursday, Halloween on Saturday, Daylight Saving Time ending on Sunday, and Day of the Dead on Monday.",
    "It's sixty one degrees and clear in Austin right now. Tomorrow is sunny with a high of seventy eight and a low "
    "of fifty five, and there is no rain in the forecast for the rest of the week, so it's a good week to walk "
    "after dinner. Take a light jacket in the morning, it will be cool until about ten.",
)


class Room:
    """What the mic hears: the room's noise, plus every sound placed at a time (Maxie's echo, his voice)."""

    def __init__(self, echo_gain: float, seed: int = 0):
        self.rng = np.random.default_rng(seed)
        self.lock = threading.Lock()
        self.sounds: list[tuple[float, np.ndarray]] = []
        self.echo_gain = echo_gain
        self.out_t = 0.0
        t = np.arange(int(RT60_S * RATE)) / RATE
        tail = self.rng.standard_normal(len(t)) * np.exp(-6.9 * t / RT60_S)
        self.ir = (0.5 * tail / np.sqrt((tail ** 2).sum())).astype(np.float32)
        self.ir[0] += 1.0
        self.noise = 10 ** (NOISE_DBFS / 20)

    def add(self, t: float, samples: np.ndarray) -> None:
        with self.lock:
            now = time.monotonic()
            self.sounds = [(s, x) for s, x in self.sounds if s + len(x) / RATE > now - 1] + [(t, samples)]

    def mix(self, t0: float, n: int) -> np.ndarray:
        out = (self.rng.standard_normal(n) * self.noise).astype(np.float32)
        with self.lock:
            sounds = list(self.sounds)
        for start, x in sounds:
            i = int(round((t0 - start) * RATE))
            lo, hi = max(0, i), min(len(x), i + n)
            if lo < hi:
                out[lo - i:hi - i] += x[lo:hi]
        return out


class RoomSpeaker:
    """The output device the real Player plays to: it takes audio through the Player's callback in real time, 10 ms
    at a time, and every block comes back as echo once the device has played it (OUT_LATENCY_S later, like the
    real one: echo placed sooner than that raced the mic, which had already heard that moment, and arrived broken)."""

    def __init__(self, room: Room, rate: int, callback):
        self.room, self.rate, self.callback, self.running = room, rate, callback, True
        self.to_16k = aec.Resampler(rate)  # one stream, no seams between the 10 ms blocks
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        block, t = self.rate // 100, time.monotonic()
        while self.running:
            buf = np.zeros((block, 1), np.float32)
            self.callback(buf, block, None, None)
            y = self.to_16k(buf[:, 0])
            if self.room.echo_gain and buf.any():
                self.room.add(t + OUT_LATENCY_S + ECHO_DELAY_S, fftconvolve(y, self.room.ir).astype(np.float32) * self.room.echo_gain)
            t += block / self.rate
            time.sleep(max(0.0, t - time.monotonic()))

    def stop(self) -> None:
        self.running = False

    def close(self) -> None:
        pass


class RoomMic:
    """Frames from the room every 32 ms into a queue, like the real Mic (they pile up while Maxie talks)."""

    def __init__(self, room: Room, filter=None):
        self.room, self.q, self.filter = room, queue.Queue(maxsize=200), filter
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        t = time.monotonic()
        while True:
            t += FRAME / RATE
            time.sleep(max(0.0, t - time.monotonic()))
            try:
                frame = self.room.mix(t - FRAME / RATE, FRAME)
                self.q.put_nowait(self.filter(frame) if self.filter else frame)
            except queue.Full:
                pass

    def frames(self):
        while True:
            yield self.q.get()

    def drain(self) -> None:
        while not self.q.empty():
            self.q.get_nowait()


class StandInHub:
    """Streams a long answer the way the hub does, after thinking; records what reached it."""

    def __init__(self, answers):
        self.answers, self.chats, self.stops, self.states, self.notices = answers, [], 0, [], []

    def chat(self, text, screen=False):
        self.chats.append(text)
        answer = self.answers[(len(self.chats) - 1) % len(self.answers)]
        time.sleep(THINK_S)
        words = answer.split(" ")
        for i in range(0, len(words), 4):
            yield {"type": "text", "text": " ".join(words[i:i + 4]) + " "}
            time.sleep(0.06)
        yield {"type": "done", "text": answer}

    def state(self, s):
        self.states.append(s)

    def notice(self, kind, text=""):
        self.notices.append(kind)

    def stop(self):
        self.stops += 1

    def pause(self, on):
        pass

    def voice_settings(self):
        return {"voice_muted": False, "speak_replies": True, "whisper": False}

    def wake_requested(self):
        return False, []

    def report_status(self, status):
        pass


class LiveHub(Hub):
    """The real hub and brain, in their own conversation. The wake queue, notices and status stay the real voice
    helper's: this one never takes them."""

    def __init__(self):
        super().__init__("http://127.0.0.1:8780", secret("HUB_TOKEN") or "")
        self.chats, self.answers = [], []

    def chat(self, text, screen=False):
        self.chats.append(text)
        with self.http.stream("POST", f"{self.base}/api/chat", headers=self.h,
                              json={"text": text, "conversation": CONVERSATION, "device": "mac"}) as r:
            for line in r.iter_lines():
                if line.strip():
                    ev = json.loads(line)
                    if ev.get("type") in ("done", "error"):
                        self.answers.append(ev.get("text", ""))
                    yield ev

    def his_messages(self) -> int:
        rows = self.http.get(f"{self.base}/api/history", params={"conversation": CONVERSATION, "limit": 500},
                             headers=self.h).json()
        return sum(r["role"] == "user" for r in rows)

    def wake_requested(self):
        return False, []

    def notice(self, kind, text=""):
        pass

    def report_status(self, status):
        pass


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _clip(tts: TTS, text: str) -> np.ndarray:
    samples, rate = tts.synth(text)
    return resample_poly(samples, RATE, rate).astype(np.float32) * USER_GAIN


def run(mode: str, trials: int, live: bool = False, cancel: bool = True) -> list[dict]:
    room = Room(echo_gain=0.0 if mode in ("headphones", "crash") else ECHO_GAIN)
    hub = LiveHub() if live else StandInHub(ANSWERS)
    tts, him = TTS(voice="af_jessica"), TTS(voice="am_michael")
    canceller = aec.create() if cancel and mode not in ("headphones", "crash") else None  # as in __main__
    if mode == "crash":
        player = Player(device=LOOPBACK, out_rate=48000)
    else:
        player = Player(open_stream=lambda rate, cb: RoomSpeaker(room, rate, cb), out_rate=48000,
                        on_write=canceller.render if canceller else None)
    states: list[tuple[float, str]] = []
    hub_state = hub.state
    hub.state = lambda s: (states.append((time.monotonic(), s)), hub_state(s))[1]
    spoken: list[tuple[str, bool]] = []  # what really played: (text, played to the end)
    saying: list[str | None] = [None]
    play = player.play

    def played(samples, rate):
        ok = play(samples, rate)
        if saying[0] is not None:  # the chime plays outside say()
            spoken.append((saying[0], ok))
        return ok

    player.play = played
    stops = []
    real_stop = player.stop
    player.stop = lambda: (stops.append(time.monotonic()), real_stop())[1]
    stt = STT()
    stt.warm()
    tts.warm()
    loop = VoiceLoop(ears=RoomMic(room, canceller.capture if canceller else None), vad=VAD(), stt=stt, tts=tts,
                     player=player, hub=hub, log=print, barge_in_ok=lambda: mode == "headphones", speaker=None)
    say = loop.say

    def saying_it(text, *a, **k):
        saying[0] = text
        try:
            return say(text, *a, **k)
        finally:
            saying[0] = None

    loop.say = saying_it
    in_session = threading.Event()
    converse = loop._converse  # every wake (early, at the end of the segment, the mic button) ends up here

    def tracked(*a, **k):
        in_session.set()
        try:
            converse(*a, **k)
        finally:
            in_session.clear()

    loop._converse = tracked
    threading.Thread(target=loop.run, daemon=True).start()
    question = _clip(him, "Hey Maxie, what's on my calendar?")
    stop_clip, over = _clip(him, "Maxie, stop."), _clip(him, "Actually, what about tomorrow?")
    rows = []
    time.sleep(2.0)
    crashes = list(CRASHES) if mode == "crash" else []
    for n in range(trials + len(crashes)):
        crash = crashes[n] if n < len(crashes) else None
        chats0, stops0, said0, hub_stops0 = len(hub.chats), len(stops), len(spoken), hub.stops if not live else 0
        his0 = hub.his_messages() if live else 0
        room.add(time.monotonic() + 0.2, question)
        woke = _wait(lambda: in_session.is_set(), 8)
        answer = ANSWERS[chats0 % len(ANSWERS)]
        if woke and mode in ("barge", "headphones"):
            _wait(lambda a=answer, n=said0: any(t != FILLER and t in a for t, _ in spoken[n:]), 15)
            room.add(time.monotonic() + 3.0, stop_clip if mode == "barge" else over)
        left_s = None
        if woke and crash:
            _wait(lambda a=answer, n=said0: any(t != FILLER and t in a for t, _ in spoken[n:]), 15)
            time.sleep(0.5)  # into the next sentence
            crashed = _crash(player, crash)
            _wait(lambda c=crashed: any(t > c and s == "idle" for t, s in states), 10)
            left_s = round(next((t for t, s in states if t > crashed and s == "idle"), float("inf")) - crashed, 3)
        _wait(lambda: not in_session.is_set(), 120)
        time.sleep(1.0)
        if live:
            answer = hub.answers[-1] if len(hub.answers) > chats0 else ""
        said = [t for t, _ in spoken[said0:]]
        fillers = [i for i, t in enumerate(said) if t == FILLER]
        reply = [t for t, ok in spoken[said0:] if t in answer and ok]
        first_reply = next((i for i, t in enumerate(said) if t in answer), len(said))
        row = {"trial": n + 1, "woke": woke, "heard": hub.chats[chats0:], "chats": len(hub.chats) - chats0,
               "cut_offs": len(stops) - stops0, "fillers": len(fillers),
               "filler_first": all(i < first_reply for i in fillers),
               "spoken_all": _words(" ".join(reply)) == _words(answer)}
        if live:
            row["his_messages"] = hub.his_messages() - his0
            row["answer"] = answer
        if crash:
            row.update(crash=crash, left_speaking_s=left_s, restarts=player.failures)
            row["ok"] = woke and row["chats"] == 1 and left_s is not None and left_s <= 1.0
        elif mode in ("speakers", "crash"):
            row["ok"] = (not live or row["his_messages"] == 1) and woke and row["chats"] == 1 \
                and row["cut_offs"] == 0 and row["spoken_all"] and row["fillers"] <= 1 and row["filler_first"]
        elif mode == "barge":
            row["ok"] = woke and row["chats"] == 1 and row["cut_offs"] == 1 and hub.stops > hub_stops0 \
                and not row["spoken_all"]
        else:
            row["ok"] = woke and row["chats"] == 2 and row["cut_offs"] >= 1 and "tomorrow" in " ".join(row["heard"]).lower()
        if mode == "crash":  # one restart per crash, none anywhere else: a slow open isn't taken for a stuck device
            row["false_restarts"] = player.failures - sum(1 for r in rows + [row] if r.get("crash"))
            row["ok"] = row["ok"] and row["false_restarts"] == 0
        rows.append(row)
        print(json.dumps(row), flush=True)
        time.sleep(FOLLOW_UP_S / 4)
    loop.stop()
    return rows


def _crash(player: Player, how: str) -> float:
    """The speaker process fails mid-sentence: its pipe breaks (the reader thread fails, as live at 3:33 PM), it is
    killed, or it freezes (no more blocks played: a stuck device)."""
    proc = player._remote.proc
    if how == "reader":
        proc.stdout.close()
    elif how == "kill":
        proc.kill()
    else:
        proc.send_signal(signal.SIGSTOP)
    print(f"crash: {how}", flush=True)
    return time.monotonic()


def _wait(cond, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="speakers", choices=["speakers", "barge", "headphones", "crash"])
    ap.add_argument("--hub", default="stand-in", choices=["stand-in", "live"])
    ap.add_argument("--aec", default="on", choices=["on", "off"])
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    rows = run(args.mode, args.trials, live=args.hub == "live", cancel=args.aec == "on")
    print(f"{args.mode}: {sum(r['ok'] for r in rows)}/{len(rows)} passed; echo turns "
          f"{sum(max(0, r['chats'] - 1) for r in rows if args.mode != 'headphones')}, cut-offs "
          f"{sum(r['cut_offs'] for r in rows) if args.mode == 'speakers' else '-'}")
    if args.out:
        with open(args.out, "w") as f:
            json.dump(rows, f, indent=1)


if __name__ == "__main__":
    main()
