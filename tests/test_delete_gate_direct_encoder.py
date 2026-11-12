"""The encoder's delete gate, tested directly (refactor plan 261113, A2).

The gate used to be the end of process_group: testing it meant running the
whole group with convert_one replaced. Now it is _delete_sources(results,
tasks, moved_finals, use_staging). Each test runs ONE real encode (real cjxl,
djxl and exiftool, in a folder named with Japanese, brackets, a space and %)
with the gate replaced by a recorder, then calls the real gate with the
recorded arguments after one controlled change, and checks what is left on
disk. The shapes of `results`/`tasks` come from the real run, never typed here.
"""
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_tiff_encoder as enc  # noqa: E402

pytestmark = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool")),
    reason="needs cjxl, djxl and exiftool on PATH")

HOSTILE = "250118_吾妻山公園 [FINAL] 100%off"


def _tiff(path: Path, seed: int, pages: int = 1) -> None:
    rng = np.random.default_rng(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tifffile.TiffWriter(str(path)) as tw:
        for _ in range(pages):
            tw.write(rng.integers(0, 65535, (48, 64, 3), dtype=np.uint16), photometric="rgb")


def _recorded_run(tmp_path, monkeypatch, names=("DSC_0001 (2).tif",), pages=1):
    """A real encode with --delete-source whose gate only records its arguments."""
    src = tmp_path / HOSTILE / "TIFF16"
    for i, name in enumerate(names):
        _tiff(src / name, seed=i, pages=pages)
    out = tmp_path / HOSTILE / "16B_JXL"
    seen = {}
    monkeypatch.setattr(enc, "_delete_sources", lambda *a: seen.setdefault("args", a))
    monkeypatch.setattr(sys, "argv", [
        "jxl_tiff_encoder.py", str(src), str(out), "--mode", "0", "--distance", "0",
        "--effort", "3", "--workers", "1", "--no-preflight", "--delete-source",
        "--delete-confirm-off"])
    try:
        enc.main()
    except SystemExit:
        pass
    monkeypatch.undo()          # the real gate back; the run's settings stay
    assert "args" in seen, "the gate was never reached: the test proves nothing"
    results, tasks, moved_finals, use_staging = seen["args"]
    for key in enc._delete_stats:
        enc._delete_stats[key] = 0
    return src, out, list(results), tasks, moved_finals, use_staging


def _gate(results, tasks, moved_finals, use_staging):
    enc._delete_sources(results, tasks, moved_finals, use_staging)
    return dict(enc._delete_stats)


def test_a_proven_source_is_deleted(tmp_path, monkeypatch):
    src, out, results, tasks, moved, staging = _recorded_run(tmp_path, monkeypatch)
    stats = _gate(results, tasks, moved, staging)
    assert not (src / "DSC_0001 (2).tif").exists()
    assert (out / "DSC_0001 (2).jxl").exists()
    assert stats["deleted"] == 1 and stats["kept"] == 0


def test_a_failed_page_keeps_the_source(tmp_path, monkeypatch, caplog):
    src, _out, results, tasks, moved, staging = _recorded_run(tmp_path, monkeypatch, pages=2)
    assert len(results) == 2, results
    results[1] = (results[1][0], "error", "planted failure") + tuple(results[1][3:])
    stats = _gate(results, tasks, moved, staging)
    assert (src / "DSC_0001 (2).tif").exists()
    assert stats == {**stats, "deleted": 0, "kept": 1}
    assert "not all pages succeeded" in caplog.text


def test_a_missing_output_keeps_the_source(tmp_path, monkeypatch):
    src, out, results, tasks, moved, staging = _recorded_run(tmp_path, monkeypatch)
    (out / "DSC_0001 (2).jxl").unlink()
    stats = _gate(results, tasks, moved, staging)
    assert (src / "DSC_0001 (2).tif").exists()
    assert stats["deleted"] == 0 and stats["kept"] == 1


def test_a_damaged_output_keeps_the_source(tmp_path, monkeypatch, caplog):
    src, out, results, tasks, moved, staging = _recorded_run(tmp_path, monkeypatch)
    jxl = out / "DSC_0001 (2).jxl"
    jxl.write_bytes(jxl.read_bytes()[: jxl.stat().st_size // 2])
    stats = _gate(results, tasks, moved, staging)
    assert (src / "DSC_0001 (2).tif").exists()
    assert stats["deleted"] == 0 and stats["kept"] == 1
    assert "integrity" in caplog.text


def test_an_output_of_another_source_keeps_both(tmp_path, monkeypatch):
    names = ("DSC_0001 (2).tif", "DSC_0002 [b].tif")
    src, out, results, tasks, moved, staging = _recorded_run(tmp_path, monkeypatch, names=names)
    a, b = out / "DSC_0001 (2).jxl", out / "DSC_0002 [b].jxl"
    data_a, data_b = a.read_bytes(), b.read_bytes()
    a.write_bytes(data_b)
    b.write_bytes(data_a)
    stats = _gate(results, tasks, moved, staging)
    assert all((src / n).exists() for n in names)
    assert stats["deleted"] == 0 and stats["kept"] == 2


def test_a_source_changed_after_the_run_read_it_is_kept(tmp_path, monkeypatch):
    src, _out, results, tasks, moved, staging = _recorded_run(tmp_path, monkeypatch)
    _tiff(src / "DSC_0001 (2).tif", seed=99)          # re-exported during the run
    stats = _gate(results, tasks, moved, staging)
    assert (src / "DSC_0001 (2).tif").exists()
    assert stats["deleted"] == 0 and stats["kept"] == 1


def test_the_changed_since_read_check_keeps_the_source(tmp_path, monkeypatch, caplog):
    """The last check before the unlink: a source rewritten while the run was
    verifying it (its bytes no longer the ones read) is kept. A direct test
    cannot time a rewrite between two checks, so the check itself answers."""
    src, _out, results, tasks, moved, staging = _recorded_run(tmp_path, monkeypatch)
    monkeypatch.setattr(enc, "_source_changed_since_read", lambda path: True)
    stats = _gate(results, tasks, moved, staging)
    assert (src / "DSC_0001 (2).tif").exists()
    assert stats["deleted"] == 0 and stats["kept"] == 1
    assert "changed since this run read it" in caplog.text
