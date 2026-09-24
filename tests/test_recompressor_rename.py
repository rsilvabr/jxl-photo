#!/usr/bin/env python3
"""`--rename-from` / `--rename-to` (recompressor).

The output name has to change at PLANNING time: the sync, the non-derivative
refusal, the duplicate-output abort and the wrapper's collision mirror all look
at the final path. The semantics are pinned to the transcoder's.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_jpeg_transcoder as tr
import jxl_recompressor as rec

REPO = Path(__file__).resolve().parent.parent


def _jxl_stub(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)


def test_apply_rename_replaces_the_profile_token():
    assert rec._apply_rename("_DSC1_ProPhoto-g22_v1.jxl", "ProPhoto-g22", "sRGB") == \
        "_DSC1_sRGB_v1.jxl"


def test_apply_rename_is_case_sensitive():
    assert rec._apply_rename("a_prophoto.jxl", "ProPhoto", "sRGB") == "a_prophoto.jxl"


def test_apply_rename_replaces_only_the_first_occurrence():
    assert rec._apply_rename("x_A_A.jxl", "A", "B") == "x_B_A.jxl"


def test_apply_rename_never_touches_the_extension():
    assert rec._apply_rename("a.jxl", "jxl", "png") == "a.jxl"


def test_apply_rename_matches_the_transcoder(tmp_path):
    cases = [("_DSC1_ProPhoto-g22_v1.jxl", "ProPhoto-g22", "sRGB"),
             ("a_prophoto.jxl", "ProPhoto", "sRGB"),
             ("x_A_A.jxl", "A", "B"),
             ("a.jxl", "jxl", "png"),
             ("plain.jxl", "zzz", "yyy")]
    for name, frm, to in cases:
        assert rec._apply_rename(name, frm, to) == tr.resolve_output_convert(
            Path(name), 2, "x", "", "jxl", frm, to, output_root=tmp_path).name


def test_cli_dry_run_shows_the_renamed_output(tmp_path):
    _jxl_stub(tmp_path / "a_ProPhoto.jxl")
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(tmp_path), "--mode", "1", "--dry-run",
                        "--rename-from", "ProPhoto", "--rename-to", "sRGB"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "a_sRGB.jxl" in r.stdout, r.stdout


def test_cli_rename_in_place_mode8_is_refused(tmp_path):
    _jxl_stub(tmp_path / "a.jxl")
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(tmp_path), "--mode", "8", "--dry-run",
                        "--rename-from", "X"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr


def test_cli_rename_in_place_mode0_single_file_is_refused(tmp_path):
    _jxl_stub(tmp_path / "a.jxl")
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(tmp_path / "a.jxl"), "--mode", "0", "--dry-run",
                        "--rename-from", "X"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr


def test_cli_rename_to_alone_is_refused(tmp_path):
    _jxl_stub(tmp_path / "a.jxl")
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(tmp_path), "--mode", "1", "--dry-run",
                        "--rename-to", "sRGB"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr


def test_cli_rename_with_path_characters_is_refused(tmp_path):
    _jxl_stub(tmp_path / "a.jxl")
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(tmp_path), "--mode", "1", "--dry-run",
                        "--rename-from", "a/b"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr


def test_cli_rename_collision_aborts(tmp_path):
    _jxl_stub(tmp_path / "a_ProPhoto.jxl")
    _jxl_stub(tmp_path / "a_sRGB.jxl")
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(tmp_path), "--mode", "1", "--dry-run",
                        "--rename-from", "ProPhoto", "--rename-to", "sRGB"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr
