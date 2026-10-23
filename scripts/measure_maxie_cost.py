"""What Maxie costs the Mac: CPU %, energy impact and memory of Maxie's own processes (the notch app
and the WebKit helpers it started, the hub, the voice helper, Laya), WindowServer, and the whole Mac, averaged
over `top` samples. Run it with Maxie running and again with Maxie stopped; the difference is Maxie.

Usage: .venv/bin/python scripts/measure_maxie_cost.py [--seconds 60] [--label on] [--out f.jsonl]
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import defaultdict
from pathlib import Path

SAMPLE_S = 5
GROUPS = (("maxie.hub.api", "hub"), ("maxie.voice", "voice"), ("laya_serve", "laya"), ("Maxie.app", "app"))


def _ps(*args: str) -> str:
    return subprocess.run(["ps", *args], capture_output=True, text=True).stdout


def maxie_pids() -> dict[int, str]:
    """pid → group for Maxie's processes, plus the WebKit helpers started with the notch app (same minute)."""
    pids: dict[int, str] = {}
    for line in _ps("-Ao", "pid,command").splitlines()[1:]:
        pid, _, cmd = line.strip().partition(" ")
        group = next((g for key, g in GROUPS if key in cmd), None)
        if group:
            pids[int(pid)] = group
    app = [p for p, g in pids.items() if g == "app"]
    if app:
        started = _ps("-o", "lstart=", "-p", str(app[0])).strip()[:16]
        for line in _ps("-Ao", "pid,lstart,command").splitlines()[1:]:
            if "com.apple.WebKit" in line:
                pid = int(line.split()[0])
                if _ps("-o", "lstart=", "-p", str(pid)).strip()[:16] == started:
                    pids[pid] = "app"
    return pids


def _mb(m: str) -> float:
    m = m.rstrip("+-")
    try:
        v = float(m[:-1])
    except ValueError:
        return 0.0
    return {"K": v / 1024, "M": v, "G": v * 1024}.get(m[-1:], 0.0)


def sample(seconds: int) -> dict:
    n = max(2, seconds // SAMPLE_S + 1)
    raw = subprocess.run(["top", "-l", str(n), "-s", str(SAMPLE_S), "-n", "1000", "-stats", "pid,cpu,power,mem,command"],
                         capture_output=True, text=True).stdout
    blocks = raw.split("\nPID")[2:]  # the first sample has no CPU deltas
    pids = maxie_pids()
    sums: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    mem: dict[str, float] = defaultdict(float)
    for block in blocks:
        for line in block.splitlines()[1:]:
            parts = line.split(None, 4)
            if len(parts) < 5 or not parts[0].isdigit():
                continue
            pid, cmd = int(parts[0]), parts[4]
            group = pids.get(pid) or ("WindowServer" if cmd.startswith("WindowServer") else None)
            for g in ([group] if group else []) + ["total"]:
                sums[g][0] += float(parts[1])
                sums[g][1] += float(parts[2])
            if group:
                mem[group] += _mb(parts[3]) / len(blocks)
    k = max(len(blocks), 1)
    out = {g: {"cpu": round(c / k, 1), "energy": round(p / k, 1), "mem_mb": round(mem.get(g, 0))} for g, (c, p) in sums.items()}
    mine = [g for _, g in GROUPS if g in out]
    out["maxie"] = {key: round(sum(out[g][key] for g in mine), 1) for key in ("cpu", "energy", "mem_mb")}
    out["samples"] = k
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--label", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    result = {"label": args.label, **sample(args.seconds)}
    print(json.dumps(result))
    if args.out:
        with Path(args.out).open("a") as f:
            f.write(json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
