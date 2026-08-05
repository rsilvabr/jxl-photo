#!/usr/bin/env python3
"""Regressions for the round-24 audit fixes (bugs #242-#251).

Every one was reproduced against the shipped code before the fix:

  * #242 — the manifest collision guard resolved mode 0 IN-PLACE, ignoring the
    Destination column the cmd builder actually passes. #234 had re-opened the
    (expensive) scan for mode 0, so it ran and found nothing: two entries
    sharing one Destination collapsed onto a single output in silence.
  * #243 — auto-generated manifests for the RECURSIVE modes (3/4/5) emitted one
    entry per folder, so the root entry and every subfolder entry handed the
    same files to separate child processes.
  * #244 — the overlap guard ignored the mode and flagged nested Sources in the
    FLAT modes (0/1), where they cannot overlap. Unattended (--run-preset) that
    is a hard refusal, so a scheduled mode-0 manifest preset never ran.
  * #245 — _session_number_error accepts "8.0" on purpose, but the consumers
    did a plain int()/str(): int("8.0") raised, and --workers 8.0 reached the
    child's argparse.
  * #246 — the transcoder's cmd_transcode/cmd_convert dry runs returned before
    record_summary(), so the wrapper read the untouched default and reported a
    simulation as a finished real run (dry_run=false, ok=0, log="").
  * #247 — a child that found no files emitted no summary at all (decoder) or a
    blank one (transcoder); the recap then printed "(no summary - ok)" in red,
    documented to mean "the child crashed, was killed, or never launched".
  * #248 — the Auto Mode report carried literal, never-formatted "{dest}"
    placeholders and promised folder names no script creates.
  * #249 — the advanced-options help announced `ignore` as the multi-page
    default (it is `split`), and the plain-text branch fell back to `ignore`
    on a typo — silently dropping the IR/mask page of every film scan.
  * #250 — mode 8 was labelled "DELETE originals" even with delete_source off.
  * #251 — the transcoder validated neither --distance nor --quality.
"""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_jpeg_transcoder as tr
import jxl_photo as wp
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def menu():
    """An InteractiveMenu with no config/UI wiring — these methods need none."""
    return wp.InteractiveMenu.__new__(wp.InteractiveMenu)


def _tiff(path: Path, value: int = 1000):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.full((16, 16, 3), value, np.uint16),
                     photometric="rgb")


# ---------------------------------------------------------------------------
# #242 — mode-0 manifest collisions
# ---------------------------------------------------------------------------

def test_242_mode0_shared_destination_is_a_collision(tmp_path, menu):
    """Two mode-0 entries writing into one Destination must be refused.

    Pre-fix the resolver returned f.parent for mode 0, so the two files
    resolved to their OWN folders and the guard saw nothing.
    """
    a, b, out = tmp_path / "A", tmp_path / "B", tmp_path / "OUT"
    _tiff(a / "foto.tif")
    _tiff(b / "foto.tif", 60000)
    out.mkdir()
    entries = [(str(a), str(out), 0), (str(b), str(out), 0)]

    assert menu._manifest_needs_collision_scan(entries, "_EXPORT") is True
    collisions = menu._manifest_output_collisions(
        entries, {".tif", ".tiff"}, origin="tiff", dest="jxl",
        export_marker="_EXPORT", export_subfolder=None)
    assert len(collisions) == 1
    assert {Path(collisions[0][0]).parent.name,
            Path(collisions[0][1]).parent.name} == {"A", "B"}


def test_242_mode0_in_place_entries_still_do_not_collide(tmp_path, menu):
    """Destination == Source is the common auto-generated shape: still quiet."""
    a, b = tmp_path / "A", tmp_path / "B"
    _tiff(a / "foto.tif")
    _tiff(b / "foto.tif")
    entries = [(str(a), str(a), 0), (str(b), str(b), 0)]

    assert menu._manifest_output_collisions(
        entries, {".tif", ".tiff"}, origin="tiff", dest="jxl",
        export_marker="_EXPORT", export_subfolder=None) == []


def test_242_mode8_stays_in_place(tmp_path, menu):
    """Mode 8 is in-place recursive and must NOT follow the Destination cell."""
    a = tmp_path / "A"
    _tiff(a / "sub" / "foto.tif")
    entries = [(str(a), str(tmp_path / "IGNORED"), 8)]

    assert menu._manifest_output_collisions(
        entries, {".tif", ".tiff"}, origin="tiff", dest="jxl",
        export_marker="_EXPORT", export_subfolder=None) == []


# ---------------------------------------------------------------------------
# #243 — auto-generated manifests for recursive modes
# ---------------------------------------------------------------------------

def _analysis(tmp_path):
    for i in range(3):
        _tiff(tmp_path / f"root{i}.tif")
        _tiff(tmp_path / "sub1" / f"a{i}.tif")
        _tiff(tmp_path / "sub2" / f"b{i}.tif")
    fa = wp.FolderAnalyzer(tmp_path, "tiff", "jxl", "_EXPORT")
    return fa, fa.analyze()


def test_243_mode3_manifest_has_one_entry_per_tree(tmp_path):
    """The child recurses in mode 3, so the root entry already covers sub1/sub2."""
    fa, analysis = _analysis(tmp_path)
    mappings = fa.generate_manifest(analysis, 3)

    assert len(mappings) == 1
    src, _dst, count, mode = mappings[0]
    assert Path(src) == tmp_path
    assert mode == 3
    # The dropped folders' files are folded in, not lost from the preview.
    assert count == 9


def test_243_mode3_keeps_disjoint_folders(tmp_path):
    """Nothing is merged when the folders are siblings rather than nested."""
    for i in range(3):
        _tiff(tmp_path / "sub1" / f"a{i}.tif")
        _tiff(tmp_path / "sub2" / f"b{i}.tif")
    fa = wp.FolderAnalyzer(tmp_path, "tiff", "jxl", "_EXPORT")
    mappings = fa.generate_manifest(fa.analyze(), 3)

    assert sorted(Path(s).name for s, _d, _c, _m in mappings) == ["sub1", "sub2"]


def test_243_mode5_still_emits_entries_when_root_has_files(tmp_path):
    """Modes 4/5 exclude the root ENTRY, so the root must not swallow the rest."""
    fa, analysis = _analysis(tmp_path)
    mappings = fa.generate_manifest(analysis, 5)

    assert sorted(Path(s).name for s, _d, _c, _m in mappings) == ["sub1", "sub2"]


def test_243_generated_manifest_passes_its_own_overlap_guard(tmp_path, menu):
    """The wrapper must not generate a manifest its own guard rejects."""
    fa, analysis = _analysis(tmp_path)
    for mode in (0, 1, 3, 4, 5):
        entries = [(s, d, m) for s, d, _c, m in fa.generate_manifest(analysis, mode)]
        assert menu._manifest_source_overlaps(entries) == [], f"mode {mode}"


# ---------------------------------------------------------------------------
# #244 — overlap guard vs. flat modes
# ---------------------------------------------------------------------------

def test_244_nested_sources_in_flat_modes_are_not_overlaps(menu):
    root = str(Path("/data").resolve())
    sub = str((Path("/data") / "sub1").resolve())
    for mode in (0, 1):
        assert menu._manifest_source_overlaps(
            [(root, root, mode), (sub, sub, mode)]) == [], f"mode {mode}"


def test_244_nested_sources_in_recursive_modes_are_overlaps(menu):
    root = str(Path("/data").resolve())
    sub = str((Path("/data") / "sub1").resolve())
    for mode in (2, 3, 4, 5, 6, 7, 8):
        assert len(menu._manifest_source_overlaps(
            [(root, root, mode), (sub, sub, mode)])) == 1, f"mode {mode}"


def test_244_outer_entry_mode_is_the_one_that_decides(menu):
    """Only the CONTAINING entry's mode can pull the inner one's files in."""
    root = str(Path("/data").resolve())
    sub = str((Path("/data") / "sub1").resolve())
    # outer flat, inner recursive -> the outer never reaches the inner's files
    assert menu._manifest_source_overlaps([(root, root, 0), (sub, sub, 3)]) == []
    # outer recursive -> it does
    assert len(menu._manifest_source_overlaps([(root, root, 3), (sub, sub, 0)])) == 1


def test_244_identical_sources_always_overlap(menu):
    root = str(Path("/data").resolve())
    assert len(menu._manifest_source_overlaps([(root, root, 0), (root, root, 0)])) == 1


def test_244_legacy_entries_without_a_mode_fail_closed(menu):
    root = str(Path("/data").resolve())
    sub = str((Path("/data") / "sub1").resolve())
    assert len(menu._manifest_source_overlaps(
        [(root, root, None), (sub, sub, None)])) == 1


# ---------------------------------------------------------------------------
# #245 — stored numbers are coerced, not just validated
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [("8.0", 8), (8.0, 8), ("8", 8), (8, 8)])
def test_245_session_int_accepts_everything_the_validator_blesses(raw, expected):
    session = {"last_output_mode": raw}
    assert wp._session_number_error(session) is None
    assert wp._session_int(session, "last_output_mode", -1) == expected


def test_245_blank_and_missing_fall_back():
    assert wp._session_int({}, "last_workers", 4) == 4
    assert wp._session_int({"last_workers": ""}, "last_workers", 4) == 4
    assert wp._session_int({"last_workers": None}, "last_workers", 4) == 4


def test_245_manifest_mode_detected_whatever_the_json_type():
    """mode 99 stored as a number (or "99.0") must still read as a manifest."""
    for raw in ("99", 99, 99.0, "99.0"):
        assert wp._session_int({"last_output_mode": raw}, "last_output_mode", 0) == 99


def test_245_describe_session_labels_a_numeric_99_as_a_manifest():
    line = wp.InteractiveMenu._describe_session({
        "last_output_mode": 99, "last_manifest_path": r"C:\m\manifest_1.csv",
        "last_origin_format": "tiff", "last_dest_format": "jxl",
    })
    assert "manifest: manifest_1.csv" in line
    assert "mode 99" not in line


# ---------------------------------------------------------------------------
# #246 / #247 — the summary contract
# ---------------------------------------------------------------------------

def _summary(script: str, args: list, cwd: Path):
    r = subprocess.run(
        [sys.executable, str(REPO / script), str(cwd)] + args + ["--summary-json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    line = next((l for l in (r.stdout or "").splitlines()
                 if l.startswith(tr.SUMMARY_PREFIX)), None)
    assert line is not None, f"{script} emitted no summary line (rc={r.returncode})"
    return json.loads(line[len(tr.SUMMARY_PREFIX):])


@pytest.mark.parametrize("args", [
    ["--mode", "0", "--force-transcode"],   # cmd_transcode
    ["--mode", "0", "--force-convert"],     # cmd_convert
    ["--mode", "0"],                        # cmd_auto
])
def test_246_transcoder_dry_run_is_reported_as_a_dry_run(tmp_path, args):
    from PIL import Image
    Image.new("RGB", (16, 16), (10, 20, 30)).save(tmp_path / "a.jpg", quality=90)

    payload = _summary("jxl_jpeg_transcoder.py", args + ["--dry-run"], tmp_path)
    assert payload["dry_run"] is True
    assert payload["ok"] == 1          # the PLANNED output, not a row of zeros
    assert payload["log"], "the child log path must reach the wrapper"


@pytest.mark.parametrize("script,args", [
    ("jxl_tiff_encoder.py", ["--mode", "0"]),
    ("jxl_tiff_decoder.py", ["--mode", "0"]),
    ("jxl_jpeg_transcoder.py", ["--mode", "0", "--force-transcode"]),
    ("jxl_jpeg_transcoder.py", ["--mode", "0", "--force-convert"]),
    ("jxl_jpeg_transcoder.py", ["--mode", "0"]),
])
def test_247_empty_input_still_reports_a_summary(tmp_path, script, args):
    """An empty folder is an empty run, not a crashed child."""
    payload = _summary(script, args, tmp_path)
    assert payload["ok"] == 0
    assert payload["errors"] == 0
    # The transcoder used to fall through to the untouched default, whose log
    # path is "" — the entry then vanished from the recap's "Child logs" list.
    assert payload["log"], "the child log path must reach the wrapper"


# ---------------------------------------------------------------------------
# #248 — the Auto Mode report names real folders
# ---------------------------------------------------------------------------

def test_248_report_has_no_unformatted_placeholders(tmp_path):
    # >10 files across several subfolders so the analyzer lands on mode 3,
    # whose label is the one that carried the placeholder.
    for i in range(6):
        _tiff(tmp_path / "sub1" / f"a{i}.tif")
        _tiff(tmp_path / "sub2" / f"b{i}.tif")
    fa = wp.FolderAnalyzer(tmp_path, "tiff", "jxl", "_EXPORT")
    analysis = fa.analyze()
    assert analysis["recommended_mode"] == 3
    report = fa.format_report(analysis)

    assert "{dest}" not in report
    assert "jxl_files" not in report
    assert "JXL_16bits" in report


@pytest.mark.parametrize("origin,dest,mode1,mode3", [
    ("tiff", "jxl", "converted_jxl", "JXL_16bits"),
    ("jxl", "tiff", "converted_tiff", "TIFF_16bits"),
    ("jxl", "jpeg", "recovered_jpeg", "recovered_jpeg"),
])
def test_248_report_names_match_the_scripts(tmp_path, origin, dest, mode1, mode3):
    fa = wp.FolderAnalyzer(tmp_path, origin, dest, "_EXPORT")
    report = fa.format_report({
        "total_files": 1, "folder_count": 1, "has_export_marker": False,
        "export_marker_paths": [], "has_subfolders": False, "subfolders": [],
        "file_distribution": {}, "recommended_mode": 3, "confidence": "high",
        "reasoning": [],
    })
    assert mode3 in report
    assert wp._dest_folder_names(origin, dest) == (mode1, mode3)


# ---------------------------------------------------------------------------
# #249 / #250 — wizard text
# ---------------------------------------------------------------------------

def test_249_multipage_help_announces_split_as_the_default():
    src = (REPO / "jxl_photo.py").read_text(encoding="utf-8")
    assert "ignore   = encode page 0 only, drop the rest (default)" not in src
    assert "ignore    = encode page 0 only, drop the rest (default)" not in src
    assert src.count("thumbnails per the next question (default)") == 2


def test_249_plain_text_fallbacks_use_the_default_not_ignore():
    """A typo must not select the one mode that discards image data."""
    src = (REPO / "jxl_photo.py").read_text(encoding="utf-8")
    assert 'multipage_mode = mp_input if mp_input in ["ignore", "skip", "split", "split_all"] else mp_default' in src
    assert 'thumbnail_mode = tm_input if tm_input in ["exclude", "include"] else tm_default' in src
    assert 'else "ignore"' not in src


def test_250_mode8_is_not_labelled_delete_in_the_summary():
    src = (REPO / "jxl_photo.py").read_text(encoding="utf-8")
    assert '8: "DELETE originals"' not in src
    assert '8: "In-place recursive"' in src


# ---------------------------------------------------------------------------
# #251 — transcoder argument ranges
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("args,needle", [
    (["--distance", "99"], "--distance must be between 0 and 15"),
    (["--distance", "-1"], "--distance must be between 0 and 15"),
    (["--quality", "500"], "--quality must be between 1 and 100"),
    (["--quality", "0"], "--quality must be between 1 and 100"),
])
def test_251_out_of_range_values_are_refused_up_front(tmp_path, args, needle):
    r = subprocess.run(
        [sys.executable, str(REPO / "jxl_jpeg_transcoder.py"), str(tmp_path)] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 2
    assert needle in (r.stdout + r.stderr)


def test_251_valid_ranges_still_pass(tmp_path):
    r = subprocess.run(
        [sys.executable, str(REPO / "jxl_jpeg_transcoder.py"), str(tmp_path),
         "--distance", "0", "--quality", "100", "--dry-run"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0


def test_251_sub_threshold_distance_warns_about_the_cjxl_clamp(tmp_path, caplog):
    from PIL import Image
    Image.new("RGB", (16, 16), (10, 20, 30)).save(tmp_path / "a.jpg", quality=90)
    r = subprocess.run(
        [sys.executable, str(REPO / "jxl_jpeg_transcoder.py"), str(tmp_path),
         "--force-convert", "--distance", "0.01", "--dry-run"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert "behaves exactly like 0.05" in r.stdout
