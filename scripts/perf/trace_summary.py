"""Summarise an xctrace Time Profiler + Hangs recording: CPU by thread and by the hottest frames,
and every main-thread hang over 100 ms.

    taskpolicy -b .venv/bin/python scripts/perf/trace_summary.py <file.trace> [--seconds 60]
"""

from __future__ import annotations

import argparse
import collections
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

HANG_MS = 100


def export(trace: Path, schema: str) -> ET.Element:
    xpath = f'/trace-toc/run[@number="1"]/data/table[@schema="{schema}"]'
    out = subprocess.run(["xcrun", "xctrace", "export", "--input", str(trace), "--xpath", xpath],
                         capture_output=True, text=True, check=True).stdout
    return ET.fromstring(out) if out.strip() else ET.Element("empty")


def _resolve(el: ET.Element, ids: dict[str, ET.Element]) -> ET.Element:
    if "id" in el.attrib:
        ids[el.attrib["id"]] = el
    return ids.get(el.attrib["ref"], el) if "ref" in el.attrib else el


def samples(root: ET.Element) -> list[dict]:
    """One dict per sample: thread name, weight (ns) and frame names from the leaf up."""
    ids: dict[str, ET.Element] = {}
    for el in root.iter():  # first pass: every element with an id
        if "id" in el.attrib:
            ids[el.attrib["id"]] = el
    out = []
    for row in root.iter("row"):
        thread = weight = None
        frames: list[str] = []
        for child in row:
            c = _resolve(child, ids)
            if c.tag == "thread":
                thread = c.attrib.get("fmt", "?")
            elif c.tag == "weight":
                weight = int(c.text or 0)
            elif c.tag in ("backtrace", "tagged-backtrace"):
                frames = [_resolve(f, ids).attrib.get("name", "?") for f in c.iter("frame")]
        out.append({"thread": thread or "?", "weight": weight or 1_000_000, "frames": frames})
    return out


def _ours(frames: list[str]) -> str:
    """The first frame of the app's own code (a Swift method: "Type.method(...)"), else the leaf."""
    own = next((f for f in frames if "." in f and "(" in f and not f.startswith(("static MainActor", "closure", "partial", "thunk",
                                                                                   "specialized", "protocol witness"))), None)
    return own or (frames[0] if frames else "?")


def hangs(root: ET.Element) -> list[float]:
    ms = []
    for row in root.iter("row"):
        for el in row.iter("duration"):
            ms.append(int(el.text or 0) / 1e6)
    return [m for m in ms if m > HANG_MS]


def summary(trace: Path, seconds: float) -> dict:
    s = samples(export(trace, "time-profile"))
    total = sum(x["weight"] for x in s) / 1e9
    threads = collections.Counter()
    hot = collections.Counter()
    for x in s:
        threads[x["thread"].split(" ")[0] if "Main" not in x["thread"] else "Main Thread"] += x["weight"]
        hot[_ours(x["frames"])] += x["weight"]
    return {"cpu_pct": round(total / seconds * 100, 2), "cpu_s": round(total, 3),
            "threads": {k: round(v / 1e9 / seconds * 100, 2) for k, v in threads.most_common(6)},
            "hot": {k: round(v / 1e9 / seconds * 100, 2) for k, v in hot.most_common(12)},
            "hangs_over_100ms": hangs(export(trace, "potential-hangs"))}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("trace", type=Path)
    ap.add_argument("--seconds", type=float, default=60)
    a = ap.parse_args()
    print(json.dumps(summary(a.trace, a.seconds), indent=1))


if __name__ == "__main__":
    main()
