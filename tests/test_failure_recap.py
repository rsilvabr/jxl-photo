#!/usr/bin/env python3
"""The failed files named once more at the end of a run (v2.31.0, plan 261121
phase 2): `Errors (N):` and the first ten `  -> name: reason`, at WARNING, for
whoever reads only the last lines of a console or a log. Never ERROR: each
failure already logged its own ERROR line, and photo-backup counts the lines
that start with ERROR (`_JXLPHOTO_ERROR`). Not with --summary-json: the
wizard prints its own list from the summary."""

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import jxl_photo_common as common  # noqa: E402
import jxl_tiff_encoder as enc  # noqa: E402

HOSTILE = "吾妻山 [x] 100%off %d"
# photo-backup's own pattern (selective_backup.py), copied: a line it matches
# is an error of the photo for photo-backup.
_JXLPHOTO_ERROR = re.compile(r"^(?:\d\d:\d\d:\d\d \| )?ERROR\b(?: \| |:\s*)?")


def test_the_recap_names_the_first_ten_at_warning(caplog):
    failures = [(f"C:/{HOSTILE}/DSC_{i:04d} (2).tif", f"reason\n{i}") for i in range(12)]
    with caplog.at_level(logging.DEBUG, logger=enc.logger.name):
        common._log_failure_recap(enc._G, failures)
    lines = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert lines[0] == (logging.WARNING, "Errors (12):")
    named = [m for _lvl, m in lines if m.startswith("  -> ")]
    assert len(named) == 10
    assert named[0] == f"  -> C:/{HOSTILE}/DSC_0000 (2).tif: reason 0"   # one line, the path
    assert lines[-1] == (logging.WARNING, "  ... and 2 more (each one above)")
    assert all(lvl == logging.WARNING for lvl, _m in lines)


def test_no_failure_no_recap(caplog):
    with caplog.at_level(logging.DEBUG, logger=enc.logger.name):
        common._log_failure_recap(enc._G, [])
        common._log_failure_recap(enc._G, None)
    assert caplog.records == []


def _env(tmp_path):
    return dict(os.environ, JXLPHOTO_SETTINGS="", JXLPHOTO_LOG_DIR=str(tmp_path / "logs"),
                JXLPHOTO_JXL_THUMB="0", PYTHONIOENCODING="utf-8")


@pytest.mark.skipif(not all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool")),
                    reason="needs cjxl, djxl and exiftool")
@pytest.mark.parametrize("summary_json", [False, True])
def test_a_real_run_with_a_failed_file(tmp_path, summary_json):
    from PIL import Image
    import numpy as np
    src = tmp_path / HOSTILE / "in"
    src.mkdir(parents=True)
    Image.fromarray(np.zeros((16, 24, 3), np.uint8)).save(src / "DSC_0001 (2).jpg", quality=90)
    (src / "not a jpeg 50%.jpg").write_bytes(os.urandom(3000))
    out = tmp_path / HOSTILE / "out"
    cmd = [sys.executable, str(REPO / "jxl_jpeg_transcoder.py"), str(src), str(out),
           "--mode", "2", "--workers", "1", "--effort", "3", "--force-transcode"]
    if summary_json:
        cmd.append("--summary-json")
    r = subprocess.run(cmd, capture_output=True, timeout=300, env=_env(tmp_path),
                       stdin=subprocess.DEVNULL)
    text = r.stdout.decode("utf-8", "replace")
    assert r.returncode == 1, text[-2000:]
    lines = text.splitlines()
    recap = [ln for ln in lines if "| Errors (" in ln or "|   -> " in ln]
    if summary_json:
        assert recap == [], recap
        return
    assert any(ln.endswith("| WARNING | Errors (1):") for ln in recap), recap
    assert any("|   -> " in ln and "not a jpeg 50%.jpg: " in ln for ln in recap), recap
    assert not [ln for ln in recap if _JXLPHOTO_ERROR.match(ln)]
    log = next((tmp_path / "logs").rglob("*.log")).read_text(encoding="utf-8")
    assert "Errors (1):" in log
