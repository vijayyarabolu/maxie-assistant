"""Pause Maxie: one switch for the notch menu, ⌥⇧P and "Maxie, go to sleep".

- `GET /api/pause`: {"paused": bool}.
- `POST /api/pause` {"on": bool}: paused, he stops listening and watching (his eyes close) and does nothing on his
  own (background tasks, the proactive scheduler and warm-ups wait). Resuming puts back what he had on before, kept
  in hub_meta so it survives a hub restart. Typed chat still works: he opened it himself.
"""

from __future__ import annotations

import json

from pydantic import BaseModel

RESTORE_KEY = "pause_restore"


class PauseIn(BaseModel):
    on: bool


def register(app, s, api) -> None:
    def remember() -> None:
        before = {"voice_muted": s.settings.voice_muted, "watching": s.watch.watching}
        s.db.execute("INSERT OR REPLACE INTO hub_meta VALUES (?, ?)", (RESTORE_KEY, json.dumps(before)))

    def restore() -> dict:
        row = s.db.one("SELECT value FROM hub_meta WHERE key=?", (RESTORE_KEY,))
        try:
            return json.loads(row["value"]) if row else {}
        except ValueError:
            return {}

    @app.get("/api/pause", dependencies=api)
    def pause_get():
        return {"paused": s.settings.paused}

    @app.post("/api/pause", dependencies=api)
    def pause_set(body: PauseIn):
        if body.on == s.settings.paused:
            return {"paused": s.settings.paused}
        if body.on:
            s.kill.stop()  # what he was doing stops too (the answer she is speaking: the voice helper cuts her off)
            remember()
            s.settings.update({"paused": True, "voice_muted": True}).save()
            s.watch.set_watching(False)
        else:
            before = restore()
            s.settings.update({"paused": False, "voice_muted": bool(before.get("voice_muted", False))}).save()
            s.watch.set_watching(bool(before.get("watching", True)))
        s.audit.log("user", "pause" if body.on else "resume", {}, "", "")
        s.events.publish({"type": "paused", "on": body.on})
        return {"paused": s.settings.paused}
