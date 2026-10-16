#!/usr/bin/env python3
"""v2.15.0: --restamp-then-sync — fix moved records, then sync, in one pass.

Real codecs (cjxl/djxl/exiftool); each test in its own tmp_path. The helpers
are copies of tests/test_overwrite_guard.py (R6), not imports from it.

Pre-fix proof (must fail on the 4921caf encoder):

    mkdir <tmp>\\rtspre
    git show 4921caf:jxl_tiff_encoder.py > <tmp>\\rtspre\\jxl_tiff_encoder.py
    $env:RTS_ENCODER = "<tmp>\\rtspre\\jxl_tiff_encoder.py"
    python -m pytest tests/test_restamp_then_sync.py -q
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import tifffile

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import jxl_tiff_encoder as enc  # noqa: E402  (only for _source_path_id)

ENCODER = os.environ.get("RTS_ENCODER") or str(REPO / "jxl_tiff_encoder.py")

_HAVE_TOOLS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
real = pytest.mark.skipif(not _HAVE_TOOLS,
                          reason="needs cjxl, djxl and exiftool on PATH")

FAST = ["--distance", "1", "--effort", "3", "--no-preflight"]


def _run(script, *args, cwd=None):
    return subprocess.run([sys.executable, str(script)] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=600,
                          cwd=str(cwd) if cwd else None,
                          stdin=subprocess.DEVNULL)


def _src_marker(path):
    r = subprocess.run(["exiftool", "-j", "-XMP-dc:Relation", str(path)],
                       capture_output=True, text=True, check=True)
    rel = json.loads(r.stdout)[0].get("Relation") or []
    rel = rel if isinstance(rel, list) else [rel]
    return [t for t in rel if str(t).startswith("jxlphoto-src:")]


def _md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _tiff(path: Path, value: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.full((48, 64, 3), value, np.uint16),
                     photometric="rgb")


def _history_len(path: Path) -> int:
    r = subprocess.run(["exiftool", "-struct", "-j", "-XMP-xmpMM:History",
                        str(path)], capture_output=True, text=True, check=True)
    h = json.loads(r.stdout)[0].get("History")
    return 0 if h is None else (len(h) if isinstance(h, list) else 1)


def _djxl_pixel_md5(jxl: Path, out_png: Path) -> str:
    """md5 of the DECODED PIXELS (a restamp changes the PNG metadata only)."""
    from PIL import Image
    r = subprocess.run(["djxl", str(jxl), str(out_png)], capture_output=True)
    assert r.returncode == 0, f"djxl failed on {jxl}"
    with Image.open(out_png) as im:
        return hashlib.md5(im.tobytes()).hexdigest()


def _layout(tmp_path):
    """Encode lib/2024-05 Viagem/p.tif, then rename its folder to Kyoto."""
    d = tmp_path / "lib" / "2024-05 Viagem"
    _tiff(d / "p.tif", 1000)
    r = _run(ENCODER, "lib", "--mode", "8", *FAST, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    d.rename(tmp_path / "lib" / "2024-05 Kyoto")
    new_d = tmp_path / "lib" / "2024-05 Kyoto"
    return new_d / "p.tif", new_d / "p.jxl"


@real
def test_s1_unchanged_renamed_guard_on(tmp_path):
    tif, jxl = _layout(tmp_path)
    mtime = jxl.stat().st_mtime_ns
    png_md5 = _djxl_pixel_md5(jxl, tmp_path / "before.png")
    hist = _history_len(jxl)

    r = _run(ENCODER, "lib", "--mode", "8", *FAST, "--restamp-then-sync", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESTAMP |" in r.stdout, r.stdout
    assert "SYNC done: 0 reconverted" in r.stdout, r.stdout
    assert _src_marker(jxl) == ["jxlphoto-src:" + enc._source_path_id(tif)]
    assert jxl.stat().st_mtime_ns == mtime, "the restamp changed the file date"
    assert _djxl_pixel_md5(jxl, tmp_path / "after.png") == png_md5
    assert _history_len(jxl) == hist, "the restamp wrote an xmpMM:History entry"


@real
def test_s2_touched_same_content_is_reconverted_not_refused(tmp_path):
    """The ORDER test: the restamp must run before the guard reads the record."""
    tif, jxl = _layout(tmp_path)
    t = time.time() + 60
    os.utime(tif, (t, t))

    r = _run(ENCODER, "lib", "--mode", "8", *FAST, "--restamp-then-sync", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESTAMP |" in r.stdout, r.stdout
    assert "SYNC done: 1 reconverted" in r.stdout, r.stdout
    assert "REFUSING" not in r.stdout, r.stdout
    assert _src_marker(jxl) == ["jxlphoto-src:" + enc._source_path_id(tif)]


@real
def test_s3_new_tiff_next_to_the_renamed_one(tmp_path):
    tif, jxl = _layout(tmp_path)
    mtime = jxl.stat().st_mtime_ns
    _tiff(tif.parent / "q.tif", 2000)

    r = _run(ENCODER, "lib", "--mode", "8", *FAST, "--restamp-then-sync", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tif.parent / "q.jxl").exists()
    assert _src_marker(jxl) == ["jxlphoto-src:" + enc._source_path_id(tif)]
    assert jxl.stat().st_mtime_ns == mtime


@real
def test_s4_moved_and_edited_guard_on_is_refused(tmp_path):
    tif, jxl = _layout(tmp_path)
    _tiff(tif, 60000)                       # renamed AND edited since
    t = time.time() + 60
    os.utime(tif, (t, t))
    before = _md5(jxl)

    r = _run(ENCODER, "lib", "--mode", "8", *FAST, "--restamp-then-sync", cwd=tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "NOT restamped" in r.stdout, r.stdout
    assert "REFUSING" in r.stdout, r.stdout
    assert _md5(jxl) == before


@real
def test_s4b_moved_and_edited_guard_off_is_overwritten(tmp_path):
    tif, jxl = _layout(tmp_path)
    _tiff(tif, 60000)
    t = time.time() + 60
    os.utime(tif, (t, t))

    r = _run(ENCODER, "lib", "--mode", "8", *FAST, "--restamp-then-sync",
             "--no-overwrite-guard", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "overwrite guard off" in r.stdout, r.stdout
    assert _src_marker(jxl) == ["jxlphoto-src:" + enc._source_path_id(tif)]


@real
def test_s5_dry_run_changes_nothing_and_predicts_no_refusal(tmp_path):
    tif, jxl = _layout(tmp_path)
    t = time.time() + 60
    os.utime(tif, (t, t))
    before = _md5(jxl)

    r = _run(ENCODER, "lib", "--mode", "8", *FAST, "--restamp-then-sync", "--dry-run",
             cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "would be rewritten" in r.stdout, r.stdout
    assert "REFUSING" not in r.stdout, r.stdout
    assert _md5(jxl) == before


@real
@pytest.mark.parametrize("flag", ["--delete-source", "--delete-skipped",
                                  "--overwrite", "--restamp-moved"])
def test_s6_refused_with_destructive_or_restamp_only_flags(tmp_path, flag):
    tif, jxl = _layout(tmp_path)
    before = _md5(jxl)
    r = _run(ENCODER, "lib", "--mode", "8", *FAST, "--restamp-then-sync", flag,
             cwd=tmp_path)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "--restamp-then-sync cannot be combined" in (r.stdout + r.stderr), \
        r.stdout + r.stderr
    assert _md5(jxl) == before


_WRAPPER = """\
import sys
sys.path.insert(0, {encoder_dir!r})
import jxl_tiff_encoder as enc
enc._rewrite_master_relation = lambda *a, **k: "simulated failure"
sys.argv = [{script!r}] + sys.argv[1:]
enc.main()
"""


@real
def test_s7_a_failed_rewrite_is_an_error(tmp_path):
    tif, jxl = _layout(tmp_path)
    wrapper = tmp_path / "failing_restamp.py"
    # The wrapper must load the SAME encoder file the test runs (the pre-fix
    # override included), not the repo copy.
    wrapper.write_text(_WRAPPER.format(encoder_dir=str(Path(ENCODER).resolve().parent),
                                       script=ENCODER),
                       encoding="utf-8")
    r = _run(wrapper, "lib", "--mode", "8", *FAST, "--restamp-then-sync",
             "--summary-json", cwd=tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "RESTAMP FAILED" in r.stdout, r.stdout
    summary = [json.loads(line[len("##JXLSUM## "):])
               for line in r.stdout.splitlines() if line.startswith("##JXLSUM## ")]
    assert summary, r.stdout
    assert summary[-1]["errors"] >= 1, summary[-1]
    reasons = [f["reason"] for f in summary[-1]["failures"]]
    assert "restamp failed: simulated failure" in reasons, reasons
