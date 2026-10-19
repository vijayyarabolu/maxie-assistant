"""Routes behind Settings (account, connections, place, voices, restore) shared by the web Settings page
and the Mac app.

Everything that touches the secret store, runs a command or calls the network goes through `deps`, so the
tests inject fakes: secret(name), store(name, value), delete(name), run_cmd(args) -> (code, output),
http (httpx.Client). Keys and tokens are never returned or logged.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import HTTPException
from pydantic import BaseModel

from ..config import delete_secret, secret, store_secret
from ..maxie_look import VOICES  # every Kokoro English voice (Settings › Voice and › Maxie)
from .place import moved_line, short_city
from .resources import snapshot

HUB_PORT_TS = 8780
TAILSCALE_SERVE = ["tailscale", "serve", "--bg", f"--https={HUB_PORT_TS}", f"http://127.0.0.1:{HUB_PORT_TS}"]
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
KEY_RE = re.compile(r"^sk-ant-[A-Za-z0-9_-]{10,}$")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _run_cmd(args: list[str]) -> tuple[int, str]:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=20)
        return out.returncode, out.stdout + out.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def default_deps() -> dict:
    return {"secret": secret, "store": store_secret, "delete": delete_secret, "run_cmd": _run_cmd,
            "http": httpx.Client(timeout=8)}


class KeyIn(BaseModel):
    key: str


class AccountIn(BaseModel):
    account: str


class PlaceIn(BaseModel):
    query: str


class HereIn(BaseModel):
    name: str
    lat: float
    lon: float
    timezone: str
    zone_name: str = ""  # the clock's everyday name on the Mac ("Central")
    first: bool = False  # the first fix after Location was turned on: fills About me in, says nothing


class RestoreIn(BaseModel):
    name: str
    folder: str


def tailscale_status(run_cmd) -> dict:
    """Is the hub served on the tailnet, and at what URL (read-only `tailscale serve status`)."""
    code, out = run_cmd(["tailscale", "serve", "status"])
    if code != 0:
        return {"served": False, "url": None}
    for line in out.splitlines():
        m = re.match(rf"^(https://\S+:{HUB_PORT_TS})\b", line.strip())
        if m:
            return {"served": True, "url": m.group(1)}
    return {"served": False, "url": None}


def register(app, s, api, deps: dict | None = None) -> None:
    d = {**default_deps(), **(deps or {})}
    secret, run_cmd, http = d["secret"], d["run_cmd"], d["http"]

    @app.get("/api/account", dependencies=api)
    def account():
        st = s.budget.status()
        return {"api_key": bool(secret("ANTHROPIC_API_KEY")), "month_usd": st.month_usd, "today_usd": st.today_usd,
                "cap_usd": s.settings.monthly_budget_usd}

    @app.post("/api/account/key", dependencies=api)
    def account_key(body: KeyIn):
        key = body.key.strip()
        if not KEY_RE.match(key):
            raise HTTPException(400, "that doesn't look like an Anthropic API key (sk-ant-...)")
        d["store"]("ANTHROPIC_API_KEY", key)
        from .setup_api import apply_anthropic_key
        apply_anthropic_key(s, key)
        s.audit.log("hub", "api_key_replaced", {}, "from Settings", "saved on this computer")
        return {"ok": True, "restart_needed": False}

    @app.get("/api/connections", dependencies=api)
    def connections():
        g = getattr(s, "google", None)
        accounts = g.accounts.public() if g is not None else []
        return {
            "google": {"client": bool(secret("GOOGLE_CLIENT_ID") and secret("GOOGLE_CLIENT_SECRET")),
                       "login_status": "", "list": accounts,
                       "accounts": {a["label"]: {"connected": not a["error"], "email": a["email"], "name": a["name"],
                                                 "picture": a["picture"]} for a in accounts},
                       "refused": _refused()},
            "jobhunt": {"token": bool(secret("JOBHUNT_TOKEN")), "url": s.settings.jobhunt_url,
                        "error": getattr(s.proactive.jobs, "error", None)},
            "resumegen": {"token": bool(secret("RESUMEGEN_TOKEN")), "url": s.settings.resumegen_url},
            "tailscale": tailscale_status(run_cmd),
            "phone": {"last_ask": _phone_last_ask(), "pushes": len(s.push.subscriptions())},
        }

    def _refused() -> list[dict]:
        """What Google said no to last, with why and the exact fix; empty once it answers."""
        last = getattr(s.proactive.cal, "refused", None)
        return [last.dict()] if last is not None else []

    def _phone_last_ask() -> float | None:
        row = s.db.one("SELECT value FROM hub_meta WHERE key='phone_last_ask'")
        return float(row["value"]) if row else None

    @app.post("/api/connections/tailscale", dependencies=api)
    def tailscale_expose():
        code, out = run_cmd(TAILSCALE_SERVE)
        if code != 0:
            raise HTTPException(500, f"tailscale serve failed: {out[:300]}")
        s.audit.log("hub", "tailscale_serve", {"port": HUB_PORT_TS}, "from Settings", "ok")
        return {"ok": True, **tailscale_status(run_cmd)}

    @app.post("/api/place", dependencies=api)
    def place(body: PlaceIn):
        """City -> coordinates + timezone (Open-Meteo geocoding), saved to Settings."""
        try:
            r = http.get(GEOCODE, params={"name": body.query.strip(), "count": 1, "language": "en"})
            r.raise_for_status()
            hit = (r.json().get("results") or [None])[0]
        except (httpx.HTTPError, ValueError) as e:
            raise HTTPException(502, f"place lookup failed: {e}") from e
        if not hit:
            raise HTTPException(404, f"no place called {body.query!r}")
        name = ", ".join(x for x in [hit.get("name"), hit.get("admin1")] if x)
        s.settings.update({"weather_place": name, "weather_lat": float(hit["latitude"]),
                           "weather_lon": float(hit["longitude"]), "timezone": hit.get("timezone") or s.settings.timezone})
        s.settings.save()
        return {"place": name, "lat": s.settings.weather_lat, "lon": s.settings.weather_lon, "timezone": s.settings.timezone}

    @app.post("/api/place/here", dependencies=api)
    def place_here(body: HereIn):
        """Location (significant changes only, FIX-UX6 §8): Maxie.app sends the city it reverse-geocoded on the
        Mac. About me follows you unless you set a fixed city; a real move is said once in the panel."""
        name = body.name.strip()
        if not (-90 <= body.lat <= 90 and -180 <= body.lon <= 180) or not 0 < len(name) <= 80 or len(body.zone_name) > 40:
            raise HTTPException(400, "that isn't a place")
        try:
            ZoneInfo(body.timezone)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise HTTPException(400, f"unknown timezone {body.timezone!r}") from e
        if not s.settings.follow_location:
            return {"ignored": True, "moved": False, "message": None, "place": s.settings.weather_place}
        moved = not body.first and short_city(name) != short_city(s.settings.weather_place)
        message = moved_line(name, body.timezone, body.zone_name, body.timezone != s.settings.timezone) if moved else None
        s.settings.update({"weather_place": name, "weather_lat": body.lat, "weather_lon": body.lon, "timezone": body.timezone})
        s.settings.save()
        if message:
            s.events.transcript("assistant", message, final=True)
        return {"place": name, "lat": body.lat, "lon": body.lon, "timezone": body.timezone, "moved": moved, "message": message}

    @app.get("/api/resources", dependencies=api)
    def resources():
        return snapshot(**({"pids_fn": d["pids_fn"], "ps_fn": d["ps_fn"]} if "ps_fn" in d else {}))

    @app.get("/api/voice/voices", dependencies=api)
    def voices():
        return {"current": s.settings.voice, "voices": VOICES}

    @app.get("/api/backup/list", dependencies=api)
    def backup_list():
        return {"backups": s.backup.list_backups()}

    @app.post("/api/backup/restore", dependencies=api)
    async def backup_restore(body: RestoreIn):
        if not NAME_RE.match(body.name):
            raise HTTPException(400, "bad backup name")
        into = Path(body.folder).expanduser()
        try:
            dbs = await asyncio.to_thread(s.backup.restore, body.name, into)
        except FileNotFoundError as e:
            raise HTTPException(404, f"no backup called {e}") from e
        s.audit.log("hub", "backup_restore", {"name": body.name}, "from Settings", f"into {into}")
        return {"ok": True, "folder": str(into), "databases": [str(p) for p in dbs]}
