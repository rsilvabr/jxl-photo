#!/usr/bin/env python3
"""Unit tests for the resize/sharpen helpers shared by the transcoder and the
recompressor. The copies are pinned by tests/test_helper_parity.py; these tests
pin the arithmetic and the label spelling itself.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_jpeg_transcoder as tr
import jxl_recompressor as rec

MODULES = [tr, rec]
IDS = ["transcoder", "recompressor"]


@pytest.mark.parametrize("mod", MODULES, ids=IDS)
@pytest.mark.parametrize("w,h,mode,value,expected", [
    (8256, 5504, "long", 2048, (2048, 1365)),
    (5504, 8256, "long", 2048, (1365, 2048)),
    (8256, 5504, "short", 1080, (1620, 1080)),
    (8256, 5504, "percent", 50, (4128, 2752)),
    (1000, 800, "long", 2048, None),
    (1000, 800, "percent", 100, None),
    (1000, 800, None, 2048, None),
])
def test_resize_geometry(mod, w, h, mode, value, expected):
    assert mod._resize_geometry(w, h, mode, value) == expected


@pytest.mark.parametrize("mod", MODULES, ids=IDS)
def test_resize_geometry_upscale_only_with_the_flag(mod):
    assert mod._resize_geometry(1000, 800, "long", 2048, allow_upscale=True) == \
        (2048, 1638)


@pytest.mark.parametrize("mod", MODULES, ids=IDS)
@pytest.mark.parametrize("mode,value", [
    ("long", 0), ("long", -1), ("short", 0), ("short", -10),
    ("percent", 0), ("percent", -5),
])
def test_resize_geometry_rejects_non_positive(mod, mode, value):
    with pytest.raises(ValueError):
        mod._resize_geometry(1000, 800, mode, value)


@pytest.mark.parametrize("mod", MODULES, ids=IDS)
def test_derived_label(mod):
    assert mod._derived_label("sRGB", "long", 2048, False, "screen") == \
        "sRGB@long2048+screen"
    assert mod._derived_label(None, "percent", 50.0) == "keep@pct50"
    assert mod._derived_label("AdobeRGB") == "AdobeRGB"
    assert mod._derived_label(None, "short", 1080, True) == "keep@short1080+up"
    assert mod._derived_label("keep", "long", 320.0, False, "print") == \
        "keep@long320+print"


@pytest.mark.parametrize("mod", MODULES, ids=IDS)
def test_sharpen_args(mod):
    assert mod._sharpen_args("none") == []
    assert mod._sharpen_args(None) == []
    assert mod._sharpen_args("screen", grey=True) == ["-unsharp", "0x0.8+0.6+0.0"]
    assert mod._sharpen_args("print", grey=True) == ["-unsharp", "0x3.24+0.77+0.0"]
    assert mod._sharpen_args("screen") == [
        "-colorspace", "Lab", "-channel", "R", "-unsharp", "0x0.8+0.6+0.0",
        "+channel", "-colorspace", "sRGB"]
    assert mod._sharpen_args("screen", grey=True, sigma=2, gain=3,
                             threshold=0.1) == ["-unsharp", "0x2.0+3.0+0.1"]
