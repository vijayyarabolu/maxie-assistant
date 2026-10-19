"""Maxie's own Shortcuts: what has no API, through `shortcuts run "<name>" --input-path --output-path`.

Only shortcuts whose names start with "Maxie " are ever listed or run; your other shortcuts are never touched.
The first one is "Maxie Ask Apple" (Receive text → Use Model → Stop and output), which reaches Apple's Private
Cloud Compute model for free, something apps can't call directly. He makes it once
in the Shortcuts app (MORNING.md has the three steps); until then the tool says so.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from .registry import Registry, Tool, schema

PREFIX = "Maxie "
ASK_APPLE = "Maxie Ask Apple"
TIMEOUT_S = 60  # Private Cloud Compute answers can take a while
HOW_TO = {
    ASK_APPLE: ("Make it once in the Shortcuts app: new shortcut named \"Maxie Ask Apple\", turn on Receive "
                "Text input, add Use Model (Private Cloud Compute) with the Shortcut Input, then Stop and Output "
                "the response."),
}


class ShortcutMissing(Exception):
    def __init__(self, name: str):
        super().__init__(name)
        self.name = name

    def message(self) -> str:
        return f"The \"{self.name}\" shortcut isn't on this Mac yet. " + HOW_TO.get(
            self.name, "Make it in the Shortcuts app.")


class Shortcuts:
    def __init__(self, run: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        self._run = run

    def installed(self) -> list[str]:
        r = self._run(["shortcuts", "list"], capture_output=True, text=True, timeout=10)
        return [n for n in r.stdout.splitlines() if n.startswith(PREFIX)]

    def run(self, name: str, text: str) -> str:
        if not name.startswith(PREFIX):
            raise ValueError("Maxie only runs its own shortcuts")
        if name not in self.installed():
            raise ShortcutMissing(name)
        with tempfile.TemporaryDirectory(prefix="maxie-sc-") as d:
            given, out = Path(d) / "in.txt", Path(d) / "out.txt"
            given.write_text(text)
            r = self._run(["shortcuts", "run", name, "--input-path", str(given), "--output-path", str(out)],
                          capture_output=True, text=True, timeout=TIMEOUT_S)
            if r.returncode != 0:
                raise RuntimeError(f"{name} failed: {r.stderr.strip()[:200]}")
            return out.read_text().strip() if out.exists() else ""


def register(reg: Registry, shortcuts: Shortcuts) -> None:
    def ask_apple(a: dict) -> str:
        try:
            return shortcuts.run(ASK_APPLE, a["question"]) or "Apple didn't answer."
        except ShortcutMissing as e:
            return e.message()
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
            return f"Apple's model couldn't answer ({type(e).__name__})."

    reg.add(Tool("ask_apple", "Ask Apple's Private Cloud Compute model (free, private) through the \"Maxie Ask "
                 "Apple\" shortcut. Good for summaries, rewriting and general questions; no web or personal data.",
                 schema({"question": {"type": "string"}}), ask_apple, "read"))
