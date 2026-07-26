#!/usr/bin/env python3
"""
Tests for the v1.8.1 audit fixes:

- duplicate output detection aborts (modes 6/7 subfolder collision)
- cmd_auto honors script-level DELETE_SOURCE and DELETE_CONFIRM
- multipage marker batch uses an exiftool argfile (-@) with UTF-8 charset
- --container=1 only applied for lossy (d>0) in encode_to_jxl
- cleanup_xmp_icc strips a leading pipe when the ICC marker was mid-string
"""

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_jpeg_transcoder as tr
import jxl_tiff_decoder as dec


class _FakeRun:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _args(tmp_path, **kw):
    base = dict(
        input=tmp_path, output=None, mode=8, workers=2, effort=7,
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
    tr.DELETE_CONFIRM = True


# ---------------------------------------------------------------------------
# duplicate output abort
# ---------------------------------------------------------------------------

def test_duplicate_outputs_abort():
    tr.setup_logger()
    pairs = [(Path("a.jpg"), Path("/out/x.jxl")), (Path("b.jpg"), Path("/out/x.jxl"))]
    with pytest.raises(SystemExit):
        tr._abort_on_duplicate_outputs(pairs)


def test_unique_outputs_no_abort():
    tr.setup_logger()
    pairs = [(Path("a.jpg"), Path("/out/a.jxl")), (Path("b.jpg"), Path("/out/b.jxl"))]
    tr._abort_on_duplicate_outputs(pairs)  # must not raise


# ---------------------------------------------------------------------------
# cmd_auto DELETE_SOURCE / DELETE_CONFIRM
# ---------------------------------------------------------------------------

def test_cmd_auto_confirms_when_script_level_delete_source(monkeypatch, tmp_path):
    """Script-level DELETE_SOURCE=True must trigger the confirmation in auto mode."""
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8fake")
    tr.setup_logger()
    tr.DELETE_SOURCE = True
    calls = []
    monkeypatch.setattr(tr.subprocess, "run", lambda *a, **k: _FakeRun())
    monkeypatch.setattr(tr, "confirm_deletion_jpeg", lambda: calls.append("confirm") or False)
    monkeypatch.setattr(tr, "confirm_deletion_lossy", lambda: calls.append("confirm_lossy") or False)
    tr.cmd_auto(_args(tmp_path, mode=8))
    assert calls == ["confirm"]  # asked, user declined -> early return, nothing processed


def test_cmd_auto_skips_confirm_when_delete_confirm_off(monkeypatch, tmp_path):
    """DELETE_CONFIRM=False must not prompt even with --delete-source."""
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8fake")
    tr.setup_logger()
    tr.DELETE_CONFIRM = False
    calls = []
    monkeypatch.setattr(tr.subprocess, "run", lambda *a, **k: _FakeRun())
    monkeypatch.setattr(tr, "confirm_deletion_jpeg", lambda: calls.append("confirm") or False)
    monkeypatch.setattr(tr, "confirm_deletion_lossy", lambda: calls.append("confirm_lossy") or False)
    monkeypatch.setattr(tr, "process_group_transcode", lambda *a, **k: [])
    tr.cmd_auto(_args(tmp_path, mode=8, delete_source=True))
    assert calls == []
    assert tr.DELETE_SOURCE is True


# ---------------------------------------------------------------------------
# multipage marker batch argfile
# ---------------------------------------------------------------------------

def test_marker_batch_uses_argfile(monkeypatch, tmp_path):
    jxls = [tmp_path / f"photo_{i}.jxl" for i in range(3)]
    for j in jxls:
        j.write_bytes(b"\x00")
    dec.setup_logger()
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[1] == "-@":
            return _FakeRun(stdout="[]")
        return _FakeRun()

    monkeypatch.setattr(dec.subprocess, "run", fake_run)
    dec._read_multipage_markers_batch(jxls)
    assert calls and calls[0][1] == "-@"
    # argfile was cleaned up
    assert not Path(calls[0][2]).exists()


# ---------------------------------------------------------------------------
# --container=1 only for lossy
# ---------------------------------------------------------------------------

def _run_encode(monkeypatch, tmp_path, distance):
    tr.setup_logger()
    src = tmp_path / "a.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    write = tmp_path / "out" / "a.jxl"
    calls = []
    monkeypatch.setattr(tr.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _FakeRun())
    monkeypatch.setattr(tr, "reorder_jxl_boxes", lambda p: None)
    monkeypatch.setattr(tr, "_copy_metadata", lambda s, d: None)
    monkeypatch.setattr(tr, "FORCE_CONTAINER_FOR_LOSSY", True)
    monkeypatch.setattr(tr, "CJXL_BUFFERING", None)
    tr.encode_to_jxl(src, write, write, effort=7, distance=distance,
                     reconvert_val=True, smart=False)
    return calls[0]


def test_container_only_for_lossy(monkeypatch, tmp_path):
    cmd = _run_encode(monkeypatch, tmp_path, 1.0)
    assert "--container=1" in cmd


def test_container_for_lossless_png_input(monkeypatch, tmp_path):
    """Non-JPEG inputs (PNG) ALWAYS get --container=1, even at d=0: at d=0
    cjxl writes a bare codestream for them, exiftool cannot inject metadata
    into it, and our integrity gate (container required) would reject the
    toolkit's own output."""
    cmd = _run_encode(monkeypatch, tmp_path, 0.0)
    assert "--container=1" in cmd


def test_container_not_for_lossless_jpeg_input(monkeypatch, tmp_path):
    """JPEG inputs keep the old rule: no --container=1 at d=0 (cjxl
    --lossless_jpeg=1 already yields a container via jbrd)."""
    tr.setup_logger()
    src = tmp_path / "a.jpg"
    src.write_bytes(b"\xff\xd8fake")
    write = tmp_path / "out" / "a.jxl"
    calls = []
    monkeypatch.setattr(tr.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _FakeRun())
    monkeypatch.setattr(tr, "reorder_jxl_boxes", lambda p: None)
    monkeypatch.setattr(tr, "_copy_metadata", lambda s, d: None)
    monkeypatch.setattr(tr, "FORCE_CONTAINER_FOR_LOSSY", True)
    monkeypatch.setattr(tr, "CJXL_BUFFERING", None)
    tr.encode_to_jxl(src, write, write, effort=7, distance=0.0,
                     reconvert_val=True, smart=False)
    assert "--container=1" not in calls[0]


# ---------------------------------------------------------------------------
# cleanup_xmp_icc leading pipe
# ---------------------------------------------------------------------------

def test_cleanup_xmp_icc_strips_leading_pipe(monkeypatch, tmp_path):
    tif = tmp_path / "a.tif"
    tif.write_bytes(b"\x00")
    dec.setup_logger()
    dec.CLEANUP_XMP_ICC_MARKER = True
    writes = []

    # cleanup_xmp_icc now goes through _run_exiftool_argfile (argfile-based);
    # mock that boundary instead of subprocess.run.
    def fake_argfile(args_lines, timeout=60):
        if any(str(a) == "-XMP-xmp:CreatorTool" for a in args_lines) and not any("CreatorTool=" in str(a) for a in args_lines):
            return _FakeRun(stdout="ICC:QUJDRA== | Capture One 23\n")
        if any("CreatorTool=" in str(a) for a in args_lines):
            writes.append(args_lines)
        return _FakeRun()

    monkeypatch.setattr(dec, "_run_exiftool_argfile", fake_argfile)
    dec.cleanup_xmp_icc(tif)
    assert writes, "cleanup did not rewrite CreatorTool"
    written = [c for c in writes[0] if "CreatorTool=" in str(c)][0]
    assert written == "-XMP-xmp:CreatorTool=Capture One 23"
