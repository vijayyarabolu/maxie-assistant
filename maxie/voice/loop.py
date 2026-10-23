"""The voice loop: wake -> chime + listen -> command -> hub -> ack first, then sentence by sentence.

Jarvis feel: chime on wake, instant acknowledgement (the hub streams `ack` first), the first
sentence is spoken while the rest streams, an 8 s follow-up window without the wake phrase, barge-in
(he talks -> it stops speaking and listens), "Max, stop" kills the task (hub /api/stop). The notch
follows along through the hub's state stream. Everything audio stays on the Mac.

The loop is written against small interfaces (ears, stt, tts, hub, player) so the tests drive it with
fakes and no microphone.
"""

from __future__ import annotations

import difflib
import itertools
import json
import queue
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import numpy as np

from ..maxie_look import VOICES
from .ahead import Ahead
from .audio import CHIME_TAIL_S, FRAME, RATE, START_P, TAIL_S, Segmenter, chime
from .speech import conversation_start, first_pieces, is_done_talking, is_sleep, is_stop, sentences
from .wake import ALL_FORMS, WakeResult, detect, only_names
from .wake_test import TEST_LISTEN_S, explain

FOLLOW_UP_S = 8.0
CONVERSATION_S = 30.0  # conversation mode: this long to start talking again before it ends on its own
WHISPER_GAIN = 0.35    # whisper mode (quiet hours): about 9 dB softer
WHISPER_SPEED, NORMAL_SPEED = 0.95, 1.05
VOICE_IDS = frozenset(v["id"] for v in VOICES)
LISTENING = "I'm listening. Say that's all when you're done."
BARGE_IN_S = 0.35      # this much continuous speech while Maxie talks stops it
STOP_P = 0.3           # on the speakers: speech over her (a lower bar than idle: the echo canceller dulls him too)
STOP_WINDOW_S = 2.0    # ...then the last this-long is checked for a stop ("Maxie, stop" with its breath fits)
STOP_EVERY_S = 0.5     # ...this often, while he has spoken within the last STOP_WINDOW_S
STOP_TAIL_S = 1.0      # a stop can be heard before he has finished saying it: the mic stays deaf this much longer
SPEAK_GRACE_S = 2.0    # the watchdog: a sentence, the chime or the closing tail may take this much longer than
                       # its own length; then the player is stuck (live 3:33 PM: "speaking" for minutes, deaf to
                       # "Hey Maxie"), so it is aborted and restarted and the loop goes back to listening
EVENT_POLL_S = 0.1     # while the hub streams a reply: how often a stop or a failed speaker is checked between events
PAUSE_POLL_S = 0.5     # while she speaks: how often the hub is asked whether he paused her (⌥⇧P, the menu, a click)
ECHO_GATE_S = 0.7      # on the speakers: after the reply's 0.3 s silent tail the mic stays deaf this long (# ~1 s after her last word, so the room's echo of it never becomes his turn)
REPEAT_S = 3.0         # the same words again this soon after the answer ended: the same question, not a new one
REPEAT_RATIO = 0.9
MIN_COMMAND_S = 0.4
POLL_S = 0.5           # how often the loop looks at what the hub sent (a mic-button wake, a text to speak)
LONG_POLL_S = 4.0      # one request waits this long on the hub for one (it answers at once when there is)
QUIET_P = 0.05         # idle and the last frame this surely not speech: the next frame skips the speech detector
                       # (it ran 31×/s, most of the helper's CPU; speech starts ≤ 32 ms later, the
                       # pre-roll still has the first syllable)
REST_POLL_S = 1.0      # muted: Settings and the hub's queue are read this often, with the mic closed
IDLE_UNLOAD_S = 600.0  # quiet this long: drop the big models (Whisper base, Kokoro); the wake word reloads them
FILLER_AFTER_S = 0.5   # nothing from the hub by then: the filler, so he hears a word within 1.5 s of stopping
                       # (0.6 s endpoint + ~0.15 s Whisper before the hub even has it)
FILLER = "One moment."
PAUSED_LINE = "Okay, I'm pausing. Press Option Shift P, or click me, to wake me."
CARRY_FRAMES = 40      # ~1.3 s: the words that interrupted Maxie are handed to the next listen, not lost
FIXED_PHRASES = (FILLER, "Stopped.", "Done.", "I didn't catch that.", "On it.", "Checking.")  # only Maxie says these
ECHO_RATIO = 0.8       # heard text this close to what Maxie just said is its own voice from the speakers
ECHO_MIN_WORDS = 4     # a shorter piece of Maxie's sentence is more likely his own answer
SAID_KEEP = 12
ENROLL_PHRASES = ("Hey Max, what's on my calendar today?", "Maxie, play something calm.",
                  "Max, remind me to call mom at six.", "Hey Maxie, how's the weather this afternoon?",
                  "Max, open my notes from yesterday.")
ENROLL_LISTEN_S = 10.0  # per phrase: this long to start saying it
# Early wake (the panel opens within 500 ms of the word). The start of the segment is checked at his first
# short breath or 0.6 s in, again every 0.3 s while unsure (only "Hey" or the name so far), up to 5 times; not 0.6 s after he
# stops talking. The rest of the segment is the command. Measured: scripts/measure_voice_latency.py.
PEEK_PAUSE_S = 0.12
BARE_PAUSE_S = 0.16    # a bare name with nothing after it yet counts only at a real pause ("Max… is going to be late")
PEEK_FIRST_S = 0.6
PEEK_EVERY_S = 0.3
PEEK_MIN_S = 0.35
PEEKS = 5
GREETINGS = frozenset({"hey", "hi", "ok", "okay", "yo"})
# What the notch says when Maxie heard something but won't act (never just silence). Icons are the app's.
NOTICES = {"not_your_voice": "That didn't sound like you. Try \u201cHey Maxie\u201d.",
           "not_enrolled": "Say \u201cHey Maxie\u201d, or teach me your voice in Settings.",
           "didnt_catch": "Didn't catch that.",
           "mic_silent": "The mic sends no sound. Check Microphone in Settings."}
NOTICE_GAP_S = 20.0     # the same notice at most this often (a TV saying "Max" over and over)
MIC_SILENT_S = 20.0     # this long of exact digital silence: the mic is blocked (a live mic always hisses a little)


@dataclass
class Turn:
    """Timings of one spoken command, for the latency report (wake -> first audio)."""
    heard_at: float = 0.0        # end of his speech
    stt_done: float = 0.0
    first_audio: float = 0.0
    reply_done: float = 0.0
    text: str = ""
    reply: str = ""
    stopped: bool = False
    ended: bool = False          # he closed a conversation ("that's all")

    def line(self) -> str:
        return (f"turn: stt {self.stt_done - self.heard_at:.2f}s, first audio {self.first_audio - self.heard_at:.2f}s, "
                f"done {self.reply_done - self.heard_at:.2f}s, \"{self.text[:40]}\"")


def quiet_now(settings: dict, now: datetime | None = None) -> bool:
    """Inside his quiet hours (Settings, default 11 PM to 7 AM) in his timezone."""
    now = now or datetime.now(ZoneInfo(settings.get("timezone") or "America/Chicago"))
    hm = now.strftime("%H:%M")
    start, end = settings.get("quiet_start", "23:00"), settings.get("quiet_end", "07:00")
    return (start <= hm or hm < end) if start > end else (start <= hm < end)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


# "Maxie, look" / "take a screenshot" / "what's on my screen": the turn goes with a picture of his screen (GOAL6
# item 10). Whole-command matches only, so "look up the weather" or "take a note" stay ordinary turns.
LOOK = re.compile(r"(?:hey )?(?:maxie |max )?(?:can you |could you |please )?"
                  r"(?:look(?: here| at (?:this|that|it|my screen|the screen))?|(?:take|grab) a screen ?shot|screen ?shot"
                  r"|what(?:s| is) on (?:my|the) screen)")


def wants_screen(text: str) -> bool:
    return LOOK.fullmatch(" ".join(_norm(text).split())) is not None


class Hub:
    """The few hub calls the loop needs."""

    def __init__(self, base: str, token: str, http: httpx.Client | None = None):
        self.base, self.http = base.rstrip("/"), http or httpx.Client(timeout=120)
        self.h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self.enroll_pending = False  # Settings › Voice › "Teach Maxie your voice" (read with the wake poll)
        self.reconnected = False  # the hub came back (restarted): it forgot what the helper last reported
        self.test_pending = False    # Settings › Voice › "Test wake word"
        self._asked: tuple[bool, list[str]] = (False, [])  # what the long poll brought (wake_requested)
        self._lock, self._closed = threading.Lock(), threading.Event()
        self._poller: threading.Thread | None = None

    def state(self, state: str) -> None:
        try:
            self.http.post(f"{self.base}/api/events/state", json={"state": state}, headers=self.h, timeout=3)
        except httpx.HTTPError:
            pass

    def stop(self) -> None:
        try:
            self.http.post(f"{self.base}/api/stop", json={}, headers=self.h, timeout=3)
        except httpx.HTTPError:
            pass

    def pause(self, on: bool) -> None:
        try:
            self.http.post(f"{self.base}/api/pause", json={"on": on}, headers=self.h, timeout=3)
        except httpx.HTTPError:
            pass

    def is_paused(self) -> bool:
        try:
            return bool(self.http.get(f"{self.base}/api/pause", headers=self.h, timeout=3).json().get("paused"))
        except (httpx.HTTPError, ValueError):
            return False

    def wake_requested(self) -> tuple[bool, list[str]]:
        """(the panel's mic button was pressed, texts to speak now): what the hub has sent since the last call."""
        if self._poller is None:
            self._poller = threading.Thread(target=self._poll, daemon=True, name="hub-poll")
            self._poller.start()
        with self._lock:
            out, self._asked = self._asked, (False, [])
        return out

    def _poll(self) -> None:
        """One request waits on the hub at a time (it answers the moment there is something, else after
        LONG_POLL_S). A hub from before answers at once, so then the requests go POLL_S apart, as they used to."""
        while not self._closed.is_set():
            t0 = time.monotonic()
            try:
                r = self.http.get(f"{self.base}/api/voice/wake", params={"wait": LONG_POLL_S}, headers=self.h,
                                  timeout=LONG_POLL_S + 3).json()
            except (httpx.HTTPError, ValueError):
                self.reconnected = True
                self._closed.wait(1.0)
                continue
            self.enroll_pending = self.enroll_pending or bool(r.get("enroll"))
            self.test_pending = self.test_pending or bool(r.get("test"))
            with self._lock:
                wake, say = self._asked
                self._asked = (wake or bool(r.get("wake")), say + [str(t) for t in r.get("say", [])])
            self._closed.wait(max(0.0, POLL_S - (time.monotonic() - t0)))

    def close(self) -> None:
        self._closed.set()

    def voice_settings(self) -> dict:
        """voice_muted (stop listening), speak_replies (off: text in the panel only), whisper (quiet hours)."""
        try:
            r = self.http.get(f"{self.base}/api/settings", headers=self.h, timeout=3).json()
            return {"voice_muted": bool(r.get("voice_muted")), "speak_replies": r.get("speak_replies", True),
                    "whisper": bool(r.get("whisper_at_night", True)) and quiet_now(r),
                    "wake_words": list(r.get("wake_words", ALL_FORMS)),
                    "voice": r.get("voice", "af_heart"), "voice_speed": r.get("voice_speed", NORMAL_SPEED)}
        except (httpx.HTTPError, ValueError):
            return {"voice_muted": False, "speak_replies": True, "whisper": False}

    def muted(self) -> bool:
        return self.voice_settings()["voice_muted"]

    def notice(self, kind: str, text: str) -> None:
        """A short line in the notch: heard, but not acting on it (and why)."""
        try:
            self.http.post(f"{self.base}/api/voice/notice", json={"kind": kind, "text": text}, headers=self.h, timeout=3)
        except httpx.HTTPError:
            pass

    def report_status(self, status: dict) -> None:
        """The mic and the last wake decision, for Settings › Voice and the notch."""
        try:
            self.http.post(f"{self.base}/api/voice/status", json=status, headers=self.h, timeout=3)
        except httpx.HTTPError:
            pass

    def report_test(self, result: dict) -> None:
        try:
            self.http.post(f"{self.base}/api/voice/test/result", json=result, headers=self.h, timeout=3)
        except httpx.HTTPError:
            pass

    def report_speaker(self, status: dict) -> None:
        """Voice enrollment result for Settings › Voice: {enrolled, threshold?, message}."""
        try:
            self.http.post(f"{self.base}/api/voice/speaker", json=status, headers=self.h, timeout=3)
        except httpx.HTTPError:
            pass

    def chat(self, text: str, screen: bool = False):
        """Yields the hub's NDJSON events (ack, text, tool, confirm, done, error). `screen`: the hub attaches a
        screenshot the Mac app takes."""
        with self.http.stream("POST", f"{self.base}/api/chat", headers=self.h,
                              json={"text": text, "conversation": "voice", "device": "mac", "screen": screen}) as r:
            for line in r.iter_lines():
                if line.strip():
                    yield json.loads(line)


@dataclass
class VoiceLoop:
    ears: object          # .frames() -> iterator of 512-sample float32 frames; .drain()
    vad: object           # .prob(frame) -> float
    stt: object           # .transcribe(audio, quick=False) -> str
    tts: object           # .synth(text) -> (samples, rate)
    player: object        # .play(samples, rate) -> bool; .stop(); .playing
    hub: Hub
    clock: object = time.monotonic
    log: object = print
    wait: object = time.sleep
    turns: list[Turn] = field(default_factory=list)
    _speak_lock: threading.Lock = field(default_factory=threading.Lock)
    _interrupted: bool = False
    _stopped: bool = False  # he said "stop" over her on the speakers: the exchange is over
    _carry: list = field(default_factory=list)  # (frame, speech probability) of the words that interrupted her
    _said: deque = field(default_factory=lambda: deque(maxlen=SAID_KEEP))
    filler_after_s: float = FILLER_AFTER_S
    barge_in_ok: object = field(default_factory=lambda: (lambda: True))  # False on the built-in speakers
    listen_while_talking: bool = True  # barge-in watcher on (tests that script one fake mic turn it off)
    speak: bool = True  # Settings speak_replies; off = the chime only, replies stay in the panel
    idle_unload_s: float = IDLE_UNLOAD_S
    speaker: object = None     # SpeakerCheck: his voice (None: no check, bare "Max"/"Maxie" never wake)
    wake_forms: frozenset = frozenset(ALL_FORMS)  # Settings › Voice › wake words
    whisper: bool = False      # quiet hours: softer and a little slower (Settings › Voice)
    base_speed: float = NORMAL_SPEED  # Settings › Maxie › speed
    conversing: bool = False   # conversation mode: the mic stays open between replies
    _asleep: bool = False
    _last_active: float = 0.0
    _running: bool = True
    _muted: bool = False
    _last_settings: float = 0.0
    _last_poll: float = 0.0
    _noticed: dict = field(default_factory=dict)
    _zero_since: float | None = None
    _mic_silent: bool = False
    _last_p: float = 1.0
    _deaf_until: float = 0.0
    _skipped: bool = False
    last_wake: dict | None = None

    # ---- speaking

    def say(self, text: str, turn: Turn | None = None, made: Future | None = None,
            still: Callable[[], bool] | None = None) -> bool:
        """Synthesise (or take what `made` synthesised) and play one chunk; False when cut short (barge-in / stop).
        `still`: asked just before playing; False skips the chunk."""
        if not text.strip() or not self.speak:
            return True
        samples, rate = made.result() if made is not None else self.tts.synth(text)
        if self.whisper:
            samples = samples * WHISPER_GAIN
        with self._speak_lock:
            if still is not None and not still():  # asked again at the last moment (the filler)
                return True
            self._said.append(text)
            if turn and not turn.first_audio:
                turn.first_audio = self.clock()
            return self._play(samples, rate)

    def _play(self, samples: np.ndarray, rate: int) -> bool:
        return self._watched(lambda: self.player.play(samples, rate), len(samples) / rate + SPEAK_GRACE_S, False)

    def _watched(self, fn: Callable, seconds: float, default):
        """The watchdog: `fn` (a play, or the player's end) runs on its own thread and gets `seconds`. Past that the
        player is stuck: it is aborted (its speaker process killed and restarted), and the loop moves on."""
        done, out = threading.Event(), {}

        def run() -> None:
            try:
                out["value"] = fn()
            except BaseException as e:  # raised again on the caller's thread  # noqa: BLE001
                out["error"] = e
            finally:
                done.set()

        threading.Thread(target=run, daemon=True, name="voice-play").start()
        if not done.wait(seconds):
            self.log(f"voice: the player is stuck ({seconds:.1f} s); restarting it")
            if (abort := getattr(self.player, "abort", None)) is not None:
                abort("stuck: the voice loop's watchdog")
            done.wait(STOP_TAIL_S)
            return default
        if "error" in out:
            raise out["error"]
        return out.get("value", default)

    def end_speech(self, tail_s: float | None = None, gate: bool = True) -> None:
        """The reply (or chime) is over: let the player write its silent tail and close the stream. On the speakers
        the mic heard all of it: what it queued meanwhile is dropped and it stays deaf ECHO_GATE_S more (not after
        the chime: he is still saying his command)."""
        end = getattr(self.player, "end", None)
        if end is not None:
            with self._speak_lock:
                tail = TAIL_S if tail_s is None else tail_s
                self._watched(lambda: end() if tail_s is None else end(tail_s), tail + SPEAK_GRACE_S, None)
        if gate and not self._carry and not self.barge_in_ok():
            self.ears.drain()
            self._deaf_until = self.clock() + ECHO_GATE_S + (STOP_TAIL_S if self._stopped else 0.0)

    def deaf(self, t: float) -> bool:
        """Right after Maxie spoke on the speakers: the frame is her echo, never his."""
        return t < self._deaf_until

    def _watch_barge_in(self, stop_flag: threading.Event) -> None:
        """While Maxie speaks, his speech stops playback. In headphones a short burst is enough, and his words are
        the next command. On the built-in speakers the mic hears Maxie too, and her own words misheard ("Your only
        talent") read as his (live at 23:17: each echo cut her off and became a new question). There only
        a whole "stop" that she isn't saying herself stops her; nothing heard over her becomes a turn."""
        if not self.barge_in_ok():
            self._watch_for_stop(stop_flag)
            return
        run, recent = 0, deque(maxlen=CARRY_FRAMES)
        for frame in self.ears.frames():
            if stop_flag.is_set():
                return
            p = self.vad.prob(frame)
            recent.append((frame, p))
            run = run + 1 if p >= 0.6 else 0
            if run * FRAME / RATE >= BARGE_IN_S:
                self._carry = list(recent)
                self._interrupted = True
                self.player.stop()
                return

    def _watch_for_stop(self, stop_flag: threading.Event) -> None:
        """On the speakers: the last STOP_WINDOW_S is checked every STOP_EVERY_S while he speaks over her. Cutting
        what he says at his pauses split "Maxie," from its "stop" (half of 10 stops missed in scripts/echo_room.py):
        "Maxie," alone is heard as nothing, and the "stop" after the breath is too dulled to start a piece of its own."""
        window = deque(maxlen=int(STOP_WINDOW_S * RATE / FRAME))
        every, since, voiced = int(STOP_EVERY_S * RATE / FRAME), 0, None
        for frame in self.ears.frames():
            if stop_flag.is_set():
                return
            window.append(frame)
            since += 1
            if self.vad.prob(frame) >= STOP_P:
                voiced = 0
            elif voiced is not None:
                voiced += 1
            if voiced is None or voiced >= window.maxlen or since < every:
                continue
            since = 0
            if self._says_stop(np.concatenate(window)):
                self.hub.stop()
                self._interrupted = self._stopped = True
                self.player.stop()
                return

    def _watch_pause(self, stop_flag: threading.Event) -> None:
        """While she speaks: paused from the notch (⌥⇧P, the menu, a click) stops her at once. On the built-in
        speakers that is the stop that always works; "Maxie, stop" over her can be lost under her voice (§8)."""
        while not stop_flag.wait(PAUSE_POLL_S):
            if self.hub.is_paused():
                self._interrupted = self._stopped = True
                self.player.stop()
                return

    def _says_stop(self, audio: np.ndarray) -> bool:
        """Over Maxie's voice from the speakers: "stop" ("Maxie, stop", "be quiet") as a whole sentence, and not
        a word of what she just said ("Stop by the store at five" is her own echo)."""
        heard = self.stt.transcribe(audio, quick=True, stop=True)
        text = next((t for t in reversed(sentences(heard, final=True)[0]) if is_stop(t)), "")  # amid her words
        if not text:
            self.log(f"over her: \"{heard.strip()}\" (not a stop)")
            return False
        said = {w for s in self._said for w in _norm(s).split()}
        return not ({w for w in _norm(text).split() if w not in GREETINGS and not only_names(w)} & said)

    def is_echo(self, heard: str) -> bool:
        """Did the mic just hear Maxie's own words (speakers, no echo cancellation)?"""
        h = _norm(heard)
        if not h:
            return False
        said = [_norm(s) for s in [*self._said, *FIXED_PHRASES]]
        # A fragment of what Maxie said counts only when it is a few words long: a short answer like "yes"
        # or "that's all" is his, even when Maxie's last sentence contained it ("Yes or no?").
        long_enough = len(h.split()) >= ECHO_MIN_WORDS
        return any((long_enough and h in s) or difflib.SequenceMatcher(None, h, s).ratio() >= ECHO_RATIO for s in said if s)

    def is_repeat(self, heard: str) -> bool:
        """The question he just had answered, again within REPEAT_S: one question gets one answer."""
        last = self.turns[-1] if self.turns else None
        if last is None or not last.reply_done or self.clock() - last.reply_done > REPEAT_S:
            return False
        return difflib.SequenceMatcher(None, _norm(heard), _norm(last.text)).ratio() >= REPEAT_RATIO

    def _filler(self, turn: Turn, stop_flag: threading.Event, ahead: Ahead) -> None:
        """Quick questions have no ack; when the hub has sent nothing to say after `filler_after_s` (the model is
        still thinking), say something. Once a piece has arrived its audio is being made: no filler before it."""
        if stop_flag.wait(self.filler_after_s) or turn.first_audio or ahead.added or self._interrupted:
            return
        self.hub.state("talk")
        # making it can outlast the wait: no filler once the answer arrived or the turn is over
        self.say(FILLER, turn, still=lambda: not (ahead.added or stop_flag.is_set() or self._interrupted))

    # ---- one command

    def handle(self, text: str, turn: Turn) -> None:
        """Send one command to the hub and speak the reply as it streams."""
        if is_stop(text):
            self.hub.stop()
            self.player.stop()
            turn.stopped = True
            self.say("Stopped.", turn)
            self.end_speech()
            return
        if is_sleep(text):  # "Maxie, go to sleep": paused until he resumes (menu, ⌥⇧P, a click on Maxie)
            self.hub.pause(True)
            turn.stopped = True
            self.say(PAUSED_LINE, turn)
            self.end_speech()
            return
        self.hub.state("think")
        buffer, replied = "", 0
        self._interrupted = self._stopped = False
        stop_flag = threading.Event()
        if self.listen_while_talking:
            threading.Thread(target=self._watch_barge_in, args=(stop_flag,), daemon=True).start()
        if getattr(self.hub, "is_paused", None) is not None:
            threading.Thread(target=self._watch_pause, args=(stop_flag,), daemon=True).start()
        # each piece is made as soon as it arrives and played in order, the next made while this one plays
        # a stop that lands while the next piece is being made: that piece doesn't start (§10: 4 of 10 under load did)
        ahead = Ahead(lambda piece, made: self.say(piece, turn, made, still=lambda: not self._interrupted),
                      lambda piece: self.tts.synth(piece) if self.speak else None, cut=lambda: self._interrupted)
        threading.Thread(target=self._filler, args=(turn, stop_flag, ahead), daemon=True).start()

        def reply(sentence: str) -> None:
            nonlocal replied
            for piece in first_pieces(sentence) if not replied else [sentence]:
                ahead.add(piece)
            replied += 1

        try:
            for ev in self._events(self.hub.chat(text, screen=wants_screen(text)),
                                   cut=lambda: self._interrupted or ahead.stopped):
                kind = ev.get("type")
                if kind == "ack" and ev.get("text"):
                    self.hub.state("talk")
                    ahead.add(ev["text"])
                elif kind == "text":
                    buffer += ev.get("text", "")
                    ready, buffer = sentences(buffer)
                    for s in ready:
                        reply(s)
                elif kind in ("done", "error"):
                    # errors and direct results arrive whole in the final event; streamed replies leave a tail
                    final = buffer if kind == "done" and (replied or buffer) else ev.get("text", "")
                    for s in sentences(final, final=True)[0]:
                        reply(s)
                    turn.reply = ev.get("text", "")
                    if kind == "done" and not replied and not turn.reply:
                        ahead.add("Done.")
        finally:
            try:
                ahead.finish()
            finally:  # a voice that failed still ends the turn: the notch goes back to idle
                stop_flag.set()
                if getattr(self.player, "failed", False) is True:
                    self.log("voice: the speaker failed mid-reply; it restarted, the reply is in the panel")
                self.end_speech()
                turn.reply_done = self.clock()
                self.hub.state("idle")
        if self._interrupted:
            self.log("barge-in: stopped speaking")

    def _events(self, chat, cut: Callable[[], bool]):
        """The hub's events, read on a thread of their own: a stop or a failed speaker leaves the reply within
        EVENT_POLL_S even while the hub is quiet (a tool call). The hub finishes the reply for the panel anyway."""
        events: queue.Queue = queue.Queue()

        def pump() -> None:
            try:
                for ev in chat:
                    events.put(("event", ev))
            except Exception as e:  # raised again on the loop's thread  # noqa: BLE001
                events.put(("error", e))
            finally:
                events.put(("end", None))

        threading.Thread(target=pump, daemon=True, name="hub-events").start()
        while not cut():
            try:
                kind, item = events.get(timeout=EVENT_POLL_S)
            except queue.Empty:
                continue
            if kind == "end":
                return
            if kind == "error":
                raise item
            yield item

    # ---- listening

    def listen_for_command(self, timeout: float) -> tuple[np.ndarray | None, float]:
        """Collect his next utterance (VAD segment) within `timeout` seconds. The words that interrupted her come
        first, with the speech probabilities the barge-in watcher gave them: run through silero again (it keeps
        state, and had already heard what came after) "Actually," dipped at its comma and ended the segment there,
        so "what about tomorrow?" was lost (2 of 10 in scripts/echo_room.py --mode headphones)."""
        seg = Segmenter()
        carry, self._carry = self._carry, []
        if not carry:
            self.ears.drain()
        start = self.clock()
        heard = ((frame, self.vad.prob(frame)) for frame in self.ears.frames())
        for frame, p in itertools.chain(carry, heard):
            t = self.clock()
            if self.deaf(t) and not carry:
                continue
            audio = seg.feed(frame, p, t)
            if audio is not None and len(audio) / RATE >= MIN_COMMAND_S:
                return audio, t
            if not seg.in_speech and t - start > timeout:
                return None, t
        return None, self.clock()

    def run_turn(self, audio: np.ndarray, heard_at: float, command: str = "") -> Turn:
        turn = Turn(heard_at=heard_at)
        if not command:
            command = self.stt.transcribe(audio)
        turn.stt_done, turn.text = self.clock(), command
        if not command.strip():
            self.notice("didnt_catch")
            self.say("I didn't catch that.", turn)
            self.end_speech()
            turn.reply_done = self.clock()
            return turn
        if self.is_echo(command):
            self.log(f"echo: ignored \"{command}\"")
            turn.reply_done = self.clock()
            return turn
        if self.is_repeat(command):
            self.log(f"repeat: ignored \"{command}\" (just answered)")
            turn.reply_done = self.clock()
            return turn
        self.log(f"heard: {command}")
        if self.conversing and is_done_talking(command):
            self.conversing, turn.ended = False, True
            self.say("Good night." if self.whisper else "Okay. Talk soon.", turn)
            self.end_speech()
            turn.reply_done = self.clock()
            self.turns.append(turn)
            return turn
        start, rest = conversation_start(command)
        if start:
            self.conversing = True
            if not rest:
                self.say(LISTENING, turn)
                self.end_speech()
                turn.reply_done = self.clock()
                self.turns.append(turn)
                return turn
            command = turn.text = rest
        self.handle(command, turn)
        turn.ended = turn.ended or self._stopped  # no follow-up: the rest of his "Maxie, stop" is no question
        self.turns.append(turn)
        self.log(turn.line())
        return turn

    def run(self) -> None:
        """Forever: wait for the wake phrase (or the panel's mic button), then take commands until the
        follow-up window closes."""
        seg, peek, looks = Segmenter(), None, []
        self._last_active = self.clock()
        self.log("voice: listening for \"Hey Maxie\"")
        for frame in self.ears.frames():
            if not self._running:
                return
            t = self.clock()
            self._housekeeping(frame, t)
            if self._poll_hub(t):
                seg, peek, looks = Segmenter(), None, []
                continue
            if self._muted:
                self._rest()
                seg, peek, looks = Segmenter(), None, []
                continue
            if self.deaf(t):
                continue
            audio = seg.feed(frame, self._idle_prob(frame, seg), t)
            if peek in (None, "unsure") and seg.in_speech and _peek_due(seg, looks):
                looks.append((seg.seconds_of_speech(), seg.trailing_silence_s()))
                verdict = self._peek(seg)
                peek = verdict[0]
                if peek == "woke":
                    self._early_session(seg, verdict[1], verdict[2])
                    seg, peek, looks = Segmenter(), None, []
                    continue
            if audio is not None:
                if peek in (None, "unsure"):
                    self._wake_at_end(audio, seg.started_after_silence, t)
                peek, looks = None, []

    def _idle_prob(self, frame: np.ndarray, seg: Segmenter) -> float:
        """The speech probability while waiting for the wake word: in a quiet room every other frame reuses the last."""
        if not seg.in_speech and self._last_p < QUIET_P and not self._skipped:
            self._skipped = True
            return self._last_p
        self._skipped = False
        self._last_p = self.vad.prob(frame)
        return self._last_p

    def _rest(self) -> None:
        """Muted (Pause listening, Pause Maxie): the mic closes, so the orange dot goes and nothing runs
        per frame, until he unmutes. Settings and the hub's queue are still read: texts to speak, and the panel's
        mic button, which opens the mic for that one turn."""
        close = getattr(self.ears, "close_mic", None)
        if close is None:
            return  # ears without a mic to close (tests): the loop skips their frames instead
        close()
        self.log("muted: mic closed")
        try:
            while self._running and self._muted:
                self.wait(REST_POLL_S)
                t = self.clock()
                self._unload_when_idle(t)
                self._follow_settings(t)
                if self._poll_hub(t):
                    close()
        finally:
            self.ears.open_mic()
            self.ears.drain()
            self.log("mic open again")

    def _housekeeping(self, frame: np.ndarray, t: float) -> None:
        """Unload the big models after a quiet stretch, follow Settings, notice a blocked mic."""
        self._unload_when_idle(t)
        self._follow_settings(t)
        self._watch_mic(frame, t)

    def _unload_when_idle(self, t: float) -> None:
        if not self._asleep and t - self._last_active > self.idle_unload_s:
            self._asleep = True
            self.stt.sleep()
            self.tts.sleep()
            if hasattr(self.speaker, "unload"):
                self.speaker.unload()
            self.log("idle: big models unloaded (the wake word brings them back)")

    def _follow_settings(self, t: float) -> None:
        if t - self._last_settings > 5:
            vs = self.hub.voice_settings()
            self._muted, self.speak, self._last_settings = vs["voice_muted"], bool(vs["speak_replies"]), t
            self.set_whisper(bool(vs.get("whisper", False)))
            if "voice" in vs:
                self.set_voice(vs["voice"], float(vs.get("voice_speed", NORMAL_SPEED)))
            self.wake_forms = frozenset(vs.get("wake_words", ALL_FORMS))

    def _watch_mic(self, frame: np.ndarray, t: float) -> None:
        """Without Microphone access macOS hands over exact zeros, which looks like a quiet room forever."""
        if np.any(frame):
            if self._mic_silent:
                self._mic_silent = False
                self.log("mic: sound again")
                self._report_status()
            self._zero_since = None
            return
        self._zero_since = self._zero_since or t
        if not self._mic_silent and t - self._zero_since >= MIC_SILENT_S:
            self._mic_silent = True
            self.log("mic: only digital silence (Microphone access off, or a muted input)")
            self.notice("mic_silent")
            self._report_status()

    def _poll_hub(self, t: float) -> bool:
        """The hub's queue: texts to speak, enrollment, the panel's mic button. True when one of them used the
        mic (start a fresh segment after it)."""
        if t - self._last_poll <= POLL_S:
            return False
        self._last_poll = t
        wake, say = self.hub.wake_requested()
        if getattr(self.hub, "reconnected", False):
            self.hub.reconnected = False
            self._report_status()  # a restarted hub starts at "mic ok": tell it what is true
        for text in say:
            self.say(text)
        if say:
            self.end_speech()
        if getattr(self.hub, "enroll_pending", False):
            self.hub.enroll_pending = False
            self._enroll()
            return True
        if getattr(self.hub, "test_pending", False):
            self.hub.test_pending = False
            self._wake_test()
            return True
        if wake:
            self.log("wake: mic button")
            self._session(None, t)
            return True
        return False

    def _peek(self, seg: Segmenter) -> tuple:
        """The early wake check on the segment so far: ("woke", heard, text) | ("skip",) | ("unsure",).
        A wake word has to start the segment, so a peek that heard other words settles it: the rest of the
        segment is never transcribed (TV talk costs one short Whisper call, not one per sentence)."""
        snap = seg.snapshot()
        text = self.stt.transcribe(snap, quick=True)
        heard = detect(text, seg.started_after_silence, self.wake_forms)
        if heard.woke and only_names(heard.rest):
            heard = WakeResult(True, heard.form, "")
            if heard.bare and seg.trailing_silence_s() < BARE_PAUSE_S:
                return ("unsure",)  # the next word decides: "Max, what's…" or "Max is going to be late"
        if heard.woke:
            return ("woke", heard, text) if self._wake_allowed(heard, snap, text) else ("skip",)
        words = _norm(text).split()
        return ("unsure",) if not words or set(words) <= GREETINGS else ("skip",)

    def _early_session(self, seg: Segmenter, heard: WakeResult, text: str) -> None:
        """Woke at his first breath: chime and open the panel now, then take the rest of the segment as the
        command ("Maxie, what's on my calendar" runs without a second wake)."""
        self.log(f"wake ({heard.form}, early): \"{text}\"")
        self._wake_now()
        spoke, audio = False, None
        for frame in self.ears.frames():
            p = self.vad.prob(frame)
            spoke = spoke or p >= START_P
            audio = seg.feed(frame, p, self.clock())
            if audio is not None or not self._running:
                break
        command = heard.rest
        if spoke and audio is not None:
            full = self.stt.transcribe(audio, wake=True)
            again = detect(full, True, self.wake_forms)
            command = again.rest if again.woke else (full or heard.rest)
        self._converse(audio if command.strip() else None, self.clock(), command)

    def _wake_at_end(self, audio: np.ndarray, after_silence: bool, t: float) -> None:
        """The segment ended before an early check could decide: check the whole of it."""
        text = self.stt.transcribe(audio, quick=True)
        heard = detect(text, after_silence, self.wake_forms)
        if not heard.woke or not self._wake_allowed(heard, audio, text):
            return
        self.log(f"wake ({heard.form}): \"{text}\"")
        command = ""
        if len(heard.rest.split()) >= 2:
            # the command came in the same breath: "Hey Max, what time is it". One model awake: the quick text is it.
            command = heard.rest if self._one_model() else (detect(self.stt.transcribe(audio, wake=True), True,
                                                                    self.wake_forms).rest or heard.rest)
        self._session(audio if command else None, t, command)

    def _one_model(self) -> bool:
        wake_model = getattr(self.stt, "wake_model", None)
        return wake_model is not None and wake_model == getattr(self.stt, "command_model", None)

    def _wake_allowed(self, heard: WakeResult, audio: np.ndarray, text: str = "") -> bool:
        """The speaker check (17 ms): "Hey Max(ie)" unless clearly someone else, a bare name only in his voice and
        never before he enrolled. Without the model on this Mac only the hey forms wake."""
        sp = self.speaker
        if sp is not None and hasattr(sp, "refresh"):
            sp.refresh()
        if sp is None or not hasattr(sp, "judge"):
            if heard.bare:
                self.log(f"wake ({heard.form}) ignored: bare names need your voice (Settings › Voice)")
                self.notice("not_enrolled")
            return not heard.bare
        verdict = self._judge(heard, audio, text)
        if verdict.woke:
            return True
        if verdict.reason == "not enrolled":
            self.notice("not_enrolled")
        else:  # however low: his own short clips score like a stranger's (live: 0.06-0.18), so never just silence
            self.notice("not_your_voice")
        return False

    def _judge(self, heard: WakeResult, audio: np.ndarray, text: str):
        verdict = self.speaker.judge(audio, heard.form)
        score = "-" if verdict.score is None else f"{verdict.score:.2f}"
        self.log(f"wake ({heard.form}) voice {score} / bar {verdict.threshold:.2f}: "
                 f"{'ok' if verdict.woke else 'ignored, ' + verdict.reason}")
        self.last_wake = {"heard": text, "form": heard.form, "score": verdict.score, "threshold": verdict.threshold,
                          "woke": verdict.woke, "reason": verdict.reason}
        self._report_status()
        return verdict

    def notice(self, kind: str) -> None:
        """Tell the notch why nothing happened, at most once per NOTICE_GAP_S for each kind."""
        now = self.clock()
        if now - self._noticed.get(kind, -NOTICE_GAP_S) < NOTICE_GAP_S:
            return
        self._noticed[kind] = now
        if hasattr(self.hub, "notice"):
            self.hub.notice(kind, NOTICES[kind])

    def _report_status(self) -> None:
        if hasattr(self.hub, "report_status"):
            self.hub.report_status({"mic": "silent" if self._mic_silent else "ok", "last_wake": self.last_wake})

    def _wake_test(self) -> None:
        """Settings › Voice › "Test wake word": one segment, judged like a real wake, reported, never acted on."""
        self.log("wake test: listening")
        audio, _ = self.listen_for_command(TEST_LISTEN_S)
        if audio is None:
            self.hub.report_test(explain("", None, self.wake_forms, True))
            return
        t0 = self.clock()
        text = self.stt.transcribe(audio, quick=True)
        heard = detect(text, True, frozenset(ALL_FORMS))
        verdict = self.speaker.judge(audio, heard.form) if heard.woke and hasattr(self.speaker, "judge") else None
        result = {**explain(text, verdict, self.wake_forms, True), "ms": round((self.clock() - t0) * 1000)}
        self.log(f"wake test: \"{text}\" -> {result['why']}")
        self.hub.report_test(result)

    def _enroll(self) -> None:
        """Settings › Voice › "Teach Maxie your voice": five phrases, said after Maxie reads each one."""
        if self.speaker is None:
            self.hub.report_speaker({"enrolled": False, "message": "The speaker model isn't on this Mac yet."})
            return
        self.hub.state("listen")
        clips = []
        for i, phrase in enumerate(ENROLL_PHRASES, 1):
            self.say(f"{'Say' if i == 1 else 'Now'}: {phrase}")
            self.end_speech()
            audio, _ = self.listen_for_command(ENROLL_LISTEN_S)
            if audio is None:
                self.say("I didn't hear you. Try again from Settings when you're ready.")
                self.end_speech()
                self.hub.report_speaker({"enrolled": bool(self.speaker.enrolled), "message": f"Stopped at phrase {i}."})
                self.hub.state("idle")
                return
            clips.append(audio)
        threshold = self.speaker.enroll(clips)
        self.log(f"voice enrolled: threshold {threshold:.2f}")
        self.say("Got it. Now I know your voice.")
        self.end_speech()
        self.hub.report_speaker({"enrolled": True, "threshold": round(threshold, 3), "message": "Enrolled."})
        self.hub.state("idle")

    def set_whisper(self, on: bool) -> None:
        if on != self.whisper:
            self.log("whisper mode " + ("on (quiet hours)" if on else "off"))
        self.whisper = on
        self._apply_speed()

    def set_voice(self, voice: str, speed: float) -> None:
        """Settings › Maxie: any Kokoro English voice and a speed, picked up on the next reply."""
        if voice in VOICE_IDS and getattr(self.tts, "voice", voice) != voice:
            self.log(f"voice: {voice}")
            self.tts.voice = voice
        self.base_speed = speed
        self._apply_speed()

    def _apply_speed(self) -> None:
        if hasattr(self.tts, "speed"):
            k = WHISPER_SPEED / NORMAL_SPEED if self.whisper else 1.0
            self.tts.speed = round(self.base_speed * k, 3)

    def _session(self, audio: np.ndarray | None, heard: float, command: str = "") -> None:
        """Chime, run the first command (given, or listened for), then the follow-up window."""
        self._wake_now()
        self._converse(audio, heard, command)

    def _wake_now(self) -> None:
        """Eyes open, the panel drops, the chime: as soon as the wake word is known."""
        self.hub.state("listen")
        self._play(chime() * (WHISPER_GAIN if self.whisper else 1.0), 24000)
        self.end_speech(CHIME_TAIL_S, gate=False)
        self._last_active = self.clock()
        if self._asleep:
            self._asleep = False
            self.stt.wake()
            self.tts.wake()
            self.log("awake: models reloading")

    def _converse(self, audio: np.ndarray | None, heard: float, command: str = "") -> None:
        """The first command (given, or listened for), then the follow-up window (30 s while in conversation
        mode, which ends on "that's all" or that much silence)."""
        if audio is not None:
            turn = self.run_turn(audio, heard, command=command)
        else:
            audio2, heard = self.listen_for_command(FOLLOW_UP_S)
            if audio2 is None:
                self.notice("didnt_catch")
                self.hub.state("idle")
                return
            turn = self.run_turn(audio2, heard)
        while not turn.ended:  # follow-up window: more commands without the wake phrase
            self.hub.state("listen")
            audio2, heard = self.listen_for_command(CONVERSATION_S if self.conversing else FOLLOW_UP_S)
            if audio2 is None:
                break
            turn = self.run_turn(audio2, heard)
        if self.conversing:
            self.log("conversation: ended after a quiet stretch")
        self.conversing = False
        self._last_active = self.clock()
        self.hub.state("idle")

    def stop(self) -> None:
        self._running = False
        if close := getattr(self.hub, "close", None):
            close()  # its long poll


def _peek_due(seg: Segmenter, looks: list[tuple[float, float]]) -> bool:
    """Time for an early look at the segment so far? `looks`: (how far in, how long quiet) at each earlier look."""
    spoken, quiet = seg.seconds_of_speech(), seg.trailing_silence_s()
    if len(looks) >= PEEKS or spoken < PEEK_MIN_S:
        return False
    if not looks:
        return quiet >= PEEK_PAUSE_S or spoken >= PEEK_FIRST_S
    last_at, last_quiet = looks[-1]
    crossed = quiet >= BARE_PAUSE_S and (last_quiet < BARE_PAUSE_S or spoken - quiet > last_at)  # a bare name's pause
    return spoken >= last_at + PEEK_EVERY_S or crossed
