"""How clean and how alive Maxie's fur is, measured on the real web character, headless.

    taskpolicy -b .venv/bin/python scripts/maxie/fur_check.py [--out DIR] [--gif]

For each size (the notch at 36x42 pt @2x, the free bot small / medium / large, the panel's live view) it renders
the character with no supersampling (the live views don't have any) and reports:
- still: the mean change between two frames 1/30 s apart when he is perfectly still (reduced motion). ~0.
- speckle: the high-frequency part of the change between two frames 1/30 s apart while he breathes (idle).
  Soft fur moving in waves changes smoothly; dots that pop in and out of the tips are pure high frequency.
- halo: ink in stray pixels floating around his outline (visible, but not connected to his body).
--out writes a still per size; --gif adds a 3-second GIF of the medium bot.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render_mascot import CHARACTERS, WEB, decode, serve  # noqa: E402

SIZES = {"notch": (72, 84), "small": (90, 105), "medium": (108, 126), "large": (130, 151), "panel": (168, 248)}
FPS = 30
PAGE = """<!doctype html><html><head><style>html,body{margin:0;background:transparent}</style></head><body>
<canvas id="c" width="%d" height="%d" style="width:%dpx;height:%dpx"></canvas>
<script src="/vendor/three.min.js"></script>%s</body></html>"""
JS_MAKE = """([still, loop]) => {
  if (window.bot) window.bot.dispose();
  const old = document.getElementById('c'), c = old.cloneNode(false); old.replaceWith(c);
  window.bot = MaxieCharacter.create(c, {framing: 'body', floor: false, interactive: false, loop, pixelRatio: 1,
                                         reduceMotion: still, seed: 7, shells: 32});
  bot.setState('idle'); for (let i = 0; i < 90; i++) bot.advance(1 / 30); bot.render(); return true; }"""
JS_STEP = """(n) => { for (let i = 0; i < n; i++) bot.advance(1 / 30); bot.render();
  return document.getElementById('c').toDataURL('image/png'); }"""


def rgba(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGBA"), dtype=np.float32) / 255.0


def still_diff(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(a - b).mean())


def _premultiplied(p: np.ndarray) -> np.ndarray:
    return np.concatenate([p[..., :3] * p[..., 3:], p[..., 3:]], axis=-1)


def _blur(channel: np.ndarray, radius: float) -> np.ndarray:
    img = Image.fromarray(((channel + 1) * 127.5).clip(0, 255).astype(np.uint8))
    return np.asarray(img.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32) / 127.5 - 1


def speckle(a: Image.Image, b: Image.Image) -> float:
    """Mean high-frequency change (premultiplied colour + alpha) inside his footprint, per pixel, x1000."""
    pa, pb = rgba(a), rgba(b)
    d = _premultiplied(pa) - _premultiplied(pb)
    smooth = np.stack([_blur(d[..., c], 1.2) for c in range(4)], axis=-1)
    box = (pa[..., 3] > 0.02) | (pb[..., 3] > 0.02)
    return float(np.abs(d - smooth)[box].mean() * 1000) if box.any() else 0.0


def halo(img: Image.Image) -> float:
    """Specks floating around him: how much ink (summed alpha) sits in visible pixels (alpha > 0.08) that are
    not connected to his body (8-connected). A soft fringe still attached to strands doesn't count, a dot does."""
    from scipy import ndimage
    alpha = rgba(img)[..., 3]
    labels, n = ndimage.label(alpha > 0.08, structure=np.ones((3, 3)))
    if n <= 1:
        return 0.0
    main = np.bincount(labels.ravel())[1:].argmax() + 1
    return round(float(alpha[(labels > 0) & (labels != main)].sum()), 1)


def look(img: Image.Image) -> dict:
    """How he looks overall, so a fur change can be held to "the same Mochi": mean colour over a dark field
    inside his footprint, and how much of the frame he covers."""
    p = rgba(img)
    a = p[..., 3:]
    over = p[..., :3] * a + np.array([24, 24, 30], dtype=np.float32) / 255 * (1 - a)
    inside = p[..., 3] > 0.5
    return {"rgb": [round(float(c), 3) for c in over[inside].mean(axis=0)], "cover": round(float((p[..., 3] > 0.5).mean()), 3)}


def _size(page, port: int, scripts: str, w: int, h: int, name: str, out: Path | None, gif: bool) -> dict:
    page.route("**/fur.html", lambda r: r.fulfill(content_type="text/html", body=PAGE % (w, h, w, h, scripts)))
    page.goto(f"http://127.0.0.1:{port}/fur.html")
    page.wait_for_function("window.MaxieCharacter && window.THREE")
    page.evaluate(JS_MAKE, [True, 0])
    s0, s1 = decode(page.evaluate(JS_STEP, 0)), decode(page.evaluate(JS_STEP, 1))
    page.evaluate(JS_MAKE, [False, 6])
    m = [decode(page.evaluate(JS_STEP, 1)) for _ in range(6)]
    if out:
        out.mkdir(parents=True, exist_ok=True)
        m[0].save(out / f"{name}.png")
        if gif and name == "medium":
            frames = [decode(page.evaluate(JS_STEP, 1)) for _ in range(3 * FPS)]
            flat = [Image.alpha_composite(Image.new("RGBA", f.size, (24, 24, 30, 255)), f).convert("RGB") for f in frames]
            flat[0].save(out / "medium-3s.gif", save_all=True, append_images=flat[1:], duration=1000 // FPS, loop=0)
    return {**look(m[0]), "still": round(still_diff(rgba(s0), rgba(s1)), 5),
            "speckle": round(float(np.mean([speckle(m[i], m[i + 1]) for i in range(5)])), 3),
            "halo": round(float(np.mean([halo(f) for f in m])), 1)}


def measure(out: Path | None = None, gif: bool = False, sizes: dict | None = None) -> dict:
    httpd, port = serve(WEB)
    scripts = "".join(f'<script src="{s}"></script>' for s in CHARACTERS["maxie"]["scripts"])
    result = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=CHARACTERS["maxie"]["gpu"])
            for name, (w, h) in (sizes or SIZES).items():
                page = browser.new_page(viewport={"width": w, "height": h})
                result[name] = _size(page, port, scripts, w, h, name, out, gif)
                page.close()
            browser.close()
    finally:
        httpd.shutdown()
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path)
    ap.add_argument("--gif", action="store_true")
    a = ap.parse_args()
    print(json.dumps(measure(a.out, a.gif), indent=1))


if __name__ == "__main__":
    main()
