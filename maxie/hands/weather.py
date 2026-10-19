"""Weather from Open-Meteo (free for non-commercial use, no key): current conditions plus a daily forecast
for the city in Settings › About me. Open-Meteo's data is CC BY 4.0: "Weather data by Open-Meteo.com"."""

from __future__ import annotations

from datetime import date

import httpx

from .registry import Registry, Tool, schema

CODES = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "fog", 48: "fog", 51: "drizzle",
         53: "drizzle", 55: "drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow",
         73: "snow", 75: "heavy snow", 80: "showers", 81: "showers", 82: "heavy showers", 95: "thunderstorms",
         96: "thunderstorms with hail", 99: "thunderstorms with hail"}
MAX_DAYS = 7


class Weather:
    def __init__(self, lat: float = 0.0, lon: float = 0.0, place: str = "", client: httpx.Client | None = None,
                 settings=None):
        # With `settings`, the place is read on every call, so a new city in About me applies at once.
        self.http, self.settings = client or httpx.Client(timeout=8), settings
        self._fixed = (lat, lon, place)

    @property
    def lat(self) -> float:
        return self.settings.weather_lat if self.settings else self._fixed[0]

    @property
    def lon(self) -> float:
        return self.settings.weather_lon if self.settings else self._fixed[1]

    @property
    def place(self) -> str:
        return self.settings.weather_place if self.settings else self._fixed[2]

    def forecast(self, days: int = 1) -> str:
        if not self.place:
            return "No city is set yet, so I can't check the weather. Set one in Settings, About me."
        days = max(1, min(int(days), MAX_DAYS))
        r = self.http.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": self.lat, "longitude": self.lon,
            "current": "temperature_2m,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "timezone": "auto", "forecast_days": days})
        r.raise_for_status()
        data = r.json()
        cur, d = data.get("current", {}), data["daily"]
        lines = [f"{self.place} now: {CODES.get(cur.get('weather_code'), 'mixed')}, {cur.get('temperature_2m', 0):.0f}°F, "
                 f"wind {cur.get('wind_speed_10m', 0):.0f} mph."]
        for i, day in enumerate(d["time"]):
            label = "Today (" + _day(day) + ")" if i == 0 else "Tomorrow (" + _day(day) + ")" if i == 1 else _day(day)
            rain = d.get("precipitation_probability_max", [None] * len(d["time"]))[i]
            lines.append(f"{label}: {CODES.get(d['weather_code'][i], 'mixed')}, "
                         f"{d['temperature_2m_min'][i]:.0f} to {d['temperature_2m_max'][i]:.0f}°F"
                         + (f", {rain:.0f}% chance of rain" if rain is not None else "") + ".")
        return " ".join(lines)

    def today(self) -> str:
        return self.forecast(1)


def _day(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%a %b %-d")


class FakeWeather:
    """The demo hub's forecast: made up, never fetched."""

    place = "Springfield"

    def forecast(self, days: int = 1) -> str:
        out = ["Springfield now: partly cloudy, 64°F, wind 6 mph.", "Today (demo): partly cloudy, 52 to 68°F, 10% chance of rain."]
        if days > 1:
            out.append("Tomorrow (demo): light rain, 49 to 61°F, 70% chance of rain.")
        return " ".join(out)

    def today(self) -> str:
        return self.forecast(1)


def register(reg: Registry, weather: Weather) -> None:
    reg.add(Tool("weather", "Current weather and the forecast where the user is (days: 1 = today, up to 7).",
                 schema({"days": {"type": "integer", "minimum": 1, "maximum": MAX_DAYS}}, []),
                 lambda a: weather.forecast(int(a.get("days", 1))), "read"))
