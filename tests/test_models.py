"""Model auto-upgrade: newest stable per tier, smoke-checked, stored in the DB, 404 fallback."""

from datetime import datetime
from types import SimpleNamespace as NS

from fastapi.testclient import TestClient

from maxie.budget import HAIKU, OPUS, SONNET, price_guessed
from maxie.hands.registry import TurnContext
from maxie.hub.api import create_app
from maxie.models import PINNED, ModelInfo, Models, pretty
from maxie.router.haiku import HaikuRouter
from tests.conftest import TZ
from tests.fakes import FakeAnthropic, turn

TOKEN = "t"
H = {"Authorization": f"Bearer {TOKEN}"}


class Api:
    """TestClient with the token on every call."""

    def __init__(self, services):
        self.c = TestClient(create_app(services, TOKEN, scheduler=False))

    def get(self, path):
        return self.c.get(path, headers=H)

    def post(self, path, **kw):
        return self.c.post(path, headers=H, **kw)

    def put(self, path, **kw):
        return self.c.put(path, headers=H, **kw)

T0 = datetime(2026, 10, 15, 8, 0, tzinfo=TZ).timestamp()
TODAY = [ModelInfo(HAIKU, T0 - 90 * 86400), ModelInfo(SONNET, T0 - 90 * 86400), ModelInfo(OPUS, T0 - 90 * 86400)]


def ok_smoke(tier, model_id):
    return None


class NotFound(Exception):
    status_code = 404


async def collect(agen):
    return [e async for e in agen]


def test_pretty_names():
    assert pretty("claude-sonnet-5-5") == "Sonnet 5.5"
    assert pretty("claude-opus-6") == "Opus 6"
    assert pretty("claude-haiku-5-5-20260901") == "Haiku 5.5 (20260901)"


def test_defaults_are_the_pinned_ids(make_services):
    s = make_services()
    assert s.models.all_active() == PINNED
    assert s.router.route("plan my week").model == SONNET
    assert s.router.route("what's 2+2, think hard").model == OPUS


def test_newer_sonnet_switches_after_smoke(make_services):
    s = make_services()
    listed = [*TODAY, ModelInfo("claude-sonnet-6", T0 - 1)]
    smoked = []
    changes = s.models.check(lambda: listed, lambda t, m: smoked.append((t, m)))
    assert smoked == [("sonnet", "claude-sonnet-6")]
    assert [(u.tier, u.old, u.new, u.ok) for u in changes] == [("sonnet", SONNET, "claude-sonnet-6", True)]
    assert s.models.active("sonnet") == "claude-sonnet-6" and s.models.active("haiku") == HAIKU
    assert s.router.route("plan my week").model == "claude-sonnet-6"  # the router reads the DB
    assert s.models.lines() == ["Upgraded Sonnet 5.5 → Sonnet 6"]
    assert price_guessed("claude-sonnet-6")
    # a fresh Models on the same DB sees it too: stored, not hardcoded
    assert Models(s.db).active("sonnet") == "claude-sonnet-6"


def test_preview_beta_mythos_fable_are_ignored(make_services):
    s = make_services()
    listed = [*TODAY, ModelInfo("claude-sonnet-6-preview", T0), ModelInfo("claude-opus-6-beta", T0),
              ModelInfo("claude-mythos-6", T0), ModelInfo("claude-fable-6", T0), ModelInfo("claude-haiku-latest", T0)]
    assert s.models.check(lambda: listed, ok_smoke) == []
    assert s.models.all_active() == PINNED


def test_smoke_failure_keeps_the_old_model(make_services):
    s = make_services()
    listed = [*TODAY, ModelInfo("claude-haiku-6", T0)]
    changes = s.models.check(lambda: listed, lambda t, m: "router eval 40% < 60%")
    assert [(u.new, u.ok) for u in changes] == [("claude-haiku-6", False)]
    assert s.models.active("haiku") == HAIKU
    assert s.models.lines() == ["Kept Haiku 5.5: Haiku 6 failed its check (router eval 40% < 60%)"]
    # not retried every day
    assert s.models.check(lambda: listed, lambda t, m: "x") == []


def test_list_failure_changes_nothing(make_services):
    s = make_services()

    def boom():
        raise OSError("offline")
    assert s.models.check(boom, ok_smoke) == []
    assert s.models.all_active() == PINNED and s.models.lines() == []


def test_pins_win_and_auto_off_skips_checks(make_services, clock):
    s = make_services()
    s.models.check(lambda: [*TODAY, ModelInfo("claude-sonnet-6", T0)], ok_smoke)
    pins = s.models.pin_current()
    assert pins["sonnet"] == "claude-sonnet-6" and not s.models.due()
    assert s.models.check(lambda: [*TODAY, ModelInfo("claude-sonnet-7", T0 + 10)], ok_smoke) == []
    assert s.models.active("sonnet") == "claude-sonnet-6"


def test_daily_cadence(make_services, clock):
    s = make_services()
    assert s.models.due()
    s.models.check(lambda: TODAY, ok_smoke)
    assert not s.models.due()
    clock.t += 25 * 3600
    assert s.models.due()


async def test_404_falls_back_and_retries_once(make_services):
    s = make_services()
    s.models.check(lambda: [*TODAY, ModelInfo("claude-sonnet-6", T0)], ok_smoke)
    client = FakeAnthropic([turn("fine now")])
    real_stream = client.beta.messages.stream
    seen = []

    def stream(**p):
        seen.append(p["model"])
        if p["model"] == "claude-sonnet-6":
            raise NotFound("model not found")
        return real_stream(**p)
    client.beta.messages.stream = stream
    s.brain.client = client
    events = await collect(s.brain.run([], "hello", s.models.active("sonnet")))
    assert seen == ["claude-sonnet-6", SONNET]
    assert events[-1]["text"] == "fine now" and events[-1]["model"] == SONNET
    assert s.models.active("sonnet") == SONNET and s.models.due()  # fell back, recheck queued
    assert "model retired" in s.models.recent_upgrades()[-1].note


def test_haiku_router_and_claude_code_use_active_ids(make_services):
    s = make_services()
    s.models.check(lambda: [*TODAY, ModelInfo("claude-haiku-6", T0), ModelInfo("claude-sonnet-6", T0)], ok_smoke)
    assert HaikuRouter(None, s.budget, s.models).model == "claude-haiku-6"
    r = s.registry.execute("delegate_to_claude_code", {"task": "fix tests", "folder": "~/Documents"},
                           TurnContext(why="t"))
    assert r.confirmation  # needs his OK; the runner receives the active sonnet when approved
    assert s.registry.resolve(r.confirmation.id, True).content.startswith("(fake) Claude Code")


def test_models_api_and_brief(make_services):
    s = make_services()
    api = Api(s)
    s.models.check(lambda: [*TODAY, ModelInfo("claude-sonnet-6", T0)], ok_smoke)
    m = api.get("/api/models").json()
    assert m["active"]["sonnet"] == "claude-sonnet-6" and m["price_guessed"]["sonnet"] and m["auto_upgrade"]
    assert m["lines"] == ["Upgraded Sonnet 5.5 → Sonnet 6"]
    assert "new model, price guessed: Sonnet 6" in api.get("/api/budget").json()["line"]
    assert "Upgraded Sonnet 5.5 → Sonnet 6." in s.proactive.morning()
    p = api.post("/api/models/pin").json()
    assert p["pins"]["sonnet"] == "claude-sonnet-6" and p["auto_upgrade"] is False
    assert api.get("/api/settings").json()["auto_upgrade_models"] is False
    api.put("/api/settings", json={"auto_upgrade_models": True})
    assert api.get("/api/models").json()["pins"] == {} and s.models.auto_upgrade


def test_smoke_with_real_shaped_client(make_services):
    from maxie.models import smoke_with_api
    s = make_services()
    calls = []

    class Sync:
        messages = NS(create=lambda **p: (calls.append(p), NS(content=[NS(type="text", text="OK")],
                                                                usage={"input_tokens": 10, "output_tokens": 1}))[1])
    smoke = smoke_with_api(Sync(), s.budget, eval_commands=None)
    assert smoke("sonnet", "claude-sonnet-6") is None
    assert calls[0]["model"] == "claude-sonnet-6" and calls[0]["max_tokens"] == 5
    assert s.budget.status().month_usd > 0  # the smoke call is counted

    class Broken:
        messages = NS(create=lambda **p: (_ for _ in ()).throw(RuntimeError("nope")))
    assert "tiny call failed" in smoke_with_api(Broken(), s.budget)("opus", "claude-opus-6")
