#!/usr/bin/env python3
"""Bug #283 — a failed move out of staging left a truncated file behind.

A cross-volume `shutil.move` is copy-then-unlink, so an ENOSPC part way through
leaves a TRUNCATED file at the destination with a FRESH mtime. Smart-sync then
compares timestamps, sees something newer than the source, and skips the
reconversion forever: the corrupt output becomes permanent. The good copy was
still sitting in staging the whole time.

A destination volume that is simply full also produced one "MOVE FAILED" line
per remaining file instead of latching the disk-full abort the scripts already
have for exactly this.
"""

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_jpeg_transcoder as tr
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc

MODULES = [enc, dec, tr]
IDS = ["encoder", "decoder", "transcoder"]


@pytest.fixture(autouse=True)
def _clean_abort():
    for m in MODULES:
        m._reset_abort()
    yield
    for m in MODULES:
        m._reset_abort()


def _staged(tmp_path, name="out.bin", size=4096):
    src = tmp_path / "staging" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"x" * size)
    return src


@pytest.mark.parametrize("mod", MODULES, ids=IDS)
def test_a_successful_move_still_reports_true(mod, tmp_path):
    src = _staged(tmp_path)
    dst = tmp_path / "final" / "out.bin"
    assert mod._promote_from_staging(src, dst) is True
    assert dst.exists() and not src.exists()


@pytest.mark.parametrize("mod", MODULES, ids=IDS)
def test_a_partial_destination_is_removed(mod, tmp_path, monkeypatch):
    """The exact ENOSPC-mid-copy shape: something landed, the staging copy
    survived, and the move raised."""
    src = _staged(tmp_path)
    dst = tmp_path / "final" / "out.bin"
    dst.parent.mkdir(parents=True)

    def _fake_move(a, b):
        Path(b).write_bytes(b"x" * 100)          # truncated copy
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(mod.shutil, "move", _fake_move)
    assert mod._promote_from_staging(src, dst) is False
    assert not dst.exists(), "the truncated file was left at the destination"
    assert src.exists(), "the complete copy must stay in staging"


@pytest.mark.parametrize("mod", MODULES, ids=IDS)
def test_an_overwritten_output_is_not_left_corrupt(mod, tmp_path, monkeypatch):
    """Worse case: the destination already held a good file, and the failed
    copy overwrote part of it. Leaving it keeps a corrupt file with a fresh
    mtime; removing it lets a later run rebuild from the source."""
    src = _staged(tmp_path)
    dst = tmp_path / "final" / "out.bin"
    dst.parent.mkdir(parents=True)
    dst.write_bytes(b"older complete output")

    def _fake_move(a, b):
        Path(b).write_bytes(b"xx")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(mod.shutil, "move", _fake_move)
    assert mod._promote_from_staging(src, dst) is False
    assert not dst.exists()


@pytest.mark.parametrize("mod", MODULES, ids=IDS)
def test_nothing_is_removed_when_the_staging_copy_is_gone(mod, tmp_path, monkeypatch):
    """If staging is empty the move actually completed and something else
    raised — deleting the destination there would destroy the only copy."""
    src = _staged(tmp_path)
    dst = tmp_path / "final" / "out.bin"
    dst.parent.mkdir(parents=True)

    def _fake_move(a, b):
        shutil.copyfile(a, b)
        Path(a).unlink()
        raise OSError("something after the move")

    monkeypatch.setattr(mod.shutil, "move", _fake_move)
    assert mod._promote_from_staging(src, dst) is False
    assert dst.exists(), "the only remaining copy was deleted"


@pytest.mark.parametrize("mod", MODULES, ids=IDS)
def test_a_full_destination_latches_the_abort(mod, tmp_path, monkeypatch):
    """Otherwise a full volume produces one MOVE FAILED line per file and the
    run grinds through the whole library."""
    src = _staged(tmp_path)
    dst = tmp_path / "final" / "out.bin"

    def _fake_move(a, b):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(mod.shutil, "move", _fake_move)
    monkeypatch.setattr(mod.shutil, "disk_usage",
                        lambda p: shutil._ntuple_diskusage(100, 100, 0))
    assert mod._promote_from_staging(src, dst) is False
    assert mod._aborted(), "a full destination volume did not stop the run"


@pytest.mark.parametrize("mod", MODULES, ids=IDS)
def test_a_locked_file_on_a_healthy_volume_does_not_abort(mod, tmp_path, monkeypatch):
    """"Cannot tell" must never be reported as "disk full" — a locked or
    read-only destination is one file's problem, not the run's."""
    src = _staged(tmp_path)
    dst = tmp_path / "final" / "out.bin"

    def _fake_move(a, b):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(mod.shutil, "move", _fake_move)
    assert mod._promote_from_staging(src, dst) is False
    assert not mod._aborted(), "one locked file stopped the whole run"
