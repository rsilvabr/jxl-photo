#!/usr/bin/env python3
"""Regressions for the staging leftovers report and sweep.

A file whose conversion failed is deliberately KEPT in staging for manual
recovery, and nothing ever swept it. Over weeks of scheduled runs that is a
slow leak on precisely the small scratch SSD the disk-full abort exists to
protect: the leftovers eventually cause the condition they were evidence of.

These tests exist for the DELETE path, so they are mostly about what must
survive it. A staging directory is frequently a shared scratch folder, and the
sweep runs unattended.
"""

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_tiff_encoder as enc
import jxl_tiff_decoder as dec
import jxl_jpeg_transcoder as tr

BACKENDS = pytest.mark.parametrize(
    "mod", [enc, dec, tr], ids=["encoder", "decoder", "transcoder"])

HEX32 = "0123456789abcdef" * 2   # exactly 32 hex chars, like uuid4().hex


def _stale(p: Path, hours=2):
    old = time.time() - hours * 3600
    os.utime(p, (old, old))


def _leftover(d: Path, name="foto_p0.jxl", prefix=HEX32, stale=True):
    f = d / f"{prefix}_{name}"
    f.write_bytes(b"x" * 5000)
    if stale:
        _stale(f)
    return f


# --------------------------------------------------------------------------
# What must NEVER be touched
# --------------------------------------------------------------------------

@BACKENDS
def test_user_files_are_never_swept(mod, tmp_path):
    """A staging directory is often a shared scratch folder. Only names this
    tool wrote -- a uuid4 hex prefix -- may be considered."""
    keep = tmp_path / "minha_foto.jxl"
    keep.write_bytes(b"x" * 5000)
    _stale(keep)
    notes = tmp_path / "notes.txt"
    notes.write_bytes(b"x")
    _stale(notes)
    _leftover(tmp_path)

    removed, _ = mod._clean_staging(str(tmp_path))

    assert removed == 1
    assert keep.exists() and notes.exists()


@BACKENDS
def test_the_sweep_does_not_recurse(mod, tmp_path):
    """Subfolders are somebody else's business."""
    sub = tmp_path / "sub"
    sub.mkdir()
    nested = _leftover(sub)
    _leftover(tmp_path)

    removed, _ = mod._clean_staging(str(tmp_path))

    assert removed == 1
    assert nested.exists(), "swept a file inside a subdirectory"


@BACKENDS
def test_recent_files_survive(mod, tmp_path):
    """A file still being written belongs to a run in flight -- possibly a
    concurrent one sharing this directory."""
    fresh = _leftover(tmp_path, name="fresh_p0.jxl", stale=False)
    _leftover(tmp_path, prefix="f" * 32)

    removed, _ = mod._clean_staging(str(tmp_path))

    assert removed == 1
    assert fresh.exists(), "swept a file a live run may still own"


@BACKENDS
@pytest.mark.parametrize("bad", [
    "0" * 31,            # one short
    "0" * 33,            # one long
    "z" * 32,            # not hex
    "ABCDEF" * 5 + "AB",  # uppercase; uuid4().hex is lowercase
])
def test_a_near_miss_prefix_is_not_ours(mod, tmp_path, bad):
    """Every one of these is STALE, so age cannot be what saves them -- only
    the prefix can. (An earlier version of this test forgot to age the files
    and passed for that reason instead.)"""
    f = tmp_path / f"{bad}_x.jxl"
    f.write_bytes(b"x" * 5000)
    _stale(f)

    removed, _ = mod._clean_staging(str(tmp_path))

    assert removed == 0
    assert f.exists()


# --------------------------------------------------------------------------
# What it does do
# --------------------------------------------------------------------------

@BACKENDS
def test_stale_leftovers_are_swept(mod, tmp_path):
    for i in range(3):
        _leftover(tmp_path, prefix=f"{i}" + HEX32[1:])

    removed, freed = mod._clean_staging(str(tmp_path))

    assert removed == 3
    assert freed == 15000
    assert list(tmp_path.iterdir()) == []


@BACKENDS
def test_the_report_names_the_leak(mod, tmp_path, monkeypatch):
    lines = []
    monkeypatch.setattr(mod.logger, "warning", lambda m, *a: lines.append(str(m)))
    _leftover(tmp_path)

    mod._report_staging_leftovers(str(tmp_path))

    text = "\n".join(lines)
    assert "1 leftover file(s)" in text
    assert "--clean-staging" in text, "the report must say how to act on it"


@BACKENDS
def test_a_clean_staging_dir_reports_nothing(mod, tmp_path, monkeypatch):
    lines = []
    monkeypatch.setattr(mod.logger, "warning", lambda m, *a: lines.append(str(m)))

    mod._report_staging_leftovers(str(tmp_path))

    assert lines == []


@BACKENDS
def test_no_staging_configured_is_a_no_op(mod):
    """Most runs have no staging at all; neither call may blow up on None."""
    mod._report_staging_leftovers(None)
    assert mod._clean_staging(None) == (0, 0)


@BACKENDS
def test_a_missing_directory_is_survivable(mod, tmp_path):
    gone = tmp_path / "not_here"
    assert mod._clean_staging(str(gone)) == (0, 0)
    mod._report_staging_leftovers(str(gone))


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------

@BACKENDS
def test_the_sweep_runs_before_the_batch_not_after(mod):
    """Sweeping at the END would delete this run's own failures -- the very
    evidence the KEEP path exists to preserve. It must clear the PREVIOUS
    runs' orphans instead."""
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "_clean_staging" in src
    doc = mod._clean_staging.__doc__ or ""
    assert "BEFORE" in doc


@BACKENDS
def test_clean_staging_is_opt_in(mod):
    """A delete must never be the default."""
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert '"--clean-staging", action="store_true"' in src
