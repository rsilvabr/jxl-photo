"""v2.25.0: the TIFF's byte order (II / MM) is kept end to end.

Until v2.24.x the encoder forced every master's EXIF to little-endian
(-ExifByteOrder=Little-endian, a line from the first version with no reason
recorded), and the copies came out big-endian: the recompressor and the
transcoder copy the tags with -tagsfromfile into a file without EXIF, and
exiftool writes a NEW EXIF block big-endian. Now the master keeps the TIFF's
order, every copy keeps its master's, and the decoder writes the TIFF in the
master's order. Plus: a JPEG derivative records its quality (/q70) in its
recipe, so a new quality re-derives it; a recipe without one (written before)
is kept.

Real codecs and exiftool, in a folder named with Japanese, brackets, a space
and %.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

REPO = Path(os.environ.get("JXLPHOTO_TEST_REPO") or Path(__file__).resolve().parent.parent)
HOSTILE = "吾妻山 [order] 100%"
_TOOLS = ("cjxl", "djxl", "exiftool", "magick")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")
ORDERS = [pytest.param(">", "Big-endian (Motorola, MM)", id="MM"),
          pytest.param("<", "Little-endian (Intel, II)", id="II")]


def _run(tmp_path, script, *args):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs")}
    r = subprocess.run([sys.executable, str(REPO / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, env=env, stdin=subprocess.DEVNULL)
    out = " ".join((r.stdout + r.stderr).split())
    assert r.returncode == 0, out[-3000:]
    return out


def _exiftool(*args):
    af = Path(os.environ.get("TEMP", ".")) / f"eorder_{os.getpid()}.args"
    af.write_text("-charset\nfilename=utf8\n" + "\n".join(map(str, args)) + "\n",
                  encoding="utf-8")
    try:
        return subprocess.run(["exiftool", "-@", str(af)], capture_output=True)
    finally:
        af.unlink()


def _tags(path, *tags):
    out = _exiftool("-j", *[f"-{t}" for t in tags], path).stdout
    return json.loads(out.decode("utf-8"))[0]


def _byte_order(path):
    return _tags(path, "ExifByteOrder").get("ExifByteOrder")


def _tiff(path, byteorder):
    rng = np.random.default_rng(7)
    a = rng.integers(0, 65535, (96, 128, 3)).astype(np.uint16)
    tifffile.imwrite(path, a, photometric="rgb", byteorder=byteorder,
                     software="Capture One 23", metadata=None)
    _exiftool("-overwrite_original", "-EXIF:Make=NIKON CORPORATION",
              "-EXIF:Model=NIKON Z 7", "-EXIF:ISO=64", path)


def _master(tmp_path, byteorder):
    d = tmp_path / HOSTILE
    d.mkdir()
    tif = d / "DSC_0001 (2).tif"
    _tiff(tif, byteorder)
    _run(tmp_path, "jxl_tiff_encoder.py", d, "--distance", "1", "--effort", "3",
         "--no-preflight")
    return d, tif, d / "DSC_0001 (2).jxl"


@needs_tools
@pytest.mark.parametrize("byteorder,expected", ORDERS)
def test_the_master_keeps_the_tiffs_byte_order(tmp_path, byteorder, expected):
    d, tif, jxl = _master(tmp_path, byteorder)
    assert _byte_order(tif) == expected
    assert _byte_order(jxl) == expected
    t = _tags(jxl, "EXIF:Make", "EXIF:ISO", "EXIF:Software")
    assert (t.get("Make"), t.get("ISO"), t.get("Software")) == \
        ("NIKON CORPORATION", 64, "Capture One 23"), t


@needs_tools
@pytest.mark.parametrize("byteorder,expected", ORDERS)
def test_copies_and_the_decoded_tiff_keep_the_masters_byte_order(tmp_path, byteorder,
                                                                  expected):
    d, tif, jxl = _master(tmp_path, byteorder)
    # A 16-bit sRGB copy (recompressor derivative), into its own folder.
    srgb = tmp_path / "sRGB [copy] 京都"
    _run(tmp_path, "jxl_recompressor.py", jxl, srgb, "--mode", "0",
         "--output-icc", "sRGB", "--distance", "2", "--effort", "3")
    copy = next(srgb.glob("*.jxl"))
    assert _byte_order(copy) == expected
    assert _tags(copy, "EXIF:Make").get("Make") == "NIKON CORPORATION"
    # A resized, sharpened JPEG (transcoder derivative).
    _run(tmp_path, "jxl_jpeg_transcoder.py", d, "--mode", "1", "--force-convert",
         "--decode", "--format", "jpeg", "--icc-profile", "sRGB",
         "--resize-long", "64", "--sharpen", "screen", "--quality", "80")
    jpg = next(d.rglob("*.jpg"))
    assert _byte_order(jpg) == expected
    assert _tags(jpg, "EXIF:Make").get("Make") == "NIKON CORPORATION"
    # The TIFF back, in the master's byte order, same pixels.
    _run(tmp_path, "jxl_tiff_decoder.py", d, "--mode", "1")
    back = next(p for p in d.rglob("*.tif") if p != tif)
    with tifffile.TiffFile(back) as t:
        assert t.byteorder == byteorder
    assert _byte_order(back) == expected
    assert _tags(back, "EXIF:Make").get("Make") == "NIKON CORPORATION"


# ---------------------------------------------------------- JPEG quality recipe
def test_recipe_changed_compares_the_jpeg_quality():
    import jxl_jpeg_transcoder as tr
    assert not tr._recipe_changed("sRGB@long2048+screen/q70", "sRGB@long2048+screen/q70")
    assert tr._recipe_changed("sRGB@long2048+screen/q70", "sRGB@long2048+screen/q80")
    assert tr._recipe_changed("sRGB@long2048+screen/q80", "sRGB@long2048+screen/q70")
    assert tr._recipe_changed("sRGB@long2048+screen/q70", "sRGB@long1600+screen/q70")
    assert tr._recipe_changed("sRGB@long2048+screen/q70", "sRGB@long2048+print/q70")
    # Written before v2.25.0: no quality recorded -> unknown, kept.
    assert not tr._recipe_changed("sRGB@long2048+screen", "sRGB@long2048+screen/q70")
    # ... but its colour/size/sharpening are still compared.
    assert tr._recipe_changed("sRGB@long2048", "sRGB@long2048+screen/q70")
    # A PNG records no quality.
    assert not tr._recipe_changed("sRGB@long2048", "sRGB@long2048")


@needs_tools
def test_a_new_jpeg_quality_re_derives_and_an_old_recipe_is_kept(tmp_path):
    d, tif, jxl = _master(tmp_path, "<")
    jpg_args = ["--mode", "1", "--force-convert", "--decode", "--format", "jpeg",
                "--icc-profile", "sRGB", "--resize-long", "64", "--sharpen", "screen",
                "--sync"]

    def label(p):
        rel = _tags(p, "XMP-dc:Relation").get("Relation") or []
        rel = rel if isinstance(rel, list) else [rel]
        return [t for t in rel if str(t).startswith("jxlphoto-derived:")]

    _run(tmp_path, "jxl_jpeg_transcoder.py", d, *jpg_args, "--quality", "70")
    jpg = next(d.rglob("*.jpg"))
    assert label(jpg) == ["jxlphoto-derived:sRGB@long64+screen/q70"]
    out = _run(tmp_path, "jxl_jpeg_transcoder.py", d, *jpg_args, "--quality", "70")
    assert "SKIP (up to date)" in out
    out = _run(tmp_path, "jxl_jpeg_transcoder.py", d, *jpg_args, "--quality", "80")
    assert "derivative recipe changed" in out
    assert label(jpg) == ["jxlphoto-derived:sRGB@long64+screen/q80"]
    # A JPEG from before v2.25.0 carries no quality: kept by a new quality.
    _exiftool("-overwrite_original", "-P",
              "-XMP-dc:Relation-=jxlphoto-derived:sRGB@long64+screen/q80",
              "-XMP-dc:Relation+=jxlphoto-derived:sRGB@long64+screen", jpg)
    out = _run(tmp_path, "jxl_jpeg_transcoder.py", d, *jpg_args, "--quality", "60")
    assert "derivative recipe changed" not in out and "SKIP (up to date)" in out
