"""Settings › Maxie: `GET /api/maxie` describes his character, look and every option;
`PUT /api/maxie` changes them. A new look starts a low-priority re-render of the notch loops on the Mac
(maxie.maxie_look.LoopRenderer); the app polls `render` and switches once `loops_dir` is set.
Every value is checked before any is stored, so a bad PUT changes nothing.
"""

from __future__ import annotations

import logging
import re

from fastapi import HTTPException

from .. import maxie_look as ml
from ..config import SLEEP_MODES, Settings, data_dir

FIELDS = {"look": "maxie_look", "character": "character", "maxie_size": "maxie_size", "personality": "personality",
          "chattiness": "chattiness", "voice": "voice", "voice_speed": "voice_speed",
          "quiet_start": "quiet_start", "quiet_end": "quiet_end", "sleep_mode": "sleep_mode", "brief_sleep": "brief_sleep"}
log = logging.getLogger(__name__)
HHMM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def usual_line(usual: tuple[str, str] | None) -> str | None:
    return f"You usually sleep around {usual[0]} and wake around {usual[1]}." if usual else None


def options() -> dict:
    return {"character": list(ml.CHARACTERS), "fur": [{"id": k, "name": v, "color": ml.SWATCHES[k]} for k, v in ml.FURS.items()],
            "furLength": list(ml.FUR_LENGTHS), "ears": list(ml.EARS), "eyes": list(ml.EYES),
            "accessory": list(ml.ACCESSORIES), "maxie_size": list(ml.SIZES), "personality": list(ml.PERSONALITIES),
            "chattiness": list(ml.CHATTINESS), "voice": ml.VOICES, "voice_speed": list(ml.VOICE_SPEED),
            "sleep_mode": list(SLEEP_MODES)}


def checked(body: dict) -> dict:
    """Settings field -> clean value for every key in the PUT, or HTTP 400 (nothing stored)."""
    unknown = set(body) - set(FIELDS)
    if unknown:
        raise HTTPException(400, f"not a Maxie setting: {', '.join(sorted(unknown))}")
    out = {}
    for key, value in body.items():
        field = FIELDS[key]
        try:
            if field in ("quiet_start", "quiet_end"):
                if not isinstance(value, str) or not HHMM.match(value):
                    raise ValueError(f"{key} must be HH:MM")
                out[field] = value
            elif field in ("sleep_mode", "brief_sleep"):
                out[field] = getattr(Settings().update({field: value}), field)  # the same check as /api/settings
            else:
                out[field] = ml.check(field, value)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    return out


def register(app, s, api) -> None:
    def renderer() -> ml.LoopRenderer:
        if getattr(s, "maxie_loops", None) is None:
            s.maxie_loops = ml.LoopRenderer(data_dir() / "mascot")
        return s.maxie_loops

    def describe(render: str | None = None) -> dict:
        st, loops = s.settings, renderer()
        look = ml.validate_look(st.maxie_look)
        where = loops.loops_dir(look) if st.character == "maxie" else None
        return {"character": st.character, "look": look, "render": render or loops.status(look, st.character),
                "loops_dir": str(where) if where else None, "maxie_size": st.maxie_size,
                "personality": st.personality, "chattiness": st.chattiness, "voice": st.voice,
                "voice_speed": st.voice_speed, "quiet_start": st.quiet_start, "quiet_end": st.quiet_end,
                "sleep_mode": st.sleep_mode, "brief_sleep": st.brief_sleep, "asleep": s.presence.asleep(),
                "usual_sleep": usual_line(s.presence.usual()),
                "options": options()}

    @app.get("/api/maxie", dependencies=api)
    def maxie_get():
        return describe()

    @app.put("/api/maxie", dependencies=api)
    def maxie_put(body: dict):
        changes = checked(body)
        for field, value in changes.items():
            setattr(s.settings, field, value)
        s.settings.save()
        return describe(start_render())

    def start_render() -> str:
        """The render is a side job: whatever goes wrong there, the save above already happened."""
        try:
            return renderer().render(ml.validate_look(s.settings.maxie_look), s.settings.character)
        except Exception:
            log.exception("maxie render failed after a save")
            return "failed"
