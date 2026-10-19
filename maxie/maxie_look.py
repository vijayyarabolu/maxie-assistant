"""Settings › Maxie: the look, the Classic robot, personality, chattiness, voice and speed.

The default look is exactly his pick: Mochi without headphones. The option lists mirror web/maxie.js (a test
keeps them in step). Changing the look re-renders the notch loops on the Mac at low priority (LoopRenderer);
the bundled loops stay on screen until the new ones are complete.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

CHARACTERS = ("maxie", "classic")
FURS = {
    "mochi": "Mochi periwinkle", "sunset": "Sunset", "ocean": "Ocean blue", "cream": "Cream",
    "charcoal": "Charcoal", "honey": "Honey", "mint": "Mint", "lilac": "Lilac", "rose": "Rose", "snow": "Snow",
    "cocoa": "Cocoa", "sky": "Sky", "midnight": "Midnight", "peach": "Peach",
}
SWATCHES = {  # fur colour at the root, as web/maxie.js LOOKS[...].root (Settings › Maxie swatches)
    "mochi": "#6a7bc0", "sunset": "#e39a6c", "ocean": "#3f6fb0", "cream": "#d9c3a0", "charcoal": "#34343f",
    "honey": "#c98a3a", "mint": "#4fa58e", "lilac": "#9a78c4", "rose": "#d27a95", "snow": "#b9c2d6",
    "cocoa": "#6b4a36", "sky": "#6aa3d8", "midnight": "#262b45", "peach": "#e8957a",
}
FUR_LENGTHS = ("short", "medium", "long", "extra")
EARS = ("round", "floppy", "pointy")
EYES = ("glossy", "sparkle", "sleepy", "button")
ACCESSORIES = ("none", "headphones", "cap", "beanie", "scarf", "glasses", "bow")
DEFAULT_LOOK = {"fur": "mochi", "furLength": "long", "ears": "round", "eyes": "glossy", "accessory": "none"}
CHOICES = {"fur": tuple(FURS), "furLength": FUR_LENGTHS, "ears": EARS, "eyes": EYES, "accessory": ACCESSORIES}

SIZES = ("small", "medium", "large")  # the free-floating bot; the notch has one size
PERSONALITIES = {
    "friend": "",  # the default voice in the system prompt
    "coach": "Personality: an upbeat coach. Push them a little toward the next step and name their progress.",
    "butler": "Personality: a calm, polite butler. Unhurried, precise, quietly formal; no jokes.",
    "playful": "Personality: playful. Light jokes and a bit of cheek are welcome, never at their expense.",
}
CHATTINESS = {
    "quiet": "Keep replies to one or two sentences.",
    "normal": "",
    "chatty": "You can say a little more than usual and add a friendly aside.",
}
VOICE_SPEED = (0.7, 1.4)
VOICES = [  # every English voice in Kokoro v1.0 (voices-v1.0.bin)
    *({"id": f"af_{n}", "label": f"{n.title()} (US, female)"} for n in
      ("heart", "alloy", "aoede", "bella", "jessica", "kore", "nicole", "nova", "river", "sarah", "sky")),
    *({"id": f"am_{n}", "label": f"{n.title()} (US, male)"} for n in
      ("adam", "echo", "eric", "fenrir", "liam", "michael", "onyx", "puck", "santa")),
    *({"id": f"bf_{n}", "label": f"{n.title()} (UK, female)"} for n in ("alice", "emma", "isabella", "lily")),
    *({"id": f"bm_{n}", "label": f"{n.title()} (UK, male)"} for n in ("daniel", "fable", "george", "lewis")),
]
MAXIE_KEYS = frozenset({"maxie_look", "character", "maxie_size", "personality", "chattiness", "voice", "voice_speed"})


def validate_look(look: object) -> dict:
    """A complete look (defaults filled in) as a new dict, or ValueError."""
    if not isinstance(look, dict):
        raise ValueError("maxie_look must be an object")
    unknown = set(look) - set(CHOICES)
    if unknown:
        raise ValueError(f"unknown look part: {', '.join(sorted(unknown))}")
    out = {**DEFAULT_LOOK, **look}
    for part, allowed in CHOICES.items():
        if out[part] not in allowed:
            raise ValueError(f"{part} must be one of {', '.join(allowed)}")
    return out


def is_default(look: dict) -> bool:
    return validate_look(look) == DEFAULT_LOOK


def look_key(look: dict) -> str:
    return hashlib.sha1(json.dumps(look, sort_keys=True).encode()).hexdigest()[:12]


def check(key: str, value: object) -> object:
    """Validates one Settings › Maxie value (Settings.update calls this); returns what to store."""
    if key == "maxie_look":
        return validate_look(value)
    if key == "voice_speed":
        lo, hi = VOICE_SPEED
        if not isinstance(value, int | float) or isinstance(value, bool) or not lo <= value <= hi:
            raise ValueError(f"voice_speed must be between {lo} and {hi}")
        return round(float(value), 2)
    allowed = {"character": CHARACTERS, "maxie_size": SIZES, "personality": tuple(PERSONALITIES),
               "chattiness": tuple(CHATTINESS), "voice": tuple(v["id"] for v in VOICES)}[key]
    if value not in allowed:
        raise ValueError(f"{key} must be one of {', '.join(allowed)}")
    return value


def style_line(personality: str, chattiness: str) -> str:
    """One line for the turn (not the system prompt, so the cache stays warm); "" for the defaults."""
    return " ".join(p for p in (PERSONALITIES.get(personality, ""), CHATTINESS.get(chattiness, "")) if p)


RENDER = Path(__file__).resolve().parents[1] / "scripts" / "render_mascot.py"
# By full path: the hub runs under launchd, whose PATH (deploy/dev.maxie.hub.plist) has no /usr/sbin.
TASKPOLICY = "/usr/sbin/taskpolicy"
log = logging.getLogger(__name__)


class LoopRenderer:
    """Re-renders the notch loops for a custom look into root/<look key>/ (a background job at low priority,
    written to <key>.part and renamed when complete). Keeps the newest `keep` looks."""

    def __init__(self, root: Path, spawn: Callable = subprocess.Popen, sync: bool = False, keep: int = 3):
        self.root, self.spawn, self.sync, self.keep = root, spawn, sync, keep
        self._jobs: dict[str, str] = {}  # look key -> "rendering" | "failed"
        self._lock = threading.Lock()

    def loops_dir(self, look: dict) -> Path | None:
        if is_default(look):
            return None
        d = self.root / look_key(look)
        return d if (d / "idle" / "meta.json").exists() else None

    def status(self, look: dict, character: str = "maxie") -> str:
        if character != "maxie" or is_default(look):
            return "bundled"
        if self.loops_dir(look):
            return "ready"
        with self._lock:
            return self._jobs.get(look_key(look), "none")

    def render(self, look: dict, character: str = "maxie") -> str:
        """Starts a render if this look has none; returns the status (bundled / ready / rendering / failed)."""
        state = self.status(look, character)
        if state in ("bundled", "ready", "rendering"):
            return state
        key = look_key(look)
        part = self.root / f"{key}.part"
        shutil.rmtree(part, ignore_errors=True)
        part.mkdir(parents=True)
        args = [TASKPOLICY, "-b", sys.executable, str(RENDER), "--custom", json.dumps(look), "--out", str(part),
                "--lean"]
        with self._lock:
            self._jobs[key] = "rendering"
        try:
            proc = self.spawn(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:  # the renderer could not even start: a failed render, never a crashed save
            log.warning("maxie render could not start: %s", e)
            shutil.rmtree(part, ignore_errors=True)
            with self._lock:
                self._jobs[key] = "failed"
            return "failed"
        if self.sync:
            self._finish(key, proc)
        else:
            threading.Thread(target=self._finish, args=(key, proc), daemon=True).start()
        return self.status(look, character)

    def _finish(self, key: str, proc) -> None:
        part, final = self.root / f"{key}.part", self.root / key
        ok = proc.wait() == 0 and (part / "idle" / "meta.json").exists()
        if ok:
            shutil.rmtree(final, ignore_errors=True)
            part.rename(final)
            self._prune(final)
        else:
            shutil.rmtree(part, ignore_errors=True)
        with self._lock:
            if ok:
                self._jobs.pop(key, None)
            else:
                self._jobs[key] = "failed"

    def _prune(self, newest: Path) -> None:
        done = sorted((d for d in self.root.iterdir() if d.is_dir() and not d.name.endswith(".part")),
                      key=lambda d: (d == newest, d.stat().st_mtime))
        for old in done[:-self.keep]:
            shutil.rmtree(old, ignore_errors=True)
