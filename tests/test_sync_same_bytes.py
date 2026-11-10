#!/usr/bin/env python3
"""--sync / smart sync of the encoder: "same bytes" is not a new file (#638).

A TIFF restored from a backup has a NEW date and the OLD bytes. The date rule
alone re-encoded it, and with a larger --distance than the master's that
replaced a good master by a worse one (measured: lossless 247 181 B -> 9 301 B).
The master carries `jxlphoto-srcsum` (sha256 of the TIFF it was made from), so
a newer TIFF whose bytes still match it is up to date: SKIP. The master's date
is NOT touched (the recompressor compares it with its derivatives': moving it
would re-derive every copy), so each run compares the newer TIFFs again.

Real cjxl and exiftool (skipped when absent), in a hostile folder: Japanese,
brackets, a space, `%` and parentheses.
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
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent
ENCODER = REPO / "jxl_tiff_encoder.py"

real = pytest.mark.skipif(
    not (shutil.which("cjxl") and shutil.which("djxl") and shutil.which("exiftool")),
    reason="cjxl/djxl/exiftool not installed")

DAY = 86400


def _pixels(seed: int, h: int = 96, w: int = 128) -> np.ndarray:
    """A smooth image with a little noise: lossless and d=3 differ a lot in size."""
    y, x = np.mgrid[0:h, 0:w]
    rng = np.random.default_rng(seed)
    base = np.stack([x * 400 + y * 100, y * 600 + x * 50, (x + y) * 250 + seed * 900], axis=2)
    return np.clip(base + rng.integers(0, 200, base.shape), 0, 65535).astype("uint16")


def _folder(tmp_path: Path) -> Path:
    d = tmp_path / "写真 [FINAL] 50%off (2)"
    d.mkdir()
    return d


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _future(p: Path, days: int = 1) -> int:
    t = time.time() + days * DAY
    os.utime(p, (t, t))
    return p.stat().st_mtime_ns


def _run(args, env_extra=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, str(ENCODER)] + [str(a) for a in args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, env=env)
    return r.returncode, r.stdout + r.stderr


def _archived(tmp_path: Path, name: str = "DSC_0001 (2).tif", seed: int = 1):
    """A TIFF encoded lossless (d=0) next to itself: (folder, tiff, jxl, sha)."""
    d = _folder(tmp_path)
    tif = d / name
    tifffile.imwrite(str(tif), _pixels(seed), photometric="rgb")
    rc, out = _run([tif, "--mode", "0", "--distance", "0", "--effort", "3"])
    assert rc == 0, out
    jxl = tif.with_suffix(".jxl")
    assert jxl.exists(), out
    return d, tif, jxl, _sha(jxl)


def _srcsum(jxl: Path):
    info = enc._read_source_markers_batch([jxl])[str(jxl)]
    assert not info.get("unread"), info
    return info["srcsum"]


def _drop_srcsum(jxl: Path):
    token = enc.SRCSUM_PREFIX + _srcsum(jxl)
    argfile = jxl.parent.parent / "exiftool_args.txt"
    argfile.write_text("\n".join(["-charset", "filename=utf8", "-overwrite_original", "-q",
                                  f"-XMP-dc:Relation-={token}", str(jxl)]) + "\n",
                       encoding="utf-8")
    r = subprocess.run(["exiftool", "-@", str(argfile)], capture_output=True, text=True)
    argfile.unlink()
    assert r.returncode == 0, r.stdout + r.stderr
    assert _srcsum(jxl) is None


SYNC_D3 = ["--mode", "0", "--sync", "--distance", "3", "--effort", "3"]


@real
def test_restored_tiff_with_the_same_bytes_keeps_the_master(tmp_path):
    """1. Newer TIFF, same bytes, a LARGER distance: the master stays byte for
    byte AND keeps its own date (review of #638)."""
    d, tif, jxl, sha0 = _archived(tmp_path)
    tiff_ns = _future(tif)
    jxl_ns = jxl.stat().st_mtime_ns
    assert jxl_ns < tiff_ns

    rc, out = _run([tif] + SYNC_D3)
    assert rc == 0, out
    assert "are the same bytes" in out, out
    assert "SKIP (sync: same TIFF bytes, JXL kept)" in out, out
    assert _sha(jxl) == sha0, "the good master was replaced by a worse one"
    assert jxl.stat().st_mtime_ns == jxl_ns, "the master's date must not move"
    assert sorted(p.name for p in d.iterdir()) == sorted([tif.name, jxl.name])


@real
def test_edited_tiff_is_reconverted(tmp_path):
    """2. The pixels changed: a new encode, a new srcsum."""
    d, tif, jxl, sha0 = _archived(tmp_path)
    old_sum = _srcsum(jxl)
    tifffile.imwrite(str(tif), _pixels(2), photometric="rgb")
    _future(tif)

    rc, out = _run([tif] + SYNC_D3)
    assert rc == 0, out
    assert "are the same bytes" not in out, out
    assert _sha(jxl) != sha0
    assert _srcsum(jxl) not in (None, old_sum)


@real
def test_master_without_srcsum_is_reconverted_as_before(tmp_path):
    """3. No readable srcsum = no proof: never skip on a doubt."""
    d, tif, jxl, sha0 = _archived(tmp_path)
    _drop_srcsum(jxl)
    sha_no_sum = _sha(jxl)
    _future(tif)

    rc, out = _run([tif] + SYNC_D3)
    assert rc == 0, out
    assert "are the same bytes" not in out, out
    assert _sha(jxl) != sha_no_sum
    assert _srcsum(jxl), "the re-encode writes the proof again"


@real
def test_second_run_skips_again_and_never_moves_the_master_date(tmp_path):
    """4. The master's date is left alone, so the next run compares the newer
    TIFF again (a read) and skips it again: same bytes, same date, no encode."""
    d, tif, jxl, sha0 = _archived(tmp_path)
    _future(tif)
    jxl_ns = jxl.stat().st_mtime_ns
    for _ in range(2):
        rc, out = _run([tif] + SYNC_D3)
        assert rc == 0 and "are the same bytes" in out, out
        assert _sha(jxl) == sha0
        assert jxl.stat().st_mtime_ns == jxl_ns


@real
def test_dry_run_says_skip_and_leaves_the_mtime(tmp_path):
    """5. A dry run reports the skip and never touches the master."""
    d, tif, jxl, sha0 = _archived(tmp_path)
    _future(tif)
    before = jxl.stat().st_mtime_ns

    rc, out = _run([tif] + SYNC_D3 + ["--dry-run"])
    assert rc == 0, out
    assert "smart sync would SKIP" in out, out
    assert jxl.stat().st_mtime_ns == before
    assert _sha(jxl) == sha0


@real
def test_split_multipage_tiff_skips_every_page_and_adds_nothing(tmp_path):
    """6. Two real pages + a thumbnail page, split: every page is skipped (the
    thumbnail guard follows the archive) and no new JXL appears."""
    d = _folder(tmp_path)
    tif = d / "scan [2] (3).tif"
    with tifffile.TiffWriter(str(tif)) as tw:
        tw.write(_pixels(1), photometric="rgb")
        tw.write(_pixels(2), photometric="rgb")
        tw.write(_pixels(3, 24, 32), photometric="rgb", subfiletype=1)
    first = ["--mode", "0", "--distance", "0", "--effort", "3",
             "--multipage-mode", "split", "--thumbnail-mode", "exclude"]
    rc, out = _run([tif] + first)
    assert rc == 0, out
    before = {p.name: _sha(p) for p in d.glob("*.jxl")}
    assert len(before) == 2, before

    _future(tif)
    rc, out = _run([tif, "--mode", "0", "--sync", "--distance", "3", "--effort", "3",
                    "--multipage-mode", "split", "--thumbnail-mode", "auto"])
    assert rc == 0, out
    assert "are the same bytes" in out, out
    after = {p.name: _sha(p) for p in d.glob("*.jxl")}
    assert after == before, "a page was rewritten or a thumbnail page was added"


@real
def test_delete_skipped_deletes_a_same_bytes_tiff_but_not_an_edited_one(tmp_path):
    """7. The srcsum that proves the skip is the one --delete-skipped needs."""
    delete = ["--delete-source", "--delete-skipped", "--delete-confirm-off"]
    d, tif, jxl, sha0 = _archived(tmp_path)
    _future(tif)
    rc, out = _run([tif] + SYNC_D3 + delete)
    assert rc == 0, out
    assert "are the same bytes" in out, out
    assert _sha(jxl) == sha0
    assert not tif.exists(), "the archived TIFF was kept: " + out

    d2 = tmp_path / "edited"
    d2.mkdir()
    d2, tif2, jxl2, sha2 = _archived(d2, name="DSC_0002.tif", seed=4)
    tifffile.imwrite(str(tif2), _pixels(5), photometric="rgb")
    _future(tif2)
    rc, out = _run([tif2] + SYNC_D3 + delete)
    assert "are the same bytes" not in out, out
    assert _sha(jxl2) != sha2, "an edited TIFF must be re-encoded, never skipped"


def test_would_skip_stays_a_pure_lookup(tmp_path, monkeypatch):
    """The decision helpers never hash: only _mark_sync_same_bytes does."""
    src = tmp_path / "a.tif"
    final = tmp_path / "a.jxl"
    src.write_bytes(b"tiff")
    final.write_bytes(b"jxl")
    t = time.time()
    os.utime(final, (t - 100, t - 100))
    os.utime(src, (t, t))
    monkeypatch.setattr(enc, "OVERWRITE", "smart")
    monkeypatch.setattr(enc, "_file_content_id",
                        lambda p: pytest.fail("a decision helper hashed a file"))
    enc._SYNC_SAME_BYTES.clear()
    assert enc._would_skip(src, final) is False
    enc._SYNC_SAME_BYTES.add((os.path.normcase(str(src)), os.path.normcase(str(final))))
    try:
        assert enc._would_skip(src, final) is True
        monkeypatch.setattr(enc, "OVERWRITE", True)
        assert enc._would_skip(src, final) is False, "--overwrite always rebuilds"
    finally:
        enc._SYNC_SAME_BYTES.clear()
