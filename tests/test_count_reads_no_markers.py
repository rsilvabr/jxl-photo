"""The delete preview's file count reads no marker (review V1 of the refactor).

The review screen redraws the count on every turn. Since F8 the count asks the
child which files a run takes (_planned_sources); the recompressor's modes 6/7
leave out derivatives by reading every JXL's markers with exiftool — which the
count did silently, on every redraw (seconds to minutes on a large library,
no progress line). The count passes read_markers=False: no exiftool, and the
number v2.29.1 showed (derivatives included).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_photo as wp  # noqa: E402
import jxl_recompressor as rec  # noqa: E402

HOSTILE = "250118_吾妻山公園 [FINAL] 100%off %d"


@pytest.fixture
def menu(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


@pytest.mark.parametrize("mode", [6, 7])
def test_the_jxl_to_jxl_count_reads_no_marker(menu, tmp_path, monkeypatch, mode):
    root = tmp_path / HOSTILE
    for name in ("DSC_0001 (2).jxl", "DSC_0002 [b].jxl"):
        p = root / "2025_EXPORT" / "16B_JXL" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    reads = []
    monkeypatch.setattr(rec, "_read_derived_markers_batch",
                        lambda paths: reads.append(list(paths)) or {})
    wf = {"origin_format": "jxl", "dest_format": "jxl", "input_dir": str(root),
          "mode_config": {"export_subfolder": "16B_JXL"} if mode == 7 else {}}
    assert menu._count_origin_files(wf, mode) == 2
    assert reads == [], "the preview count read the derivative markers"
