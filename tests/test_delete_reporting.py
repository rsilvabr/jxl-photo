#!/usr/bin/env python3
"""Reporting around the deletion (bugs #266, #267, #269, #270).

The delete gates were correct but silent:

  #266 `deleted`/`kept` were locals in process_group, so emit_summary_json had
       no count and the wrapper's end-of-manifest recap never said how many
       originals were removed — nor how many were KEPT by a refusing gate,
       which is the number that says something needs looking at.
  #267 a dry run of a --delete-source run printed the planned outputs and
       stopped. The flag that destroys originals was the one thing the
       simulation never mentioned.
  #269 the [D] confirmation counted by extension, ignoring the marker and
       tool-output filters, so mode 6 announced 23 files for a run that
       touches 3. That count is what makes a wrong folder visible.
  #270 a MIXED source (one page skipped, one freshly converted) was labelled
       "already archived".
"""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_jpeg_transcoder as tr
import jxl_photo as wp
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent


def _tiff(path: Path, value: int = 1000):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.full((16, 16, 3), value, np.uint16),
                     photometric="rgb")


def _run(*args, cwd):
    return subprocess.run([sys.executable, str(REPO / "jxl_tiff_encoder.py"), *args],
                          capture_output=True, text=True, timeout=600,
                          cwd=str(cwd), stdin=subprocess.DEVNULL)


# ---------------------------------------------------------------------------
# #267 — the dry run must say deletion is armed
# ---------------------------------------------------------------------------

def test_dry_run_announces_the_deletion(tmp_path):
    _tiff(tmp_path / "src" / "a.tif")
    _tiff(tmp_path / "src" / "b.tif", 2000)
    r = _run("src", "out", "--mode", "2", "--distance", "0", "--delete-source",
             "--delete-confirm-off", "--dry-run", cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    assert "--delete-source is ARMED" in r.stdout
    assert "2 source TIFF(s) would be DELETED" in r.stdout
    assert (tmp_path / "src" / "a.tif").exists(), "a dry run deleted something"


def test_dry_run_mentions_the_roundtrip_gate_when_armed(tmp_path):
    _tiff(tmp_path / "src" / "a.tif")
    r = _run("src", "out", "--mode", "2", "--distance", "0", "--delete-source",
             "--delete-confirm-off", "--verify-roundtrip", "--dry-run", cwd=tmp_path)
    assert "round-trip comparison" in r.stdout


def test_dry_run_stays_quiet_without_delete(tmp_path):
    _tiff(tmp_path / "src" / "a.tif")
    r = _run("src", "out", "--mode", "2", "--distance", "0", "--dry-run", cwd=tmp_path)
    assert "ARMED" not in r.stdout


# ---------------------------------------------------------------------------
# #266 — the counts reach the summary and the screen
# ---------------------------------------------------------------------------

def test_summary_reports_what_was_deleted(tmp_path):
    _tiff(tmp_path / "src" / "a.tif")
    _tiff(tmp_path / "src" / "b.tif", 2000)
    r = _run("src", "out", "--mode", "2", "--distance", "0", "--delete-source",
             "--delete-confirm-off", "--summary-json", cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    line = [l for l in r.stdout.splitlines() if l.startswith("##JXLSUM## ")][-1]
    extras = json.loads(line[len("##JXLSUM## "):])["extras"]
    assert extras.get("Sources deleted") == 2
    assert "Sources DELETED: 2" in r.stdout, "and it must be on screen too"


def test_summary_reports_sources_kept_by_a_gate(tmp_path, monkeypatch):
    """The number that says 'something needs looking at'."""
    src = tmp_path / "photo.tif"
    _tiff(src)
    final = tmp_path / "out" / "photo.jxl"
    final.parent.mkdir()
    final.write_bytes(b"jxl")

    enc.setup_logger()
    monkeypatch.setattr(enc, "DELETE_SOURCE", True)
    monkeypatch.setattr(enc, "TEMP2_DIR", None)
    monkeypatch.setattr(enc, "VERIFY_ROUNDTRIP", False)
    monkeypatch.setattr(enc, "_delete_stats",
                        {"deleted": 0, "deleted_archived": 0, "kept": 0})
    monkeypatch.setattr(enc, "_verify_jxl_integrity", lambda p: False)
    monkeypatch.setattr(enc, "convert_one",
                        lambda t, w, f, p=0, *a, **k: ((str(t), p), "ok", str(f), t))
    enc.process_group([(src, final, 0, False, 0, 3)], 1, 3)
    assert enc._delete_stats["kept"] == 1
    assert enc._delete_stats["deleted"] == 0


@pytest.mark.parametrize("mod", [enc, dec, tr])
def test_every_script_tracks_delete_stats(mod):
    """A script that forgot the counters would report zeros forever."""
    assert set(mod._delete_stats) == {"deleted", "deleted_archived", "kept"}


# ---------------------------------------------------------------------------
# #270 — a mixed source is not "already archived"
# ---------------------------------------------------------------------------

def test_mixed_source_is_not_labelled_already_archived(tmp_path, monkeypatch, caplog):
    """One page skipped, one freshly converted: a normal delete with a note."""
    src = tmp_path / "scan.tif"
    _tiff(src)
    out = tmp_path / "out"
    out.mkdir()
    f0, f2 = out / "scan.jxl", out / "scan_page2.jxl"
    f0.write_bytes(b"jxl")
    f2.write_bytes(b"jxl")

    enc.setup_logger()
    enc.logger.propagate = True
    monkeypatch.setattr(enc, "DELETE_SOURCE", True)
    monkeypatch.setattr(enc, "DELETE_SKIPPED", True)
    monkeypatch.setattr(enc, "TEMP2_DIR", None)
    monkeypatch.setattr(enc, "VERIFY_ROUNDTRIP", False)
    monkeypatch.setattr(enc, "_delete_stats",
                        {"deleted": 0, "deleted_archived": 0, "kept": 0})
    monkeypatch.setattr(enc, "_verify_jxl_integrity", lambda p: True)
    monkeypatch.setattr(
        enc, "convert_one",
        lambda t, w, f, p=0, *a, **k: (((str(t), p), "skipped", str(f), None)
                                       if p == 0 else
                                       ((str(t), p), "ok", str(f), t)))
    with caplog.at_level("INFO", logger="jxl_convert"):
        enc.process_group([(src, f0, 0, False, 0, 3), (src, f2, 2, False, 0, 3)], 1, 3)

    assert not src.exists()
    assert enc._delete_stats["deleted"] == 1
    assert enc._delete_stats["deleted_archived"] == 0, "a mixed source is not archived"
    assert "partly already archived" in caplog.text


# ---------------------------------------------------------------------------
# #269 — the [D] count matches the child's own finder
# ---------------------------------------------------------------------------

@pytest.fixture
def menu(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


def _tree(root: Path):
    for i in range(20):
        _tiff(root / f"loose{i}.tif")
    for i in range(3):
        _tiff(root / "2024_EXPORT" / "TIFF16" / f"exp{i}.tif")


@pytest.mark.parametrize("mode,finder", [
    (6, "find_tiffs_mode6"),
    (7, "find_tiffs_mode7"),
    (3, "find_tiffs_recursive"),
    (0, "find_files_mode0"),
])
def test_D_count_matches_the_encoder_finder(menu, tmp_path, mode, finder):
    _tree(tmp_path)
    wf = {"input_dir": str(tmp_path), "origin_format": "tiff", "dest_format": "jxl"}
    assert menu._count_origin_files(wf, mode) == len(getattr(enc, finder)(tmp_path))


def test_D_count_is_not_inflated_for_marker_modes(menu, tmp_path):
    """The concrete regression: 23 announced for a run that touches 3."""
    _tree(tmp_path)
    wf = {"input_dir": str(tmp_path), "origin_format": "tiff", "dest_format": "jxl"}
    assert menu._count_origin_files(wf, 6) == 3
    assert menu._count_origin_files(wf, 3) == 23


def test_D_count_matches_the_decoder_finder(menu, tmp_path):
    stub = b"\x00\x00\x00\x0cJXL \r\n\x87\n"
    for i in range(5):
        (tmp_path / f"a{i}.jxl").write_bytes(stub)
    (tmp_path / "_EXPORT").mkdir()
    (tmp_path / "_EXPORT" / "b.jxl").write_bytes(stub)
    wf = {"input_dir": str(tmp_path), "origin_format": "jxl", "dest_format": "tiff"}
    assert menu._count_origin_files(wf, 6) == len(dec.find_jxls_mode6(tmp_path))
    assert menu._count_origin_files(wf, 3) == len(dec.find_jxls_recursive(tmp_path))


def test_D_count_uses_the_configured_marker(menu, tmp_path):
    """A custom marker must be honoured, or the preview counts the wrong tree."""
    for i in range(4):
        _tiff(tmp_path / "MEU_EXPORT" / f"x{i}.tif")
    _tiff(tmp_path / "loose.tif")
    menu.config.config.export_marker = "MEU_EXPORT"
    wf = {"input_dir": str(tmp_path), "origin_format": "tiff", "dest_format": "jxl"}
    assert menu._count_origin_files(wf, 6) == 4


def test_D_count_does_not_leak_the_marker_into_the_child(menu, tmp_path):
    """It mutates the child's global to ask the question; it must put it back."""
    before = enc.EXPORT_MARKER
    menu.config.config.export_marker = "OUTRO"
    _tiff(tmp_path / "a.tif")
    menu._count_origin_files({"input_dir": str(tmp_path), "origin_format": "tiff",
                              "dest_format": "jxl"}, 6)
    assert enc.EXPORT_MARKER == before


def test_D_count_survives_an_unreadable_folder(menu):
    wf = {"input_dir": "//nope/nope", "origin_format": "tiff", "dest_format": "jxl"}
    assert menu._count_origin_files(wf, 3) in (-1, 0)
