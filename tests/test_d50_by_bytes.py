#!/usr/bin/env python3
"""v2.15.0: D50 patch "auto" decides by the ICC bytes, not the Software tag.

The criterion is a "near miss": the header illuminant field (bytes 68-79) is
within 4/65536 on each of X, Y, Z of the spec D50 (0000F6D6 00010000 0000D32D)
but not exact — Kodak's ProPhoto.icm stores Z as 0xD32B. Exact profiles and
far-off ones (Capture One's PhaseOne RGB(Trinitron G18 D50).icm) are left as
they are, and "auto" never starts an exiftool process.

U1-U3 are unit tests; R1 runs the real cjxl/djxl/exiftool end to end (the
D50 decision and the XMP CreatorTool ICC write are only visible against real
codecs). Pre-fix proof (must fail on the 4921caf/a1f6b3b encoder):

    mkdir <tmp>\\d50pre
    git show 4921caf:jxl_tiff_encoder.py > <tmp>\\d50pre\\jxl_tiff_encoder.py
    $env:JXL_ENCODER_UNDER_TEST = "<tmp>\\d50pre\\jxl_tiff_encoder.py"
    python -m pytest tests/test_d50_by_bytes.py -q
"""

import base64
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

_REPO = Path(__file__).resolve().parent.parent
_OVERRIDE = os.environ.get("JXL_ENCODER_UNDER_TEST")
sys.path.insert(0, str(Path(_OVERRIDE).resolve().parent) if _OVERRIDE else str(_REPO))
import jxl_tiff_encoder as enc

ENCODER = Path(_OVERRIDE) if _OVERRIDE else _REPO / "jxl_tiff_encoder.py"

_REAL = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
real = pytest.mark.skipif(not _REAL,
                          reason="needs cjxl, djxl and exiftool on PATH")

_D50_BYTES = bytes.fromhex("0000f6d6000100000000d32d")


def _base_icc():
    from PIL import ImageCms
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _with_illuminant(icc, x, y, z):
    b = bytearray(icc)
    b[68:80] = struct.pack(">iii", x, y, z)
    b[84:100] = bytes(16)   # profile ID: zero = not computed (allowed)
    return bytes(b)


_BASE = _base_icc()
EXACT = _with_illuminant(_BASE, 63190, 65536, 54061)
KODAK = _with_illuminant(_BASE, 63190, 65536, 54059)    # Z 0xD32B
FAR = _with_illuminant(_BASE, 63178, 65536, 54025)      # PhaseOne 0xF6CA/0xD309


def _reset_counts():
    for k in enc._d50_patch_count:
        enc._d50_patch_count[k] = 0
    enc._d50_patched_hashes.clear()


# ---------------------------------------------------------------------------
# U1 — _d50_near_miss
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("icc,expected", [
    (EXACT, False),
    (KODAK, True),
    (FAR, False),
    (b"\x00" * 79, False),
    (_with_illuminant(_BASE, 63190 + 4, 65536, 54061), True),
    (_with_illuminant(_BASE, 63190 - 4, 65536, 54061), True),
    (_with_illuminant(_BASE, 63190 + 5, 65536, 54061), False),
    (_with_illuminant(_BASE, 63190 - 5, 65536, 54061), False),
], ids=["exact", "kodak", "far", "short-79-bytes", "x-plus-4", "x-minus-4",
        "x-plus-5", "x-minus-5"])   # explicit ids: the lcms header carries a date, so the bytes differ per process (xdist)
def test_u1_near_miss(icc, expected):
    assert enc._d50_near_miss(icc) is expected


# ---------------------------------------------------------------------------
# U2 — should_apply_d50_patch decides from the bytes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode,kodak,exact,far", [
    ("auto", True, False, False),
    ("on", True, True, True),
    ("off", False, False, False),
])
def test_u2_should_apply_by_bytes(monkeypatch, mode, kodak, exact, far):
    monkeypatch.setattr(enc, "D50_PATCH_MODE", mode)
    assert enc.should_apply_d50_patch(KODAK) is kodak
    assert enc.should_apply_d50_patch(EXACT) is exact
    assert enc.should_apply_d50_patch(FAR) is far


# ---------------------------------------------------------------------------
# U3 — "auto" patches the near miss without starting any process
# ---------------------------------------------------------------------------

def test_u3_auto_starts_no_process_and_counts(monkeypatch, tmp_path):
    monkeypatch.setattr(enc, "D50_PATCH_MODE", "auto")
    _reset_counts()

    def _boom(*a, **k):
        raise AssertionError("a process was started by D50 'auto'")

    monkeypatch.setattr(enc, "_run_captured", _boom)
    monkeypatch.setattr(enc.subprocess, "run", _boom)

    assert enc.apply_d50_policy(KODAK, tmp_path / "kodak.tif")[68:80] == _D50_BYTES
    assert enc.apply_d50_policy(EXACT, tmp_path / "exact.tif") == EXACT
    assert enc.apply_d50_policy(FAR, tmp_path / "far.tif") == FAR
    assert enc._d50_patch_count["applied"] == 1
    assert enc._d50_patch_count["already_correct"] == 1
    assert enc._d50_patch_count["other_illuminant"] == 1
    # In "auto" a near miss APPLIES: it must never be counted as skipped
    assert enc._d50_patch_count["skipped_needed"] == 0


# ---------------------------------------------------------------------------
# R1 — real codecs: one folder, three profiles, one run
# ---------------------------------------------------------------------------

@real
def test_r1_encoder_auto_by_bytes_end_to_end(tmp_path):
    folder = tmp_path / "lib"
    folder.mkdir()
    rng = np.random.default_rng(4)
    for name, profile in (("exact.tif", EXACT), ("kodak.tif", KODAK),
                          ("far.tif", FAR)):
        tifffile.imwrite(
            str(folder / name),
            (rng.random((64, 48, 3)) * 65535).astype("uint16"),
            photometric="rgb", iccprofile=profile)

    r = subprocess.run(
        [sys.executable, str(ENCODER), str(folder), "--mode", "0",
         "--delete-confirm-off", "--d50-patch", "auto"],
        capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL,
        env={**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs")})

    assert r.returncode == 0, r.stdout + r.stderr
    names = sorted(p.name for p in folder.glob("*.jxl"))
    assert names == ["exact.jxl", "far.jxl", "kodak.jxl"], r.stdout + r.stderr
    assert "D50 patch: 1 applied" in r.stdout, r.stdout
    assert "1 other illuminant (left as is)" in r.stdout, r.stdout

    # The XMP copy (CreatorTool) keeps the ORIGINAL profile: only the profile
    # handed to cjxl is patched, never the archived bytes.
    probe = subprocess.run(
        ["exiftool", "-s3", "-XMP-xmp:CreatorTool", str(folder / "kodak.jxl")],
        capture_output=True, text=True, timeout=60,
        stdin=subprocess.DEVNULL)
    val = probe.stdout.strip()
    b64 = val.split("ICC:", 1)[1].strip().split("|", 1)[0].strip()
    prof = base64.b64decode(b64)
    assert prof[76:80] == bytes.fromhex("0000d32b"), (
        f"the archived CreatorTool ICC was patched: {prof[76:80].hex()}")
