#!/usr/bin/env python3
"""Regressions for the round-25 audit fixes (bugs #252-#259).

Every one was reproduced against the shipped code before the fix:

  * #252 — the transcoder was the only script whose --mode had no `choices`,
    so --mode 9 sailed through argparse and died inside
    resolve_output_transcode with a raw ValueError traceback, after the log
    header and "Files found: N" had already been printed.
  * #253 — --multipage-mode skip dropped the embedded thumbnail of every
    single-real-page TIFF without touching a counter: no summary line, no
    `extras` for the wrapper, and no "(embedded thumbnail page not encoded)"
    note on the mode-8 delete line. That is the shape of every Capture One
    export and of the film scans, so on a real library it was silent on every
    file.
  * #254 — the Auto Mode report printed len() of the FIVE-item display sample
    as the subfolder count ("Subfolders: 5" for a tree with 40), and the
    mode-4 heuristic decided on subfolders[:3] of that same sample.
  * #255 — the plain-text Step-7 manifest summary never printed `extra_info`,
    so a terminal without `rich` asked for YES on a manifest that deletes
    originals without saying so. Neither branch showed the multi-page line.
  * #256 — the decoder and the transcoder still called their worker pool once
    per output FOLDER, so a folder with fewer files than --workers could never
    fill it and every folder boundary drained it (the encoder fixed exactly
    this and measured 10s vs 33s on eight real 45 MP files).
  * #257 — cmd_auto folded "reconvert" into ok and reported overwritten=0, so
    the wrapper's manifest recap showed "ovw 0" for a pass that reconverted
    the whole folder.
  * #258 — a marked multi-page group arriving with pages missing decoded to a
    valid single-page TIFF, and mode 8 deleted the JXL for it: no downstream
    check can notice, because the file that was written is complete.
  * #259 — the three manifest guards ran on the RAW manifest mode while the
    run used the DETECTED one, so a legacy manifest (no Mode cell) had its
    collisions checked against a flat Destination write that modes 6/7 never
    perform.
"""

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


def _multipage_tiff(path: Path, thumbs: int = 1, real: int = 1):
    """A TIFF shaped like a real export: N real page(s) + N reduced thumbnail(s)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tifffile.TiffWriter(str(path)) as w:
        for i in range(real):
            w.write(np.full((16, 16, 3), 1000 + i, np.uint16), photometric="rgb",
                    metadata=None, software="")
        for i in range(thumbs):
            w.write(np.full((4, 4, 3), 10 + i, np.uint8), photometric="rgb",
                    subfiletype=1, metadata=None, software="")


# ---------------------------------------------------------------------------
# #252 — the transcoder accepted --mode out of range
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_mode", ["9", "42", "-1"])
def test_252_transcoder_rejects_out_of_range_mode(tmp_path, bad_mode):
    """argparse must refuse it. Pre-fix this reached resolve_output_transcode
    and raised a bare ValueError traceback mid-run."""
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9")
    r = subprocess.run(
        [sys.executable, str(REPO / "jxl_jpeg_transcoder.py"), str(tmp_path),
         "--force-transcode", "--mode", bad_mode, "--dry-run"],
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 2, r.stdout + r.stderr
    assert "Traceback" not in (r.stdout + r.stderr)
    assert "invalid choice" in (r.stdout + r.stderr).lower()


def test_252_transcoder_still_accepts_every_valid_mode():
    """Do not over-fix: 0-8 must all remain reachable."""
    parser = tr.build_parser()
    for mode in range(9):
        args = parser.parse_args(["x", "--mode", str(mode)])
        assert args.mode == mode
    # default stays None so main() can pick the per-command default
    assert parser.parse_args(["x"]).mode is None


# ---------------------------------------------------------------------------
# #253 — --multipage-mode skip dropped thumbnails silently
# ---------------------------------------------------------------------------

def test_253_skip_mode_records_dropped_thumbnails(tmp_path, monkeypatch):
    """1 real page + 1 thumbnail under `skip`: the drop must be counted."""
    src = tmp_path / "photo.tif"
    _multipage_tiff(src, thumbs=1, real=1)

    monkeypatch.setattr(enc, "MULTIPAGE_TIFF_MODE", "skip")
    monkeypatch.setattr(enc, "_thumbnails_dropped", {"files": 0, "pages": 0})
    monkeypatch.setattr(enc, "_discarded_thumb_sources", set())

    items = enc.convert_multipage(src, tmp_path, mode=0)

    assert len(items) == 1, "the single real page is still encoded"
    assert enc._thumbnails_dropped == {"files": 1, "pages": 1}
    import os
    assert os.path.normcase(str(src)) in enc._discarded_thumb_sources, (
        "mode 8 needs this to say the preview page is not coming back")


def test_253_skip_summary_names_the_right_setting(tmp_path, monkeypatch, caplog):
    """--thumbnail-mode include does nothing in skip mode; do not advertise it."""
    monkeypatch.setattr(enc, "MULTIPAGE_TIFF_MODE", "skip")
    monkeypatch.setattr(enc, "_thumbnails_dropped", {"files": 1, "pages": 1})
    monkeypatch.setattr(enc, "_multipage_ignored", {"files": 0, "pages": 0})
    monkeypatch.setattr(enc, "_discard_warned", {"count": 0, "suppressed": 0})
    enc.logger.propagate = True
    with caplog.at_level("INFO", logger="jxl_convert"):
        enc._log_discard_summary()
    text = caplog.text
    assert "--multipage-mode skip" in text
    assert "--thumbnail-mode include" not in text


def test_253_split_mode_still_records_thumbnails(tmp_path, monkeypatch):
    """Do not over-fix: the split path that already worked must keep working."""
    src = tmp_path / "photo.tif"
    _multipage_tiff(src, thumbs=1, real=1)

    monkeypatch.setattr(enc, "MULTIPAGE_TIFF_MODE", "split")
    monkeypatch.setattr(enc, "THUMBNAIL_MODE", "exclude")
    monkeypatch.setattr(enc, "_thumbnails_dropped", {"files": 0, "pages": 0})
    monkeypatch.setattr(enc, "_discarded_thumb_sources", set())

    enc.convert_multipage(src, tmp_path, mode=0)
    assert enc._thumbnails_dropped == {"files": 1, "pages": 1}


def test_253_skip_with_no_thumbnails_counts_nothing(tmp_path, monkeypatch):
    """A plain single-page TIFF must not be reported as a thumbnail drop."""
    src = tmp_path / "plain.tif"
    tifffile.imwrite(str(src), np.zeros((8, 8, 3), np.uint16), photometric="rgb")

    monkeypatch.setattr(enc, "MULTIPAGE_TIFF_MODE", "skip")
    monkeypatch.setattr(enc, "_thumbnails_dropped", {"files": 0, "pages": 0})
    monkeypatch.setattr(enc, "_discarded_thumb_sources", set())

    enc.convert_multipage(src, tmp_path, mode=0)
    assert enc._thumbnails_dropped == {"files": 0, "pages": 0}


# ---------------------------------------------------------------------------
# #254 — Auto Mode reported the display sample as the subfolder count
# ---------------------------------------------------------------------------

def test_254_report_shows_the_real_subfolder_count(tmp_path):
    for i in range(12):
        d = tmp_path / f"shoot_{i:02d}"
        d.mkdir()
        tifffile.imwrite(str(d / "a.tif"), np.zeros((8, 8, 3), np.uint16),
                         photometric="rgb")

    analyzer = wp.FolderAnalyzer(tmp_path, "tiff", "jxl")
    analysis = analyzer.analyze()
    assert analysis["subfolder_count"] == 12

    report = analyzer.format_report(analysis)
    assert "Subfolders: 12" in report, "pre-fix this read 'Subfolders: 5'"
    assert "... and 9 more" in report, "pre-fix this read '... and 2 more'"


def test_254_mode4_heuristic_sees_past_the_display_sample(tmp_path):
    """A '*_TIFF' folder sorted past position 5 must still recommend mode 4."""
    for i in range(8):
        d = tmp_path / f"aaa_{i:02d}"
        d.mkdir()
        tifffile.imwrite(str(d / "a.tif"), np.zeros((8, 8, 3), np.uint16),
                         photometric="rgb")
    late = tmp_path / "zzz_shoot_TIFF"
    late.mkdir()
    tifffile.imwrite(str(late / "a.tif"), np.zeros((8, 8, 3), np.uint16),
                     photometric="rgb")

    analysis = wp.FolderAnalyzer(tmp_path, "tiff", "jxl").analyze()
    assert analysis["recommended_mode"] == 4


# ---------------------------------------------------------------------------
# #255 — plain-text Step 7 (manifest) hid the config line
# ---------------------------------------------------------------------------

def _manifest_workflow(**over):
    wf = {
        "mode": 99,
        "manifest_path": "M.csv",
        "manifest_entries": [("A", "A", 8)],
        "workers": 4,
        "origin_format": "tiff",
        "dest_format": "jxl",
        "staging": "",
        "advanced_options": {"delete_source": True},
        "conversion_type": "jxl_tiff_encoder",
        "effort": 7,
        "distance": 0.1,
        "input_dir": "A",
    }
    wf.update(over)
    return wf


def test_255_plain_text_manifest_summary_shows_delete_source(menu, monkeypatch, capsys):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr("builtins.input", lambda *a: "NO")

    assert menu._wizard_confirm(_manifest_workflow()) is False
    out = capsys.readouterr().out
    assert "DELETE SOURCE: ON (!)" in out, (
        "pre-fix the plain-text manifest branch never printed extra_info")


def test_255_plain_text_manifest_summary_shows_dry_run_and_expert(menu, monkeypatch, capsys):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr("builtins.input", lambda *a: "NO")

    wf = _manifest_workflow(dry_run=True, expert_flags="--effort 9",
                            advanced_options={})
    menu._wizard_confirm(wf)
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "Expert: Yes" in out


def test_255_manifest_summary_shows_the_multipage_line(menu, monkeypatch, capsys):
    """An `ignore` manifest discards the IR page of every scan — say so."""
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr("builtins.input", lambda *a: "NO")

    wf = _manifest_workflow(advanced_options={"multipage_mode": "ignore"})
    menu._wizard_confirm(wf)
    out = capsys.readouterr().out
    assert "Multi-page TIFF:" in out
    assert "DISCARDED" in out


def test_255_manifest_summary_omits_multipage_for_other_directions(menu, monkeypatch, capsys):
    """Do not over-fix: JXL->TIFF has no multi-page ENCODE setting."""
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr("builtins.input", lambda *a: "NO")

    wf = _manifest_workflow(origin_format="jxl", dest_format="tiff",
                            conversion_type="jxl_tiff_decoder")
    menu._wizard_confirm(wf)
    assert "Multi-page TIFF:" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# #256 — one worker pool per RUN, not per output folder
# ---------------------------------------------------------------------------

def _jxl_stub(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)


def test_256_decoder_uses_one_pool_for_every_folder(tmp_path, monkeypatch):
    """Mode 3 puts each source folder's output in its own TIFF_16bits/.

    Pre-fix main() called process_group once per output folder; the pool
    therefore drained three times and never held more than one file.
    """
    for i in range(3):
        _jxl_stub(tmp_path / f"shoot{i}" / "a.jxl")

    calls = []

    def fake_process_group(tasks, workers, mode, target_icc=None):
        calls.append(list(tasks))
        return [(str(t["main_jxl"]), "ok", str(t["final_tiff"])) for t in tasks]

    monkeypatch.setattr(dec, "process_group", fake_process_group)
    monkeypatch.setattr(dec, "_check_external_tools", lambda dry_run=False: None)
    monkeypatch.setattr(dec, "_warn_if_libjxl_too_old", lambda *a, **k: None)
    monkeypatch.setattr(dec, "_read_multipage_markers_batch",
                        lambda jxls: {str(j): {"group": None, "inherited": False,
                                               "subfiletype": 0, "grayscale": False,
                                               "depth": None, "page": None,
                                               "thumb": False} for j in jxls})
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_decoder.py", str(tmp_path), "--mode", "3",
                         "--workers", "4"])
    dec.main()

    assert len(calls) == 1, f"expected ONE pool, got {len(calls)} (one per folder)"
    assert len(calls[0]) == 3, "every task must be submitted to that one pool"


def test_256_transcoder_uses_one_pool_for_every_folder(tmp_path, monkeypatch):
    for i in range(3):
        d = tmp_path / f"shoot{i}"
        d.mkdir()
        (d / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9")

    calls = []

    def fake_pool(pairs, workers, decode, verify, mode, reconvert_val, smart,
                  effort=7):
        calls.append(list(pairs))
        return [(str(s), "ok", str(o), None) for s, o in pairs]

    monkeypatch.setattr(tr, "process_group_transcode", fake_pool)
    args = tr.build_parser().parse_args(
        [str(tmp_path), "--force-transcode", "--mode", "3", "--workers", "4"])
    tr.cmd_transcode(args)

    assert len(calls) == 1, f"expected ONE pool, got {len(calls)} (one per folder)"
    assert len(calls[0]) == 3


def test_256_staging_still_flushes_per_destination_folder(tmp_path, monkeypatch):
    """Do not over-fix: staging must NOT hold the whole batch to the end."""
    staging = tmp_path / "stg"
    staging.mkdir()
    finals = [tmp_path / f"d{i}" / "a.jxl" for i in range(3)]
    pairs = [(tmp_path / f"s{i}.jpg", finals[i]) for i in range(3)]
    for s, _ in pairs:
        s.write_bytes(b"\xff\xd8\xff\xd9")

    moved_at = []

    def fake_encode(src, write, final, *a, **k):
        write.parent.mkdir(parents=True, exist_ok=True)
        write.write_bytes(b"\x00" * 16)
        return (str(src), "ok", str(final), None)

    real_move = tr.shutil.move

    def spy_move(a, b):
        moved_at.append(Path(b).parent.name)
        return real_move(a, b)

    monkeypatch.setattr(tr, "TEMP2_DIR", str(staging))
    monkeypatch.setattr(tr, "encode_one_transcode", fake_encode)
    monkeypatch.setattr(tr.shutil, "move", spy_move)
    tr.setup_logger()
    tr.process_group_transcode(pairs, 3, decode=False, verify=False, mode=0,
                               reconvert_val=False, smart=False)

    assert sorted(moved_at) == ["d0", "d1", "d2"]
    assert not list(staging.glob("*.jxl")), "staging must be empty at the end"


# ---------------------------------------------------------------------------
# #257 — cmd_auto always reported overwritten=0
# ---------------------------------------------------------------------------

def test_257_auto_mode_counts_reconverts(tmp_path, monkeypatch):
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (tmp_path / "b.jpg").write_bytes(b"\xff\xd8\xff\xd9")

    def fake_pool(pairs, workers, decode, verify, mode, reconvert_val, smart,
                  effort=7):
        return [(str(s), "reconvert", str(o), None) for s, o in pairs]

    recorded = {}
    monkeypatch.setattr(tr, "process_group_transcode", fake_pool)
    monkeypatch.setattr(tr, "record_summary",
                        lambda **kw: recorded.update(kw))
    args = tr.build_parser().parse_args([str(tmp_path), "--mode", "0"])
    tr.cmd_auto(args)

    assert recorded["ok"] == 2
    assert recorded["overwritten"] == 2, "pre-fix cmd_auto hardcoded 0"


# ---------------------------------------------------------------------------
# #258 — an incomplete multi-page group must not lose its sources
# ---------------------------------------------------------------------------

def _marked(page, group="abc123"):
    return {"group": group, "inherited": False, "subfiletype": 0,
            "grayscale": False, "depth": None, "page": page, "thumb": False}


def test_258_incomplete_group_is_flagged(tmp_path, monkeypatch):
    """Only page 2 of a split is present: warn and mark the group."""
    lone = tmp_path / "scan_page2.jxl"
    _jxl_stub(lone)
    monkeypatch.setattr(dec, "_read_multipage_markers_batch",
                        lambda jxls: {str(lone): _marked(2)})

    groups = dec.collect_multipage_groups([lone])
    import os
    assert os.path.normcase(str(lone)) in dec._incomplete_groups
    assert len(groups[lone]) == 1


def test_258_incomplete_group_blocks_mode8_delete(tmp_path, monkeypatch):
    lone = tmp_path / "scan_page2.jxl"
    _jxl_stub(lone)
    final = tmp_path / "scan_page2.tif"
    tifffile.imwrite(str(final), np.zeros((8, 8, 3), np.uint16), photometric="rgb")

    dec.setup_logger()
    monkeypatch.setattr(dec, "DELETE_SOURCE", True)
    monkeypatch.setattr(dec, "TEMP2_DIR", None)
    monkeypatch.setattr(dec, "_verify_tiff_integrity", lambda p: True)
    monkeypatch.setattr(dec, "_read_multipage_markers_batch",
                        lambda jxls: {str(lone): _marked(2)})
    dec.collect_multipage_groups([lone])          # populates _incomplete_groups

    task = {"type": "multi", "main_jxl": lone,
            "entries": [(lone, 2, False, False, 0, False, None)],
            "ignored_thumbs": [], "final_tiff": final}
    monkeypatch.setattr(dec, "convert_multipage_jxl_group",
                        lambda *a, **k: (str(lone), "ok", str(final)))
    dec.process_group([task], 1, 8)

    assert lone.exists(), "an incomplete group must never have its source deleted"


def test_258_complete_group_still_deletes(tmp_path, monkeypatch):
    """Do not over-fix: a whole group present is deleted as before."""
    p0, p2 = tmp_path / "scan.jxl", tmp_path / "scan_page2.jxl"
    _jxl_stub(p0)
    _jxl_stub(p2)
    final = tmp_path / "scan.tif"
    tifffile.imwrite(str(final), np.zeros((8, 8, 3), np.uint16), photometric="rgb")

    dec.setup_logger()
    monkeypatch.setattr(dec, "DELETE_SOURCE", True)
    monkeypatch.setattr(dec, "TEMP2_DIR", None)
    monkeypatch.setattr(dec, "_verify_tiff_integrity", lambda p: True)
    monkeypatch.setattr(dec, "_read_multipage_markers_batch",
                        lambda jxls: {str(p0): _marked(0), str(p2): _marked(2)})
    dec.collect_multipage_groups([p0, p2])

    task = {"type": "multi", "main_jxl": p0,
            "entries": [(p0, 0, False, False, 0, False, None),
                        (p2, 2, False, False, 0, False, None)],
            "ignored_thumbs": [], "final_tiff": final}
    monkeypatch.setattr(dec, "convert_multipage_jxl_group",
                        lambda *a, **k: (str(p0), "ok", str(final)))
    dec.process_group([task], 1, 8)

    assert not p0.exists() and not p2.exists()


def test_258_lone_page0_is_not_flagged(tmp_path, monkeypatch):
    """A marked file that IS page 0 is the ordinary single-output case."""
    solo = tmp_path / "scan.jxl"
    _jxl_stub(solo)
    monkeypatch.setattr(dec, "_read_multipage_markers_batch",
                        lambda jxls: {str(solo): _marked(0)})
    dec.collect_multipage_groups([solo])
    assert dec._incomplete_groups == set()


# ---------------------------------------------------------------------------
# #259 — manifest guards ran on the raw mode, the run on the detected one
# ---------------------------------------------------------------------------

def test_259_guards_receive_the_detected_mode(tmp_path, monkeypatch, menu):
    """A legacy manifest (no Mode cell) whose entries detect to mode 6."""
    root = tmp_path / "Fotos"
    (root / "2024_EXPORT" / "TIFF16").mkdir(parents=True)
    tifffile.imwrite(str(root / "2024_EXPORT" / "TIFF16" / "a.tif"),
                     np.zeros((8, 8, 3), np.uint16), photometric="rgb")

    seen = {}
    monkeypatch.setattr(
        wp.InteractiveMenu, "_manifest_source_overlaps",
        staticmethod(lambda entries: seen.setdefault("overlaps", list(entries)) and []))
    monkeypatch.setattr(
        wp.InteractiveMenu, "_manifest_needs_collision_scan",
        lambda self, entries, marker: bool(seen.setdefault("scan", list(entries))) and False)
    monkeypatch.setattr(wp.InteractiveMenu, "_run_subprocess", lambda self, cmd: 0)
    monkeypatch.setattr(wp.InteractiveMenu, "_render_manifest_summary",
                        lambda self, *a, **k: None)
    menu._last_child_summary = None

    workflow = {
        "manifest_entries": [(str(root), str(root / "2024_EXPORT"), None)],
        "origin_format": "tiff", "dest_format": "jxl", "workers": 2,
        "advanced_options": {}, "dry_run": True,
    }
    menu.config = type("C", (), {"config": type("C2", (), {"export_marker": "_EXPORT"})()})()
    menu._execute_manifest_workflow(workflow, {})

    # detect_mode_for_entry maps (Fotos -> Fotos/2024_EXPORT) to mode 6
    assert seen["overlaps"][0][2] == 6, "guards must see 6, not the raw None"
    assert seen["scan"][0][2] == 6


def test_259_explicit_modes_are_untouched(menu):
    """Do not over-fix: an explicit Mode cell always wins over detection."""
    analyzer = wp.FolderAnalyzer(Path("."), "tiff", "jxl", "_EXPORT")
    for mode in range(9):
        assert analyzer.detect_mode_for_entry("A", "B", original_mode=mode) == mode
