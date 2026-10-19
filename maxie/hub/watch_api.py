"""Watch and learn.

- `GET /api/watch`: eyes open or closed, excluded apps, how much is kept, what he does habitually, the open
  question and his routines.
- `PUT /api/watch`: open or close the eyes, and the apps never watched.
- `POST /api/watch/observe`: the notch app's batch of app switches, titles and who he messages. Each item is
  checked again (maxie.watch.admit) before it's kept.
- `POST /api/watch/forget-today`.
- `POST /api/watch/suggestions/{id}` with yes or no.
"""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, StrictBool

from ..watch import KEEP_DAYS

MAX_ITEMS = 500


class WatchIn(BaseModel):
    watching: StrictBool | None = None
    excluded: list[str] | None = None


class ItemIn(BaseModel):
    kind: str = Field(min_length=1, max_length=20)
    app: str = Field(min_length=1, max_length=300)
    name: str = Field(default="", max_length=300)
    detail: str = Field(default="", max_length=1000)
    category: str = Field(default="", max_length=100)


class ObserveIn(BaseModel):
    items: list[ItemIn] = Field(max_length=MAX_ITEMS)


class AnswerIn(BaseModel):
    answer: Literal["yes", "no"]


def register(app, s, api) -> None:
    def describe() -> dict:
        w = s.watch
        return {"watching": w.watching, "excluded": list(s.settings.watch_excluded), "kept_days": KEEP_DAYS,
                "today": w.today(), "kept": w.count(), "habits": w.habits(), "suggestion": w.pending(),
                "routines": w.routines()}

    @app.get("/api/watch", dependencies=api)
    def watch_get():
        return describe()

    @app.put("/api/watch", dependencies=api)
    def watch_put(body: WatchIn):
        if body.excluded is not None:
            try:
                s.settings.update({"watch_excluded": body.excluded}).save()
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
        if body.watching is not None:
            s.watch.set_watching(body.watching)
        return describe()

    @app.post("/api/watch/observe", dependencies=api)
    def watch_observe(body: ObserveIn):
        return {"kept": s.watch.observe([i.model_dump() for i in body.items])}

    @app.post("/api/watch/forget-today", dependencies=api)
    def watch_forget_today():
        return {"forgot": s.watch.forget_today()}

    @app.post("/api/watch/suggestions/{sid}", dependencies=api)
    def watch_answer(sid: int, body: AnswerIn):
        said = s.watch.accept(sid) if body.answer == "yes" else ("Okay." if s.watch.dismiss(sid) else None)
        if said is None:
            raise HTTPException(404, "no such suggestion")
        return {"said": said, **describe()}
