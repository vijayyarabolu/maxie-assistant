from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from maxie.config import Settings
from maxie.hub.services import build

TZ = ZoneInfo("America/Chicago")


def SETTINGS() -> Settings:  # the fictional test user, set up the way Setup leaves a new hub  # noqa: N802
    return Settings(user_name="Alex", weather_place="Springfield, IL", weather_lat=39.78, weather_lon=-89.65,
                    timezone="America/Chicago", monthly_budget_usd=95.0, watching=True)


class Clock:
    def __init__(self, dt: datetime):
        self.t = dt.timestamp()

    def __call__(self) -> float:
        return self.t

    def set(self, dt: datetime) -> None:
        self.t = dt.timestamp()


@pytest.fixture(autouse=True)
def never_live(tmp_path_factory, monkeypatch):
    """Every test runs sandboxed: a throwaway data folder, and the live folders refuse to open."""
    monkeypatch.setenv("MAXIE_DATA", str(tmp_path_factory.mktemp("data")))
    monkeypatch.setenv("MAXIE_SANDBOX", "1")
    monkeypatch.setenv("TZ", "America/Chicago")  # Settings() takes this computer's zone; the tests pin one


@pytest.fixture
def clock():
    return Clock(datetime(2026, 10, 15, 7, 59, tzinfo=TZ))


@pytest.fixture
def make_services(tmp_path, monkeypatch, clock):
    monkeypatch.setenv("MAXIE_DATA", str(tmp_path))

    def _make(client=None, **kw):
        return build(SETTINGS(), db_path=":memory:", fakes=True, anthropic_client=client, clock=clock, **kw)
    return _make
