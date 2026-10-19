"""Hub routes: wake words, voice enrollment, location."""


from test_settings_api import Deps, H, client


def test_wake_words_setting_takes_only_the_four_forms(make_services):
    c, s = client(make_services, Deps())
    assert c.get("/api/settings", headers=H).json()["wake_words"] == ["hey max", "hey maxie", "max", "maxie"]
    assert c.put("/api/settings", json={"wake_words": ["hey max"]}, headers=H).status_code == 200
    assert s.settings.wake_words == ["hey max"]
    assert c.put("/api/settings", json={"wake_words": ["hey claude"]}, headers=H).status_code == 400


def test_enrollment_request_reaches_the_voice_loop_once(make_services):
    c, _ = client(make_services, Deps())
    assert c.post("/api/voice/enroll", headers=H).json()["ok"] is True
    assert c.get("/api/voice/wake", headers=H).json()["enroll"] is True
    assert c.get("/api/voice/wake", headers=H).json()["enroll"] is False


def test_speaker_status_and_forget(make_services, tmp_path):
    # Arrange: the voice loop enrolled him and reported back
    c, s = client(make_services, Deps())
    s.speaker_store = tmp_path / "voice" / "speaker.npz"
    s.speaker_store.parent.mkdir()
    s.speaker_store.write_bytes(b"prints")
    c.post("/api/voice/speaker", json={"enrolled": True, "threshold": 0.6, "message": "Enrolled."}, headers=H)

    # Act
    st = c.get("/api/voice/speaker", headers=H).json()
    forgot = c.post("/api/voice/speaker/forget", headers=H).json()

    # Assert
    assert st["enrolled"] is True and st["message"] == "Enrolled."
    assert forgot["enrolled"] is False and not s.speaker_store.exists()


def test_location_sets_the_city_in_about_me(make_services):
    # Arrange
    c, s = client(make_services, Deps())
    here = {"name": "Springfield, IL", "lat": 39.78, "lon": -89.65, "timezone": "America/Chicago"}

    # Act
    r = c.post("/api/place/here", json=here, headers=H)

    # Assert
    assert r.status_code == 200
    assert (s.settings.weather_place, s.settings.weather_lat, s.settings.timezone) == ("Springfield, IL", 39.78, "America/Chicago")


def test_location_rejects_nonsense(make_services):
    c, _ = client(make_services, Deps())
    assert c.post("/api/place/here", json={"name": "X", "lat": 120, "lon": 0, "timezone": "UTC"}, headers=H).status_code == 400
    assert c.post("/api/place/here", json={"name": "X", "lat": 1, "lon": 1, "timezone": "Mars/Base"}, headers=H).status_code == 400
