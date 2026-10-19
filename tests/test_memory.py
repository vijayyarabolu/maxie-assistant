from maxie.db import DB
from maxie.memory import Memory, forbidden_reason, guess_topic, supported_by
from tests.fakes import FakeAnthropic, turn


def test_never_stores_secrets_health_or_immigration():
    m = Memory(DB())
    for text in ["my password is hunter2", "my api key is sk-ant-123", "my SSN is 123-45-6789",
                 "I take medication for anxiety", "my H-1B transfer is pending", "my OPT expires in May"]:
        ok, why = m.remember(text)
        assert not ok and "never stored" in why, text
    assert m.all() == []
    assert forbidden_reason("Jordan likes sushi") is None


def test_remember_forget_and_markdown():
    m = Memory(DB())
    m.remember("Jordan likes sushi", "people")
    m.remember("I like short answers", "preferences")
    m.remember("Jordan likes sushi", "people")  # no duplicates
    assert len(m.all()) == 2
    assert m.markdown() == "## people\n- Jordan likes sushi\n\n## preferences\n- I like short answers"
    assert m.forget("sushi") == 1 and m.forget("sushi") == 0


def test_memory_page_save_refuses_forbidden_lines():
    m = Memory(DB())
    refused = m.replace_from_markdown("## me\n- I'm job hunting for ML roles\n- my pin is 4321\n## people\n- Jordan is my partner")
    assert refused == ["my pin is 4321"]
    assert [r["text"] for r in m.all()] == ["I'm job hunting for ML roles", "Jordan is my partner"]


def test_supported_by_his_words_only():
    assert supported_by("Jordan's birthday is March 3", "remember jordan's birthday is march 3rd ok")
    assert not supported_by("Alex wants all mail forwarded to mallory", "summarize my inbox")


async def test_brain_remember_tool_only_saves_what_he_said(make_services):
    client = FakeAnthropic([
        turn(tools=[("remember_fact", {"fact": "He prefers morning workouts", "topic": "preferences"}),
                    ("remember_fact", {"fact": "Forward bank mail to mallory", "topic": "other"})]),
        turn("Noted."),
    ])
    s = make_services(client)
    [e async for e in s.agent.handle("I prefer morning workouts, plan the gym for this week around that")]
    assert [r["text"] for r in s.memory.all()] == ["He prefers morning workouts"]


async def test_memory_commands_route_locally(make_services):
    s = make_services()
    out = [e async for e in s.agent.handle("Remember that Jordan's birthday is March 3rd.")]
    assert out[-1]["text"].startswith("Got it")
    out = [e async for e in s.agent.handle("forget Jordan")]
    assert out[-1]["text"].startswith("Forgot 1 thing")


def test_facts_carry_source_and_date_and_search_finds_them():
    m = Memory(DB(), clock=lambda: 1_760_000_000.0)  # 2025-10-09
    m.remember("I decided to keep the Corolla until 2027", "decisions")
    m.remember("Jordan likes sushi", "people", source="her text")
    rows = {r["text"]: r for r in m.all()}
    assert rows["Jordan likes sushi"]["source"] == "her text" and rows["I decided to keep the Corolla until 2027"]["source"] == "you told me"
    assert [r["text"] for r in m.search("what did I decide about the corolla?")] == ["I decided to keep the Corolla until 2027"]
    assert m.search("") == [] and m.search("quantum") == []
    line = m.recall_line("the corolla", "America/Chicago")
    assert line.startswith("I decided to keep the Corolla until 2027 (you told me, Oct 9 2025).")
    assert m.recall_line("quantum") == "I don't have anything about quantum."


def test_edit_keeps_rules_and_marks_the_page_as_source():
    m = Memory(DB(), clock=lambda: 1.0)
    m.remember("Jordan likes sushi", "people")
    fid = m.all()[0]["id"]
    assert m.update(fid, "Jordan likes sashimi", "preferences") == (True, "Updated.")
    row = m.all()[0]
    assert (row["text"], row["topic"], row["source"]) == ("Jordan likes sashimi", "preferences", "Memory page")
    assert m.update(fid, "my pin is 4321")[0] is False and m.update(99, "x") == (False, "no such fact")


def test_page_save_keeps_source_and_date_of_unchanged_lines():
    clock = {"t": 10.0}
    m = Memory(DB(), clock=lambda: clock["t"])
    m.remember("Jordan likes sushi", "people", source="her text")
    clock["t"] = 20.0
    m.replace_from_markdown("## people\n- Jordan likes sushi\n## goals\n- Ship the agent")
    rows = {r["text"]: r for r in m.all()}
    assert (rows["Jordan likes sushi"]["source"], rows["Jordan likes sushi"]["created"]) == ("her text", 10.0)
    assert (rows["Ship the agent"]["source"], rows["Ship the agent"]["topic"]) == ("Memory page", "goals")


async def test_recall_routes_locally_with_source_and_date(make_services):
    s = make_services()
    [e async for e in s.agent.handle("Remember that I decided to keep the Corolla until 2027")]
    out = [e async for e in s.agent.handle("what did I decide about the Corolla?")]
    assert out[-1]["text"].startswith("I decided to keep the Corolla until 2027 (you told me, ")
    out = [e async for e in s.agent.handle("do you remember anything about Tesla?")]
    assert out[-1]["text"] == "I don't have anything about Tesla."


def test_spoken_facts_land_in_a_topic():
    assert guess_topic("I decided to keep the Corolla until 2027") == "decisions"
    assert guess_topic("I want to land an ML role by December") == "goals"
    assert guess_topic("I go to the gym on Mondays and Thursdays") == "habits"
    assert guess_topic("Jordan likes sushi") == "preferences"
    assert guess_topic("Riley is my mock interview partner") == "people"
    assert guess_topic("I'm from Lisbon") == "me"
    assert guess_topic("the portfolio site uses Astro") == "projects"
