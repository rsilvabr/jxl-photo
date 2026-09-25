#!/usr/bin/env python3
"""The transcoder's built-in AdobeRGB profile (--icc-profile AdobeRGB).

The recompressor always had the alias; the transcoder only knew "sRGB", so a
manifest row asking for AdobeRGB on a JXL -> JPEG run had nowhere to go. The
transcoder stores the profile as bytes (its generator needs numpy, which the
transcoder does not otherwise require) — pinned here to the recompressor's.
"""

import base64
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_jpeg_transcoder as tr
import jxl_recompressor as rec

REPO = Path(__file__).resolve().parent.parent

_HAVE_TOOLS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool", "magick"))
real = pytest.mark.skipif(
    not _HAVE_TOOLS, reason="needs cjxl, djxl, exiftool and magick on PATH")


def test_builtin_profile_is_the_recompressors_byte_for_byte():
    assert base64.b64decode("".join(tr._ADOBE_RGB_ICC_B64)) == rec._adobe_rgb_icc_bytes()


def test_alias_spellings_and_args(monkeypatch, tmp_path):
    monkeypatch.setattr(tr, "TEMP_DIR", str(tmp_path))
    monkeypatch.setattr(tr, "_adobergb_icc_cache", None)
    for spelling in ("AdobeRGB", "adobergb", "ADOBE", "adobergb1998"):
        assert tr._ICC_ALIASES[spelling.lower()] == "AdobeRGB"
    assert tr._ICC_ALIASES["srgb"] == "sRGB"
    args = tr._magick_icc_args("AdobeRGB", [])
    assert args[0] == "-profile"
    assert Path(args[1]).read_bytes() == rec._adobe_rgb_icc_bytes()
    assert tr._output_icc_label("AdobeRGB") == "AdobeRGB"      # same label as the recompressor


def test_missing_profile_file_is_refused_up_front(tmp_path):
    src = tmp_path / "a.jxl"
    src.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)
    r = subprocess.run([sys.executable, str(REPO / "jxl_jpeg_transcoder.py"),
                        str(src), "--decode", "--force-convert",
                        "--icc-profile", str(tmp_path / "nope.icc"), "--dry-run"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "--icc-profile" in r.stderr


def _psnr(a: Path, b: Path) -> float:
    r = subprocess.run(["magick", "compare", "-metric", "PSNR", str(a), str(b), "null:"],
                       capture_output=True, text=True, timeout=120)
    return float(re.search(r"([0-9.]+)", r.stderr).group(1))


@real
def test_lowercase_alias_converts_an_srgb_jxl_to_adobergb(tmp_path):
    pytest.importorskip("PIL.ImageCms")
    np = pytest.importorskip("numpy")
    imagecodecs = pytest.importorskip("imagecodecs")
    from PIL import ImageCms

    x = np.linspace(0, 1, 64)
    yy, xx = np.meshgrid(x, x, indexing="ij")
    arr = np.stack([(xx * 65535), (yy * 65535), ((1 - xx) * (1 - yy) * 65535)],
                   axis=2).astype("uint16")
    raw = tmp_path / "raw.png"
    raw.write_bytes(imagecodecs.png_encode(arr))
    master = tmp_path / "master.jxl"
    subprocess.run(["cjxl", str(raw), str(master), "-d", "1", "--container=1"],
                   check=True, capture_output=True)

    r = subprocess.run([sys.executable, str(REPO / "jxl_jpeg_transcoder.py"),
                        str(master), "--decode", "--force-convert", "--mode", "1",
                        "--format", "png", "--bit-depth", "16",
                        "--icc-profile", "adobergb", "--workers", "1"],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    out = tmp_path / "recovered_jpeg" / "master.png"
    desc = subprocess.run(["exiftool", "-s", "-s", "-s", "-ICC_Profile:ProfileDescription",
                           str(out)], capture_output=True, text=True).stdout
    assert "AdobeRGB1998-compatible" in desc, desc

    adobe = tmp_path / "adobe.icc"
    adobe.write_bytes(rec._adobe_rgb_icc_bytes())
    srgb = tmp_path / "srgb.icc"
    srgb.write_bytes(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    dec = tmp_path / "dec.png"
    subprocess.run(["djxl", str(master), str(dec), "--bits_per_sample=16"],
                   check=True, capture_output=True)
    ref = tmp_path / "ref.png"
    subprocess.run(["magick", str(dec), "-profile", str(srgb), "-profile", str(adobe),
                    "-depth", "16", str(ref)], check=True, capture_output=True)
    assert _psnr(out, ref) >= 60.0
