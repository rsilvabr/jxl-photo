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


def _slow_disk(monkeypatch, secs_per_batch):
    """Every exiftool batch 'takes' secs_per_batch on a fake clock."""
    from types import SimpleNamespace
    clock = {"t": 1000.0}
    calls = []

    def fake_run(cmd, timeout, text=False, input=None):
        calls.append(cmd)
        clock["t"] += secs_per_batch
        return rec._CompletedProcess(cmd, 0, "[]", "")

    monkeypatch.setattr(rec, "_run_captured", fake_run)
    monkeypatch.setattr(rec, "time", SimpleNamespace(monotonic=lambda: clock["t"]))
    return calls


def test_slow_encode_record_read_reports_progress(monkeypatch, caplog):
    # A hard disk: ~0.25 s per file, so a batch of 100 is ~25-30 s. The plan
    # said nothing for minutes between "Planning N file(s)" and "Planned in".
    calls = _slow_disk(monkeypatch, 30.0)
    paths = [f"F:/x/{n:04d}.jxl" for n in range(350)]
    with caplog.at_level(logging.INFO, logger=rec.logger.name):
        info = rec._read_encode_params_batch(paths)
    assert len(info) == 350
    assert len(calls) == -(-350 // rec._ENCODE_RECORD_BATCH)
    lines = re.findall(r"Encode records: (\d+)/350 read \((\S+), ~(\S+) left\)",
                       caplog.text)
    assert lines, caplog.text
    # 100 files in 30 s -> the other 250 at the same rate: 75 s, "1m".
    assert lines[0] == ("100", "30s", "1m"), lines
    # Never a line for the last batch: "Planned in" closes the phase.
    assert all(int(done) < 350 for done, _, _ in lines), lines


@pytest.mark.parametrize("secs,text", [
    (0, "0s"), (-3, "0s"), (42.4, "42s"), (59.4, "59s"), (75, "1m"),
    (89, "1m"), (90, "2m"), (362, "6m"),
])
def test_fmt_eta(secs, text):
    assert rec._fmt_eta(secs) == text


def test_fast_encode_record_read_stays_quiet(monkeypatch, caplog):
    _slow_disk(monkeypatch, 0.1)   # a warm cache
    paths = [f"F:/x/{n:04d}.jxl" for n in range(350)]
    with caplog.at_level(logging.INFO, logger=rec.logger.name):
        rec._read_encode_params_batch(paths)
    assert "Encode records:" not in caplog.text, caplog.text
