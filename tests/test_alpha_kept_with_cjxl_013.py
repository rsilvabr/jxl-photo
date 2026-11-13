"""A lossy encode keeps an all-opaque alpha channel with cjxl 0.13 (#670).

cjxl 0.13 drops, by default, an alpha channel with nothing in it (every sample
opaque) from a lossy encode (libjxl #4981, --strip_alpha). The real-photo
battery caught it with libjxl 0.13 on PATH: the lossy master of a 16-bit RGBA
TIFF (a Luminar export) came back as an RGB TIFF. The toolkit keeps every
channel its source has, as cjxl 0.12 did: with cjxl 0.13+ every lossy pixel
encode passes --strip_alpha=0 — the encoder, the recompressor, the
transcoder's convert. The real-codec tests need cjxl 0.13+ (0.12 never drops
it); each works in a folder with Japanese, [ ], a space and `%`.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import jxl_photo_common as common  # noqa: E402

HOSTILE = "250118_吾妻山公園 [FINAL] 100%off %d"
_TOOLS = all(shutil.which(t) for t in ("cjxl", "djxl", "jxlinfo", "exiftool", "magick"))


@pytest.mark.parametrize("version,flags", [
    ((0, 13, 0), ["--strip_alpha=0"]), ((1, 0, 0), ["--strip_alpha=0"]),
    ((0, 12, 0), []), (None, []),
])
def test_the_flag_follows_the_cjxl_version(monkeypatch, version, flags):
    monkeypatch.setattr(common, "_tool_version", lambda exe: version)
    assert common._keep_alpha_flags("cjxl") == flags


def _cjxl_013():
    v = common._tool_version("cjxl") if shutil.which("cjxl") else None
    return v is not None and v[:2] >= (0, 13)


def _has_alpha(jxl):
    r = subprocess.run(["jxlinfo", str(jxl)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return "Alpha" in r.stdout, r.stdout.splitlines()[:2]


def _run(script, *args, tmp):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", JXLPHOTO_SETTINGS="",
               JXLPHOTO_LOG_DIR=str(tmp / "logs"), JXLPHOTO_JXL_THUMB="0")
    r = subprocess.run([sys.executable, str(REPO / script)] + [str(a) for a in args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env, stdin=subprocess.DEVNULL, timeout=600)
    assert r.returncode == 0, (r.stdout + r.stderr)[-2500:]
    return r


def _rgba16(h=96, w=128):
    rng = np.random.default_rng(9)
    rgb = (rng.random((h, w, 3)) * 50000 + 5000).astype(np.uint16)
    alpha = np.full((h, w, 1), 65535, np.uint16)             # every sample opaque
    return np.concatenate([rgb, alpha], axis=2)


needs_013 = pytest.mark.skipif(not _TOOLS, reason="needs cjxl, djxl, jxlinfo, exiftool, magick")


@needs_013
def test_the_encoders_lossy_master_keeps_the_alpha_and_the_tiff_comes_back_whole(tmp_path):
    if not _cjxl_013():
        pytest.skip("cjxl older than 0.13 never drops it")
    import tifffile
    d = tmp_path / HOSTILE
    d.mkdir()
    tif = d / "DSC 0001 (rgba).tif"
    tifffile.imwrite(str(tif), _rgba16(), photometric="rgb", extrasamples=["unassalpha"])
    # --scan-distance same: a tagless RGBA TIFF reads as a film scan, which is
    # archived lossless by default.
    _run("jxl_tiff_encoder.py", tif, "--distance", "1", "--scan-distance", "same",
         "--delete-confirm-off", tmp=tmp_path)
    jxl = tif.with_suffix(".jxl")
    ok, info = _has_alpha(jxl)
    assert ok, info
    out = tmp_path / "back [x]"
    _run("jxl_tiff_decoder.py", jxl, out, "--mode", "0", tmp=tmp_path)
    back = tifffile.imread(str(out / tif.name))
    assert back.shape == (96, 128, 4), back.shape


@needs_013
def test_the_recompressors_lossy_copy_keeps_the_alpha(tmp_path):
    if not _cjxl_013():
        pytest.skip("cjxl older than 0.13 never drops it")
    d = tmp_path / HOSTILE
    d.mkdir()
    pam = tmp_path / "rgba.pam"
    a = _rgba16()
    pam.write_bytes(f"P7\nWIDTH {a.shape[1]}\nHEIGHT {a.shape[0]}\nDEPTH 4\nMAXVAL 65535\n"
                    f"TUPLTYPE RGB_ALPHA\nENDHDR\n".encode() + a.astype(">u2").tobytes())
    src = d / "DSC 0002 (rgba).jxl"
    subprocess.run(["cjxl", str(pam), str(src), "-d", "0", "--container=1"],
                   check=True, capture_output=True)
    assert _has_alpha(src)[0]
    out = tmp_path / "small [x]"
    _run("jxl_recompressor.py", src, out, "--mode", "2", "--distance", "2",
         "--on-unknown", "convert", "--no-keep-smaller", "--no-preflight", tmp=tmp_path)
    copy = out / src.name
    ok, info = _has_alpha(copy)
    assert ok, info


@needs_013
def test_the_transcoders_lossy_convert_keeps_the_alpha(tmp_path):
    if not _cjxl_013():
        pytest.skip("cjxl older than 0.13 never drops it")
    from PIL import Image
    d = tmp_path / HOSTILE
    d.mkdir()
    png = d / "screen 0003 (rgba).png"
    rgb = (np.random.default_rng(2).random((96, 128, 3)) * 255).astype(np.uint8)
    Image.fromarray(np.dstack([rgb, np.full((96, 128), 255, np.uint8)]), "RGBA").save(png)
    _run("jxl_jpeg_transcoder.py", png, "--force-convert", "--distance", "1", tmp=tmp_path)
    ok, info = _has_alpha(png.with_suffix(".jxl"))
    assert ok, info
