"""TEMP_DIR checked before the run (owner's request, 2026-11-08).

1. The transcoder had no check: with TEMP_DIR pointing at a folder that does
   not exist, every file failed mid-run at its first tempfile
   ("[Errno 2] No such file or directory: '<TEMP_DIR>\\tmp...'"). The other
   three create it up front (_check_dir); now the transcoder does too.
2. In all four, a DRY RUN only checked that an existing TEMP_DIR is a folder,
   but the dry run writes into it (exiftool argfiles, the planning reads'
   captured output): every marker read failed and the encoder refused a good
   master as "belongs to a DIFFERENT file". A dry run now stops first, with
   the cause named, and still creates nothing.

Real runs of the scripts (subprocess, real cjxl/exiftool) in a hostile
folder: Japanese, [ ], a space and %.

Pre-fix proof: git show HEAD:<script> for the four into a tmp folder, then
    $env:JXLPHOTO_SCRIPTS_UNDER_TEST = "<tmp>"
    python -m pytest tests/test_work_dir_check.py -q
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)

BACKENDS = ("jxl_tiff_encoder", "jxl_tiff_decoder", "jxl_recompressor", "jxl_jpeg_transcoder")
HOSTILE = "写真 [A] 5% x"

needs_codecs = pytest.mark.skipif(
    not (shutil.which("cjxl") and shutil.which("djxl") and shutil.which("exiftool")),
    reason="needs cjxl, djxl and exiftool")


def _overlay(tmp_path, temp_dir) -> Path:
    path = tmp_path / "overlay.json"
    path.write_text(json.dumps({m: {"TEMP_DIR": str(temp_dir)} for m in BACKENDS}),
                    encoding="utf-8")
    return path


def _run(script, args, overlay, timeout=300):
    env = {**os.environ, "JXLPHOTO_SETTINGS": str(overlay), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, str(SCRIPTS / f"{script}.py"), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=timeout,
                          stdin=subprocess.DEVNULL)


def _jpeg(folder: Path) -> Path:
    from PIL import Image
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "IMG 1 (5%).jpg"
    Image.new("RGB", (64, 48), (200, 30, 10)).save(path, quality=90)
    return path


def _tiff(folder: Path) -> Path:
    import numpy as np
    import tifffile
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "DSC_0001 (2).tif"
    rng = np.random.default_rng(7)
    tifffile.imwrite(path, rng.integers(0, 65535, (48, 64, 3), dtype=np.uint16),
                     photometric="rgb")
    return path


@needs_codecs
def test_transcoder_creates_a_missing_temp_dir(tmp_path):
    root = tmp_path / HOSTILE
    src = _jpeg(root / "jpg [B] 京都")
    temp = root / "temp nao existe [x]" / "sub 5%"
    r = _run("jxl_jpeg_transcoder", [src.parent, "--mode", "0"], _overlay(tmp_path, temp))
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert temp.is_dir()
    assert src.with_suffix(".jxl").is_file(), out


@pytest.mark.parametrize("script", BACKENDS)
def test_a_temp_dir_that_is_a_file_is_refused_before_anything(tmp_path, script):
    root = tmp_path / HOSTILE
    src = _jpeg(root / "in [B] 京都")
    not_a_dir = root / "temp file [x].txt"
    not_a_dir.write_text("x", encoding="utf-8")
    r = _run(script, [src.parent, "--mode", "0"], _overlay(tmp_path, not_a_dir))
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "TEMP_DIR is not usable" in " ".join(out.split()), out
    assert sorted(p.name for p in src.parent.iterdir()) == [src.name]


@needs_codecs
@pytest.mark.parametrize("script", BACKENDS)
def test_a_dry_run_with_a_missing_temp_dir_stops_and_creates_nothing(tmp_path, script):
    root = tmp_path / HOSTILE
    src = (_jpeg if script == "jxl_jpeg_transcoder" else _tiff)(root / "in [B] 京都")
    temp = root / "temp nao existe [x]"
    r = _run(script, [src.parent, "--mode", "0", "--dry-run"], _overlay(tmp_path, temp))
    out = " ".join((r.stdout + r.stderr).split())
    assert r.returncode == 2, out
    assert "TEMP_DIR does not exist" in out and "A dry run creates no folders" in out, out
    assert not temp.exists()


@needs_codecs
def test_encoder_dry_run_over_its_own_master(tmp_path):
    """The case that showed it: a dry run of a sync over a master the encoder
    wrote. With the folder in place the master reads fine (no refusal)."""
    root = tmp_path / HOSTILE
    src = _tiff(root / "_EXPORT [B] 京都")
    temp = root / "temp [x] 5%"
    ov = _overlay(tmp_path, temp)
    r = _run("jxl_tiff_encoder", [src.parent, "--mode", "0"], ov)
    assert r.returncode == 0, r.stdout + r.stderr
    assert src.with_suffix(".jxl").is_file() and temp.is_dir()
    os.utime(src)                                   # newer than its master: a re-encode
    r = _run("jxl_tiff_encoder", [src.parent, "--mode", "0", "--dry-run"], ov)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "REFUSING" not in out and "marker batch failed" not in out, out


# -- the helper itself ------------------------------------------------------

def _helper():
    sys.path.insert(0, str(SCRIPTS))
    import jxl_jpeg_transcoder as t
    return t._work_dir_problem


def test_helper_rules(tmp_path):
    check = _helper()
    missing = tmp_path / HOSTILE / "new [x]"
    assert check(None, "TEMP_DIR", True, True) is None
    # dry run: never creates; a folder it writes into must exist
    assert "does not exist" in check(missing, "TEMP_DIR", True, True)
    assert check(missing, "staging", True, False) is None
    assert not missing.exists()
    # real run: creates
    assert check(missing, "TEMP_DIR", False, True) is None and missing.is_dir()
    assert check(missing, "TEMP_DIR", True, True) is None
    a_file = tmp_path / HOSTILE / "f.txt"
    a_file.write_text("x", encoding="utf-8")
    assert "is not a directory" in check(a_file, "staging", True, False)
    assert "is not usable" in check(a_file, "TEMP_DIR", False, True)
