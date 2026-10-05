#!/usr/bin/env python3
"""The recompressor announces its planning phase and times it (#477).

Planning reads every source with exiftool (the encode record) and walks its
boxes (jbrd) before the first [n/total] line. On a hard disk that is
~0.15-0.25 s per file: the scheduled MOBILE runs sat 105 s, 285 s and 466 s
(3327 files) with nothing on screen after "JXLs found". The run now says what
it is doing and logs how long each part took.
"""

import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_recompressor as rec


@pytest.mark.parametrize("secs,text", [
    (0, "0s"), (42.4, "42s"), (59.6, "1m00s"), (180, "3m00s"), (466, "7m46s"),
])
def test_fmt_secs(secs, text):
    assert rec._fmt_secs(secs) == text


def test_planning_is_announced_and_timed(tmp_path, monkeypatch, caplog):
    for tool in ("cjxl", "exiftool"):
        if shutil.which(tool) is None:
            pytest.skip(f"needs {tool}")
    from PIL import Image

    src = tmp_path / "src"
    src.mkdir()
    png = tmp_path / "a.png"
    img = np.zeros((32, 48, 3), dtype=np.uint8)
    img[..., 0] = np.arange(48, dtype=np.uint8) * 5
    img[..., 1] = 120
    Image.fromarray(img).save(png)
    r = subprocess.run(["cjxl", str(png), str(src / "a.jxl"), "-d", "1", "-e", "1"],
                       capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr

    monkeypatch.setattr(sys, "argv", [
        "jxl_recompressor.py", str(src), "--mode", "1", "--distance", "2",
        "--effort", "3", "--on-unknown", "convert", "--no-preflight", "--dry-run"])
    with caplog.at_level(logging.INFO, logger=rec.logger.name):
        with pytest.raises(SystemExit) as e:
            rec.main()
    assert e.value.code in (0, None)

    text = caplog.text
    assert "Planning 1 file(s): reading each one's encode record" in text, text
    m = re.search(r"Planned in (\S+) \(encode records (\S+), jbrd check (\S+), "
                  r"output checks (\S+)\)", text)
    assert m, text
    # Order: announced after discovery, reported before the plan is printed.
    assert (text.index("JXLs found") < text.index("Planning 1 file(s)")
            < text.index("Planned in") < text.index(" DRY | "))
