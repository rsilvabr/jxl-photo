#!/usr/bin/env python3
"""Regression tests for round 37 (bugs_to_fix_260920.md, audit of 2026-09-20).

  B1  wrapper: recompressor policies (on_downgrade/on_regeneration/on_unknown/
      jbrd_policy/no_keep_smaller) were dropped when the wizard rebuilt
      advanced_options, and --on-unknown/--jbrd-policy never reached the
      manifest command line
  B2  encoder --encode-tag xmp ignored a lineage chain sitting in the TIFF's
      EXIF Software field (contradictory records in the JXL)
  B3  recompressor delete gate skipped the MD5 proof for the KEEP_SMALLER
      fallback (action stays "convert", status is "copied")
  B4  outputs were written under their FINAL name: an externally killed run
      left a truncated file that the next smart-sync run trusted forever
  B5  checksums.md5 appends raced between child processes (manifest entries
      targeting one folder), corrupting lines
  B6  dry runs skipped the provenance refusal gate: the simulation promised
      outputs the real run refuses (and the recompressor dry run exited 1)
  B7  two runs started in the same second opened the SAME log file
  B8  decoder: a missing final output left the delete gate silently (no
      kept++, no KEEP line) — invisible in the summary
  B9  transcoder repair wrote its temp as *.jxl (a crash left a fake input
      for the next recursive scan) and its mkstemp ignored TEMP_DIR
  B10 wrapper: mode-6 collision mirror exempted the requested subfolder the
      real finder does NOT exempt; --repair-jbrd demanded cjxl it never uses

The real-codec test (B2) is skipped without cjxl/djxl/exiftool.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import tifffile

import jxl_jpeg_transcoder as tr
import jxl_recompressor as rec
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc
import jxl_photo as wp

REPO = Path(__file__).resolve().parent.parent

_HAVE_TOOLS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
real = pytest.mark.skipif(not _HAVE_TOOLS, reason="needs cjxl/djxl/exiftool")


def _jxl_stub(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)


class _FakeRun:
    returncode = 0
    stdout = ""
    stderr = ""


def _menu():
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


# ---------------------------------------------------------------------------
# B1 — wrapper carries the recompressor policies
# ---------------------------------------------------------------------------

def test_manifest_cmd_carries_recompress_policies():
    menu = _menu()
    cmd = menu._build_manifest_entry_cmd(
        script="jxl_recompressor.py",
        source=str(Path("F:/lib")), dest_path=str(Path("F:/out")), mode=2,
        origin="jxl", dest="jxl", workers=2,
        workflow={"distance": 1.0, "effort": 7, "mode_config": {}},
        advanced={"on_downgrade": "skip", "on_regeneration": "copy",
                  "on_unknown": "skip", "jbrd_policy": "copy",
                  "no_keep_smaller": True},
    )
    for flag, value in (("--on-downgrade", "skip"),
                        ("--on-regeneration", "copy"),
                        ("--on-unknown", "skip"),
                        ("--jbrd-policy", "copy")):
        assert flag in cmd, flag
        assert cmd[cmd.index(flag) + 1] == value, flag
    assert "--no-keep-smaller" in cmd


def test_wizard_declining_advanced_keeps_recompress_policies(monkeypatch):
    """Step 6 may have written the policies already; answering 'no' to the
    advanced step rebuilt the dict from scratch and dropped them (the child
    fell back to `ask`, a silent skip on the wrapper's pipe)."""
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
    menu = _menu()
    workflow = {"origin_format": "jxl", "dest_format": "jxl",
                "overwrite_mode": "2",
                "advanced_options": {"on_downgrade": "skip",
                                     "on_regeneration": "skip",
                                     "on_unknown": "convert",
                                     "jbrd_policy": "copy"}}
    menu._wizard_parameters_advanced(workflow, {})
    adv = workflow["advanced_options"]
    assert adv.get("on_downgrade") == "skip"
    assert adv.get("on_regeneration") == "skip"
    assert adv.get("on_unknown") == "convert"
    assert adv.get("jbrd_policy") == "copy"


# ---------------------------------------------------------------------------
# B2 — --encode-tag xmp merges the chain sitting in EXIF Software (real codec)
# ---------------------------------------------------------------------------

@real
def test_encode_tag_xmp_merges_software_chain(tmp_path, monkeypatch):
    tif = tmp_path / "a.tif"
    tifffile.imwrite(tif, np.zeros((16, 16, 3), dtype=np.uint16),
                     photometric="rgb")
    # A TIFF recovered by the decoder from a --encode-tag software JXL: the
    # chain lives in EXIF Software, dc:Description is empty.
    subprocess.run(["exiftool", "-overwrite_original",
                    "-Software=MySoft | gen=1 | cjxl d=1.0 e=8", str(tif)],
                   check=True, capture_output=True)
    final = tmp_path / "a.jxl"

    enc.setup_logger()
    monkeypatch.setattr(enc, "OVERWRITE", True)
    monkeypatch.setattr(enc, "ENCODE_TAG_MODE", "xmp")
    monkeypatch.setattr(enc, "CJXL_DISTANCE", 0)
    monkeypatch.setattr(enc, "CJXL_EFFORT", 7)

    (_k, status, msg, _e) = enc.convert_one(tif, final, final)
    assert status == "ok", msg

    def _read(tag):
        r = subprocess.run(["exiftool", "-s", "-s", "-s", tag, str(final)],
                           capture_output=True, text=True)
        return r.stdout.strip()

    desc = _read("-XMP-dc:Description")
    assert "d=1.0" in desc and "e=8" in desc, \
        f"the Software chain was not merged into dc:Description: {desc!r}"
    sw = _read("-Software")
    assert "cjxl" not in sw and "gen=" not in sw, \
        f"stale chain left in Software: {sw!r}"
    assert "MySoft" in sw   # unrelated user text survives the strip


# ---------------------------------------------------------------------------
# B3 — keep-smaller fallback goes through the copy MD5 gate
# ---------------------------------------------------------------------------

def _reset_rec_delete_stats():
    for k in rec._delete_stats:
        rec._delete_stats[k] = 0


def test_copied_fallback_must_pass_the_md5_gate(tmp_path, monkeypatch):
    """action stays "convert" on a keep-smaller fallback but the output is a
    byte-copy of the source; gating the MD5 proof on action == "copy" alone
    deleted the source of a CORRUPT copy."""
    src = tmp_path / "a.jxl"
    src.write_bytes(b"original-bytes")
    final = tmp_path / "out" / "a.jxl"
    final.parent.mkdir()
    final.write_bytes(b"original-bYtes")          # one byte flipped

    rec.setup_logger()
    _reset_rec_delete_stats()
    monkeypatch.setattr(rec, "DELETE_SOURCE", True)
    monkeypatch.setattr(rec, "DELETE_SKIPPED", False)
    monkeypatch.setattr(rec, "VERIFY_ROUNDTRIP", False)
    monkeypatch.setattr(rec, "_verify_jxl_integrity", lambda p: True)
    monkeypatch.setattr(rec, "_read_mpg_markers", lambda paths: ({}, True))

    it = {"src": src, "final": final, "in_place": False,
          "action": "convert", "src_d": 1.0}
    rec._delete_gate([it], {str(src): ("copied", str(final))}, {str(src)})
    assert src.exists(), "corrupt keep-smaller copy certified without an MD5 check"
    assert rec._delete_stats["kept"] == 1


def test_copied_fallback_with_matching_bytes_is_deleted(tmp_path, monkeypatch):
    """Positive control: the gate must still pass a faithful copy."""
    src = tmp_path / "a.jxl"
    src.write_bytes(b"original-bytes")
    final = tmp_path / "out" / "a.jxl"
    final.parent.mkdir()
    final.write_bytes(b"original-bytes")

    rec.setup_logger()
    _reset_rec_delete_stats()
    monkeypatch.setattr(rec, "DELETE_SOURCE", True)
    monkeypatch.setattr(rec, "DELETE_SKIPPED", False)
    monkeypatch.setattr(rec, "VERIFY_ROUNDTRIP", False)
    monkeypatch.setattr(rec, "_verify_jxl_integrity", lambda p: True)
    monkeypatch.setattr(rec, "_read_mpg_markers", lambda paths: ({}, True))

    it = {"src": src, "final": final, "in_place": False,
          "action": "convert", "src_d": 1.0}
    rec._delete_gate([it], {str(src): ("copied", str(final))}, {str(src)})
    assert not src.exists()


# ---------------------------------------------------------------------------
# B4 — the codec never writes under the final name (temp + os.replace)
# ---------------------------------------------------------------------------

def test_encoder_codec_writes_to_a_uuid_temp_beside_final(tmp_path, monkeypatch):
    """The cjxl output argument must be a temp next to the final, so a killed
    run never leaves a truncated file under the final (smart-sync-trusted)
    name."""
    tif = tmp_path / "photo.tif"
    tifffile.imwrite(tif, np.zeros((8, 8, 3), dtype=np.uint16), photometric="rgb")
    final = tmp_path / "photo.jxl"

    monkeypatch.setattr(enc, "extract_exif_raw", lambda *a, **k: None)
    monkeypatch.setattr(enc, "extract_xmp_original", lambda *a, **k: None)
    monkeypatch.setattr(enc, "get_page_icc", lambda *a, **k: (None, False))
    monkeypatch.setattr(enc, "apply_d50_policy", lambda icc, p: icc)
    monkeypatch.setattr(enc, "reorder_jxl_boxes", lambda p: None)
    monkeypatch.setattr(enc, "_verify_jxl_integrity", lambda p: True)

    cjxl_targets = []

    def fake_run(cmd, **kw):
        if "cjxl" in str(cmd[0]):
            cjxl_targets.append(Path(cmd[2]))
            Path(cmd[2]).write_bytes(b"jxl-bytes")
        return _FakeRun()

    monkeypatch.setattr(enc.subprocess, "run", fake_run)
    enc.setup_logger()
    monkeypatch.setattr(enc, "OVERWRITE", True)

    (_k, status, msg, _e) = enc.convert_one(tif, final, final)
    assert status == "ok", msg
    assert cjxl_targets, "cjxl was never invoked"
    target = cjxl_targets[0]
    assert target != final, "cjxl wrote straight to the final path"
    assert target.parent == final.parent
    # ".tmp, not .jxl": a kill before promotion must not leave a file the
    # next run's folder scan can adopt as a real JXL input.
    assert target.name.endswith("_" + final.name + ".tmp")
    assert not target.name.endswith(".jxl")
    assert final.exists() and final.read_bytes() == b"jxl-bytes"
    assert list(tmp_path.glob("*_photo.jxl*")) == [], "uuid temp left behind"


def test_encoder_failed_codec_leaves_nothing_at_final(tmp_path, monkeypatch):
    tif = tmp_path / "photo.tif"
    tifffile.imwrite(tif, np.zeros((8, 8, 3), dtype=np.uint16), photometric="rgb")
    final = tmp_path / "photo.jxl"

    monkeypatch.setattr(enc, "extract_exif_raw", lambda *a, **k: None)
    monkeypatch.setattr(enc, "extract_xmp_original", lambda *a, **k: None)
    monkeypatch.setattr(enc, "get_page_icc", lambda *a, **k: (None, False))
    monkeypatch.setattr(enc, "apply_d50_policy", lambda icc, p: icc)
    monkeypatch.setattr(enc, "reorder_jxl_boxes", lambda p: None)

    cjxl_targets = []

    def fake_run(cmd, **kw):
        if "cjxl" in str(cmd[0]):
            cjxl_targets.append(Path(cmd[2]))
            Path(cmd[2]).write_bytes(b"PARTIAL")
            return subprocess.CompletedProcess(cmd, 1, b"", b"boom")
        return _FakeRun()

    monkeypatch.setattr(enc.subprocess, "run", fake_run)
    enc.setup_logger()
    monkeypatch.setattr(enc, "OVERWRITE", True)

    (_k, status, _m, _e) = enc.convert_one(tif, final, final)
    assert status == "error"
    assert cjxl_targets and cjxl_targets[0] != final
    assert not final.exists(), "a partial output carries the final name"
    assert list(tmp_path.glob("*_photo.jxl*")) == [], "uuid temp left behind"


def test_recompressor_main_assigns_a_beside_final_temp(tmp_path, monkeypatch):
    """Without staging the run used to write the re-encode straight to the
    FINAL name; main() must hand the worker a uuid temp beside it instead."""
    src_dir = tmp_path / "in"
    src_dir.mkdir()
    src = src_dir / "a.jxl"
    _jxl_stub(src)
    out_dir = tmp_path / "out"

    captured = {}

    def fake_pg(items, workers):
        captured["items"] = [dict(it) for it in items]
        return ({str(it["src"]): ("ok", str(it["final"])) for it in items},
                {str(it["src"]) for it in items})

    monkeypatch.setattr(rec, "process_group", fake_pg)
    monkeypatch.setattr(rec, "_read_encode_params_batch",
                        lambda paths: {str(p): {"desc": "", "software": "",
                                                "params": None, "gen": 0}
                                       for p in paths})
    monkeypatch.setattr(rec, "emit_summary_json", lambda *a, **k: None)
    monkeypatch.setattr(rec, "_get_cjxl_cmd", lambda: "cjxl")
    monkeypatch.setattr(shutil, "which", lambda c: "C:/tools/x")
    monkeypatch.setattr(sys, "argv",
                        ["jxl_recompressor.py", str(src_dir), str(out_dir),
                         "--mode", "2", "--on-unknown", "convert",
                         "--no-preflight"])
    try:
        rec.main()
    except SystemExit as e:
        assert e.code == 0
    it = captured["items"][0]
    assert it["write"] != it["final"], "the re-encode writes under the final name"
    assert it["write"].parent == it["final"].parent
    # Round 39 (audit item 26): the beside-final temp ends in ".tmp" so no
    # orphan is adoptable as a real input on the next scan.
    assert it["write"].name.endswith("_" + it["final"].name + ".tmp"), \
        f"temp lost the .tmp rule: {it['write'].name}"


def test_recompressor_promotes_beside_final_temp_over_existing(tmp_path, monkeypatch):
    """Recompress of a target that ALREADY exists (a keep-smaller re-run):
    the beside-the-final uuid temp must go in with os.replace — shutil.move
    onto an existing destination raises FileExistsError on Windows, so the
    old _promote_from_staging path reported an error and kept the old file."""
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    final = tmp_path / "out" / "photo.jxl"
    final.parent.mkdir()
    final.write_bytes(b"older-output")
    write = final.parent / f"{'ab' * 16}_photo.jxl"

    def fake_convert(src, w, f, *a):
        Path(w).write_bytes(b"recompressed")
        return (str(src), "ok", str(f))

    monkeypatch.setattr(rec, "convert_one", fake_convert)
    monkeypatch.setattr(rec, "TEMP2_DIR", None)
    rec.setup_logger()

    it = {"src": src, "final": final, "write": write, "action": "convert",
          "in_place": False, "desc": "", "software": "", "src_d": 1.0}
    results, promoted = rec.process_group([it], 1)
    assert results[str(src)][0] == "ok", results
    assert final.read_bytes() == b"recompressed", "existing output was not replaced"
    assert not write.exists(), "uuid temp left behind"
    assert str(src) in promoted


def test_transcoder_codec_writes_to_a_temp_beside_final(tmp_path, monkeypatch):
    src = tmp_path / "a.jpg"
    src.write_bytes(b"\xff\xd8fake")
    final = tmp_path / "a.jxl"

    cjxl_targets = []

    def fake_run(cmd, **kw):
        if "cjxl" in str(cmd[0]):
            cjxl_targets.append(Path(cmd[2]))
            Path(cmd[2]).write_bytes(b"PARTIAL")
            return subprocess.CompletedProcess(cmd, 1, b"", b"boom")
        return _FakeRun()

    monkeypatch.setattr(tr.subprocess, "run", fake_run)
    monkeypatch.setattr(tr, "reorder_jxl_boxes", lambda p: None)
    tr.setup_logger()

    (s, status, _f, _e) = tr.encode_one_transcode(src, final, final, False, 7, False)
    assert status == "error"
    assert cjxl_targets and cjxl_targets[0] != final, \
        "cjxl wrote straight to the final path"
    assert not final.exists()
    assert [p for p in tmp_path.iterdir() if p != src] == [], "temp left behind"


# ---------------------------------------------------------------------------
# B5 — checksums.md5 appends are serialized across processes
# ---------------------------------------------------------------------------

def test_checksum_append_fails_closed_while_locked(tmp_path, monkeypatch):
    """A lock held by another process means the line is NOT written — a
    missing provenance entry blocks deletions; a possibly-corrupt line is
    worse."""
    db = tmp_path / "checksums.md5"
    (tmp_path / "checksums.md5.lock").write_text("")
    monkeypatch.setattr(tr, "_MD5_LOCK_TIMEOUT_S", 0.3)
    tr._append_checksum_line(db, "abc123  x.jxl\n")
    assert not db.exists() or "x.jxl" not in db.read_text()


def test_checksum_db_survives_concurrent_child_processes(tmp_path):
    """Two manifest entries targeting one folder are two child processes; the
    thread lock alone let their appends interleave mid-line."""
    child = (
        "import sys; sys.path.insert(0, r'%s');"
        "from pathlib import Path;"
        "import jxl_jpeg_transcoder as tr;"
        "[tr.store_md5_db(Path(r'%s') / ('p%%d_%s.jxl' %% i), '0' * 32)"
        " for i in range(20)]"
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", child % (REPO, tmp_path, tag)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for tag in ("a", "b")
    ]
    for p in procs:
        assert p.wait() == 0
    lines = (tmp_path / "checksums.md5").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 40, f"lost or torn lines: {len(lines)}"
    for line in lines:
        md5, name = line.split(None, 1)
        assert len(md5) == 32 and name.endswith(".jxl"), f"torn line: {line!r}"


# ---------------------------------------------------------------------------
# B6 — dry runs preview the provenance refusals
# ---------------------------------------------------------------------------

def test_decoder_dry_run_reports_would_refuse(tmp_path, monkeypatch):
    src_dir = tmp_path / "in"
    src_dir.mkdir()
    src = src_dir / "a.jxl"
    _jxl_stub(src)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "a.tif").write_bytes(b"an unrelated master")

    captured = {}
    monkeypatch.setattr(dec, "emit_summary_json",
                        lambda *a, **k: captured.update(k))
    monkeypatch.setattr(dec, "collect_multipage_groups",
                        lambda jxls: {src: [(src, 0, False, False, 0, False, None)]})
    monkeypatch.setattr(dec, "_read_source_markers_batch",
                        lambda paths: {str(p): {"src": None, "srcsum": None}
                                       for p in paths})
    monkeypatch.setattr(dec, "OVERWRITE", False)
    monkeypatch.setattr(dec, "FORCE_NONE_MODE", False)
    monkeypatch.setattr(dec, "PROVENANCE_CHECK", "path")
    monkeypatch.setattr(dec, "_group_conflicts", [])
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_decoder.py", str(src_dir), str(out_dir),
                         "--mode", "2", "--delete-source", "--dry-run",
                         "--summary-json"])
    dec.main()
    assert captured.get("errors") == 1, \
        f"dry run hid the refusal from the summary: {captured}"


def test_recompressor_dry_run_reports_would_refuse_and_exits_0(tmp_path, monkeypatch):
    src_dir = tmp_path / "in"
    src_dir.mkdir()
    src = src_dir / "a.jxl"
    _jxl_stub(src)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "a.jxl").write_bytes(b"someone else's archive")

    captured = {}
    monkeypatch.setattr(rec, "emit_summary_json",
                        lambda *a, **k: captured.update(k))
    monkeypatch.setattr(rec, "_read_encode_params_batch",
                        lambda paths: {str(p): {"desc": "", "software": "",
                                                "params": None, "gen": 0}
                                       for p in paths})
    monkeypatch.setattr(rec, "_read_source_markers_batch",
                        lambda paths: {str(p): {"src": None, "srcsum": None}
                                       for p in paths})
    monkeypatch.setattr(sys, "argv",
                        ["jxl_recompressor.py", str(src_dir), str(out_dir),
                         "--mode", "2", "--delete-source", "--dry-run",
                         "--summary-json", "--on-unknown", "convert"])
    with pytest.raises(SystemExit) as exc:
        rec.main()
    assert exc.value.code == 0
    assert captured.get("errors") == 1, \
        f"dry run hid the refusal from the summary: {captured}"


# ---------------------------------------------------------------------------
# B7 — two runs in the same second get distinct log files
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mod", [enc, dec, tr, rec])
def test_setup_logger_names_are_unique_within_a_second(mod, monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "LOG_DIR", tmp_path)
    a = mod.setup_logger()
    b = mod.setup_logger()
    assert a != b, "two runs in the same second share one log file"
    assert str(os.getpid()) in a.name


# ---------------------------------------------------------------------------
# B8 — decoder counts a missing final output as a KEEP
# ---------------------------------------------------------------------------

def test_decoder_missing_final_output_is_counted_kept(tmp_path, monkeypatch):
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    final = tmp_path / "out" / "photo.tif"        # never created
    final.parent.mkdir()

    dec.setup_logger()
    for k in dec._delete_stats:
        dec._delete_stats[k] = 0
    monkeypatch.setattr(dec, "DELETE_SOURCE", True)
    monkeypatch.setattr(dec, "TEMP2_DIR", None)
    monkeypatch.setattr(dec, "convert_multipage_jxl_group",
                        lambda *a, **k: (str(src), "ok", str(final)))
    task = {"type": "multi", "main_jxl": src,
            "entries": [(src, 0, False, False, 0, False, None)],
            "ignored_thumbs": [], "final_tiff": final}
    dec.process_group([task], 1)
    assert src.exists()
    assert dec._delete_stats["kept"] == 1, \
        "a group whose output never materialised left no trace in the summary"


# ---------------------------------------------------------------------------
# B9 — repair temps are not *.jxl; mkstemp honors TEMP_DIR
# ---------------------------------------------------------------------------

def test_repair_temp_is_not_a_jxl_name(tmp_path, monkeypatch):
    jxl = tmp_path / "p.jxl"
    _jxl_stub(jxl)
    seen = {}

    def fake_strip(src, tmp):
        seen["tmp"] = tmp
        return None, "cannot repair"

    monkeypatch.setattr(tr, "_strip_markers_proving_repair", fake_strip)
    state, _detail = tr._repair_one_jbrd(jxl, dry_run=True)
    assert state == "broken"
    tmp = seen["tmp"]
    assert tmp.suffix == ".tmp" and "_repair_" in tmp.name, \
        f"a crash would leave a *.jxl the next scan eats: {tmp.name}"
    assert not tmp.exists(), "repair temp left behind"


def test_reconstruct_temp_honors_temp_dir(tmp_path, monkeypatch):
    tdir = tmp_path / "mytemp"
    tdir.mkdir()
    jxl = tmp_path / "a.jxl"
    _jxl_stub(jxl)
    jpeg = tmp_path / "a.jpg"
    jpeg.write_bytes(b"\xff\xd8\xff\xd9")
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        Path(cmd[-1]).write_bytes(b"\xff\xd8\xff\xd9")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(tr, "TEMP_DIR", str(tdir))
    monkeypatch.setattr(tr, "_tool_at_least", lambda *a: True)
    monkeypatch.setattr(tr.subprocess, "run", fake_run)
    # No checksums.md5 beside the JXL -> no self-hash -> the legacy path
    # reconstructs into a temp, which is where TEMP_DIR was ignored.
    assert tr._jxl_binds_to_archived_jpeg(jxl, jpeg, None)
    assert seen and Path(seen[0][-1]).parent == tdir, \
        "the reconstruction temp ignored TEMP_DIR"


# ---------------------------------------------------------------------------
# B10 — wrapper mirrors the real finders; repair needs no cjxl
# ---------------------------------------------------------------------------

def test_mode6_collision_mirror_matches_the_real_finder(tmp_path):
    """The real mode-6 finder skips decoder-output folders UNCONDITIONALLY
    (honor_requested_subfolder=False); the mirror exempted the requested
    subfolder and reported a collision the child would never produce."""
    root = tmp_path / "lib"
    a = root / "_EXPORT" / "16B_TIFF" / "x.tif"
    b = root / "_EXPORT" / "shoot" / "x.tif"
    a.parent.mkdir(parents=True)
    b.parent.mkdir(parents=True)
    a.write_bytes(b"x")
    b.write_bytes(b"x")
    # Sanity: the child's own finder skips the decoder-output folder even
    # with the subfolder explicitly requested.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(enc, "EXPORT_MARKER", "_EXPORT")
    monkeypatch.setattr(enc, "EXPORT_TIFF_SUBFOLDER", "16B_TIFF")
    found = [f.name for f in enc.find_tiffs_mode6(root)]
    monkeypatch.undo()
    assert found == ["x.tif"] and len(found) == 1

    menu = _menu()
    collisions = menu._manifest_output_collisions(
        [(str(root), str(root), 6)], {".tif", ".tiff"},
        origin="tiff", dest="jxl",
        export_marker="_EXPORT", export_subfolder="16B_TIFF")
    assert collisions == [], \
        f"mirror reported a collision the real run never produces: {collisions}"


def test_repair_jbrd_needs_no_cjxl(tmp_path, monkeypatch):
    """Repair only decodes (djxl --reconstruct_jpeg) and edits metadata
    (exiftool); requiring cjxl refused a repair on a machine that can run one."""
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    called = {}
    def fake_repair(args):
        called["ran"] = True
        return 0
    monkeypatch.setattr(tr, "cmd_repair_jbrd", fake_repair)
    monkeypatch.setattr(tr, "_tool_at_least", lambda *a: True)
    monkeypatch.setattr(shutil, "which",
                        lambda c: None if "cjxl" in str(c) else "C:/tools/x")
    monkeypatch.setattr(sys, "argv",
                        ["jxl_jpeg_transcoder.py", str(tmp_path), "--repair-jbrd"])
    with pytest.raises(SystemExit) as exc:
        tr.main()
    assert exc.value.code == 0
    assert called.get("ran"), "--repair-jbrd was gated on cjxl it never invokes"
