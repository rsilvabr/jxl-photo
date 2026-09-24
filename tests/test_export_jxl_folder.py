#!/usr/bin/env python3
"""`--export-jxl-folder`: the modes 6/7 output folder name is configurable.

Encoder (phase 1) and recompressor (phase 2). The name is created directly
under the export marker, so it must be one plain path component that neither
matches the marker (a second anchor for later scans) nor equals the input
subfolder (outputs among the sources).
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_recompressor as rec
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent


def _jxl_stub(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)


# ---------------------------------------------------------------------------
# Encoder
# ---------------------------------------------------------------------------

def test_encoder_resolve_output_mode6_uses_the_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(enc, "EXPORT_JXL_FOLDER", "PRINT_JXL")
    tif = tmp_path / "_EXPORT" / "TIFF16" / "foto.tif"
    out = enc.resolve_output(tif, 6, tmp_path)
    assert out == tmp_path / "_EXPORT" / "PRINT_JXL" / "foto.jxl"


def test_encoder_resolve_output_mode7_uses_the_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(enc, "EXPORT_JXL_FOLDER", "PRINT_JXL")
    monkeypatch.setattr(enc, "EXPORT_TIFF_SUBFOLDER", "TIFF16")
    tif = tmp_path / "_EXPORT" / "TIFF16" / "foto.tif"
    out = enc.resolve_output(tif, 7, tmp_path)
    assert out == tmp_path / "_EXPORT" / "PRINT_JXL" / "foto.jxl"


def test_encoder_validate_export_folder_name():
    v = enc._validate_export_folder_name
    assert v("", "_EXPORT", "") is not None
    assert v("a/b", "_EXPORT", "") is not None
    assert v("..", "_EXPORT", "") is not None
    assert v("X_EXPORT", "_EXPORT", "") is not None
    assert v("tiff16", "_EXPORT", "TIFF16") is not None
    assert v("PRINT_JXL", "_EXPORT", "TIFF16") is None


def test_encoder_cli_rejects_a_path_in_the_folder_name(tmp_path):
    r = subprocess.run([sys.executable, str(REPO / "jxl_tiff_encoder.py"),
                        str(tmp_path), "--mode", "7",
                        "--export-jxl-folder", "a/b", "--dry-run"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "--export-jxl-folder" in r.stderr, r.stdout + r.stderr


# ---------------------------------------------------------------------------
# Recompressor
# ---------------------------------------------------------------------------

def test_recompressor_resolve_output_mode7_uses_the_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "EXPORT_JXL_FOLDER", "16B_JXL_sRGB")
    monkeypatch.setattr(rec, "EXPORT_JXL_SUBFOLDER", "16B_JXL")
    src = tmp_path / "_EXPORT" / "16B_JXL" / "a.jxl"
    _jxl_stub(src)
    out = rec.resolve_output(src, 7, tmp_path)
    assert out == tmp_path / "_EXPORT" / "16B_JXL_sRGB" / "a.jxl"


def test_recompressor_own_output_path_follows_the_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "EXPORT_JXL_FOLDER", "16B_JXL_sRGB")
    assert rec._is_own_output_path(["16b_jxl_srgb"]) is True
    monkeypatch.setattr(rec, "EXPORT_JXL_FOLDER", "16B_JXL_small")
    assert rec._is_own_output_path(["16b_jxl_srgb"]) is False


def test_recompressor_recursive_scan_skips_the_configured_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "EXPORT_JXL_FOLDER", "16B_JXL_sRGB")
    monkeypatch.setattr(rec, "EXPORT_MARKER", "_EXPORT")
    root = tmp_path
    _jxl_stub(root / "_EXPORT" / "16B_JXL" / "a.jxl")
    _jxl_stub(root / "_EXPORT" / "16B_JXL_sRGB" / "a.jxl")
    found = {p.name for p in rec.find_jxls_recursive(root)}
    assert found == {"a.jxl"}
    assert all("16B_JXL_sRGB" not in str(p) for p in rec.find_jxls_recursive(root))
