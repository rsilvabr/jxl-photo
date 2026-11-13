"""The delete preview's file count reads no marker (#652, review V1 of the refactor).

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


@pytest.mark.parametrize("conversion,dest", [("jxl_to_jpeg_auto", "jpeg"),
                                             ("jxl_to_jpeg_force", "jpeg"),
                                             ("jxl_to_png", "png")])
def test_the_jxl_to_jpeg_count_reads_no_marker(menu, tmp_path, monkeypatch, conversion,
                                               dest):
    """The transcoder's decode leaves out derivatives in mode 6 by their
    markers too (#602); the count must not read them either (the v2.30.0
    review's M16: only the recompressor's side was tested)."""
    import jxl_jpeg_transcoder as tr
    root = tmp_path / HOSTILE
    for name in ("DSC_0001 (2).jxl", "DSC_0002 [b].jxl"):
        p = root / "2025_EXPORT" / "16B_JXL" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    reads = []
    monkeypatch.setattr(tr, "_read_derived_markers_batch",
                        lambda paths: reads.append(list(paths)) or {})
    wf = {"origin_format": "jxl", "dest_format": dest, "input_dir": str(root),
          "conversion_type": conversion}
    assert menu._count_origin_files(wf, 6) == 2
    assert reads == [], "the preview count read the derivative markers"
