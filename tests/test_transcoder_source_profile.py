#!/usr/bin/env python3
"""B1: the transcoder must ASSIGN the source profile before converting.

A JXL encoded as sRGB decodes to a PNG that carries an sRGB chunk and no
iCCP. `magick -profile <target>` on such a PNG ASSIGNS the target instead of
converting from sRGB, so the colours shift silently (measured 2026-09-25:
the output was identical to a plain assignment, 35.7 dB away from the correct
conversion). The fix assigns the source profile explicitly first; this test
pins both directions of the difference.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_recompressor as rec

REPO = Path(__file__).resolve().parent.parent

_HAVE_TOOLS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool", "magick"))
real = pytest.mark.skipif(
    not _HAVE_TOOLS, reason="needs cjxl, djxl, exiftool and magick on PATH")


def _saturated_png(path: Path):
    import numpy as np
    import imagecodecs
    x = np.linspace(0, 1, 64)
    yy, xx = np.meshgrid(x, x, indexing="ij")
    arr = np.stack([(xx * 65535).astype("uint16"),
                    (yy * 65535).astype("uint16"),
                    ((1 - xx) * (1 - yy) * 65535).astype("uint16")], axis=2)
    path.write_bytes(imagecodecs.png_encode(arr))


def _psnr(a: Path, b: Path) -> float:
    r = subprocess.run(["magick", "compare", "-metric", "PSNR",
                        str(a), str(b), "null:"],
                       capture_output=True, text=True, timeout=120)
    m = re.search(r"([0-9.]+)", r.stderr)
    return float(m.group(1))


@real
def test_source_profile_is_assigned_before_conversion(tmp_path):
    """A JXL whose pixels are sRGB must be CONVERTED to AdobeRGB, not re-tagged."""
    pytest.importorskip("PIL.ImageCms")
    from PIL import ImageCms

    raw = tmp_path / "raw.png"                     # no profile: cjxl assumes sRGB
    _saturated_png(raw)
    master = tmp_path / "master.jxl"
    subprocess.run(["cjxl", str(raw), str(master), "-d", "1", "--container=1"],
                   check=True, capture_output=True)
    info = subprocess.run(["jxlinfo", str(master)], capture_output=True,
                          text=True, timeout=120).stdout
    assert "Primaries: sRGB" in info, info

    adobe = tmp_path / "adobe.icc"
    adobe.write_bytes(rec._adobe_rgb_icc_bytes())
    srgb = tmp_path / "srgb.icc"
    srgb.write_bytes(
        ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())

    r = subprocess.run([sys.executable, str(REPO / "jxl_jpeg_transcoder.py"),
                        str(master), "--decode", "--force-convert", "--mode", "1",
                        "--format", "png", "--bit-depth", "16",
                        "--icc-profile", str(adobe), "--workers", "1"],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    out = tmp_path / "recovered_jpeg" / "master.png"
    assert out.exists(), r.stdout + r.stderr

    dec = tmp_path / "dec.png"
    subprocess.run(["djxl", str(master), str(dec), "--bits_per_sample=16"],
                   check=True, capture_output=True)

    correct = tmp_path / "correct.png"
    subprocess.run(["magick", str(dec), "+profile", "*", "-profile", str(srgb),
                    "-intent", "Relative", "-black-point-compensation",
                    "-profile", str(adobe), "-depth", "16", str(correct)],
                   check=True, capture_output=True)
    assigned = tmp_path / "assigned.png"
    subprocess.run(["magick", str(dec), "+profile", "*", "-profile", str(adobe),
                    "-depth", "16", str(assigned)],
                   check=True, capture_output=True)

    assert _psnr(out, correct) >= 60.0, (
        "the output is not the ICC-converted image — the source profile was "
        "not assigned before the target profile")
    assert _psnr(out, assigned) < 50.0, (
        "the output matches a plain re-tag: the target profile was assigned "
        "instead of converting from the source profile")
