"""should Maxie warm Sonnet's cache too? His idea ("we speak data"): test it, don't guess.
The flag `warm_sonnet` (off by default), the measurement (Sonnet cost per waking day, chat + warm, and the time
to the model's first word, warm vs cold first turns), and a 7-days-off / 7-days-on run that decides on its own."""

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from maxie.budget import HAIKU, SONNET, cost_usd
from maxie.hub.api import create_app
from maxie.latency import Turn
from maxie.warm_experiment import WarmExperiment, models_to_warm
from tests.conftest import TZ
from tests.fakes import FakeAnthropic, turn

TOKEN = "t" * 32
H = {"Authorization": f"Bearer {TOKEN}"}
START = datetime(2026, 10, 16, 8, 0, tzinfo=TZ)
WARM = {"input_tokens": 5, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 6000}


# ---- the bill is right first: 1-hour cache writes cost 2x input, not 1.25x


def test_hour_long_cache_writes_are_billed_at_twice_the_input_price():
    assert cost_usd(SONNET, {"cache_creation_input_tokens": 1_000_000}) == pytest.approx(4.00)
    five_min = {"cache_creation_input_tokens": 1_000_000,
                "cache_creation": {"ephemeral_5m_input_tokens": 1_000_000, "ephemeral_1h_input_tokens": 0}}
    assert cost_usd(SONNET, five_min) == pytest.approx(2.50)
    mixed = {"cache_creation_input_tokens": 2_000_000,
             "cache_creation": {"ephemeral_5m_input_tokens": 1_000_000, "ephemeral_1h_input_tokens": 1_000_000}}
    assert cost_usd(SONNET, mixed) == pytest.approx(6.50)


# ---- what is measured on every turn


def run(agen):
    async def go():
        return [ev async for ev in agen]
    return asyncio.run(go())


@pytest.mark.parametrize("usage,cache", [
    ({"input_tokens": 50, "output_tokens": 20, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 6000}, "warm"),
    ({"input_tokens": 50, "output_tokens": 20, "cache_creation_input_tokens": 6000, "cache_read_input_tokens": 0}, "cold"),
])
def test_the_brain_reports_first_model_word_and_whether_the_cache_was_warm(make_services, usage, cache):
    s = make_services(client=FakeAnthropic([turn("Here it is.", usage=usage)]))
    done = run(s.brain.run([], "draft a reply", SONNET))[-1]
    assert done["type"] == "done" and done["cache"] == cache
    assert done["first_model_ms"] is not None and done["first_model_ms"] >= 0


def test_turns_keep_the_model_first_word_and_cache_state(make_services):
    s = make_services(client=FakeAnthropic([turn("Paris.")]))
    run(s.agent.handle("what's the capital of France?"))
    row = s.latency.recent(1)[0]
    assert row["model"] and row["model_first_ms"] is not None and row["cache"] == "warm"


# ---- which models are kept warm


def test_only_haiku_is_warmed_unless_the_flag_is_on(make_services):
    s = make_services()
    assert models_to_warm(s.settings, s.models) == [s.models.active("haiku")]
    s.settings.warm_sonnet = True
    assert models_to_warm(s.settings, s.models) == [s.models.active("haiku"), s.models.active("sonnet")]


def test_the_flag_is_a_setting_he_can_flip(make_services):
    s = make_services()
    with TestClient(create_app(s, TOKEN, scheduler=False)) as cl:
        assert cl.put("/api/settings", json={"warm_sonnet": "yes"}, headers=H).status_code == 400
        assert cl.put("/api/settings", json={"warm_sonnet": True}, headers=H).status_code == 200
    assert s.settings.warm_sonnet is True


# ---- the two weeks


def day(n: int, h: int = 10, m: int = 0) -> float:
    return (START + timedelta(days=n)).replace(hour=h, minute=m).timestamp()


def use(s, clock, n, chat_usd, firsts, warm_usd=0.0, cache="cold"):
    """One waking day: Sonnet chat spend, Sonnet first turns (each > 1 h apart), and the day's warm-ups."""
    for i, ms in enumerate(firsts):
        clock.t = day(n, 9 + 2 * i)
        s.latency.record(Turn("heavy", "draft", "multi_step", "laya", SONNET, 1, 400, ms + 800, 0.0,
                              model_first_ms=ms, cache=cache))
    clock.t = day(n, 20)
    s.db.execute("INSERT INTO usage VALUES (?,?,?,?,?,?,?,?)", (clock.t, SONNET, "chat", 0, 0, 0, 0, chat_usd))
    if warm_usd:
        s.db.execute("INSERT INTO usage VALUES (?,?,?,?,?,?,?,?)", (clock.t, SONNET, "warm", 0, 0, 0, 6000, warm_usd))
    s.db.execute("INSERT INTO usage VALUES (?,?,?,?,?,?,?,?)", (clock.t, HAIKU, "warm", 0, 0, 0, 6000, 0.001))


def experiment(s, clock, notes=None):
    clock.set(START)
    e = WarmExperiment(s.db, s.settings, notify=(notes.append if notes is not None else None), clock=clock)
    e.tick()
    return e


def test_it_starts_off_turns_on_after_a_week_and_decides_after_two(make_services, clock):
    s = make_services()
    s.settings.warm_sonnet = True
    notes = []
    e = experiment(s, clock, notes)
    assert e.status()["phase"] == "off" and s.settings.warm_sonnet is False, "week one: cold, as today"
    for n in range(7):
        use(s, clock, n, 0.30, [2400, 2600])
    clock.t = day(7, 0, 5)
    e.tick()
    assert e.status()["phase"] == "on" and s.settings.warm_sonnet is True
    for n in range(7, 14):
        use(s, clock, n, 0.10, [900, 1000], warm_usd=0.30, cache="warm")
    clock.t = day(14, 0, 5)
    e.tick()
    st = e.status()
    assert st["phase"] == "done" and s.settings.warm_sonnet is False, "warming cost more: back off"
    assert st["verdict"].startswith("Keep Sonnet cold: $0.30 a day cold vs $0.40 warm")
    assert notes and notes[0] == st["verdict"]
    e.tick()
    assert e.status()["phase"] == "done", "it runs once"


def test_when_cost_is_within_ten_percent_the_faster_one_wins(make_services, clock):
    s = make_services()
    e = experiment(s, clock)
    for n in range(7):
        use(s, clock, n, 0.40, [2400, 2600])
    for n in range(7, 14):
        use(s, clock, n, 0.10, [900, 1000], warm_usd=0.32, cache="warm")
    v = e.verdict()
    assert v["off"]["usd_per_day"] == pytest.approx(0.40) and v["on"]["usd_per_day"] == pytest.approx(0.42)
    assert v["off"]["first_p50"] == 2500 and v["on"]["first_p50"] == 950
    assert v["keep_on"] is True and v["line"].startswith("Keep Sonnet warm")


def test_days_are_sorted_by_what_really_happened_not_the_calendar(make_services, clock):
    """If he flips the flag by hand mid-run, a day counts as warm only if Sonnet was really warmed that day."""
    s = make_services()
    e = experiment(s, clock)
    use(s, clock, 0, 0.30, [2400])
    use(s, clock, 1, 0.10, [900], warm_usd=0.30, cache="warm")
    rows = e.days()
    assert [(r["warm"], r["sonnet_usd"]) for r in rows] == [(False, 0.30), (True, 0.40)]
    assert rows[1]["first_turns"] == 1 and rows[1]["first_p50"] == 900


def test_too_little_use_keeps_it_off_and_says_so(make_services, clock):
    s = make_services()
    e = experiment(s, clock)
    use(s, clock, 0, 0.30, [2400])
    v = e.verdict()
    assert v["keep_on"] is False and v["line"].startswith("Not enough use to tell")


def test_the_report_and_the_api_show_the_numbers(make_services, clock):
    s = make_services()
    e = experiment(s, clock)
    use(s, clock, 0, 0.30, [2400, 2600])
    md = e.markdown()
    assert "| Day | Warm | Sonnet $ | of it warm-up $ | Sonnet turns | First turns | First word p50 |" in md
    assert "| Fri Oct 16 | no | 0.300 | 0.000 | 2 | 2 | 2500 ms |" in md
    with TestClient(create_app(s, TOKEN, scheduler=False)) as cl:
        r = cl.get("/api/experiments/warm_sonnet", headers=H).json()
    assert r["status"]["phase"] == "off" and r["status"]["day"] == 1 and r["days"][0]["sonnet_usd"] == 0.3


def test_the_cli_prints_the_report_from_a_hub_database(tmp_path):
    from maxie.cli import warm_report
    out = warm_report(tmp_path / "hub.db")
    assert out.startswith("Warm Sonnet experiment: not started") and "| Day | Warm |" in out
