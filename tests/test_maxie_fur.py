"""Maxie's fur moves like hair, with no dots floating around him, and he stays exactly the Mochi he
picked. Measured on the real web character in headless Chromium (off-screen) by scripts/maxie/fur_check.py.
Before the fix (screen-space random discard at the tips): speckle ~58-63, stray ink 6 (notch) / 118 (panel)."""

import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("playwright.sync_api")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "maxie"))
import fur_check  # noqa: E402

MOCHI_RGB = {"notch": [0.53, 0.557, 0.703], "panel": [0.532, 0.559, 0.708]}  # his pick, as rendered before the fix


@pytest.fixture(scope="module")
def fur():
    try:
        return fur_check.measure(sizes={k: fur_check.SIZES[k] for k in ("notch", "panel")})
    except Exception as e:  # no Chromium for Playwright on this machine
        pytest.skip(f"headless Chromium unavailable: {e}")


@pytest.mark.parametrize("size", ["notch", "panel"])
def test_no_speckle_when_he_moves(fur, size):
    assert fur[size]["speckle"] < 45, fur[size]


@pytest.mark.parametrize("size,most", [("notch", 2.0), ("panel", 25.0)])
def test_nothing_floats_around_his_outline(fur, size, most):
    assert fur[size]["halo"] <= most, fur[size]


@pytest.mark.parametrize("size", ["notch", "panel"])
def test_still_the_same_mochi(fur, size):
    assert np.allclose(fur[size]["rgb"], MOCHI_RGB[size], atol=0.012), fur[size]
    assert fur[size]["cover"] > 0.34


@pytest.mark.parametrize("size", ["notch", "panel"])
def test_still_means_still(fur, size):
    assert fur[size]["still"] < 0.015, fur[size]


def test_speckle_metric_sees_random_dots():
    from PIL import Image
    rng = np.random.default_rng(1)
    base = np.zeros((40, 40, 4), np.uint8)
    base[8:32, 8:32] = [120, 130, 200, 255]
    a, b = base.copy(), base.copy()
    a[8:32, 8:32, 3] = rng.integers(0, 2, (24, 24)) * 255
    b[8:32, 8:32, 3] = rng.integers(0, 2, (24, 24)) * 255
    noisy = fur_check.speckle(Image.fromarray(a), Image.fromarray(b))
    smooth = fur_check.speckle(Image.fromarray(base), Image.fromarray(np.roll(base, 1, axis=1)))
    assert noisy > 5 * smooth


def test_halo_counts_only_detached_specks():
    pytest.importorskip("scipy")
    from PIL import Image
    img = np.zeros((30, 30, 4), np.uint8)
    img[5:20, 5:20] = [100, 100, 200, 255]
    img[20:23, 12] = [100, 100, 200, 120]  # a feathered strand still attached: not a speck
    assert fur_check.halo(Image.fromarray(img)) == 0
    img[27, 27] = [255, 255, 255, 255]  # a lone dot
    assert fur_check.halo(Image.fromarray(img)) == 1.0
