#!/usr/bin/env python3
"""A2 of the 2026-11-03 audit — the encoder items.

  E-B (#569) the worker cap counted only cjxl, not the page array and PNG the
             Python process holds per worker; the PNG writer copied the page
             several times over.
  E-J (#573) the cautious ICC probe ran at CJXL_DISTANCE, not at the file's
             own distance (a film scan under SCAN_DISTANCE).
  E-D (#576) a split whose folder moved and that lost one page got a new
             group id on that page only: two incomplete groups.
  E-E (#584) the lossy pixel check of an ICC-blob master always failed with
             a misleading reason.

Every fixture path is hostile: Japanese, `[ ]`, a space, `%`, parentheses.

Pre-fix proof (must fail on the base commit's scripts):

    git show 488e8b6:<script>.py > <tmp>/a2pre/<script>.py   (the 4 backends)
    JXL_SCRIPTS_UNDER_TEST=<tmp>/a2pre python -m pytest tests/test_audit_a2_encoder.py -q
"""

import os
import shutil
import struct
import subprocess
import sys
import tracemalloc
import zlib
from pathlib import Path

import numpy as np
import pytest
import tifffile

_REPO = Path(__file__).resolve().parent.parent
_OVERRIDE = os.environ.get("JXL_SCRIPTS_UNDER_TEST")
_SCRIPTS = Path(_OVERRIDE).resolve() if _OVERRIDE else _REPO
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jxl_tiff_encoder as enc  # noqa: E402
from _icc_fixtures import romm_toe_icc  # noqa: E402

HOSTILE = "250118_吾妻山公園_菜の花 [FINAL] 5%"
_HAVE = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
real = pytest.mark.skipif(not _HAVE, reason="needs cjxl, djxl and exiftool on PATH")


def _run(script, *args, check=True, timeout=900):
    r = subprocess.run([sys.executable, str(_SCRIPTS / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    if check:
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return r


def _gradient(h=96, w=128):
    x = np.linspace(0.1, 0.9, w)
    y = np.linspace(0.1, 0.9, h)
    yy, xx = np.meshgrid(y, x, indexing="ij")
    return np.stack([(xx * 65535), (yy * 65535),
                     ((1 - xx) * (1 - yy) * 0.8 + 0.1) * 65535], -1).astype(np.uint16)


# ===========================================================================
# E-B (#569)
# ===========================================================================

def _old_make_png_bytes(img, icc_bytes=None):
    """make_png_bytes as it was at 488e8b6 (the equivalence reference)."""
    if img.ndim == 2:
        img = img[:, :, np.newaxis]
    h, w, c = img.shape
    color_type = {1: 0, 2: 4, 3: 2, 4: 6}[c]

    def chunk(name, data):
        p = name + data
        return struct.pack(">I", len(data)) + p + struct.pack(">I", zlib.crc32(p) & 0xFFFFFFFF)

    if img.dtype == np.uint8:
        bit_depth = 8
        raw = b"".join(b"\x00" + row.tobytes() for row in img)
    else:
        bit_depth = 16
        raw = b"".join(b"\x00" + row.tobytes() for row in img.astype(">u2"))
    out = b"\x89PNG\r\n\x1a\n"
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, bit_depth, color_type, 0, 0, 0))
    if icc_bytes:
        out += chunk(b"iCCP", b"ICC Profile\x00\x00" + zlib.compress(icc_bytes))
    out += chunk(b"IDAT", zlib.compress(raw, 1))
    out += chunk(b"IEND", b"")
    return out


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16])
@pytest.mark.parametrize("channels", [1, 2, 3, 4])
@pytest.mark.parametrize("icc", [None, b"x" * 300], ids=["no-icc", "icc"])
def test_eb_png_bytes_identical_to_the_old_writer(dtype, channels, icc):
    """Equivalence pin (no pre-fix proof): the streamed writer must produce
    the very bytes the old one did, on noise and on a smooth gradient, with
    a height that is not a multiple of the row block."""
    rng = np.random.default_rng(channels)
    hi = 255 if dtype == np.uint8 else 65535
    for img in (rng.integers(0, hi, (131, 77, channels)).astype(dtype),
                (np.linspace(0, hi, 131 * 77 * channels)
                 .reshape(131, 77, channels)).astype(dtype)):
        if channels == 1:
            img2d = img[:, :, 0]
            assert enc.make_png_bytes(img2d, icc) == _old_make_png_bytes(img2d, icc)
        assert enc.make_png_bytes(img, icc) == _old_make_png_bytes(img, icc)


def test_eb_png_writer_holds_no_whole_image_copy():
    """A smooth 16-bit page: the old writer held a big-endian copy AND the raw
    filtered buffer (2x the page) at once."""
    img = (np.linspace(0, 65535, 1500 * 2000 * 3).reshape(1500, 2000, 3)).astype(np.uint16)
    tracemalloc.start()
    try:
        enc.make_png_bytes(img)
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < img.nbytes, f"peak {peak / 2**20:.0f} MiB for a {img.nbytes / 2**20:.0f} MiB page"


@real
def test_eb_cap_counts_python_buffers(tmp_path, monkeypatch):
    """The encoder hands the cap its per-worker Python cost."""
    src = tmp_path / HOSTILE / "TIFF16 (1)"
    src.mkdir(parents=True)
    tifffile.imwrite(str(src / "DSC_0001 [a].tif"), _gradient(), photometric="rgb")
    seen = {}
    real_cap = enc._memory_capped_workers

    def spy(*a, **k):
        seen.update(k)
        return real_cap(*a, **k)
    monkeypatch.setattr(enc, "_memory_capped_workers", spy)
    monkeypatch.setattr(sys, "argv", ["jxl_tiff_encoder.py", str(src),
                                      str(tmp_path / HOSTILE / "16B_JXL"), "--mode", "0",
                                      "--distance", "0", "--effort", "3", "--no-preflight"])
    try:
        enc.main()
    except SystemExit:
        pass
    assert getattr(enc, "_PYTHON_BYTES_PER_PIXEL", 0) > 0
    assert seen.get("extra_bytes_per_pixel") == enc._PYTHON_BYTES_PER_PIXEL, seen


@real
def test_eb_uint16_page_not_copied(tmp_path, monkeypatch):
    """A 16-bit page reaches the PNG writer without a uint16 -> uint16 copy."""
    src = tmp_path / HOSTILE / "TIFF16 (2)"
    src.mkdir(parents=True)
    tifffile.imwrite(str(src / "DSC_0002 (2).tif"), _gradient(), photometric="rgb")
    pages, handed = [], []
    real_asarray = tifffile.TiffPage.asarray

    def asarray(self, *a, **k):
        arr = real_asarray(self, *a, **k)
        pages.append(arr)
        return arr
    monkeypatch.setattr(tifffile.TiffPage, "asarray", asarray)
    real_png = enc.make_png_bytes

    def png(img, icc_bytes=None):
        handed.append(img)
        return real_png(img, icc_bytes)
    monkeypatch.setattr(enc, "make_png_bytes", png)
    monkeypatch.setattr(sys, "argv", ["jxl_tiff_encoder.py", str(src),
                                      str(tmp_path / HOSTILE / "16B_JXL"), "--mode", "0",
                                      "--distance", "0", "--effort", "3", "--no-preflight"])
    try:
        enc.main()
    except SystemExit:
        pass
    big = [h for h in handed if h.shape[:2] == (96, 128)]
    assert big, "the page never reached make_png_bytes"
    assert any(np.shares_memory(big[0], p) for p in pages), (
        "the uint16 page was copied before the PNG writer")


# ===========================================================================
# E-J (#573)
# ===========================================================================

@real
def test_ej_scan_distance_drives_the_cautious_probe(tmp_path):
    src = tmp_path / HOSTILE / "scans [SF] (1)"
    src.mkdir(parents=True)
    icc = romm_toe_icc()
    tifffile.imwrite(str(src / "scan 0001 (2).tif"), _gradient(), photometric="rgb",
                     software="SilverFast 9.2.0", metadata=None,
                     extratags=[(34675, "B", len(icc), icc, False)])
    out = tmp_path / HOSTILE / "16B_JXL scans"
    r = _run("jxl_tiff_encoder.py", src, out, "--mode", "0", "--distance", "0",
             "--scan-distance", "1", "--workers", "1", "--icc-png-strategy", "cautious",
             "--icc-cache-dir", tmp_path / "icc cache [x]", "--no-preflight")
    jxl = out / "scan 0001 (2).jxl"
    assert jxl.exists(), r.stdout[-2000:]
    probe = tmp_path / "probe"
    probe.mkdir()
    o, oi = probe / "o.icc", probe / "oi.icc"
    subprocess.run(["djxl", str(jxl), str(probe / "p.png"), f"--icc_out={o}",
                    f"--orig_icc_out={oi}"], check=True, capture_output=True)
    assert o.read_bytes() == oi.read_bytes(), (
        "the lossy scan stores its scanner ICC as a blob: the probe ran lossless")
    assert "Skipping ICC in PNG iCCP" in r.stdout + r.stderr


# ===========================================================================
# E-D (#576)
# ===========================================================================

def _group_ids(paths):
    marks = enc._read_group_markers_batch(list(paths))
    return {Path(p).name: marks.get(str(p)) for p in paths}


@real
def test_ed_moved_split_heals_into_one_group(tmp_path):
    a = tmp_path / HOSTILE / "A [x]"
    a.mkdir(parents=True)
    tif = a / "scan 吾妻 (1).tif"
    with tifffile.TiffWriter(str(tif)) as w:
        w.write(np.full((64, 96, 3), 20000, np.uint16), photometric="rgb")
        w.write(np.full((64, 96, 3), 40000, np.uint16), photometric="rgb")
    _run("jxl_tiff_encoder.py", a, "--mode", "1", "--distance", "0", "--effort", "3",
         "--no-preflight")
    b = tmp_path / HOSTILE / "B 吾妻 (2)"
    a.rename(b)                                   # the event folder was renamed
    out = b / enc.CONVERTED_JXL_FOLDER
    p0, p1 = out / "scan 吾妻 (1).jxl", out / "scan 吾妻 (1)_page1.jxl"
    before = _group_ids([p0, p1])
    assert before[p0.name] and before[p0.name] == before[p1.name], before
    p1.unlink()                                   # one page lost (failed before)
    _run("jxl_tiff_encoder.py", b, "--mode", "1", "--distance", "0", "--effort", "3",
         "--no-preflight")
    assert p1.exists()
    after = _group_ids([p0, p1])
    assert after[p0.name] == after[p1.name] == before[p0.name], (
        f"the re-encoded page joined another group: {after}")


# ===========================================================================
# E-E (#584)
# ===========================================================================

@real
def test_ee_blob_master_verify_names_the_real_reason(tmp_path):
    src = tmp_path / HOSTILE / "TIFF16 blob"
    src.mkdir(parents=True)
    icc = romm_toe_icc()
    tif = src / "DSC_0003 (2).tif"
    tifffile.imwrite(str(tif), _gradient(), photometric="rgb", metadata=None,
                     extratags=[(34675, "B", len(icc), icc, False)])
    r = _run("jxl_tiff_encoder.py", src, tmp_path / HOSTILE / "16B_JXL blob", "--mode", "0",
             "--distance", "1", "--workers", "1", "--icc-png-strategy", "always",
             "--delete-source", "--delete-confirm-off", "--no-preflight", check=False)
    log = r.stdout + r.stderr
    assert tif.exists(), "the TIFF of an unverifiable master was deleted"
    assert "lossy ICC blob" in log, log[-2500:]
