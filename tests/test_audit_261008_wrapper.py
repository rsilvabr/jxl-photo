#!/usr/bin/env python3
"""Wrapper items of the 2026-10-08 audit.

  W5  the [D] panel ("About to delete originals") counts the files in Step 4,
      before Step 5 sets the export marker / mode-7 subfolder / output folder:
      reproduced with real photos, panel 3, run 2. Step 7 now recounts with
      the final settings and flags the difference.
  W6  "Convert to sRGB?" + [D] sent --icc-profile with --delete-source on the
      direct route (the manifest's OutputICC rows never did): the transcoder
      deleted the 16-bit master after writing an 8-bit sRGB JPEG. A colour
      conversion is a derivative: the wrapper refuses the pair before the
      HHMM token, and the transcoder refuses it too (exit 2).

Pre-fix proof: point the tests at the HEAD scripts
    $env:JXLPHOTO_SCRIPTS_UNDER_TEST = "<tmp>"   (git show HEAD:<script> ...)
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)


def _load_wrapper():
    """The wrapper under test, loaded from SCRIPTS (its children are imported
    in process from the same folder)."""
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(
            "w261008_jxl_photo", str(SCRIPTS / "jxl_photo.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(SCRIPTS))
    return mod


@pytest.fixture
def wp():
    return _load_wrapper()


@pytest.fixture
def menu(wp, tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


def _tif(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.zeros((8, 8, 3), np.uint16), photometric="rgb")


def test_w5_step7_recounts_with_the_mode7_subfolder(wp, menu, tmp_path, monkeypatch, capsys):
    root = tmp_path / "shoot"
    _tif(root / "_EXPORT" / "TIFF16" / "a.tif")
    _tif(root / "_EXPORT" / "TIFF16" / "b.tif")
    _tif(root / "_EXPORT" / "Other" / "c.tif")
    wf = {"origin_format": "tiff", "dest_format": "jxl", "input_dir": str(root),
          "mode": 7, "conversion_type": "jxl_tiff_encoder", "workers": 2,
          "effort": 7, "distance": 0.1, "staging": "",
          "mode_config": {"export_subfolder": "TIFF16"},
          "advanced_options": {"delete_source": True}}
    # What the [D] panel counted in Step 4, before Step 5 existed.
    panel = menu._count_origin_files(dict(wf, mode_config={}), 7)
    assert panel == 3
    menu._delete_panel_count = panel
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr("builtins.input", lambda *a: "NO")
    assert menu._wizard_confirm(wf) is False
    out = " ".join(capsys.readouterr().out.split())
    assert "2 TIFF file(s) in scope" in out, out
    assert "counted 3" in out, out


def test_w6_direct_route_refuses_srgb_conversion_with_delete(wp, menu, monkeypatch):
    launched = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600:
                        (launched.append(list(map(str, cmd))), 0)[1])
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode", lambda self: True,
                        raising=False)
    monkeypatch.setattr("builtins.input", lambda *a: "")
    wf = {"mode": 3, "origin_format": "jxl", "dest_format": "jpeg",
          "input_dir": ".", "workers": 2, "effort": 7, "distance": 1.0,
          "quality": 95, "staging": "", "compression": "zip", "bit_depth": 8,
          "conversion_type": "jxl_to_jpeg_force", "icc_profile": "sRGB",
          "advanced_options": {"delete_source": True}}
    status = {"cjxl": True, "djxl": True, "exiftool": True, "magick": True}
    menu.execute_workflow(wf, status)
    assert not any("--delete-source" in c for c in launched), launched


def test_w6_transcoder_refuses_icc_profile_with_delete_source(tmp_path):
    (tmp_path / "in").mkdir()
    r = subprocess.run([sys.executable, str(SCRIPTS / "jxl_jpeg_transcoder.py"),
                        str(tmp_path / "in"), "--force-convert", "--decode",
                        "--to-srgb", "--delete-source", "--delete-confirm-off"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       timeout=300)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "DERIVATIVE" in (r.stdout + r.stderr)
