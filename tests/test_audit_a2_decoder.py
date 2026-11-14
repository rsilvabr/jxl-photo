#!/usr/bin/env python3
"""A2 of the 2026-11-03 audit — the decoder items.

  D-B (#570) the JPEG preview converted the WHOLE image to float64 twice
             (~2.2 GB per worker on a 45 MP master) for a 256 px preview.
  D-D (#582) a failed read or clear of dc:Relation was ignored: the JXL's
             internal tokens stayed in the TIFF and the file counted as ok.
  D-E (#585) the TIFF integrity check refused BigTIFF (43) and accepted a
             signature whose byte order contradicts its II/MM.

Every fixture path is hostile: Japanese, `[ ]`, a space, `%`, parentheses.

Pre-fix proof (must fail on the base commit's scripts):

    git show 488e8b6:<script>.py > <tmp>/a2pre/<script>.py   (the 4 backends)
    JXL_SCRIPTS_UNDER_TEST=<tmp>/a2pre python -m pytest tests/test_audit_a2_decoder.py -q
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

_REPO = Path(__file__).resolve().parent.parent
_OVERRIDE = os.environ.get("JXL_SCRIPTS_UNDER_TEST")
_SCRIPTS = Path(_OVERRIDE).resolve() if _OVERRIDE else _REPO
sys.path.insert(0, str(_SCRIPTS))

import jxl_jpeg_transcoder as tra  # noqa: E402
import jxl_tiff_decoder as dec  # noqa: E402

HOSTILE = "250118_吾妻山公園_菜の花 [FINAL] 5%"
_HAVE = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
real = pytest.mark.skipif(not _HAVE, reason="needs cjxl, djxl and exiftool on PATH")


def _hostile(tmp_path, sub="01_Z7 (2)") -> Path:
    d = tmp_path / HOSTILE / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def _image(h, w):
    rng = np.random.default_rng(7)
    base = np.linspace(500, 64000, h * w * 3).reshape(h, w, 3)
    return (base + rng.integers(0, 400, (h, w, 3))).clip(0, 65535).astype(np.uint16)


# ===========================================================================
# D-B (#570)
# ===========================================================================

def _preview(tmp_path, monkeypatch, img):
    # The preview of the decoder's single-page TIFF, from the pixels it writes
    # (_jpeg_preview_array: page 1 of the first writer since #681).
    dec.setup_logger()
    handed = []
    from PIL import Image
    real_fromarray = Image.fromarray

    def fromarray(arr, *a, **k):
        handed.append(np.array(arr, copy=True))
        return real_fromarray(arr, *a, **k)
    monkeypatch.setattr(Image, "fromarray", fromarray)
    result = dec._jpeg_preview_array(img, None)
    return result, handed


def test_db_preview_input_identical(tmp_path, monkeypatch):
    """Equivalence pin (no pre-fix proof): the 8-bit image the preview is
    resized from is exactly rint(x / 257) of the whole image, block by block."""
    img = _image(600, 400)
    result, handed = _preview(tmp_path, monkeypatch, img)
    assert handed, "no preview was made"
    np.testing.assert_array_equal(handed[0], np.rint(img / 257).astype(np.uint8))
    assert result is not None and result[1] == (170, 256), result and result[1]


def test_db_no_full_image_float(tmp_path, monkeypatch):
    img = _image(1000, 1500)
    sizes = []
    real_rint = np.rint

    def rint(x, *a, **k):
        sizes.append(getattr(x, "size", 0))
        return real_rint(x, *a, **k)
    monkeypatch.setattr(np, "rint", rint)
    _preview(tmp_path, monkeypatch, img)
    assert sizes, "the 8-bit conversion never ran"
    assert max(sizes) <= 256 * 1500 * 3, (
        f"a float of {max(sizes)} samples: the whole image ({img.size})")


# ===========================================================================
# D-D (#582)
# ===========================================================================

@real
@pytest.mark.parametrize("which", ["clear", "read"])
def test_dd_failed_relation_step_is_an_error(tmp_path, monkeypatch, which):
    src = _hostile(tmp_path, "TIFF16 [a]")
    tifffile.imwrite(str(src / "DSC_0004 (2).tif"), _image(64, 96), photometric="rgb")
    jxl_dir = tmp_path / HOSTILE / "16B_JXL 5%"
    r = subprocess.run([sys.executable, str(_SCRIPTS / "jxl_tiff_encoder.py"), str(src),
                        str(jxl_dir), "--mode", "0", "--distance", "0", "--effort", "3",
                        "--no-preflight"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout[-2000:]
    jxl = jxl_dir / "DSC_0004 (2).jxl"
    assert jxl.exists()
    real_call = dec._run_exiftool_argfile

    def fake(lines, *a, **k):
        lines = [str(x) for x in lines]
        if which == "clear" and lines[:2] == ["-overwrite_original", "-XMP-dc:Relation="]:
            return subprocess.CompletedProcess(lines, 1, "", "Error: simulated")
        # The read of the TIFF being written (a temp name, never the .jxl).
        if (which == "read" and lines[:2] == ["-j", "-XMP-dc:Relation"]
                and len(lines) == 3 and not lines[-1].lower().endswith(".jxl")):
            return subprocess.CompletedProcess(lines, 1, "", "Error: simulated")
        return real_call(lines, *a, **k)
    monkeypatch.setattr(dec, "_run_exiftool_argfile", fake)
    out = tmp_path / HOSTILE / "16B_TIFF (1)"
    monkeypatch.setattr(sys, "argv", ["jxl_tiff_decoder.py", str(jxl_dir), str(out),
                                      "--mode", "0", "--delete-source",
                                      "--delete-confirm-off"])
    try:
        dec.main()
    except SystemExit:
        pass
    assert jxl.exists(), "the JXL was deleted although its TIFF kept internal tokens"


# ===========================================================================
# D-E (#585)
# ===========================================================================

def test_de_bigtiff_passes_integrity(tmp_path):
    d = _hostile(tmp_path)
    big = d / "big [43] 5%.tif"
    tifffile.imwrite(str(big), _image(32, 48), photometric="rgb", bigtiff=True)
    assert big.read_bytes()[2:4] == b"\x2b\x00"
    assert dec._verify_tiff_integrity(big) is True
    assert tra._verify_file_integrity(big) is True


def test_de_mixed_endianness_fails(tmp_path):
    """Control (passes before too: tifffile cannot open it either)."""
    d = _hostile(tmp_path)
    ok = d / "ok.tif"
    tifffile.imwrite(str(ok), _image(32, 48), photometric="rgb")
    bad = d / "mixed (2).tif"
    data = ok.read_bytes()
    bad.write_bytes(b"II\x00\x2a" + data[4:])
    assert dec._verify_tiff_integrity(bad) is False
    assert tra._verify_file_integrity(bad) is False
