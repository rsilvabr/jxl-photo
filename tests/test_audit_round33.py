#!/usr/bin/env python3
"""Round 33 — _manifest_needs_collision_scan never compared its two families
against each other (bug #314, regression from 1906819 / bug #306).

The cheap check buckets entries into `marker_dirs` (modes 6/7 whose export
marker is an ANCESTOR of the Source; the output folder is a constant
subfolder of the marker dir, shared by every entry under it) and
`within_source` (mode 0 in-place, 1, 3, 8, and 6/7 with the marker below the
Source) — and then only looked for collisions WITHIN each family.

Cross-family example that sailed through: a mode-6 entry anchored on
G:\\_EXPORT (outputs land in G:\\_EXPORT\\16B_JXL) next to a mode-0 in-place
entry whose Source IS G:\\_EXPORT\\16B_JXL. Disjoint Sources in both families,
one output folder, two child processes writing it — and
_manifest_source_overlaps stays silent because the Sources are disjoint too.
The pre-1906819 code returned True for every marker-below-Source entry and
would have forced the scan.

Every test here was reproduced against the pre-fix code (the True-asserting
ones failed there).
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


# ---------------------------------------------------------------------------
# M2 — the cross-family check
# ---------------------------------------------------------------------------

def test_marker_output_folder_as_a_mode0_source_needs_the_scan(menu, tmp_path):
    """The exact M2 shape: mode 6 anchored on <tmp>/shoot_EXPORT writes
    <tmp>/shoot_EXPORT/16B_JXL; a mode-0 in-place entry whose Source IS that
    output folder converts the same TIFFs a second time, onto the same files.
    The pre-fix check returned False: the Sources are disjoint, so neither
    family saw the other."""
    marker = tmp_path / "shoot_EXPORT"
    entries = [(str(marker), str(marker), 6),
               (str(marker / "16B_JXL"), str(marker / "16B_JXL"), 0)]
    assert menu._manifest_needs_collision_scan(entries, "_EXPORT")


def test_mode0_source_inside_the_output_folder_needs_the_scan(menu, tmp_path):
    """Containment one level deeper is the same collision."""
    marker = tmp_path / "shoot_EXPORT"
    entries = [(str(marker / "TIFF16"), str(marker / "TIFF16"), 7),
               (str(marker / "16B_JXL" / "2024"), str(marker / "16B_JXL" / "2024"), 0)]
    assert menu._manifest_needs_collision_scan(entries, "_EXPORT")


def test_marker_dir_inside_a_within_source_tree_needs_the_scan(menu, tmp_path):
    """The reverse containment: a mode-6 Source whose marker sits BELOW it
    lands in within_source, yet its recursive scan reaches the SAME marker
    folder a mode-7 sibling entry anchors on — both write
    <tmp>/lib/shoot_EXPORT/16B_JXL."""
    entries = [(str(tmp_path / "lib"), str(tmp_path / "lib"), 6),
               (str(tmp_path / "lib" / "shoot_EXPORT" / "TIFF16"),
                str(tmp_path / "lib" / "shoot_EXPORT" / "TIFF16"), 7)]
    assert menu._manifest_needs_collision_scan(entries, "_EXPORT")


def test_ambiguous_sibling_of_the_output_folder_fails_safe(menu, tmp_path):
    """A mode-0 Source that is a sibling of the marker family's output folder
    (<marker>/TIFF16 next to <marker>/16B_JXL) cannot actually collide — but
    the wrapper does not know the run's direction here, so it cannot name the
    output subfolder (16B_JXL vs 16B_TIFF vs the transcoder's pair). It must
    fail SAFE and take the scan rather than guess a folder name."""
    marker = tmp_path / "shoot_EXPORT"
    entries = [(str(marker / "16bit"), str(marker / "16bit"), 7),
               (str(marker / "TIFF16"), str(marker / "TIFF16"), 0)]
    assert menu._manifest_needs_collision_scan(entries, "_EXPORT")


def test_disjoint_cross_family_entries_still_skip_the_scan(menu, tmp_path):
    """The #306 fast path must survive: a marker-anchored entry and a within-
    source entry with DISJOINT trees genuinely cannot share an output folder,
    because the marker family's outputs never leave the marker dir."""
    entries = [(str(tmp_path / "shoot_EXPORT" / "TIFF16"),
                str(tmp_path / "shoot_EXPORT" / "TIFF16"), 7),
               (str(tmp_path / "elsewhere"), str(tmp_path / "elsewhere"), 0)]
    assert not menu._manifest_needs_collision_scan(entries, "_EXPORT")


def test_disjoint_marker_below_source_mixes_still_skip_the_scan(menu, tmp_path):
    """The auto-generated shape from #306, now mixed: whole-library mode-6
    entries (marker below Source) plus one marker-anchored entry somewhere
    else entirely."""
    entries = [(str(tmp_path / "2024"), str(tmp_path / "2024"), 6),
               (str(tmp_path / "2025"), str(tmp_path / "2025"), 6),
               (str(tmp_path / "other" / "A_EXPORT" / "TIFF16"),
                str(tmp_path / "other" / "A_EXPORT" / "TIFF16"), 7)]
    assert not menu._manifest_needs_collision_scan(entries, "_EXPORT")
