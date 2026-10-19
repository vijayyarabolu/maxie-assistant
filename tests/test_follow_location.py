"""About me follows him. The Mac reports significant moves (reverse-geocoded on the Mac); the hub
updates the city and clock and tells him once in the panel. A fixed city stays fixed."""

from test_settings_api import Deps, H, client

from maxie.hub.place import moved_line, short_city

AUSTIN = {"name": "Austin, Texas", "lat": 30.27, "lon": -97.74, "timezone": "America/Chicago", "zone_name": "Central"}
DENVER = {"name": "Denver, Colorado", "lat": 39.74, "lon": -104.99, "timezone": "America/Denver", "zone_name": "Mountain"}


def said(s) -> list[dict]:
    """Records what the hub publishes on the events stream from now on (the panel shows transcript lines)."""
    seen: list[dict] = []
    publish = s.events.publish
    s.events.publish = lambda e: (seen.append(e), publish(e))[1]
    return seen


def transcripts(seen):
    return [e for e in seen if e.get("type") == "transcript"]


def test_a_move_updates_about_me_and_tells_him_once(make_services):
    # Arrange
    c, s = client(make_services, Deps())
    s.settings.update({"weather_place": "Springfield, IL", "timezone": "America/Chicago"})
    seen = said(s)

    # Act
    first = c.post("/api/place/here", json=DENVER, headers=H).json()
    again = c.post("/api/place/here", json=DENVER, headers=H).json()

    # Assert
    assert first["moved"] is True and first["message"] == "You're in Denver now; I set your clock to Mountain."
    assert (s.settings.weather_place, s.settings.timezone) == ("Denver, Colorado", "America/Denver")
    assert again["moved"] is False and again["message"] is None
    lines = transcripts(seen)
    assert [e["text"] for e in lines] == ["You're in Denver now; I set your clock to Mountain."]
    assert lines[0]["role"] == "assistant" and lines[0]["final"] is True


def test_same_clock_says_only_the_city(make_services):
    c, s = client(make_services, Deps())
    s.settings.update({"weather_place": "Springfield, IL", "timezone": "America/Chicago"})
    r = c.post("/api/place/here", json=AUSTIN, headers=H).json()
    assert r["message"] == "You're in Austin now."


def test_a_fixed_city_stays_fixed(make_services):
    c, s = client(make_services, Deps())
    s.settings.update({"weather_place": "Springfield, IL", "follow_location": False})
    seen = said(s)
    r = c.post("/api/place/here", json=DENVER, headers=H).json()
    assert r == {"ignored": True, "moved": False, "message": None, "place": "Springfield, IL"}
    assert s.settings.weather_place == "Springfield, IL" and transcripts(seen) == []


def test_follow_location_is_on_by_default_and_must_be_a_switch(make_services):
    c, s = client(make_services, Deps())
    assert c.get("/api/settings", headers=H).json()["follow_location"] is True
    assert c.put("/api/settings", json={"follow_location": "yes"}, headers=H).status_code == 400
    assert c.put("/api/settings", json={"follow_location": False}, headers=H).status_code == 200
    assert s.settings.follow_location is False


def test_the_first_fix_fills_in_quietly(make_services):
    """Turning Location on in Permissions sets the city; it isn't a move, so nothing is said."""
    c, s = client(make_services, Deps())
    s.settings.update({"weather_place": "Springfield, IL", "timezone": "America/Chicago"})
    seen = said(s)
    r = c.post("/api/place/here", json={**DENVER, "first": True}, headers=H).json()
    assert r["moved"] is False and transcripts(seen) == []
    assert s.settings.weather_place == "Denver, Colorado"


def test_zone_name_falls_back_to_the_city_in_the_zone_id():
    assert moved_line("Pune, Maharashtra", "Asia/Kolkata", "", zone_changed=True) == \
        "You're in Pune now; I set your clock to Kolkata time."
    assert short_city("  Austin, Texas ") == "Austin"
    assert short_city("Reykjavík") == "Reykjavík"
