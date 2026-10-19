"""Render the mascot's notch loops: one seamless, transparent loop per state, head to feet.

    .venv/bin/python scripts/render_mascot.py                         # Maxie (Mochi), all states
    .venv/bin/python scripts/render_mascot.py idle talk               # some
    .venv/bin/python scripts/render_mascot.py --character classic     # the Classic robot
    .venv/bin/python scripts/render_mascot.py --custom '{"fur":"honey"}' --out DIR   # a customized Maxie

Run it under `taskpolicy -b` (make mascot does): it is GPU and CPU heavy for a minute or two.

Needs `uv pip install -p .venv playwright==1.56.0 pillow` and Chromium (preinstalled in the cloud;
`playwright install chromium` elsewhere). Deterministic: a fixed 30 fps clock, seeded randomness, every
motion snapped to whole cycles per loop, a pre-roll so springs settle. No wall time is used.

Output: assets/mascot/<state>/ (Maxie) or assets/mascot-classic/<state>/
    1x/000.png ... 30x50 px   2x/ 60x100   3x/ 90x150     (Maxie is 30x50 pt)
    noeyes/2x, noeyes/3x      round-eye states only: same frames without eyes; the Mac app draws
                              live eyes on top at the per-frame anchors in meta.json
    preview.png (APNG, 2x) + preview.webp (animated, 2x)
    meta.json                 fps, frame count, loop seconds, seam error, eye anchors (2x px)
"""

from __future__ import annotations

import argparse
import base64
import functools
import http.server
import io
import json
import shutil
import sys
import threading
from pathlib import Path

from PIL import Image, ImageChops
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
FPS = 30
SUPER = 6              # render at 6x the point size, then downsample (smooth edges)
CLASSIC_LOOPS = {"idle": 6.0, "listen": 2.0, "think": 2.0, "talk": 2.0, "happy": 3.0, "proud": 2.0, "sleep": 4.0, "gentle": 4.0}
# Per character: scripts, the JS global, notch size in points, loops (seconds), states that get live eyes.
CHARACTERS = {
    "maxie": {"name": "maxie", "scripts": ["/maxie-fur.js", "/maxie.js"], "global": "MaxieCharacter", "pt": (36, 42),
              "out": ROOT / "assets" / "mascot", "gpu": ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"],
              "loops": {**CLASSIC_LOOPS, "happy": 2.0, "wave": 2.0, "closed": 4.0, "stretch": 4.0},
              "round_eyes": {"idle", "listen", "think", "talk", "gentle", "wave"}},
    "classic": {"name": "classic", "scripts": ["/mascot.js"], "global": "ClaudeMascot", "pt": (30, 50),
                "out": ROOT / "assets" / "mascot-classic", "gpu": ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
                "loops": CLASSIC_LOOPS, "round_eyes": {"idle", "listen", "think", "talk", "gentle"}},
}
C = CHARACTERS["maxie"]  # set by main()
PT = C["pt"]
LOOPS = C["loops"]
ROUND_EYES = C["round_eyes"]
CUSTOM: dict = {}

PAGE = """<!doctype html><html><head><style>html,body{margin:0;background:transparent}</style></head><body>
<canvas id="c" width="%d" height="%d" style="width:%dpx;height:%dpx"></canvas>
<script src="/vendor/three.min.js"></script>%s</body></html>"""

JS_MAKE = """([state, loop, eyes, name, custom]) => {
  if (window.bot) window.bot.dispose();
  const old = document.getElementById('c'), c = old.cloneNode(false);  // fresh WebGL context per run
  old.replaceWith(c);
  window.bot = window[name].create(c, {look: 'sunset', framing: 'body', floor: false, interactive: false,
                                       loop, eyes, pixelRatio: 1, reduceMotion: false, seed: 7, custom});
  bot.setState(state);
  return true;
}"""
JS_STEP = """([n, fps]) => { for (let i = 0; i < n; i++) bot.advance(1 / fps); bot.render();
  return [document.getElementById('c').toDataURL('image/png'), bot.eyeAnchors()]; }"""


def serve(directory: Path) -> tuple[http.server.ThreadingHTTPServer, int]:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = functools.partial(Quiet, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def decode(data_url: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1]))).convert("RGBA")


def capture(page, state: str, eyes: bool) -> tuple[list[Image.Image], list[dict], float]:
    loop = LOOPS[state]
    n = round(loop * FPS)
    page.evaluate(JS_MAKE, [state, loop, eyes, C["global"], CUSTOM])
    page.evaluate(JS_STEP, [round(max(2 * loop, 4.0) / loop) * n, FPS])  # pre-roll whole loops so it settles
    frames, anchors = [], []
    for _ in range(n):
        url, a = page.evaluate(JS_STEP, [1, FPS])
        frames.append(decode(url))
        anchors.append(a)
    after = decode(page.evaluate(JS_STEP, [1, FPS])[0])  # frame n == frame 0 if the loop is seamless
    seam = max(ImageChops.difference(frames[0], after).getextrema()[c][1] for c in range(4)) / 255
    return frames, anchors, seam


def save(frames: list[Image.Image], folder: Path, scale: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    size = (PT[0] * scale, PT[1] * scale)
    for i, f in enumerate(frames):
        f.resize(size, Image.LANCZOS).save(folder / f"{i:03d}.png", optimize=True)


def render(states: list[str], OUT: Path, lean: bool = False) -> None:
    """lean: only what the Mac app loads (2x/3x frames and live-eye frames; no 1x, no previews), for the
    custom looks the hub re-renders from Settings › Maxie."""
    httpd, port = serve(WEB)
    w, h = PT[0] * SUPER, PT[1] * SUPER
    scripts = "".join(f'<script src="{s}"></script>' for s in C["scripts"])
    with sync_playwright() as p:
        browser = p.chromium.launch(args=C["gpu"])
        page = browser.new_page(viewport={"width": w, "height": h})
        page.route("**/render.html", lambda r: r.fulfill(content_type="text/html", body=PAGE % (w, h, w, h, scripts)))
        page.goto(f"http://127.0.0.1:{port}/render.html")
        page.wait_for_function(f"window.{C['global']} && window.THREE")
        for state in states:
            out = OUT / state
            shutil.rmtree(out, ignore_errors=True)
            frames, _, seam = capture(page, state, eyes=True)
            for scale in (2, 3) if lean else (1, 2, 3):
                save(frames, out / f"{scale}x", scale)
            if not lean:
                small = [f.resize((PT[0] * 2, PT[1] * 2), Image.LANCZOS) for f in frames]
                small[0].save(out / "preview.png", save_all=True, append_images=small[1:], duration=1000 // FPS,
                              loop=0, disposal=1, optimize=True)
                small[0].save(out / "preview.webp", save_all=True, append_images=small[1:], duration=1000 // FPS,
                              loop=0, lossless=True)
            meta = {"state": state, "fps": FPS, "frames": len(frames), "loop_seconds": LOOPS[state],
                    "size_pt": PT, "scales": [2, 3] if lean else [1, 2, 3], "seam_error": round(seam, 4),
                    "character": C["name"], "custom": CUSTOM or None}
            if state in ROUND_EYES:
                bare, anchors, _ = capture(page, state, eyes=False)
                save(bare, out / "noeyes" / "2x", 2)
                save(bare, out / "noeyes" / "3x", 3)  # the bigger bot outside the notch, still with live eyes
                k = 2 / SUPER  # anchors were measured on the SUPER canvas; store them in 2x pixels
                meta["eye_anchors_2x"] = [
                    {side: {key: round(a[side][key] * k, 2) for key in ("x", "y", "w", "h")} for side in ("left", "right")}
                    for a in anchors]
                meta["eye_color"] = anchors[0]["color"]
                meta["eye_style"] = anchors[0].get("style", "glow")
            (out / "meta.json").write_text(json.dumps(meta, indent=1))
            print(f"{state:7s} {len(frames)} frames, seam error {seam:.3f}")
        browser.close()
    httpd.shutdown()


def main(argv: list[str]) -> None:
    global C, PT, LOOPS, ROUND_EYES, CUSTOM
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("states", nargs="*")
    ap.add_argument("--character", choices=sorted(CHARACTERS), default="maxie")
    ap.add_argument("--custom", default="", help="Maxie's look as JSON (Settings › Maxie)")
    ap.add_argument("--out", type=Path, help="output folder (default: the character's assets folder)")
    ap.add_argument("--lean", action="store_true", help="only the frames the Mac app loads (custom looks)")
    a = ap.parse_args(argv)
    C = CHARACTERS[a.character]
    PT, LOOPS, ROUND_EYES = C["pt"], C["loops"], C["round_eyes"]
    CUSTOM = json.loads(a.custom) if a.custom else {}
    if CUSTOM.get("eyes", "glossy") != "glossy":
        ROUND_EYES = set()  # sparkle / sleepy / button eyes stay baked in; the app draws only glossy live eyes
    unknown = [s for s in a.states if s not in LOOPS]
    if unknown:
        sys.exit(f"unknown state(s) for {a.character}: {', '.join(unknown)}")
    render(a.states or list(LOOPS), a.out or C["out"], lean=a.lean)


if __name__ == "__main__":
    main(sys.argv[1:])
