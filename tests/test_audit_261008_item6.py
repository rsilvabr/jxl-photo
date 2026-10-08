#!/usr/bin/env python3
"""Item 6 of the 2026-10-08 audit (E8/D4, E5, E4; E6/X3 lives in
tests/test_staging_promotion.py).

  E8/D4  jxlphoto-derived: was not an internal marker for the encoder or the
         decoder: master -> sRGB derivative -> decoded TIFF -> encoded again
         gave a NEW master marked as a derivative AND with jxlphoto-src.
  E5     an image kept in a SubIFD (TIFF/EP, DNG layout) is invisible to the
         planner: --multipage-mode skip/ignore archived the small preview and
         --delete-source deleted the file holding the real image.
  E4     4 channels in lossy: cjxl's default --keep_invisible=0 rewrote the
         colour wherever the 4th channel is 0 (an RGB+IR scan's dust).

Real codecs, scripts run as subprocesses. Pre-fix proof:
    $env:JXLPHOTO_SCRIPTS_UNDER_TEST = "<tmp>"   (git show HEAD:<script> ...)
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)
sys.path.insert(0, str(REPO))

_HAS_CODECS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
requires_codecs = pytest.mark.skipif(
    not _HAS_CODECS, reason="cjxl/djxl/exiftool not on PATH")


def _run(script, *args, timeout=900):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *map(str, args)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, stdin=subprocess.DEVNULL)


def _out(r):
    return (r.stdout or "") + (r.stderr or "")


def _relation(path: Path) -> str:
    return subprocess.run(["exiftool", "-s3", "-XMP-dc:Relation", str(path)],
                          capture_output=True, text=True).stdout


def test_derived_prefix_is_the_same_in_every_script():
    import jxl_jpeg_transcoder as tr
    import jxl_recompressor as rec
    import jxl_tiff_decoder as dec
    import jxl_tiff_encoder as enc
    assert (enc.DERIVED_XMP_PREFIX == dec.DERIVED_XMP_PREFIX
            == rec.DERIVED_XMP_PREFIX == tr.DERIVED_XMP_PREFIX)


@requires_codecs
@pytest.mark.skipif(not shutil.which("magick"), reason="magick not on PATH")
def test_e8_d4_a_master_made_from_a_derivative_is_not_a_derivative(tmp_path):
    rng = np.random.default_rng(1)
    src = tmp_path / "t" / "foto.tif"
    src.parent.mkdir()
    tifffile.imwrite(str(src), rng.integers(0, 65535, (48, 64, 3), dtype=np.uint16),
                     photometric="rgb")
    r = _run("jxl_tiff_encoder.py", src.parent, tmp_path / "m", "--mode", "2",
             "--distance", "0.1", "--no-preflight")
    assert r.returncode == 0, _out(r)
    r = _run("jxl_recompressor.py", tmp_path / "m", "--mode", "1", "--output-icc",
             "sRGB", "--distance", "2", "--effort", "3", "--on-unknown", "convert",
             "--on-downgrade", "convert", "--no-preflight")
    assert r.returncode == 0, _out(r)
    deriv = tmp_path / "m" / "recompressed_jxl" / "foto.jxl"
    assert "jxlphoto-derived:" in _relation(deriv), "fixture is not a derivative"
    r = _run("jxl_tiff_decoder.py", deriv.parent, tmp_path / "d", "--mode", "2")
    assert r.returncode == 0, _out(r)
    tif = tmp_path / "d" / "foto.tif"
    assert "jxlphoto-derived" not in _relation(tif), "the decoded TIFF kept it (D4)"
    r = _run("jxl_tiff_encoder.py", tif.parent, tmp_path / "n", "--mode", "2",
             "--distance", "0", "--no-preflight")
    assert r.returncode == 0, _out(r)
    rel = _relation(tmp_path / "n" / "foto.jxl")
    assert "jxlphoto-src:" in rel
    assert "jxlphoto-derived" not in rel, f"the new master reads as a derivative: {rel}"


def _subifd_tiff(path: Path):
    full = np.random.default_rng(5).integers(0, 65535, (256, 384, 3)).astype(np.uint16)
    with tifffile.TiffWriter(str(path)) as tw:
        # IFD0 = reduced preview; the full image lives in a SubIFD (TIFF/EP/DNG)
        tw.write(full[::8, ::8].copy(), photometric="rgb", subfiletype=1, subifds=1)
        tw.write(full, photometric="rgb", subfiletype=0)


@requires_codecs
@pytest.mark.parametrize("mp_mode", ["skip", "ignore", "split_all"])
def test_e5_a_subifd_image_is_never_deleted_unarchived(tmp_path, mp_mode):
    f = tmp_path / "f"
    f.mkdir()
    src = f / "scan.tif"
    _subifd_tiff(src)
    r = _run("jxl_tiff_encoder.py", f, "--mode", "8", "--distance", "0",
             "--multipage-mode", mp_mode, "--delete-source",
             "--delete-confirm-off", "--no-preflight")
    assert src.exists(), (f"--multipage-mode {mp_mode} deleted a TIFF whose main "
                          f"image (in a SubIFD) was never encoded")
    assert "SubIFD" in _out(r), _out(r)


@requires_codecs
def test_e4_lossy_keeps_the_colour_under_a_zero_fourth_channel(tmp_path):
    import imagecodecs
    h, w = 256, 256
    y, x = np.mgrid[0:h, 0:w]
    rgb = np.stack([(x * 200 + 3000), (y * 200 + 5000), ((x + y) * 100 + 8000)],
                   axis=2).astype(np.uint16)
    ir = np.full((h, w), 60000, np.uint16)
    ir[100:140, 100:140] = 0                  # a dust speck: 4th channel == 0
    f = tmp_path / "f"
    f.mkdir()
    tifffile.imwrite(str(f / "scan.tif"), np.concatenate([rgb, ir[..., None]], axis=2),
                     photometric="rgb", extrasamples=[0])
    r = _run("jxl_tiff_encoder.py", f, "--mode", "8", "--distance", "0.1",
             "--no-preflight")
    assert r.returncode == 0, _out(r)
    png = tmp_path / "dec.png"
    subprocess.run(["djxl", str(f / "scan.jxl"), str(png), "--bits_per_sample=16"],
                   check=True, capture_output=True)
    dec = imagecodecs.png_decode(png.read_bytes()).astype(np.float64)
    err = np.abs(dec[..., :3] - rgb.astype(np.float64))
    under = err[ir == 0].mean()
    rest = err[ir != 0].mean()
    # Measured before the fix: 1601 under the zero channel vs 19 elsewhere.
    assert under < 10 * max(rest, 1.0), f"under the 4th==0 area {under:.0f}, elsewhere {rest:.0f}"
