#!/usr/bin/env python3
"""Regressions for the round-23 audit fixes.

Every one of these was reproduced against the shipped code before the fix:

  * B1 — the manifest collision scan was skipped for modes 0/6/7, but mode 0
    HONORS the Destination column and modes 6/7 write to a SIBLING of the
    Source (<marker>/16B_JXL). Two sibling sources under one _EXPORT silently
    produced ONE output: the second entry reported "SKIP (sync: up to date)"
    and exited 0.
  * B2 — --clean-staging deleted staging leftovers during a --dry-run.
  * B3 — messages emitted before setup_logger() (the --distance clamp warning,
    every --clean-staging line) went to a handler-less logger and never
    reached the log file.
  * B4 — the transcoder overwrote its own documented TEMP2_DIR setting with
    args.staging unconditionally, and gated --clean-staging on args.staging
    instead of the effective staging dir.
  * B5 — a manifest kept launching entries after a child exited 2 (aborted),
    which is exactly the "grinding on a full disk" the abort exists to stop.
  * B6 — TIFF SubfileType 4 (MASK, used by film scanners for the IR page) was
    downgraded to 2 on decode.
"""

import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_jpeg_transcoder as tr
import jxl_photo as wp
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc


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
# B1 — which modes can collide across manifest entries
# ---------------------------------------------------------------------------

def test_mode7_siblings_under_one_marker_need_the_scan(menu, tmp_path):
    """_EXPORT/TIFF16 and _EXPORT/AdobeRGB do NOT overlap (so the overlap guard
    stays quiet) yet both write into _EXPORT/16B_JXL."""
    marker = tmp_path / "2024_EXPORT"
    entries = [(str(marker / "TIFF16"), str(marker / "TIFF16"), 7),
               (str(marker / "AdobeRGB"), str(marker / "AdobeRGB"), 7)]
    assert menu._manifest_needs_collision_scan(entries, "_EXPORT")


def test_mode7_distinct_markers_still_skip_the_scan(menu, tmp_path):
    """The auto-generated shape (one entry per export folder) must stay cheap."""
    entries = [(str(tmp_path / "A_EXPORT" / "TIFF16"),
                str(tmp_path / "A_EXPORT" / "TIFF16"), 7),
               (str(tmp_path / "B_EXPORT" / "TIFF16"),
                str(tmp_path / "B_EXPORT" / "TIFF16"), 7)]
    assert not menu._manifest_needs_collision_scan(entries, "_EXPORT")


def test_mode6_same_marker_needs_the_scan(menu, tmp_path):
    marker = tmp_path / "shoot_EXPORT"
    entries = [(str(marker / "a"), str(marker / "a"), 6),
               (str(marker / "b"), str(marker / "b"), 6)]
    assert menu._manifest_needs_collision_scan(entries, "_EXPORT")


def test_marker_below_a_lone_source_skips_the_scan(menu, tmp_path):
    """Source above the markers: every marker it anchors on lies INSIDE that
    Source, so every output does too. One entry cannot collide with itself
    across processes — two files of the SAME entry landing on one output is
    the child's own _abort_on_duplicate_outputs, which sees the whole entry.

    This is the shape of the auto-generated 'sync the whole library' manifest
    (G:\\2024, G:\\2025, ... in mode 6), which used to walk every tree before
    converting a single file.
    """
    entries = [(str(tmp_path / "Fotos"), str(tmp_path / "Fotos"), 7)]
    assert not menu._manifest_needs_collision_scan(entries, "_EXPORT")


def test_markers_below_disjoint_sources_skip_the_scan(menu, tmp_path):
    entries = [(str(tmp_path / "2024"), str(tmp_path / "2024"), 6),
               (str(tmp_path / "2025"), str(tmp_path / "2025"), 6),
               (str(tmp_path / "2026"), str(tmp_path / "2026"), 6)]
    assert not menu._manifest_needs_collision_scan(entries, "_EXPORT")


def test_markers_below_nested_sources_need_the_scan(menu, tmp_path):
    """Nested Sources can reach the same marker folder from two entries. The
    overlap guard only WARNS on this for an attended run, so the cheap check
    must not treat 'no overlap' as given."""
    entries = [(str(tmp_path / "Fotos"), str(tmp_path / "Fotos"), 6),
               (str(tmp_path / "Fotos" / "2024"), str(tmp_path / "Fotos" / "2024"), 6)]
    assert menu._manifest_needs_collision_scan(entries, "_EXPORT")


def test_mode0_with_a_real_destination_needs_the_scan(menu, tmp_path):
    """Mode 0 is the one 'per-source' mode that honors the Destination cell."""
    out = str(tmp_path / "out")
    entries = [(str(tmp_path / "A"), out, 0), (str(tmp_path / "B"), out, 0)]
    assert menu._manifest_needs_collision_scan(entries, "_EXPORT")


def test_mode0_in_place_still_skips_the_scan(menu, tmp_path):
    entries = [(str(tmp_path / "A"), str(tmp_path / "A"), 0),
               (str(tmp_path / "B"), str(tmp_path / "B"), 0)]
    assert not menu._manifest_needs_collision_scan(entries, "_EXPORT")


@pytest.mark.parametrize("mode", [1, 3, 8])
def test_source_derived_modes_still_skip_the_scan(menu, tmp_path, mode):
    """Modes 1/3/8 derive their output from the Source itself, and overlapping
    Sources are refused earlier — they genuinely cannot collide."""
    entries = [(str(tmp_path / "A"), str(tmp_path / "A"), mode),
               (str(tmp_path / "B"), str(tmp_path / "B"), mode)]
    assert not menu._manifest_needs_collision_scan(entries, "_EXPORT")


@pytest.mark.parametrize("mode", [2, 4, 5])
def test_shared_target_modes_always_scan(menu, tmp_path, mode):
    entries = [(str(tmp_path / "A"), str(tmp_path / "A"), mode)]
    assert menu._manifest_needs_collision_scan(entries, "_EXPORT")


def test_legacy_manifest_without_mode_always_scans(menu, tmp_path):
    entries = [(str(tmp_path / "A"), str(tmp_path / "A"), None)]
    assert menu._manifest_needs_collision_scan(entries, "_EXPORT")


def test_mode7_sibling_collision_is_actually_reported(menu, tmp_path):
    """End to end: once the scan runs, it must find the collapse."""
    marker = tmp_path / "2024_EXPORT"
    _tiffs(marker / "TIFF16" / "photo.tif", marker / "AdobeRGB" / "photo.tif")
    entries = [(str(marker / "TIFF16"), str(marker / "TIFF16"), 7),
               (str(marker / "AdobeRGB"), str(marker / "AdobeRGB"), 7)]
    cols = menu._manifest_output_collisions(entries, TIF_EXTS,
                                            origin="tiff", dest="jxl",
                                            export_marker="_EXPORT")
    assert cols, "two mode-7 siblings collapsing into <marker>/16B_JXL went unseen"


def test_entry_marker_dir_ignores_a_marker_free_source(menu, tmp_path):
    assert menu._entry_marker_dir(str(tmp_path / "Fotos" / "2024"), "_EXPORT") is None


def test_manifest_refuses_colliding_mode7_siblings(menu, tmp_path, monkeypatch):
    """The behavioural proof, through the real entry point: two sibling sources
    under one _EXPORT must abort the run BEFORE any child is launched.

    Pre-fix this returned no collisions (the scan was skipped for mode 7), both
    children ran, and the second file was reported "SKIP (sync: up to date)" —
    one JXL on disk, exit 0, no warning.
    """
    launched = []
    monkeypatch.setattr(menu, "_run_subprocess",
                        lambda cmd: launched.append(cmd) or 0)
    monkeypatch.setattr(menu, "_render_manifest_summary", lambda *a, **kw: None)

    marker = tmp_path / "2024_EXPORT"
    _tiffs(marker / "TIFF16" / "photo.tif", marker / "AdobeRGB" / "photo.tif")
    workflow = {
        "manifest_entries": [(str(marker / "TIFF16"), str(marker / "TIFF16"), 7),
                             (str(marker / "AdobeRGB"), str(marker / "AdobeRGB"), 7)],
        "origin_format": "tiff", "dest_format": "jxl",
        "workers": 2, "advanced_options": {}, "dry_run": False,
    }
    assert menu._execute_manifest_workflow(workflow, {}) is False
    assert not launched, "children ran despite two entries sharing one output folder"


def test_manifest_runs_when_markers_differ(menu, tmp_path, monkeypatch):
    """Control: the ordinary one-entry-per-export-folder manifest must NOT be
    refused (and must still take the cheap path)."""
    launched = []
    monkeypatch.setattr(menu, "_run_subprocess",
                        lambda cmd: launched.append(cmd) or 0)
    monkeypatch.setattr(menu, "_render_manifest_summary", lambda *a, **kw: None)

    a, b = tmp_path / "A_EXPORT", tmp_path / "B_EXPORT"
    _tiffs(a / "TIFF16" / "photo.tif", b / "TIFF16" / "photo.tif")
    workflow = {
        "manifest_entries": [(str(a / "TIFF16"), str(a / "TIFF16"), 7),
                             (str(b / "TIFF16"), str(b / "TIFF16"), 7)],
        "origin_format": "tiff", "dest_format": "jxl",
        "workers": 2, "advanced_options": {}, "dry_run": False,
    }
    assert menu._execute_manifest_workflow(workflow, {}) is True
    assert len(launched) == 2


# ---------------------------------------------------------------------------
# B2/B3 — --clean-staging must not run on a dry run, and must be audible
# ---------------------------------------------------------------------------

def _stale_leftover(staging: Path) -> Path:
    """A staging leftover old enough for the sweep to consider it orphaned."""
    staging.mkdir(parents=True, exist_ok=True)
    leftover = staging / ("a" * 32 + "_old_p0.jxl")
    leftover.write_bytes(b"x" * 1000)
    old = time.time() - 10 * 3600
    import os
    os.utime(leftover, (old, old))
    return leftover


def _run_script(script: str, *args) -> subprocess.CompletedProcess:
    root = Path(__file__).resolve().parent.parent
    return subprocess.run([sys.executable, str(root / script), *map(str, args)],
                          capture_output=True, text=True, timeout=300)


def test_encoder_clean_staging_does_not_run_on_a_dry_run(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    tifffile.imwrite(str(src / "a.tif"),
                     np.zeros((8, 8, 3), np.uint16), photometric="rgb")
    staging = tmp_path / "stg"
    leftover = _stale_leftover(staging)
    _run_script("jxl_tiff_encoder.py", src, "--mode", "0", "--dry-run",
                "--staging", staging, "--clean-staging")
    assert leftover.exists(), "a dry run swept the staging directory"


def test_decoder_clean_staging_does_not_run_on_a_dry_run(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.jxl").write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n")
    staging = tmp_path / "stg"
    leftover = _stale_leftover(staging)
    _run_script("jxl_tiff_decoder.py", src, "--mode", "0", "--dry-run",
                "--staging", staging, "--clean-staging")
    assert leftover.exists(), "a dry run swept the staging directory"


def test_transcoder_clean_staging_does_not_run_on_a_dry_run(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    staging = tmp_path / "stg"
    leftover = _stale_leftover(staging)
    _run_script("jxl_jpeg_transcoder.py", src, "--force-transcode", "--mode", "0",
                "--dry-run", "--staging", staging, "--clean-staging")
    assert leftover.exists(), "a dry run swept the staging directory"


def test_encoder_distance_clamp_warning_reaches_the_log(tmp_path):
    """The warning used to be emitted before setup_logger(), so it landed on
    raw stderr via logging.lastResort and never in the log file."""
    src = tmp_path / "src"
    src.mkdir()
    tifffile.imwrite(str(src / "a.tif"),
                     np.zeros((8, 8, 3), np.uint16), photometric="rgb")
    r = _run_script("jxl_tiff_encoder.py", src, "--mode", "0", "--dry-run",
                    "--distance", "0.02")
    log_line = next((ln for ln in r.stdout.splitlines() if "Log saved to:" in ln), "")
    log_path = Path(log_line.split("Log saved to:", 1)[1].strip())
    assert log_path.exists()
    assert "behaves exactly like" in log_path.read_text(encoding="utf-8")


def test_encoder_clean_staging_reports_what_it_removed(tmp_path):
    """The sweep summary was logged before setup_logger() at INFO level, so it
    was dropped entirely: files vanished with no line anywhere."""
    src = tmp_path / "src"
    src.mkdir()
    tifffile.imwrite(str(src / "a.tif"),
                     np.zeros((8, 8, 3), np.uint16), photometric="rgb")
    staging = tmp_path / "stg"
    leftover = _stale_leftover(staging)
    r = _run_script("jxl_tiff_encoder.py", src, "--mode", "0",
                    "--staging", staging, "--clean-staging")
    assert not leftover.exists(), "a real run should sweep the orphan"
    assert "removed 1 leftover file" in r.stdout


# ---------------------------------------------------------------------------
# B4 — the transcoder's documented TEMP2_DIR setting must survive
# ---------------------------------------------------------------------------

def test_transcoder_keeps_temp2_dir_when_no_staging_flag(monkeypatch, tmp_path):
    """cmd_* used to do `TEMP2_DIR = args.staging` unconditionally, wiping the
    script-level setting the README tells users to edit."""
    monkeypatch.setattr(tr, "TEMP2_DIR", str(tmp_path / "from_script"))
    seen = {}

    def _fake_group(pairs, workers, *a, **kw):
        seen["staging"] = tr.TEMP2_DIR
        return []

    monkeypatch.setattr(tr, "process_group_transcode", _fake_group)
    monkeypatch.setattr(tr, "setup_logger", lambda: tmp_path / "x.log")
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.jpg").write_bytes(b"\xff\xd8\xff\xd9")

    args = tr.build_parser().parse_args([str(src), "--mode", "0"])
    tr.cmd_transcode(args)
    assert seen["staging"] == str(tmp_path / "from_script")


def test_transcoder_staging_flag_still_wins(monkeypatch, tmp_path):
    monkeypatch.setattr(tr, "TEMP2_DIR", str(tmp_path / "from_script"))
    seen = {}

    def _fake_group(pairs, workers, *a, **kw):
        seen["staging"] = tr.TEMP2_DIR
        return []

    monkeypatch.setattr(tr, "process_group_transcode", _fake_group)
    monkeypatch.setattr(tr, "setup_logger", lambda: tmp_path / "x.log")
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.jpg").write_bytes(b"\xff\xd8\xff\xd9")

    args = tr.build_parser().parse_args(
        [str(src), "--mode", "0", "--staging", str(tmp_path / "flag")])
    tr.cmd_transcode(args)
    assert seen["staging"] == str(tmp_path / "flag")


# ---------------------------------------------------------------------------
# B5 — a manifest must stop when a child aborts (exit 2)
# ---------------------------------------------------------------------------

def test_manifest_stops_after_a_child_aborts(menu, tmp_path, monkeypatch):
    """Exit 2 means "the volume filled, nothing was deleted, retry later".
    Launching the remaining entries just repeats the failure."""
    launched = []

    def _fake_run(cmd):
        launched.append(cmd)
        return 2

    monkeypatch.setattr(menu, "_run_subprocess", _fake_run)
    monkeypatch.setattr(menu, "_render_manifest_summary",
                        lambda *a, **kw: None)
    a, b = tmp_path / "A", tmp_path / "B"
    for d in (a, b):
        d.mkdir()
    workflow = {
        "manifest_entries": [(str(a), str(a), 0), (str(b), str(b), 0)],
        "origin_format": "tiff", "dest_format": "jxl",
        "workers": 2, "advanced_options": {}, "dry_run": False,
    }
    assert menu._execute_manifest_workflow(workflow, {}) is False
    assert len(launched) == 1, "the manifest kept going after an abort"


def test_manifest_continues_after_a_plain_failure(menu, tmp_path, monkeypatch):
    """Exit 1 (some files failed) is NOT an abort: the remaining entries are
    independent folders and must still run."""
    launched = []

    def _fake_run(cmd):
        launched.append(cmd)
        return 1

    monkeypatch.setattr(menu, "_run_subprocess", _fake_run)
    monkeypatch.setattr(menu, "_render_manifest_summary", lambda *a, **kw: None)
    a, b = tmp_path / "A", tmp_path / "B"
    for d in (a, b):
        d.mkdir()
    workflow = {
        "manifest_entries": [(str(a), str(a), 0), (str(b), str(b), 0)],
        "origin_format": "tiff", "dest_format": "jxl",
        "workers": 2, "advanced_options": {}, "dry_run": False,
    }
    menu._execute_manifest_workflow(workflow, {})
    assert len(launched) == 2


# ---------------------------------------------------------------------------
# B6 — SubfileType 4 (MASK) must survive the round trip
# ---------------------------------------------------------------------------

def test_decoder_restores_subfiletype_4(tmp_path):
    """Film scanners tag the IR/mask page SubfileType=4. tifffile refuses the
    value on its `subfiletype=` parameter, so it used to be downgraded to 2 —
    silently changing the page's role."""
    out = tmp_path / "scan.tif"
    entries = [
        (np.zeros((16, 16, 3), np.uint16), 0),
        (np.zeros((16, 16), np.uint16), 4),
    ]
    with tifffile.TiffWriter(str(out)) as w:
        for arr, sft in entries:
            kwargs = dec._page_subfiletype_kwargs(sft)
            kwargs["photometric"] = "rgb" if arr.ndim == 3 else "minisblack"
            kwargs["metadata"] = None
            kwargs["software"] = ""
            w.write(arr, **kwargs)

    with tifffile.TiffFile(str(out)) as t:
        assert [int(p.subfiletype or 0) for p in t.pages] == [0, 4]


def test_transcoder_should_process_survives_a_vanished_file(tmp_path):
    """should_process() runs BEFORE the worker's try block, so an OSError here
    escaped as a bare "error" with no useful message."""
    src, dst = tmp_path / "a.jpg", tmp_path / "a.jxl"
    src.write_bytes(b"\xff\xd8\xff\xd9")
    dst.write_bytes(b"x")
    real_stat = Path.stat

    def _vanishing(self, *a, **kw):
        if self == src:
            raise FileNotFoundError(src)
        return real_stat(self, *a, **kw)

    import unittest.mock as mock
    with mock.patch.object(Path, "stat", _vanishing):
        # Must not raise, and must fall through to attempting the conversion.
        assert tr.should_process(src, dst, smart=True, reconvert_val=False) is True


def _capture_logger(logger):
    """Attach a list-collecting handler to a script's logger."""
    import logging

    records = []

    class _Grab(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Grab()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return records, handler


def test_preflight_reports_a_destination_that_does_not_exist_yet(tmp_path, monkeypatch):
    """Output folders are created per group by the children, so at preflight
    time the destination usually does NOT exist — and disk_usage() raises on a
    missing path, dropping the line on exactly the first run."""
    src = tmp_path / "src" / "a.tif"
    src.parent.mkdir(parents=True)
    tifffile.imwrite(str(src), np.zeros((8, 8, 3), np.uint16), photometric="rgb")
    missing_dest = tmp_path / "not" / "created" / "yet"
    assert not missing_dest.exists()

    monkeypatch.setattr(enc, "_PREFLIGHT_MIN_BYTES", 0)
    monkeypatch.setattr(enc, "_measure_batch_ratio",
                        lambda tiffs, d, e: (0.5, [("a.tif", 0.5)]))
    records, handler = _capture_logger(enc.logger)
    try:
        groups = {missing_dest: [(src, missing_dest / "a.jxl", 0, False, 0, 3)]}
        enc._preflight_space(groups, 0.1, 7, None)
    finally:
        enc.logger.removeHandler(handler)

    assert any("Preflight: destination" in m for m in records), (
        f"the destination estimate was dropped for a not-yet-created folder: {records}")


def test_encoder_warns_that_mode1_ignores_the_output_folder(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    tifffile.imwrite(str(src / "a.tif"),
                     np.zeros((8, 8, 3), np.uint16), photometric="rgb")
    r = _run_script("jxl_tiff_encoder.py", src, tmp_path / "ignored",
                    "--mode", "1", "--dry-run")
    assert "--mode 1 ignores the output folder" in r.stdout


def test_decoder_warns_that_mode1_ignores_the_output_folder(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.jxl").write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n")
    r = _run_script("jxl_tiff_decoder.py", src, tmp_path / "ignored",
                    "--mode", "1", "--dry-run")
    assert "--mode 1 ignores the output folder" in r.stdout


def test_page_subfiletype_kwargs_uses_the_plain_parameter_when_accepted():
    """1 (reduced) and 2 (page) are valid tifffile enum members — keep using
    the documented parameter for them instead of a raw tag."""
    assert dec._page_subfiletype_kwargs(1) == {"subfiletype": 1}
    assert dec._page_subfiletype_kwargs(2) == {"subfiletype": 2}
    assert dec._page_subfiletype_kwargs(0) == {}
