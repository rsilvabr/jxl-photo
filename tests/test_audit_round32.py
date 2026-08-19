"""Round 32 — an RGB ICC was attached to single-channel outputs (bug #313).

`--to-srgb` / `--icc-profile` on the JXL -> PNG/JPEG path handed every decoded
image to `magick -profile <sRGB.icc>`. ImageMagick attaches the profile without
converting the image, so a **grayscale** source came out as a grayscale file
carrying an RGB profile:

    PNG warning: iCCP: profile 'icc': 'RGB ': RGB color space not permitted
                 on grayscale PNG

PNG requires the iCCP profile's data colour space to match the colour type, and
a 1-component JPEG with an sRGB profile is wrong the same way. It hits every
film-scan IR page and every grayscale scan.

The encoder learned this first: it does not apply an inherited RGB ICC to a
grayscale page, "which prevents libpng iCCP errors on scanner IR/mask pages"
(README_jxl_tiff_encoder). The transcoder never did.
"""
from __future__ import annotations

import importlib.util
import struct
import sys
import zlib
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tr():
    return _load("tr_r32", REPO / "jxl_jpeg_transcoder.py")


@pytest.fixture(scope="module")
def enc():
    # make_png_bytes maps 1/2/3/4 channels onto PNG colour types 0/4/2/6,
    # which is exactly the axis under test.
    return _load("enc_r32", REPO / "jxl_tiff_encoder.py")


def _png(enc_mod, tmp_path: Path, channels: int, name: str) -> Path:
    arr = np.zeros((8, 8, channels), dtype=np.uint16)
    if channels == 1:
        arr = arr[:, :, 0]
    p = tmp_path / name
    p.write_bytes(enc_mod.make_png_bytes(arr))
    return p


def _colour_type(p: Path) -> int:
    return p.read_bytes()[25]


# ---------------------------------------------------------------------------
# the probe
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("channels,expected_type,grey", [
    (1, 0, True),    # grayscale
    (2, 4, True),    # grayscale + alpha
    (3, 2, False),   # RGB
    (4, 6, False),   # RGBA
])
def test_png_is_grayscale_matches_the_colour_type(
        tr, enc, tmp_path, channels, expected_type, grey):
    p = _png(enc, tmp_path, channels, f"c{channels}.png")
    assert _colour_type(p) == expected_type
    assert tr._png_is_grayscale(p) is grey


def test_png_probe_is_false_on_a_missing_or_bogus_file(tr, tmp_path):
    """A probe that cannot read must not claim grayscale — that would silently
    drop the profile from a colour image."""
    assert tr._png_is_grayscale(tmp_path / "nope.png") is False
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"not a png at all")
    assert tr._png_is_grayscale(junk) is False


# ---------------------------------------------------------------------------
# the decision
# ---------------------------------------------------------------------------

def test_grayscale_gets_no_profile_argument(tr, enc, tmp_path):
    p = _png(enc, tmp_path, 1, "grey.png")
    args = tr._icc_args_for(p, "sRGB", ["-depth", "8"])
    assert "-profile" not in args
    assert "-colorspace" not in args
    assert args == ["-depth", "8"]


def test_colour_still_gets_the_profile(tr, enc, tmp_path):
    p = _png(enc, tmp_path, 3, "rgb.png")
    args = tr._icc_args_for(p, "sRGB", ["-depth", "8"])
    assert "-profile" in args or "-colorspace" in args
    assert args[-2:] == ["-depth", "8"]


def test_a_named_profile_file_is_also_skipped_for_grayscale(tr, enc, tmp_path):
    """--icc-profile <file> takes the same path as --to-srgb."""
    icc = tmp_path / "custom.icc"
    icc.write_bytes(b"\x00" * 200)
    grey = _png(enc, tmp_path, 1, "g.png")
    colour = _png(enc, tmp_path, 3, "c.png")
    assert tr._icc_args_for(grey, str(icc), ["-quality", "92"]) == ["-quality", "92"]
    assert tr._icc_args_for(colour, str(icc), ["-quality", "92"]) == [
        "-profile", str(icc), "-quality", "92"]


def test_gray_alpha_is_treated_as_grayscale(tr, enc, tmp_path):
    """Colour type 4 is single-channel plus alpha — an RGB profile is just as
    invalid there as on type 0."""
    p = _png(enc, tmp_path, 2, "la.png")
    assert tr._icc_args_for(p, "sRGB", ["-depth", "16"]) == ["-depth", "16"]


# ---------------------------------------------------------------------------
# the shape of a real output, without needing ImageMagick
# ---------------------------------------------------------------------------

def _iccp_space(png: Path):
    """Data colour space recorded in the PNG's iCCP chunk, or None."""
    d = png.read_bytes()
    i = 8
    while i < len(d):
        n = struct.unpack(">I", d[i:i + 4])[0]
        typ = d[i + 4:i + 8]
        if typ == b"iCCP":
            payload = d[i + 8:i + 8 + n]
            raw = zlib.decompress(payload[payload.index(b"\x00") + 2:])
            return raw[16:20].decode("latin1")
        i += 8 + n + 4
    return None


def test_an_rgb_profile_on_a_grey_png_is_what_we_refuse_to_write(enc, tmp_path):
    """Pins the shape of the defect itself, so the assertion above is anchored
    to something real: an RGB profile inside a colour-type-0 PNG."""
    grey = np.zeros((8, 8), dtype=np.uint16)
    icc = b"\x00" * 16 + b"RGB " + b"\x00" * 108
    bad = tmp_path / "bad.png"
    bad.write_bytes(enc.make_png_bytes(grey, icc_bytes=icc))
    assert _colour_type(bad) == 0
    assert _iccp_space(bad) == "RGB "      # exactly what libpng rejects
