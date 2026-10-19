"""About me follows him: what Maxie says once when Location shows he has moved.

The Mac reverse-geocodes on the Mac (CLGeocoder) and sends the city, coordinates, IANA time zone and the zone's
everyday name ("Central"); the hub only words the line.
"""

from __future__ import annotations


def short_city(place: str) -> str:
    """"Austin, Texas" -> "Austin"."""
    return place.strip().split(",")[0].strip()


def zone_word(timezone: str, zone_name: str) -> str:
    """The clock's everyday name: the Mac's ("Central"), else the city in the zone id ("Kolkata time")."""
    if zone_name.strip():
        return zone_name.strip()
    return timezone.rsplit("/", 1)[-1].replace("_", " ") + " time"


def moved_line(place: str, timezone: str, zone_name: str, zone_changed: bool) -> str:
    city = short_city(place)
    if not zone_changed:
        return f"You're in {city} now."
    return f"You're in {city} now; I set your clock to {zone_word(timezone, zone_name)}."
