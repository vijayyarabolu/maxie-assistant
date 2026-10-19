"""Delegate coding / long jobs to Claude Code (`claude -p`) on your own Claude plan, so they don't spend API credit."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from ..budget import SONNET
from .registry import Registry, Tool, schema

EFFORT = "medium"


def command(task: str, model: str = SONNET) -> list[str]:
    return ["claude", "-p", task, "--model", model, "--effort", EFFORT]


def spawn(task: str, cwd: str, model: str = SONNET) -> str:
    log = Path(cwd).expanduser() / ".maxie-task.log"
    with open(log, "a") as fh:
        subprocess.Popen(command(task, model), cwd=str(Path(cwd).expanduser()), stdout=fh, stderr=subprocess.STDOUT)
    return f"Claude Code is on it in {cwd}; output goes to {log}"


def register(reg: Registry, allowed_folders: list[str], runner: Callable[..., str] = spawn, models=None) -> None:
    from .mac import in_allowed

    def run(a):
        if not in_allowed(a["folder"], allowed_folders):
            raise PermissionError("that folder isn't on the allowed list (Settings)")
        if models is None:
            return runner(a["task"], a["folder"])
        return runner(a["task"], a["folder"], models.active("sonnet"))  # the same Sonnet the brain uses

    s = {"type": "string"}
    reg.add(Tool("delegate_to_claude_code",
                 "Hand a coding or long multi-step job to Claude Code (runs on this computer). Needs the user's OK.",
                 schema({"task": s, "folder": s}), run, "run",
                 summary=lambda a: f"Run Claude Code in {a['folder']}: {a['task'][:80]}"))
