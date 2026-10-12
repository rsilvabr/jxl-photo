#!/usr/bin/env python3
"""v2.12.0: --embed-thumbnail no longer writes an EXIF thumbnail.

The encoder no longer generates or embeds the thumbnail (no viewer tested
reads an EXIF thumbnail in a JXL), but the flag stays ACCEPTED: a manifest,
an old preset, `last_expert_flags` or a user script may still pass it, and
without the argument argparse would exit 2. It only logs a warning.

Real-codec test: the encoder runs as a subprocess against a real TIFF and the
output JXL is probed with exiftool. Pre-fix proof (must fail on the check at
the end — the old encoder embeds a thumbnail and logs nothing):

    mkdir <tmp>\\old
    git show aa35726:jxl_tiff_encoder.py > <tmp>\\old\\jxl_tiff_encoder.py
    $env:JXL_ENCODER_UNDER_TEST = "<tmp>\\old\\jxl_tiff_encoder.py"
    python -m pytest tests/test_embed_thumbnail_removed.py -q
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
ENCODER = (Path(os.environ["JXL_ENCODER_UNDER_TEST"])
           if os.environ.get("JXL_ENCODER_UNDER_TEST")
           else REPO / "jxl_tiff_encoder.py")

_HAS_TOOLS = all(shutil.which(t) for t in ("cjxl", "exiftool"))
needs_codecs = pytest.mark.skipif(
    not _HAS_TOOLS, reason="needs cjxl and exiftool on PATH")


@needs_codecs
def test_embed_thumbnail_flag_is_accepted_and_writes_nothing(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out_dir = tmp_path / "out"

    rows = np.linspace(0, 65535, 64, dtype=np.uint16)
    arr = np.stack([np.tile(rows, (64, 1)),
                    np.tile(rows[:, None], (1, 64)),
                    np.full((64, 64), 32768, dtype=np.uint16)], axis=-1)
    tifffile.imwrite(in_dir / "a.tif", arr, photometric="rgb")

    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs")}
    r = subprocess.run(
        [sys.executable, str(ENCODER), str(in_dir), str(out_dir),
         "--mode", "0", "--distance", "1", "--effort", "3",
         "--embed-thumbnail"],
        capture_output=True, text=True, timeout=300, env=env)

    assert r.returncode == 0, r.stdout + r.stderr
    jxl = out_dir / "a.jxl"
    assert jxl.exists(), r.stdout + r.stderr

    probe = subprocess.run(
        [shutil.which("exiftool") or "exiftool", "-s3", "-ThumbnailLength",
         str(jxl)],
        capture_output=True, text=True, timeout=60)
    assert probe.stdout.strip() == "", (
        f"an EXIF thumbnail was written: {probe.stdout!r}")

    log = r.stdout + r.stderr
    assert "--embed-thumbnail is ignored" in log, log
