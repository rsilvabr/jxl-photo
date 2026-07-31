#!/usr/bin/env python3
"""Regressions for the disk-full abort.

A staging drive is usually a small cheap SSD nobody watches, and it holds a
whole destination folder's output until that folder's last file lands — for a
flat run (one destination) that is the ENTIRE batch. When it filled, cjxl/djxl
kept exiting 0 while writing truncated files, the integrity check rejected each
one, and the run ground on to the end: one error per remaining file, thousands
of identical lines, and nothing naming the disk.

Now the first such failure latches an abort, queued files fall straight through
as "not attempted", and the run exits 2 (aborted) instead of 1 (some files
failed) — a distinction automation needs, because the aborted run is the one
worth retrying.
"""

import collections
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_tiff_encoder as enc
import jxl_tiff_decoder as dec
import jxl_jpeg_transcoder as tr

BACKENDS = pytest.mark.parametrize("mod", [enc, dec, tr], ids=["encoder", "decoder", "transcoder"])

Usage = collections.namedtuple("usage", "total used free")
MB = 1024 * 1024


@pytest.fixture(autouse=True)
def clean_latch():
    """The latch is module state; never let it leak between tests."""
    for m in (enc, dec, tr):
        m._reset_abort()
    yield
    for m in (enc, dec, tr):
        m._reset_abort()


# --------------------------------------------------------------------------
# The latch
# --------------------------------------------------------------------------

@BACKENDS
def test_latch_starts_clear(mod):
    assert mod._aborted() is None


@BACKENDS
def test_latch_keeps_the_first_reason(mod):
    """Racing workers all fail within milliseconds; the earliest one explains
    the run, so a later failure must not overwrite it."""
    mod._signal_abort("first")
    mod._signal_abort("second")
    assert mod._aborted() == "first"


@BACKENDS
def test_reset_clears_the_latch(mod):
    mod._signal_abort("stale")
    mod._reset_abort()
    assert mod._aborted() is None


# --------------------------------------------------------------------------
# _abort_if_disk_full
# --------------------------------------------------------------------------

@BACKENDS
def test_full_volume_latches(mod, tmp_path, monkeypatch):
    monkeypatch.setattr(mod.shutil, "disk_usage", lambda p: Usage(500 * MB, 499 * MB, 1 * MB))
    assert mod._abort_if_disk_full(tmp_path, 200 * MB) is True
    assert "no space left" in mod._aborted()


@BACKENDS
def test_roomy_volume_does_not_latch(mod, tmp_path, monkeypatch):
    monkeypatch.setattr(mod.shutil, "disk_usage", lambda p: Usage(500000 * MB, 0, 400000 * MB))
    assert mod._abort_if_disk_full(tmp_path, 200 * MB) is False
    assert mod._aborted() is None


@BACKENDS
def test_a_tiny_file_still_needs_the_floor(mod, tmp_path, monkeypatch):
    """A volume with 1 MB free is full even for a 1 KB write."""
    monkeypatch.setattr(mod.shutil, "disk_usage", lambda p: Usage(500 * MB, 499 * MB, 1 * MB))
    assert mod._abort_if_disk_full(tmp_path, 1024) is True


@BACKENDS
def test_free_space_above_the_floor_is_fine_for_a_tiny_file(mod, tmp_path, monkeypatch):
    monkeypatch.setattr(mod.shutil, "disk_usage", lambda p: Usage(500 * MB, 400 * MB, 100 * MB))
    assert mod._abort_if_disk_full(tmp_path, 1024) is False
    assert mod._aborted() is None


@BACKENDS
def test_unqueryable_volume_is_never_called_full(mod, tmp_path, monkeypatch):
    """"Cannot tell" must never reach the user as "disk full" — that would
    abort healthy runs on any filesystem shutil cannot stat."""
    def boom(p):
        raise OSError("no such device")
    monkeypatch.setattr(mod.shutil, "disk_usage", boom)
    assert mod._abort_if_disk_full(tmp_path, 200 * MB) is False
    assert mod._aborted() is None


@BACKENDS
def test_a_missing_size_falls_back_to_the_floor(mod, tmp_path, monkeypatch):
    """The call sites pass 0 when the source cannot be stat'd."""
    monkeypatch.setattr(mod.shutil, "disk_usage", lambda p: Usage(500 * MB, 499 * MB, 1 * MB))
    assert mod._abort_if_disk_full(tmp_path, 0) is True
    assert mod._abort_if_disk_full(tmp_path, None) is True


# --------------------------------------------------------------------------
# Workers fall through once the latch is set
# --------------------------------------------------------------------------

def test_encoder_worker_falls_through(tmp_path):
    enc._signal_abort("disk full")
    src = tmp_path / "a.tif"
    src.write_bytes(b"not really a tiff")
    out = tmp_path / "a.jxl"

    result = enc.convert_one(src, out, out)

    assert result[1] == "aborted"
    assert not out.exists(), "an aborted task must not write anything"


def test_decoder_worker_falls_through(tmp_path):
    dec._signal_abort("disk full")
    src = tmp_path / "a.jxl"
    src.write_bytes(b"not really a jxl")
    out = tmp_path / "a.tif"

    result = dec.convert_multipage_jxl_group(src, [(src, 0, False, False, 0, False, 16)], out, out)

    assert result[1] == "aborted"
    assert not out.exists()


@pytest.mark.parametrize("fn,args", [
    ("encode_one_transcode", (False, 7, False)),
    ("decode_one_transcode", (False, False, False)),
    ("encode_to_jxl", (7, 1.0, False, False)),
    ("decode_to_image", (95, "jpeg", 8, None, True, False, False)),
])
def test_transcoder_workers_fall_through(tmp_path, fn, args):
    tr._signal_abort("disk full")
    src = tmp_path / "a.jxl"
    src.write_bytes(b"not really a jxl")
    out = tmp_path / "a.out"

    result = getattr(tr, fn)(src, out, out, *args)

    assert result[1] == "aborted"
    assert not out.exists()


def test_auto_mode_does_not_count_aborted_files_as_errors(tmp_path):
    """The auto-mode accumulator ends in a catch-all `else` that calls anything
    unrecognised an error. Without an explicit "aborted" branch, every
    never-attempted file would be reported as a failure — thousands of invented
    errors, which is the exact noise the abort was added to remove."""
    import argparse

    src = tmp_path / "a.jpg"
    src.write_bytes(b"\xff\xd8\xff\xd9")  # smallest thing shaped like a JPEG

    args = argparse.Namespace(
        input=tmp_path, output=None, mode=0, workers=1, dry_run=False,
        format="jpeg", bit_depth=8, quality=95, distance=1.0, effort=7,
        icc_profile=None, no_verify=True, ram=True, overwrite=False, sync=False,
        output_name="converted_jxl", output_suffix=None,
        rename_from="", rename_to="",
    )

    tr._signal_abort("disk full")
    tally = tr._process_file_group([src], args, use_transcode=True)

    assert tally["aborted"] == 1
    assert tally["err"] == 0, "a never-attempted file was reported as a failure"
    assert tally["failures"] == []
    # An auto run calls _process_file_group up to four times (jbrd / lossy /
    # JPEG / PNG). Clearing the latch in there would forget an abort raised by
    # an earlier group and let the next one start converting into a full disk;
    # the reset belongs once per run, in cmd_auto.
    assert tr._aborted() is not None, "_process_file_group cleared the latch"


def test_a_healthy_run_does_not_fall_through(tmp_path):
    """The guard must be inert while the latch is clear — otherwise it would
    abort every run rather than none."""
    assert enc._aborted() is None
    src = tmp_path / "a.tif"
    src.write_bytes(b"not really a tiff")
    out = tmp_path / "a.jxl"

    result = enc.convert_one(src, out, out)

    # It fails (the input is junk), but it FAILED — it was actually attempted.
    assert result[1] == "error"
