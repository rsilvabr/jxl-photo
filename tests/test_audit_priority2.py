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
    """Mode 6: a TIFF inside 16b_tiff below the marker is skipped by the
    child's anti-ping-pong rule — the guard must skip it too."""
    sess = tmp_path / "sess"
    e1 = sess / "_EXPORT" / "a" / "16b_tiff"
    e2 = sess / "_EXPORT" / "b" / "16b_tiff"
    _tiffs(e1 / "foto.tif", e2 / "foto.tif")
    entries = [(str(sess / "_EXPORT" / "a"), str(sess / "_EXPORT" / "a"), 6),
               (str(sess / "_EXPORT" / "b"), str(sess / "_EXPORT" / "b"), 6)]
    assert not menu._manifest_output_collisions(entries, TIF_EXTS,
                                                origin="tiff", dest="jxl",
                                                export_marker="_EXPORT")


def test_encoder_skip_applies_only_in_modes_6_7(menu, tmp_path):
    """REGRESSION (2nd audit): outside modes 6/7 the encoder's finders are
    UNFILTERED — a TIFF in a converted_tiff folder IS processed in mode 2,
    so the guard must see the collision (it used to skip it everywhere)."""
    out = tmp_path / "out"
    a, b = tmp_path / "A", tmp_path / "B"
    _tiffs(a / "converted_tiff" / "foto.tif", b / "foto.tif")
    entries = [(str(a), str(out), 2), (str(b), str(out), 2)]
    cols = menu._manifest_output_collisions(entries, TIF_EXTS,
                                            origin="tiff", dest="jxl")
    assert cols, "file the child processes (mode 2, unfiltered) was skipped by the guard"


# ---------------------------------------------------------------------------
# Transcoder / decoder directions (2nd audit regressions 1 and 2)
# ---------------------------------------------------------------------------

JPG_EXTS = {".jpg", ".jpeg"}
JXL_EXTS = {".jxl"}


def _files(*paths: Path, content=b"x"):
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


def test_transcoder_encode_skips_tool_output_dirs(menu, tmp_path):
    """REGRESSION (2nd audit #1): JPEG->JXL skips the toolkit's own output
    folders (recovered_jpeg etc.) on recursive scans — the guard's hardcoded
    TIFF skip set did not cover them and REFUSED a legit manifest."""
    out = tmp_path / "out"
    a, b = tmp_path / "A", tmp_path / "B"
    _files(a / "recovered_jpeg" / "foto.jpg", b / "foto.jpg")
    entries = [(str(a), str(out), 2), (str(b), str(out), 2)]
    cols = menu._manifest_output_collisions(entries, JPG_EXTS,
                                            origin="jpeg", dest="jxl")
    assert not cols, f"collision reported for a file the child never scans: {cols}"


def test_transcoder_encode_still_flags_real_collisions(menu, tmp_path):
    out = tmp_path / "out"
    a, b = tmp_path / "A", tmp_path / "B"
    _files(a / "deep" / "foto.jpg", b / "foto.jpg")
    entries = [(str(a), str(out), 2), (str(b), str(out), 2)]
    assert menu._manifest_output_collisions(entries, JPG_EXTS,
                                            origin="jpeg", dest="jxl")


def test_transcoder_decode_is_unfiltered(menu, tmp_path):
    """JXL->JPEG finders are deliberately UNFILTERED (round-trip sources) —
    a JXL inside 16b_jxl IS decoded, so the guard must see the collision."""
    out = tmp_path / "out"
    a, b = tmp_path / "A", tmp_path / "B"
    _files(a / "16b_jxl" / "foto.jxl", b / "foto.jxl")
    entries = [(str(a), str(out), 2), (str(b), str(out), 2)]
    cols = menu._manifest_output_collisions(entries, JXL_EXTS,
                                            origin="jxl", dest="jpeg")
    assert cols, "decoder-direction file was skipped although the child reads it"


def test_decoder_direction_is_unfiltered(menu, tmp_path):
    """jxl->tiff (decoder) likewise skips nothing."""
    out = tmp_path / "out"
    a, b = tmp_path / "A", tmp_path / "B"
    _files(a / "jxl_16bits" / "foto.jxl", b / "foto.jxl")
    entries = [(str(a), str(out), 2), (str(b), str(out), 2)]
    cols = menu._manifest_output_collisions(entries, JXL_EXTS,
                                            origin="jxl", dest="tiff")
    assert cols


def test_guard_emits_no_child_warnings(menu, tmp_path):
    """REGRESSION (2nd audit #3): resolving directories and sidecars through
    the child spammed its logger (mode 4/5 warnings). The guard must filter
    before resolving, and silence the child while it works (the real run
    emits its own lines)."""
    import logging
    import jxl_tiff_encoder as enc
    s = tmp_path / "Shoot_TIFF"
    _tiffs(s / "foto.tif")
    (s / "foto.xmp").write_text("<x/>")
    (s / "subdir").mkdir()

    records = []

    class _H(logging.Handler):
        def emit(self, r):
            records.append(r)

    h = _H()
    enc.logger.addHandler(h)
    try:
        entries = [(str(s), str(s), 5)]
        menu._manifest_output_collisions(entries, TIF_EXTS, origin="tiff", dest="jxl")
    finally:
        enc.logger.removeHandler(h)
    assert records == [], f"guard leaked child warnings: {[r.getMessage() for r in records]}"
    assert enc.logger.disabled is False, "child logger left disabled"


# ---------------------------------------------------------------------------
# W4 follow-up (2nd audit): overlap is refuse-unattended / confirm-attended
# ---------------------------------------------------------------------------

def _manifest_workflow(entries, unattended=False):
    return {
        'manifest_entries': entries,
        'origin_format': 'tiff', 'dest_format': 'jxl',
        'workers': 2, 'advanced_options': {}, 'dry_run': False,
        'mode_config': {}, 'expert_flags': '',
        'unattended': unattended,
    }


def test_overlap_refused_unattended(menu, tmp_path, monkeypatch, capsys):
    root = tmp_path / "2024"
    sub = root / "sub"
    sub.mkdir(parents=True)
    entries = [(str(root), str(root), 3), (str(sub), str(sub), 3)]
    wf = _manifest_workflow(entries, unattended=True)
    assert menu._execute_manifest_workflow(wf, {}) is False
    assert "unattended" in capsys.readouterr().out


def test_overlap_declined_attended(menu, tmp_path, monkeypatch):
    root = tmp_path / "2024"
    sub = root / "sub"
    sub.mkdir(parents=True)
    entries = [(str(root), str(root), 3), (str(sub), str(sub), 3)]
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp, "console", None)
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    wf = _manifest_workflow(entries, unattended=False)
    assert menu._execute_manifest_workflow(wf, {}) is False


def test_overlap_confirmed_attended_runs(menu, tmp_path, monkeypatch):
    root = tmp_path / "2024"
    sub = root / "sub"
    _tiffs(root / "a.tif", sub / "b.tif")
    entries = [(str(root), str(root), 3), (str(sub), str(sub), 3)]
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp, "console", None)
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    launched = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600: (launched.append(cmd), 0)[1])
    wf = _manifest_workflow(entries, unattended=False)
    assert menu._execute_manifest_workflow(wf, {}) is True
    assert len(launched) == 2


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
