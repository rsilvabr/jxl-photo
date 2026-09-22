#!/usr/bin/env python3
"""Regressions for the decoder audit round 39 (20260921_audit_consolidated.md,
items 3 / 4 / 20 / 21 / 25 / 26 — all anchored in jxl_tiff_decoder.py):

3.  The smart-sync SKIP branch returned "skipped" without the
    _decode_output_is_ours check, so an ORIGINAL MASTER (no jxlphoto-src
    marker) whose mtime was >= the JXL's was admitted to --delete-skipped and
    the JXL — which is not the decode of that TIFF — was deleted. The skip
    must refuse (NOT skip), and the delete gate must refuse a skipped source
    whose TIFF carries no marker.
4.  _read_multipage_markers_batch was fail-open (empty stdout / rc≠0 /
    exception): the batch fell back to standalone defaults, every page of a
    marked split became a group of one, decoded into perfectly valid TIFFs,
    and the gate deleted the JXLs — losing SubfileType / inherited ICC /
    grayscale / depth. Same fail-closed recipe as the recompressor's
    mpg_complete=False (round 38): a batch whose marker read failed keeps its
    sources for that run.
20. The exiftool call writing the jxlphoto-src/srcsum provenance markers had
    its returncode ignored and sat under a `except: debug` that still
    returned success: the TIFF left without its marker and was read as an
    original master on the next run.
21. The os.replace promoting the beside-final temp ran BEFORE the
    `if not meta_ok` verdict: a metadata-failed output was promoted to the
    final name with a fresh mtime, the next smart-sync run SKIPs it and
    --delete-skipped deletes the JXL holding the only copy of the lost
    metadata. The verdict now comes first and the fresh output is discarded.
25. The dry-run preview lied: it excluded provenance refusals but not the
    master-TIFF refusals nor --matrix (whose gate keeps everything), and
    "kept" counted an incomplete group as 1 instead of its N JXLs.
26. The beside-final temp was named "<uuid>_<name>.tif" — an orphan left by
    an external kill sat beside the final and was adoptable as real input on
    the next run. It now ends in ".tmp" (the v2.1.1 repair-jbrd rule);
    os.replace does not care about the extension.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_tiff_decoder as dec

REPO = Path(__file__).resolve().parent.parent

_HAS_TOOLS = shutil.which("exiftool") is not None
requires_tools = pytest.mark.skipif(
    not _HAS_TOOLS, reason="exiftool not on PATH")


def _jxl_stub(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)


def _tiff(path: Path, value: int = 1000):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.full((16, 16, 3), value, np.uint16),
                     photometric="rgb")


def _make_newer(target: Path, than: Path):
    stamp = than.stat().st_mtime + 100
    os.utime(target, (stamp, stamp))


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


@pytest.fixture(autouse=True)
def _reset_globals():
    dec._reset_abort()
    dec._delete_stats.update({"deleted": 0, "deleted_archived": 0, "kept": 0})
    # getattr, not a bare reference: against the PRE-FIX code the set does not
    # exist, and the point of this suite is that each test fails on its own
    # assertion there, not in a fixture.
    getattr(dec, "_mpg_marker_failures", set()).clear()
    yield
    dec._reset_abort()
    getattr(dec, "_mpg_marker_failures", set()).clear()
    dec.TEMP2_DIR = None
    dec.DELETE_SOURCE = False
    dec.DELETE_SKIPPED = False
    dec.OVERWRITE = "smart"
    dec.USE_MATRIX_MODE = False
    dec.ADD_JPEG_PREVIEW = True


# ===========================================================================
# 3 — the smart-sync skip branch honours the marker, in both directions
# ===========================================================================

def test_sync_skip_refuses_a_master_tiff(tmp_path, monkeypatch):
    """The audit's trigger verbatim: a marker-less master TIFF whose mtime is
    >= the JXL's (re-save, touch, backup restore, cloud sync). Status must be
    "refused", NOT "skipped" — a skip admits the source to --delete-skipped."""
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    final = tmp_path / "a.tif"
    final.write_bytes(b"the original master")
    _make_newer(final, src)          # TIFF newer -> the sync-skip branch

    dec.setup_logger()
    monkeypatch.setattr(dec, "_aborted", lambda: None)
    monkeypatch.setattr(dec, "OVERWRITE", "smart")
    monkeypatch.setattr(dec, "_decode_output_is_ours", lambda p: False)

    result = dec.convert_multipage_jxl_group(
        src, [(src, 0, False, False, 0, False, None)], final, final)
    assert result[1] == "refused", \
        "a marker-less up-to-date TIFF must not be reported as a skip"
    assert final.read_bytes() == b"the original master"


def test_sync_skip_still_skips_its_own_decode(tmp_path, monkeypatch):
    """The marker is the only thing a skip is admitted on: our own decode,
    up to date, keeps skipping exactly as before."""
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    final = tmp_path / "a.tif"
    final.write_bytes(b"our earlier decode")
    _make_newer(final, src)

    dec.setup_logger()
    monkeypatch.setattr(dec, "_aborted", lambda: None)
    monkeypatch.setattr(dec, "OVERWRITE", "smart")
    monkeypatch.setattr(dec, "_decode_output_is_ours", lambda p: True)

    result = dec.convert_multipage_jxl_group(
        src, [(src, 0, False, False, 0, False, None)], final, final)
    assert result[1] == "skipped"
    assert final.read_bytes() == b"our earlier decode"


def test_delete_gate_keeps_a_skipped_source_without_marker(tmp_path, monkeypatch):
    """Belt-and-braces in the gate itself: a skipped source is judged on the
    FILE, and the marker is the part of the file that says whose decode the
    TIFF is. Without it nothing certifies the deletion."""
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    final = tmp_path / "out" / "photo.tif"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"an unrelated master")
    _make_newer(final, src)

    dec.setup_logger()
    monkeypatch.setattr(dec, "DELETE_SOURCE", True)
    monkeypatch.setattr(dec, "DELETE_SKIPPED", True)
    monkeypatch.setattr(dec, "TEMP2_DIR", None)
    monkeypatch.setattr(dec, "_verify_tiff_integrity", lambda p: True)
    monkeypatch.setattr(dec, "_decode_output_is_ours", lambda p: False)
    monkeypatch.setattr(dec, "convert_multipage_jxl_group",
                        lambda m, e, w, f, *a: (str(m), "skipped", str(f)))

    task = {"type": "multi", "main_jxl": src,
            "entries": [(src, 0, False, False, 0, False, None)],
            "ignored_thumbs": [], "final_tiff": final}
    dec.process_group([task], 1)

    assert src.exists(), "a skip was admitted on the strength of a marker-less TIFF"
    assert dec._delete_stats["kept"] == 1


def test_delete_gate_still_deletes_a_proven_skip(tmp_path, monkeypatch):
    """The marker present, the skip proven: deletion works exactly as before."""
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    final = tmp_path / "out" / "photo.tif"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"our earlier decode")
    _make_newer(final, src)

    dec.setup_logger()
    monkeypatch.setattr(dec, "DELETE_SOURCE", True)
    monkeypatch.setattr(dec, "DELETE_SKIPPED", True)
    monkeypatch.setattr(dec, "TEMP2_DIR", None)
    monkeypatch.setattr(dec, "_verify_tiff_integrity", lambda p: True)
    monkeypatch.setattr(dec, "_decode_output_is_ours", lambda p: True)
    monkeypatch.setattr(dec, "convert_multipage_jxl_group",
                        lambda m, e, w, f, *a: (str(m), "skipped", str(f)))

    task = {"type": "multi", "main_jxl": src,
            "entries": [(src, 0, False, False, 0, False, None)],
            "ignored_thumbs": [], "final_tiff": final}
    dec.process_group([task], 1)

    assert not src.exists()


@requires_tools
def test_dry_run_topline_excludes_master_refusals(tmp_path):
    """Item 25(a): the --delete-source preview excluded the provenance
    refusals but not the master-TIFF refusals the real run reports."""
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    final = tmp_path / "a.tif"
    final.write_bytes(b"the original master")
    _make_newer(src, final)          # JXL newer -> the real run REFUSES

    r = _run("jxl_tiff_decoder.py", tmp_path, "--mode", "0", "--dry-run",
             "--delete-source", "--delete-confirm-off")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "would REFUSE |" in r.stdout, r.stdout
    assert "Up to 0 source JXL(s) would be" in r.stdout, \
        f"the topline promised a deletion the real run refuses:\n{r.stdout}"


@requires_tools
def test_dry_run_topline_excludes_up_to_date_master_refusals(tmp_path):
    """FIX 1 (the item-3 mirror the round-39 fix left open): a marker-less
    master TIFF whose mtime is >= the JXL's hits the old `continue` before the
    marker check in the _would_refuse loop, so the --delete-source topline
    promised "Up to 1 ... would be DELETED" while the real run returned
    "refused" and preserved everything."""
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    final = tmp_path / "a.tif"
    final.write_bytes(b"the original master")
    _make_newer(final, src)          # TIFF newer -> the up-to-date direction

    r = _run("jxl_tiff_decoder.py", tmp_path, "--mode", "0", "--dry-run",
             "--delete-source", "--delete-confirm-off")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "would REFUSE |" in r.stdout, \
        f"the up-to-date master was hidden from the refusal preview:\n{r.stdout}"
    assert "Up to 0 source JXL(s) would be" in r.stdout, \
        f"the topline promised a deletion the real run refuses:\n{r.stdout}"


# ===========================================================================
# 4 — a failed marker read keeps the affected sources for the run
# ===========================================================================

def _seed_subprocess(monkeypatch, *, rc=0, stdout="", raise_exc=None):
    def _fake_run(cmd, **kw):
        if raise_exc is not None:
            raise raise_exc
        return _FakeRun(rc=rc, stdout=stdout)
    monkeypatch.setattr(dec, "_get_exiftool_cmd", lambda: "exiftool")
    monkeypatch.setattr(dec.subprocess, "run", _fake_run)


def test_reader_marks_the_batch_on_empty_stdout(tmp_path, monkeypatch):
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    _seed_subprocess(monkeypatch, rc=0, stdout="")
    dec._read_multipage_markers_batch([src])
    assert os.path.normcase(str(src)) in dec._mpg_marker_failures, \
        "an empty marker read must fail closed for the delete gate"


def test_reader_marks_the_batch_on_rc_nonzero(tmp_path, monkeypatch):
    """exiftool exits non-zero on a format-level failure WITHOUT an Error key
    in the JSON (the message goes to stderr with an empty entry), so no file
    of the batch can prove it is standalone rather than unread."""
    a = tmp_path / "a.jxl"
    b = tmp_path / "b.jxl"
    _jxl_stub(a)
    _jxl_stub(b)
    _seed_subprocess(monkeypatch, rc=1, stdout='[{"SourceFile": "%s"}]' % str(b))
    dec._read_multipage_markers_batch([a, b])
    assert dec._mpg_marker_failures, \
        "a partially-failed batch must fail closed for the delete gate"


def test_reader_marks_the_batch_on_exception(tmp_path, monkeypatch):
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    # A timeout raises TimeoutExpired into the reader's except handler.
    _seed_subprocess(monkeypatch, raise_exc=subprocess.TimeoutExpired("exiftool", 120))
    dec._read_multipage_markers_batch([src])
    assert os.path.normcase(str(src)) in dec._mpg_marker_failures


def test_collect_clears_marker_failures(tmp_path, monkeypatch):
    """The verdicts are per-run: a second collect must not inherit a previous
    reader failure (same rule as _incomplete_groups)."""
    dec._mpg_marker_failures.add("stale")
    a = tmp_path / "a.jxl"
    _jxl_stub(a)
    monkeypatch.setattr(dec, "_read_multipage_markers_batch", lambda jxls: {})
    dec.collect_multipage_groups([a])
    assert dec._mpg_marker_failures == set()


def test_marker_failure_keeps_sources_of_fresh_outputs(tmp_path, monkeypatch):
    """The batch failed, the files fell back to standalone defaults, each page
    decoded into its own perfectly valid TIFF — and the gate must still keep
    every source, because no integrity check can tell the split lost a page."""
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    final = tmp_path / "out" / "photo.tif"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"a perfectly valid single-page tiff")

    dec.setup_logger()
    monkeypatch.setattr(dec, "DELETE_SOURCE", True)
    monkeypatch.setattr(dec, "TEMP2_DIR", None)
    monkeypatch.setattr(dec, "_verify_tiff_integrity", lambda p: True)
    monkeypatch.setattr(dec, "convert_multipage_jxl_group",
                        lambda m, e, w, f, *a: (str(m), "ok", str(f)))
    dec._mpg_marker_failures.add(os.path.normcase(str(src)))

    task = {"type": "multi", "main_jxl": src,
            "entries": [(src, 0, False, False, 0, False, None)],
            "ignored_thumbs": [], "final_tiff": final}
    dec.process_group([task], 1)

    assert src.exists(), "a marker-read failure must not delete the source"
    assert dec._delete_stats["kept"] == 1


def test_marker_failure_is_not_inherited_by_the_next_task(tmp_path, monkeypatch):
    """Only groups holding an unread file are retained; a clean file's source
    deletes exactly as before."""
    clean = tmp_path / "clean.jxl"
    _jxl_stub(clean)
    final = tmp_path / "out" / "clean.tif"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"tiff")

    dec.setup_logger()
    monkeypatch.setattr(dec, "DELETE_SOURCE", True)
    monkeypatch.setattr(dec, "TEMP2_DIR", None)
    monkeypatch.setattr(dec, "_verify_tiff_integrity", lambda p: True)
    monkeypatch.setattr(dec, "convert_multipage_jxl_group",
                        lambda m, e, w, f, *a: (str(m), "ok", str(f)))
    dec._mpg_marker_failures.add(os.path.normcase(str(tmp_path / "other.jxl")))

    task = {"type": "multi", "main_jxl": clean,
            "entries": [(clean, 0, False, False, 0, False, None)],
            "ignored_thumbs": [], "final_tiff": final}
    dec.process_group([task], 1)

    assert not clean.exists()


# ===========================================================================
# 20 — a failed provenance marker write is a failure, not a silent ok
# ===========================================================================

def _marker_write_fails(rc=0, raise_exc=None):
    """_run_exiftool_argfile stub: everything succeeds except the provenance
    marker write (recognisable by its Relation+= arguments)."""
    def fake_argfile(args_lines, timeout=60):
        if raise_exc is not None and any(
                str(a).startswith("-XMP-dc:Relation+=") for a in args_lines):
            raise raise_exc
        if rc != 0 and any(
                str(a).startswith("-XMP-dc:Relation+=") for a in args_lines):
            return _FakeRun(rc=rc, stderr="Error: file locked")
        return _FakeRun()
    return fake_argfile


def test_provenance_marker_write_rc_failure_fails_the_copy(monkeypatch, tmp_path):
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    tif = tmp_path / "a.tif"
    tif.write_bytes(b"\x00")
    monkeypatch.setattr(dec, "_run_exiftool_argfile", _marker_write_fails(rc=1))
    assert dec.copy_metadata(src, tif, tmp_path, provenance_sources=[src]) is False, \
        "the TIFF left without its marker — the copy must not report success"


def test_provenance_marker_write_exception_fails_the_copy(monkeypatch, tmp_path):
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    tif = tmp_path / "a.tif"
    tif.write_bytes(b"\x00")
    monkeypatch.setattr(dec, "_run_exiftool_argfile",
                        _marker_write_fails(raise_exc=RuntimeError("argfile vanished")))
    assert dec.copy_metadata(src, tif, tmp_path, provenance_sources=[src]) is False, \
        "the except handler used to log at DEBUG and still return success"


def test_provenance_marker_write_success_still_passes(monkeypatch, tmp_path):
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    tif = tmp_path / "a.tif"
    tif.write_bytes(b"\x00")
    monkeypatch.setattr(dec, "_run_exiftool_argfile",
                        lambda *a, **k: _FakeRun())
    assert dec.copy_metadata(src, tif, tmp_path, provenance_sources=[src]) is True


# ===========================================================================
# 21 — the meta_ok verdict comes BEFORE the promotion
# ===========================================================================

def test_metadata_failure_never_touches_the_preexisting_final(monkeypatch, tmp_path):
    """A metadata-failed output promoted to the final name carries a fresh
    mtime: the next smart-sync run SKIPs it and --delete-skipped deletes the
    JXL holding the only copy of the lost metadata. The verdict now comes
    first; the fresh output is discarded and the old final is untouched."""
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    final = tmp_path / "photo.tif"
    final.write_bytes(b"the previous good tiff")
    _make_newer(src, final)          # JXL newer -> reconvert path

    dec.setup_logger()
    monkeypatch.setattr(dec, "OVERWRITE", "smart")
    monkeypatch.setattr(dec, "_decode_output_is_ours", lambda p: True)
    monkeypatch.setattr(dec, "ADD_JPEG_PREVIEW", False)
    monkeypatch.setattr(dec, "decode_jxl_to_numpy",
                        lambda *a, **k: (np.zeros((8, 8, 3), dtype=np.uint16),
                                         None, "x", "roundtrip"))
    monkeypatch.setattr(dec, "copy_metadata", lambda *a, **k: False)
    monkeypatch.setattr(dec, "cleanup_xmp_icc", lambda *a, **k: None)

    _m, status, _reason = dec.convert_multipage_jxl_group(
        src, [(src, 0, False, False, 0, False, None)], final, final)

    assert status == "error"
    assert final.read_bytes() == b"the previous good tiff", \
        "the metadata-failed output was promoted over the old final"
    assert not list(tmp_path.glob(f"*_{final.name}*")), \
        "the discarded temp was left behind beside the final"


# ===========================================================================
# 26 — the beside-final temp ends in ".tmp", not the final extension
# ===========================================================================

def test_beside_final_temp_is_tmp_suffixed(monkeypatch, tmp_path):
    """An external kill between the write and the os.replace must not leave an
    adoptable "<uuid>_<name>.tif" in a scanned folder (the v2.1.1 repair-jbrd
    rule: ".tmp, not .jxl")."""
    seen = []
    _real_writer = tifffile.TiffWriter

    class _RecordingWriter:
        def __init__(self, path, *a, **k):
            seen.append(Path(path))
            self._w = _real_writer(str(path))

        def write(self, *a, **k):
            return self._w.write(*a, **k)

        def save(self, *a, **k):
            return self._w.save(*a, **k)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return self._w.__exit__(*a)

    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    final = tmp_path / "photo.tif"

    dec.setup_logger()
    monkeypatch.setattr(dec, "OVERWRITE", True)
    monkeypatch.setattr(dec, "ADD_JPEG_PREVIEW", False)
    monkeypatch.setattr(dec.tifffile, "TiffWriter", _RecordingWriter)
    monkeypatch.setattr(dec, "decode_jxl_to_numpy",
                        lambda *a, **k: (np.zeros((8, 8, 3), dtype=np.uint16),
                                         None, "x", "roundtrip"))
    monkeypatch.setattr(dec, "copy_metadata", lambda *a, **k: True)
    monkeypatch.setattr(dec, "cleanup_xmp_icc", lambda *a, **k: None)

    _m, status, _reason = dec.convert_multipage_jxl_group(
        src, [(src, 0, False, False, 0, False, None)], final, final)

    assert status == "ok"
    assert seen, "fixture broken: the TIFF was never written"
    assert seen[0].name.endswith("_photo.tif.tmp"), \
        f"the beside-final temp used an adoptable name: {seen[0].name}"
    assert final.exists(), "the verified temp must still be promoted"
    assert not list(tmp_path.glob("*_photo.tif.tmp")), \
        "the promoted temp was left behind"


# ===========================================================================
# 25 — the dry-run preview mirrors the real gate
# ===========================================================================

@requires_tools
def test_dry_run_delete_skipped_keeps_unproven_groups(tmp_path):
    """Item 3 previewed: an up-to-date marker-less TIFF must not be counted as
    "would DELETE" by the --delete-skipped preview."""
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    final = tmp_path / "a.tif"
    _tiff(final)
    _make_newer(final, src)          # up to date -> the skip direction

    r = _run("jxl_tiff_decoder.py", tmp_path, "--mode", "0", "--dry-run",
             "--delete-source", "--delete-skipped", "--delete-confirm-off")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "and keep 1." in r.stdout, r.stdout
    assert "would DELETE | a.jxl" not in r.stdout, \
        f"the preview promised to delete a source the gate refuses:\n{r.stdout}"


@requires_tools
def test_dry_run_matrix_promises_no_deletions(tmp_path):
    """Item 25(b): --matrix's real gate keeps EVERY source (it decodes through
    PPM and cannot prove alpha was not dropped); the preview must say so."""
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    final = tmp_path / "a.tif"
    _tiff(final)
    _make_newer(final, src)          # up to date -> the skip direction

    r = _run("jxl_tiff_decoder.py", tmp_path, "--mode", "0", "--dry-run",
             "--matrix", "--delete-source", "--delete-skipped",
             "--delete-confirm-off")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "would DELETE NO source JXL(s)" in r.stdout, r.stdout
    assert "would DELETE | a.jxl" not in r.stdout, r.stdout


def test_incomplete_group_kept_count_counts_the_jxls(tmp_path, monkeypatch):
    """Item 25(c): the incomplete-group KEEP used to add 1 to the kept stat
    while N sources survive — the counter must count the JXLs."""
    srcs = [tmp_path / f"scan_page{i}.jxl" for i in range(3)]
    for s in srcs:
        _jxl_stub(s)
    final = tmp_path / "out" / "scan.tif"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"tiff")

    dec.setup_logger()
    monkeypatch.setattr(dec, "DELETE_SOURCE", True)
    monkeypatch.setattr(dec, "DELETE_SKIPPED", True)
    monkeypatch.setattr(dec, "TEMP2_DIR", None)
    monkeypatch.setattr(dec, "_verify_tiff_integrity", lambda p: True)
    monkeypatch.setattr(dec, "_decode_output_is_ours", lambda p: True)
    monkeypatch.setattr(dec, "convert_multipage_jxl_group",
                        lambda m, e, w, f, *a: (str(m), "skipped", str(f)))
    monkeypatch.setattr(dec, "_incomplete_groups",
                        {os.path.normcase(str(srcs[0])): "truncated"})

    task = {"type": "multi", "main_jxl": srcs[0],
            "entries": [(s, i, False, False, 0, False, None)
                        for i, s in enumerate(srcs)],
            "ignored_thumbs": [], "final_tiff": final}
    dec.process_group([task], 1)

    assert all(s.exists() for s in srcs)
    assert dec._delete_stats["kept"] == 3, \
        f"kept={dec._delete_stats['kept']}: the counter must count the 3 surviving JXLs"


# ===========================================================================
# FIX 1 — the dry-run mirror of the item-3 decision
# ===========================================================================

def test_would_skip_group_is_false_for_an_up_to_date_master(tmp_path, monkeypatch):
    """In the up-to-date direction (TIFF newer) the preview used to return
    True on the mtime alone — but the real run refuses a marker-less TIFF
    instead of skipping it, so a True here promised a skip (and, with
    --delete-skipped armed, a deletion) the gate vetoes."""
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    final = tmp_path / "a.tif"
    final.write_bytes(b"master")
    _make_newer(final, src)          # TIFF newer -> the skip direction
    monkeypatch.setattr(dec, "OVERWRITE", "smart")
    monkeypatch.setattr(dec, "_decode_output_is_ours", lambda p: False)
    assert dec._would_skip_group([(src, 0, False, False, 0, False, None)], final) is False
    # And an up-to-date decode of ours is still a skip, exactly as before.
    monkeypatch.setattr(dec, "_decode_output_is_ours", lambda p: True)
    assert dec._would_skip_group([(src, 0, False, False, 0, False, None)], final) is True
