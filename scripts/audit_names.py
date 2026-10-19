"""Name audit: everything he sees or hears says Maxie ("him"), never the old names.

    .venv/bin/python scripts/audit_names.py        # exits 1 and lists the lines that still say them

Searches every tracked file except assets/ and JSON. A hit passes only when a rule in
scripts/audit_names.allow (path glob <TAB> line regex) lets it through: the Classic robot, which is an option
by that name, and tests that check an old name is gone.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PATTERN = re.compile(r"robot|antenna|ear ?cups|screen face|claude bot|the bot\b|sunset bot|hey claude", re.I)
SKIP = ("assets/",)
ALLOW = Path(__file__).with_name("audit_names.allow")


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    text: str


@dataclass(frozen=True)
class Rule:
    glob: str
    regex: re.Pattern


def load_allow(text: str) -> tuple[Rule, ...]:
    rules = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        glob, _, regex = raw.partition("\t")
        if not regex:
            raise ValueError(f"allow rule needs 'glob<TAB>regex': {raw!r}")
        rules.append(Rule(glob.strip(), re.compile(regex.strip())))
    return tuple(rules)


def skipped(path: str) -> bool:
    return path.startswith(SKIP) or path.endswith(".json")


def offends(text: str, path: str, rules: tuple[Rule, ...]) -> bool:
    """True when the line says an old name and no rule for this path lets it through."""
    if not PATTERN.search(text):
        return False
    allowed = [r.regex for r in rules if fnmatch.fnmatch(path, r.glob)]
    rest = text
    for regex in allowed:
        if regex.pattern == ".*":
            return False
        rest = regex.sub("", rest)
    return bool(PATTERN.search(rest))


def tracked(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if not skipped(p)]


def run(root: Path, rules: tuple[Rule, ...] | None = None) -> list[Hit]:
    rules = load_allow(ALLOW.read_text()) if rules is None else rules
    hits = []
    for path in tracked(root):
        file = root / path
        if not file.is_file():
            continue
        try:
            lines = file.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue  # images, fonts, models
        hits += [Hit(path, n, line.strip()) for n, line in enumerate(lines, 1) if offends(line, path, rules)]
    return hits


def main() -> int:
    hits = run(Path(__file__).resolve().parents[1])
    for h in hits:
        print(f"{h.path}:{h.line}: {h.text[:160]}")
    print(f"name audit: {len(hits)} line(s) still use an old name" if hits else "name audit: clean")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
