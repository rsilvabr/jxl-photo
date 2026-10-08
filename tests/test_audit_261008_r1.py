#!/usr/bin/env python3
"""R1 of the 2026-10-08 audit: recompressing a master whose ICC profile has no
native JPEG XL form changed its colours.

Measured on the owner's real film scans (profile SFprofT: A2B tables, no B2A):
the FIRST recompression of a lossless master came back at 27 dB through the
decoder, the second at 18 dB (correct: ~49 dB). Two mechanisms:
  * lossless master -> lossy: cjxl kept the profile as an ICC blob, which the
    toolkit decodes through linear sRGB — and for an A2B-only profile the way
    back is not the inverse of the way in;
  * a lossy ICC-blob source: cjxl READS it as linear sRGB and writes native
    linear sRGB with the old profile still in XMP.

The fix mirrors the encoder: a lossy-blob source is never converted (copied),
a profile with no native form is encoded tagged sRGB with the profile in XMP
("skip"), and an A2B-only profile is never a conversion TARGET (derivative
refused, decoder keeps the source).

Real cjxl/djxl/exiftool/magick, scripts run as subprocesses. Pre-fix proof:

    git show HEAD:<script> > <tmp>/<script>      (all five)
    $env:JXLPHOTO_SCRIPTS_UNDER_TEST = "<tmp>"
    python -m pytest tests/test_audit_261008_r1.py
"""

import hashlib
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jxl_recompressor as rec  # noqa: E402
from _icc_fixtures import romm_toe_icc  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)

_HAS_TOOLS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool", "magick"))
requires_tools = pytest.mark.skipif(
    not _HAS_TOOLS, reason="cjxl/djxl/exiftool/magick not on PATH")

_REC = ["--on-unknown", "convert", "--on-regeneration", "convert",
        "--on-downgrade", "convert", "--no-preflight", "--effort", "3",
        "--workers", "1"]


def _run(script, *args, timeout=900):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *map(str, args)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, stdin=subprocess.DEVNULL)


def _out(r):
    return (r.stdout or "") + (r.stderr or "")


def _md5(p):
    return hashlib.md5(Path(p).read_bytes()).hexdigest()


def a2b_only_icc() -> bytes:
    """The table-curve ProPhoto profile PLUS an A2B0 (lut16) that maps the
    device values through sRGB primaries — and no B2A. Like a scanner
    profile: A2B0 is what a CMM uses to READ the pixels, while the way back
    INTO the profile falls to the (different) matrix/TRC."""
    base = romm_toe_icc()
    n = struct.unpack(">I", base[128:132])[0]
    tags = []
    for i in range(n):
        sig, off, ln = struct.unpack(">4sII", base[132 + 12 * i:144 + 12 * i])
        tags.append((sig, base[off:off + ln]))
    m = np.array([[0.4360747, 0.3850649, 0.1430804],
                  [0.2225045, 0.7168786, 0.0606169],
                  [0.0139322, 0.0971045, 0.7141733]])
    clut = b""
    for r in (0, 1):
        for g in (0, 1):
            for b in (0, 1):
                xyz = m @ np.array([r, g, b], dtype=float)
                clut += b"".join(struct.pack(">H", min(65535, round(v * 32768)))
                                 for v in xyz)
    ident = b"".join(struct.pack(">i", 65536 if i % 4 == 0 else 0) for i in range(9))
    curves = struct.pack(">HH", 0, 65535) * 3
    mft2 = (b"mft2" + b"\0" * 4 + bytes([3, 3, 2, 0]) + ident
            + struct.pack(">HH", 2, 2) + curves + clut + curves)
    tags.append((b"A2B0", mft2))
    off = 128 + 4 + 12 * len(tags)
    table, data = b"", b""
    for sig, d in tags:
        while len(d) % 4:
            d += b"\0"
        table += struct.pack(">4sII", sig, off + len(data), len(d))
        data += d
    out = bytearray(base[:128]) + struct.pack(">I", len(tags)) + table + data
    struct.pack_into(">I", out, 0, len(out))
    return bytes(out)


def _gradient_tiff(path: Path, icc: bytes):
    import tifffile
    path.parent.mkdir(parents=True, exist_ok=True)
    lo, hi = 0.1, 0.9
    x = np.linspace(lo, hi, 128)
    yy, xx = np.meshgrid(x, x, indexing="ij")
    arr = np.stack([(xx * 65535).astype("uint16"),
                    (yy * 65535).astype("uint16"),
                    (((1 - xx) * (1 - yy) * (hi - lo) + lo) * 65535).astype("uint16")],
                   axis=2)
    tifffile.imwrite(str(path), arr, photometric="rgb", metadata=None,
                     extratags=[(34675, "B", len(icc), icc, False)])
    return path


def _master(tmp_path, name, icc, distance, strategy="cautious"):
    tif = _gradient_tiff(tmp_path / f"t_{name}" / f"{name}.tif", icc)
    dst = tmp_path / f"m_{name}"
    r = _run("jxl_tiff_encoder.py", tif.parent, dst, "--distance", distance,
             "--effort", "3", "--workers", "1", "--no-preflight",
             "--icc-png-strategy", strategy)
    assert r.returncode == 0, _out(r)
    jxls = sorted(dst.rglob("*.jxl"))
    assert len(jxls) == 1, _out(r)
    return tif, jxls[0]


def _blob_state(jxl: Path, work: Path):
    """True when djxl returns the file's own colour space, False for a lossy
    ICC blob (linear sRGB). Computed without the scripts' helpers."""
    work.mkdir(parents=True, exist_ok=True)
    out_icc, orig_icc = work / "o.icc", work / "oo.icc"
    subprocess.run(["djxl", str(jxl), str(work / "p.png"),
                    f"--icc_out={out_icc}", f"--orig_icc_out={orig_icc}"],
                   check=True, capture_output=True, timeout=300)
    return out_icc.read_bytes() == orig_icc.read_bytes()


def _decode_psnr(tmp_path, jxl: Path, tif: Path, tag: str) -> float:
    import tifffile
    d = tmp_path / f"dec_{tag}"
    d.mkdir()
    shutil.copy2(jxl, d / "x.jxl")
    r = _run("jxl_tiff_decoder.py", d, "--mode", "8", "--workers", "1")
    assert r.returncode == 0, _out(r)
    a = tifffile.imread(str(tif)).astype(np.float64)
    b = tifffile.imread(str(d / "x.tif")).astype(np.float64)
    mse = float(((a - b) ** 2).mean())
    return float("inf") if mse == 0 else 10 * np.log10(65535.0 ** 2 / mse)


def _recompress(tmp_path, jxl: Path, tag: str, *extra) -> tuple:
    g = tmp_path / f"rec_{tag}"
    g.mkdir()
    shutil.copy2(jxl, g / jxl.name)
    r = _run("jxl_recompressor.py", g, "--mode", "1", *_REC, *extra)
    return r, g / "recompressed_jxl" / jxl.name


# ---------------------------------------------------------------------------

@requires_tools
@pytest.mark.parametrize("profile", ["table_curve", "a2b_only"])
def test_lossless_master_keeps_its_colours_over_two_recompressions(tmp_path, profile):
    icc = romm_toe_icc() if profile == "table_curve" else a2b_only_icc()
    tif, master = _master(tmp_path, "m", icc, "0")
    r, g1 = _recompress(tmp_path, master, "g1", "--distance", "1")
    assert r.returncode == 0, _out(r)
    assert _blob_state(g1, tmp_path / "p1") is True, \
        "generation 1 became a lossy ICC blob"
    p1 = _decode_psnr(tmp_path, g1, tif, "g1")
    r, g2 = _recompress(tmp_path, g1, "g2", "--distance", "2")
    assert r.returncode == 0, _out(r)
    p2 = _decode_psnr(tmp_path, g2, tif, "g2")
    # Measured on this fixture (effort 3): the encoder's own "skip" straight
    # from the TIFF gives 43.1 dB at d=1 and 36.9 dB at d=2; the fixed chain
    # 43.1 / 36.2. Pre-fix: 46.2 / 14.1 (table curve), 16.8 / 12.2 (A2B-only).
    assert p1 >= 30 and p2 >= 30, f"PSNR gen1 {p1:.1f} dB, gen2 {p2:.1f} dB"


@requires_tools
def test_lossy_icc_blob_source_is_copied_never_converted(tmp_path):
    tif, blob = _master(tmp_path, "b", romm_toe_icc(), "1", strategy="always")
    assert _blob_state(blob, tmp_path / "p0") is False, "fixture is not a lossy blob"
    r, out = _recompress(tmp_path, blob, "c", "--distance", "2")
    assert r.returncode == 0, _out(r)
    assert _md5(out) == _md5(blob), "a lossy ICC-blob source was re-encoded"


@requires_tools
def test_keep_derivative_of_a_lossless_table_curve_master_is_not_a_blob(tmp_path):
    tif, master = _master(tmp_path, "k", romm_toe_icc(), "0")
    r, out = _recompress(tmp_path, master, "k", "--distance", "1",
                         "--resize-percent", "50")
    assert r.returncode == 0, _out(r)
    assert _blob_state(out, tmp_path / "pk") is True, \
        "the keep derivative was written as a lossy ICC blob"


@requires_tools
def test_keep_derivative_into_an_a2b_only_profile_is_refused(tmp_path):
    tif, blob = _master(tmp_path, "r", a2b_only_icc(), "1", strategy="always")
    assert _blob_state(blob, tmp_path / "p0") is False, "fixture is not a lossy blob"
    r, out = _recompress(tmp_path, blob, "r", "--distance", "1",
                         "--resize-percent", "50")
    assert r.returncode == 1, _out(r)
    assert not out.exists()
    assert "no B2A" in _out(r)


@requires_tools
def test_decoder_keeps_the_jxl_of_an_approximate_a2b_only_decode(tmp_path):
    tif, blob = _master(tmp_path, "d", a2b_only_icc(), "1", strategy="always")
    assert _blob_state(blob, tmp_path / "p0") is False, "fixture is not a lossy blob"
    r = _run("jxl_tiff_decoder.py", blob.parent, "--mode", "1", "--workers", "1",
             "--delete-source", "--delete-confirm-off")
    assert r.returncode == 0, _out(r)
    assert blob.exists(), "the JXL was deleted after an approximate decode"
    assert list(blob.parent.rglob("*.tif")), _out(r)


def test_output_icc_rejects_an_a2b_only_profile(tmp_path):
    p = tmp_path / "scanner.icc"
    p.write_bytes(a2b_only_icc())
    (tmp_path / "in").mkdir()
    r = _run("jxl_recompressor.py", tmp_path / "in", "--output-icc", p)
    assert r.returncode == 2, _out(r)
    assert "no B2A" in _out(r)


def test_icc_a2b_only_reads_the_tag_table():
    assert rec._icc_a2b_only(a2b_only_icc()) is True
    assert rec._icc_a2b_only(romm_toe_icc()) is False      # matrix/TRC only
    assert rec._icc_a2b_only(b"") is False
    assert rec._icc_a2b_only(b"\0" * 200) is False
