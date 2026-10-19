"""Settings › Setup: one status panel for everything Maxie can use, a Test button per item, the key fields,
and the "Connect Google" OAuth flow.

Each item is `ok` (connected and, if tested, answering), `missing` (needs something from you), `failing`
(set up but the last test or call failed), `off` (optional and turned off) or `demo` (the demo hub fakes it).
Nothing here ever returns a key or token, and nothing crashes on a missing piece: it becomes a line.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from ..config import data_dir, delete_secret, secret_source, store_secret
from ..config import secret as secret_default
from ..hands import google_real
from ..hands.google import MAIN
from ..router.haiku import HaikuRouter
from .settings_api import KEY_RE

CALLBACK = "/oauth/google/callback"
ANTHROPIC_KEYS_URL = "https://console.anthropic.com/settings/keys"
PRICING_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
# Keys you can paste in Settings, with how to check one before saving it.
PASTEABLE = {
    "ANTHROPIC_API_KEY": lambda v: bool(KEY_RE.match(v)),
    "LAYA_API_KEY": lambda v: 8 <= len(v) <= 512,
    "JOBHUNT_TOKEN": lambda v: 8 <= len(v) <= 512,
    "RESUMEGEN_TOKEN": lambda v: 8 <= len(v) <= 512,
    "BACKUP_PASSPHRASE": lambda v: len(v) >= 12,
}
MAC_ONLY = ("mac", "voice", "notch")


@dataclass
class Item:
    id: str
    name: str
    state: str  # ok | missing | failing | off | demo
    line: str
    optional: bool = False
    mac_only: bool = False
    testable: bool = True
    tested: str = ""  # the last Test's answer, if any


class SecretIn(BaseModel):
    name: str
    value: str


class ClientIn(BaseModel):
    json_text: str = ""
    client_id: str = ""
    client_secret: str = ""


class ConnectIn(BaseModel):
    label: str = MAIN


def apply_anthropic_key(s, key: str | None) -> None:
    """Use a new key right away: the brain, the Haiku router step and the models check all switch over."""
    if getattr(s, "mode", {}).get("demo"):
        return
    if not key:
        s.brain.client, s.anthropic_sync, s.router.haiku = None, None, None
        s.mode["brain"] = "no API key"
        return
    import anthropic
    s.brain.client = anthropic.AsyncAnthropic(api_key=key)
    s.anthropic_sync = anthropic.Anthropic(api_key=key)
    s.router.haiku = HaikuRouter(s.anthropic_sync, s.budget, s.models)
    s.mode["brain"] = "on"


def register(app, s, api, deps: dict | None = None) -> None:
    d = deps or {}
    secret = d.get("secret", secret_default)
    store = d.get("store", store_secret)
    delete = d.get("delete", delete_secret)
    http: httpx.Client = d.get("http") or httpx.Client(timeout=10)
    run_cmd = d.get("run_cmd")
    system = d.get("system", sys.platform)
    demo = bool(s.mode.get("demo"))
    google = getattr(s, "google", None)
    if google is not None and "secret" in d:  # tests: the same fake store the routes write to
        google = google_real.LiveGoogle(secret, store, http)
        s.google = google
    accounts = google_real.Accounts(secret, store)
    logins = google_real.Logins(d.get("clock", time.time))
    tested: dict[str, tuple[bool, str]] = {}

    def oauth() -> google_real.OAuth | None:
        cid, csec = secret("GOOGLE_CLIENT_ID"), secret("GOOGLE_CLIENT_SECRET")
        return google_real.OAuth(cid, csec, http) if cid and csec else None

    def mark(item: Item) -> Item:
        if item.id in tested:
            ok, line = tested[item.id]
            item.tested = line
            if not ok and item.state == "ok":
                item.state = "failing"
        return item

    def items() -> list[Item]:
        out = []
        on_mac = system == "darwin"
        key_src = None if demo else (secret_source("ANTHROPIC_API_KEY") if "secret" not in d else
                                     ("file" if secret("ANTHROPIC_API_KEY") else None))
        if demo:
            out.append(Item("anthropic", "Claude API key", "demo", "Demo hub: chat answers come from a script, not Claude."))
        elif key_src:
            out.append(Item("anthropic", "Claude API key", "ok",
                            "Set" + (" by the ANTHROPIC_API_KEY environment variable." if key_src == "env" else ".")))
        else:
            out.append(Item("anthropic", "Claude API key", "missing",
                            "Needed for chat, briefs and anything the rules can't answer. Without it, "
                            "commands like open an app, timers, maths and the time still work."))

        if demo:
            out.append(Item("google", "Google", "demo", "Demo hub: made-up mail, calendar and files."))
        else:
            acc = accounts.public()
            if not (secret("GOOGLE_CLIENT_ID") and secret("GOOGLE_CLIENT_SECRET")):
                out.append(Item("google", "Google", "missing",
                                "Add your own OAuth client first (the steps are below), then Connect Google.",
                                optional=True, testable=False))
            elif not acc:
                out.append(Item("google", "Google", "missing", "OAuth client saved. Press Connect Google.",
                                optional=True, testable=False))
            elif bad := [a for a in acc if a["error"]]:
                out.append(Item("google", "Google", "failing",
                                f"{bad[0]['email']}: {bad[0]['error']}", optional=True))
            else:
                out.append(Item("google", "Google", "ok",
                                "Connected: " + ", ".join(f"{a['email']} ({a['label']})" for a in acc) + ".",
                                optional=True))

        place = s.settings.weather_place
        out.append(Item("weather", "Weather", "demo" if demo else ("ok" if place else "missing"),
                        "Demo hub: a made-up forecast." if demo else
                        (f"Open-Meteo for {place}. Free, no key." if place else
                         "Set your city in About me. Open-Meteo is free and needs no key."),
                        optional=True, testable=bool(place) or demo))

        laya_on = bool(s.settings.laya_url) and not demo
        up = laya_on and s.laya is not None and s.laya.healthy()
        out.append(Item("laya", "Local router (Laya)", "off" if not laya_on else ("ok" if up else "failing"),
                        "Off. The router goes rules, then Claude Haiku, then keywords." if not laya_on else
                        (f"Answering at {s.settings.laya_url}." if up else
                         f"Not answering at {s.settings.laya_url} (make laya starts it). The router skips it meanwhile."),
                        optional=True, testable=laya_on))

        out.append(Item("push", "Phone notifications", "demo" if demo else ("ok" if s.push.enabled else "missing"),
                        "Demo hub: notifications aren't sent." if demo else
                        f"Web push keys are set. {len(s.push.subscriptions())} device(s) subscribed."
                        if s.push.enabled else "No push keys yet; the hub makes them on its first real start.",
                        optional=True, testable=s.push.enabled))

        jobs = s.fakes.get("jobs")
        if demo:
            out.append(Item("jobs", "Job search apps", "demo", "Demo hub: made-up jobs and applications.", optional=True))
        elif getattr(jobs, "off", False):
            out.append(Item("jobs", "Job search apps", "off",
                            "Off. These are two companion apps of my own (jobhunt and resumegen). Add their URLs "
                            "below if you run them; nothing else depends on them.", optional=True, testable=False))
        else:
            err = getattr(jobs, "error", None)
            out.append(Item("jobs", "Job search apps", "failing" if err else "ok",
                            err or f"jobhunt at {s.settings.jobhunt_url}.", optional=True))

        claude = shutil.which("claude")
        out.append(Item("claude_code", "Claude Code (long jobs)", "ok" if claude else "off",
                        "Found the claude command. Long coding jobs can run on your Claude plan instead of API credit."
                        if claude else "Optional. Install Claude Code to hand long jobs to `claude -p`.",
                        optional=True, testable=bool(claude)))

        note = f"Mac only. This hub runs on {'Linux' if system.startswith('linux') else system}, so this is off."
        if demo:
            note += " The demo hub fakes the Mac actions so you can try them."
        out.append(Item("mac", "Mac actions", "ok" if on_mac else "off",
                        "Open apps, music, volume, timers, Notes, Reminders and Shortcuts." if on_mac else note,
                        optional=True, mac_only=True, testable=False))
        out.append(Item("voice", "\"Hey Max\" voice", "ok" if on_mac else "off",
                        "Runs as its own service on the Mac (deploy/install.sh voice)." if on_mac else note,
                        optional=True, mac_only=True, testable=False))
        out.append(Item("notch", "Notch app", "ok" if on_mac else "off",
                        "Build it with make notch-run, then Connect the Mac app under Access." if on_mac else note,
                        optional=True, mac_only=True, testable=False))

        out.append(Item("backup", "Encrypted backups", "ok" if secret("BACKUP_PASSPHRASE") else "off",
                        "Nightly, encrypted with your passphrase." if secret("BACKUP_PASSPHRASE") else
                        "Optional. Set a backup passphrase to turn on nightly encrypted backups.",
                        optional=True, testable=False))
        return [mark(i) for i in out]

    @app.get("/api/setup", dependencies=api)
    def setup():
        return {"items": [asdict(i) for i in items()], "demo": demo, "system": system,
                "data_dir": str(data_dir()), "budget": asdict(s.budget.status()),
                "google": {"client": bool(secret("GOOGLE_CLIENT_ID") and secret("GOOGLE_CLIENT_SECRET")),
                           "accounts": [] if demo else accounts.public(), "callback": CALLBACK},
                "keys": {name: bool(secret(name)) for name in PASTEABLE},
                "links": {"anthropic_keys": ANTHROPIC_KEYS_URL, "pricing": PRICING_URL}}

    def run_test(item: str) -> tuple[bool, str]:
        if demo:
            return True, "Demo hub: nothing real is called."
        if item == "anthropic":
            client = s.anthropic_sync
            if client is None:
                return False, "No key yet."
            try:
                client.models.list(limit=1)
            except Exception as e:  # shown as the test's answer  # noqa: BLE001
                return False, f"Anthropic said no: {str(e)[:160]}"
            return True, "The key works. Listing models is free."
        if item == "google":
            g = s.google
            if g is None or not g.labels():
                return False, "No Google account connected."
            lines, ok = [], True
            for label in g.labels():
                try:
                    g.search(label, "", None)
                    lines.append(f"{label}: Gmail answered")
                except Exception as e:  # noqa: BLE001
                    ok = False
                    lines.append(f"{label}: {str(e)[:120]}")
            return ok, "; ".join(lines) + "."
        if item == "weather":
            try:
                return True, s.weather.forecast(1)[:200]
            except Exception as e:  # noqa: BLE001
                return False, f"Open-Meteo didn't answer: {str(e)[:120]}"
        if item == "laya":
            return (True, "Laya answered.") if (s.laya and s.laya.healthy()) else (False, "Laya isn't answering.")
        if item == "push":
            subs = s.push.subscriptions()
            if not subs:
                return False, "No device has turned on notifications yet (Turn on notifications in the app)."
            s.push.push("Maxie", "Test from Settings. Notifications work.", tag="test", mood="happy")
            return True, f"Sent to {len(subs)} device(s)."
        if item == "jobs":
            jobs = s.fakes.get("jobs")
            jobs.new_jobs()
            err = getattr(jobs, "error", None)
            return (False, err) if err else (True, "jobhunt answered.")
        if item == "claude_code":
            code, out = run_cmd(["claude", "--version"]) if run_cmd else _run(["claude", "--version"])
            return code == 0, out.strip()[:120] or "no answer"
        raise HTTPException(404, f"nothing to test for {item}")

    @app.post("/api/setup/test/{item}", dependencies=api)
    def setup_test(item: str):
        ok, line = run_test(item)
        tested[item] = (ok, line)
        s.audit.log("hub", "setup_test", {"item": item}, "Settings", "ok" if ok else line[:120])
        return {"ok": ok, "line": line, "items": [asdict(i) for i in items()]}

    @app.post("/api/setup/secret", dependencies=api)
    def setup_secret(body: SecretIn):
        name, value = body.name.strip(), body.value.strip()
        check = PASTEABLE.get(name)
        if check is None:
            raise HTTPException(400, f"{name} can't be set here")
        if not check(value):
            hint = " Anthropic keys start with sk-ant-." if name == "ANTHROPIC_API_KEY" else ""
            raise HTTPException(400, f"That doesn't look right for {name}.{hint}")
        try:
            store(name, value)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        tested.pop({"ANTHROPIC_API_KEY": "anthropic", "LAYA_API_KEY": "laya"}.get(name, name), None)
        if name == "ANTHROPIC_API_KEY":
            apply_anthropic_key(s, value)
        if name in ("JOBHUNT_TOKEN", "RESUMEGEN_TOKEN") and hasattr(s.fakes.get("jobs"), "set_tokens"):
            s.fakes["jobs"].set_tokens(**{name.split("_")[0].lower() + "_token": value})
        if name == "BACKUP_PASSPHRASE":
            s.backup.passphrase = value
        s.audit.log("user", "secret_saved", {"name": name}, "Settings", "saved on this computer")
        return {"ok": True, "items": [asdict(i) for i in items()]}

    @app.delete("/api/setup/secret/{name}", dependencies=api)
    def setup_secret_delete(name: str):
        if name not in PASTEABLE:
            raise HTTPException(400, f"{name} can't be removed here")
        delete(name)
        if name == "ANTHROPIC_API_KEY":
            apply_anthropic_key(s, secret(name))  # an environment variable may still set one
        s.audit.log("user", "secret_removed", {"name": name}, "Settings", "removed")
        return {"ok": True, "items": [asdict(i) for i in items()]}

    # ---- Connect Google

    @app.post("/api/google/client", dependencies=api)
    def google_client(body: ClientIn):
        """Your own OAuth client (Desktop app type), pasted as the downloaded JSON or as the two values."""
        try:
            if body.json_text.strip():
                cid, csec = google_real.parse_client(body.json_text)
            else:
                cid, csec = body.client_id.strip(), body.client_secret.strip()
                if not cid.endswith(".apps.googleusercontent.com") or len(csec) < 8:
                    raise ValueError("The client ID ends in .apps.googleusercontent.com, and the secret is the "
                                     "string next to it in Google Cloud.")
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        store("GOOGLE_CLIENT_ID", cid)
        store("GOOGLE_CLIENT_SECRET", csec)
        s.audit.log("user", "google_client_saved", {}, "Settings", "saved on this computer")
        return {"ok": True}

    @app.delete("/api/google/client", dependencies=api)
    def google_client_delete():
        delete("GOOGLE_CLIENT_ID")
        delete("GOOGLE_CLIENT_SECRET")
        return {"ok": True}

    @app.post("/api/google/connect", dependencies=api)
    def google_connect(body: ConnectIn, request: Request):
        """The consent page URL. The browser goes there, and Google sends it back to this hub's callback."""
        if demo:
            raise HTTPException(409, "The demo hub fakes Google. Run the real hub (make run) to connect an account.")
        label = body.label.strip().lower()
        if not google_real.LABEL_RE.match(label):
            raise HTTPException(400, "a label is a short lowercase word, like main, job or school")
        o = oauth()
        if o is None:
            raise HTTPException(409, "Add your Google OAuth client first (the steps are on this page).")
        host = request.url.hostname or ""
        if host not in ("127.0.0.1", "localhost"):
            raise HTTPException(409, "Connect Google from the computer that runs Maxie: open http://127.0.0.1:"
                                     f"{request.url.port or s.settings.hub_port}/settings.html there. Google only "
                                     "sends the sign-in back to this computer.")
        redirect = f"{request.url.scheme}://{host}:{request.url.port or s.settings.hub_port}{CALLBACK}"
        hint = (accounts.get(label) or {}).get("email", "")
        return {"url": logins.start(o, label, redirect, hint)}

    @app.get(CALLBACK)
    def google_callback(state: str = "", code: str = "", error: str = ""):
        """Google's redirect lands here. No session cookie arrives on a cross-site redirect, so the one-time
        `state` from /api/google/connect is what proves the flow started on this hub."""
        pending = logins.take(state) if state else None
        if pending is None:
            return _page("That sign-in link expired or was already used. Go back to Settings and press Connect again.")
        label, verifier, redirect = pending
        if error or not code:
            return _page(f"Google didn't connect the account ({error or 'cancelled'}). Nothing was saved.")
        o = oauth()
        if o is None:
            return _page("The OAuth client was removed while you were signing in. Add it again in Settings.")
        try:
            acct = google_real.finish_login(o, accounts, label, code, verifier, redirect, http)
        except (httpx.HTTPError, PermissionError, KeyError, ValueError) as e:
            s.audit.log("hub", "google_connect_failed", {"label": label}, "Connect Google", str(e)[:200])
            return _page(f"Google connected, but the token swap failed: {str(e)[:200]}")
        tested.pop("google", None)
        s.audit.log("user", "google_connected", {"label": label, "email": acct["email"]}, "Connect Google", "ok")
        return RedirectResponse(f"/settings.html?connected={label}#google", status_code=303)

    @app.post("/api/google/accounts/{label}/disconnect", dependencies=api)
    def google_disconnect(label: str):
        gone = accounts.remove(label)
        if gone is None:
            raise HTTPException(404, "no account with that label")
        o = oauth()
        revoked = bool(o and o.revoke(gone["refresh_token"]))
        s.audit.log("user", "google_disconnected", {"label": label}, "Settings", "revoked" if revoked else "forgotten")
        return {"ok": True, "revoked": revoked}


def _run(args: list[str]) -> tuple[int, str]:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=20)
        return out.returncode, out.stdout + out.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def _page(text: str) -> HTMLResponse:
    from html import escape
    return HTMLResponse(f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Maxie</title><link rel="stylesheet" href="/styles.css">
<main style="max-width:420px;margin:15vh auto;padding:16px;text-align:center"><h1>Maxie</h1><p>{escape(text)}</p>
<p><a href="/settings.html#google">Back to Settings</a></p></main>""")

