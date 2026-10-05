#!/usr/bin/env python3
"""Round 45 — the log folders follow JXLPHOTO_LOG_DIR (N7).

The suite and the real-photo battery set the environment variable so their runs
never land in `Logs\\` next to the scripts (that folder had grown to ~25 700
files, synced by OneDrive). Without the variable the paths are unchanged.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_jpeg_transcoder as tr
import jxl_recompressor as rec
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("mod,attr,leaf", [
    ("jxl_tiff_encoder", "LOG_DIR", "jxl_tiff_encoder"),
    ("jxl_tiff_decoder", "LOG_DIR", "jxl_tiff_decoder"),
    ("jxl_jpeg_transcoder", "LOG_DIR", "jxl_jpeg_transcoder"),
    ("jxl_recompressor", "LOG_DIR", "jxl_recompressor"),
    ("jxl_photo", "WRAPPER_LOG_DIR", "jxl_photo"),
])
def test_log_dir_follows_the_environment(tmp_path, mod, attr, leaf):
    """Each backend (and the wrapper) reads the variable at import time.

    A subprocess is required: `LOG_DIR` is computed at import, so an in-process
    monkeypatch of os.environ cannot move it.
    """
    code = f"import {mod}; print({mod}.{attr})"
    r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace",
                       env={**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path)})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == str(tmp_path / leaf)


@pytest.mark.parametrize("mod", [enc, tr, rec],
                         ids=["encoder", "transcoder", "recompressor"])
def test_rejected_files_log_follows_log_dir(mod, tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "LOG_DIR", tmp_path)
    mod._log_rejected_file(Path("x.tif"), "test reason")
    assert (tmp_path / "rejected_files.log").exists()


def test_the_suite_logs_outside_the_repository():
    assert "JXLPHOTO_LOG_DIR" in os.environ
    log_dir = Path(os.environ["JXLPHOTO_LOG_DIR"]).resolve()
    assert not log_dir.is_relative_to(REPO.resolve())
