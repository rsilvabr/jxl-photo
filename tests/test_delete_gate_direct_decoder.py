"""The decoder's delete gate, tested directly (refactor plan 261113, A2).

_delete_decoded_sources(results, tasks, moved_finals, use_staging) is the
gate that used to end process_group. Each test makes a lossless master with
the real encoder, decodes it with the real decoder (--delete-source) with the
gate replaced by a recorder, then calls the real gate with the recorded
arguments after one controlled change -- all in a folder named with Japanese,
brackets, a space and % -- and checks what is left on disk.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import jxl_tiff_decoder as dec  # noqa: E402

pytestmark = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool")),
    reason="needs cjxl, djxl and exiftool on PATH")

HOSTILE = "250118_吾妻山公園 [FINAL] 100%off"
NAME = "DSC_0001 (2)"


def _master(tmp_path) -> Path:
    tiffs = tmp_path / HOSTILE / "TIFF16"
    tiffs.mkdir(parents=True)
    rng = np.random.default_rng(7)
    tifffile.imwrite(str(tiffs / f"{NAME}.tif"),
                     rng.integers(0, 65535, (48, 64, 3), dtype=np.uint16), photometric="rgb")
    masters = tmp_path / HOSTILE / "16B_JXL"
    r = subprocess.run([sys.executable, str(REPO / "jxl_tiff_encoder.py"), str(tiffs), str(masters),
                        "--mode", "0", "--distance", "0", "--effort", "3", "--no-preflight"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]
    return masters


def _recorded_run(tmp_path, monkeypatch):
    masters = _master(tmp_path)
    out = tmp_path / HOSTILE / "TIFF_restored"
    seen = {}
    monkeypatch.setattr(dec, "_delete_decoded_sources", lambda *a: seen.setdefault("args", a))
    monkeypatch.setattr(sys, "argv", [
        "jxl_tiff_decoder.py", str(masters), str(out), "--mode", "0", "--workers", "1",
        "--delete-source", "--delete-confirm-off"])
    try:
        dec.main()
    except SystemExit:
        pass
    monkeypatch.undo()
    assert "args" in seen, "the gate was never reached: the test proves nothing"
    for key in dec._delete_stats:
        dec._delete_stats[key] = 0
    return masters, out, seen["args"]


def _gate(args):
    dec._delete_decoded_sources(*args)
    return dict(dec._delete_stats)


def test_a_proven_source_is_deleted(tmp_path, monkeypatch):
    masters, out, args = _recorded_run(tmp_path, monkeypatch)
    stats = _gate(args)
    assert not (masters / f"{NAME}.jxl").exists()
    assert (out / f"{NAME}.tif").exists()
    assert stats["deleted"] == 1 and stats["kept"] == 0


def test_a_missing_output_keeps_the_source(tmp_path, monkeypatch, caplog):
    masters, out, args = _recorded_run(tmp_path, monkeypatch)
    (out / f"{NAME}.tif").unlink()
    stats = _gate(args)
    assert (masters / f"{NAME}.jxl").exists()
    assert stats["deleted"] == 0 and stats["kept"] >= 1
    assert "KEEP (final output missing)" in caplog.text


def test_a_damaged_output_keeps_the_source(tmp_path, monkeypatch, caplog):
    masters, out, args = _recorded_run(tmp_path, monkeypatch)
    tif = out / f"{NAME}.tif"
    tif.write_bytes(tif.read_bytes()[:200])
    stats = _gate(args)
    assert (masters / f"{NAME}.jxl").exists()
    assert stats["deleted"] == 0 and stats["kept"] >= 1
    assert "KEEP (TIFF failed integrity check)" in caplog.text


def test_an_output_that_cannot_reach_the_disk_keeps_the_source(tmp_path, monkeypatch, caplog):
    masters, _out, args = _recorded_run(tmp_path, monkeypatch)
    monkeypatch.setattr(dec, "_flush_to_disk", lambda path: False)
    stats = _gate(args)
    assert (masters / f"{NAME}.jxl").exists()
    assert stats["deleted"] == 0 and stats["kept"] >= 1
    assert "could not be flushed to disk" in caplog.text
