#!/usr/bin/env python3
"""Regression tests for the audit fixes in this round.

Critical:
  C1  transcoder: no XMP markers inside jbrd JXLs (they break
      djxl --reconstruct_jpeg) + the delete gate proves reconstruction
      before unlinking a JPEG + --repair-jbrd strips the bad markers
  C2  recompressor: --delete-skipped without --delete-source is inert
  C3  recompressor: a failed conversion removes its partial output
  C4  decoder: smart-sync refuses to overwrite a TIFF that carries no
      jxlphoto-src marker (the original master)

High:
  A1  the regeneration guard fires at gen >= 2, not >= 1
  A3  the lineage survives a field change (union of dc:Description and
      Software, max of both gen= tokens)
  A2  the wrapper names the recompressor's real output folders and gates
      in-place recompression like a delete

Medium:
  M2  multi-page groups delete all-or-nothing (jxlphoto-mpg)
  M3  mode 0/2 refuse an output folder that equals the input folder
  M4  the recompressor reorders JXL boxes after its exiftool restamp

Low:
  B2  a caption merely containing "cjxl d=1 e=7" is not a generation
  B4  _classify no longer says "FIRST lossy generation" for a d=0-tailed
      chain with gen >= 1
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_jpeg_transcoder as tr
import jxl_recompressor as rec
import jxl_tiff_decoder as dec
import jxl_photo as wp
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent


def _jxl_stub(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)


# ---------------------------------------------------------------------------
# C1 — markers never enter a jbrd container; the delete gate proves recovery
# ---------------------------------------------------------------------------

def test_encode_transcode_skips_markers_on_jbrd_jxl(tmp_path, monkeypatch):
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8\xff\xd9")
    out = tmp_path / "photo.jxl"

    def _fake_run(cmd, **kw):
        # cjxl "wrote" the output
        Path(cmd[2]).write_bytes(b"JXL container bytes")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    writes = []
    monkeypatch.setattr(tr, "subprocess",
                        type("S", (), {"run": staticmethod(_fake_run)}))
    monkeypatch.setattr(tr, "has_jbrd_box", lambda p: True)   # the lossless case
    monkeypatch.setattr(tr, "_run_exiftool_argfile",
                        lambda lines, **kw: writes.append(lines))
    monkeypatch.setattr(tr, "reorder_jxl_boxes", lambda p: None)
    monkeypatch.setattr(tr, "_verify_file_integrity", lambda p: True)
    monkeypatch.setattr(tr, "_aborted", lambda: None)
    monkeypatch.setattr(tr, "STORE_MD5", False)
    monkeypatch.setattr(tr, "_counter", {"done": 0, "total": 1})

    status = tr.encode_one_transcode(src, out, out, False, 7, False)
    assert status[1] == "ok"
    assert writes == [], f"a jbrd JXL got XMP writes: {writes}"


def test_encode_to_jxl_still_marks_jxl_without_jbrd(tmp_path, monkeypatch):
    src = tmp_path / "photo.png"
    src.write_bytes(b"PNG")
    out = tmp_path / "photo.jxl"

    def _fake_run(cmd, **kw):
        Path(cmd[2]).write_bytes(b"JXL container bytes")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    writes = []
    monkeypatch.setattr(tr, "subprocess",
                        type("S", (), {"run": staticmethod(_fake_run)}))
    monkeypatch.setattr(tr, "has_jbrd_box", lambda p: False)  # lossy: no jbrd
    monkeypatch.setattr(tr, "_run_exiftool_argfile",
                        lambda lines, **kw: writes.append(lines))
    monkeypatch.setattr(tr, "_copy_metadata", lambda s, d: None)
    monkeypatch.setattr(tr, "reorder_jxl_boxes", lambda p: None)
    monkeypatch.setattr(tr, "_verify_file_integrity", lambda p: True)
    monkeypatch.setattr(tr, "_aborted", lambda: None)
    monkeypatch.setattr(tr, "STORE_MD5", False)
    monkeypatch.setattr(tr, "_counter", {"done": 0, "total": 1})

    status = tr.encode_to_jxl(src, out, out, 7, 1.0, False, False)
    assert status[1] == "ok"
    assert any(any("jxlphoto-src:" in str(a) for a in w) for w in writes), \
        "a non-jbrd JXL lost its provenance marker"


def test_delete_gate_keeps_source_when_reconstruction_fails(tmp_path, monkeypatch):
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8\xff\xd9")
    final = tmp_path / "photo.jxl"
    final.write_bytes(b"jxl")

    tr.setup_logger()
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "TEMP2_DIR", None)
    monkeypatch.setattr(tr, "STORE_MD5", False)
    monkeypatch.setattr(tr, "_verify_file_integrity", lambda p: True)
    monkeypatch.setattr(tr, "has_jbrd_box", lambda p: True)
    monkeypatch.setattr(tr, "_tool_at_least", lambda exe, a, b: True)
    # THE point of the gate: reconstruction does NOT reproduce the source.
    monkeypatch.setattr(tr, "_jxl_reconstructs_to", lambda j, s: False)
    monkeypatch.setattr(tr, "encode_one_transcode",
                        lambda s, w, f, *a, **k: (str(s), "ok", str(f), None))

    tr.process_group_transcode([(src, final)], 1, decode=False, verify=False,
                               mode=0, reconvert_val=False, smart=False)
    assert src.exists(), "source deleted although reconstruction fails"


def test_delete_gate_deletes_when_reconstruction_proves(tmp_path, monkeypatch):
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8\xff\xd9")
    final = tmp_path / "photo.jxl"
    final.write_bytes(b"jxl")

    tr.setup_logger()
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "TEMP2_DIR", None)
    monkeypatch.setattr(tr, "STORE_MD5", False)
    monkeypatch.setattr(tr, "_verify_file_integrity", lambda p: True)
    monkeypatch.setattr(tr, "has_jbrd_box", lambda p: True)
    monkeypatch.setattr(tr, "_tool_at_least", lambda exe, a, b: True)
    monkeypatch.setattr(tr, "_jxl_reconstructs_to", lambda j, s: True)
    monkeypatch.setattr(tr, "encode_one_transcode",
                        lambda s, w, f, *a, **k: (str(s), "ok", str(f), None))

    tr.process_group_transcode([(src, final)], 1, decode=False, verify=False,
                               mode=0, reconvert_val=False, smart=False)
    assert not src.exists(), "source kept although reconstruction proved"


def test_repair_strips_only_our_markers(tmp_path, monkeypatch):
    jxl = tmp_path / "photo.jxl"
    _jxl_stub(jxl)

    state = {"stripped": False}
    monkeypatch.setattr(tr, "_read_source_markers_batch",
                        lambda paths: {str(p): ({"src": None, "srcsum": None}
                                                if state["stripped"] else
                                                {"src": "abc123", "srcsum": "def456"})
                                       for p in paths})
    calls = []

    def _exif(lines, **kw):
        calls.append(lines)
        state["stripped"] = True
        return subprocess.CompletedProcess(lines, 0, "", "")
    monkeypatch.setattr(tr, "_run_exiftool_argfile", _exif)
    reordered = []
    monkeypatch.setattr(tr, "reorder_jxl_boxes", lambda p: reordered.append(p))

    info = {"src": "abc123", "srcsum": "def456"}
    assert tr._strip_provenance_markers(jxl, info) is None
    joined = "\n".join("\n".join(map(str, c)) for c in calls)
    assert "-XMP-dc:Relation-=jxlphoto-src:abc123" in joined
    assert "-XMP-dc:Relation-=jxlphoto-srcsum:def456" in joined
    assert reordered == [jxl], "boxes were not reordered after the strip"

    calls.clear()
    assert tr._strip_provenance_markers(jxl, {"src": None, "srcsum": None}) \
        == "no toolkit markers to remove"
    assert calls == [], "a marker-less file must not be touched"


# ---------------------------------------------------------------------------
# C2 — --delete-skipped without --delete-source deletes nothing
# ---------------------------------------------------------------------------

def test_recompressor_delete_gate_requires_delete_source(tmp_path, monkeypatch):
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    final = tmp_path / "out" / "photo.jxl"
    final.parent.mkdir()
    _jxl_stub(final)

    rec.setup_logger()
    monkeypatch.setattr(rec, "DELETE_SOURCE", False)
    monkeypatch.setattr(rec, "DELETE_SKIPPED", True)   # armed alone
    monkeypatch.setattr(rec, "_verify_jxl_integrity", lambda p: True)
    monkeypatch.setattr(rec, "_read_mpg_markers", lambda paths: {str(p): None for p in paths})

    items = [{"src": src, "final": final, "in_place": False, "action": "copy",
              "src_d": 1.0}]
    rec._delete_gate(items, {str(src): ("skipped", str(final))}, set())
    assert src.exists(), "--delete-skipped alone deleted a source"


# ---------------------------------------------------------------------------
# C3 — a failed conversion removes its partial output
# ---------------------------------------------------------------------------

def test_convert_one_removes_partial_output_on_failure(tmp_path, monkeypatch):
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    final = tmp_path / "out" / "photo.jxl"

    def _failing_codec(cmd, **kw):
        # the codec truncates: it creates the file, then reports failure
        Path(cmd[2]).write_bytes(b"partial")
        return subprocess.CompletedProcess(cmd, 1, b"", b"boom")

    rec.setup_logger()
    monkeypatch.setattr(rec, "_aborted", lambda: None)
    monkeypatch.setattr(rec, "_would_skip", lambda a, b: False)
    monkeypatch.setattr(rec, "subprocess", type("S", (), {"run": staticmethod(_failing_codec), "TimeoutExpired": subprocess.TimeoutExpired}))
    monkeypatch.setattr(rec, "_counter", {"done": 0, "total": 1})

    status = rec.convert_one(src, final, final, "convert", False, "", "", 1.0)
    assert status[1] == "error"
    assert not final.exists(), "the partial output survived the failure"


def test_convert_one_removes_uuid_temp_in_place_on_failure(tmp_path, monkeypatch):
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    write_path = tmp_path / f"{'ab' * 16}_photo.jxl"

    def _failing_codec(cmd, **kw):
        Path(cmd[2]).write_bytes(b"partial")
        return subprocess.CompletedProcess(cmd, 1, b"", b"boom")

    rec.setup_logger()
    monkeypatch.setattr(rec, "_aborted", lambda: None)
    monkeypatch.setattr(rec, "_would_skip", lambda a, b: False)
    monkeypatch.setattr(rec, "subprocess", type("S", (), {"run": staticmethod(_failing_codec), "TimeoutExpired": subprocess.TimeoutExpired}))
    monkeypatch.setattr(rec, "_counter", {"done": 0, "total": 1})

    status = rec.convert_one(src, write_path, src, "convert", True, "", "", 1.0)
    assert status[1] == "error"
    leftovers = list(tmp_path.glob("*_photo.jxl"))
    assert leftovers == [], f"in-place temp survived: {leftovers}"


# ---------------------------------------------------------------------------
# C4 — smart-sync refuses to overwrite an original master TIFF
# ---------------------------------------------------------------------------

def test_decoder_refuses_to_overwrite_a_master_tiff(tmp_path, monkeypatch):
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    final = tmp_path / "a.tif"
    final.write_bytes(b"the original master")

    dec.setup_logger()
    monkeypatch.setattr(dec, "_aborted", lambda: None)
    monkeypatch.setattr(dec, "OVERWRITE", "smart")
    # The TIFF on disk is NOT one of ours (no jxlphoto-src marker):
    monkeypatch.setattr(dec, "_decode_output_is_ours", lambda p: False)
    # JXL newer than the TIFF:
    _old = final.stat().st_mtime
    import os as _os
    _os.utime(final, (_old - 100, _old - 100))

    entries = [(src, 0, False, False, 0, False, None)]
    result = dec.convert_multipage_jxl_group(src, entries, tmp_path / "w.tif", final)
    # "refused", never "skipped": a skip would admit the JXL to
    # --delete-skipped on the strength of an unrelated TIFF (round 36).
    assert result[1] == "refused"
    assert final.read_bytes() == b"the original master"


def test_decoder_redecodes_its_own_output(tmp_path, monkeypatch):
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    final = tmp_path / "a.tif"
    final.write_bytes(b"our earlier decode")

    dec.setup_logger()
    monkeypatch.setattr(dec, "_aborted", lambda: None)
    monkeypatch.setattr(dec, "OVERWRITE", "smart")
    monkeypatch.setattr(dec, "_decode_output_is_ours", lambda p: True)
    import os as _os
    _old = final.stat().st_mtime
    _os.utime(final, (_old - 100, _old - 100))

    entries = [(src, 0, False, False, 0, False, None)]
    # The refusal happens BEFORE any decoding: with ours=True the function
    # proceeds past the gate; make the heavy work abort immediately to
    # prove the gate let it through.
    monkeypatch.setattr(dec, "_signal_abort", lambda reason: None)
    monkeypatch.setattr(dec, "_aborted", lambda: "stop")
    result = dec.convert_multipage_jxl_group(src, entries, tmp_path / "w.tif", final)
    assert result[1] == "aborted", result   # passed the gate, stopped later


# ---------------------------------------------------------------------------
# A1 — regeneration guard threshold
# ---------------------------------------------------------------------------

def test_regeneration_guard_fires_at_gen_two(monkeypatch):
    monkeypatch.setattr(rec, "ON_REGENERATION", "ask")
    assert rec._regeneration_action(1, 1.0) is None, \
        "gen=1 (every encoder output) must not fire the guard"
    assert rec._regeneration_action(2, 1.0) == "ask"
    assert rec._regeneration_action(2, 0.0) is None, "lossless adds no generation"


def test_classify_not_first_generation_for_d0_tail_with_gen():
    cat, reason = rec._classify((0.0, 7), 1.0, 7, gen=2)
    assert cat == "ok"
    assert "FIRST lossy" not in reason
    assert "gen=2" in reason
    cat, reason = rec._classify((0.0, 7), 1.0, 7, gen=0)
    assert "FIRST lossy" in reason


# ---------------------------------------------------------------------------
# A3 — lineage union across fields
# ---------------------------------------------------------------------------

def test_merge_lineage_blocks_unions_and_dedupes():
    entries, stored = rec._merge_lineage_blocks(
        "My caption | gen=1 | cjxl d=0.1 e=7",
        "gen=2 | cjxl d=0.1 e=7 | cjxl d=1.0 e=7")
    assert entries == [("0.1", "7"), ("1.0", "7")]
    assert stored == 2


def test_restamp_merges_when_the_record_moves_fields(monkeypatch):
    # The record lived in Software; this restamp writes dc:Description.
    monkeypatch.setattr(rec, "ENCODE_TAG_MODE", "xmp")
    monkeypatch.setattr(rec, "CJXL_DISTANCE", 2.0)
    monkeypatch.setattr(rec, "CJXL_EFFORT", 7)
    monkeypatch.setattr(rec, "_gen_divergence_logged", False)
    lines = rec._restamp_args("A caption", "gen=1 | cjxl d=0.1 e=7")
    desc = next(l for l in lines if l.startswith("-XMP-dc:Description="))
    assert "A caption" in desc
    assert "cjxl d=0.1 e=7" in desc, "the old chain was dropped instead of migrated"
    assert "cjxl d=2.0 e=7" in desc
    assert "gen=2" in desc


# ---------------------------------------------------------------------------
# B2 — a caption is not a generation
# ---------------------------------------------------------------------------

def test_caption_mentioning_cjxl_is_not_counted():
    assert rec._reconcile_gen("developed with cjxl d=1 e=7 settings") == (0, 0, 0)
    new_text, stored, counted, _ = rec._append_encode_entry(
        "developed with cjxl d=1 e=7 settings", 1.0, 7)
    assert counted == 0
    assert new_text.count("cjxl d=1 e=7") == 1, "caption text absorbed into the chain"
    assert new_text.startswith("developed with cjxl d=1 e=7 settings | gen=1")


# ---------------------------------------------------------------------------
# M2 — multi-page groups delete all-or-nothing
# ---------------------------------------------------------------------------

def _mpg_run(tmp_path, monkeypatch, *, p1_ok, p2_ok):
    srcs = []
    finals = []
    for name in ("p0.jxl", "p1.jxl"):
        s = tmp_path / name
        _jxl_stub(s)
        f = tmp_path / "out" / name
        f.parent.mkdir(exist_ok=True)
        _jxl_stub(f)
        srcs.append(s)
        finals.append(f)

    rec.setup_logger()
    monkeypatch.setattr(rec, "DELETE_SOURCE", True)
    monkeypatch.setattr(rec, "DELETE_SKIPPED", False)
    monkeypatch.setattr(rec, "_read_mpg_markers",
                        lambda paths: {str(p): "group-1" for p in paths})
    monkeypatch.setattr(rec, "_verify_jxl_integrity",
                        lambda p: p1_ok if p.name == "p0.jxl" else p2_ok)
    items = [{"src": s, "final": f, "in_place": False, "action": "convert",
              "src_d": 1.0} for s, f in zip(srcs, finals)]
    results = {str(s): ("ok", str(f)) for s, f in zip(srcs, finals)}
    rec._delete_gate(items, results, {str(s) for s in srcs})
    return srcs


def test_multipage_group_kept_when_one_page_fails(tmp_path, monkeypatch):
    srcs = _mpg_run(tmp_path, monkeypatch, p1_ok=True, p2_ok=False)
    assert all(s.exists() for s in srcs), "part of the group was deleted"


def test_multipage_group_deleted_when_all_pages_pass(tmp_path, monkeypatch):
    srcs = _mpg_run(tmp_path, monkeypatch, p1_ok=True, p2_ok=True)
    assert not any(s.exists() for s in srcs), "the whole group should be gone"


# ---------------------------------------------------------------------------
# M3 — output == input is refused (mode 2 collapses the tree)
# ---------------------------------------------------------------------------

def test_recompressor_refuses_output_equal_to_input(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    _jxl_stub(root / "a.jxl")
    r = subprocess.run(
        [sys.executable, str(REPO / "jxl_recompressor.py"), str(root), str(root),
         "--mode", "2", "--dry-run"],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 2
    assert "equals the input folder" in (r.stdout + r.stderr)


# ---------------------------------------------------------------------------
# M4 — the recompressor reorders boxes after the restamp
# ---------------------------------------------------------------------------

def test_convert_one_reorders_boxes_after_restamp(tmp_path, monkeypatch):
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    out = tmp_path / "out" / "photo.jxl"

    calls = []

    def _fake_run(cmd, **kw):
        Path(cmd[2]).write_bytes(b"JXL container bytes")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    def _fake_exif(lines, **kw):
        calls.append("exiftool")
        return subprocess.CompletedProcess([], 0, "", "")

    rec.setup_logger()
    monkeypatch.setattr(rec, "_aborted", lambda: None)
    monkeypatch.setattr(rec, "_would_skip", lambda a, b: False)
    monkeypatch.setattr(rec, "KEEP_SMALLER", False)
    monkeypatch.setattr(rec, "VERIFY_ROUNDTRIP", False)
    monkeypatch.setattr(rec, "CJXL_DISTANCE", 1.0)
    monkeypatch.setattr(rec, "subprocess", type("S", (), {"run": staticmethod(_fake_run)}))
    monkeypatch.setattr(rec, "_run_exiftool_argfile", _fake_exif)
    monkeypatch.setattr(rec, "_verify_jxl_integrity", lambda p: True)
    monkeypatch.setattr(rec, "_counter", {"done": 0, "total": 1})

    def _reorder(p):
        calls.append("reorder")

    monkeypatch.setattr(rec, "reorder_jxl_boxes", _reorder)
    status = rec.convert_one(src, out, out, "convert", False, "", "", 1.0)
    assert status[1] == "ok"
    assert calls == ["exiftool", "reorder"], calls


# ---------------------------------------------------------------------------
# A2 — the wrapper knows the recompressor's folders and gates in-place
# ---------------------------------------------------------------------------

def test_wrapper_names_recompressor_folders():
    assert wp._dest_folder_names('jxl', 'jxl') == ('recompressed_jxl', 'JXL_recompressed')


def test_wrapper_gates_in_place_recompress(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    menu_calls = []
    monkeypatch.setattr(menu, "_stream_child",
                        lambda cmd, idle_timeout=3600:
                            (menu_calls.append(list(map(str, cmd))), 0)[1])

    asked = []
    monkeypatch.setattr(menu, "_confirm_in_place_replace",
                        lambda: (asked.append(True), True)[1])
    monkeypatch.setattr(menu, "_confirm_archive_mode",
                        lambda: (asked.append("delete-mode"), True)[1])

    wf = {"mode": 8, "origin_format": "jxl", "dest_format": "jxl",
          "input_dir": str(tmp_path), "workers": 2, "effort": 7, "distance": 1.0,
          "staging": "", "advanced_options": {}}
    menu.execute_workflow(wf, {k: True for k in
                               ("cjxl", "djxl", "exiftool", "magick", "tifffile",
                                "pillow", "imagecodecs")})
    assert asked == [True], "in-place recompression was not gated as destructive"
    assert "--delete-confirm-off" in menu_calls[0], \
        "the child would re-ask mid-stream on an invisible prompt"
    assert "--delete-source" not in menu_calls[0]


def test_wrapper_asks_for_a_recompress_preset(tmp_path, monkeypatch):
    """A headless preset must refuse in-place recompression like a delete."""
    session = {k: None for k in wp.ToolConfig.__dataclass_fields__ if k.startswith("last_")}
    session.update(last_input_dir=str(tmp_path), last_output_mode="8",
                   last_origin_format="jxl", last_dest_format="jxl",
                   last_workers=2, last_effort=7,
                   last_advanced_options={})
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    launched = []
    monkeypatch.setattr(menu, "_stream_child",
                        lambda cmd, idle_timeout=3600:
                            (launched.append(list(map(str, cmd))), 0)[1])

    ok = menu._run_saved_session(session, {k: True for k in
                                           ("cjxl", "djxl", "exiftool", "magick",
                                            "tifffile", "pillow", "imagecodecs")},
                                 answers={"overwrite": False, "dry_run": False})
    assert ok is False, "an in-place recompress preset ran unattended"
    assert launched == []

