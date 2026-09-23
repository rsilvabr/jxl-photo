#!/usr/bin/env python3
"""Regression tests for round 40 batch B (bug_report_260923.md items #6-#22).

  #6  encoder: a single-page TIFF whose only page is SubFileType=4 (MASK) — and
      page 0 under --multipage-mode ignore — left no `jxlphoto-subfiletype`
      marker, so the decoder wrote SubfileType=0 back. (The end-to-end proof
      with real codecs is in tests/test_audit_round36.py.)
  #7  decoder: a single-FILE run in modes 4/5 anchored the "outside input
      tree" warning on the file's own parent — the output is a sibling of that
      folder BY DESIGN, so every such run warned with nothing to say.
  #9  decoder: _verify_tiff_integrity only decoded the LAST page, so damage
      in an earlier page passed the delete gate. (Evidence test lives in
      tests/test_audit_round30.py next to the #302 fix.)
  #10 recompressor: _preflight_space stat()'d the sources WITHOUT an OSError
      guard in the staging estimate (the first loop has one) — a source that
      vanished between the plan and the estimate escaped main() as a raw
      traceback; convert_one's error handler stat()'ed a vanished source out
      of its own except block.
  #11 transcoder: --provenance silently inert without --delete-source (the
      recompressor's round-39 #413 warning never reached this copy).
  #16 transcoder: the output positional was silently discarded in modes 3-8
      (mode 1 already warned).
  #17 encoder: reorder_jxl_boxes lacked the OverflowError guard its transcoder
      and recompressor copies have for a re-headered size-0 box.
  #18 encoder: no per-run module state was reset, so a second run in the SAME
      process inherited progress counters, discard counters and discard sets.
  #19 recompressor + transcoder: the delete confirmation was charged BEFORE
      the plan was known — a no-TTY re-run of an already-archived folder with
      --delete-source asked for a token it could not answer and exited 3
      forever, deleting nothing.
  #21 --export-marker '' (an explicit "export NOTHING here") was treated as
      absent and silently kept the default marker.
  #22 _parse_encode_params was dead code (superseded by
      _read_encode_params_batch); the tests now exercise the batch reader.

These import only the child scripts and the round-40 test file, so they fail
against an extracted HEAD copy (pre-fix proof) as well as passing here. In a
HEAD extraction the tests for the Tier-1 batch fail for OTHER reasons; run
only this file (and its #9 fixture in tests/test_audit_round30.py) there.
"""

import io
import json
import logging
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_jpeg_transcoder as tr
import jxl_recompressor as rec
import jxl_tiff_encoder as enc
import jxl_tiff_decoder as dec

REPO = Path(__file__).resolve().parent.parent

_JXL_SIG = b"\x00\x00\x00\x0cJXL \r\n\x87\n"


def _jxl_stub(path: Path, payload: bytes = b"\x00" * 64):
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


def _main_with_argv(mod, argv, tmp_path):
    """Run a script's main() in-process with patched argv, capturing stdout.
    Always use an input the script accepts, and default to --dry-run so the
    run cannot write anything."""
    old = sys.argv
    sys.argv = [mod.__name__ if hasattr(mod, "__name__") else "mod"] + [
        str(a) if not isinstance(a, str) else a for a in argv]
    out, err = io.StringIO(), io.StringIO()
    rc = None
    try:
        with redirect_stdout(out), redirect_stderr(err):
            try:
                mod.main()
            except SystemExit as ex:
                rc = getattr(ex, "code", 0)
    finally:
        sys.argv = old
    return out.getvalue() + err.getvalue(), rc


# ---------------------------------------------------------------------------
# #6 — encoder carries the real SubfileType for non-default page-0 pages
# ---------------------------------------------------------------------------

_MASK_EXTRATAGS = [(254, 4, 1, 4, True)]  # SubfileType as a raw tag: 4 = MASK


def _single_page_mask_tiff(path: Path):
    import numpy
    import tifffile
    tifffile.imwrite(path, numpy.zeros((8, 8), dtype=numpy.uint8),
                     photometric="minisblack", extratags=_MASK_EXTRATAGS)
    return path


def test_encoder_ignore_mode_plans_page0_subfiletype(tmp_path, monkeypatch):
    """--multipage-mode ignore hardcoded subfiletype=0 for page 0."""
    monkeypatch.setattr(enc, "MULTIPAGE_TIFF_MODE", "ignore")
    tiff = _single_page_mask_tiff(tmp_path / "mask.tif")
    items = enc.convert_multipage(tiff, tmp_path, mode=0)
    assert len(items) == 1
    _tiff, final_jxl, page_idx, is_thumb, subfiletype, samples = items[0]
    assert (page_idx, is_thumb) == (0, False)
    assert subfiletype == 4, (
        "ignore mode hardcoded page 0's SubfileType to 0: a single-page MASK "
        "TIFF decoded back without its role")


def test_encoder_solo_marker_reached_for_mask_page0(tmp_path):
    """The solo marker block must no longer be gated on `page_idx > 0`."""
    src = (REPO / "jxl_tiff_encoder.py").read_text(encoding="utf-8")
    marker = "if not multipage_group and not STRIP_METADATA and (\n"
    assert marker in src.split("r_solo = _run_exiftool_argfile")[0], (
        "the solo marker block is still gated on page_idx > 0 alone")
    block = src.split("if not multipage_group and not STRIP_METADATA and (")[1]
    block = block[:block.index("r_solo = _run_exiftool_argfile")]
    assert "subfiletype != (1 if is_thumbnail else 0)" in block


def test_encoder_solo_marker_real_run(tmp_path):
    """Real-codec subset of the round-36 test: a mask TIFF encoded directly
    through convert_one must carry the subfiletype marker after the marker
    writes (the full encode/decode round-trip lives in round 36)."""
    tiff = _single_page_mask_tiff(tmp_path / "mask.tif")
    final = tmp_path / "mask.jxl"
    result = enc.convert_one(tiff, final, final, page_idx=0,
                             is_thumbnail=False, subfiletype=4, samples=1)
    assert result[1] == "ok", result
    r = subprocess.run(
        [enc._get_exiftool_cmd(), "-j", "-XMP-dc:Relation", str(final)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60)
    assert "jxlphoto-subfiletype:4" in r.stdout, (
        f"single-page MASK page 0 left no subfiletype marker: {r.stdout} "
        f"{r.stderr}")


# ---------------------------------------------------------------------------
# #7 — decoder single-file modes 4/5 no spurious 'outside input tree'
# ---------------------------------------------------------------------------

def test_decoder_single_file_mode45_no_spurious_outside_tree(tmp_path, caplog):
    src_dir = tmp_path / "photos_JXL"
    src_dir.mkdir()
    jxl = src_dir / "photo.jxl"
    jxl.write_bytes(_JXL_SIG + b"\x00" * 16)
    for mode in (4, 5):
        with caplog.at_level(logging.WARNING, logger="jxl_decode"):
            out = dec.resolve_output(jxl, mode, jxl.parent, single_file=True)
        assert out is not None
        assert out.parent.parent == src_dir.parent
    assert "Output outside input tree" not in caplog.text, caplog.text


def test_decoder_folder_run_still_warns_outside_tree(tmp_path, caplog):
    jxl = tmp_path / "photo.jxl"
    jxl.write_bytes(_JXL_SIG + b"\x00" * 16)
    with caplog.at_level(logging.WARNING, logger="jxl_decode"):
        dec.resolve_output(jxl, 5, tmp_path)
    assert "Output outside input tree" in caplog.text, caplog.text


# ---------------------------------------------------------------------------
# #10 — preflight / error-handler TOCTOU
# ---------------------------------------------------------------------------

def test_preflight_space_survives_a_vanished_source(tmp_path, monkeypatch):
    items = []
    for i in range(3):
        src = tmp_path / f"src{i}.jxl"
        _jxl_stub(src, payload=b"\x00" * (256 * (i + 1)))
        items.append({"src": src, "in_place": False,
                      "final": tmp_path / f"dst{i}.jxl"})
    monkeypatch.setattr(rec, "TEMP2_DIR", None)
    missing = [tmp_path / "nowhere.jxl"]
    # All sources vanish between the plan and the estimate
    for it in items:
        it["src"].unlink()
    rec._preflight_space(items)          # must not raise
    rec._preflight_space([{"src": missing[0], "in_place": False,
                           "final": tmp_path / "x.jxl"},
                          {"in_place": True, "src": missing[0],
                           "final": missing[0]}])


def test_convert_one_error_handler_survives_a_vanished_source(tmp_path, monkeypatch):
    src = tmp_path / "src.jxl"
    _jxl_stub(src)
    final = tmp_path / "dst.jxl"

    def _explode(*args, **kwargs):
        raise OSError("vanished mid-run")

    # Make the codec call fail (the except handler is the code under test),
    # and have the handler's own stat() hit a vanished source.
    monkeypatch.setattr(rec, "subprocess", type(
        "S", (), {"run": staticmethod(_explode),
                  "TimeoutExpired": subprocess.TimeoutExpired}))
    monkeypatch.setattr(rec, "_would_skip", lambda a, b: False)

    class _Vanishing:
        def __getattr__(self, name):
            return getattr(src, name)

        def stat(self, *a, **k):
            raise OSError("vanished mid-run")

        def __str__(self):
            return str(src)

        def __fspath__(self):
            return str(src)

    status = rec.convert_one(_Vanishing(), final.parent / "src.tmp", final,
                             "convert", False, "", "", 0.0)
    assert status[1] == "error"


# ---------------------------------------------------------------------------
# #11 — transcoder --provenance without --delete-source says something
# ---------------------------------------------------------------------------

def test_transcoder_provenance_without_delete_source_warns(tmp_path, monkeypatch):
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8\xff\xd9")
    out, _rc = _main_with_argv(tr, [str(src), "--provenance", "content",
                                    "--dry-run"], tmp_path)
    assert "--provenance has no effect without --delete-source" in out, out


def test_transcoder_provenance_with_delete_source_stays_quiet(tmp_path, monkeypatch):
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8\xff\xd9")
    out, _rc = _main_with_argv(tr, [str(src), "--provenance", "content",
                                    "--dry-run", "--delete-source"], tmp_path)
    assert "--provenance has no effect without --delete-source" not in out


# ---------------------------------------------------------------------------
# #16 — transcoder output positional ignored in modes 3-8 says something
# ---------------------------------------------------------------------------

def test_transcoder_output_positional_mode3_dry_run_warns(tmp_path, monkeypatch):
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8\xff\xd9")
    out, _rc = _main_with_argv(
        tr, [str(src), "--mode", "3", "--dry-run",
             str(tmp_path / "some_folder")], tmp_path)
    assert "output positional is only honored in modes 0 and 2" in out, out


def test_transcoder_output_positional_mode0_stays_quiet(tmp_path, monkeypatch):
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8\xff\xd9")
    out, _rc = _main_with_argv(
        tr, [str(src), "--mode", "0", "--dry-run",
             str(tmp_path / "some_folder")], tmp_path)
    assert "only honored in modes 0 and 2" not in out


# ---------------------------------------------------------------------------
# #17 — encoder reorder_jxl_boxes re-header overflow is a RuntimeError
# ---------------------------------------------------------------------------

def test_encoder_reorder_oversize_size0_box_raises_runtime_error():
    # Same shape as tests/test_round34_transcoder_lows.py:4
    head = (8).to_bytes(4, "big") + b"jxlc" + (0).to_bytes(4, "big") + b"Exif"

    class _HugePayload:
        def __len__(self):
            return 0xFFFFFFF8  # 8 + this == 2**32: past the 4-byte size field

    class _FakeData:
        def __len__(self):
            return len(head)

        def __getitem__(self, item):
            if isinstance(item, slice) and (item.start or 0) >= len(head):
                return _HugePayload()
            return head[item]

    class _FakePath:
        def read_bytes(self):
            return _FakeData()

        def write_bytes(self, data):
            raise AssertionError("the file must not be rewritten")

    with pytest.raises(RuntimeError, match="32-bit"):
        enc.reorder_jxl_boxes(_FakePath())


# ---------------------------------------------------------------------------
# #18 — encoder per-run module state resets in main()
# ---------------------------------------------------------------------------

def test_encoder_module_state_resets_between_runs(monkeypatch, tmp_path):
    # Mutate IN PLACE, so any fix implemented as rebinding (not clearing)
    # would still leave the stale values behind and the test would catch it.
    enc._counter["done"] += 7
    enc._d50_patch_count["applied"] += 2
    enc._d50_patch_count["skipped"] += 1
    enc._d50_patch_count["already_correct"] += 1
    enc._d50_patch_count["skipped_needed"] += 1
    enc._d50_patch_count["applied_already_correct"] += 1
    enc._d50_patch_count["skipped_already_correct"] += 1
    enc._d50_patched_hashes.add("abc")
    enc._multipage_ignored["files"] += 3
    enc._multipage_ignored["pages"] += 5
    enc._thumbnails_dropped["files"] += 2
    enc._thumbnails_dropped["pages"] += 2
    enc._discarded_real_page_sources.add("C:\\gone.tif")
    enc._discarded_thumb_sources.add("C:\\gine.tif")
    enc._discard_warned["count"] += 20
    enc._discard_warned["suppressed"] += 9
    enc._delete_stats["deleted"] += 4
    enc._delete_stats["deleted_archived"] += 1
    enc._delete_stats["kept"] += 3

    monkeypatch.setattr(sys, "argv", [
        "jxl_tiff_encoder.py", str(tmp_path), "--mode", "0", "--dry-run",
        "--workers", "1"])
    rc = None
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            enc.main()
        except SystemExit as ex:
            rc = getattr(ex, "code", 0)
    # The sandbox folder holds no TIFFs, so the run ends with an empty plan.
    assert rc in (0, None), (rc, out.getvalue(), err.getvalue())
    assert enc._counter["done"] == 0, enc._counter
    assert enc._multipage_ignored == {"files": 0, "pages": 0}
    assert enc._thumbnails_dropped == {"files": 0, "pages": 0}
    assert enc._discarded_real_page_sources == set()
    assert enc._discarded_thumb_sources == set()
    assert enc._discard_warned == {"count": 0, "suppressed": 0}
    assert all(enc._d50_patch_count[k] == 0 for k in enc._d50_patch_count)
    assert enc._d50_patched_hashes == set()
    assert enc._delete_stats == {"deleted": 0, "deleted_archived": 0, "kept": 0}


# ---------------------------------------------------------------------------
# #19 — no confirmation charged on an empty / all-skip delete plan
# ---------------------------------------------------------------------------

def _child_run(script: Path, args, tmp_path: Path):
    return subprocess.run([sys.executable, str(script)] + args,
                          capture_output=True, cwd=str(tmp_path),
                          stdin=subprocess.DEVNULL, timeout=300)


def test_recompressor_all_skip_delete_plan_is_not_charged(tmp_path):
    """A no-TTY re-run of an archived folder (--delete-source, no
    --delete-skipped) used to ask for a token it could not answer and exit 3
    forever — with not a single deletion pending."""
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    out_dir = tmp_path / "recompressed_jxl"   # the recompressor's mode-1 folder
    _jxl_stub(out_dir / "photo.jxl")
    _make_newer(out_dir / "photo.jxl", src)
    r = _child_run(REPO / "jxl_recompressor.py",
                   [str(src), "--mode", "1", "--delete-source"], tmp_path)
    out = r.stdout.decode("utf-8", errors="replace") + r.stderr.decode("utf-8", errors="replace")
    assert "SKIP (exists) | photo.jxl" in out
    assert "Aborted by user" not in out
    assert r.returncode == 0, (r.returncode, out[-800:])


def test_recompressor_delete_plan_with_a_real_conversion_still_confirms(tmp_path):
    """Guard: a plan that WOULD convert (no pre-existing output) still asks —
    the prompt only disappears when nothing is deletable."""
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    r = _child_run(REPO / "jxl_recompressor.py",
                   [str(src), "--mode", "1", "--delete-source"], tmp_path)
    out = r.stdout.decode("utf-8", errors="replace")
    # On stdin EOF the prompt declines the deletion — the run aborts with the
    # documented cancellation code.
    assert r.returncode == 3, (r.returncode, out[-800:], r.stderr[-300:])
    assert "Aborted by user" in out


def test_transcoder_all_skip_delete_plan_is_not_charged(tmp_path):
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8\xff\xd9")
    out_dir = tmp_path / "converted_jxl"
    out_dir.mkdir()
    (out_dir / "photo.jxl").write_bytes(_JXL_SIG + b"\x00" * 64)
    _make_newer(out_dir / "photo.jxl", src, delta=200)
    r = _child_run(REPO / "jxl_jpeg_transcoder.py",
                   [str(src), "--mode", "1", "--force-convert",
                    "--delete-source"], tmp_path)
    out = r.stdout.decode("utf-8", errors="replace")
    err = r.stderr.decode("utf-8", errors="replace")
    assert "SKIP (exists) | photo.jpg" in out or "verify" in out.lower() \
        or "SKIP" in out, out[-800:]
    assert "Deletion not confirmed" not in out
    assert err.rfind("Deletion not confirmed") == -1
    assert r.returncode == 0, (r.returncode, out[-800:], err[-300:])


def test_transcoder_delete_plan_with_a_real_conversion_still_confirms(tmp_path):
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8\xff\xd9")
    r = _child_run(REPO / "jxl_jpeg_transcoder.py",
                   [str(src), "--mode", "1", "--delete-source"], tmp_path)
    out = r.stdout.decode("utf-8", errors="replace")
    assert r.returncode == 3, (r.returncode, out[-800:], r.stderr[-300:])
    assert "Deletion not confirmed" in out


# ---------------------------------------------------------------------------
# #21 — --export-marker '' is honored, not swallowed
# ---------------------------------------------------------------------------

def test_transcoder_empty_export_marker_is_kept(tmp_path, monkeypatch):
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8\xff\xd9")
    monkeypatch.setattr(tr, "EXPORT_MARKER", "_EXPORT")
    _out, _rc = _main_with_argv(tr, [str(src), "--mode", "0", "--dry-run",
                                     "--export-marker", ""], tmp_path)
    assert tr.EXPORT_MARKER == "", "empty marker was silently dropped"


def test_recompressor_empty_export_marker_is_kept(tmp_path, monkeypatch):
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    monkeypatch.setattr(rec, "EXPORT_MARKER", "_EXPORT")
    _out, _rc = _main_with_argv(rec, [str(src), "--mode", "1", "--dry-run",
                                      "--export-marker", ""], tmp_path)
    assert rec.EXPORT_MARKER == "", "empty marker was silently dropped"


# ---------------------------------------------------------------------------
# #22 — _parse_encode_params is gone; the batch reader owns the parsing
# (basic parse rules live in tests/test_recompressor.py::TestReadEncodeParamsBatch)
# ---------------------------------------------------------------------------

def test_parse_encode_params_is_dead_code():
    src = (REPO / "jxl_recompressor.py").read_text(encoding="utf-8")
    assert "def _parse_encode_params" not in src, \
        "#22: _parse_encode_params was removed as dead code"


def test_read_encode_params_batch_unions_desc_and_software(monkeypatch):
    """The record can be SPLIT across dc:Description and Software; the reader
    must merge both fields and reconcile gen from the union."""
    run = _FakeRun(json.dumps([{
        "SourceFile": "NOFILE.jxl",
        "Description": "caption | cjxl d=0.2 e=7",
        "Software": "prior tool | cjxl d=0.8 e=6",
    }]))
    monkeypatch.setattr(rec, "subprocess", type(
        "S", (), {"run": staticmethod(lambda *a, **k: run),
                  "TimeoutExpired": subprocess.TimeoutExpired}))
    monkeypatch.setattr(rec, "_get_exiftool_cmd", lambda: "exiftool")
    info = rec._read_encode_params_batch(["NOFILE.jxl"])
    # The LAST entry of the MERGED chain is the current file's parameters.
    assert info["NOFILE.jxl"]["params"] == (0.8, 6)
    assert info["NOFILE.jxl"]["gen"] >= 2
