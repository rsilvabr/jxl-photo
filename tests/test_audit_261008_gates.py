#!/usr/bin/env python3
"""D2, T1 and T2 of the 2026-10-08 audit: delete gates that let a degraded,
foreign or truncated output through.

  D2  decoder: --depth 8 from a 16-bit master, --none, and --basic dropping
      the profile recorded in XMP all deleted the JXL after a DEGRADED decode
      (only --matrix was blocked). A degraded decode is a derivative, and a
      derivative never deletes its master.
  T1  transcoder, lossy --delete-skipped: ANY same-named JPEG/PNG deleted the
      master JXL ("nothing can prove it") — although every lossy output the
      transcoder writes carries its source's provenance marker.
  T2  transcoder JPEG integrity: "an EOI anywhere" passed a real camera JPEG
      cut in half (the EXIF thumbnail has its own EOI near the start).

Real codecs, scripts run as subprocesses (T2 loads the script directly).
Pre-fix proof: extract the HEAD scripts and point the tests at them:

    git show HEAD:<script> > <tmp>/<script>      (all five)
    $env:JXLPHOTO_SCRIPTS_UNDER_TEST = "<tmp>"
    python -m pytest tests/test_audit_261008_gates.py
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _icc_fixtures import romm_toe_icc  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)

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


def _tiff(path: Path, seed: int, icc: bytes = None):
    import tifffile
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 65535, (48, 64, 3), dtype=np.uint16)
    extra = [(34675, "B", len(icc), icc, False)] if icc else []
    tifffile.imwrite(str(path), a, photometric="rgb", metadata=None, extratags=extra)
    return path


def _master(tmp_path, folder: Path, distance="0", icc=None) -> Path:
    t = _tiff(tmp_path / "t" / "foto.tif", 1, icc)
    r = _run("jxl_tiff_encoder.py", t.parent, folder, "--mode", "2",
             "--distance", distance, "--no-preflight")
    assert r.returncode == 0, _out(r)
    return folder / "foto.jxl"


# ---------------------------------------------------------------------------
# D2 — decoder
# ---------------------------------------------------------------------------

@requires_codecs
def test_d2_depth8_of_a_16bit_master_keeps_the_jxl(tmp_path):
    import tifffile
    jxl = _master(tmp_path, tmp_path / "m")
    r = _run("jxl_tiff_decoder.py", jxl.parent, "--mode", "8", "--depth", "8",
             "--delete-source", "--delete-confirm-off")
    assert r.returncode == 0, _out(r)
    tif = jxl.with_suffix(".tif")
    assert tifffile.imread(str(tif)).dtype == np.uint8
    assert jxl.exists(), "the 16-bit master was deleted after an 8-bit decode"


@requires_codecs
def test_d2_none_with_delete_source_is_refused(tmp_path):
    jxl = _master(tmp_path, tmp_path / "m")
    r = _run("jxl_tiff_decoder.py", jxl.parent, "--mode", "8", "--none",
             "--delete-source", "--delete-confirm-off")
    assert jxl.exists(), "the master was deleted after a --none decode"
    assert r.returncode == 2, _out(r)


@requires_codecs
def test_d2_basic_dropping_the_xmp_profile_keeps_the_jxl(tmp_path):
    # A table-curve profile at d=1: the encoder's default writes the pixels
    # tagged sRGB with the real profile in XMP ("skip"); --basic ignores XMP.
    jxl = _master(tmp_path, tmp_path / "m", distance="1", icc=romm_toe_icc())
    r = _run("jxl_tiff_decoder.py", jxl.parent, "--mode", "8", "--basic",
             "--delete-source", "--delete-confirm-off")
    assert r.returncode == 0, _out(r)
    assert jxl.with_suffix(".tif").exists()
    assert jxl.exists(), "the master was deleted after a decode in the wrong profile"


# ---------------------------------------------------------------------------
# T1 — transcoder lossy --delete-skipped
# ---------------------------------------------------------------------------

@requires_codecs
def test_t1_unrelated_same_named_jpeg_never_deletes_the_master(tmp_path):
    from PIL import Image
    jxl = _master(tmp_path, tmp_path / "f")
    Image.fromarray(np.full((50, 80, 3), 128, np.uint8)).save(
        str(jxl.with_suffix(".jpg")), quality=90)
    t = jxl.stat().st_mtime + 100
    os.utime(jxl.with_suffix(".jpg"), (t, t))
    r = _run("jxl_jpeg_transcoder.py", jxl, "--force-convert", "--delete-source",
             "--delete-skipped", "--delete-confirm-off")
    assert jxl.exists(), "the master JXL was deleted because of an unrelated JPEG"
    assert "KEEP" in _out(r), _out(r)


@requires_codecs
def test_t1_own_lossy_output_still_certifies_the_skip(tmp_path):
    """No regression: the transcoder's own JPEG of this JXL still proves it."""
    jxl = _master(tmp_path, tmp_path / "f")
    r = _run("jxl_jpeg_transcoder.py", jxl, "--force-convert")
    assert r.returncode == 0, _out(r)
    assert jxl.with_suffix(".jpg").exists(), _out(r)
    r = _run("jxl_jpeg_transcoder.py", jxl, "--force-convert", "--delete-source",
             "--delete-skipped", "--delete-confirm-off")
    assert r.returncode == 0, _out(r)
    assert not jxl.exists(), _out(r)


# ---------------------------------------------------------------------------
# T2 — JPEG integrity
# ---------------------------------------------------------------------------

def _load_tr():
    spec = importlib.util.spec_from_file_location(
        "t2_transcoder", str(SCRIPTS / "jxl_jpeg_transcoder.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(not shutil.which("exiftool"), reason="exiftool not on PATH")
def test_t2_truncated_jpeg_with_exif_thumbnail_fails_the_check(tmp_path):
    from PIL import Image
    tr = _load_tr()
    rng = np.random.default_rng(3)
    big = Image.fromarray(rng.integers(0, 255, (600, 800, 3), dtype=np.uint8))
    photo = tmp_path / "photo.jpg"
    big.save(str(photo), quality=92)
    big.resize((160, 120)).save(str(tmp_path / "thumb.jpg"), quality=80)
    subprocess.run(["exiftool", "-overwrite_original",
                    f"-ThumbnailImage<={tmp_path / 'thumb.jpg'}", str(photo)],
                   check=True, capture_output=True)
    data = photo.read_bytes()
    assert data.count(b"\xff\xd9") >= 2, "fixture has no embedded thumbnail"
    half = tmp_path / "half.jpg"
    half.write_bytes(data[: len(data) // 2])
    trailer = tmp_path / "trailer.jpg"
    trailer.write_bytes(data + b"MotionPhoto payload" * 1000)
    assert tr._verify_file_integrity(photo) is True
    assert tr._verify_file_integrity(trailer) is True     # data after EOI is fine
    assert tr._verify_file_integrity(half) is False, \
        "a JPEG cut in half passed on its thumbnail's EOI"


def test_t2_progressive_and_restart_markers_pass(tmp_path):
    from PIL import Image
    tr = _load_tr()
    rng = np.random.default_rng(4)
    img = Image.fromarray(rng.integers(0, 255, (300, 400, 3), dtype=np.uint8))
    prog = tmp_path / "prog.jpg"
    img.save(str(prog), quality=90, progressive=True)
    assert tr._verify_file_integrity(prog) is True
    data = prog.read_bytes()
    cut = tmp_path / "prog_cut.jpg"
    cut.write_bytes(data[: len(data) - 200])
    assert tr._verify_file_integrity(cut) is False
