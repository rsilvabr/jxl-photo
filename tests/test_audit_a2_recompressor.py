#!/usr/bin/env python3
"""A2 of the 2026-11-03 audit — the recompressor items.

  R-A (#572) in place with staging, the copy out of staging replaced the only
             copy BEFORE its integrity was checked.
  R-B (#574) the delete gate's group veto only saw the pages in this run: a
             lone page of a split was deleted while its siblings stayed.
  R-C (#577) output == input through a junction (or subst/UNC/8.3) was not
             seen as in place.

Every fixture path is hostile: Japanese, `[ ]`, a space, `%`, parentheses.

Pre-fix proof (must fail on the base commit's scripts):

    git show 488e8b6:<script>.py > <tmp>/a2pre/<script>.py   (the 4 backends)
    JXL_SCRIPTS_UNDER_TEST=<tmp>/a2pre python -m pytest tests/test_audit_a2_recompressor.py -q
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

import jxl_recompressor as rec  # noqa: E402

HOSTILE = "250118_吾妻山公園_菜の花 [FINAL] 5%"
_HAVE = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
real = pytest.mark.skipif(not _HAVE, reason="needs cjxl, djxl and exiftool on PATH")
windows = pytest.mark.skipif(os.name != "nt", reason="junctions are Windows-only")


def _run(script, *args, check=True, timeout=900):
    r = subprocess.run([sys.executable, str(_SCRIPTS / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    if check:
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return r


def _lossless_jxl(path: Path, seed=2) -> Path:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    png = path.parent / f"_{seed}.png"
    rng = np.random.default_rng(seed)
    Image.fromarray((rng.random((96, 128, 3)) * 255).astype("uint8")).save(png)
    r = rec._run_captured(["cjxl", str(png), str(path), "-d", "0"], 300)
    png.unlink()
    assert r.returncode == 0, r.stderr
    return path


def _main(argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["jxl_recompressor.py"] + [str(a) for a in argv])
    try:
        rec.main()
    except SystemExit as e:
        return e.code
    return 0


# ===========================================================================
# R-A (#572)
# ===========================================================================

@real
def test_ra_damaged_copy_out_of_staging_never_replaces_the_source(tmp_path, monkeypatch):
    src_dir = tmp_path / HOSTILE / "16B_JXL (1)"
    jxl = _lossless_jxl(src_dir / "DSC_0001 (2).jxl")
    before = jxl.read_bytes()
    staging = tmp_path / "ステージング [s] 5%"
    real_move = shutil.move

    def damaging_move(src, dst, *a, **k):
        if str(src).startswith(str(staging)):
            data = Path(src).read_bytes()
            Path(dst).write_bytes(data[: len(data) // 2])      # a silent bad copy
            Path(src).unlink()
            return str(dst)
        return real_move(src, dst, *a, **k)
    monkeypatch.setattr(rec.shutil, "move", damaging_move)
    _main([src_dir, "--mode", "8", "--distance", "1", "--staging", staging,
           "--delete-confirm-off"], monkeypatch)
    assert jxl.read_bytes() == before, "the only copy was replaced by a damaged one"
    assert not list(src_dir.glob("*.tmp")), "a temp was left beside the source"


# ===========================================================================
# R-B (#574)
# ===========================================================================

def _split(tmp_path):
    a = tmp_path / HOSTILE / "scan [x] 吾妻"
    a.mkdir(parents=True)
    tif = a / "scan 0001 (1).tif"
    with tifffile.TiffWriter(str(tif)) as w:
        w.write(np.full((64, 96, 3), 20000, np.uint16), photometric="rgb")
        w.write(np.full((64, 96, 3), 40000, np.uint16), photometric="rgb")
    _run("jxl_tiff_encoder.py", a, "--mode", "1", "--distance", "0", "--effort", "3",
         "--no-preflight")
    out = a / "converted_jxl"
    return out / "scan 0001 (1).jxl", out / "scan 0001 (1)_page1.jxl"


@real
def test_rb_lone_page_of_a_split_is_kept(tmp_path, monkeypatch):
    p0, p1 = _split(tmp_path)
    dest = tmp_path / HOSTILE / "small 5%"
    _main([p0, dest, "--mode", "0", "--distance", "1", "--delete-source",
           "--delete-confirm-off"], monkeypatch)
    assert (dest / p0.name).exists(), "fixture: page 0 was not recompressed"
    assert p0.exists(), "a lone page of a two-page document was deleted"
    assert p1.exists()


@real
def test_rb_control_whole_group_is_deleted(tmp_path, monkeypatch):
    p0, p1 = _split(tmp_path)
    dest = tmp_path / HOSTILE / "small (2)"
    _main([p0.parent, dest, "--mode", "0", "--distance", "1", "--delete-source",
           "--delete-confirm-off"], monkeypatch)
    assert (dest / p0.name).exists() and (dest / p1.name).exists()
    assert not p0.exists() and not p1.exists(), "the whole group should be deleted"


