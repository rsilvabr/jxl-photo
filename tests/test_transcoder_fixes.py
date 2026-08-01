#!/usr/bin/env python3
"""
Regression tests for the v1.8.0 transcoder/wrapper audit fixes:

- --dry-run honored on transcode and auto paths (no conversion subprocess runs)
- mode 1 is flat (matches docs and the TIFF decoder)
- modes 4/5 renamed to match encoder/decoder (4=suffix rename, 5=sibling)
- auto mode pre-switches JPEG+16-bit outputs to .png (staging orphan fix)
- auto mode processes PNG-only folders (convert encode to JXL)
"""

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_jpeg_transcoder as tr


class _FakeRun:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class _FakeLogger:
    def __init__(self):
        self.infos, self.warnings, self.errors = [], [], []

    def info(self, m): self.infos.append(str(m))
    def warning(self, m): self.warnings.append(str(m))
    def error(self, m): self.errors.append(str(m))
    def debug(self, m): pass


def _args(tmp_path, **kw):
    base = dict(
        input=tmp_path, output=None, mode=1, workers=2, effort=7,
        overwrite=False, sync=False, staging=None, dry_run=False,
        delete_source=False, no_md5=False, no_verify=False, decode=False,
        force_transcode=False, force_convert=False, format=None, quality=95,
        distance=1.0, bit_depth=None, icc_profile=None, ram=True,
        output_name="converted", output_suffix="_converted",
        rename_from="", rename_to="",
    )
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture(autouse=True)
def _reset_globals():
    yield
    tr.DELETE_SOURCE = False
    tr.TEMP2_DIR = None
    tr.STORE_MD5 = True


# ---------------------------------------------------------------------------
# dry-run honored everywhere
# ---------------------------------------------------------------------------

def test_dry_run_transcode_runs_no_conversion(monkeypatch, tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8fake")
    tr.setup_logger()
    calls = []
    monkeypatch.setattr(tr.subprocess, "run", lambda *a, **k: calls.append(a[0]) or _FakeRun())
    tr.cmd_transcode(_args(tmp_path, dry_run=True))
    assert calls == []


def test_dry_run_auto_runs_no_conversion(monkeypatch, tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8fake")
    tr.setup_logger()
    calls = []
    monkeypatch.setattr(tr.subprocess, "run", lambda *a, **k: calls.append(a[0]) or _FakeRun())
    tr.cmd_auto(_args(tmp_path, dry_run=True))
    assert calls == []


# ---------------------------------------------------------------------------
# mode 1 is flat
# ---------------------------------------------------------------------------

def test_mode1_is_flat(monkeypatch, tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8fake")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.jpg").write_bytes(b"\xff\xd8fake")
    fake = _FakeLogger()
    monkeypatch.setattr(tr, "setup_logger", lambda: None)  # keep our logger
    monkeypatch.setattr(tr, "logger", fake)
    tr.cmd_transcode(_args(tmp_path, mode=1, dry_run=True))
    dry = [m for m in fake.infos if m.startswith(" DRY |")]
    assert len(dry) == 1
    assert "a.jpg" in dry[0]


# ---------------------------------------------------------------------------
# modes 4/5 aligned with encoder/decoder (4=suffix rename, 5=sibling)
# ---------------------------------------------------------------------------

def test_transcode_modes_4_and_5(tmp_path):
    src = tmp_path / "vacation_JPEG" / "photo.jpg"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"x")
    out4 = tr.resolve_output_transcode(src, 4, tmp_path, decode=False)
    out5 = tr.resolve_output_transcode(src, 5, tmp_path, decode=False)
    assert out4.parent.name == "vacation_JXL"
    assert out5.parent.name == tr.JXL_SIBLING_FOLDER


def test_convert_modes_4_and_5(tmp_path):
    src = tmp_path / "vacation_JPEG" / "photo.jpg"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"x")
    out4 = tr.resolve_output_convert(src, 4, "converted", "_conv", "jxl", "", "", tmp_path, decode=False)
    out5 = tr.resolve_output_convert(src, 5, "converted", "_conv", "jxl", "", "", tmp_path, decode=False)
    assert out4.parent.name == "vacation_JXL"
    assert out5.parent.name == tr.JXL_SIBLING_FOLDER


# ---------------------------------------------------------------------------
# auto mode: 16-bit JPEG output pre-switches to .png (staging orphan fix)
# ---------------------------------------------------------------------------

def test_auto_16bit_output_is_png(monkeypatch, tmp_path):
    jxl = tmp_path / "a.jxl"
    jxl.write_bytes(b"\x00" * 16)
    tr.setup_logger()
    captured = {}

    def fake_pgc(group_pairs, workers, **kw):
        captured["pairs"] = group_pairs
        return [], set()

    monkeypatch.setattr(tr, "process_group_convert", fake_pgc)
    tr._process_file_group([jxl], _args(tmp_path, format=None, bit_depth=16),
                           use_transcode=False)
    out = captured["pairs"][0][1]
    assert out.suffix == ".png"


# ---------------------------------------------------------------------------
# auto mode processes PNG-only folders
# ---------------------------------------------------------------------------

def test_auto_processes_png_only_folder(monkeypatch, tmp_path):
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    tr.setup_logger()
    seen = {}

    def fake_pgc(group_pairs, workers, direction=None, **kw):
        seen["direction"] = direction
        seen["files"] = [str(s) for s, _ in group_pairs]
        return [], set()

    monkeypatch.setattr(tr, "process_group_convert", fake_pgc)
    tr.cmd_auto(_args(tmp_path))
    assert seen.get("direction") == "to_jxl"
    assert seen.get("files") == [str(tmp_path / "a.png")]
