#!/usr/bin/env python3
"""Regressions for the priority-2 audit fixes (wrapper cross-entry guards):

  * W1 — the collision guard only scanned each Source's DIRECT children, but
    mode 2 is recursive-flat: A/deep/foto.tif (entry 1) and B/foto.tif
    (entry 2) sharing a Destination both become <dest>/foto.jxl, invisible
    to the old scan and to any child (separate processes).
  * W2 — an empty Destination cell falls back to Source at load time, which
    bucketed every entry separately and disabled the guard entirely for
    hand-written manifests in modes 1/3/4/5/6/7 (the README's recommended
    format). The guard now resolves each file's output with the CHILD'S OWN
    resolver, so mode 5's <root>/JXL_16bits collapse is seen for what it is.
  * W4 — duplicate or nested Source folders re-process the same files as two
    separate child processes writing the same outputs on sync-mtime luck.
    Refused up front.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_photo as wp


@pytest.fixture
def menu(tmp_path, monkeypatch):
    """A menu on a throwaway config — never the user's real one."""
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


TIF_EXTS = {".tif", ".tiff"}


def _tiffs(*paths: Path):
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")


# ---------------------------------------------------------------------------
# W1 — mode 2 is recursive-flat
# ---------------------------------------------------------------------------

def test_mode2_nested_files_collide_across_entries(menu, tmp_path):
    out = tmp_path / "out"
    a, b = tmp_path / "A", tmp_path / "B"
    _tiffs(a / "deep" / "foto.tif", b / "foto.tif")
    entries = [(str(a), str(out), 2), (str(b), str(out), 2)]
    cols = menu._manifest_output_collisions(entries, TIF_EXTS,
                                            origin="tiff", dest="jxl")
    assert cols, "mode-2 nested file colliding in a shared Destination was missed"


def test_mode2_distinct_stems_do_not_collide(menu, tmp_path):
    out = tmp_path / "out"
    a, b = tmp_path / "A", tmp_path / "B"
    _tiffs(a / "deep" / "foto1.tif", b / "foto2.tif")
    entries = [(str(a), str(out), 2), (str(b), str(out), 2)]
    assert not menu._manifest_output_collisions(entries, TIF_EXTS,
                                                origin="tiff", dest="jxl")


# ---------------------------------------------------------------------------
# W2 — empty Destination must not disable the guard
# ---------------------------------------------------------------------------

def test_mode5_empty_destination_collides(menu, tmp_path):
    """The exact case the guard was built for: sub1/foto.tif and
    sub2/foto.tif both target <root>/JXL_16bits/foto.jxl. With an empty
    Destination cell (fallback: Source) the old guard saw nothing."""
    r = tmp_path / "r"
    sub1, sub2 = r / "sub1", r / "sub2"
    _tiffs(sub1 / "foto.tif", sub2 / "foto.tif")
    # _load_manifest_entries replaces an empty Destination with the Source —
    # the guard receives the entries exactly like this.
    entries = [(str(sub1), str(sub1), 5), (str(sub2), str(sub2), 5)]
    cols = menu._manifest_output_collisions(entries, TIF_EXTS,
                                            origin="tiff", dest="jxl")
    assert cols, "mode-5 collapse with empty Destination was missed"


def test_mode3_distinct_parents_do_not_collide(menu, tmp_path):
    """Mode 3 writes <each parent>/JXL_16bits — different source folders must
    NOT be flagged (the resolver must be real, not a blanket root bucket)."""
    r = tmp_path / "r"
    sub1, sub2 = r / "sub1", r / "sub2"
    _tiffs(sub1 / "foto.tif", sub2 / "foto.tif")
    entries = [(str(sub1), str(sub1), 3), (str(sub2), str(sub2), 3)]
    assert not menu._manifest_output_collisions(entries, TIF_EXTS,
                                                origin="tiff", dest="jxl")


def test_mode6_shared_marker_folder_collides(menu, tmp_path):
    """Two entries under different subfolders of the same _EXPORT both write
    <root>/_EXPORT/16B_JXL/<stem>.jxl."""
    sess = tmp_path / "sess"
    e1, e2 = sess / "_EXPORT" / "16bit", sess / "_EXPORT" / "8bit"
    _tiffs(e1 / "foto.tif", e2 / "foto.tif")
    entries = [(str(e1), str(e1), 6), (str(e2), str(e2), 6)]
    cols = menu._manifest_output_collisions(entries, TIF_EXTS,
                                            origin="tiff", dest="jxl",
                                            export_marker="_EXPORT")
    assert cols, "mode-6 marker collapse across entries was missed"


def test_mode6_file_outside_marker_is_skipped(menu, tmp_path):
    """Files outside the marker resolve to None in the child — flagging them
    would abort a run the child would have completed (false positive)."""
    sess = tmp_path / "sess"
    inside, outside = sess / "_EXPORT" / "16bit", sess / "plain"
    _tiffs(inside / "foto.tif", outside / "foto.tif")
    entries = [(str(inside), str(inside), 6), (str(outside), str(outside), 6)]
    assert not menu._manifest_output_collisions(entries, TIF_EXTS,
                                                origin="tiff", dest="jxl",
                                                export_marker="_EXPORT")


def test_decoder_output_folders_are_not_double_counted(menu, tmp_path):
    """A TIFF sitting in a tiff_16bits subfolder is skipped by the child's
    anti-ping-pong rule — the guard must skip it too."""
    r = tmp_path / "r"
    sub1, sub2 = r / "sub1", r / "sub2"
    _tiffs(sub1 / "tiff_16bits" / "foto.tif", sub2 / "tiff_16bits" / "foto.tif")
    entries = [(str(sub1), str(sub1), 5), (str(sub2), str(sub2), 5)]
    assert not menu._manifest_output_collisions(entries, TIF_EXTS,
                                                origin="tiff", dest="jxl")


def test_legacy_entries_keep_flat_destination_scan(menu, tmp_path):
    """No direction info (or no Mode cell) -> the old behavior, unchanged."""
    a, b = tmp_path / "A", tmp_path / "B"
    _tiffs(a / "foto.tif", b / "foto.tif")
    out = tmp_path / "out"
    entries = [(str(a), str(out), None), (str(b), str(out), None)]
    cols = menu._manifest_output_collisions(entries, TIF_EXTS,
                                            origin="tiff", dest="jxl")
    assert cols, "legacy flat-Destination detection regressed"
    # And without origin/dest, the resolver path must not engage at all.
    cols2 = menu._manifest_output_collisions(
        [(str(tmp_path / "A"), str(tmp_path / "A"), 5),
         (str(tmp_path / "B"), str(tmp_path / "B"), 5)], TIF_EXTS)
    assert not cols2


# ---------------------------------------------------------------------------
# W4 — duplicate / nested source trees
# ---------------------------------------------------------------------------

def test_nested_sources_are_detected(menu, tmp_path):
    root = tmp_path / "2024"
    sub = root / "sub"
    sub.mkdir(parents=True)
    entries = [(str(root), str(root), 3), (str(sub), str(sub), 3)]
    overlaps = menu._manifest_source_overlaps(entries)
    assert overlaps == [(str(root), str(sub))]


def test_duplicate_rows_are_detected(menu, tmp_path):
    root = tmp_path / "2024"
    root.mkdir()
    entries = [(str(root), str(root), 0), (str(root), str(root), 0)]
    assert menu._manifest_source_overlaps(entries)


def test_disjoint_sources_are_fine(menu, tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir(); b.mkdir()
    entries = [(str(a), str(a), 3), (str(b), str(b), 3)]
    assert not menu._manifest_source_overlaps(entries)


def test_sibling_prefix_names_are_not_nested(menu, tmp_path):
    """'2024' and '2024_final' share a string prefix but neither is inside
    the other — the boundary check must not flag them."""
    a, b = tmp_path / "2024", tmp_path / "2024_final"
    a.mkdir(); b.mkdir()
    entries = [(str(a), str(a), 3), (str(b), str(b), 3)]
    assert not menu._manifest_source_overlaps(entries)
