#!/usr/bin/env python3
"""X1 of the 2026-10-08 audit: #268 was only half fixed, in all four backends.

The cross-run provenance guard ran only when the CURRENT run had
--delete-source in a folder-collapsing mode. The loss happens on the
OVERWRITE, though: an earlier run already deleted the other photo's source,
so a later plain sync (every scheduled preset is one) destroyed that photo's
only file. And every mode collapses names (foto.tif / foto.tiff -> foto.jxl).

Every test here runs the REAL scripts as subprocesses, with cjxl/djxl/exiftool,
and checks the bytes on disk: the bug was invisible to the mocked suite.

Pre-fix proof: extract the HEAD scripts and point the tests at them:

    git show HEAD:jxl_tiff_encoder.py > <tmp>/jxl_tiff_encoder.py   (all five)
    $env:JXLPHOTO_SCRIPTS_UNDER_TEST = "<tmp>"
    python -m pytest tests/test_audit_261008_x1.py

The scenario tests then FAIL (the archive of photo A is overwritten); the
"legitimate re-export still syncs" tests pass on both, by design.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_recompressor as rec  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)

_HAS_CODECS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
requires_codecs = pytest.mark.skipif(
    not _HAS_CODECS, reason="cjxl/djxl/exiftool not on PATH")


def _run(script, *args, timeout=600):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *map(str, args)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, stdin=subprocess.DEVNULL)


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def _tiff(path: Path, seed: int, shape=(48, 64)):
    import tifffile
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 65535, size=(*shape, 3), dtype=np.uint16)
    tifffile.imwrite(str(path), a, photometric="rgb")
    return path


def _jpeg(path: Path, seed: int):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 255, size=(48, 64, 3), dtype=np.uint8)
    Image.fromarray(a).save(str(path), quality=90)
    return path


def _newer(path: Path, than: Path, by: float = 100.0):
    t = than.stat().st_mtime + by
    os.utime(path, (t, t))


def _out(r):
    return (r.stdout or "") + (r.stderr or "")


# ---------------------------------------------------------------------------
# Encoder (E1, E2)
# ---------------------------------------------------------------------------

@requires_codecs
def test_e1_sync_without_delete_never_overwrites_a_deleted_photos_archive(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    _tiff(src / "A" / "foto.tif", 1)
    r = _run("jxl_tiff_encoder.py", src, out, "--mode", "2", "--distance", "0",
             "--delete-source", "--delete-confirm-off", "--no-preflight")
    assert r.returncode == 0, _out(r)
    archive = out / "foto.jxl"
    assert archive.exists() and not (src / "A" / "foto.tif").exists()
    before = _md5(archive)

    b = _tiff(src / "B" / "foto.tif", 2)
    _newer(b, archive)
    r = _run("jxl_tiff_encoder.py", src, out, "--mode", "2", "--distance", "0",
             "--no-preflight")
    assert _md5(archive) == before, "photo A's only archive was overwritten"
    assert r.returncode == 1, _out(r)
    assert "REFUSING" in _out(r)
    assert b.exists()


@requires_codecs
def test_e2_mode8_tif_and_tiff_never_overwrite_each_other(tmp_path):
    f = tmp_path / "f"
    _tiff(f / "foto.tif", 1)
    r = _run("jxl_tiff_encoder.py", f, "--mode", "8", "--distance", "0",
             "--delete-source", "--delete-confirm-off", "--no-preflight")
    assert r.returncode == 0, _out(r)
    archive = f / "foto.jxl"
    before = _md5(archive)

    b = _tiff(f / "foto.tiff", 2)
    _newer(b, archive)
    r = _run("jxl_tiff_encoder.py", f, "--mode", "8", "--distance", "0",
             "--delete-source", "--delete-confirm-off", "--no-preflight")
    assert _md5(archive) == before, "foto.tiff overwrote the archive of foto.tif"
    assert b.exists(), "the refused source was deleted"
    assert r.returncode == 1, _out(r)


@requires_codecs
def test_encoder_never_overwrites_a_lossless_jpeg_archive(tmp_path):
    f = tmp_path / "f"
    _jpeg(f / "foto.jpg", 3)
    r = _run("jxl_jpeg_transcoder.py", f / "foto.jpg")
    assert r.returncode == 0, _out(r)
    archive = f / "foto.jxl"
    assert archive.exists(), _out(r)
    before = _md5(archive)

    t = _tiff(f / "foto.tif", 4)
    _newer(t, archive)
    r = _run("jxl_tiff_encoder.py", f, "--mode", "8", "--distance", "0",
             "--no-preflight")
    assert _md5(archive) == before, "the encoder overwrote a jbrd JPEG archive"
    assert r.returncode == 1, _out(r)


@requires_codecs
def test_encoder_legitimate_reexport_still_syncs(tmp_path):
    """No regression: re-exporting the SAME source in place overwrites."""
    src, out = tmp_path / "src", tmp_path / "out"
    a = _tiff(src / "A" / "foto.tif", 1)
    r = _run("jxl_tiff_encoder.py", src, out, "--mode", "2", "--distance", "0",
             "--no-preflight")
    assert r.returncode == 0, _out(r)
    archive = out / "foto.jxl"
    before = _md5(archive)
    _tiff(a, 5)
    _newer(a, archive)
    r = _run("jxl_tiff_encoder.py", src, out, "--mode", "2", "--distance", "0",
             "--no-preflight")
    assert r.returncode == 0, _out(r)
    assert _md5(archive) != before, "a re-export of the same source was refused"


# ---------------------------------------------------------------------------
# Decoder (D1)
# ---------------------------------------------------------------------------

def _master_jxl(tmp_path, folder: Path, seed: int) -> Path:
    t = _tiff(tmp_path / f"tiffs{seed}" / "foto.tif", seed)
    r = _run("jxl_tiff_encoder.py", t.parent, folder, "--mode", "2",
             "--distance", "0", "--no-preflight")
    assert r.returncode == 0, _out(r)
    return folder / "foto.jxl"


@requires_codecs
def test_d1_sync_without_delete_never_overwrites_a_deleted_jxls_tiff(tmp_path):
    a = _master_jxl(tmp_path, tmp_path / "src" / "A", 1)
    out = tmp_path / "out"
    r = _run("jxl_tiff_decoder.py", tmp_path / "src", out, "--mode", "2",
             "--delete-source", "--delete-confirm-off")
    assert r.returncode == 0, _out(r)
    tif = out / "foto.tif"
    assert tif.exists() and not a.exists()
    before = _md5(tif)

    b = _master_jxl(tmp_path, tmp_path / "src" / "B", 2)
    _newer(b, tif)
    r = _run("jxl_tiff_decoder.py", tmp_path / "src", out, "--mode", "2")
    assert _md5(tif) == before, "the only copy of photo A (its TIFF) was overwritten"
    assert r.returncode == 1, _out(r)
    assert b.exists()


@requires_codecs
def test_decoder_up_to_date_foreign_tiff_is_refused_not_skipped(tmp_path):
    """The smart sync's up-to-date direction: another JXL's decode is not
    'up to date' for this JXL."""
    _master_jxl(tmp_path, tmp_path / "A", 1)
    out = tmp_path / "out"
    r = _run("jxl_tiff_decoder.py", tmp_path / "A", out, "--mode", "2")
    assert r.returncode == 0, _out(r)
    tif = out / "foto.tif"
    before = _md5(tif)
    b = _master_jxl(tmp_path, tmp_path / "B", 2)
    old = tif.stat().st_mtime - 100
    os.utime(b, (old, old))
    r = _run("jxl_tiff_decoder.py", tmp_path / "B", out, "--mode", "2")
    assert _md5(tif) == before
    assert "SKIP (sync: TIFF up to date)" not in _out(r), _out(r)
    assert "DIFFERENT JXL" in _out(r), _out(r)


@requires_codecs
def test_decoder_legitimate_resync_still_overwrites(tmp_path):
    a = _master_jxl(tmp_path, tmp_path / "A", 1)
    out = tmp_path / "out"
    r = _run("jxl_tiff_decoder.py", tmp_path / "A", out, "--mode", "2")
    assert r.returncode == 0, _out(r)
    tif = out / "foto.tif"
    _newer(a, tif)
    r = _run("jxl_tiff_decoder.py", tmp_path / "A", out, "--mode", "2")
    assert r.returncode == 0, _out(r)
    assert "SYNC: JXL newer than TIFF, reconverting" in _out(r), _out(r)


# ---------------------------------------------------------------------------
# Recompressor (R2)
# ---------------------------------------------------------------------------

_REC = ["--on-unknown", "convert", "--on-regeneration", "convert",
        "--on-downgrade", "convert", "--no-preflight"]


@requires_codecs
@pytest.mark.parametrize("mode", ["2", "1"])
def test_r2_recompressor_never_overwrites_another_photos_archive(tmp_path, mode):
    src = tmp_path / "src"
    a = _master_jxl(tmp_path, src / "A", 1)
    out = tmp_path / "out"
    args = ([src, out, "--mode", "2"] if mode == "2"
            else [src / "A", "--mode", "1"])
    r = _run("jxl_recompressor.py", *args, "--distance", "1",
             "--delete-source", "--delete-confirm-off", *_REC)
    assert r.returncode == 0, _out(r)
    archive = (out / "foto.jxl" if mode == "2"
               else src / "A" / "recompressed_jxl" / "foto.jxl")
    assert archive.exists() and not a.exists(), _out(r)
    before = _md5(archive)

    b = _master_jxl(tmp_path, src / ("B" if mode == "2" else "A"), 2)
    _newer(b, archive)
    args = ([src, out, "--mode", "2"] if mode == "2"
            else [src / "A", "--mode", "1"])
    r = _run("jxl_recompressor.py", *args, "--distance", "1", *_REC)
    assert _md5(archive) == before, "photo A's only archive was overwritten"
    assert r.returncode == 1, _out(r)
    assert "REFUSED" in _out(r)


def test_r3_content_mode_is_a_superset_of_path():
    same_path = {"src": "loc1", "srcsum": "old"}
    reexported = {"src": "loc1", "srcsum": "new"}
    assert rec._markers_match(same_path, reexported, "path") is True
    assert rec._markers_match(same_path, reexported, "content") is True
    moved = {"src": "loc2", "srcsum": "old"}
    assert rec._markers_match(same_path, moved, "path") is False
    assert rec._markers_match(same_path, moved, "content") is True
    other = {"src": "loc9", "srcsum": "zzz"}
    assert rec._markers_match(same_path, other, "content") is False


# ---------------------------------------------------------------------------
# Transcoder (T3)
# ---------------------------------------------------------------------------

@requires_codecs
def test_t3_sync_never_overwrites_a_deleted_jpegs_lossless_archive(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    a = _jpeg(src / "A" / "foto.jpg", 1)
    r = _run("jxl_jpeg_transcoder.py", src, out, "--mode", "2",
             "--delete-source", "--delete-confirm-off")
    assert r.returncode == 0, _out(r)
    archive = out / "foto.jxl"
    assert archive.exists() and not a.exists(), _out(r)
    before = _md5(archive)

    b = _jpeg(src / "B" / "foto.jpg", 2)
    _newer(b, archive)
    r = _run("jxl_jpeg_transcoder.py", src, out, "--mode", "2", "--sync")
    assert _md5(archive) == before, "the only copy of JPEG A was overwritten"
    assert r.returncode == 1, _out(r)
    assert b.exists()


@requires_codecs
def test_t3_lossy_decode_never_overwrites_another_jxls_jpeg(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    _master_jxl(tmp_path, src / "A", 1)
    r = _run("jxl_jpeg_transcoder.py", src, out, "--mode", "2",
             "--force-convert")
    assert r.returncode == 0, _out(r)
    jpg = out / "foto.jpg"
    assert jpg.exists(), _out(r)
    before = _md5(jpg)
    shutil.rmtree(src / "A")

    b = _master_jxl(tmp_path, src / "B", 2)
    _newer(b, jpg)
    r = _run("jxl_jpeg_transcoder.py", src, out, "--mode", "2",
             "--force-convert", "--sync")
    assert _md5(jpg) == before, "photo A's JPEG was overwritten by photo B's"
    assert r.returncode == 1, _out(r)


@requires_codecs
def test_transcoder_legitimate_resync_still_overwrites(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    a = _jpeg(src / "A" / "foto.jpg", 1)
    r = _run("jxl_jpeg_transcoder.py", src, out, "--mode", "2")
    assert r.returncode == 0, _out(r)
    archive = out / "foto.jxl"
    _newer(a, archive)      # same bytes, newer mtime: the checksum still matches
    r = _run("jxl_jpeg_transcoder.py", src, out, "--mode", "2", "--sync")
    assert r.returncode == 0, _out(r)
    assert "REFUSING" not in _out(r)
