#!/usr/bin/env python3
"""A deleting auto-mode transcoder run acts in one direction only (#542).

Auto mode converts both ways in one folder. Armed with --delete-source it
decoded a lossy 16-bit master (a TIFF-encoder JXL, no jbrd) into an 8-bit JPEG
and deleted the master, and the next run of the same command turned the
archived JXLs back into JPEGs. A deleting run now decodes JXLs only when that
direction was asked for (--decode or --from-jxl).
"""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

REPO = Path(__file__).resolve().parent.parent
_HAVE = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool", "magick"))
real = pytest.mark.skipif(not _HAVE, reason="needs cjxl, djxl, exiftool and magick on PATH")


def _md5(p):
    return hashlib.md5(Path(p).read_bytes()).hexdigest()


def _run(script, args, env):
    return subprocess.run([sys.executable, str(REPO / script)] + [str(a) for a in args],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=600)


@real
def test_deleting_auto_run_never_decodes_a_lossy_master(tmp_path):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs")}
    src = tmp_path / "src 吾妻山 [x]"
    src.mkdir()
    rng = np.random.default_rng(7)
    tifffile.imwrite(src / "master.tif",
                     rng.integers(0, 65535, (96, 128, 3), dtype=np.uint16), photometric="rgb")
    r = _run("jxl_tiff_encoder.py", [src, "--mode", "1", "--distance", "0.5", "--effort", "1",
                                     "--no-preflight"], env)
    assert r.returncode == 0, r.stdout[-1500:]

    d = tmp_path / "folder 京都 [M] 1%"
    d.mkdir()
    shutil.copy2(src / "converted_jxl" / "master.jxl", d / "master.jxl")
    png = tmp_path / "cam.png"
    tifffile.imwrite(tmp_path / "cam.tif",
                     rng.integers(0, 255, (96, 128, 3), dtype=np.uint8), photometric="rgb")
    subprocess.run(["magick", str(tmp_path / "cam.tif"), str(png)], check=True)
    subprocess.run(["magick", str(png), "-quality", "90", str(tmp_path / "camera.jpg")],
                   check=True)
    shutil.copy2(tmp_path / "camera.jpg", d / "camera.jpg")
    master_md5 = _md5(d / "master.jxl")

    args = [d, "--mode", "8", "--delete-source", "--delete-confirm-off"]
    r = _run("jxl_jpeg_transcoder.py", args, env)
    out = r.stdout + r.stderr
    assert (d / "master.jxl").exists() and _md5(d / "master.jxl") == master_md5, out[-2000:]
    assert not (d / "master.jpg").exists(), out[-2000:]
    assert (d / "camera.jxl").exists() and not (d / "camera.jpg").exists(), out[-2000:]

    # The same command again (a scheduled task): nothing flips back.
    camera_md5 = _md5(d / "camera.jxl")
    r = _run("jxl_jpeg_transcoder.py", args, env)
    out = r.stdout + r.stderr
    assert sorted(p.name for p in d.iterdir() if p.suffix != ".md5") == \
        ["camera.jxl", "master.jxl"], out[-2000:]
    assert _md5(d / "camera.jxl") == camera_md5 and _md5(d / "master.jxl") == master_md5
