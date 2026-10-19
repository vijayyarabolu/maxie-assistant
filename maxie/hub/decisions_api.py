"""Settings › Speed › Deciders: per decision type, how fast and how right each decider is.

The scores are kept in data/decisions.json; "Run now" (and the weekly job) recomputes them. Laya's share of
the work runs in Laya's own low-priority process, never in the hub.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..config import data_dir
from ..router.decisions import run_all


def report_path() -> Path:
    return data_dir() / "decisions.json"


def load() -> dict:
    p = report_path()
    try:
        return json.loads(p.read_text()) if p.exists() else {"updated": None, "types": []}
    except (OSError, ValueError):
        return {"updated": None, "types": []}


def run(s) -> dict:
    report = run_all(s.router.labelled(), s.laya)
    report_path().write_text(json.dumps(report, indent=2))
    return report


def register(app, s, api) -> None:
    @app.get("/api/decisions", dependencies=api)
    def decisions_get():
        return load()

    @app.post("/api/decisions/run", dependencies=api)
    async def decisions_run():
        return await asyncio.to_thread(run, s)
