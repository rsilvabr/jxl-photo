#!/usr/bin/env python3
"""#684 (v2.31.0): the EXIF copied into an output keeps the source's VALUES.

Every copy went through `-tagsfromfile <source> -exif:all`, which copies the
PRINT-converted values and converts them back: MaxApertureValue f/2.83 came
back f/2.8 (12 of 25 of the owner's photos, round trip of 2026-11-15) and a
phone's FocalLength 6.62 mm came back 6.6 mm (measured 2026-11-21). The copy is
`-exif:all#` now (numeric values) in the five places that copy EXIF: the
encoder's master, the decoder's TIFF (and its 'none' strategy), the
transcoder's converted outputs and the recompressor's derivatives. Real codecs
and ExifTool, in a folder with Japanese, [ ], a space and '%'."""

import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_jpeg_transcoder as tr
import jxl_recompressor as rec
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc

pytestmark = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool", "magick")),
    reason="needs cjxl, djxl, exiftool and magick")

HOSTILE = "吾妻山 [x] 100%off %d"
# Values whose print conversion is lossy: f/2.828... prints "2.8", 6.62 mm
# prints "6.6 mm", and the APEX values round the same way.
EXACT = {"MaxApertureValue": 2.82842712474619, "FocalLength": 6.62,
         "ApertureValue": 3.29436406907029, "ExposureTime": 0.003125}


def _read(path, tags=tuple(EXACT)):
    r = enc._run_exiftool_argfile(["-j", "-n"] + [f"-{t}" for t in tags] + [str(path)],
                                  timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)[0]


def _write_exif(path):
    lines = ["-overwrite_original", "-Make=NIKON", "-Model=Z 8"]
    lines += [f"-{t}#={v}" for t, v in EXACT.items()]
    r = enc._run_exiftool_argfile(lines + [str(path)], timeout=60)
    assert r.returncode == 0, r.stderr
    got = _read(path)
    for t, v in EXACT.items():
        assert got[t] == pytest.approx(v, rel=1e-6), (t, got)


def _assert_exact(path, what):
    got = _read(path)
    for t, v in EXACT.items():
        assert t in got, f"{what}: {t} missing ({got})"
        assert got[t] == pytest.approx(v, rel=1e-6), f"{what}: {t} = {got[t]}, source {v}"


def _run(mod, argv):
    old = sys.argv
    sys.argv = [mod.__name__ + ".py"] + [str(a) for a in argv]
    try:
        mod.main()
    except SystemExit as e:
        assert e.code in (0, None), f"{mod.__name__} exited {e.code}"
    finally:
        sys.argv = old


@pytest.fixture
def master(tmp_path):
    src = tmp_path / HOSTILE / "TIFF16"
    src.mkdir(parents=True)
    tif = src / "DSC_0001 (2).tif"
    rng = np.random.default_rng(1)
    tifffile.imwrite(str(tif), rng.integers(0, 65535, (40, 56, 3), dtype=np.uint16),
                     photometric="rgb")
    _write_exif(tif)
    out = tmp_path / HOSTILE / "16B_JXL"
    _run(enc, [src, out, "--mode", "0", "--distance", "0", "--effort", "3",
               "--workers", "1", "--no-preflight"])
    jxl = out / "DSC_0001 (2).jxl"
    assert jxl.exists()
    return jxl


def test_the_master_keeps_the_exact_values(master):
    _assert_exact(master, "master")


def test_the_decoded_tiff_keeps_the_exact_values(master, tmp_path):
    out = tmp_path / HOSTILE / "back [tif]"
    _run(dec, [master.parent, out, "--mode", "0", "--workers", "1"])
    tifs = list(out.glob("*.tif"))
    assert len(tifs) == 1, tifs
    _assert_exact(tifs[0], "decoded TIFF")


def test_a_jpeg_made_from_the_master_keeps_the_exact_values(master, tmp_path):
    out = tmp_path / HOSTILE / "jpeg 100%"
    _run(tr, [master.parent, out, "--mode", "0", "--format", "jpeg", "--workers", "1"])
    jpgs = list(out.glob("*.jpg"))
    assert len(jpgs) == 1, jpgs
    _assert_exact(jpgs[0], "JPEG")


def test_a_derivative_keeps_the_exact_values(master, tmp_path):
    out = tmp_path / HOSTILE / "small [jxl]"
    _run(rec, [master.parent, out, "--mode", "0", "--distance", "1", "--effort", "3",
               "--resize-long", "32", "--workers", "1", "--no-preflight",
               "--on-unknown", "convert", "--on-regeneration", "convert",
               "--on-downgrade", "convert"])
    jxls = list(out.glob("*.jxl"))
    assert len(jxls) == 1, jxls
    _assert_exact(jxls[0], "derivative")
