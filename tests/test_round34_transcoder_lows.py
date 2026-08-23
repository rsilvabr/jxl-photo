#!/usr/bin/env python3
"""Regressions for the round-34 low-severity transcoder batch.

  1. #267 was incompletely fixed: the dry-run "--delete-source is ARMED"
     preview only existed in _process_file_group (cmd_auto). cmd_transcode and
     cmd_convert ran the same armed simulation without ever mentioning it.
  2. The ICC-conversion paths decoded the intermediate PNG without
     --bits_per_sample, so a 16-bit request relied on `magick -depth 16`
     upscaling whatever djxl defaulted to instead of decoding at 16-bit.
  3. cmd_auto charged the strict HHMM lossy token for a PNG->JXL group even at
     --distance 0 (lossless modular); cmd_convert gates on distance > 0.
  4. reorder_jxl_boxes died with a raw OverflowError when a size-0 box moved
     off the tail of a ~4 GiB file computed a real size past the 32-bit field.
  5. Two silent gate bypasses: the transcode delete gate's missing-final
     branch `continue`d with no KEEP log/count, and the convert staging mover
     silently dropped an "ok" result whose staged file was gone.

The tests import only jxl_jpeg_transcoder, so they run unchanged against an
extracted HEAD copy of the script (pre-fix proof).
"""

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_jpeg_transcoder as tr


class _FakeRun:
    def __init__(self, stdout="", stderr=b"", returncode=0):
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
    tr.DELETE_CONFIRM = True
    tr.TEMP2_DIR = None
    tr.STORE_MD5 = True


# ---------------------------------------------------------------------------
# 1. #267 — every dry run of an armed delete run must say so
# ---------------------------------------------------------------------------

def test_dry_run_transcode_announces_delete_armed(monkeypatch, tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8fake")
    fake = _FakeLogger()
    monkeypatch.setattr(tr, "setup_logger", lambda: None)
    monkeypatch.setattr(tr, "logger", fake)
    monkeypatch.setattr(tr, "DELETE_SOURCE", False)  # restore after cmd sets it
    tr.cmd_transcode(_args(tmp_path, dry_run=True, delete_source=True))
    assert any("--delete-source is ARMED" in m for m in fake.warnings)
    assert any("would be DELETED" in m for m in fake.warnings)


def test_dry_run_convert_announces_delete_armed(monkeypatch, tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8fake")
    fake = _FakeLogger()
    monkeypatch.setattr(tr, "setup_logger", lambda: None)
    monkeypatch.setattr(tr, "logger", fake)
    monkeypatch.setattr(tr, "DELETE_SOURCE", False)
    tr.cmd_convert(_args(tmp_path, dry_run=True, delete_source=True), from_jxl=False)
    assert any("--delete-source is ARMED" in m for m in fake.warnings)
    assert any("would be DELETED" in m for m in fake.warnings)


def test_dry_run_auto_announces_delete_armed(monkeypatch, tmp_path):
    """The third entry point: already present, pinned so it cannot regress."""
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8fake")
    fake = _FakeLogger()
    monkeypatch.setattr(tr, "setup_logger", lambda: None)
    monkeypatch.setattr(tr, "logger", fake)
    monkeypatch.setattr(tr, "DELETE_SOURCE", False)
    tr.cmd_auto(_args(tmp_path, dry_run=True, delete_source=True))
    assert any("--delete-source is ARMED" in m for m in fake.warnings)


@pytest.mark.parametrize("cmd", ["transcode", "convert"])
def test_dry_run_stays_quiet_without_delete(monkeypatch, tmp_path, cmd):
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8fake")
    fake = _FakeLogger()
    monkeypatch.setattr(tr, "setup_logger", lambda: None)
    monkeypatch.setattr(tr, "logger", fake)
    if cmd == "transcode":
        tr.cmd_transcode(_args(tmp_path, dry_run=True))
    else:
        tr.cmd_convert(_args(tmp_path, dry_run=True), from_jxl=False)
    assert not any("ARMED" in m for m in fake.warnings + fake.infos)


# ---------------------------------------------------------------------------
# 2. ICC intermediate decode must request the output bit depth
# ---------------------------------------------------------------------------

def _png26(color_type=2):
    """26-byte signature+IHDR stub: enough for _png_is_grayscale."""
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0d" + b"IHDR"
            + b"\x00" * 8 + b"\x08" + bytes([color_type]))


@pytest.mark.parametrize("fmt,bit_depth", [("png", 16), ("jpeg", 8)])
def test_icc_intermediate_decode_requests_bit_depth(monkeypatch, tmp_path, fmt, bit_depth):
    jxl = tmp_path / "a.jxl"
    jxl.write_bytes(b"\x00" * 16)
    final = tmp_path / ("a.png" if fmt == "png" else "a.jpg")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        # djxl: output is argv[2]; magick: output is the last argument.
        target = Path(cmd[2] if cmd[0] == "djxl" else cmd[-1])
        target.write_bytes(_png26())
        return _FakeRun()

    monkeypatch.setattr(tr, "MAGICK_AVAILABLE", True)
    monkeypatch.setattr(tr.subprocess, "run", fake_run)
    monkeypatch.setattr(tr, "_copy_metadata", lambda *a, **k: None)
    monkeypatch.setattr(tr, "_run_exiftool_argfile", lambda *a, **k: None)
    monkeypatch.setattr(tr, "_verify_file_integrity", lambda p: True)
    tr.setup_logger()

    status = tr.decode_to_image(jxl, final, final, 90, fmt, bit_depth,
                                str(tmp_path / "fake.icc"), True, False, False)
    assert status[1] == "ok", status
    djxl_call = calls[0]
    assert djxl_call[0] == "djxl"
    assert f"--bits_per_sample={bit_depth}" in djxl_call, djxl_call


# ---------------------------------------------------------------------------
# 3. cmd_auto: PNG -> JXL at distance 0 is lossless — no HHMM token
# ---------------------------------------------------------------------------

def _auto_confirm_run(monkeypatch, tmp_path, distance):
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    tr.setup_logger()
    called = {"lossy": 0, "jpeg": 0}

    def _lossy():
        called["lossy"] += 1
        return True

    def _jpeg():
        called["jpeg"] += 1
        return True

    monkeypatch.setattr(tr, "confirm_deletion_lossy", _lossy)
    monkeypatch.setattr(tr, "confirm_deletion_jpeg", _jpeg)
    monkeypatch.setattr(tr, "DELETE_SOURCE", False)  # restore after cmd sets it
    monkeypatch.setattr(tr, "DELETE_CONFIRM", True)
    monkeypatch.setattr(tr, "process_group_convert",
                        lambda *a, **k: ([], set()))
    tr.cmd_auto(_args(tmp_path, distance=distance, delete_source=True))
    return called


def test_auto_distance0_png_group_charges_no_lossy_token(monkeypatch, tmp_path):
    called = _auto_confirm_run(monkeypatch, tmp_path, 0.0)
    assert called["lossy"] == 0, "lossless modular PNG->JXL is not a lossy delete"


def test_auto_lossy_png_group_still_charges_lossy_token(monkeypatch, tmp_path):
    """The conservative direction is kept: distance > 0 IS lossy."""
    called = _auto_confirm_run(monkeypatch, tmp_path, 1.0)
    assert called["lossy"] == 1


# ---------------------------------------------------------------------------
# 4. reorder_jxl_boxes: oversize re-header is a RuntimeError, not OverflowError
# ---------------------------------------------------------------------------

def test_reorder_oversize_size0_box_raises_runtime_error():
    # File layout: [jxlc size 8][Exif size 0 -> EOF]. Regrouping moves the
    # size-0 Exif box BEFORE the codestream, so its header must be rewritten
    # with the real size — which does not fit the 32-bit field for a ~4 GiB
    # payload. (The payload length is faked: allocating 4 GiB in a test is
    # not an option, and the code under test only ever calls len() on it.)
    head = (8).to_bytes(4, "big") + b"jxlc" + (0).to_bytes(4, "big") + b"Exif"

    class _HugePayload:
        def __len__(self):
            return 0xFFFFFFF8  # 8 + this == 2**32, past the 4-byte field

    class _FakeData:
        def __len__(self):
            return len(head)

        def __getitem__(self, item):
            if isinstance(item, slice) and (item.start or 0) >= len(head):
                return _HugePayload()
            return head[item]

    class _FakePath:
        def read_bytes(self):
            return _FakeData()

        def write_bytes(self, data):
            raise AssertionError("the file must not be rewritten")

    with pytest.raises(RuntimeError, match="32-bit"):
        tr.reorder_jxl_boxes(_FakePath())


# ---------------------------------------------------------------------------
# 5a. transcode delete gate: a missing final output is logged and counted
# ---------------------------------------------------------------------------

def test_transcode_delete_gate_logs_missing_final(monkeypatch, tmp_path, caplog):
    src = tmp_path / "a.jpg"
    src.write_bytes(b"\xff\xd8fake")
    final = tmp_path / "a.jxl"  # the worker "succeeded" but never wrote it
    tr.setup_logger()
    tr.logger.propagate = True
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "TEMP2_DIR", None)
    monkeypatch.setattr(tr, "_delete_stats",
                        {"deleted": 0, "deleted_archived": 0, "kept": 0})
    monkeypatch.setattr(
        tr, "encode_one_transcode",
        lambda s, w, f, *a, **k: (str(s), "ok", str(f), "d41d8cd98f00b204e9800998ecf8427e"))
    with caplog.at_level("INFO", logger="jxl_jpeg_transcoder"):
        tr.process_group_transcode([(src, final)], 1, False, False, 1, False, False)
    assert tr._delete_stats["kept"] == 1
    assert tr._delete_stats["deleted"] == 0
    assert "KEEP (final output missing)" in caplog.text


# ---------------------------------------------------------------------------
# 5b. convert staging mover: an "ok" result with no staged file is not dropped
# ---------------------------------------------------------------------------

def test_convert_mover_logs_missing_staging_file(monkeypatch, tmp_path, caplog):
    src = tmp_path / "a.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    final = tmp_path / "out" / "a.jxl"
    tr.setup_logger()
    tr.logger.propagate = True
    monkeypatch.setattr(tr, "TEMP2_DIR", str(tmp_path / "staging"))
    monkeypatch.setattr(tr, "_delete_stats",
                        {"deleted": 0, "deleted_archived": 0, "kept": 0})
    # Reports success without ever writing the staged output.
    monkeypatch.setattr(
        tr, "encode_to_jxl",
        lambda s, w, f, *a, **k: (str(s), "ok", str(f), None))
    with caplog.at_level("INFO", logger="jxl_jpeg_transcoder"):
        tr.process_group_convert([(src, final)], 1, "to_jxl", quality=95,
                                 distance=1.0, fmt=None, bit_depth=None,
                                 output_icc=None, use_ram=True, effort=7,
                                 reconvert_val=False, use_internal_srgb=False,
                                 smart=False)
    assert tr._delete_stats["kept"] == 1
    assert "KEEP (staging file missing)" in caplog.text
