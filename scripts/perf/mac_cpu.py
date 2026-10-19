"""Real-screen CPU of the running Maxie app, only while he is away.

    taskpolicy -b .venv/bin/python scripts/perf/mac_cpu.py --label after [--wait] [--swap]

Waits (with --wait) until he has been idle 10+ minutes, nothing is full-screen and no video is playing; then
measures 60 s each: idle (Maxie only), panel open, Settings › General, Settings › Maxie (live preview), plus a
Time Profiler + Hangs trace of the idle minute. The moment he touches the Mac it closes what it opened and stops.
--swap first measures the running build ("before"), then relaunches the freshly built one in the background
(no focus) and measures it ("after"). Writes perf/<label>.json under the data folder.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path.home() / ".maxie" / "perf"
BIN = Path("/tmp") / "maxie-perf-bin"
APP = ROOT / "mac/ClaudeNotch/build/Build/Products/Debug/Maxie.app"
AWAY_S = 600
SECONDS = 60
SCENARIOS = (("idle", []), ("panel", ["open"]), ("settings_general", ["close", "settings:General"]),
             ("settings_maxie", ["settings:Maxie"]))


class Back(Exception):
    """He came back: stop at once."""


def tool(name: str) -> Path:
    BIN.mkdir(exist_ok=True)
    exe = BIN / name
    src = Path(__file__).with_name(f"{name}.swift")
    if not exe.exists() or exe.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(["xcrun", "swiftc", "-O", str(src), "-o", str(exe)], check=True, capture_output=True)
    return exe


def away() -> bool:
    idle, full, dark = (int(x) for x in subprocess.run([tool("macgate")], capture_output=True, text=True).stdout.split())
    video = re.search(r"PreventUserIdleDisplaySleep\s+1", subprocess.run(["pmset", "-g", "assertions"],
                                                                        capture_output=True, text=True).stdout)
    return idle >= AWAY_S and full == 0 and not video and not dark  # unlocked, displays on, nobody there


def remote(cmd: str) -> None:
    subprocess.run([tool("macremote"), cmd], check=True)


def pid() -> int | None:
    out = subprocess.run(["pgrep", "-x", "Maxie"], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else None


def webcontent() -> set[int]:
    out = subprocess.run(["pgrep", "-f", "com.apple.WebKit.WebContent"], capture_output=True, text=True).stdout
    return {int(p) for p in out.split()}


def cputime(pids: list[int]) -> float:
    if not pids:
        return 0.0
    out = subprocess.run(["ps", "-o", "cputime=", "-p", ",".join(map(str, pids))], capture_output=True, text=True).stdout
    total = 0.0
    for t in out.split():
        parts = [float(x) for x in t.replace("-", ":").split(":")]
        total += sum(v * 60 ** i for i, v in enumerate(reversed(parts)))
    return total


def watch(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(5)
        if not away():
            raise Back


def measure(app: int, steps: list[str], before_web: set[int]) -> dict:
    for s in steps:
        remote(s)
    watch(4)  # let it open and settle
    web = sorted(webcontent() - before_web)  # the web views this app just started
    a0, w0, t0 = cputime([app]), cputime(web), time.time()
    watch(SECONDS)
    dt = time.time() - t0
    return {"app_pct": round((cputime([app]) - a0) / dt * 100, 2), "webkit_pct": round((cputime(web) - w0) / dt * 100, 2),
            "web_processes": len(web)}


def trace(app: int, label: str) -> subprocess.Popen:
    out = OUT / f"{label}-idle.trace"
    subprocess.run(["rm", "-rf", str(out)])
    return subprocess.Popen(["xcrun", "xctrace", "record", "--template", "Time Profiler", "--instrument", "Hangs",
                             "--attach", str(app), "--time-limit", f"{SECONDS}s", "--output", str(out), "--no-prompt"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run(label: str, old: bool = False) -> dict:
    """old: a build from before the Settings remote commands; it only knows open/close (anything else toggles)."""
    app = pid()
    if app is None:
        return {"error": "Maxie isn't running"}
    remote("close")
    if not old:
        remote("settings-close")
    base_web = webcontent()
    result = {"label": label, "pid": app, "at": time.strftime("%Y-%m-%d %H:%M")}
    try:
        tracer = trace(app, label)
        for name, steps in SCENARIOS[:2] if old else SCENARIOS:
            result[name] = measure(app, steps, base_web)
        tracer.wait(timeout=30)
    except Back:
        result["stopped"] = "he came back"
    finally:
        if not old:
            remote("settings-close")
        remote("close")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{label}.json").write_text(json.dumps(result, indent=1))
    return result


def relaunch() -> None:
    app = pid()
    if app:
        subprocess.run(["kill", str(app)])
        time.sleep(3)
    subprocess.run(["open", "-g", str(APP)], check=True)  # -g: in the background, never takes focus
    time.sleep(15)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--label", default="after")
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--swap", action="store_true")
    a = ap.parse_args()
    while a.wait and not away():
        time.sleep(60)
    if not away():
        print("He's at the Mac: nothing measured (queue it in MORNING.md).")
        return
    if a.swap:
        print(json.dumps(run("before", old=True)))
        if not away():
            return
        relaunch()
    print(json.dumps(run(a.label)))


if __name__ == "__main__":
    main()
