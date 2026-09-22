#!/usr/bin/env python3
"""Regressions for the transcoder audit round 39 (20260921_audit_consolidated.md,
items 1 / 2 / 6 / 22 / 23 / 24 / 25 / 26 / 27 / 28 / 29 - all anchored in
jxl_jpeg_transcoder.py):

1.  --force-convert -d 0: `_copy_metadata` rewrote the XMP INSIDE a jbrd
    container (exiftool reorders rdf:Description blocks), which breaks
    `djxl --reconstruct_jpeg` while every structural check keeps passing -
    and the convert/auto delete gates never tested reconstruction, so
    --delete-source destroyed the original JPEG. The copy now skips jbrd
    outputs, and both gates require a bit-exact reconstruction (fail-closed
    with djxl < 0.12), the same gate the transcode path has always had.
2.  A JPEG with a large appended trailer (Motion Photo, 200 KB) was refused
    by the structural integrity check (EOI searched only in the last
    64 KiB) even though encode/reconstruction were perfect - and the encode
    gate then deleted the source because its own checks are MD5-based. The
    EOI scan now walks backward from EOF (trailer-after-EOI is legitimate),
    and the decode path accepts a REAL md5 match as proof.
6.  cmd_auto, PNG-only folder + --distance 0 + --delete-source: both
    `has_lossy` and `has_lossless` were False, so nothing asked before
    deleting. d=0 is lossless (cmd_convert's rule), so the weak
    confirm_deletion_jpeg() must run.
22. A jbrd JXL always routes to the lossless transcode decode (JPEG output);
    --format png / --bit-depth were silently ignored. The routing stays -
    a warning names the ignored flags instead.
23. read_md5_db / read_jxl_self_hash_db read raw utf-8 without a try: a torn
    / locked / BOM'd checksums.md5 crashed the run or poisoned the first
    token; md5_of_file sat outside the try in _jxl_binds_to_archived_jpeg.
    All reads now fail CLOSED (None -> KEEP).
24. --rename-from/--rename-to are accepted on the transcode paths but have
    no effect there; the resolver stays without rename parameters (the safe
    fix), and the paths that ignore the flags SAY so.
25. cmd_convert's dry run and cmd_auto reported errors=0 right below a
    "REFUSING N" provenance block; the refusals are charged as errors, the
    way cmd_transcode already did.
26. Beside-final temps wore the real output name ("<32hex>_<name>.<ext>"):
    an orphan left by an external kill sat beside the final, in a folder the
    next run scans, adoptable as real input. They now end in ".tmp" (the
    v2.1.1 repair-jbrd rule); os.replace does not care about the extension,
    and the codecs get the format explicitly where they would otherwise
    infer it from the extension.
27. --repair-jbrd --dry-run wrote its proof copy BESIDE the source before the
    dry_run check - against the promised "nothing is written", and fatal on
    read-only media. The dry run now copies into TEMP_DIR.
28. _auto_repair_copy ignored TEMP_DIR and named its temp ".jxl". It now
    honors TEMP_DIR (system temp fallback) and wears ".tmp".
29. resolve_output_transcode anchored _warn_if_outside on the input root:
    a single-FILE mode 4/5 run warned "Output outside input tree" for every
    legitimate sibling output. The anchor is now input_root.parent.parent
    for file inputs (the encoder's pattern).

The codec tests run the REAL cjxl/djxl/exiftool (skipped when absent) -
rounds 35/36 showed that this class of bug only shows up against real files.

Pre-fix proof: extract HEAD's script and run this file against it -
    git show HEAD:jxl_jpeg_transcoder.py > <tmpdir>/jxl_jpeg_transcoder.py
then run pytest from inside <tmpdir> with a copy of this test file that
imports the old script: the in-process tests fail on the OLD code and pass
on the new (see the run log in the round-39 fix commit).
"""

import argparse
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_jpeg_transcoder as tr

REPO = Path(__file__).resolve().parent.parent

_HAS_CODECS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
requires_codecs = pytest.mark.skipif(
    not _HAS_CODECS, reason="cjxl/djxl/exiftool not on PATH")


class _FakeRun:
    def __init__(self, rc=0, stdout="", stderr=b""):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = rc


_JXL_SIG = b"\x00\x00\x00\x0cJXL \r\n\x87\n"
# A container stub whose jbrd box has_jbrd_box() really finds (box: size 16).
_JBRD_STUB = _JXL_SIG + (16).to_bytes(4, "big") + b"jbrd" + b"\x00" * 8


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
def _reset_globals():
    tr._run_summary.clear()
    yield
    tr.DELETE_SOURCE = False
    tr.DELETE_CONFIRM = True
    tr.TEMP2_DIR = None
    tr.STORE_MD5 = True
    tr._reset_abort()
    tr._delete_stats.update({"deleted": 0, "deleted_archived": 0, "kept": 0})
    tr._auto_repaired.clear()
    tr._run_summary.clear()


def _main_of(argv):
    """Run the script's main() in a fresh interpreter (module globals, print
    capture and exit codes all behave like a real run)."""
    code = ("import sys; sys.argv = ['jxl_jpeg_transcoder.py'] + %r; "
            "sys.path.insert(0, %r); import jxl_jpeg_transcoder as m; "
            "m.main()" % (argv, str(REPO)))
    return subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=180)


# ===========================================================================
# Item 1a - _copy_metadata must not touch a jbrd container (REAL codecs)
# ===========================================================================

@requires_codecs
def test_convert_d0_does_not_copy_metadata_into_a_jbrd_container(tmp_path):
    """The audit reproduced this against real files: after _copy_metadata
    (exiftool -tagsfromfile -xmp:all), a jbrd JXL built from an XMP-bearing
    JPEG no longer reconstructs. The fixed path skips the copy entirely, so
    the reconstruction still passes afterwards."""
    from PIL import Image
    src = tmp_path / "photo.jpg"
    Image.new("RGB", (32, 32), (10, 20, 30)).save(src, quality=90)
    subprocess.run(["exiftool", "-q", "-overwrite_original",
                    "-XMP-dc:Description=first", "-XMP-crs:Version=11.0",
                    str(src)], check=True, capture_output=True)
    out = tmp_path / "out.jxl"
    # d=0 on the convert path: cjxl keeps the jbrd box (lossless_jpeg=1
    # default) - the exact input class of the audit's reproduction.
    status = tr.encode_to_jxl(src, out, out, effort=7, distance=0.0,
                              reconvert_val=False, smart=False)
    assert status[1] == "ok", status
    assert tr.has_jbrd_box(out), "d=0 must keep the jbrd box for this test"
    assert tr._jxl_reconstructs_to(out, src), \
        "metadata copy (or another edit) broke the jbrd reconstruction"


@requires_codecs
def test_convert_lossy_still_copies_metadata(tmp_path, monkeypatch):
    """The guard must not over-fire: a d>0 JPEG->JXL output has NO jbrd and
    still gets its metadata copied (onto the temp it is written to)."""
    from PIL import Image
    src = tmp_path / "photo.jpg"
    Image.new("RGB", (32, 32), (10, 20, 30)).save(src, quality=90)
    out = tmp_path / "out.jxl"
    calls = []
    monkeypatch.setattr(tr, "_copy_metadata",
                        lambda s, d: calls.append(Path(d)) or None)
    status = tr.encode_to_jxl(src, out, out, effort=7, distance=1.0,
                              reconvert_val=False, smart=False)
    assert status[1] == "ok", status
    assert calls and calls[0].name.endswith("_out.jxl.tmp"), \
        "_copy_metadata was skipped on a jbrd-less lossy output"


# ===========================================================================
# Item 1b - the convert and cmd_auto delete gates require bit-exact
# reconstruction for jbrd outputs (the mocked reconstruction FAILS, which is
# the state a corrupted jbrd container leaves behind)
# ===========================================================================

def _convert_gate_run(monkeypatch, tmp_path, tool_version, reconstructs):
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8" + b"\x00" * 16 + b"\xff\xd9")
    final = tmp_path / "out" / "photo.jxl"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(_JBRD_STUB)

    monkeypatch.setattr(tr, "setup_logger", lambda: None)
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "DELETE_CONFIRM", False)
    monkeypatch.setattr(tr, "TEMP2_DIR", None)
    monkeypatch.setattr(tr, "_run_collapses_structure", lambda *a, **k: False)
    monkeypatch.setattr(tr, "encode_to_jxl",
                        lambda s, w, f, *a, **k: (str(s), "ok", str(f), None))
    monkeypatch.setattr(tr, "_verify_file_integrity", lambda p: True)
    monkeypatch.setattr(tr, "_jxl_reconstructs_to", lambda j, s: reconstructs)
    monkeypatch.setattr(tr, "_tool_version", lambda exe: tool_version)
    args = _args(tmp_path, mode=2, output=tmp_path / "out", distance=0.0,
                 delete_source=True)
    args.summary_json = False
    tr.cmd_convert(args, from_jxl=False)


def test_convert_delete_gate_keeps_a_jbrd_output_that_fails_reconstruction(
        monkeypatch, tmp_path, caplog):
    with caplog.at_level("WARNING", logger="jxl_jpeg_transcoder"):
        _convert_gate_run(monkeypatch, tmp_path, (0, 12, 0), False)
    assert tr._delete_stats["kept"] == 1
    assert tr._delete_stats["deleted"] == 0
    assert (tmp_path / "photo.jpg").exists(), \
        "the source was deleted although the reconstruction is broken"
    assert "does not reproduce" in caplog.text


def test_convert_delete_gate_fail_closed_on_djxl_below_012(
        monkeypatch, tmp_path, caplog):
    """With djxl < 0.12 the reconstruction cannot even be tested: an
    unverifiable output must block the deletion, never be waved through."""
    with caplog.at_level("WARNING", logger="jxl_jpeg_transcoder"):
        _convert_gate_run(monkeypatch, tmp_path, (0, 11, 2), False)
    assert tr._delete_stats["kept"] == 1
    assert (tmp_path / "photo.jpg").exists()
    assert "djxl<0.12" in caplog.text


def test_convert_delete_gate_deletes_after_a_proven_reconstruction(
        monkeypatch, tmp_path):
    """Positive control: the gate opens when the reconstruction IS exact."""
    with pytest.MonkeyPatch.context() as mp:
        _convert_gate_run(mp, tmp_path, (0, 12, 0), True)
    assert tr._delete_stats["deleted"] == 1
    assert not (tmp_path / "photo.jpg").exists()


def test_auto_convert_delete_gate_keeps_a_jbrd_output_that_fails_reconstruction(
        monkeypatch, tmp_path, caplog):
    """Same port, cmd_auto's branch inside _process_file_group. Mode 0 puts
    the output beside the source, where the jbrd stub already sits."""
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8" + b"\x00" * 16 + b"\xff\xd9")
    final = tmp_path / "photo.jxl"
    final.write_bytes(_JBRD_STUB)

    monkeypatch.setattr(tr, "setup_logger", lambda: None)
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "TEMP2_DIR", None)
    monkeypatch.setattr(tr, "process_group_convert",
                        lambda *a, **k: ([(str(src), "ok", str(final), None)],
                                         set()))
    monkeypatch.setattr(tr, "_verify_file_integrity", lambda p: True)
    monkeypatch.setattr(tr, "_jxl_reconstructs_to", lambda j, s: False)
    monkeypatch.setattr(tr, "_tool_version", lambda exe: (0, 12, 0))
    with caplog.at_level("WARNING", logger="jxl_jpeg_transcoder"):
        tr._process_file_group([src], _args(tmp_path, mode=0, distance=0.0,
                                            delete_source=True),
                               use_transcode=False, direction="to_jxl")
    assert tr._delete_stats["kept"] == 1
    assert src.exists()
    assert "does not reproduce" in caplog.text


# ===========================================================================
# Item 2 - EOI at the LOGICAL end, not in the last 64 KiB; MD5 proof accepted
# ===========================================================================

def test_jpeg_with_200kb_trailer_passes_integrity(tmp_path):
    """The audit's exact shape: EOI followed by a ~200 KB Motion-Photo-style
    trailer. The old 64 KiB tail window refused it; the backward scan finds
    the EOI wherever it is."""
    p = tmp_path / "motion.jpg"
    p.write_bytes(b"\xff\xd8" + b"\x00" * 128 + b"\xff\xd9"
                  + b"MOTIONPHOTO_TRAILER" * 10500)
    assert tr._verify_file_integrity(p), \
        "a complete JPEG with a large legitimate trailer was refused"


def test_truncated_jpeg_without_eoi_still_fails(tmp_path):
    """The scan must not weaken the gate: no EOI anywhere -> refuse."""
    p = tmp_path / "cut.jpg"
    p.write_bytes(b"\xff\xd8" + b"\x00" * 4096)  # no EOI at all
    assert not tr._verify_file_integrity(p)


def test_eoi_straddling_a_chunk_boundary_is_found(tmp_path):
    """1 MiB chunking: put the EOI exactly on a chunk edge so only the
    1-byte carry logic can find it."""
    body = b"\x00" * ((1 << 20) - 2)
    p = tmp_path / "edge.jpg"
    p.write_bytes(b"\xff\xd8" + body + b"\xff\xd9")
    assert tr._verify_file_integrity(p)


@requires_codecs
def test_decode_of_a_trailer_jpeg_recovers_and_verifies(tmp_path):
    """End-to-end with real codecs: encode a trailer JPEG, decode it back.
    Before the fix the decode worker raised 'output failed the integrity
    check' even though the recovery was bit-exact."""
    from PIL import Image
    src = tmp_path / "src.jpg"
    Image.new("RGB", (48, 48), (1, 2, 3)).save(src, quality=92)
    src.write_bytes(src.read_bytes() + b"TRAILER" * 30000)  # ~210 KB
    out = tmp_path / "src.jxl"
    status = tr.encode_one_transcode(src, out, out, False, 7, False)
    assert status[1] == "ok", status
    assert tr._jxl_reconstructs_to(out, src)

    final = tmp_path / "back.jpg"
    res = tr.decode_one_transcode(out, final, final, verify=True,
                                  reconvert_val=False, smart=False)
    assert res[1] == "ok", res
    assert final.exists()
    assert tr.md5_of_file(final) == tr.md5_of_file(src), \
        "the recovered JPEG is not bit-exact"


# ===========================================================================
# Item 6 - cmd_auto: PNG-only plan at d=0 asks the WEAK confirmation
# ===========================================================================

def _auto_confirm_run(monkeypatch, tmp_path, distance):
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    tr.setup_logger()
    called = {"lossy": 0, "jpeg": 0}

    def _lossy():
        called["lossy"] += 1
        return True

    def _jpeg():
        called["jpeg"] += 1
        return True

    monkeypatch.setattr(tr, "confirm_deletion_lossy", _lossy)
    monkeypatch.setattr(tr, "confirm_deletion_jpeg", _jpeg)
    monkeypatch.setattr(tr, "DELETE_SOURCE", False)  # restored by the fixture
    monkeypatch.setattr(tr, "DELETE_CONFIRM", True)
    monkeypatch.setattr(tr, "process_group_convert",
                        lambda *a, **k: ([], set()))
    monkeypatch.setattr(tr, "process_group_transcode", lambda *a, **k: [])
    tr.cmd_auto(_args(tmp_path, distance=distance, delete_source=True))
    return called


def test_auto_png_only_d0_asks_the_weak_confirmation(monkeypatch, tmp_path):
    """Before the fix NOTHING asked: both has_lossy and has_lossless were
    False for a PNG-only plan at d=0, and --delete-source emptied the
    folder without any confirmation."""
    called = _auto_confirm_run(monkeypatch, tmp_path, 0.0)
    assert called["lossy"] == 0, "d=0 is lossless - no HHMM token"
    assert called["jpeg"] == 1, "d=0 is lossless - the weak confirmation must run"


def test_auto_png_only_d1_still_asks_the_lossy_token(monkeypatch, tmp_path):
    called = _auto_confirm_run(monkeypatch, tmp_path, 1.0)
    assert called["lossy"] == 1
    assert called["jpeg"] == 0


# ===========================================================================
# Item 22 - explicit --format/--bit-depth on a jbrd JXL say they are ignored
# ===========================================================================

def test_main_warns_when_format_is_explicit_on_a_jbrd_jxl(tmp_path):
    jxl = tmp_path / "photo.jxl"
    jxl.write_bytes(_JBRD_STUB)
    r = _main_of([str(jxl), "--format", "png", "--dry-run"])
    out = r.stdout + r.stderr
    assert "--format/--bit-depth are ignored" in out, out
    assert "jbrd" in out


def test_main_warns_when_bit_depth_is_explicit_on_a_jbrd_jxl(tmp_path):
    jxl = tmp_path / "photo.jxl"
    jxl.write_bytes(_JBRD_STUB)
    r = _main_of([str(jxl), "--bit-depth", "16", "--dry-run"])
    assert "--format/--bit-depth are ignored" in (r.stdout + r.stderr)


def test_main_stays_quiet_without_format_on_a_jbrd_jxl(tmp_path):
    jxl = tmp_path / "photo.jxl"
    jxl.write_bytes(_JBRD_STUB)
    r = _main_of([str(jxl), "--dry-run"])
    assert "--format/--bit-depth are ignored" not in (r.stdout + r.stderr)


# ===========================================================================
# Item 23 - checksums.md5 reads fail closed; the self-hash read is shielded
# ===========================================================================

def test_read_md5_db_survives_a_torn_or_locked_db(tmp_path, monkeypatch):
    good = tmp_path / "good.jxl"
    torn = tmp_path / "torn" / "a.jxl"
    bom = tmp_path / "bom" / "a.jxl"
    for p in (torn, bom):
        p.parent.mkdir(parents=True, exist_ok=True)
    tr.store_md5_db(good, "0" * 32)

    tr.store_md5_db(torn, "1" * 32)
    db = torn.parent / tr.CHECKSUMS_FILENAME
    db.write_bytes(b"deadbeef  a.jxl\n\xff\xfe torn second line")  # non-UTF8

    tr.store_md5_db(bom, "2" * 32)
    db2 = bom.parent / tr.CHECKSUMS_FILENAME
    db2.write_bytes(b"\xef\xbb\xbf" + ("2" * 32 + "  a.jxl\n").encode())

    assert tr.read_md5_db(good) == "0" * 32
    assert tr.read_md5_db(torn) is None, "a non-UTF8 db must read as 'no proof'"
    assert tr.read_md5_db(bom) == "2" * 32, "a BOM must not poison the first token"

    def boom(*a, **k):
        raise PermissionError("locked by AV")
    monkeypatch.setattr("builtins.open", boom)
    assert tr.read_md5_db(good) is None, "an unreadable db must read as 'no proof'"


def test_read_jxl_self_hash_db_fail_closed(tmp_path):
    jxl = tmp_path / "a.jxl"
    jxl.parent.mkdir(parents=True, exist_ok=True)
    tr.store_jxl_self_hash_db(jxl, "3" * 32)
    assert tr.read_jxl_self_hash_db(jxl) == "3" * 32
    db = jxl.parent / tr.CHECKSUMS_FILENAME
    db.write_bytes(b"\xff\xfe garbage")  # torn / non-UTF8
    assert tr.read_jxl_self_hash_db(jxl) is None


def test_content_binding_refuses_when_the_self_hash_read_fails(
        tmp_path, monkeypatch):
    """md5_of_file sat OUTSIDE the function's shield: a locked JXL crashed
    the whole run instead of failing THIS proof closed."""
    jxl = tmp_path / "a.jxl"
    jxl.write_bytes(b"\x00" * 64)
    monkeypatch.setattr(tr, "read_jxl_self_hash_db", lambda p: "4" * 32)
    real_md5 = tr.md5_of_file

    def locked_md5(p):
        if Path(p) == jxl:
            raise OSError("locked")
        return real_md5(p)
    monkeypatch.setattr(tr, "md5_of_file", locked_md5)
    assert tr._jxl_binds_to_archived_jpeg(jxl, tmp_path / "x.jpg", None) is False


# ===========================================================================
# Item 24 - the transcode paths SAY that --rename-from/--rename-to is ignored
# ===========================================================================

def test_cmd_transcode_warns_about_ignored_rename_flags(monkeypatch, tmp_path,
                                                        caplog):
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 8 + b"\xff\xd9")
    monkeypatch.setattr(tr, "setup_logger", lambda: None)
    with caplog.at_level("WARNING", logger="jxl_jpeg_transcoder"):
        tr.cmd_transcode(_args(tmp_path, dry_run=True,
                               rename_from="_old", rename_to="_new"))
    assert "ignored for JPEG->JXL transcode" in caplog.text


def test_auto_transcode_group_warns_about_ignored_rename_flags(
        monkeypatch, tmp_path, caplog):
    src = tmp_path / "a.jpg"
    src.write_bytes(b"\xff\xd8" + b"\x00" * 8 + b"\xff\xd9")
    monkeypatch.setattr(tr, "setup_logger", lambda: None)
    # No groups get processed (all outputs exist) - only the warning matters.
    monkeypatch.setattr(tr, "should_process", lambda *a, **k: False)
    with caplog.at_level("WARNING", logger="jxl_jpeg_transcoder"):
        tr._process_file_group([src], _args(tmp_path, dry_run=False,
                                            rename_from="_old",
                                            rename_to="_new"),
                               use_transcode=True)
    assert "ignored for the JPEG->JXL transcode paths" in caplog.text


def test_auto_convert_group_does_not_warn(monkeypatch, tmp_path, caplog):
    """The convert groups DO apply the rename - no false warning there."""
    src = tmp_path / "a.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    monkeypatch.setattr(tr, "setup_logger", lambda: None)
    monkeypatch.setattr(tr, "should_process", lambda *a, **k: False)
    with caplog.at_level("WARNING", logger="jxl_jpeg_transcoder"):
        tr._process_file_group([src], _args(tmp_path, rename_from="_old",
                                            rename_to="_new"),
                               use_transcode=False, direction="to_jxl")
    assert "ignored" not in caplog.text


# ===========================================================================
# Item 25 - convert / auto report the provenance refusals as errors
# ===========================================================================

def test_convert_dry_run_reports_refused_pairs_as_errors(monkeypatch, tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out = out_dir / "a.jxl"
    out.write_bytes(_JXL_SIG + b"\x00" * 32)

    monkeypatch.setattr(tr, "setup_logger", lambda: None)
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "_run_collapses_structure", lambda *a, **k: True)
    monkeypatch.setattr(tr, "_read_source_markers_batch",
                        lambda paths: {str(out): {"src": None, "srcsum": None}})
    args = _args(tmp_path, mode=2, output=out_dir, dry_run=True,
                 delete_source=True)
    args.summary_json = False
    tr.cmd_convert(args, from_jxl=False)
    assert tr._run_summary["errors"] == 1, tr._run_summary
    assert tr._run_summary["dry_run"] is True


def test_auto_dry_run_reports_refused_pairs_as_errors(monkeypatch, tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    out = tmp_path / "a.jxl"   # mode 2, flat into the input root
    out.write_bytes(_JXL_SIG + b"\x00" * 32)

    monkeypatch.setattr(tr, "setup_logger", lambda: None)
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "_run_collapses_structure", lambda *a, **k: True)
    monkeypatch.setattr(tr, "_read_source_markers_batch",
                        lambda paths: {str(out): {"src": None, "srcsum": None}})
    args = _args(tmp_path, mode=2, dry_run=True, delete_source=True,
                 output_suffix="")   # outputs flat into the input root
    tr.cmd_auto(args)
    assert tr._run_summary["errors"] == 1, tr._run_summary
    assert tr._run_summary["dry_run"] is True


# ===========================================================================
# Item 26 - beside-final temps end in ".tmp" and still verify
# ===========================================================================

def test_beside_final_temps_end_in_tmp(monkeypatch, tmp_path):
    """Every beside-final writer must leave no adoptable orphan: the temp
    name ends in the final name PLUS '.tmp', never in the bare extension."""
    seen = []

    def fake_run(cmd, **kw):
        # cjxl: ["cjxl", src, out, ...flags] - output at index 2.
        # djxl/magick: flags first, output always LAST.
        out = Path(cmd[2] if cmd[0] == "cjxl" else cmd[-1])
        seen.append(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        if cmd[0] == "cjxl":
            out.write_bytes(_JXL_SIG + b"\x00" * 32)
        else:
            out.write_bytes(b"\xff\xd8" + b"\x00" * 8 + b"\xff\xd9")
        return _FakeRun()

    monkeypatch.setattr(tr.subprocess, "run", fake_run)
    monkeypatch.setattr(tr, "_tool_version", lambda exe: (0, 12, 0))
    monkeypatch.setattr(tr, "_copy_metadata", lambda *a, **k: None)
    monkeypatch.setattr(tr, "_run_exiftool_argfile", lambda *a, **k: None)
    monkeypatch.setattr(tr, "reorder_jxl_boxes", lambda p: None)
    monkeypatch.setattr(tr, "has_jbrd_box", lambda p: True)
    monkeypatch.setattr(tr, "_verify_file_integrity", lambda p: True)
    tr.setup_logger()

    # 1) transcode encode
    src = tmp_path / "a.jpg"
    src.write_bytes(b"\xff\xd8" + b"\x00" * 8 + b"\xff\xd9")
    final = tmp_path / "a.jxl"
    tr.encode_one_transcode(src, final, final, False, 7, False)
    assert seen[-1].name.endswith("_a.jxl.tmp"), seen[-1]

    # 2) transcode decode
    jxl = tmp_path / "b.jxl"
    jxl.write_bytes(b"\x00" * 16)
    bfinal = tmp_path / "b.jpg"
    tr.decode_one_transcode(jxl, bfinal, bfinal, verify=False,
                            reconvert_val=False, smart=False)
    assert seen[-1].name.endswith("_b.jpg.tmp"), seen[-1]

    # 3) convert encode
    png = tmp_path / "c.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    cfinal = tmp_path / "c.jxl"
    tr.encode_to_jxl(png, cfinal, cfinal, effort=7, distance=1.0,
                     reconvert_val=False, smart=False)
    assert seen[-1].name.endswith("_c.jxl.tmp"), seen[-1]

    # 4) convert decode (direct djxl, no ICC)
    djxl = tmp_path / "d.jxl"
    djxl.write_bytes(b"\x00" * 16)
    dfinal = tmp_path / "d.jpg"
    tr.decode_to_image(djxl, dfinal, dfinal, 90, "jpeg", 8, None, True,
                       False, False)
    assert seen[-1].name.endswith("_d.jpg.tmp"), seen[-1]

    # ...and the final names were all promoted into place.
    for f in (final, bfinal, cfinal, dfinal):
        assert f.exists(), f"{f} was never promoted"


def test_integrity_check_still_verifies_a_tmp_named_output(tmp_path):
    """The .tmp rule is only safe while the integrity check keeps working on
    temps: a complete JPEG/JXL under any of these names must pass, a
    truncated one must fail."""
    ok_jpg = tmp_path / "ok.jpg.tmp"
    ok_jpg.write_bytes(b"\xff\xd8" + b"\x00" * 8 + b"\xff\xd9")
    assert tr._verify_file_integrity(ok_jpg)

    cut_jpg = tmp_path / "cut.jpg.tmp"
    cut_jpg.write_bytes(b"\xff\xd8" + b"\x00" * 8)  # no EOI
    assert not tr._verify_file_integrity(cut_jpg)

    ok_jxl = tmp_path / "ok.jxl.tmp"
    ok_jxl.write_bytes(_JXL_SIG + (8).to_bytes(4, "big") + b"jxlc" + b"\x00" * 8)
    assert tr._verify_file_integrity(ok_jxl)

    garbage = tmp_path / "garbage.tmp"
    garbage.write_bytes(b"not an image")
    assert not tr._verify_file_integrity(garbage), "no false pass for garbage"


@requires_codecs
def test_real_transcode_round_trip_with_tmp_temps(tmp_path):
    """REAL codecs through the fixed workers (no staging): the .tmp temp is
    written by cjxl/djxl, verified and promoted; the outputs reconstruct
    bit-exactly. This is the test the mocked suite cannot express."""
    from PIL import Image
    src = tmp_path / "photo.jpg"
    Image.new("RGB", (40, 40), (9, 99, 199)).save(src, quality=90)
    jxl = tmp_path / "photo.jxl"

    status = tr.encode_one_transcode(src, jxl, jxl, False, 7, False)
    assert status[1] == "ok", status
    assert not list(tmp_path.glob("*.tmp")), "the temp was not cleaned up"

    back = tmp_path / "back.jpg"
    res = tr.decode_one_transcode(jxl, back, back, verify=True,
                                  reconvert_val=False, smart=False)
    assert res[1] == "ok", res
    assert tr.md5_of_file(back) == tr.md5_of_file(src)
    assert not list(tmp_path.glob("*.tmp"))


# ===========================================================================
# Item 27 - --repair-jbrd --dry-run writes its proof copy into TEMP_DIR
# ===========================================================================

def test_repair_dry_run_does_not_write_beside_the_source(monkeypatch, tmp_path):
    tdir = tmp_path / "mytemp"
    monkeypatch.setattr(tr, "TEMP_DIR", str(tdir))
    monkeypatch.setattr(tr, "has_jbrd_box", lambda p: True)
    monkeypatch.setattr(tr, "_jxl_reconstruct_ok", lambda p: False)
    monkeypatch.setattr(tr, "_read_source_markers_batch",
                        lambda paths: {str(tmp_path / "a.jxl"):
                                       {"src": "jxlphoto-src:0" * 2,
                                        "srcsum": None}})
    where = []

    def fake_strip(jxl_path, tmp):
        where.append(Path(tmp))
        tmp.write_bytes(b"copy")
        return ("0" * 32, "markers removable; reconstruction then succeeds")
    monkeypatch.setattr(tr, "_strip_markers_proving_repair", fake_strip)

    src = tmp_path / "a.jxl"
    src.write_bytes(b"\x00" * 64)
    args = types.SimpleNamespace(input=src, dry_run=True, summary_json=False)
    rc = tr.cmd_repair_jbrd(args)
    assert rc == 0
    assert not list(tmp_path.glob("*.tmp")), \
        "the dry run wrote beside the source"
    assert where and where[0].parent == tdir, \
        f"the dry-run proof copy did not go to TEMP_DIR: {where}"
    assert where[0].name.endswith("_repair_a.tmp")


def test_repair_real_run_still_writes_beside_the_source(monkeypatch, tmp_path):
    """The real repair must keep its beside-the-source copy (os.replace needs
    the same volume) - only the dry run moved to TEMP_DIR."""
    monkeypatch.setattr(tr, "TEMP_DIR", None)
    monkeypatch.setattr(tr, "has_jbrd_box", lambda p: True)
    monkeypatch.setattr(tr, "_jxl_reconstruct_ok", lambda p: False)
    monkeypatch.setattr(tr, "_read_source_markers_batch",
                        lambda paths: {str(tmp_path / "a.jxl"):
                                       {"src": "jxlphoto-src:0" * 2,
                                        "srcsum": None}})
    where = []

    def fake_strip(jxl_path, tmp):
        where.append(Path(tmp))
        tmp.write_bytes(b"copy")
        return ("0" * 32, "ok")
    monkeypatch.setattr(tr, "_strip_markers_proving_repair", fake_strip)

    src = tmp_path / "a.jxl"
    src.write_bytes(b"\x00" * 64)
    args = types.SimpleNamespace(input=src, dry_run=False, summary_json=False)
    tr.cmd_repair_jbrd(args)
    assert where and where[0].parent == tmp_path, \
        "the real repair must stage beside the source"


# ===========================================================================
# Item 28 - _auto_repair_copy honors TEMP_DIR and wears ".tmp"
# ===========================================================================

def test_auto_repair_copy_honors_tempdir_and_tmp_suffix(monkeypatch, tmp_path):
    tdir = tmp_path / "tdir"
    tdir.mkdir()
    monkeypatch.setattr(tr, "TEMP_DIR", str(tdir))

    def fake_strip(jxl_path, tmp):
        assert tmp.suffix == ".tmp", f"temp must wear .tmp, got {tmp.name}"
        tmp.write_bytes(b"repaired")
        return ("0" * 32, "ok")
    monkeypatch.setattr(tr, "_strip_markers_proving_repair", fake_strip)

    jxl = tmp_path / "photo.jxl"
    jxl.write_bytes(b"\x00" * 64)
    copy = tr._auto_repair_copy(jxl)
    assert copy is not None
    assert copy.parent.parent == tdir, f"TEMP_DIR ignored: {copy.parent}"
    assert copy.suffix == ".tmp"
    shutil.rmtree(copy.parent, ignore_errors=True)


def test_auto_repair_copy_falls_back_to_the_system_temp(monkeypatch, tmp_path):
    monkeypatch.setattr(tr, "TEMP_DIR", None)
    monkeypatch.setattr(tr, "_strip_markers_proving_repair",
                        lambda j, t: ("0" * 32, "ok"))
    jxl = tmp_path / "photo.jxl"
    jxl.write_bytes(b"\x00" * 64)
    copy = tr._auto_repair_copy(jxl)
    assert copy is not None and copy.suffix == ".tmp"
    shutil.rmtree(copy.parent, ignore_errors=True)


# ===========================================================================
# Item 29 - single-FILE mode 4/5 does not warn "Output outside input tree"
# ===========================================================================

def test_resolve_output_transcode_single_file_does_not_warn(tmp_path, caplog):
    f = tmp_path / "JPEG" / "photo.jpg"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"\xff\xd8\xff\xd9")   # must EXIST: is_file() picks the anchor
    for mode in (4, 5):
        with caplog.at_level("WARNING", logger="jxl_jpeg_transcoder"):
            out = tr.resolve_output_transcode(f, mode, f, decode=False,
                                              single_file=True)
        assert out is not None
        assert "Output outside input tree" not in caplog.text, \
            f"mode {mode}: false warning for a legitimate single-file sibling"


def test_resolve_output_transcode_folder_mode_still_warns(tmp_path, caplog):
    root = tmp_path / "JPEG"
    root.mkdir(parents=True, exist_ok=True)
    f = root / "photo.jpg"      # a file AT the input root
    f.write_bytes(b"\xff\xd8\xff\xd9")
    with caplog.at_level("WARNING", logger="jxl_jpeg_transcoder"):
        tr.resolve_output_transcode(f, 4, root, decode=False)
    assert "Output outside input tree" in caplog.text, \
        "the legitimate warning for a root-level file must survive"
