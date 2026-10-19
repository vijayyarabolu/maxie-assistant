"""Is he up?

- `POST /api/presence`: the notch app, every minute and on lock / unlock / display sleep: screen locked, displays
  asleep, seconds since the last input, media playing. Strict types: a wrong one is a 422, nothing stored.
- `POST /api/presence/active`: the phone app came to the front (or a tap): he is awake.
- `GET /api/presence`: asleep or not, and the sleep / wake log (local only, for learning his bedtime).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, StrictBool

MAX_IDLE_S = 7 * 24 * 3600.0


class SignalsIn(BaseModel):
    locked: StrictBool
    displays_asleep: StrictBool
    idle_s: float = Field(ge=0, le=MAX_IDLE_S)
    media: StrictBool


class ActiveIn(BaseModel):
    source: Literal["phone", "mac", "voice"] = "phone"


def register(app, s, api) -> None:
    @app.post("/api/presence", dependencies=api)
    def presence_report(body: SignalsIn):
        return {"asleep": s.presence.report(body.locked, body.displays_asleep, body.idle_s, body.media)}

    @app.post("/api/presence/active", dependencies=api)
    def presence_active(body: ActiveIn):
        return {"asleep": s.presence.activity(body.source)}

    @app.get("/api/presence", dependencies=api)
    def presence_get():
        return {"asleep": s.presence.asleep(), "mode": s.settings.sleep_mode, "usual": s.presence.usual(),
                "log": s.presence.log()}
