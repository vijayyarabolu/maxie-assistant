"""Settings and secrets, both kept on this computer in the data folder (~/.maxie unless MAXIE_DATA says
otherwise), never in the repo.

- <data>/settings.json: everything Settings shows (About me, budget, voice, rules...).
- <data>/secrets.json: API keys, the Google OAuth client, hub tokens. Written with mode 0600.

An environment variable with the same name wins over secrets.json, which is handy for CI and for a
throwaway demo hub. Settings shows which one is in use.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import maxie_look
from .voice.wake import ALL_FORMS as WAKE_FORMS

SECRETS_FILE = "secrets.json"
NAME_RE = re.compile(r"[A-Z][A-Z0-9_]{2,60}")
SECRET_SHAPE = re.compile(r"^[A-Za-z0-9._~+/=:-]{8,4096}$")


def data_dir() -> Path:
    env = os.environ.get("MAXIE_DATA")
    path = Path(env).expanduser() if env else Path.home() / ".maxie"
    if os.environ.get("MAXIE_SANDBOX") == "1":
        from .testdata import is_live
        if is_live(path):
            raise RuntimeError(f"tests and evals never use the live data folder ({path}); call testdata.sandbox() first")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _secrets_path() -> Path:
    return data_dir() / SECRETS_FILE


def _read_secrets() -> dict[str, str]:
    try:
        raw = json.loads(_secrets_path().read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(raw, dict) else {}


def _write_secrets(values: dict[str, str]) -> None:
    """Atomic, and never readable by other users: a temp file at 0600, then a rename."""
    path = _secrets_path()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".secrets-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(values, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def secret(name: str) -> str | None:
    """The environment first, then <data>/secrets.json. None when unset."""
    return os.environ.get(name) or _read_secrets().get(name) or None


def secret_source(name: str) -> str | None:
    """"env", "file" or None: Settings says where a key came from."""
    if os.environ.get(name):
        return "env"
    return "file" if _read_secrets().get(name) else None


def store_secret(name: str, value: str) -> None:
    if not NAME_RE.fullmatch(name):
        raise ValueError(f"bad secret name {name!r}")
    if not SECRET_SHAPE.match(value) and not (name.startswith("GOOGLE_") and value.startswith("{")):
        raise ValueError("that doesn't look like a key or token")
    values = _read_secrets()
    values[name] = value
    _write_secrets(values)


def delete_secret(name: str) -> bool:
    values = _read_secrets()
    if name not in values:
        return False
    del values[name]
    _write_secrets(values)
    return True


def local_timezone() -> str:
    """This computer's IANA zone (TZ, then /etc/localtime), or UTC."""
    tz = os.environ.get("TZ", "")
    if "/" in tz:
        return tz.lstrip(":")
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "UTC"


SLEEP_MODES = ("presence", "fixed")
BOOL_KEYS = ("brief_sleep", "screen_when_away", "warm_sonnet", "paused")


def _excluded(value: object) -> list[str]:
    """Apps he never wants watched: bundle ids or app names, trimmed, no repeats, at most 50."""
    if not isinstance(value, list) or not all(isinstance(v, str) and 0 < len(v.strip()) <= 100 for v in value):
        raise ValueError("watch_excluded must be a list of app names or bundle ids")
    out = list(dict.fromkeys(v.strip() for v in value))
    if len(out) > 50:
        raise ValueError("at most 50 excluded apps")
    return out


@dataclass
class Settings:
    monthly_budget_usd: float = 20.0
    budget_warn_ratio: float = 0.8
    timezone: str = field(default_factory=lambda: local_timezone())
    user_name: str = ""  # "" until Setup asks; Maxie then just says "you"
    work_start: str = "09:00"  # your work hours, for context ("About me" in Settings)
    work_end: str = "18:00"
    weather_place: str = ""  # "" = not set: the weather tool asks you to set a city in Settings
    weather_lat: float = 0.0
    weather_lon: float = 0.0
    follow_location: bool = True  # About me's city and clock follow Location; False: a fixed city
    # "presence" = Maxie sleeps when he sleeps (maxie.presence); "fixed" = quiet hours below
    sleep_mode: str = "presence"
    # apps (bundle ids or names) that are never watched
    watching: bool = False  # watch and learn (Mac notch app only): off until you turn it on in Settings
    watch_excluded: list[str] = field(default_factory=list)
    brief_sleep: bool = False  # "Slept 2:05 to 9:40" in the morning brief, only if he turns it on
    # a background task that needs the real screen runs once he steps away (else it asks first)
    screen_when_away: bool = False
    warm_sonnet: bool = False  # keep Sonnet's cached prefix warm too (an experiment measures if it pays)
    quiet_start: str = "23:00"
    quiet_end: str = "07:00"
    proactive_muted: bool = False
    morning_brief_at: str = "08:00"
    evening_wrap_at: str = "21:00"
    daily_application_goal: int = 10
    voice: str = "af_heart"  # Kokoro voice id on the Mac; the phone uses its own voices
    voice_muted: bool = False  # the Mac stops listening for "Hey Max" (menu bar toggle)
    paused: bool = False  # Pause Maxie: no listening, watching or work of his own (hub/pause_api)
    voice_input_device: str = ""  # sounddevice input name; "" = system default
    speak_replies: bool = True  # the Mac speaks replies to "Hey Max"; off = text in the panel only
    whisper_at_night: bool = True  # in quiet hours the Mac speaks softer and a little slower
    wake_words: list[str] = field(default_factory=lambda: list(WAKE_FORMS))  # which of the four forms wake Maxie
    hub_host: str = "127.0.0.1"
    hub_port: int = 8780  # jobhunt --lan is on 8765, resumegen on 8770
    laya_url: str = ""  # the optional local router model (make laya: http://127.0.0.1:8000); "" = off
    # Optional sibling apps for job search (my own jobhunt and resumegen). "" = off.
    jobhunt_url: str = ""
    resumegen_url: str = ""
    jobhunt_token_file: str = ""  # where jobhunt keeps its LAN token, for the one-click Link
    resumegen_token_file: str = ""
    allowed_folders: list[str] = field(default_factory=lambda: ["~/Documents", "~/Downloads", "~/Desktop"])
    bot_moments: bool = True  # Maxie's rare idle moments (a glance, a hop): at most three a day, never in quiet hours
    ask_my_claude: bool = True  # "ask my Claude" / "use my plan" runs the official Claude Code CLI with his login
    auto_upgrade_models: bool = True  # daily GET /v1/models; newer stable Haiku/Sonnet/Opus switch in after a check
    model_pins: dict = field(default_factory=dict)  # tier -> id ("pin current" fills this and turns auto off)
    action_rules: dict = field(default_factory=dict)  # tool name -> allow | ask | block (Settings › Rules); defaults by kind
    # Settings › Maxie
    character: str = "maxie"  # "classic": the Classic robot instead
    maxie_look: dict = field(default_factory=dict)  # {} = his pick: Mochi, no headphones
    maxie_size: str = "medium"  # the free-floating bot: small | medium | large
    personality: str = "friend"  # friend | coach | butler | playful
    chattiness: str = "normal"  # quiet | normal | chatty
    voice_speed: float = 1.05  # Kokoro speed (whisper mode slows it a little more at night)

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or data_dir() / "settings.json"
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: Path | None = None) -> None:
        path = path or data_dir() / "settings.json"
        path.write_text(json.dumps(asdict(self), indent=2))

    def update(self, changes: dict) -> Settings:
        known = {f.name: f for f in fields(self)}
        for key, value in changes.items():
            if key not in known:
                raise ValueError(f"unknown setting: {key}")
            if key.startswith("hub_"):
                raise ValueError(f"{key} can only be changed on the Mac")
            if key == "wake_words" and (not isinstance(value, list) or not set(value) <= set(WAKE_FORMS)):
                raise ValueError(f"wake_words must be a list drawn from {', '.join(WAKE_FORMS)}")
            if key == "follow_location" and not isinstance(value, bool):
                raise ValueError("follow_location must be true or false")
            if key == "sleep_mode" and value not in SLEEP_MODES:
                raise ValueError(f"sleep_mode must be one of {', '.join(SLEEP_MODES)}")
            if key == "watching" and not isinstance(value, bool):
                raise ValueError("watching must be true or false")
            if key == "watch_excluded":
                value = _excluded(value)
            if key in BOOL_KEYS and not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
            if key in maxie_look.MAXIE_KEYS:
                value = maxie_look.check(key, value)
            setattr(self, key, value)
        return self
