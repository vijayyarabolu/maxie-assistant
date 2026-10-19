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
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import numpy as np

from ..maxie_look import VOICES
from .audio import CHIME_TAIL_S, FRAME, RATE, Segmenter, chime
from .speech import conversation_start, is_done_talking, is_stop, sentences
from .wake import ALL_FORMS, WakeResult, detect

FOLLOW_UP_S = 8.0
CONVERSATION_S = 30.0  # conversation mode: this long to start talking again before it ends on its own
WHISPER_GAIN = 0.35    # whisper mode (quiet hours): about 9 dB softer
WHISPER_SPEED, NORMAL_SPEED = 0.95, 1.05
VOICE_IDS = frozenset(v["id"] for v in VOICES)
LISTENING = "I'm listening. Say that's all when you're done."
BARGE_IN_S = 0.35      # this much continuous speech while Maxie talks stops it
MIN_COMMAND_S = 0.4
POLL_S = 0.5           # how often the loop asks the hub for a mic-button wake or a text to speak
IDLE_UNLOAD_S = 600.0  # quiet this long: drop the big models (Whisper base, Kokoro); the wake word reloads them
FILLER_AFTER_S = 1.2   # no audio from the hub by then (a tool is running): say the filler so he hears something
FILLER = "One moment."
CARRY_FRAMES = 40      # ~1.3 s: the words that interrupted Maxie are handed to the next listen, not lost
FIXED_PHRASES = (FILLER, "Stopped.", "Done.", "I didn't catch that.", "On it.", "Checking.")  # only Maxie says these
ECHO_RATIO = 0.8       # heard text this close to what Maxie just said is its own voice from the speakers
ECHO_MIN_WORDS = 4     # a shorter piece of Maxie's sentence is more likely his own answer
SAID_KEEP = 12
ENROLL_PHRASES = ("Hey Max, what's on my calendar today?", "Maxie, play something calm.",
                  "Max, remind me to call mom at six.", "Hey Maxie, how's the weather this afternoon?",
                  "Max, open my notes from yesterday.")
ENROLL_LISTEN_S = 10.0  # per phrase: this long to start saying it


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

    def wake_requested(self) -> tuple[bool, list[str]]:
        """(the panel's mic button was pressed, texts to speak now), cleared by the hub once read."""
        try:
            r = self.http.get(f"{self.base}/api/voice/wake", headers=self.h, timeout=3).json()
            self.enroll_pending = self.enroll_pending or bool(r.get("enroll"))
            return bool(r.get("wake")), [str(t) for t in r.get("say", [])]
        except (httpx.HTTPError, ValueError):
            return False, []

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
    turns: list[Turn] = field(default_factory=list)
    _speak_lock: threading.Lock = field(default_factory=threading.Lock)
    _interrupted: bool = False
    _carry: list = field(default_factory=list)
    _said: deque = field(default_factory=lambda: deque(maxlen=SAID_KEEP))
    filler_after_s: float = FILLER_AFTER_S
    barge_in_ok: object = field(default_factory=lambda: (lambda: True))  # False on the built-in speakers
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

    # ---- speaking

    def say(self, text: str, turn: Turn | None = None) -> bool:
        """Synthesise and play one chunk; False when cut short (barge-in / stop)."""
        if not text.strip() or not self.speak:
            return True
        samples, rate = self.tts.synth(text)
        if self.whisper:
            samples = samples * WHISPER_GAIN
        self._said.append(text)
        if turn and not turn.first_audio:
            turn.first_audio = self.clock()
        with self._speak_lock:
            ok = self.player.play(samples, rate)
        return ok

    def end_speech(self, tail_s: float | None = None) -> None:
        """The reply (or chime) is over: let the player write its silent tail and close the stream."""
        end = getattr(self.player, "end", None)
        if end is None:
            return
        with self._speak_lock:
            end() if tail_s is None else end(tail_s)

    def _watch_barge_in(self, stop_flag: threading.Event) -> None:
        """While Maxie speaks: a short burst of his speech stops playback (echo from the speakers is
        shorter and quieter than a real interruption; AirPods make this clean)."""
        run, recent = 0, deque(maxlen=CARRY_FRAMES)
        for frame in self.ears.frames():
            if stop_flag.is_set():
                return
            recent.append(frame)
            run = run + 1 if self.vad.prob(frame) >= 0.6 else 0
            if run * FRAME / RATE >= BARGE_IN_S:
                self._carry = list(recent)
                self._interrupted = True
                self.player.stop()
                return

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

    def _filler(self, turn: Turn, stop_flag: threading.Event) -> None:
        """Quick questions have no ack; when the hub is still busy after `filler_after_s`, say something."""
        if stop_flag.wait(self.filler_after_s) or turn.first_audio or self._interrupted:
            return
        self.hub.state("talk")
        self.say(FILLER, turn)
        if stop_flag.is_set():  # the reply finished while the filler played: close the stream again
            self.end_speech()

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
        self.hub.state("think")
        buffer, spoken_any = "", False
        self._interrupted = False
        stop_flag = threading.Event()
        if self.barge_in_ok():
            threading.Thread(target=self._watch_barge_in, args=(stop_flag,), daemon=True).start()
        threading.Thread(target=self._filler, args=(turn, stop_flag), daemon=True).start()
        try:
            for ev in self.hub.chat(text, screen=wants_screen(text)):
                if self._interrupted:
                    break
                kind = ev.get("type")
                if kind == "ack" and ev.get("text"):
                    self.hub.state("talk")
                    self.say(ev["text"], turn)
                elif kind == "text":
                    buffer += ev.get("text", "")
                    ready, buffer = sentences(buffer)
                    for s in ready:
                        if self._interrupted or not self.say(s, turn):
                            break
                        spoken_any = True
                elif kind in ("done", "error"):
                    # errors and direct results arrive whole in the final event; streamed replies leave a tail
                    final = buffer if kind == "done" and (spoken_any or buffer) else ev.get("text", "")
                    ready, buffer = sentences(final, final=True)
                    for s in ready:
                        if self._interrupted or not self.say(s, turn):
                            break
                        spoken_any = True
                    turn.reply = ev.get("text", "")
                    if kind == "done" and not spoken_any and not turn.reply:
                        self.say("Done.", turn)
        finally:
            stop_flag.set()
            self.end_speech()
            turn.reply_done = self.clock()
            self.hub.state("idle")
        if self._interrupted:
            self.log("barge-in: stopped speaking")

    # ---- listening

    def listen_for_command(self, timeout: float) -> tuple[np.ndarray | None, float]:
        """Collect his next utterance (VAD segment) within `timeout` seconds."""
        seg = Segmenter()
        carry, self._carry = self._carry, []
        if not carry:
            self.ears.drain()
        start = self.clock()
        for frame in itertools.chain(carry, self.ears.frames()):
            t = self.clock()
            audio = seg.feed(frame, self.vad.prob(frame), t)
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
            self.say("I didn't catch that.", turn)
            self.end_speech()
            turn.reply_done = self.clock()
            return turn
        if self.is_echo(command):
            self.log(f"echo: ignored \"{command}\"")
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
        self.turns.append(turn)
        self.log(turn.line())
        return turn

    def run(self) -> None:
        """Forever: wait for the wake phrase (or the panel's mic button), then take commands until the
        follow-up window closes."""
        seg = Segmenter()
        last_mute_check, last_poll, muted = 0.0, 0.0, False
        self._last_active = self.clock()
        self.log("voice: listening for \"Hey Max\"")
        for frame in self.ears.frames():
            if not self._running:
                return
            t = self.clock()
            if not self._asleep and t - self._last_active > self.idle_unload_s:
                self._asleep = True
                self.stt.sleep()
                self.tts.sleep()
                if hasattr(self.speaker, "unload"):
                    self.speaker.unload()
                self.log("idle: big models unloaded (the wake word brings them back)")
            if t - last_mute_check > 5:
                vs = self.hub.voice_settings()
                muted, self.speak, last_mute_check = vs["voice_muted"], bool(vs["speak_replies"]), t
                self.set_whisper(bool(vs.get("whisper", False)))
                if "voice" in vs:
                    self.set_voice(vs["voice"], float(vs.get("voice_speed", NORMAL_SPEED)))
                self.wake_forms = frozenset(vs.get("wake_words", ALL_FORMS))
            if t - last_poll > POLL_S:
                last_poll = t
                wake, say = self.hub.wake_requested()
                for text in say:
                    self.say(text)
                if say:
                    self.end_speech()
                if getattr(self.hub, "enroll_pending", False):
                    self.hub.enroll_pending = False
                    self._enroll()
                    seg = Segmenter()
                    continue
                if wake:
                    self.log("wake: mic button")
                    self._session(None, t)
                    seg = Segmenter()
                    continue
            audio = seg.feed(frame, self.vad.prob(frame), t)
            if audio is None or muted:
                continue
            text = self.stt.transcribe(audio, quick=True)
            heard = detect(text, seg.started_after_silence, self.wake_forms)
            if not heard.woke or not self._his_voice(heard, audio):
                continue
            self.log(f"wake ({heard.form}): \"{text}\"")
            if len(heard.rest.split()) >= 2:
                # the command came in the same breath: "Hey Max, what time is it"
                full = detect(self.stt.transcribe(audio), True, self.wake_forms).rest
                self._session(audio, t, full or heard.rest)
            else:
                self._session(None, t)

    def _his_voice(self, heard: WakeResult, audio: np.ndarray) -> bool:
        """Once he enrolled, only his voice wakes Maxie. Before that, only "Hey Max(ie)": a bare name is too
        common on TV and in other people's mouths."""
        sp = self.speaker
        if sp is not None and hasattr(sp, "refresh"):
            sp.refresh()
        if sp is None or not sp.enrolled:
            if heard.bare:
                self.log(f"wake ({heard.form}) ignored: bare names need your voice (Settings › Voice)")
            return not heard.bare
        ok = sp.verify(audio)
        if not ok:
            self.log(f"wake ({heard.form}) ignored: not your voice")
        return ok

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
        """Chime, run the first command (given, or listened for), then the follow-up window (30 s while
        in conversation mode, which ends on "that's all" or that much silence)."""
        self.hub.state("listen")
        self.player.play(chime() * (WHISPER_GAIN if self.whisper else 1.0), 24000)
        self.end_speech(CHIME_TAIL_S)
        self._last_active = self.clock()
        if self._asleep:
            self._asleep = False
            self.stt.wake()
            self.tts.wake()
            self.log("awake: models reloading")
        if audio is not None:
            turn = self.run_turn(audio, heard, command=command)
        else:
            audio2, heard = self.listen_for_command(FOLLOW_UP_S)
            if audio2 is None:
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
