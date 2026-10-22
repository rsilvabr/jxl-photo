#!/usr/bin/env python3
"""A derivative run that writes nothing for a file says so (round 60).

A derivative (--output-icc/--resize/--sharpen) of a master already at the
requested distance is a "downgrade"; its policy answer copy becomes a skip,
and the run used to count it among the ordinary skips: "Done: 0 recompressed
| ... | 2 skipped" and exit 0, with no derivative on disk. Found by running the
owner's SRGB preset over masters encoded at the same distance.
"""

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


def _run(script, args, env):
    return subprocess.run([sys.executable, str(REPO / script)] + [str(a) for a in args],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=600)


@pytest.mark.skipif(not _HAVE, reason="needs cjxl, djxl, exiftool and magick on PATH")
def test_derivative_run_names_the_files_it_did_not_write(tmp_path):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs")}
    src = tmp_path / "吾妻山 [x] 1%"
    src.mkdir()
    tifffile.imwrite(src / "a.tif", np.random.default_rng(3).integers(
        0, 65535, (64, 96, 3), dtype=np.uint16), photometric="rgb")
    r = _run("jxl_tiff_encoder.py", [src, "--mode", "1", "--distance", "0.5",
                                     "--effort", "1", "--no-preflight"], env)
    assert r.returncode == 0, r.stdout[-1500:]
    masters = src / "converted_jxl"
    r = _run("jxl_recompressor.py", [masters, "--mode", "1", "--output-icc", "sRGB",
                                     "--distance", "0.5", "--effort", "1",
                                     "--on-downgrade", "copy", "--no-preflight"], env)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-2000:]
    assert not [p for p in masters.rglob("*.jxl") if p.parent != masters], out[-2000:]
    assert "1 file(s) got NO derivative" in " ".join(out.split()), out[-2000:]
