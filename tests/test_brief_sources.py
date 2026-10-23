"""GET /api/brief was a 500 when Google Calendar answered 403. One failing source never fails the
brief: the other parts show, plus one line for the source that said no. Settings › Connections says why Calendar
was refused (the API is off in his Google Cloud project, or the sign-in lacks calendar access) and the fix."""

from datetime import datetime

import httpx
import pytest

from maxie.hands.google_real import GoogleCalendar, GoogleRefused, refusal
from maxie.proactive import BriefSection
from tests.conftest import TZ
from tests.test_api import H, client_for
from tests.test_real_sources import jobs_answering, live_like
from tests.test_settings_api import Deps, client

ACTIVATE = "https://console.developers.google.com/apis/api/calendar-json.googleapis.com/overview?project=123"
API_OFF = {"error": {"code": 403, "status": "PERMISSION_DENIED",
                     "message": f"Google Calendar API has not been used in project 123 before or it is disabled. Enable it by visiting {ACTIVATE} then retry.",
                     "errors": [{"reason": "accessNotConfigured", "domain": "usageLimits"}],
                     "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "SERVICE_DISABLED",
                                  "metadata": {"activationUrl": ACTIVATE, "service": "calendar-json.googleapis.com"}}]}}
NO_SCOPE = {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "Request had insufficient authentication scopes.",
                      "errors": [{"reason": "insufficientPermissions"}],
                      "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}}


class Token:
    def bearer(self) -> dict:
        return {"Authorization": "Bearer x"}


def calendar_answering(status: int, body: dict | None = None) -> GoogleCalendar:
    t = httpx.MockTransport(lambda r: httpx.Response(status, json=body if body is not None else {"items": []}))
    return GoogleCalendar(Token(), http=httpx.Client(transport=t))


def evening_of(clock) -> tuple[datetime, datetime]:
    clock.set(datetime(2026, 10, 16, 18, 45, tzinfo=TZ))
    return datetime(2026, 10, 16, 18, 45, tzinfo=TZ), datetime(2026, 10, 17, tzinfo=TZ)


@pytest.mark.parametrize("body,why,url", [(API_OFF, "api_off", ACTIVATE), (NO_SCOPE, "scope", ""), ({}, "other", "")])
def test_google_says_why_it_refused(body, why, url):
    e = refusal("Calendar", httpx.Response(403, json=body))
    assert (e.service, e.why, e.fix_url) == ("Calendar", why, url)


def test_the_activation_link_is_found_in_the_message_too():
    body = {"error": {"code": 403, "message": f"... Enable it by visiting {ACTIVATE} then retry.",
                      "errors": [{"reason": "accessNotConfigured"}]}}
    assert refusal("Calendar", httpx.Response(403, json=body)).fix_url == ACTIVATE


def test_a_non_json_403_is_still_a_refusal():
    assert refusal("Calendar", httpx.Response(403, text="<html>no</html>")).why == "other"


def test_calendar_403_raises_a_refusal_and_remembers_it(clock):
    cal = calendar_answering(403, API_OFF)
    with pytest.raises(GoogleRefused) as e:
        cal.events(*evening_of(clock))
    assert e.value.why == "api_off" and cal.refused is e.value
    cal.http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"items": []})))
    assert cal.events(*evening_of(clock)) == [] and cal.refused is None, "fixed: forgotten"


def test_a_403_from_calendar_never_fails_the_brief(clock):
    evening_of(clock)
    b = live_like(clock, jobs_answering(200), cal=calendar_answering(403, API_OFF)).brief()
    sections = {s.name: s for s in b.sections}
    assert sections["Calendar"] == BriefSection("Calendar", missing="Google said no (403)", action="open_connections")
    assert {"Email", "Jobs", "Spend"} <= set(sections), "the other parts still show"
    assert "Calendar: Google said no (403)." in b.text()


def test_any_failing_source_becomes_one_line(clock):
    evening_of(clock)

    class Broken:
        connected = True

        def events(self, start, end):
            raise RuntimeError("socket closed")

    b = live_like(clock, jobs_answering(200), cal=Broken()).brief()
    calendar = next(s for s in b.sections if s.name == "Calendar")
    assert (calendar.missing, calendar.action) == ("Calendar isn't answering right now", "open_connections")


def test_the_evening_wrap_survives_a_refused_calendar(clock):
    evening_of(clock)
    text = live_like(clock, jobs_answering(200), cal=calendar_answering(403, NO_SCOPE)).evening()
    assert text.startswith("Wrap-up.") and "Tomorrow: Google said no (403)." in text


def test_api_brief_is_200_with_a_refused_calendar(make_services):
    s = make_services()
    s.proactive.cal = calendar_answering(403, API_OFF)
    r = client_for(s).get("/api/brief", headers=H)
    assert r.status_code == 200
    assert {"name": "Calendar", "lines": [], "missing": "Google said no (403)", "action": "open_connections"} in r.json()["sections"]


@pytest.mark.parametrize("body,why,fix", [(API_OFF, "api_off", ACTIVATE), (NO_SCOPE, "scope", "")])
def test_connections_says_why_calendar_is_refused_and_the_fix(make_services, body, why, fix):
    c, s = client(make_services, Deps())
    s.proactive.cal = calendar_answering(403, body)
    c.get("/api/brief", headers=H)
    refused = c.get("/api/connections", headers=H).json()["google"]["refused"]
    assert refused == [{"service": "Calendar", "why": why, "line": GoogleRefused("Calendar", why, fix).line, "fix_url": fix}]
    assert ("Enable" in refused[0]["line"]) == (why == "api_off")
    assert ("Sign in with Google again" in refused[0]["line"]) == (why == "scope")


def test_connections_lists_nothing_while_calendar_answers(make_services):
    c, _ = client(make_services, Deps())
    assert c.get("/api/connections", headers=H).json()["google"]["refused"] == []


def test_the_brief_lists_the_macs_calendars_beside_google(clock):
    # the brief read only Google; the Mac's Calendar (iCloud Home / Work, invites, holidays) was missing
    from maxie.hands.apple import FakeApple
    p = live_like(clock, jobs_answering(200))
    day = "2026-10-15"  # the clock fixture's day
    p.apple = FakeApple(events=[{"title": "Maharishi Valmiki Jayanti", "start": day, "all_day": "true",
                                 "calendar": "Holidays in India"}])
    calendar = next(s for s in p.brief().sections if s.name == "Calendar")
    assert "All day: Maharishi Valmiki Jayanti" in calendar.lines


def test_a_mac_without_the_bridge_still_gets_googles_calendar(clock):
    from maxie.hands.apple import AppleUnavailable, FakeApple
    p = live_like(clock, jobs_answering(200), cal=calendar_answering(200))
    p.apple = FakeApple(error=AppleUnavailable("bridge_down"))
    calendar = next(s for s in p.brief().sections if s.name == "Calendar")
    assert calendar.missing == "" and calendar.lines == ["Nothing on your calendars today."]
