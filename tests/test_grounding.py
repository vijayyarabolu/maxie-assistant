"""Never make things up: every turn carries time, date, timezone and city; live facts come from tools only."""

from datetime import datetime
from zoneinfo import ZoneInfo

from maxie.brain import context_line
from maxie.config import Settings
from maxie.evals_live import CASES, judge
from maxie.hands.registry import TurnContext
from maxie.personality import SYSTEM_PROMPT
from tests.fakes import FakeAnthropic, turn


async def collect(agen):
    return [e async for e in agen]


def test_context_line_has_time_date_timezone_and_city():
    s = Settings(timezone="America/Chicago", weather_place="Springfield, IL", work_start="09:00", work_end="18:00")
    now = datetime(2026, 10, 14, 23, 30, tzinfo=ZoneInfo("America/Chicago"))
    line = context_line(s, lambda: now)
    assert line.startswith("Now: Wednesday October 14 2026, 11:30 PM CDT (America/Chicago)")
    assert "Springfield, IL" in line and "09:00" in line and "18:00" in line


async def test_every_brain_turn_starts_with_the_context_line(make_services):
    client = FakeAnthropic([turn("ok")])
    s = make_services(client)
    await collect(s.brain.run([], "hello", "claude-haiku-5-5"))
    first = client.calls[0]["messages"][-1]["content"][0]["text"]
    assert first.startswith("Now: ") and "(America/Chicago)" in first and "Springfield" in first


def test_persona_says_live_facts_need_a_tool():
    assert "I can't check that yet" in SYSTEM_PROMPT


def test_clock_and_clipboard_tools(make_services):
    s = make_services()
    ctx = TurnContext(actor="test", why="test")
    clock = s.registry.execute("clock", {}, ctx)
    assert not clock.is_error and "(America/Chicago)" in clock.content
    clip = s.registry.execute("clipboard", {}, ctx)
    assert not clip.is_error and "fake clipboard text" in clip.content  # wrapped as untrusted_data
    assert s.registry.get("clipboard").untrusted_output and s.registry.get("clipboard").kind == "read"


def test_live_facts_judge_needs_a_tool_or_an_honest_no():
    assert len(CASES) >= 20 and all(c["text"] for c in CASES)
    assert judge([{"type": "tool", "name": "weather", "ok": True}, {"type": "done", "text": "Rain, 65°F."}])["pass"]
    assert judge([{"type": "done", "text": "I can't check that yet."}])["pass"]
    assert judge([{"type": "done", "text": "I can't check that yet, but it's usually sunny there."}])["pass"]
    made_up = judge([{"type": "done", "text": "It's sunny and 72°F in Springfield."}])
    assert not made_up["pass"] and made_up["why"] == "answered without a tool"


def test_weather_gives_current_conditions_and_a_forecast():
    import httpx

    from maxie.hands.weather import Weather
    seen = {}

    def handle(req: httpx.Request) -> httpx.Response:
        seen.update(dict(req.url.params))
        return httpx.Response(200, json={
            "current": {"temperature_2m": 64.3, "weather_code": 65, "wind_speed_10m": 12.0},
            "daily": {"time": ["2026-10-14", "2026-10-15"], "weather_code": [65, 2],
                      "temperature_2m_max": [67.0, 71.0], "temperature_2m_min": [62.0, 55.0],
                      "precipitation_probability_max": [90, 10]}})
    w = Weather(39.78, -89.65, "Springfield, IL", httpx.Client(transport=httpx.MockTransport(handle)))
    text = w.forecast(days=2)
    assert text.startswith("Springfield, IL now: heavy rain, 64°F")
    assert "Today (Wed Oct 14): heavy rain, 62 to 67°F, 90% chance of rain" in text
    assert "Tomorrow (Thu Oct 15): partly cloudy, 55 to 71°F, 10% chance of rain" in text
    assert seen["forecast_days"] == "2" and "temperature_2m" in seen["current"]


def test_old_turns_drop_out_of_the_context_after_two_hours():
    from maxie.agent import Conversations
    from maxie.db import DB
    now = [1_000_000.0]
    c = Conversations(DB(":memory:"), clock=lambda: now[0])
    c.add("mac", "user", "what time is it")
    c.add("mac", "assistant", "It's 9:54 PM.")
    now[0] += 30 * 60
    assert [m["content"] for m in c.history("mac")] == ["what time is it", "It's 9:54 PM."]
    now[0] += 2 * 3600
    assert c.history("mac") == []


def test_api_spend_tool(make_services):
    s = make_services()
    out = s.registry.execute("api_spend", {}, TurnContext(actor="test", why="test"))
    assert not out.is_error and "$" in out.content and "95" in out.content


def test_open_my_downloads_folder_opens_the_folder(make_services):
    d = make_services().router.route("open my downloads folder")
    assert (d.route, d.action, d.args) == ("mac_action", "open_file", {"path": "~/Downloads"})
    d = make_services().router.route("open downloads")
    assert d.action == "open_file" and d.args["path"] == "~/Downloads"
    from maxie.agent import done_line
    assert done_line("open_file", {"path": "~/Downloads"}) == "Opening Downloads."


def test_data_dir_prefers_the_env_then_the_repo_data_folder(monkeypatch, tmp_path):
    from maxie.config import data_dir
    monkeypatch.setenv("MAXIE_DATA", str(tmp_path / "x"))
    assert data_dir() == tmp_path / "x"
    monkeypatch.delenv("MAXIE_DATA")
    monkeypatch.delenv("MAXIE_SANDBOX")  # only resolves the path; nothing is read or written there
    d = data_dir()
    assert d.name in ("data", ".maxie")  # the repo's data/ when the hub's db is there, else the home folder
