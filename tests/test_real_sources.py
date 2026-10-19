"""FIX-UX5 §1-3: the live hub never shows fixture data, the brief follows the clock, errors become fixes."""
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from maxie import testdata
from maxie.budget import Budget
from maxie.config import Settings
from maxie.db import DB
from maxie.hands import google
from maxie.hands.jobs import JobsHTTP
from maxie.hands.registry import TurnContext
from maxie.hub import services as services_mod
from maxie.hub.api import create_app
from maxie.hub.services import assert_real, build
from maxie.proactive import Proactive, Wins
from tests.conftest import TZ

TOKEN = "t" * 32
H = {"Authorization": f"Bearer {TOKEN}"}
FIXTURES = ("Landlord Co", "Mallory", "Morgan", "AcmeAI", "Nimbus", "Hooli", "Umbrella", "Riley")


def jobs_answering(status: int) -> JobsHTTP:
    t = httpx.MockTransport(lambda r: httpx.Response(status, json={"jobs": [], "items": []}))
    return JobsHTTP("http://jobhunt", "http://resumegen", client=httpx.Client(transport=t))


def live_like(clock, jobs, cal=None) -> Proactive:
    """What the live hub runs when Google isn't signed in: NotConnected mail and calendar, real jobhunt client."""
    db = DB(":memory:")
    s = Settings()
    nc = google.NotConnected()
    return Proactive(db, s, nc, cal or nc, jobs, Budget(db, 95, 0.8, s.timezone, clock=clock), Wins(db, clock=clock),
                     notifier=None, clock=clock)


def test_the_live_hub_refuses_fake_sources():
    with pytest.raises(RuntimeError, match="fake mail, calendar on the live hub"):
        assert_real(mail=google.FakeGoogle(), calendar=google.FakeGoogle(), drive=google.NotConnected())
    assert_real(mail=google.NotConnected(), jobs=jobs_answering(200))


def test_fixtures_never_run_on_the_live_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("MAXIE_DATA", str(tmp_path))
    monkeypatch.setattr(testdata, "is_live", lambda p: True)
    monkeypatch.delenv("MAXIE_SANDBOX", raising=False)  # the folder is a temp one; test this guard alone
    with pytest.raises(RuntimeError, match="never runs on the live data folder"):
        build(Settings(), fakes=True)


def test_a_real_hub_without_google_has_no_fixture_mail(tmp_path, monkeypatch):
    monkeypatch.setenv("MAXIE_DATA", str(tmp_path))
    monkeypatch.setattr(services_mod, "secret", lambda name: None)
    monkeypatch.setattr(services_mod.jobs, "JobsHTTP", lambda *a, **k: jobs_answering(200))  # never the real jobhunt
    s = build(Settings(), db_path=":memory:", fakes=False)
    assert type(s.proactive.mail).__name__ == "LiveGoogle" and not s.proactive.mail.connected
    assert type(s.fakes["jobs"]).__name__ == "JobsOff"  # no jobhunt URL in Settings: the job apps are off
    text = s.proactive.morning()
    assert not any(word in text for word in FIXTURES), text
    assert "Gmail not connected" in text and "Google Calendar not connected" in text
    r = s.registry.execute("search_email", {"account": "main", "query": "rent"}, TurnContext())
    assert r.is_error and "isn't connected yet" in r.content
    assert s.proactive.check_job_inbox() == [] and s.proactive.wins.since(7) == []


@pytest.mark.parametrize("hour,title", [(8, "Good morning"), (11, "Good morning"), (12, "Today so far"),
                                         (13, "Today so far"), (20, "Today so far")])
def test_the_title_follows_the_clock(clock, hour, title):
    clock.set(datetime(2026, 10, 15, hour, 37, tzinfo=TZ))
    b = live_like(clock, jobs_answering(200)).brief()
    assert b.title == title
    assert b.text().startswith(f"{title}.") and ("Good morning" in b.text()) == (hour < 12)


def test_jobhunt_401_is_a_link_not_an_error(clock):
    b = {x.name: x for x in live_like(clock, jobs_answering(401)).brief().sections}
    assert (b["Jobs"].missing, b["Jobs"].action) == ("jobhunt not linked", "link_jobhunt")
    assert "401" not in live_like(clock, jobs_answering(401)).morning()


def test_jobhunt_down_says_so_without_the_error(clock):
    b = {x.name: x for x in live_like(clock, jobs_answering(500)).brief().sections}
    assert b["Jobs"].missing == "jobhunt isn't answering" and "500" not in b["Jobs"].text()


def test_brief_api_has_sections(make_services):
    s = make_services()
    r = TestClient(create_app(s, TOKEN, scheduler=False)).get("/api/brief", headers=H).json()
    assert r["title"] == "Good morning" and [x["name"] for x in r["sections"]] == ["Calendar", "Email", "Jobs", "Spend"]
    assert r["text"].startswith("Good morning.")


def test_link_jobhunt_copies_the_token_into_the_keychain(make_services, tmp_path):
    s = make_services()
    jobs = jobs_answering(200)
    s.fakes["jobs"] = jobs
    (tmp_path / "lan_token").write_text("abc123def456\n")
    s.token_files = {"JOBHUNT_TOKEN": str(tmp_path / "lan_token"), "RESUMEGEN_TOKEN": str(tmp_path / "missing")}
    saved = {}
    s.store_secret = lambda name, value: saved.__setitem__(name, value)
    r = TestClient(create_app(s, TOKEN, scheduler=False)).post("/api/connections/jobhunt/link", headers=H).json()
    assert r == {"ok": True, "linked": ["jobhunt"], "line": "jobhunt linked."}
    assert saved == {"JOBHUNT_TOKEN": "abc123def456"} and jobs.jh_headers == {"Cookie": "jh_token=abc123def456"}
    assert "abc123" not in str(s.audit.recent(5)), "the token is never logged"


def test_link_jobhunt_without_the_file_says_what_is_missing(make_services, tmp_path):
    s = make_services()
    s.fakes["jobs"] = jobs_answering(200)
    s.token_files = {"JOBHUNT_TOKEN": str(tmp_path / "nope")}
    r = TestClient(create_app(s, TOKEN, scheduler=False)).post("/api/connections/jobhunt/link", headers=H)
    assert r.status_code == 404 and "token file" in r.json()["detail"]


def test_jobs_tool_says_not_linked_instead_of_looking_empty_or_raw():
    import httpx

    from maxie.hands.jobs import UNLINKED_LINE, JobsHTTP, job_status

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "bad token"})

    jobs = JobsHTTP("http://jh", "http://rg", client=httpx.Client(transport=httpx.MockTransport(handler)))
    status = job_status(jobs)
    assert status["new_jobs"] == [] and status["note"] == UNLINKED_LINE
    reply = jobs.queue_resume("abc")
    assert reply == UNLINKED_LINE and "401" not in reply and "Keychain" not in reply


def test_off_a_mac_the_mac_hands_say_they_are_mac_only(tmp_path, monkeypatch):
    """CI runs on Linux: there is no Mac to drive, so every Mac action says so plainly, and a fake Mac is
    never allowed on a live hub anywhere."""
    monkeypatch.setenv("MAXIE_DATA", str(tmp_path))
    monkeypatch.setattr(services_mod, "secret", lambda name: None)
    monkeypatch.setattr(services_mod.mac.sys, "platform", "linux")
    s = build(Settings(), db_path=":memory:", fakes=False)
    assert type(s.mac).__name__ == "NoMac"
    r = s.registry.execute("open_app", {"name": "Spotify"}, TurnContext())
    assert r.is_error and "needs macOS" in r.content and "Linux" in r.content
    monkeypatch.setattr(services_mod.mac, "default_mac", lambda: services_mod.mac.FakeMac())
    with pytest.raises(RuntimeError, match="fake mac"):
        build(Settings(), db_path=":memory:", fakes=False)
