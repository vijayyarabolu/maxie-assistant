"""Build the whole system from Settings. `fakes=True` swaps every outside service for a fake."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .. import flows as flow_mod
from .. import mood, testdata
from ..agent import Agent, Conversations
from ..background import Background
from ..backup import Backup, default_dirs, default_sources
from ..brain import Brain, context_line
from ..budget import Budget
from ..config import Settings, data_dir, delete_secret, secret, store_secret
from ..db import DB
from ..events import Events
from ..hands import apple, claude_code, google, google_real, jobs, mac, sessions, shortcuts, weather
from ..hands.registry import Registry, Tool, schema
from ..latency import Latency
from ..memory import Memory
from ..models import Models
from ..personality import Wellbeing
from ..presence import Presence, follow
from ..proactive import FakeNotifier, Proactive, Wins
from ..push import WebPush
from ..router import Router
from ..router.haiku import HaikuRouter
from ..router.laya import LayaClient
from ..rules import Rules
from ..safety import AuditLog, Confirmations, KillSwitch
from ..tasks import Tasks
from ..warm_experiment import WarmExperiment
from ..watch import Watch


@dataclass
class Services:
    settings: Settings
    db: DB
    budget: Budget
    audit: AuditLog
    registry: Registry
    memory: Memory
    router: Router
    brain: Brain
    agent: Agent
    proactive: Proactive
    push: WebPush
    kill: KillSwitch
    conversations: Conversations
    laya: LayaClient | None
    events: Events
    models: Models
    backup: Backup
    mode: dict  # which parts are real vs fake, shown in /health and Settings
    fakes: dict
    anthropic_sync: object = None  # sync SDK client, for the daily models check
    latency: Latency | None = None
    tasks: Tasks | None = None
    rules: Rules | None = None
    flows: flow_mod.Flows | None = None
    background: Background | None = None
    experiment: WarmExperiment | None = None


def assert_real(**sources) -> None:
    """The live hub never runs on fixture data: no Fake* source, ever."""
    fake = [name for name, obj in sources.items() if type(obj).__name__.startswith("Fake")]
    if fake:
        raise RuntimeError(f"fake {', '.join(fake)} on the live hub")


def build(settings: Settings | None = None, db_path: Path | str | None = None, fakes: bool = False,
          anthropic_client=None, anthropic_sync=None, laya: LayaClient | None = None, notifier=None,
          clock=None, backup_dirs: list | None = None, demo_brain: bool = False) -> Services:
    settings = settings or Settings.load()
    db = DB(db_path if db_path is not None else data_dir() / "hub.db")
    kw = {"clock": clock} if clock else {}
    # one clock for every tool: the fake one in tests, the wall clock on the Mac
    now_dt = (lambda: datetime.fromtimestamp(clock())) if clock else datetime.now
    budget = Budget(db, settings.monthly_budget_usd, settings.budget_warn_ratio, settings.timezone, **kw)
    audit, confirmations, kill = AuditLog(db, **kw), Confirmations(db, **kw), KillSwitch()
    rules = Rules(settings, audit)
    registry = Registry(audit, confirmations, rules=rules.for_tool)
    memory = Memory(db, **kw)
    models = Models(db, pins=settings.model_pins, auto_upgrade=settings.auto_upgrade_models, **kw)
    backup = Backup(backup_dirs or default_dirs(), default_sources(data_dir()), secret("BACKUP_PASSPHRASE"),
                    log=lambda m: audit.log("hub", "backup", {}, "", m), **kw)

    if fakes and db_path is None and testdata.is_live(data_dir()):
        raise RuntimeError("fixture data never runs on the live data folder; set MAXIE_DATA to a test folder")
    the_mac = mac.FakeMac() if fakes else mac.default_mac()
    # Fixture mail and calendar only for the demo hub. On the live hub Google is whatever Settings connected,
    # and with nothing connected it says so.
    g = google.FakeGoogle() if fakes else google.NotConnected()
    live_google = None if fakes else google_real.LiveGoogle(secret, store_secret)
    if fakes:
        the_jobs = jobs.FakeJobs()
    elif settings.jobhunt_url:
        the_jobs = jobs.JobsHTTP(settings.jobhunt_url, settings.resumegen_url or settings.jobhunt_url,
                                 jobhunt_token=secret("JOBHUNT_TOKEN"), resumegen_token=secret("RESUMEGEN_TOKEN"))
    else:
        the_jobs = jobs.JobsOff()
    mac.register(registry, the_mac, settings.allowed_folders)
    # Apple data through Maxie.app's local bridge; the fake hub gets a fake.
    the_apple = apple.FakeApple() if fakes else apple.BridgeHTTP(secret("HUB_TOKEN") or "")
    apple.register(registry, the_apple, settings.timezone)
    # His "Maxie …" shortcuts (Private Cloud Compute via Use Model); the fake hub never runs a real one.
    shortcuts.register(registry, shortcuts.Shortcuts(run=_no_shortcuts) if fakes else shortcuts.Shortcuts())
    registry.add(Tool("clock", "The current time, date, timezone, the user's city and work hours.", schema({}),
                      lambda a: context_line(settings, **kw), "read"))
    registry.add(Tool("api_spend", "Claude API spend today and this month, and the monthly cap.", schema({}),
                      lambda a: budget.status().line(), "read"))
    if live_google:
        mail, cal, drive = live_google, live_google, live_google.drive()
    else:
        mail, cal, drive = g, g, google.FakeDrive(g)
    google.register(registry, mail, cal, drive, now=now_dt)
    if not fakes:
        assert_real(mail=mail, calendar=cal, drive=drive, jobs=the_jobs, mac=the_mac)
    jobs.register(registry, the_jobs)
    flows = flow_mod.Flows(db, **kw)
    flows.audit = audit
    flow_mod.register(registry, flows)
    the_weather = weather.FakeWeather() if fakes else weather.Weather(settings=settings)
    weather.register(registry, the_weather)
    sessions.register(registry)
    claude_code.register(registry, settings.allowed_folders, models=models,
                         runner=(lambda task, folder, model: f"(fake) Claude Code: {task}") if fakes else claude_code.spawn)

    key = secret("ANTHROPIC_API_KEY")
    if anthropic_client is None and fakes and demo_brain:
        from ..demo import DemoAnthropic
        anthropic_client = DemoAnthropic()  # `make demo`: scripted answers, no key, no network
    if anthropic_client is None and key and not fakes:
        import anthropic
        anthropic_client, anthropic_sync = anthropic.AsyncAnthropic(api_key=key), anthropic.Anthropic(api_key=key)
    if laya is None and not fakes and settings.laya_url:
        laya = LayaClient(settings.laya_url, secret("LAYA_API_KEY"))
    calibration = data_dir() / "router_calibration.json" if db_path is None else None
    router = Router(db, laya=laya, haiku=HaikuRouter(anthropic_sync, budget, models) if anthropic_sync else None,
                    calibration_path=calibration, models=models)

    brain = Brain(anthropic_client, registry, budget, memory, kill, models=models,
                  now_line=lambda: context_line(settings, **kw))
    conversations = Conversations(db, **kw)
    if not fakes and db_path is None and not secret("VAPID_PRIVATE_KEY"):
        _make_push_keys()  # notifications work on first run; the keys never leave this computer
    push = WebPush(db, secret("VAPID_PRIVATE_KEY"), secret("VAPID_PUBLIC_KEY"),
                   secret("VAPID_CONTACT") or "mailto:admin@localhost")
    notifier = notifier or (FakeNotifier() if fakes else push)
    proactive = Proactive(db, settings, mail, cal, the_jobs, budget, Wins(db, **kw), notifier, models=models, **kw)
    proactive.sessions = sessions.Watcher(proactive.notify if hasattr(proactive, "notify") else notifier.push, **kw)
    presence = Presence(db, settings, fallback_quiet=proactive.quiet, **kw)  # asleep when you sleep
    proactive.presence = presence
    events = Events(quiet=presence.asleep, **kw)
    follow(presence, events, proactive)
    watch = Watch(db, settings, memory, events, asleep=presence.asleep, recording=lambda: flows.recording, **kw)  # Phase 4
    proactive.watch = watch
    proactive.events = events
    flows.events = events
    latency = Latency(db, settings.timezone, **kw)
    tasks = Tasks(db, events, **kw)
    agent = Agent(router, brain, registry, memory, kill, conversations, Wellbeing(db, **kw), settings, proactive,
                  events, latency=latency, tasks=tasks, **kw)
    moments = mood.Moments(events, quiet=presence.asleep, enabled=lambda: settings.bot_moments and settings.chattiness != "quiet",
                           **kw)
    agent.moments = proactive.moments = moments
    agent.apple = the_apple
    mode = {"demo": fakes, "system": sys.platform,
            "mac": type(the_mac).__name__,
            "google": "fake" if fakes else ("real: " + ", ".join(live_google.labels()) if live_google.labels() else "not connected"),
            "jobs": type(the_jobs).__name__,
            "brain": ("demo script" if demo_brain and fakes else "on") if anthropic_client else "no API key", "push": "on" if push.enabled else "no VAPID keys"}
    services = Services(settings, db, budget, audit, registry, memory, router, brain, agent, proactive, push, kill,
                        conversations, laya, events, models, backup, mode,
                        {"mac": the_mac, "google": g, "jobs": the_jobs, "notifier": notifier, "apple": the_apple})
    services.anthropic_sync = anthropic_sync
    services.latency = latency
    services.presence = presence
    services.watch = watch
    agent.watch = watch
    services.tasks = tasks
    services.rules = rules
    # queued tasks, the screen when you're away, the Scheduled list
    registry.screen_when_away = lambda: settings.screen_when_away
    background = Background(db, events, notify=lambda title, body: proactive.deliver(title, body, "background"),
                            registry=registry, presence=presence, settings=settings, watch=watch, audit=audit, **kw)
    agent.background = services.background = background
    experiment = WarmExperiment(db, settings, notify=lambda line: proactive.deliver("Sonnet warm-up test", line, "experiment"),
                                audit=audit, **kw)  # runs on its own from the minute tick
    proactive.experiment = services.experiment = experiment
    services.flows = flows
    services.store_secret = store_secret
    services.delete_secret = delete_secret
    services.google = live_google
    services.mac = the_mac
    services.weather = the_weather
    services.token_files = {"JOBHUNT_TOKEN": settings.jobhunt_token_file, "RESUMEGEN_TOKEN": settings.resumegen_token_file}
    services.speaker_store = data_dir() / "voice" / "speaker.npz"   # your voice prints (no audio), Settings › Voice
    return services


def _make_push_keys() -> None:
    from ..push import new_keys
    private, public = new_keys()
    store_secret("VAPID_PRIVATE_KEY", private)
    store_secret("VAPID_PUBLIC_KEY", public)


def _no_shortcuts(args, **kw) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args, 0, "", "")
