#!/usr/bin/env python3
"""Item 7 of the 2026-10-08 audit (settings literals and wrapper robustness).

  E7  the encoder skipped the decoder's output folders by a LITERAL copy of
      their default names: a user who renamed them in jxl_tiff_decoder.py had
      the decoded TIFFs re-encoded by modes 6/7 (a lossy generation more).
  W1  the wrapper's _dest_folder_names (the "About to delete originals" panel,
      the previews) repeated the children's default folder names.
  W3  the idle-timeout kill took down the Python child only; cjxl/djxl it had
      started kept running (and left their temps beside the finals).
  W4  JXL -> TIFF with decode mode 'none' and [D] went all the way to the
      child (which now refuses it, D2) instead of being refused up front.

Pre-fix proof: $env:JXLPHOTO_SCRIPTS_UNDER_TEST = "<tmp>" (HEAD scripts).
"""

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_wrapper():
    sys.path.insert(0, str(SCRIPTS))
    try:
        return _load(SCRIPTS / "jxl_photo.py", "i7_jxl_photo")
    finally:
        sys.path.remove(str(SCRIPTS))


def test_e7_encoder_skips_the_decoders_configured_folder(tmp_path):
    (tmp_path / "jxl_tiff_encoder.py").write_text(
        (SCRIPTS / "jxl_tiff_encoder.py").read_text(encoding="utf-8"), encoding="utf-8")
    dec = (SCRIPTS / "jxl_tiff_decoder.py").read_text(encoding="utf-8")
    assert dec.count('EXPORT_TIFF_FOLDER = "16B_TIFF"') == 1
    (tmp_path / "jxl_tiff_decoder.py").write_text(
        dec.replace('EXPORT_TIFF_FOLDER = "16B_TIFF"', 'EXPORT_TIFF_FOLDER = "Decoded_TIFF"'),
        encoding="utf-8")
    enc = _load(tmp_path / "jxl_tiff_encoder.py", "i7_encoder_copy")
    assert "decoded_tiff" in enc._DECODER_OUTPUT_FOLDERS
    assert "16b_tiff" in enc._DECODER_OUTPUT_FOLDERS      # the defaults stay
    assert enc._skip_decoder_output(["decoded_tiff"], honor_requested_subfolder=False)


def test_w1_dest_folder_names_follow_the_childs_setting(monkeypatch):
    wp = _load_wrapper()
    import jxl_tiff_decoder as dec_mod
    import jxl_recompressor as rec_mod
    monkeypatch.setattr(dec_mod, "CONVERTED_TIFF_FOLDER", "decoded_here")
    monkeypatch.setattr(rec_mod, "JXL_FOLDER_NAME", "Smaller_JXL")
    assert wp._dest_folder_names("jxl", "tiff")[0] == "decoded_here"
    assert wp._dest_folder_names("jxl", "jxl")[1] == "Smaller_JXL"


@pytest.mark.skipif(os.name != "nt", reason="the tree kill is Windows-specific")
def test_w3_the_kill_takes_the_grandchildren_too(tmp_path):
    wp = _load_wrapper()
    pidfile = tmp_path / "grandchild.pid"
    code = ("import subprocess, sys, time; "
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
            f"open(r'{pidfile}', 'w').write(str(p.pid)); time.sleep(120)")
    child = subprocess.Popen([sys.executable, "-c", code])
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        time.sleep(0.1)
    gpid = int(pidfile.read_text())
    wp._kill_process_tree(child)
    child.wait(timeout=30)
    time.sleep(1.0)
    alive = str(gpid) in subprocess.run(
        ["tasklist", "/FI", f"PID eq {gpid}", "/NH"],
        capture_output=True, text=True).stdout
    if alive:      # do not leave it behind whatever the verdict
        subprocess.run(["taskkill", "/F", "/PID", str(gpid)], capture_output=True)
    assert not alive, "the grandchild survived the kill"


def test_w4_none_decode_with_delete_is_refused_before_launch(tmp_path, monkeypatch):
    wp = _load_wrapper()
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    launched = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600:
                        (launched.append(list(map(str, cmd))), 0)[1])
    monkeypatch.setattr("builtins.input", lambda *a: "")
    # Get past the HHMM proof of presence: the refusal under test must come
    # from the delete + 'none' rule, not from an unanswered token.
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_hhmm", lambda self, *a, **k: True)
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode", lambda self: True,
                        raising=False)
    wf = {"mode": 3, "origin_format": "jxl", "dest_format": "tiff", "input_dir": ".",
          "workers": 2, "effort": 7, "distance": 1.0, "quality": 95, "staging": "",
          "compression": "zip", "bit_depth": 16,
          "conversion_type": "jxl_tiff_decoder",
          "advanced_options": {"delete_source": True, "none": True}}
    status = {"cjxl": True, "djxl": True, "exiftool": True, "magick": True}
    assert menu.execute_workflow(wf, status) is False
    assert not launched, launched


def test_w10_auto_mode_looks_at_every_export_folder(tmp_path):
    """Three export folders keep their TIFFs in TIFF16/, the fourth in Other/:
    mode 7 (one subfolder name for the whole run) would skip the fourth's
    files. Before the fix only the first three folders were looked at."""
    import numpy as np
    import tifffile
    wp = _load_wrapper()
    for i, sub in enumerate(["TIFF16", "TIFF16", "TIFF16", "Other"]):
        p = tmp_path / f"shoot{i}" / "_EXPORT" / sub / f"a{i}.tif"
        p.parent.mkdir(parents=True)
        tifffile.imwrite(str(p), np.zeros((8, 8, 3), np.uint16), photometric="rgb")
    result = wp.FolderAnalyzer(tmp_path, "tiff", "jxl").analyze()
    assert result["recommended_mode"] != 7, result


def test_w8_killed_delete_entry_is_flagged_in_the_recap(tmp_path, monkeypatch):
    wp = _load_wrapper()
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    reports = [{"index": 1, "mode": 8, "source": "A", "state": "killed",
                "summary": None, "deletes": True}]
    totals = {"ok": 0, "overwritten": 0, "skipped": 0, "unreadable": 0, "errors": 1}
    lines = menu._build_manifest_summary_lines(reports, totals, {}, [], [], [],
                                               0, 0, 1, False, False)
    text = " ".join(t for t, _s in lines)
    assert "NOT COUNTED" in text, text
