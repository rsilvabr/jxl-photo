#!/usr/bin/env python3
"""Regression tests for the 2026-09-21 audit (round 38).

One test per bug of `bug_report_260921.md`. The first block runs with REAL
codecs (skipped when cjxl/djxl/exiftool are absent) because the audit found
them against real files; the rest are unit tests over the fixed code paths.

Bug index:
   1  encoder: Lab/YCbCr TIFF archived as RGB
   2  decoder: --matrix dropped alpha and then deleted the source
   3  decoder: a source named *_pageN re-decoded under a different name
   4  recompressor: --provenance path|content was inert
   5  encoder: grayscale page with its own RGB ICC failed every encode
   6  decoder: --none ignored a failed EXIF copy (delete gate stayed open)
   7  recompressor: unreadable group markers failed OPEN in the delete gate
   8  wrapper: unattended manifest preset with in-place JXL->JXL rows
   9  encoder: single output from page > 0 lost its reconstruction markers
  10  recompressor/wrapper: empty export marker raised IndexError
  11  recompressor: --workers < 1 crashed with a raw traceback
  12  recompressor: --jbrd-policy convert had no per-file log
  13  recompressor: dry run counted refused sources in the plan
  14  recompressor: delete counters used labels the wrapper never showed
  15  recompressor: single-file modes 4/5 warned "output outside input tree"
  16a recompressor: --on-regeneration help said gen >= 1 (code: >= 2)
  16b recompressor/encoder: _reconcile_gen read only the FIRST gen= token
  17  wrapper: mode-0 file row sent the file as an output positional
  18  wrapper: collision guard skipped file-Source rows
  19  wrapper: Auto Mode advertised wrong JXL->JXL folders / dead q=
  20  decoder: native.icc was reused across pages of a group
  21  decoder: dry-run delete count / ignored-thumbnail KEEP count
  22  transcoder: --repair-jbrd aborted on one bad file, no summary JSON
  23  transcoder: an unhashable file crashed the delete gate
  24  transcoder: dry-run reported errors=0 for provenance refusals
  25  transcoder: --to-srgb silently ignored on the transcode route
  26  transcoder: --force-transcode on a non-JPEG failed deep in the worker
  27  transcoder: sRGB ICC profile written non-atomically
"""

import shutil
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_jpeg_transcoder as tr
import jxl_photo as wp
import jxl_recompressor as rec
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent

_HAS_CODECS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
requires_codecs = pytest.mark.skipif(
    not _HAS_CODECS, reason="cjxl/djxl/exiftool not on PATH")


def _jxl_stub(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)


def _run(script, *args, timeout=600):
    return subprocess.run(
        [sys.executable, str(REPO / script), *map(str, args)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout)


class _FakeRun:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def menu(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


def _session(**over):
    s = {n: None for n in wp.ToolConfig.__dataclass_fields__ if n.startswith("last_")}
    s.update({
        "last_output_mode": "0", "last_workers": 4, "last_effort": 7,
        "last_distance": 0.1, "last_origin_format": "jxl",
        "last_dest_format": "jpeg", "last_conversion_type": "jxl_to_jpeg_force",
        "last_advanced_options": {"overwrite": False, "sync": True},
    })
    s.update(over)
    return s


def _encode_tiff(path: Path, value: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, np.full((16, 16, 3), value, dtype=np.uint8),
                     photometric="rgb")


# ===========================================================================
# 1 — encoder rejects Lab/YCbCr instead of archiving them as RGB
# ===========================================================================

@requires_codecs
def test_cielab_tiff_is_rejected(tmp_path):
    lab = np.zeros((8, 8, 3), dtype=np.uint8)
    lab[..., 0], lab[..., 1], lab[..., 2] = 200, 128, 128
    p = tmp_path / "lab.tif"
    tifffile.imwrite(p, lab, photometric="cielab")

    _k, status, msg, _e = enc.convert_one(p, tmp_path / "lab.jxl", tmp_path / "lab.jxl")
    assert status == "error", "a Lab TIFF must not encode as if it were RGB"
    assert "CIELAB" in msg
    assert not (tmp_path / "lab.jxl").exists()


# ===========================================================================
# 2 — matrix mode never deletes sources (it cannot prove alpha survived)
# ===========================================================================

@requires_codecs
def test_matrix_delete_source_keeps_the_jxl(tmp_path):
    from PIL import Image
    Image.new("RGBA", (8, 8), (10, 200, 30, 128)).save(tmp_path / "a.png")
    r = subprocess.run(["cjxl", str(tmp_path / "a.png"), str(tmp_path / "a.jxl"),
                        "-d", "0", "-e", "1"], capture_output=True, timeout=120)
    assert r.returncode == 0

    res = _run("jxl_tiff_decoder.py", tmp_path, "--matrix", "--mode", "1",
               "--delete-source", "--delete-confirm-off")
    assert res.returncode == 0, res.stdout + res.stderr
    assert (tmp_path / "a.jxl").exists(), \
        "matrix mode dropped alpha and deleted the only source"


# ===========================================================================
# 3 — a `_pageN` that belongs to the SOURCE NAME is not a generated suffix
# ===========================================================================

def test_numeric_suffix_equal_to_a_real_page_is_stripped():
    anchor = Path("scan_page1.jxl")
    entries = [(anchor, 1, False, False, 0, False, None)]
    assert dec._group_naming_path(anchor, entries, True).name == "scan.jxl"


def test_page_number_inside_the_source_name_is_kept():
    anchor = Path("scan_page2.jxl")
    entries = [(anchor, 0, False, False, 0, False, None)]
    assert dec._group_naming_path(anchor, entries, True).name == "scan_page2.jxl"


@requires_codecs
def test_source_named_page_n_round_trips_under_the_same_name(tmp_path):
    src = tmp_path / "scan_page2.tif"
    with tifffile.TiffWriter(src) as tw:
        tw.write(np.zeros((8, 8, 3), np.uint8), photometric="rgb")
        tw.write(np.full((8, 8, 3), 9, np.uint8), photometric="rgb")
    r = _run("jxl_tiff_encoder.py", src, "--mode", "0",
             "--multipage-mode", "split", "--delete-confirm-off")
    assert r.returncode == 0, r.stdout + r.stderr
    r = _run("jxl_tiff_decoder.py", tmp_path, "--mode", "1",
             "--overwrite", "--delete-confirm-off")
    assert r.returncode == 0, r.stdout + r.stderr
    out = sorted(p.name for p in (tmp_path / "converted_tiff").glob("*.tif*"))
    assert out == ["scan_page2.tif"], f"wrong reconstruction name: {out}"


# ===========================================================================
# 4 — recompressor honours --provenance path|content
# ===========================================================================

def test_provenance_path_rejects_a_content_only_match():
    out_info = {"src": None, "srcsum": "SAME"}
    src_info = {"src": "x", "srcsum": "SAME"}
    assert rec._markers_match(out_info, src_info, "content") is True
    assert rec._markers_match(out_info, src_info, "path") is False
    assert rec._markers_match(out_info, src_info) is False   # path is the default


# ===========================================================================
# 5 — gray page with its own RGB ICC encodes (kept in XMP only)
# ===========================================================================

@requires_codecs
def test_gray_page_with_own_rgb_icc_encodes(tmp_path):
    from PIL import ImageCms
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    gray = np.linspace(0, 65535, 64, dtype=np.uint16).reshape(8, 8)
    p = tmp_path / "gray_own_icc.tif"
    tifffile.imwrite(p, gray, photometric="minisblack",
                     extratags=[(34675, "B", len(srgb), srgb, False)])

    _k, status, _m, _e = enc.convert_one(p, tmp_path / "g.jxl", tmp_path / "g.jxl")
    assert status in ("ok", "overwrite"), \
        "a grayscale page carrying an RGB ICC must not fail the encode"
    assert (tmp_path / "g.jxl").exists()


# ===========================================================================
# 6 — decoder --none: failed EXIF copy is an error (delete gate stays closed)
# ===========================================================================

def test_none_mode_exif_failure_is_an_error(tmp_path, monkeypatch):
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    out = tmp_path / "photo.tif"

    dec.setup_logger()
    monkeypatch.setattr(dec, "OVERWRITE", True)
    monkeypatch.setattr(dec, "ADD_JPEG_PREVIEW", False)
    monkeypatch.setattr(dec, "_verify_tiff_integrity", lambda p: True)
    monkeypatch.setattr(dec, "decode_jxl_to_numpy",
                        lambda *a, **k: (np.zeros((8, 8, 3), np.uint16),
                                         None, "none", "none"))
    monkeypatch.setattr(dec, "_run_exiftool_argfile",
                        lambda *a, **k: _FakeRun(rc=1))

    main, status, _reason = dec.convert_multipage_jxl_group(
        src, [(src, 0, False, False, 0, False, None)], out, out)

    assert status == "error", "a failed EXIF copy must not pass as OK"
    # Round 39 (audit item 21): the meta_ok verdict moved BEFORE the promotion,
    # so a metadata-failed output is no longer promoted to the final name —
    # promoting it gave it a fresh mtime and the next smart-sync run would
    # skip-then-delete the JXL holding the only copy of that metadata. The
    # fresh output is discarded; the SOURCE is what stays.
    assert not out.exists(), "a metadata-failed output must not be promoted"
    assert not list(tmp_path.glob("*_photo.tif*")), "no temp leftover beside the final"
    assert src.exists()


# ===========================================================================
# 7 — unreadable group markers block every deletion (fail closed)
# ===========================================================================

def test_unreadable_group_markers_block_all_deletions(tmp_path, monkeypatch):
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    final = tmp_path / "out" / "a.jxl"
    _jxl_stub(final)

    rec.setup_logger()
    rec._reset_abort()
    monkeypatch.setattr(rec, "DELETE_SOURCE", True)
    monkeypatch.setattr(rec, "_verify_jxl_integrity", lambda p: True)
    monkeypatch.setattr(rec, "_read_mpg_markers",
                        lambda paths: ({str(p): "group-1" for p in paths}, False))

    it = {"src": src, "final": final, "in_place": False,
          "action": "convert", "src_d": 1.0}
    rec._delete_gate([it], {str(src): ("ok", str(final))}, {str(src)})

    assert src.exists(), "marker read failure must not delete anything"
    assert rec._delete_stats["kept"] == 1


# ===========================================================================
# 8 — unattended manifest preset with in-place JXL->JXL rows is refused
# ===========================================================================

def test_unattended_manifest_in_place_recompress_is_refused(
        menu, tmp_path, monkeypatch, capsys):
    f = tmp_path / "a.jxl"
    _jxl_stub(f)
    wf = {
        'manifest_entries': [(str(f), str(f), 0)],
        'origin_format': 'jxl', 'dest_format': 'jxl',
        'workers': 2, 'advanced_options': {}, 'dry_run': False,
        'mode_config': {}, 'expert_flags': '', 'unattended': True,
    }
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp, "console", None)
    launched = []
    monkeypatch.setattr(
        wp.InteractiveMenu, "_stream_child",
        lambda self, cmd, idle_timeout=3600: (launched.append(cmd), 0)[1])

    assert menu._execute_manifest_workflow(wf, {}) is False
    assert launched == [], "the child was launched despite the unattended refusal"
    assert "unattended" in capsys.readouterr().out.lower()


# ===========================================================================
# 9 — a single output from page > 0 keeps its page marker
# ===========================================================================

@requires_codecs
def test_single_output_from_page_one_keeps_page_marker(tmp_path):
    src = tmp_path / "scan_thumbfirst.tif"
    with tifffile.TiffWriter(src) as tw:
        tw.write(np.zeros((8, 8, 3), np.uint8), photometric="rgb", subfiletype=1)
        tw.write(np.full((16, 16, 3), 9, np.uint8), photometric="rgb")
    r = _run("jxl_tiff_encoder.py", src, "--mode", "0",
             "--multipage-mode", "split", "--thumbnail-mode", "exclude",
             "--delete-confirm-off")
    assert r.returncode == 0, r.stdout + r.stderr
    rel = subprocess.run(["exiftool", "-s", "-s", "-s", "-XMP-dc:Relation",
                          str(tmp_path / "scan_thumbfirst.jxl")],
                         capture_output=True, text=True).stdout
    assert "jxlphoto-page:1" in rel, \
        f"single output from page 1 lost its page marker: {rel}"


# ===========================================================================
# 10 — an empty export marker is ignored, never an IndexError
# ===========================================================================

def test_empty_export_marker_matches_nothing_in_every_copy():
    for mod in (enc, dec, tr, rec, wp):
        assert mod._marker_matches("abc", "") is False


def test_recompressor_empty_export_marker_does_not_crash(tmp_path):
    _jxl_stub(tmp_path / "a.jxl")
    r = _run("jxl_recompressor.py", tmp_path, "--mode", "6",
             "--export-marker", "", "--dry-run")
    assert "IndexError" not in (r.stdout + r.stderr)
    assert "string index out of range" not in (r.stdout + r.stderr)


# ===========================================================================
# 11 — recompressor rejects --workers < 1 up front
# ===========================================================================

def test_recompressor_rejects_zero_workers(tmp_path):
    _jxl_stub(tmp_path / "a.jxl")
    r = _run("jxl_recompressor.py", tmp_path / "a.jxl", "--mode", "0",
             "--workers", "0", "--distance", "1.0", "--delete-confirm-off")
    assert r.returncode != 0
    assert "workers" in (r.stdout + r.stderr).lower()
    assert "max_workers" not in (r.stdout + r.stderr)


# ===========================================================================
# 12 — --jbrd-policy convert logs every file it destroys
# ===========================================================================

@requires_codecs
def test_jbrd_convert_policy_logs_per_file(tmp_path):
    from PIL import Image
    Image.new("RGB", (8, 8), (1, 2, 3)).save(tmp_path / "p.jpg", quality=90)
    r = _run("jxl_jpeg_transcoder.py", tmp_path / "p.jpg", "--mode", "0",
             "--delete-confirm-off")
    assert r.returncode == 0, r.stdout + r.stderr

    r = _run("jxl_recompressor.py", tmp_path, "--mode", "0", "--distance", "1.0",
             "--jbrd-policy", "convert", "--dry-run")
    out = r.stdout + r.stderr
    assert "jbrd JPEG recovery will be DESTROYED" in out, \
        "the README promises a per-file log for --jbrd-policy convert"


# ===========================================================================
# 13 — dry run does not count refused sources in the plan
# ===========================================================================

@requires_codecs
def test_dry_run_refusals_do_not_count_as_deletions(tmp_path):
    enc_in = tmp_path / "in"
    out = tmp_path / "out"
    _encode_tiff(tmp_path / "t1.tif", 10)
    _encode_tiff(tmp_path / "t2.tif", 200)
    r = _run("jxl_tiff_encoder.py", tmp_path / "t1.tif", "--mode", "0",
             "--delete-confirm-off")
    assert r.returncode == 0, r.stdout + r.stderr
    r = _run("jxl_tiff_encoder.py", tmp_path / "t2.tif", "--mode", "0",
             "--delete-confirm-off")
    assert r.returncode == 0, r.stdout + r.stderr
    enc_in.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(tmp_path / "t1.jxl", enc_in / "photo.jxl")
    shutil.copy2(tmp_path / "t2.jxl", out / "photo.jxl")

    r = _run("jxl_recompressor.py", enc_in, str(out), "--mode", "2",
             "--distance", "1.0", "--delete-source", "--dry-run")
    text = r.stdout + r.stderr
    assert "would REFUSE" in text
    assert "would delete 0 source(s)" in text, \
        "the refused source was still counted as a planned deletion"


# ===========================================================================
# 14 — delete counts reach the wrapper with its own labels
# ===========================================================================

@requires_codecs
def test_delete_summary_uses_wrapper_labels(tmp_path):
    _encode_tiff(tmp_path / "t1.tif", 10)
    r = _run("jxl_tiff_encoder.py", tmp_path / "t1.tif", "--mode", "0",
             "--delete-confirm-off")
    assert r.returncode == 0, r.stdout + r.stderr
    src = tmp_path / "t1.jxl"
    r = _run("jxl_recompressor.py", src, "--mode", "1", "--distance", "1.0",
             "--delete-source", "--delete-confirm-off", "--summary-json")
    assert r.returncode == 0, r.stdout + r.stderr
    assert '"Sources deleted"' in (r.stdout + r.stderr), \
        "the wrapper pops this exact label for the red deletion panel"


# ===========================================================================
# 15 — single-file modes 4/5 do not warn about a legitimate sibling output
# ===========================================================================

def test_single_file_mode5_does_not_warn_outside_tree(tmp_path):
    _jxl_stub(tmp_path / "y.jxl")
    r = _run("jxl_recompressor.py", tmp_path / "y.jxl", "--mode", "5",
             "--dry-run")
    assert "Output outside input tree" not in (r.stdout + r.stderr)


# ===========================================================================
# 16a/16b — help text and gen token parsing
# ===========================================================================

def test_on_regeneration_help_reports_the_real_threshold():
    r = _run("jxl_recompressor.py", "--help")
    assert "gen >= 2" in r.stdout
    assert "gen >= 1" not in r.stdout


def test_reconcile_gen_takes_the_largest_token():
    text = "gen=1 | cjxl d=0.1 e=7 | gen=5 | cjxl d=1.0 e=7"
    assert rec._reconcile_gen(text)[0] == 5
    assert enc._reconcile_gen(text)[0] == 5


# ===========================================================================
# 17 — a file row never sends its own path as an output positional
# ===========================================================================

def test_mode0_file_row_does_not_emit_the_file_as_output(menu, tmp_path):
    f = tmp_path / "a.jxl"
    _jxl_stub(f)
    assert wp._recompress_entry_in_place(str(f), str(f), 0) is True

    cmd = menu._build_manifest_entry_cmd(
        str(REPO / "jxl_recompressor.py"), str(f), str(f), 0,
        "jxl", "jxl", 4, {}, {})
    assert cmd.count(str(f)) == 1, f"the file was sent twice as source/output: {cmd}"


# ===========================================================================
# 18 — the collision guard sees file-Source rows
# ===========================================================================

def test_collision_guard_resolves_file_sources(menu, tmp_path):
    root = tmp_path / "root"
    sub = root / "sub"
    out = tmp_path / "out"
    sub.mkdir(parents=True)
    out.mkdir()
    _jxl_stub(root / "a.jxl")
    _jxl_stub(sub / "a.jxl")

    entries = [(str(root / "a.jxl"), str(out), 2),
               (str(sub), str(out), 2)]
    collisions = menu._manifest_output_collisions(
        entries, {".jxl"}, origin="jxl", dest="jxl")
    assert collisions, "two rows writing out/a.jxl were not reported"


# ===========================================================================
# 19 — Auto Mode folder names / dead q= knob
# ===========================================================================

def test_auto_mode_mode5_sibling_matches_every_child(tmp_path):
    # The oracle is each child's own mode-5 constant: a wrapper preview that
    # disagrees points at a folder the run never creates.
    cases = [
        ("tiff", "jxl", enc.JXL_FOLDER_NAME),
        ("jxl", "tiff", dec.TIFF_FOLDER_NAME),
        ("jpeg", "jxl", tr.JXL_SIBLING_FOLDER),
        ("jxl", "jpeg", tr.JPEG_SIBLING_FOLDER),
        ("jxl", "png", tr.JPEG_SIBLING_FOLDER),
        ("jxl", "jxl", rec.JXL_FOLDER_NAME),
    ]
    for origin, dest, expected in cases:
        an = wp.FolderAnalyzer(tmp_path, origin, dest)
        maps = an.compute_folder_mappings({"file_distribution": {"sub": 1}}, 5)
        assert maps == [(str(tmp_path / "sub"), str(tmp_path / expected), 1)], \
            f"mode 5 {origin}->{dest} pointed at {maps} instead of {expected}"


def test_auto_mode_mode4_suffix_matches_every_child(tmp_path):
    cases = [
        ("tiff", "jxl", enc.JXL_SUFFIX_REPLACE),
        ("jxl", "tiff", dec.TIFF_SUFFIX_REPLACE),
        ("jpeg", "jxl", tr.JXL_SUFFIX_REPLACE),
        ("jxl", "jpeg", tr.JPEG_SUFFIX_REPLACE_DEC),
        ("jxl", "png", tr.JPEG_SUFFIX_REPLACE_DEC),
        ("jxl", "jxl", rec.JXL_SUFFIX_REPLACE),
    ]
    for origin, dest, expected in cases:
        an = wp.FolderAnalyzer(tmp_path, origin, dest)
        maps = an.compute_folder_mappings({"file_distribution": {"sub": 1}}, 4)
        assert maps == [(str(tmp_path / "sub"), str(tmp_path / f"sub_{expected}"), 1)], \
            f"mode 4 {origin}->{dest} pointed at {maps} instead of sub_{expected}"


def test_jpeg_preset_does_not_advertise_dead_quality(menu):
    s = _session(last_origin_format="jpeg", last_dest_format="jxl",
                 last_conversion_type="transcode_lossless", last_quality=95)
    assert "q=95" not in menu._describe_session(s)


def test_jxl_to_jpeg_preset_still_advertises_quality(menu):
    s = _session(last_origin_format="jxl", last_dest_format="jpeg",
                 last_conversion_type="jxl_to_jpeg_force", last_quality=92)
    assert "q=92" in menu._describe_session(s)


# ===========================================================================
# 20 — native ICC extraction never reuses the previous page's file
# ===========================================================================

def test_native_icc_does_not_reuse_a_stale_file(tmp_path, monkeypatch):
    def fake(args_lines, timeout=60):
        out = Path(args_lines[1])
        src = Path(args_lines[-1])
        if out.exists():
            return _FakeRun(rc=1)          # exiftool -o refuses to overwrite
        payload = b"A" * 36 + b"acsp" + (src.name.encode() * 30)
        out.write_bytes(payload)
        return _FakeRun(rc=0)

    monkeypatch.setattr(dec, "_run_exiftool_argfile", fake)
    j1, j2 = tmp_path / "one.jxl", tmp_path / "two.jxl"
    _jxl_stub(j1)
    _jxl_stub(j2)

    first = dec.extract_icc_native(j1, tmp_path)
    second = dec.extract_icc_native(j2, tmp_path)

    assert first == b"A" * 36 + b"acsp" + (b"one.jxl" * 30)
    assert second == b"A" * 36 + b"acsp" + (b"two.jxl" * 30), \
        "page 2 was served page 1's stale native ICC"


# ===========================================================================
# 21 — ignored thumbnail sources count as KEPT
# ===========================================================================

def test_ignored_thumbnail_sources_count_as_kept(tmp_path, monkeypatch):
    src = tmp_path / "photo.jxl"
    thumb = tmp_path / "photo_thumbnail.jxl"
    _jxl_stub(src)
    _jxl_stub(thumb)
    final = tmp_path / "out" / "photo.tif"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"tiff-placeholder")

    dec.setup_logger()
    dec._delete_stats.update({"deleted": 0, "deleted_archived": 0, "kept": 0})
    monkeypatch.setattr(dec, "DELETE_SOURCE", True)
    monkeypatch.setattr(dec, "DELETE_SKIPPED", False)
    monkeypatch.setattr(dec, "TEMP2_DIR", None)
    monkeypatch.setattr(dec, "USE_MATRIX_MODE", False)
    monkeypatch.setattr(dec, "_verify_tiff_integrity", lambda p: True)
    monkeypatch.setattr(dec, "convert_multipage_jxl_group",
                        lambda m, e, w, f, *a: (str(m), "ok", str(f)))

    task = {"type": "multi", "main_jxl": src,
            "entries": [(src, 0, False, False, 0, False, None)],
            "ignored_thumbs": [(thumb, 1, True, False, 0, False, None)],
            "final_tiff": final}
    dec.process_group([task], 1)

    assert thumb.exists(), "an ignored thumbnail must never be deleted"
    assert not src.exists()
    assert dec._delete_stats["kept"] == 1, \
        "kept-by-a-gate must include ignored thumbnail sources"


# ===========================================================================
# 22 — repair survives one broken file and emits summary JSON
# ===========================================================================

def test_repair_survives_a_raising_file(tmp_path, monkeypatch, capsys):
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    monkeypatch.setattr(tr, "has_jbrd_box", lambda p: True)
    monkeypatch.setattr(tr, "_jxl_reconstruct_ok", lambda p: False)

    def boom(p, dry):
        raise OSError("locked by another process")
    monkeypatch.setattr(tr, "_repair_one_jbrd", boom)

    args = types.SimpleNamespace(input=src, dry_run=False, summary_json=True)
    rc = tr.cmd_repair_jbrd(args)
    assert rc == 1
    assert tr.SUMMARY_PREFIX in capsys.readouterr().out, \
        "a repair run must emit its --summary-json payload"


# ===========================================================================
# 23 — an unhashable file refuses the pair, never crashes the run
# ===========================================================================

def test_provenance_filter_refuses_when_hashing_fails(tmp_path, monkeypatch):
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    out = tmp_path / "a.jpg"
    out.write_bytes(b"jpeg")

    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "PROVENANCE_CHECK", "path")
    monkeypatch.setattr(tr, "_run_collapses_structure", lambda *a: True)
    monkeypatch.setattr(tr, "read_md5_db", lambda p: "stored")
    monkeypatch.setattr(tr, "md5_of_file",
                        lambda p: (_ for _ in ()).throw(PermissionError("locked")))

    kept, refused = tr._provenance_filter([(src, out)], 2, decode_lossless=True)
    assert not kept
    assert refused and "could not hash" in refused[0][2]


# ===========================================================================
# 24 — no delete extras without --delete-source
# ===========================================================================

def test_delete_extras_empty_without_delete_source(monkeypatch):
    monkeypatch.setattr(tr, "DELETE_SOURCE", False)
    assert tr._delete_extras() == {}


# ===========================================================================
# 25 — --to-srgb on the lossless transcode route says it is ignored
# ===========================================================================

def test_to_srgb_warns_on_the_lossless_route(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"\xff\xd8\xff\xd9")
    r = _run("jxl_jpeg_transcoder.py", p, "--to-srgb", "--dry-run")
    assert "no effect on the lossless transcode path" in (r.stdout + r.stderr)


def test_to_srgb_does_not_warn_for_directory_auto(tmp_path):
    # cmd_auto converts the JXL pairs WITH the profile, so the flag is not dead
    # there and the pure-transcode warning must not appear.
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    r = _run("jxl_jpeg_transcoder.py", tmp_path, "--to-srgb", "--dry-run")
    assert "no effect on the lossless transcode path" not in (r.stdout + r.stderr)


# ===========================================================================
# 26 — --force-transcode rejects a non-JPEG before starting
# ===========================================================================

def test_force_transcode_rejects_non_jpeg_up_front(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG")
    r = _run("jxl_jpeg_transcoder.py", p, "--force-transcode")
    assert r.returncode == 2
    assert "requires a JPEG input" in (r.stdout + r.stderr)


# ===========================================================================
# 27 — the shared sRGB profile is written atomically and validated
# ===========================================================================

def test_srgb_icc_profile_is_repaired_and_has_no_partial_leftovers(
        tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "TEMP_DIR", str(tmp_path))
    monkeypatch.setattr(tr, "_srgb_icc_cache", None)
    (tmp_path / "jxl_photo_sRGB.icc").write_bytes(b"trunc")   # crashed write

    path = tr._get_srgb_icc_path()
    assert path is not None
    assert Path(path).stat().st_size > 128, \
        "a truncated cached profile must be rewritten, not trusted"
    assert not list(tmp_path.glob("*.part"))
