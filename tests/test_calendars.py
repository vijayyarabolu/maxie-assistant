""""what's on my calendar?" said only "Dussehra". It read only the Google primary calendar; the Mac's
Calendar (iCloud Home / Work, invites, holidays) was a separate tool. One tool reads every calendar now and says
which it read and which it couldn't."""

from datetime import datetime
from zoneinfo import ZoneInfo

from maxie.hands import calendars
from maxie.hands.apple import AppleUnavailable, FakeApple
from maxie.hands.google import Event, FakeGoogle, NotConnected

NOW = datetime(2026, 10, 21, 9, 30, tzinfo=ZoneInfo("America/Chicago"))
MAC_CALENDARS = [{"title": "Home", "account": "iCloud"}, {"title": "Work", "account": "iCloud"},
                 {"title": "Holidays in India", "account": "Other"}]


def google_with(*events):
    g = FakeGoogle({"emails": [], "events": [], "drive": []})
    g.event_list = list(events)
    return g


def test_events_from_google_and_the_mac_come_together_in_time_order():
    # Arrange
    g = google_with(Event(id="1", title="Mock interview", start="2026-10-21T14:00:00-05:00", end="2026-10-21T15:00:00-05:00"))
    mac = FakeApple(events=[{"title": "Maharishi Valmiki Jayanti", "start": "2026-10-21", "all_day": "true",
                             "end": "2026-10-22T00:00:00Z", "location": "", "calendar": "Holidays in India"},
                            {"title": "Dentist", "start": "2026-10-21T16:00:00Z", "all_day": "false", "end": "",
                             "location": "Main St", "calendar": "Home"}], calendars=MAC_CALENDARS)

    # Act
    out = calendars.all_events(g, mac, NOW, days=1)

    # Assert
    assert [e["title"] for e in out["events"]] == ["Maharishi Valmiki Jayanti", "Mock interview", "Dentist"]
    assert out["events"][0]["all_day"] is True and out["events"][0]["start"] == "2026-10-21", "an all-day event keeps its day"
    assert out["calendars_read"] == ["Google (primary)", "Mac Calendar: Holidays in India (Other), Home (iCloud), Work (iCloud)"]
    assert out["could_not_read"] == [] and out["from"] == "2026-10-21"


def test_an_event_on_both_google_and_the_mac_is_said_once():
    g = google_with(Event(id="1", title="Standup", start="2026-10-21T09:00:00-05:00", end=""))
    mac = FakeApple(events=[{"title": "Standup", "start": "2026-10-21T09:00:00-05:00", "all_day": "false",
                             "calendar": "alex@example.com"}], calendars=MAC_CALENDARS)
    assert [e["title"] for e in calendars.all_events(g, mac, NOW)["events"]] == ["Standup"]


def test_a_calendar_that_cannot_be_read_is_named_and_the_others_still_answer():
    mac = FakeApple(error=AppleUnavailable("calendars"))
    g = google_with(Event(id="1", title="Gym", start="2026-10-21T18:00:00-05:00", end=""))
    out = calendars.all_events(g, mac, NOW)
    assert [e["title"] for e in out["events"]] == ["Gym"]
    assert out["could_not_read"] == ["Mac Calendar: Turn on Calendars for Maxie in Settings › Permissions."]


def test_google_not_connected_is_said_and_the_mac_still_answers():
    mac = FakeApple(events=[{"title": "Dussehra", "start": "2026-10-20", "all_day": "true", "calendar": "Holidays in India"}],
                    calendars=MAC_CALENDARS)
    out = calendars.all_events(NotConnected(), mac, NOW)
    assert [e["title"] for e in out["events"]] == ["Dussehra"]
    assert out["could_not_read"][0].startswith("Google (primary): Google isn't connected yet")


def test_the_tool_starts_at_the_start_of_today_and_caps_the_days():
    class Reg:
        tools = {}

        def add(self, tool):
            self.tools[tool.name] = tool

    reg, mac = Reg(), FakeApple(calendars=MAC_CALENDARS)
    calendars.register(reg, google_with(), mac, now=lambda: NOW)
    out = reg.tools["calendar_events"].fn({"days": 90})
    assert out["days"] == calendars.MAX_DAYS and out["from"] == "2026-10-21" and mac.calls == [("calendar", 31)]
