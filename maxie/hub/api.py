"""Hub API (FastAPI). Binds 127.0.0.1 by default; a phone can reach it over HTTPS on a private network
(Tailscale serve works well), never over the open internet.

Every /api route needs a signed-in browser (passcode, then a session cookie) or an app token
(`Authorization: Bearer <token>` made in Settings). /health is open but says nothing private, so a
client can tell "hub asleep" from "not signed in". See signin.py.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import testdata
from ..background import work as background_work
from ..backup import status_dict as backup_status
from ..budget import price_guessed
from ..config import data_dir, secret, store_secret
from ..db import SCHEMA_VERSION
from ..evals import load as load_eval_commands
from ..events import sse
from ..flows import FlowError
from ..hands.apple import AppleUnavailable
from ..latency import TARGET_MS
from ..memory import TOPICS
from ..models import PINNED, list_from_api, pretty, smoke_with_api
from ..presence import goodnight_said
from ..proactive import run_scheduler
from ..router import ROUTES
from ..router.calibrate import calibrate, load_labels
from ..spoken import spoken
from ..warm_experiment import models_to_warm
from . import (
    background_api,
    decisions_api,
    maxie_api,
    pause_api,
    presence_api,
    settings_api,
    setup_api,
    signin,
    voice_api,
    watch_api,
)
from .services import Services, build

WEB = Path(__file__).resolve().parents[2] / "web"
STARTER_LABELS = Path(__file__).resolve().parents[1] / "router" / "starter_labels.jsonl"
VERSION = "0.1.0"
WARM_EVERY_S = 55 * 60   # Haiku's cached prefix lives an hour; rewrite it just before it expires
DEMO_PASSCODE = "demo1234"
KEEPALIVE_S = 45         # a free GET keeps the HTTPS connection to the API open (saves ~250 ms on a cold turn)


class ChatIn(BaseModel):
    text: str
    conversation: str = "default"
    device: str = "mac"  # "mac" | "phone"
    images: list[str] = []  # base64 PNG/JPEG (a screenshot from the notch app, a paste); never stored
    files: list[str] = []   # paths in allowed folders (dropped on the panel)
    screen: bool = False    # "Maxie, look": the hub asks Maxie.app for one screenshot


class AskIn(BaseModel):
    text: str
    conversation: str = "phone"


class ConfirmIn(BaseModel):
    approve: bool


class MemoryIn(BaseModel):
    markdown: str


class FlowIn(BaseModel):
    name: str
    steps: list[dict]


class FlowResultIn(BaseModel):
    ok: bool
    done: int = 0  # steps finished before it stopped
    error: str = ""


class FactIn(BaseModel):
    text: str
    topic: str | None = None


class StateIn(BaseModel):
    state: str


class LabelIn(BaseModel):
    id: int
    route: str


class RuleIn(BaseModel):
    name: str
    rule: str


def create_app(services: Services, token: str | None = None, scheduler: bool = True,
               deps: dict | None = None) -> FastAPI:
    """`token`: a fixed Bearer token (HUB_TOKEN; tests and `make demo`). `deps` overrides the Settings
    routes' secret store / command / HTTP helpers (tests)."""
    s = services
    d = deps or {}
    clock = d.get("clock", time.time)
    secret_of = d.get("secret", secret)
    sessions = signin.Sessions(s.db, clock)
    tokens = signin.Tokens(s.db, clock)
    passcode = signin.Passcode(s.db)
    demo = os.environ.get("MAXIE_FAKES") == "1"
    if demo and not passcode.is_set:
        passcode.set(DEMO_PASSCODE)  # the demo hub's sign-in page says so

    def auth(request: Request, response: Response, authorization: str | None = Header(default=None)) -> None:
        """An app token or HUB_TOKEN (Bearer), or a signed-in browser's session cookie, rotated when due."""
        given = (authorization or "").removeprefix("Bearer ").strip()
        if given:
            fixed = token or secret_of("HUB_TOKEN")
            if (fixed and hmac.compare_digest(given.encode(), fixed.encode())) or tokens.check(given):
                return
            raise HTTPException(401, "bad token")
        raw = request.cookies.get(signin.COOKIE)
        live = sessions.check(raw) if raw else None
        if live is None:
            raise HTTPException(401, "not signed in")
        if live[1]:
            signin.set_cookie(request, response, live[1], sessions.seconds_left(live[1]))

    def models_check() -> list[dict]:
        """GET /v1/models; switch a tier to a newer stable model that passes its smoke check."""
        client = s.anthropic_sync
        if client is None:
            return []
        smoke = smoke_with_api(client, s.budget, load_eval_commands()[:12])
        changes = s.models.check(lambda: list_from_api(client), smoke,
                                 before_switch=lambda tier, new: s.backup.run(f"before {tier} upgrade to {new}"))
        for u in changes:
            s.audit.log("hub", "model_upgrade" if u.ok else "model_upgrade_skipped", {"tier": u.tier},
                        u.note, f"{u.old} -> {u.new}")
        return [asdict(u) for u in changes]

    s.proactive.models_check = models_check

    def schema_guard() -> bool:
        """True when this is the first start on a new schema version (tables migrate themselves at init);
        the backup that marks the change runs after the hub is up, so a slow disk never delays it."""
        row = s.db.one("SELECT value FROM hub_meta WHERE key='schema_version'")
        seen = int(row["value"]) if row else 0
        if seen == SCHEMA_VERSION:
            return False
        s.db.execute("INSERT OR REPLACE INTO hub_meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
        return bool(seen)

    s.db.script("CREATE TABLE IF NOT EXISTS hub_meta(key TEXT PRIMARY KEY, value TEXT)")
    schema_changed = schema_guard()

    @asynccontextmanager
    async def lifespan(_app):
        tasks = []
        if schema_changed and scheduler:
            tasks.append(asyncio.create_task(asyncio.to_thread(s.backup.run, f"after schema change to {SCHEMA_VERSION}")))
        if scheduler:
            tasks.append(asyncio.create_task(run_scheduler(s.proactive, paused=lambda: s.settings.paused)))
            tasks.append(asyncio.create_task(weekly_recalibration()))
            tasks.append(asyncio.create_task(keep_fast()))
            tasks.append(asyncio.create_task(background_work(
                s.background, s.agent, on_error=lambda m: s.audit.log("hub", "background_error", {}, "", m[:200]),
                paused=lambda: s.settings.paused)))
            if s.models.due():
                tasks.append(asyncio.create_task(asyncio.to_thread(models_check)))  # on hub start
        yield
        for t in tasks:
            t.cancel()

    async def keep_fast():
        """Waking hours only: pre-warm Haiku's cached prefix (hub start, then every 55 min) and keep the
        connection to the API open. Costs about a cent a day; the first word after a pause stays fast.
        Sonnet too while `warm_sonnet` is on (the experiment in maxie.warm_experiment decides)."""
        last_warm = 0.0
        while True:
            try:
                if s.brain.client is not None and not s.proactive.asleep() and not s.settings.paused:
                    if time.time() - last_warm > WARM_EVERY_S:
                        warmed = [await s.brain.warm(m) for m in models_to_warm(s.settings, s.models)]
                        if warmed[0]:
                            last_warm = time.time()
                    else:
                        await s.brain.client.models.list(limit=1)
            except Exception as e:  # never let a warm-up kill the loop  # noqa: BLE001
                s.audit.log("hub", "keep_fast_error", {}, "", str(e)[:200])
            await asyncio.sleep(KEEPALIVE_S)

    async def weekly_recalibration():
        while True:
            await asyncio.to_thread(recalibrate)
            await asyncio.sleep(7 * 86400)

    def recalibrate() -> dict:
        if not (s.laya and s.laya.healthy()):
            return {"ok": False, "reason": "Laya is not running"}
        examples = load_labels(STARTER_LABELS) + s.router.labelled()

        def predict(text):
            r = s.laya.classify(text)
            return (r.route, r.route_p) if r else None

        cal = calibrate(examples, predict)
        s.router.calibration = cal
        if s.router.calibration_path:
            cal.save(s.router.calibration_path)
        s.audit.log("hub", "recalibrate", {"n": cal.n}, "weekly", f"accuracy {cal.accuracy}")
        decisions_api.run(s)  # per-type deciders, scored on the same labels
        return {"ok": True, **asdict(cal)}

    app = FastAPI(title="Claude hub", version=VERSION, lifespan=lifespan)
    live_hub = (os.environ.get("MAXIE_FAKES") != "1" and s.db.path != ":memory:"
                and testdata.is_live(Path(s.db.path)))

    def no_test_traffic(request: Request) -> None:
        """The live hub never takes test traffic (DESIGN.md): tests and benchmarks use the fake hub."""
        if live_hub and request.headers.get(testdata.TEST_HEADER):
            raise HTTPException(403, "the live hub doesn't take test traffic; use the fake hub (make fake-hub)")

    def refuse_test_source(*names: str) -> None:
        if live_hub and any(testdata.is_test_source(n) for n in names):
            raise HTTPException(403, "the live hub doesn't take test traffic; use the fake hub (make fake-hub)")

    api = [Depends(auth), Depends(no_test_traffic)]

    @app.get("/health")
    def health():
        st = s.budget.status()
        return {"ok": True, "version": VERSION, "time": time.time(), "budget": st.state,
                "laya": "up" if (s.laya and s.laya.healthy()) else "down", **s.mode}

    voice_api.register(app, s, api)  # mic button, texts to speak, voice enrollment
    pause_api.register(app, s, api)  # Pause Maxie: no listening, watching or work of his own
    decisions_api.register(app, s, api)  # Settings › Speed › Deciders
    maxie_api.register(app, s, api)  # Settings › Maxie: look, character, personality, voice
    presence_api.register(app, s, api)  # the Mac and the phone say whether he is up
    watch_api.register(app, s, api)  # watch and learn: eyes, habits, routines
    background_api.register(app, s, api)  # background tasks, the screen when he's away, Scheduled

    async def with_screen(text: str, images: list[str]) -> tuple[str, list[str]]:
        """The Mac app's screenshot on the turn; without one, Maxie still answers and says why."""
        try:
            return text, [*images, await asyncio.to_thread(s.agent.apple.screenshot)]
        except AppleUnavailable as e:
            return f"{text}\n\n[Maxie couldn't take a screenshot: {e.message()} Say so in one short line.]", images

    @app.post("/api/chat", dependencies=api)
    async def chat(body: ChatIn):
        refuse_test_source(body.conversation, body.device)
        night = goodnight_said(body.text)
        if not night:
            s.presence.heard(body.text)  # talking to Maxie: he's up

        async def stream():
            text, images = body.text, list(body.images)
            if body.screen:
                text, images = await with_screen(text, images)
            try:
                async for ev in s.agent.handle(text, body.conversation, body.device, images=images, files=body.files):
                    yield json.dumps(ev, default=str) + "\n"
            except Exception as e:  # tell the client instead of dropping the stream  # noqa: BLE001
                s.audit.log("hub", "chat_error", {}, body.text[:100], str(e))
                yield json.dumps({"type": "error", "text": "Something broke on the hub. It's in the audit log."}) + "\n"
            finally:
                if night:
                    s.presence.heard(body.text)  # "goodnight Max": he answers, then his eyes close
        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.get("/api/events", dependencies=api)
    async def events(once: bool = False):
        """Server-sent events: mascot state + transcript + steps + confirmations. Heartbeat every 15 s."""
        return StreamingResponse(sse(s.events, once=once), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/events/state", dependencies=api)
    def set_state(body: StateIn):
        """For clients that know first: the phone's hold-to-talk and the Mac's wake word say "listen"."""
        try:
            s.events.set_state(body.state)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return s.events.current()

    @app.post("/api/shortcut", dependencies=api)
    async def shortcut(body: ChatIn):
        """For the iOS Shortcut: one request, one plain reply to speak, plus any phone action."""
        reply, confirms, phone_action = "", [], None
        async for ev in s.agent.handle(body.text, body.conversation, "phone"):
            if ev["type"] == "done":
                reply = ev["text"]
            elif ev["type"] == "confirm":
                confirms.append(ev)
            elif ev["type"] == "phone_action":
                phone_action = ev
        if confirms:
            reply += " It's waiting for your OK in the Maxie app."
        return {"reply": reply, "confirmations": confirms, "phone_action": phone_action}

    @app.post("/api/ask", dependencies=api)
    async def ask(body: AskIn):
        """The iPhone's Action Button / "Hey Max" Shortcut: text in, a short spoken answer out.
        `reply` is what the phone speaks (ends with "More on screen." when the full answer is longer);
        `full` is the whole answer, also in the PWA's history. Direct actions and quick answers take the
        fast path (rules, then Haiku) like every other turn."""
        refuse_test_source(body.conversation)
        t0 = time.perf_counter()
        full, first_ms, confirms, phone_action = "", None, [], None
        async for ev in s.agent.handle(body.text, body.conversation, "phone"):
            if ev["type"] in ("text", "ack") and first_ms is None and ev.get("text", "").strip():
                first_ms = round((time.perf_counter() - t0) * 1000)
            if ev["type"] == "done":
                full = ev["text"]
            elif ev["type"] == "confirm":
                confirms.append(ev)
            elif ev["type"] == "phone_action":
                phone_action = ev
        if confirms:
            full += " It's waiting for your OK in the Maxie app."
        say, more = spoken(full)
        s.db.execute("INSERT OR REPLACE INTO hub_meta VALUES ('phone_last_ask', ?)", (str(time.time()),))
        return {"reply": say, "full": full, "more_on_screen": more, "confirmations": confirms,
                "phone_action": phone_action, "first_ms": first_ms, "total_ms": round((time.perf_counter() - t0) * 1000)}

    @app.get("/api/history", dependencies=api)
    def history(conversation: str | None = None, limit: int = 100):
        return s.conversations.recent(conversation, limit)

    @app.get("/api/confirmations", dependencies=api)
    def confirmations():
        """Cards waiting for him, then screen tasks set to run when he steps away."""
        c = s.registry.confirmations
        return [asdict(x) for x in c.pending() + c.waiting_away()]

    @app.post("/api/confirm/{cid}", dependencies=api)
    def confirm(cid: str, body: ConfirmIn):
        r = s.registry.resolve(cid, body.approve)
        if r.is_error:
            raise HTTPException(409, r.content)
        return {"ok": True, "result": r.content}

    @app.post("/api/stop", dependencies=api)
    def stop():
        s.kill.stop()
        s.audit.log("user", "stop", {}, "kill switch", "stopped")
        return {"ok": True}

    @app.get("/api/memory", dependencies=api)
    def memory_get():
        return {"markdown": s.memory.markdown(), "facts": s.memory.all(), "topics": list(TOPICS)}

    @app.post("/api/testdata/clear", dependencies=api)
    def testdata_clear():
        """Settings › Backups › Clear test data: what tests and evals left in this database."""
        removed = testdata.clear(s.db)
        total = sum(removed.values())
        s.audit.log("user", "clear_test_data", removed, "Settings › Backups", f"{total} rows")
        return {"ok": True, "removed": removed, "total": total}

    # ---- watch me once (maxie.flows) ----
    @app.get("/api/flows", dependencies=api)
    def flows_list():
        return {"flows": [f.dict() for f in s.flows.all()]}

    @app.post("/api/flows", dependencies=api)
    def flows_save(body: FlowIn):
        """The Mac app saves what it watched, under the name he gave it."""
        try:
            f = s.flows.save(body.name, body.steps)
        except FlowError as e:
            raise HTTPException(400, str(e)) from e
        s.audit.log("user", "save_flow", {"name": f.name, "steps": len(f.steps)}, "watch me once", "ok")
        return f.dict()

    @app.delete("/api/flows/{flow_id}", dependencies=api)
    def flows_forget(flow_id: int):
        if not s.flows.forget(flow_id):
            raise HTTPException(404, "no such flow")
        s.audit.log("user", "forget_flow", {"id": flow_id}, "Settings › Flows", "ok")
        return {"ok": True}

    @app.post("/api/flows/{flow_id}/run", dependencies=api)
    def flows_run(flow_id: int):
        """Run pressed in Settings › Flows: his click is the approval."""
        f = s.flows.get(flow_id)
        if f is None:
            raise HTTPException(404, "no such flow")
        s.audit.log("user", "run_flow", {"name": f.name}, "Settings › Flows", "started")
        return {"run": s.flows.start(f)}

    @app.post("/api/flows/runs/{run}", dependencies=api)
    def flows_result(run: str, body: FlowResultIn):
        """The Mac app reports how a replay went."""
        try:
            return s.flows.finish(run, body.ok, body.done, body.error).dict()
        except FlowError as e:
            raise HTTPException(404, str(e)) from e

    @app.get("/api/memory/search", dependencies=api)
    def memory_search(q: str):
        return {"facts": s.memory.search(q), "line": s.memory.recall_line(q, s.settings.timezone)}

    @app.put("/api/memory/{fact_id}", dependencies=api)
    def memory_update(fact_id: int, body: FactIn):
        ok, msg = s.memory.update(fact_id, body.text, body.topic)
        if not ok:
            raise HTTPException(404 if msg == "no such fact" else 400, msg)
        s.audit.log("user", "memory_edit", {"id": fact_id}, "Memory page", "updated")
        return {"ok": True, "facts": s.memory.all()}

    @app.put("/api/memory", dependencies=api)
    def memory_put(body: MemoryIn):
        refused = s.memory.replace_from_markdown(body.markdown)
        return {"ok": True, "refused": refused, "markdown": s.memory.markdown()}

    @app.delete("/api/memory/{fact_id}", dependencies=api)
    def memory_delete(fact_id: int):
        s.memory.delete(fact_id)
        s.audit.log("user", "memory_forget", {"id": fact_id}, "Memory page", "forgotten")
        return {"ok": True}

    @app.get("/api/budget", dependencies=api)
    def budget():
        st = s.budget.status()
        line = st.line()
        if guessed := [m for m in s.models.all_active().values() if price_guessed(m)]:
            line += f" (new model, price guessed: {', '.join(pretty(m) for m in guessed)})"
        return {**asdict(st), "line": line}

    settings_api.register(app, s, api, deps)
    signin.register(app, s, api, sessions, tokens, passcode, clock, demo=demo,
                    mac_keychain=d.get("mac_keychain", mac_app_token if sys.platform == "darwin" else None))
    setup_api.register(app, s, api, deps)  # Settings: status panel, keys, Test buttons, Connect Google  # /signin.html for the PWA and other devices

    @app.get("/api/backup", dependencies=api)
    def backup_get():
        return backup_status(s.backup, s.proactive.tz)

    @app.post("/api/backup", dependencies=api)
    async def backup_now(verify: bool = False):
        name = await asyncio.to_thread(s.backup.run, "from the panel", True)
        ok = (await asyncio.to_thread(s.backup.verify_latest)) if verify else True
        return {"name": name, "verified": ok, **backup_status(s.backup, s.proactive.tz)}

    @app.get("/api/models", dependencies=api)
    def models_get():
        active = s.models.all_active()
        return {"active": active, "pinned_fallback": PINNED, "auto_upgrade": s.settings.auto_upgrade_models,
                "pins": s.settings.model_pins, "lines": s.models.lines(),
                "price_guessed": {t: price_guessed(m) for t, m in active.items()},
                "last_checked": s.models.last_checked()}

    @app.post("/api/models/check", dependencies=api)
    def models_check_now():
        if s.anthropic_sync is None:
            raise HTTPException(409, "no Anthropic API key yet (Settings)")
        return {"changes": models_check(), "active": s.models.all_active()}

    @app.post("/api/models/pin", dependencies=api)
    def models_pin():
        """Pin current: today's ids stay until you turn auto-upgrade back on."""
        pins = s.models.pin_current()
        s.settings.update({"model_pins": pins, "auto_upgrade_models": False})
        s.settings.save()
        return {"active": s.models.all_active(), "pins": pins, "auto_upgrade": False}

    @app.get("/api/settings", dependencies=api)
    def settings_get():
        return {**asdict(s.settings), "mode": s.mode}

    @app.put("/api/settings", dependencies=api)
    def settings_put(changes: dict):
        try:
            s.settings.update(changes)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        s.settings.save()
        if "laya_url" in changes and not s.mode.get("demo"):  # the router picks the new address up at once
            if s.settings.laya_url:
                from ..router.laya import LayaClient
                s.laya = LayaClient(s.settings.laya_url, secret("LAYA_API_KEY"))
            else:
                s.laya = None
            s.router.laya = s.laya
        s.budget.cap_usd = s.settings.monthly_budget_usd
        s.budget.warn_ratio = s.settings.budget_warn_ratio
        s.models.auto_upgrade = s.settings.auto_upgrade_models
        s.models.pins = dict(s.settings.model_pins) if not s.settings.auto_upgrade_models else {}
        if s.settings.auto_upgrade_models and s.settings.model_pins:
            s.settings.model_pins = {}  # turning auto back on drops the pins
            s.settings.save()
        return asdict(s.settings)

    @app.get("/api/tasks", dependencies=api)
    def tasks_list(limit: int = 30):
        """Activity: every task as a timeline (steps, result, cost, status); the running one first."""
        cur = s.tasks.current
        return {"current": cur.dict() if cur else None, "tasks": s.tasks.recent(limit), "paused": s.kill.paused}

    @app.post("/api/tasks/{task_id}/{verb}", dependencies=api)
    def tasks_control(task_id: int, verb: str):
        """pause / resume hold the running task between steps; cancel stops it (same as the kill switch)."""
        cur = s.tasks.current
        if verb not in ("pause", "resume", "cancel"):
            raise HTTPException(400, "verb must be pause, resume or cancel")
        if cur is None or cur.id != task_id:
            raise HTTPException(409, "that task isn't running")
        if verb == "pause":
            s.kill.pause()
            s.tasks.set_status("paused")
        elif verb == "resume":
            s.kill.resume()
            s.tasks.set_status("running")
        else:
            s.kill.stop()
        s.audit.log("user", f"task_{verb}", {"task": task_id}, "Activity", verb)
        return {"ok": True, "status": s.tasks.current.status if s.tasks.current else "stopped"}

    @app.get("/api/rules", dependencies=api)
    def rules_get():
        return {"rules": s.rules.table(s.registry), "choices": ["allow", "ask", "block"]}

    @app.put("/api/rules", dependencies=api)
    def rules_put(body: RuleIn):
        tool = s.registry.get(body.name)
        if tool is None:
            raise HTTPException(404, f"no tool called {body.name}")
        try:
            s.rules.set(body.name, body.rule, tool.kind)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "rules": s.rules.table(s.registry)}

    @app.get("/api/experiments/warm_sonnet", dependencies=api)
    def warm_sonnet_experiment():
        """where the 7-days-off / 7-days-on run is, the numbers per waking day, and the verdict so far."""
        e = s.experiment
        return {"status": e.status(), "days": e.days(), "verdict": e.verdict(), "markdown": e.markdown()}

    @app.get("/api/latency", dependencies=api)
    def latency():
        """Speed, measured on every turn: today and the last 7 days per kind, targets, the last turns."""
        return {"today": s.latency.summary(1), "week": s.latency.summary(7), "targets": TARGET_MS,
                "recent": s.latency.recent(20), "line": s.latency.line(), "share": s.latency.share(7)}

    @app.get("/api/audit", dependencies=api)
    def audit(limit: int = 100):
        return s.audit.recent(limit)

    @app.get("/api/brief", dependencies=api)
    def brief(kind: str = "morning"):
        """The panel shows the sections; voice and push use `text`."""
        if kind == "morning":
            return s.proactive.brief().dict()
        return {"title": "Today's wrap", "date": "", "text": s.proactive.build(kind), "sections": []}

    @app.post("/api/connections/jobhunt/link", dependencies=api)
    def link_jobhunt():
        """"jobhunt not linked · Link": read jobhunt's (and resumegen's) token from the files set in Settings,
        keep them in the secret store and use them right away. Tokens are never logged or sent back."""
        jobs = s.fakes["jobs"]
        if not hasattr(jobs, "set_tokens"):
            raise HTTPException(409, "the job search apps are off or faked on this hub")
        found = {}
        for name, path in s.token_files.items():
            p = Path(path).expanduser() if path else None
            if p and p.is_file() and (value := p.read_text().strip()):
                found[name] = value
        if "JOBHUNT_TOKEN" not in found:
            raise HTTPException(404, "jobhunt's token file isn't there. Set its path in Settings, or paste the token.")
        try:
            for name, value in found.items():
                s.store_secret(name, value)
        except (ValueError, RuntimeError) as e:
            raise HTTPException(500, f"Couldn't save it: {e}") from e
        jobs.set_tokens(found.get("JOBHUNT_TOKEN"), found.get("RESUMEGEN_TOKEN"))
        jobs.new_jobs()  # one call to see whether jobhunt takes it
        ok = not jobs.error
        linked = sorted(n.split("_")[0].lower() for n in found)
        s.audit.log("user", "link_jobhunt", {"linked": linked}, "panel", "ok" if ok else "token turned away")
        return {"ok": ok, "linked": linked,
                "line": "jobhunt linked." if ok else "jobhunt turned the token away. Is it the right jobhunt?"}

    @app.get("/api/push/key", dependencies=api)
    def push_key():
        return {"key": s.push.public_key}

    @app.post("/api/push/test", dependencies=api)
    def push_test():
        """Settings › Connections: one test notification to every subscribed phone."""
        if not s.push.enabled:
            raise HTTPException(409, "push keys aren't set (VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY); the hub makes them on first start")
        subs = s.push.subscriptions()
        if not subs:
            raise HTTPException(409, "no phone has turned on notifications yet")
        s.push.push("Maxie", "Test from your hub. Notifications work.", tag="test", mood="happy")
        s.audit.log("hub", "push_test", {"devices": len(subs)}, "from Settings", "sent")
        return {"ok": True, "devices": len(subs)}

    @app.post("/api/push/subscribe", dependencies=api)
    def push_subscribe(sub: dict):
        try:
            s.push.subscribe(sub)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    @app.get("/api/routes", dependencies=api)
    def routes(limit: int = 50):
        return {"routes": ROUTES, "recent": s.router.recent(limit), "calibration": asdict(s.router.calibration)}

    @app.post("/api/routes/label", dependencies=api)
    def label(body: LabelIn):
        try:
            s.router.label(body.id, body.route)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    @app.post("/api/routes/recalibrate", dependencies=api)
    def recalibrate_now():
        return recalibrate()

    if WEB.exists():
        app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
    return app


def mac_app_token(raw: str) -> None:
    """The Mac app and the hub share HUB_TOKEN: the notch app reads it from the login Keychain, the hub from
    secrets.json (it also signs the hub's calls to the app's local bridge). The value goes in over stdin."""
    store_secret("HUB_TOKEN", raw)
    out = subprocess.run(["security", "-i"], input=f'add-generic-password -U -s maxie -a HUB_TOKEN -w "{raw}"\n',
                         capture_output=True, text=True, timeout=10)
    if out.returncode != 0:
        raise RuntimeError("the Keychain refused it")


def main() -> None:
    import faulthandler
    import signal

    import uvicorn

    faulthandler.register(signal.SIGUSR1, all_threads=True)  # `kill -USR1 <hub pid>` dumps every thread's stack to hub.err.log

    fakes = os.environ.get("MAXIE_FAKES") == "1"
    token = os.environ.get("HUB_TOKEN") if fakes else None  # the demo hub's fixed token; otherwise secrets.json
    if fakes and testdata.is_live(data_dir()):
        raise SystemExit("The demo hub never uses your real data folder: run `make demo` (its own folder, port 8781).")
    services = build(fakes=fakes, demo_brain=fakes)
    host = services.settings.hub_host
    port = int(os.environ.get("HUB_PORT") or services.settings.hub_port)
    uvicorn.run(create_app(services, token), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
