"""RAM and CPU of the Mac services (hub, voice helper, Laya, the Maxie app) for Settings › General.
Numbers come from `ps`; `ps_fn` is injectable for tests."""

from __future__ import annotations

import os
import re
import subprocess
import sys

SERVICES = [  # name, pgrep pattern (-f), exact process name (-x)
    ("hub", "maxie.hub.api", None),
    ("voice", "maxie.voice", None),
    ("laya", "deploy/laya_serve.py", None),
    ("maxie app", None, "Maxie"),
]


def _pids(pattern: str | None, exact: str | None) -> list[int]:
    args = ["pgrep", "-x", exact] if exact else ["pgrep", "-f", pattern or ""]
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    pids = [int(p) for p in out.split() if p.isdigit()]
    # macOS pgrep never lists its own parent, and the parent is the hub: add ourselves when the pattern
    # matches this process (else Settings says "hub off" while the hub answers).
    if pattern and re.search(pattern, " ".join(sys.argv)) and os.getpid() not in pids:
        pids.append(os.getpid())
    return pids


def _ps(pids: list[int]) -> list[tuple[int, float, float]]:
    """(pid, rss MB, %cpu) per pid."""
    if not pids:
        return []
    try:
        out = subprocess.run(["ps", "-o", "pid=,rss=,%cpu=", "-p", ",".join(map(str, pids))],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3:
            rows.append((int(parts[0]), round(int(parts[1]) / 1024, 1), float(parts[2])))
    return rows


def snapshot(pids_fn=_pids, ps_fn=_ps) -> dict:
    """{"services": [{name, running, pid, rss_mb, cpu_pct}], "total_rss_mb": n, "line": "..."}"""
    services, total = [], 0.0
    for name, pattern, exact in SERVICES:
        pids = pids_fn(pattern, exact)
        rows = ps_fn(pids) if pids else []
        if rows:
            pid, rss, cpu = max(rows, key=lambda r: r[1])
            total += rss
            services.append({"name": name, "running": True, "pid": pid, "rss_mb": rss, "cpu_pct": cpu})
        else:
            services.append({"name": name, "running": False, "pid": None, "rss_mb": 0.0, "cpu_pct": 0.0})
    line = ", ".join(f"{s['name']} {s['rss_mb']:.0f} MB / {s['cpu_pct']:.0f}%" if s["running"] else f"{s['name']} off"
                     for s in services)
    return {"services": services, "total_rss_mb": round(total, 1), "line": line}
