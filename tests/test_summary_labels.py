"""The run summary has one spelling for both sides (refactor F4.5).

Each child prints `##JXLSUM## {json}` and the wrapper's end-of-manifest block
pops the deletion counts out of its `extras` BY NAME. The prefix and those
keys live in jxl_photo_common.py, read by the four children and the wrapper:
a label changed on one side only used to drop the deletions from the
wrapper's "SOURCES DELETED" line without a word (the recompressor's
"deleted sources" once did).

Tested end to end: a REAL encoder run that deletes its sources, its summary
line parsed by the wrapper, and the wrapper's combined log must say
"SOURCES DELETED: 2" — in a folder with Japanese, brackets, a space and %.
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
import jxl_photo as wp  # noqa: E402
import jxl_photo_common as common  # noqa: E402

HOSTILE = "250118_吾妻山公園 [FINAL] 100%off %d"
needs_tools = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool")),
    reason="needs cjxl, djxl and exiftool on PATH")


@pytest.fixture
def menu(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    monkeypatch.setattr(wp, "WRAPPER_LOG_DIR", tmp_path / "wrapper_logs")
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


def _tiff(path: Path, value: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.full((16, 16, 3), value, np.uint16),
                     photometric="rgb")


def _combined_log(menu, summary, tmp_path) -> str:
    rep = {"index": 1, "mode": 2, "source": "src", "summary": summary,
           "state": "ok", "deletes": True}
    menu._render_manifest_summary([rep], 1, 0, 0)
    logs = list((tmp_path / "wrapper_logs").glob("*.log"))
    assert len(logs) == 1, logs
    return logs[0].read_text(encoding="utf-8")


def test_the_wrapper_reads_the_children_s_prefix():
    assert wp.CHILD_SUMMARY_PREFIX == common.SUMMARY_PREFIX


def test_the_deletion_block_reads_the_common_keys(menu, tmp_path):
    """Every key of the block, from a summary written with the common names."""
    line = common.SUMMARY_PREFIX + (
        '{"ok": 3, "extras": {"%s": 2, "%s": 1, "%s": 1, "%s": 4}}'
        % (common.SUMMARY_DELETED, common.SUMMARY_DELETED_ARCHIVED,
           common.SUMMARY_KEPT, common.SUMMARY_REFUSED))
    summary = menu._parse_child_summary(line)
    text = _combined_log(menu, summary, tmp_path)
    assert "SOURCES DELETED: 2  (1 already archived)" in text
    assert "Sources KEPT by a delete gate: 1 (see the child logs for why)" in text
    assert "Refused, output belongs to another source: 4" in text
    # popped into their own lines, never left in the generic extras line too
    assert common.SUMMARY_DELETED not in text     # covers "... (already archived)"
    assert common.SUMMARY_REFUSED not in text
    assert text.count(common.SUMMARY_KEPT) == 1


@needs_tools
def test_a_real_encoder_s_deletions_reach_the_wrapper_s_block(menu, tmp_path):
    src = tmp_path / HOSTILE / "TIFF16"
    _tiff(src / "DSC_0001 (2).tif", 1000)
    _tiff(src / "DSC_0002 [b].tif", 2000)
    out = tmp_path / HOSTILE / "16B_JXL"
    r = subprocess.run(
        [sys.executable, str(REPO / "jxl_tiff_encoder.py"), str(src), str(out),
         "--mode", "2", "--distance", "0", "--delete-source", "--delete-confirm-off",
         "--summary-json", "--workers", "1"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, (r.stdout + r.stderr)[-4000:]
    assert not list(src.glob("*.tif")), "the encoder kept its sources"
    lines = [l for l in r.stdout.splitlines() if l.startswith(common.SUMMARY_PREFIX)]
    assert lines, r.stdout[-3000:]
    summary = menu._parse_child_summary(lines[-1])
    assert summary["extras"].get(common.SUMMARY_DELETED) == 2, summary
    assert "SOURCES DELETED: 2" in _combined_log(menu, summary, tmp_path)
