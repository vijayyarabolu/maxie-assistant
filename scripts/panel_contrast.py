"""WCAG contrast of body text in a panel capture: glyph cores vs the worst background behind the words.
Region: the reading area (below the tabs, above the composer). Text mask = pixels far from the local
background, dilated 4 px; worst background = 99.5th percentile toward the text's luminance.

    .venv/bin/python scripts/panel_contrast.py 225,760,900,1095 shots/panel-*.png
"""
import json
import sys

import numpy as np
from PIL import Image, ImageFilter


def lum(a: np.ndarray) -> np.ndarray:
    c = a / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * c[..., 0] + 0.7152 * c[..., 1] + 0.0722 * c[..., 2]

def measure(path: str, box: tuple[int, int, int, int]) -> dict:
    im = Image.open(path).convert("RGB")
    a = np.asarray(im.crop(box)).astype(float)
    L = lum(a)
    med = np.median(L)
    dark_bg = med < 0.3
    # text = pixels clearly brighter (dark mode) / darker (light mode) than the background
    diff = (L - med) if dark_bg else (med - L)
    text = diff > 0.25
    mask = Image.fromarray((text * 255).astype("uint8")).filter(ImageFilter.MaxFilter(9))
    bgmask = np.asarray(mask) == 0
    bg = L[bgmask]
    worst_bg = np.percentile(bg, 99.5) if dark_bg else np.percentile(bg, 0.5)
    core = np.sort(L[text])
    t = np.median(core[-max(1, len(core)//10):]) if dark_bg else np.median(core[:max(1, len(core)//10)])
    hi, lo = max(t, worst_bg), min(t, worst_bg)
    return {"text_L": round(float(t), 3), "worst_bg_L": round(float(worst_bg), 3), "median_bg_L": round(float(med), 3),
            "contrast": round((hi + 0.05) / (lo + 0.05), 1)}

if __name__ == "__main__":
    box = tuple(int(v) for v in sys.argv[1].split(","))
    out = {p.rsplit("/", 1)[-1]: measure(p, box) for p in sys.argv[2:]}
    print(json.dumps(out, indent=1))
