"""Background tasks and the screen.

- `GET /api/background`: the queue, newest first (queued, running, done, waiting for his OK, error, cancelled).
- `POST /api/background` {text}: queue a task (the panel's "Do it in the background", the phone).
- `DELETE /api/background/{id}`: cancel a task that hasn't started.
- `GET /api/scheduled`: what Maxie does on his own (Activity › Scheduled).
- `POST /api/confirm/{id}/away`: "When I'm away" on a "Can I use the screen?" card.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import HTTPException
from pydantic import BaseModel, StringConstraints


class BackgroundIn(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


def register(app, s, api) -> None:
    @app.get("/api/background", dependencies=api)
    def background_list(limit: int = 30):
        return s.background.recent(max(1, min(limit, 100)))

    @app.post("/api/background", dependencies=api)
    def background_add(body: BackgroundIn):
        job = s.background.add(body.text)
        s.audit.log("user", "background_add", {"id": job["id"]}, body.text[:200], "queued")
        return job

    @app.delete("/api/background/{job_id}", dependencies=api)
    def background_cancel(job_id: int):
        if s.background.get(job_id) is None:
            raise HTTPException(404, "no such task")
        if not s.background.cancel(job_id):
            raise HTTPException(409, "it already started; say \"Max, stop\" to stop it")
        s.audit.log("user", "background_cancel", {"id": job_id}, "", "cancelled")
        return {"ok": True}

    @app.get("/api/scheduled", dependencies=api)
    def scheduled():
        return s.background.scheduled()

    @app.post("/api/confirm/{cid}/away", dependencies=api)
    def confirm_away(cid: str):
        if s.registry.confirmations.get(cid) is None:
            raise HTTPException(404, "no such confirmation")
        try:
            c = s.registry.confirmations.later(cid)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        s.audit.log("user", f"later:{c.tool}", c.args, "when I'm away", "away")
        return asdict(c)
