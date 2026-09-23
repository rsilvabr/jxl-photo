#!/usr/bin/env python3
"""Regression tests for round 40 (bug_report_260923.md).

  #1  decoder: under --no-reconstruct-multipage a normal single-page photo
      named `*_thumbnail` was tagged as a thumbnail (SubFileType=1), or skipped
      entirely by --thumbnail-handling ignore. _has_internal_markers is true for
      ANY jxlphoto-* marker (jxlphoto-depth is on every encoder output), so the
      filename suffix decided the role alone.
  #2  recompressor: modes 1/3 deleted a SOURCE under --delete-skipped on the
      strength of an integrity check alone; a valid, newer, same-named output
      from an UNRELATED photo certified the deletion (real data loss). The
      provenance proof now runs on the skipped path in every mode.
  #3  --repair-jbrd stripped only the LAST marker pair; a file carrying two
      pairs with different ids stayed broken and was reported STILL BROKEN.
  #4  dry runs labelled would-SKIP pairs as conversions (recompressor +
      transcoder cmd_transcode/cmd_convert/cmd_auto). #15: cmd_auto counted
      provenance-refused pairs in `ok`.
  #5  decoder: the dry-run topline ignored smart-sync / existing-output skips
      (the encoder's #409 fix never reached the decoder).

These import only the child scripts, so they fail against an extracted HEAD
copy (pre-fix proof) as well as passing here.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_jpeg_transcoder as tr
import jxl_recompressor as rec
import jxl_tiff_decoder as dec

REPO = Path(__file__).resolve().parent.parent

_JXL_SIG = b"\x00\x00\x00\x0cJXL \r\n\x87\n"


def _jxl_stub(path: Path, payload: bytes = b"\x00" * 32):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_JXL_SIG + payload)


def _make_newer(target: Path, than: Path, delta: int = 100):
    stamp = than.stat().st_mtime + delta
    os.utime(target, (stamp, stamp))


class _FakeRun:
    def __init__(self, stdout="", stderr=b"", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class _FakeLogger:
    def __init__(self):
        self.infos, self.warnings, self.errors = [], [], []

    def info(self, m): self.infos.append(str(m))
    def warning(self, m): self.warnings.append(str(m))
    def error(self, m): self.errors.append(str(m))
    def debug(self, m): pass


def _args(tmp_path, **kw):
    base = dict(
        input=tmp_path, output=None, mode=1, workers=2, effort=7,
        overwrite=False, sync=False, staging=None, dry_run=False,
        delete_source=False, no_md5=False, no_verify=False, decode=False,
        force_transcode=False, force_convert=False, format=None, quality=95,
        distance=1.0, bit_depth=None, icc_profile=None, ram=True,
        output_name="converted", output_suffix="_converted",
        rename_from="", rename_to="",
    )
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture(autouse=True)
def _reset_tr_globals():
    tr._run_summary.clear()
    tr._reset_abort()
    yield
    tr.DELETE_SOURCE = False
    tr.DELETE_CONFIRM = True
    tr.TEMP2_DIR = None
    tr.STORE_MD5 = True
    tr._run_summary.clear()


@pytest.fixture(autouse=True)
def _reset_rec_globals():
    """The full suite can leave a recompressor global armed from an earlier
    test: `rec.main()` assigns the module globals through a `global` statement,
    so a `--delete-source` run leaks `DELETE_SOURCE=True` past its test. Pin the
    delete/provenance defaults before AND after every test in this file so the
    dry-run tests are order-independent and do not leak in turn."""
    def _clean():
        rec.DELETE_SOURCE = False
        rec.DELETE_SKIPPED = False
        rec.VERIFY_ROUNDTRIP = False
        rec.OVERWRITE = "smart"
        rec.PROVENANCE_CHECK = "path"
    _clean()
    yield
    _clean()


def _reset_rec_delete_stats():
    for k in rec._delete_stats:
        rec._delete_stats[k] = 0


# ===========================================================================
# #1 — the thumbnail role comes from a marker, not the filename suffix
# ===========================================================================

_INFO = {'group': None, 'inherited': False, 'subfiletype': 0,
         'grayscale': False, 'depth': '8', 'page': None, 'pages': None,
         'thumb': False, 'srcsum': None}


def _groups_for(tmp_path, monkeypatch, name, **info_over):
    jxl = tmp_path / name
    _jxl_stub(jxl)
    info = dict(_INFO)
    info.update(info_over)
    monkeypatch.setattr(dec, "_read_multipage_markers_batch",
                        lambda jxls: {str(j): dict(info) for j in jxls})
    monkeypatch.setattr(dec, "RECONSTRUCT_MULTIPAGE", False)
    monkeypatch.setattr(dec, "THUMBNAIL_SUFFIX", "_thumbnail")
    groups = dec.collect_multipage_groups([jxl])
    return groups[jxl][0]


def test_standalone_thumbnail_suffix_is_a_normal_photo(tmp_path, monkeypatch):
    """An encoder output carries jxlphoto-depth on every page; that must not
    make `photo_thumbnail.jxl` a reduced-resolution thumbnail."""
    entry = _groups_for(tmp_path, monkeypatch, "photo_thumbnail.jxl")
    assert entry[2] is False, "a normal photo named *_thumbnail was tagged as one"


def test_grayscale_standalone_thumbnail_suffix_is_a_normal_photo(tmp_path, monkeypatch):
    entry = _groups_for(tmp_path, monkeypatch, "holiday_thumbnail.jxl",
                        grayscale=True)
    assert entry[2] is False


def test_legacy_split_thumbnail_with_group_marker_is_still_a_thumbnail(tmp_path, monkeypatch):
    """A legacy split page carries jxlphoto-group (and/or jxlphoto-page); the
    suffix is trusted for those."""
    entry = _groups_for(tmp_path, monkeypatch, "scan_page1_thumbnail.jxl",
                        group="gid", page=1)
    assert entry[2] is True


def test_solo_page_marker_thumbnail_suffix_is_a_thumbnail(tmp_path, monkeypatch):
    entry = _groups_for(tmp_path, monkeypatch, "scan_page2_thumbnail.jxl",
                        page=2)
    assert entry[2] is True


def test_thumb_marker_alone_is_a_thumbnail(tmp_path, monkeypatch):
    entry = _groups_for(tmp_path, monkeypatch, "scan.jxl", thumb=True)
    assert entry[2] is True


# ===========================================================================
# #2 — the --delete-skipped gate proves provenance in every mode
# ===========================================================================

def test_delete_skipped_refuses_an_unrelated_output(tmp_path, monkeypatch):
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    final = tmp_path / "recompressed_jxl" / "photo.jxl"
    _jxl_stub(final, b"someone else's archive")

    rec.setup_logger()
    _reset_rec_delete_stats()
    monkeypatch.setattr(rec, "DELETE_SOURCE", True)
    monkeypatch.setattr(rec, "DELETE_SKIPPED", True)
    monkeypatch.setattr(rec, "VERIFY_ROUNDTRIP", False)
    monkeypatch.setattr(rec, "_verify_jxl_integrity", lambda p: True)
    monkeypatch.setattr(rec, "_read_mpg_markers", lambda paths: ({}, True))
    monkeypatch.setattr(rec, "_read_source_markers_batch",
                        lambda paths: {str(p): ({"src": "other-photo", "srcsum": "o"}
                                                if "recompressed_jxl" in str(p)
                                                else {"src": "this-photo", "srcsum": "s"})
                                       for p in paths})

    it = {"src": src, "final": final, "in_place": False,
          "action": "convert", "src_d": 1.0}
    rec._delete_gate([it], {str(src): ("skipped", str(final))}, set())
    assert src.exists(), "the source was deleted on an unrelated same-named output"
    assert rec._delete_stats["kept"] == 1


def test_delete_skipped_deletes_when_provenance_matches(tmp_path, monkeypatch):
    """Positive control: the same path still deletes a genuine archived source."""
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    final = tmp_path / "recompressed_jxl" / "photo.jxl"
    _jxl_stub(final, b"the previous re-encode")

    rec.setup_logger()
    _reset_rec_delete_stats()
    monkeypatch.setattr(rec, "DELETE_SOURCE", True)
    monkeypatch.setattr(rec, "DELETE_SKIPPED", True)
    monkeypatch.setattr(rec, "VERIFY_ROUNDTRIP", False)
    monkeypatch.setattr(rec, "_verify_jxl_integrity", lambda p: True)
    monkeypatch.setattr(rec, "_read_mpg_markers", lambda paths: ({}, True))
    monkeypatch.setattr(rec, "_read_source_markers_batch",
                        lambda paths: {str(p): {"src": "same", "srcsum": "same"}
                                       for p in paths})

    it = {"src": src, "final": final, "in_place": False,
          "action": "convert", "src_d": 1.0}
    rec._delete_gate([it], {str(src): ("skipped", str(final))}, set())
    assert not src.exists()


def test_delete_skipped_keeps_when_a_marker_cannot_be_read(tmp_path, monkeypatch):
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    final = tmp_path / "recompressed_jxl" / "photo.jxl"
    _jxl_stub(final)

    rec.setup_logger()
    _reset_rec_delete_stats()
    monkeypatch.setattr(rec, "DELETE_SOURCE", True)
    monkeypatch.setattr(rec, "DELETE_SKIPPED", True)
    monkeypatch.setattr(rec, "VERIFY_ROUNDTRIP", False)
    monkeypatch.setattr(rec, "_verify_jxl_integrity", lambda p: True)
    monkeypatch.setattr(rec, "_read_mpg_markers", lambda paths: ({}, True))
    monkeypatch.setattr(rec, "_read_source_markers_batch",
                        lambda paths: {str(p): {"src": None, "srcsum": None}
                                       for p in paths})

    it = {"src": src, "final": final, "in_place": False,
          "action": "convert", "src_d": 1.0}
    rec._delete_gate([it], {str(src): ("skipped", str(final))}, set())
    assert src.exists(), "an unreadable marker must fail CLOSED (keep)"


# ===========================================================================
# #3 — --repair-jbrd strips EVERY marker pair
# ===========================================================================

def test_strip_removes_every_marker_pair(tmp_path, monkeypatch):
    jxl = tmp_path / "p.jxl"
    _jxl_stub(jxl)
    state = {"srcs": ["AAAA", "BBBB"], "srcsums": ["1111", "2222"]}
    calls = []

    def fake_exif(lines, **kw):
        calls.append(list(lines))
        for line in lines:
            if line.startswith("-XMP-dc:Relation-=jxlphoto-src:"):
                v = line.split("jxlphoto-src:", 1)[1]
                state["srcs"] = [x for x in state["srcs"] if x != v]
            elif line.startswith("-XMP-dc:Relation-=jxlphoto-srcsum:"):
                v = line.split("jxlphoto-srcsum:", 1)[1]
                state["srcsums"] = [x for x in state["srcsums"] if x != v]
        return subprocess.CompletedProcess(lines, 0, "", "")

    monkeypatch.setattr(tr, "_read_all_source_marker_values",
                        lambda p: (list(state["srcs"]), list(state["srcsums"])))
    monkeypatch.setattr(tr, "_run_exiftool_argfile", fake_exif)
    monkeypatch.setattr(tr, "reorder_jxl_boxes", lambda p: None)

    why = tr._strip_provenance_markers(jxl, {"src": "BBBB", "srcsum": "2222"})
    assert why is None, why
    joined = "\n".join("\n".join(c) for c in calls)
    for token in ("jxlphoto-src:AAAA", "jxlphoto-src:BBBB",
                  "jxlphoto-srcsum:1111", "jxlphoto-srcsum:2222"):
        assert token in joined, f"{token} was never removed"
    assert state["srcs"] == [] and state["srcsums"] == []


def test_strip_postcheck_rejects_a_leftover_token(tmp_path, monkeypatch):
    """If exiftool silently leaves ANY token, the strip must NOT report success
    (the reconstruction test would then mark the file STILL BROKEN)."""
    jxl = tmp_path / "p.jxl"
    _jxl_stub(jxl)
    monkeypatch.setattr(tr, "_read_all_source_marker_values",
                        lambda p: (["AAAA"], []))
    monkeypatch.setattr(tr, "_run_exiftool_argfile",
                        lambda lines, **kw: subprocess.CompletedProcess(lines, 0, "", ""))
    monkeypatch.setattr(tr, "reorder_jxl_boxes", lambda p: None)
    why = tr._strip_provenance_markers(jxl, {"src": "AAAA"})
    assert why and "still there" in why


# ===========================================================================
# #4 — dry runs label would-SKIP and count it as skipped
# ===========================================================================

def test_recompressor_dry_run_counts_existing_output_as_skip(tmp_path, monkeypatch):
    src_dir = tmp_path / "in"
    src_dir.mkdir()
    src = src_dir / "a.jxl"
    _jxl_stub(src)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "a.jxl").write_bytes(b"existing archive")

    captured = {}
    fake = _FakeLogger()
    monkeypatch.setattr(rec, "emit_summary_json",
                        lambda *a, **k: captured.update(k))
    monkeypatch.setattr(rec, "setup_logger", lambda: None)
    monkeypatch.setattr(rec, "logger", fake)
    monkeypatch.setattr(rec, "_read_encode_params_batch",
                        lambda paths: {str(p): {"desc": "", "software": "",
                                                "params": None, "gen": 0}
                                       for p in paths})
    monkeypatch.setattr(sys, "argv",
                        ["jxl_recompressor.py", str(src_dir), str(out_dir),
                         "--mode", "2", "--dry-run", "--summary-json",
                         "--on-unknown", "convert"])
    with pytest.raises(SystemExit):
        rec.main()
    assert captured.get("ok") == 0, captured
    assert captured.get("skipped") == 1, captured
    assert any("SKIP (exists)" in m for m in fake.infos)


def test_recompressor_dry_run_does_not_promise_a_refused_deletion(tmp_path, monkeypatch):
    """The --delete-skipped preview must mirror the new gate: a skipped convert
    without matching provenance is NOT a would-delete."""
    src_dir = tmp_path / "in"
    src_dir.mkdir()
    src = src_dir / "a.jxl"
    _jxl_stub(src)
    final = src_dir / "recompressed_jxl" / "a.jxl"
    _jxl_stub(final, b"unrelated")
    _make_newer(final, src)

    captured = {}
    fake = _FakeLogger()
    monkeypatch.setattr(rec, "emit_summary_json",
                        lambda *a, **k: captured.update(k))
    monkeypatch.setattr(rec, "setup_logger", lambda: None)
    monkeypatch.setattr(rec, "logger", fake)
    monkeypatch.setattr(rec, "DELETE_SOURCE", True)
    monkeypatch.setattr(rec, "DELETE_SKIPPED", True)
    monkeypatch.setattr(rec, "OVERWRITE", "smart")
    monkeypatch.setattr(rec, "_read_encode_params_batch",
                        lambda paths: {str(p): {"desc": "", "software": "",
                                                "params": None, "gen": 0}
                                       for p in paths})
    monkeypatch.setattr(rec, "_read_source_markers_batch",
                        lambda paths: {str(p): ({"src": "other", "srcsum": "o"}
                                                if "recompressed_jxl" in str(p)
                                                else {"src": "this", "srcsum": "s"})
                                       for p in paths})
    monkeypatch.setattr(sys, "argv",
                        ["jxl_recompressor.py", str(src_dir), "--mode", "1",
                         "--dry-run", "--summary-json", "--on-unknown", "convert",
                         "--delete-source", "--delete-skipped"])
    with pytest.raises(SystemExit):
        rec.main()
    assert any("would delete 0 source(s)" in m for m in fake.infos), \
        f"preview promised a deletion the gate refuses: {fake.infos}"


def test_transcode_dry_run_counts_existing_output_as_skip(monkeypatch, tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8fake")
    out_dir = tmp_path / "converted_jxl"
    out_dir.mkdir()
    (out_dir / "a.jxl").write_bytes(b"existing")
    fake = _FakeLogger()
    monkeypatch.setattr(tr, "setup_logger", lambda: None)
    monkeypatch.setattr(tr, "logger", fake)
    tr.cmd_transcode(_args(tmp_path, dry_run=True))
    assert tr._run_summary["skipped"] == 1, tr._run_summary
    assert tr._run_summary["ok"] == 0, tr._run_summary
    assert any("SKIP (exists)" in m for m in fake.infos)


def test_convert_dry_run_counts_existing_output_as_skip(monkeypatch, tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8fake")
    out_dir = tmp_path / "converted_jxl"
    out_dir.mkdir()
    (out_dir / "a.jxl").write_bytes(b"existing")
    fake = _FakeLogger()
    monkeypatch.setattr(tr, "setup_logger", lambda: None)
    monkeypatch.setattr(tr, "logger", fake)
    tr.cmd_convert(_args(tmp_path, dry_run=True), from_jxl=False)
    assert tr._run_summary["skipped"] == 1, tr._run_summary
    assert tr._run_summary["ok"] == 0, tr._run_summary


def test_auto_dry_run_counts_existing_output_as_skip(monkeypatch, tmp_path):
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    (tmp_path / "a.jxl").write_bytes(_JXL_SIG + b"\x00" * 32)  # mode 2, flat
    fake = _FakeLogger()
    monkeypatch.setattr(tr, "setup_logger", lambda: None)
    monkeypatch.setattr(tr, "logger", fake)
    # Isolate the encode direction: without this the existing output is also
    # scanned as a JXL input and adds a second pair.
    monkeypatch.setattr(tr, "find_jxls_recursive", lambda root: [])
    tr.cmd_auto(_args(tmp_path, mode=2, dry_run=True, output_suffix=""))
    assert tr._run_summary["skipped"] == 1, tr._run_summary
    assert tr._run_summary["ok"] == 0, tr._run_summary


def test_auto_dry_run_ok_excludes_provenance_refused_pairs(monkeypatch, tmp_path):
    """#15: `ok=len(all_pairs)` counted a pair the provenance filter refused."""
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    (tmp_path / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    (tmp_path / "b.jxl").write_bytes(_JXL_SIG + b"\x00" * 32)  # unrelated output

    monkeypatch.setattr(tr, "setup_logger", lambda: None)
    monkeypatch.setattr(tr, "find_jxls_recursive", lambda root: [])
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "_run_collapses_structure", lambda *a, **k: True)
    monkeypatch.setattr(tr, "_read_source_markers_batch",
                        lambda paths: {str(p): {"src": None, "srcsum": None}
                                       for p in paths})
    tr.cmd_auto(_args(tmp_path, mode=2, dry_run=True, delete_source=True,
                      output_suffix=""))
    assert tr._run_summary["ok"] == 1, tr._run_summary
    assert tr._run_summary["errors"] == 1, tr._run_summary


# ===========================================================================
# #5 — the decoder's dry-run topline matches the real run on skips
# ===========================================================================

def test_decoder_dry_run_counts_sync_skips(tmp_path, monkeypatch):
    src_dir = tmp_path / "in"
    src_dir.mkdir()
    src = src_dir / "a.jxl"
    _jxl_stub(src)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    final = out_dir / "a.tif"
    final.write_bytes(b"our decode")
    _make_newer(final, src)

    captured = {}
    monkeypatch.setattr(dec, "emit_summary_json",
                        lambda *a, **k: captured.update(k))
    monkeypatch.setattr(dec, "collect_multipage_groups",
                        lambda jxls: {src: [(src, 0, False, False, 0, False, None)]})
    monkeypatch.setattr(dec, "_decode_output_is_ours", lambda p: True)
    monkeypatch.setattr(dec, "OVERWRITE", "smart")
    monkeypatch.setattr(dec, "FORCE_NONE_MODE", False)
    monkeypatch.setattr(dec, "_group_conflicts", [])
    monkeypatch.setattr(dec, "_check_external_tools", lambda *a, **k: None)
    monkeypatch.setattr(dec, "_warn_if_libjxl_too_old", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_decoder.py", str(src_dir), str(out_dir),
                         "--mode", "2", "--dry-run", "--summary-json"])
    dec.main()
    assert captured.get("ok") == 0, captured
    assert captured.get("skipped") == 1, captured


def test_decoder_dry_run_counts_plain_existing_output_as_skip(tmp_path, monkeypatch):
    """Not only smart sync: an existing TIFF with skip-existing is a skip too."""
    src_dir = tmp_path / "in"
    src_dir.mkdir()
    src = src_dir / "a.jxl"
    _jxl_stub(src)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "a.tif").write_bytes(b"existing")

    captured = {}
    monkeypatch.setattr(dec, "emit_summary_json",
                        lambda *a, **k: captured.update(k))
    monkeypatch.setattr(dec, "collect_multipage_groups",
                        lambda jxls: {src: [(src, 0, False, False, 0, False, None)]})
    monkeypatch.setattr(dec, "OVERWRITE", False)
    monkeypatch.setattr(dec, "FORCE_NONE_MODE", False)
    monkeypatch.setattr(dec, "_group_conflicts", [])
    monkeypatch.setattr(dec, "_check_external_tools", lambda *a, **k: None)
    monkeypatch.setattr(dec, "_warn_if_libjxl_too_old", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_decoder.py", str(src_dir), str(out_dir),
                         "--mode", "2", "--dry-run", "--summary-json"])
    dec.main()
    assert captured.get("ok") == 0, captured
    assert captured.get("skipped") == 1, captured
