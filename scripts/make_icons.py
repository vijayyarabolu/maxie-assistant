"""App, home-screen and push icons from the real Maxie (his pick: Mochi, no headphones).
Run: taskpolicy -b .venv/bin/python scripts/make_icons.py

Renders web/maxie.js headless (off-screen, same setup as render_mascot.py) and writes
web/icon-{180,192,512}.png (Maxie on a soft periwinkle-to-cream background), web/badge-72.png (white
silhouette for notification badges) and mac/ClaudeNotch/Resources/AppIcon.icns (the Mac app icon, so
notifications and Finder show Maxie).
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter
from playwright.sync_api import sync_playwright
from render_mascot import CHARACTERS, ROOT, WEB, decode, serve

SIZE = 1024
SCRIPTS = "".join(f'<script src="{s}"></script>' for s in CHARACTERS["maxie"]["scripts"])
PAGE = f"""<!doctype html><html><body style="margin:0;background:transparent">
<canvas id="c" width="{SIZE}" height="{SIZE}" style="width:{SIZE}px;height:{SIZE}px"></canvas>
<script src="/vendor/three.min.js"></script>{SCRIPTS}</body></html>"""
TOP, BOTTOM = (226, 231, 255), (255, 244, 232)  # periwinkle mist to warm cream: Mochi's fur tip and muzzle
MAC_ICON = ROOT / "mac" / "ClaudeNotch" / "Resources" / "AppIcon.icns"
ICONSET = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2)]


def background(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size))
    d = ImageDraw.Draw(img)
    for y in range(size):
        k = y / (size - 1)
        d.line([(0, y), (size, y)], fill=tuple(round(a + (b - a) * k) for a, b in zip(TOP, BOTTOM, strict=True)) + (255,))
    return img


def shoot(page, framing: str) -> Image.Image:
    url = page.evaluate("""(framing) => {
      const old = document.getElementById('c'), c = old.cloneNode(false); old.replaceWith(c);
      const b = MaxieCharacter.create(c, {framing, floor: false, interactive: false, loop: 6, shells: 56,
                                          pixelRatio: 1, reduceMotion: false, seed: 7});
      b.setState('idle'); for (let i = 0; i < 90; i++) b.advance(1 / 30); b.render();
      return c.toDataURL('image/png'); }""", framing)
    return decode(url)


def mac_icon(bot: Image.Image) -> Image.Image:
    """macOS shape: an 824 pt rounded square on the 1024 canvas, a soft shadow, Maxie inside."""
    inset, radius = 100, 185
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([inset, inset, SIZE - inset, SIZE - inset], radius, fill=255)
    tile = background(SIZE)
    face = bot.resize((SIZE - 2 * inset,) * 2, Image.LANCZOS)
    tile.alpha_composite(face, (inset, inset + 18))
    tile.putalpha(mask)
    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    shadow.putalpha(mask.point(lambda a: a * 70 // 255).filter(ImageFilter.GaussianBlur(18)))
    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    out.alpha_composite(shadow, (0, 12))
    out.alpha_composite(tile)
    return out


def write_icns(icon: Image.Image) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "AppIcon.iconset"
        folder.mkdir()
        for pt, scale in ICONSET:
            name = f"icon_{pt}x{pt}{'@2x' if scale == 2 else ''}.png"
            icon.resize((pt * scale,) * 2, Image.LANCZOS).save(folder / name)
        MAC_ICON.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["iconutil", "-c", "icns", str(folder), "-o", str(MAC_ICON)], check=True)
        shutil.copy(folder / "icon_512x512@2x.png", MAC_ICON.with_suffix(".png"))  # a preview for PRs and docs


def main() -> None:
    httpd, port = serve(WEB)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=CHARACTERS["maxie"]["gpu"])
        page = browser.new_page(viewport={"width": SIZE, "height": SIZE})
        page.route("**/icon.html", lambda r: r.fulfill(content_type="text/html", body=PAGE))
        page.goto(f"http://127.0.0.1:{port}/icon.html")
        page.wait_for_function("window.MaxieCharacter && window.THREE")
        shots = {name: shoot(page, name) for name in ("portrait", "body")}
        browser.close()
    httpd.shutdown()

    bot = shots["portrait"]
    for n in (180, 192, 512):
        icon = background(SIZE)
        icon.alpha_composite(bot)
        icon.convert("RGB").resize((n, n), Image.LANCZOS).save(WEB / f"icon-{n}.png", optimize=True)
    alpha = shots["body"].getchannel("A").resize((72, 72), Image.LANCZOS)
    badge = Image.new("RGBA", (72, 72), (255, 255, 255, 0))
    badge.putalpha(alpha.point(lambda a: 255 if a > 96 else 0))
    badge.save(WEB / "badge-72.png", optimize=True)
    write_icns(mac_icon(bot))
    print("icons written:", ", ".join(sorted(f.name for f in Path(WEB).glob("*.png"))), "and", MAC_ICON.name)


if __name__ == "__main__":
    main()
