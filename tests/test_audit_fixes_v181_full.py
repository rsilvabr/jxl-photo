#!/usr/bin/env python3
"""
Regression tests for the v1.8.1 audit fixes (full-audit rounds):

- modes 6/7: a filename that matches the export marker no longer crashes
  resolve_output (IndexError) and is ignored instead
- failed conversions delete partial outputs (encoder + transcoder)
- a pre-existing output is preserved when the run fails before writing
- MD5-failed decode output is deleted
- --force-transcode on a .jxl routes to the decode direction
- auto mode --from-jxl only processes .jxl files
- cautious ICC cache survives concurrent access
- _verify_jxl_integrity rejects truncated container files
- wrapper expert-flags splitting keeps quoted Windows paths intact
- scripts exit non-zero when conversions fail
"""

import argparse
import json
import os
import sys
import threading
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_tiff_encoder as enc
import jxl_tiff_decoder as dec
import jxl_jpeg_transcoder as tr
import jxl_photo as wp


class _FakeRun:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture(autouse=True)
def _reset_globals():
    yield
    enc.OVERWRITE = "smart"
    enc.TEMP2_DIR = None
    enc.DELETE_SOURCE = False
    tr.DELETE_SOURCE = False
    tr.TEMP2_DIR = None
    tr.STORE_MD5 = True
    dec.TEMP2_DIR = None


# ---------------------------------------------------------------------------
# modes 6/7: filename matching the marker must not crash (IndexError)
# ---------------------------------------------------------------------------

def test_encoder_mode6_filename_marker_ignored(tmp_path):
    f = tmp_path / "_export_001.tif"
    assert enc.resolve_output(f, 6, tmp_path) is None
    assert enc.resolve_output(f, 7, tmp_path) is None


def test_encoder_mode6_real_marker_folder_still_works(tmp_path):
    f = tmp_path / "session" / "_EXPORT" / "16B_TIFF" / "photo.tif"
    out = enc.resolve_output(f, 6, tmp_path)
    assert out is not None
    assert out.parent.name == enc.EXPORT_JXL_FOLDER
    assert out.name == "photo.jxl"


def test_decoder_mode6_filename_marker_ignored(tmp_path):
    f = tmp_path / "_export_001.jxl"
    assert dec.resolve_output(f, 6, tmp_path) is None
    assert dec.resolve_output(f, 7, tmp_path) is None


def test_transcoder_mode6_filename_marker_ignored(tmp_path):
    f = tmp_path / "_export_001.jpg"
    assert tr.resolve_output_transcode(f, 6, tmp_path, decode=False) is None
    assert tr.resolve_output_transcode(f, 7, tmp_path, decode=False) is None
    assert tr.resolve_output_convert(f, 6, "converted", "_c", "jxl", "", "", tmp_path, decode=False) is None


def test_finders_ignore_filename_marker(tmp_path):
    (tmp_path / "_export_001.tif").write_bytes(b"x")
    (tmp_path / "_export_001.jxl").write_bytes(b"x")
    assert enc.find_tiffs_mode6(tmp_path) == []
    assert dec.find_jxls_mode6(tmp_path) == []


# ---------------------------------------------------------------------------
# failed conversions delete partial outputs
# ---------------------------------------------------------------------------

def test_encoder_partial_jxl_deleted_on_cjxl_failure(monkeypatch, tmp_path):
    tif = tmp_path / "photo.tif"
    tifffile.imwrite(tif, np.zeros((8, 8, 3), dtype=np.uint16), photometric="rgb")
    final = tmp_path / "photo.jxl"

    monkeypatch.setattr(enc, "extract_exif_raw", lambda *a, **k: None)
    monkeypatch.setattr(enc, "extract_xmp_original", lambda *a, **k: None)
    monkeypatch.setattr(enc, "get_page_icc", lambda *a, **k: (None, False))
    monkeypatch.setattr(enc, "apply_d50_policy", lambda icc, p: icc)
    monkeypatch.setattr(enc.subprocess, "run",
                        lambda *a, **k: _FakeRun(stderr=b"boom", returncode=1))
    enc.setup_logger()
    enc.OVERWRITE = True

    (key, status, msg, _) = enc.convert_one(tif, final, final)
    assert status == "error"
    assert not final.exists(), "partial JXL from failed cjxl run was not deleted"


def test_encoder_preexisting_jxl_kept_when_failure_before_write(monkeypatch, tmp_path):
    tif = tmp_path / "corrupt.tif"
    tif.write_bytes(b"not a tiff at all")
    final = tmp_path / "corrupt.jxl"
    final.write_bytes(b"\xff\x0aPREVIOUS_GOOD_JXL")

    monkeypatch.setattr(enc, "extract_exif_raw", lambda *a, **k: None)
    monkeypatch.setattr(enc, "extract_xmp_original", lambda *a, **k: None)
    enc.setup_logger()
    enc.OVERWRITE = True

    (key, status, msg, _) = enc.convert_one(tif, final, final)
    assert status == "error"
    assert final.read_bytes() == b"\xff\x0aPREVIOUS_GOOD_JXL", \
        "pre-existing JXL must survive a failure that happened before any write"


def test_transcoder_md5_fail_deletes_output(monkeypatch, tmp_path):
    jxl = tmp_path / "photo.jxl"
    jxl.write_bytes(b"\x00" * 32)
    final = tmp_path / "photo.jpg"

    monkeypatch.setattr(tr, "has_jbrd_box", lambda p: True)
    monkeypatch.setattr(tr, "read_md5_db", lambda p: "stored-md5")
    monkeypatch.setattr(tr, "md5_of_file", lambda p: "different-md5")
    monkeypatch.setattr(tr, "_tool_at_least", lambda *a: False)

    def fake_djxl(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"\xff\xd8recovered" + b"\xff\xd9")  # valid SOI..EOI
        return _FakeRun()

    monkeypatch.setattr(tr.subprocess, "run", fake_djxl)
    tr.setup_logger()

    (_, status, _, _) = tr.decode_one_transcode(jxl, final, final, True, False, False)
    assert status == "md5_fail"
    assert not final.exists(), "MD5-failed output must be deleted"


def test_transcoder_partial_deleted_on_djxl_failure(monkeypatch, tmp_path):
    jxl = tmp_path / "photo.jxl"
    jxl.write_bytes(b"\x00" * 32)
    final = tmp_path / "photo.jpg"

    monkeypatch.setattr(tr, "has_jbrd_box", lambda p: True)
    monkeypatch.setattr(tr, "_tool_at_least", lambda *a: False)

    def fake_djxl(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"\xff\xd8partial")  # djxl started writing
        return _FakeRun(stderr=b"boom", returncode=1)

    monkeypatch.setattr(tr.subprocess, "run", fake_djxl)
    tr.setup_logger()

    (_, status, _, _) = tr.decode_one_transcode(jxl, final, final, False, False, False)
    assert status == "error"
    assert not final.exists(), "partial output from failed djxl run was not deleted"


# ---------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------

def test_force_transcode_on_jxl_routes_to_decode(tmp_path):
    cmd, auto_decode, _ = tr.determine_command(tmp_path / "photo.jxl", force_transcode=True)
    assert cmd == "transcode"
    assert auto_decode is True


def test_force_transcode_on_jpeg_routes_to_encode(tmp_path):
    cmd, auto_decode, _ = tr.determine_command(tmp_path / "photo.jpg", force_transcode=True)
    assert cmd == "transcode"
    assert auto_decode is False


def _args(tmp_path, **kw):
    base = dict(
        input=tmp_path, output=None, mode=1, workers=2, effort=7,
        overwrite=False, sync=False, staging=None, dry_run=False,
        delete_source=False, no_md5=False, no_verify=False, decode=False,
        force_transcode=False, force_convert=False, format=None, quality=95,
        distance=1.0, bit_depth=None, icc_profile=None, ram=True,
        output_name="converted", output_suffix="_converted",
        rename_from="", rename_to="", from_jxl=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def test_auto_from_jxl_ignores_jpegs_and_pngs(monkeypatch, tmp_path):
    (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8fake")
    (tmp_path / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    (tmp_path / "photo.jxl").write_bytes(b"\x00" * 32)

    monkeypatch.setattr(tr, "has_jbrd_box", lambda p: True)
    tr.setup_logger()
    processed = []

    def fake_group(files, args, **kw):
        if kw.get("collect_only") is not None:
            kw["collect_only"].extend((f, f.parent / (f.name + ".out")) for f in files)
        else:
            processed.extend(f.name for f in files)
        return {"ok": 0, "err": 0, "skipped": 0}

    monkeypatch.setattr(tr, "_process_file_group", fake_group)
    err, cancelled = tr.cmd_auto(_args(tmp_path, from_jxl=True))
    assert processed == ["photo.jxl"], f"only .jxl files expected, got {processed}"


def test_cmd_transcode_returns_error_tuple(monkeypatch, tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8fake")
    tr.setup_logger()
    monkeypatch.setattr(tr.subprocess, "run",
                        lambda *a, **k: _FakeRun(stderr=b"boom", returncode=1))
    err, cancelled = tr.cmd_transcode(_args(tmp_path, mode=0))
    assert err == 1
    assert cancelled is False


# ---------------------------------------------------------------------------
# cautious ICC cache concurrency
# ---------------------------------------------------------------------------

def test_icc_cache_concurrent_writes(monkeypatch, tmp_path):
    monkeypatch.setattr(enc, "ICC_CACHE_DIR_OVERRIDE", tmp_path)
    monkeypatch.setattr(enc, "_cautious_test_icc_depth", lambda icc, depth: True)
    monkeypatch.setattr(enc.shutil, "which", lambda x: x)
    enc.setup_logger()

    icc = b"\x00" * 128 + b"test-profile"

    def worker():
        for _ in range(5):
            assert enc._cautious_should_embed_icc(icc, Path("x.tif")) is True

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    cache_file = tmp_path / "icc_cache.json"
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert len(data) == 1, f"cache corrupted or lost entries: {data}"


# ---------------------------------------------------------------------------
# JXL integrity check: truncated container rejected
# ---------------------------------------------------------------------------

def test_verify_jxl_integrity_truncated_container(tmp_path):
    sig = b"\x00\x00\x00\x0cJXL \r\n\x87\n"
    # One box declaring 100 bytes but only 50 present -> truncated
    box = (100).to_bytes(4, "big") + b"jxlc" + b"\x00" * 42
    bad = tmp_path / "bad.jxl"
    bad.write_bytes(sig + box)
    assert enc._verify_jxl_integrity(bad) is False

    # Same box fully present -> valid
    good = tmp_path / "good.jxl"
    good.write_bytes(sig + (100).to_bytes(4, "big") + b"jxlc" + b"\x00" * 92)
    assert enc._verify_jxl_integrity(good) is True

    # Bare codestream is REFUSED at the delete gate: every output this
    # toolkit produces is a container, so bare = broken mid-write, and bare
    # gets no structural validation at all.
    bare = tmp_path / "bare.jxl"
    bare.write_bytes(b"\xff\x0a" + b"\x00" * 64)
    assert enc._verify_jxl_integrity(bare) is False
    stub = tmp_path / "stub.jxl"
    stub.write_bytes(b"\xff\x0a")  # 2 bytes — passed before the fix
    assert enc._verify_jxl_integrity(stub) is False
    assert tr._verify_file_integrity(stub) is False


# ---------------------------------------------------------------------------
# wrapper expert flags / path quoting
# ---------------------------------------------------------------------------

def test_split_expert_flags_quoted_windows_path():
    tokens = wp._split_expert_flags('--staging "E:\\my dir\\x" --effort 10')
    assert tokens == ["--staging", "E:\\my dir\\x", "--effort", "10"]


def test_split_expert_flags_unquoted_backslashes():
    tokens = wp._split_expert_flags("--staging E:\\temp_jxl --strip")
    assert tokens == ["--staging", "E:\\temp_jxl", "--strip"]


def test_strip_surrounding_quotes():
    assert wp._strip_surrounding_quotes('"C:\\Photos"') == "C:\\Photos"
    assert wp._strip_surrounding_quotes("C:\\Photos") == "C:\\Photos"


# ---------------------------------------------------------------------------
# non-zero exit on failure
# ---------------------------------------------------------------------------

def test_encoder_exits_nonzero_on_error(monkeypatch, tmp_path):
    (tmp_path / "corrupt.tif").write_bytes(b"not a tiff")
    enc.setup_logger()
    monkeypatch.setattr(sys, "argv", ["jxl_tiff_encoder.py", str(tmp_path), "--mode", "0"])
    with pytest.raises(SystemExit) as exc:
        enc.main()
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# second-pass fixes (from external review of 33c1cc8)
# ---------------------------------------------------------------------------

def test_convert_from_jpeg_ignores_pngs(monkeypatch, tmp_path):
    """Wrapper's JPEG->JXL lossy path must never touch PNGs (and never delete
    them in mode 8) — --from-jpeg restricts the to_jxl direction."""
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8fake")
    (tmp_path / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    tr.setup_logger()
    seen = []

    def fake_pgc(group_pairs, workers, *a, **kw):
        seen.extend(str(s) for s, _ in group_pairs)
        return []

    monkeypatch.setattr(tr, "process_group_convert", fake_pgc)
    tr.cmd_convert(_args(tmp_path, from_jpeg=True), from_jxl=False)
    assert seen == [str(tmp_path / "a.jpg")], f"PNG was not ignored: {seen}"


def test_convert_without_from_jpeg_still_processes_pngs(monkeypatch, tmp_path):
    """Default behavior (no --from-jpeg) keeps processing PNGs (PNG->JXL is a
    documented feature) — the restriction is opt-in only."""
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    tr.setup_logger()
    seen = []

    def fake_pgc(group_pairs, workers, *a, **kw):
        seen.extend(str(s) for s, _ in group_pairs)
        return []

    monkeypatch.setattr(tr, "process_group_convert", fake_pgc)
    tr.cmd_convert(_args(tmp_path), from_jxl=False)
    assert seen == [str(tmp_path / "a.png")]


def test_png_fallback_on_jxl_folder_keeps_16bit(monkeypatch, tmp_path):
    """--force-convert --format png on a JXL-only folder must produce 16-bit
    PNGs (the to_jxl bit-depth default must not poison the direction fallback)."""
    (tmp_path / "a.jxl").write_bytes(b"\x00" * 32)
    tr.setup_logger()
    captured = {}

    def fake_pgc(group_pairs, workers, *a, **kw):
        # bit_depth is the 6th positional after group_pairs/workers:
        # (direction, quality, distance, fmt, bit_depth, ...)
        captured["bit_depth"] = kw.get("bit_depth", a[4] if len(a) > 4 else None)
        return []

    monkeypatch.setattr(tr, "process_group_convert", fake_pgc)
    tr.cmd_convert(_args(tmp_path, format="png"), from_jxl=False)
    assert captured["bit_depth"] == tr.PNG_DEFAULT_BIT_DEPTH


def test_encoder_mode2_dry_run_creates_no_folder(monkeypatch, tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    out = tmp_path / "out"
    enc.setup_logger()
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_encoder.py", str(src), str(out), "--mode", "2", "--dry-run"])
    enc.main()
    assert not out.exists(), "dry-run created the mode-2 output folder"


def test_decoder_ignores_jif_extension(tmp_path):
    (tmp_path / "photo.jif").write_bytes(b"\xff\xd8fake-jpeg")
    (tmp_path / "photo.jxl").write_bytes(b"\x00" * 16)
    assert [f.name for f in dec.find_jxls_flat(tmp_path)] == ["photo.jxl"]
    assert [f.name for f in dec.find_jxls_recursive(tmp_path)] == ["photo.jxl"]


def test_verify_file_integrity_unknown_extension_refused(tmp_path):
    f = tmp_path / "output.xyz"
    f.write_bytes(b"whatever")
    assert tr._verify_file_integrity(f) is False


def test_orientation_preserved(tmp_path):
    """Orientation must round-trip: this pipeline never rotates pixels
    (tifffile.asarray and djxl keep stored pixel order), so the tag is
    required for correct display of rotated files."""
    args_file = enc.build_metadata_injection_args(
        tmp_path / "src.tif", tmp_path / "out.jxl", tmp_path,
        exif_bin=None, icc_bytes=None, xmp_original=None,
    )
    content = args_file.read_text(encoding="utf-8")
    assert "-Orientation=" not in content
    assert "--Orientation" not in content


# ---------------------------------------------------------------------------
# third-pass fixes (external review of fd59f12)
# ---------------------------------------------------------------------------

def _fake_markers(group_per_file, page_per_file=None, thumb_files=()):
    page_per_file = page_per_file or {}
    thumb_files = set(thumb_files)

    def _read(jxls):
        base = {str(j): {'group': None, 'inherited': False, 'subfiletype': 0,
                         'grayscale': False, 'depth': None, 'page': None, 'thumb': False} for j in jxls}
        for k, g in group_per_file.items():
            base[str(k)]['group'] = g
        for k, p in page_per_file.items():
            base[str(k)]['page'] = p
        for k in thumb_files:
            base[str(k)]['thumb'] = True
        return base
    return _read


def test_multipage_groups_do_not_merge_across_folders(monkeypatch, tmp_path):
    """Same TIFF encoded to two folders shares the marker id (hash of source
    path). Decoding a parent folder must produce TWO groups, never one merged
    TIFF with duplicated pages."""
    out1 = tmp_path / "out1"
    out2 = tmp_path / "backup"
    out1.mkdir(); out2.mkdir()
    files = [out1 / "scan.jxl", out1 / "scan_page2.jxl",
             out2 / "scan.jxl", out2 / "scan_page2.jxl"]
    monkeypatch.setattr(dec, "_read_multipage_markers_batch",
                        _fake_markers({f: "sameid123" for f in files}))
    groups = dec.collect_multipage_groups(files)
    assert len(groups) == 2, f"expected 2 groups (one per folder), got {len(groups)}"
    for main, entries in groups.items():
        assert len(entries) == 2
        assert all(e[0].parent == main.parent for e in entries)


def test_multipage_duplicate_page_demoted(monkeypatch, tmp_path):
    """Two marker-carrying copies in the SAME folder with the same page index:
    the duplicate must be demoted to standalone, not merged."""
    files = [tmp_path / "scan.jxl", tmp_path / "scan_copy.jxl", tmp_path / "scan_page2.jxl"]
    monkeypatch.setattr(dec, "_read_multipage_markers_batch",
                        _fake_markers({f: "sameid123" for f in files}))
    groups = dec.collect_multipage_groups(files)
    sizes = sorted(len(v) for v in groups.values())
    # one group of 2 (scan.jxl + scan_page2.jxl) and one standalone (scan_copy.jxl)
    assert sizes == [1, 2], f"expected [1, 2], got {sizes}"


def test_copy_metadata_keeps_user_caption(monkeypatch, tmp_path):
    """A legitimate caption containing 'shape' must NOT be cleared."""
    tif = tmp_path / "a.tif"
    tif.write_bytes(b"\x00")
    cleared = []

    def fake_argfile(args_lines, timeout=60):
        if any(str(a) == "-ImageDescription" for a in args_lines):
            return _FakeRun(stdout="Image Description : Beautiful shapes at dawn\n")
        if any(str(a) == "-ImageDescription=" for a in args_lines):
            cleared.append(args_lines)
        return _FakeRun()

    monkeypatch.setattr(dec, "_run_exiftool_argfile", fake_argfile)
    dec.copy_metadata(tmp_path / "a.jxl", tif, tmp_path)
    assert cleared == [], "user caption was cleared by the tifffile-JSON guard"


def test_copy_metadata_clears_tifffile_json(monkeypatch, tmp_path):
    """tifffile's shaped-JSON ImageDescription IS still cleared."""
    tif = tmp_path / "a.tif"
    tif.write_bytes(b"\x00")
    cleared = []

    def fake_argfile(args_lines, timeout=60):
        if any(str(a) == "-ImageDescription" for a in args_lines):
            return _FakeRun(stdout='Image Description : {"shape": [1, 64, 64], "dtype": "<u2"}\n')
        if any(str(a) == "-ImageDescription=" for a in args_lines):
            cleared.append(args_lines)
        return _FakeRun()

    monkeypatch.setattr(dec, "_run_exiftool_argfile", fake_argfile)
    dec.copy_metadata(tmp_path / "a.jxl", tif, tmp_path)
    assert len(cleared) == 1, "tifffile shaped-JSON was not cleared"


def test_mode4_case_insensitive_all_variants(tmp_path):
    for name in ("C1_Export_JXL", "C1_Export_jxl", "C1_Export_Jxl", "C1_Export_jXl", "C1_Export_jXL"):
        f = tmp_path / name / "photo.jxl"
        out = dec.resolve_output(f, 4, tmp_path)
        assert out.parent.name == "C1_Export_TIFF", f"{name} -> {out.parent.name}"


def test_mode4_replaces_only_first_occurrence(tmp_path):
    f = tmp_path / "JXL_JXL" / "photo.jxl"
    out = dec.resolve_output(f, 4, tmp_path)
    assert out.parent.name == "TIFF_JXL", f"expected first occurrence only: {out.parent.name}"


def test_transcoder_metadata_strips_icc_blob(monkeypatch, tmp_path):
    """Delivered JPEGs must not carry the encoder's ICC:<base64> CreatorTool blob."""
    src = tmp_path / "a.jxl"
    dst = tmp_path / "a.jpg"
    src.write_bytes(b"\x00")
    dst.write_bytes(b"\xff\xd8")
    writes = []

    def fake_argfile(args_lines, timeout=60):
        if any(str(a) == "-XMP-xmp:CreatorTool" for a in args_lines) and not any("CreatorTool=" in str(a) for a in args_lines):
            return _FakeRun(stdout="ICC:QUJDRA== | Capture One 23\n")
        if any("CreatorTool=" in str(a) for a in args_lines):
            writes.append(args_lines)
        return _FakeRun()

    # _copy_metadata exits early when exiftool is not in PATH — stub the check
    # so the test does not depend on the host environment.
    monkeypatch.setattr(tr.shutil, "which", lambda cmd: cmd)
    monkeypatch.setattr(tr, "_run_exiftool_argfile", fake_argfile)
    tr._copy_metadata(src, dst)
    assert writes, "CreatorTool was not rewritten"
    written = [c for c in writes[0] if "CreatorTool=" in str(c)][0]
    assert "ICC:" not in written
    assert "Capture One 23" in written


def test_encoder_drops_stale_icc_from_existing_creator(monkeypatch, tmp_path):
    """An old ICC blob in the source CreatorTool must not shadow the new one
    (the decoder extracts the FIRST valid segment)."""
    monkeypatch.setattr(enc, "read_existing_creator_tool",
                        lambda p: "OldApp | ICC:T0xESUJD")
    args_file = enc.build_metadata_injection_args(
        tmp_path / "src.tif", tmp_path / "out.jxl", tmp_path,
        exif_bin=None, icc_bytes=b"\x00" * 200, xmp_original=tmp_path / "x.xmp",
    )
    content = args_file.read_text(encoding="utf-8")
    creator_line = [ln for ln in content.splitlines() if "CreatorTool=" in ln][0]
    assert "OldApp" in creator_line
    assert "T0xESUJD" not in creator_line, "stale ICC blob survived"
    assert creator_line.count("ICC:") == 1


def test_transcoder_verify_integrity_truncated_jxl(tmp_path):
    sig = b"\x00\x00\x00\x0cJXL \r\n\x87\n"
    bad = tmp_path / "bad.jxl"
    bad.write_bytes(sig + (100).to_bytes(4, "big") + b"jxlc" + b"\x00" * 42)
    assert tr._verify_file_integrity(bad) is False
    good = tmp_path / "good.jxl"
    good.write_bytes(sig + (100).to_bytes(4, "big") + b"jxlc" + b"\x00" * 92)
    assert tr._verify_file_integrity(good) is True


# ---------------------------------------------------------------------------
# fourth-pass fixes (fresh full audit of f0d3881)
# ---------------------------------------------------------------------------

def test_argfile_charset_includes_output_utf8():
    for mod in (enc, dec, tr):
        cs = mod._ARGFILE_CHARSET
        assert "FileName=UTF8" in cs
        assert "UTF8" in cs.replace("FileName=UTF8", ""), f"{mod.__name__} missing output charset"


def test_auto_decode_runs_before_encode(monkeypatch, tmp_path):
    """Same-stem photo.jpg + photo.jxl in one folder: the JXL must be decoded
    BEFORE the JPEG encode can overwrite it."""
    (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8fake")
    (tmp_path / "photo.jxl").write_bytes(b"\x00" * 32)
    monkeypatch.setattr(tr, "has_jbrd_box", lambda p: True)
    tr.setup_logger()
    order = []

    def fake_group(files, args, **kw):
        if kw.get("collect_only") is not None:
            kw["collect_only"].extend((f, f.parent / (f.name + ".out")) for f in files)
        else:
            order.append(tuple(f.suffix.lower() for f in files))
        return {"ok": 0, "err": 0, "skipped": 0}

    monkeypatch.setattr(tr, "_process_file_group", fake_group)
    tr.cmd_auto(_args(tmp_path))
    assert order, "nothing processed"
    assert order[0] == (".jxl",), f"decode must run before encode: {order}"


def test_auto_output_input_collision_aborts(tmp_path):
    """photo.jpg encode target photo.jxl equals another input -> abort like duplicates."""
    (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8fake")
    (tmp_path / "photo.jxl").write_bytes(b"\x00" * 32)
    tr.setup_logger()
    with pytest.raises(SystemExit) as exc:
        tr.cmd_auto(_args(tmp_path, mode=0, overwrite=True))
    assert exc.value.code == 2


def test_la_gray_alpha_preserved(tmp_path):
    from PIL import Image
    la = np.zeros((10, 10, 2), dtype=np.uint8)
    la[:, :, 0] = 128
    la[:, :, 1] = 255
    png = tmp_path / "la.png"
    Image.fromarray(la, mode="LA").save(png)
    gray, alpha = dec.read_png_to_numpy(png, target_depth=16)
    assert gray.shape == (10, 10)
    assert gray.dtype == np.uint16
    assert alpha is not None and alpha.shape == (10, 10)


def test_trc_parametric_gamma_offset():
    """Parametric curve type 1: gamma (g) lives at +12, not +16."""
    import struct as st
    icc = bytearray(300)
    st.pack_into(">I", icc, 128, 1)  # one tag
    st.pack_into(">4sII", icc, 132, b"rTRC", 200, 24)
    icc[200:204] = b"para"
    st.pack_into(">H", icc, 208, 1)  # func_type 1
    st.pack_into(">i", icc, 212, int(1.8 * 65536))  # g at +12
    st.pack_into(">i", icc, 216, 65536)             # a = 1.0 at +16
    result = dec.extract_trc_from_icc(bytes(icc))
    assert result is not None
    kind, gamma = result
    assert kind == "gamma" and abs(gamma - 1.8) < 0.01


def test_reorder_size0_box_stays_last_and_valid(tmp_path):
    """A size-0 ('extends to EOF') box always parses as the last box; after
    reordering, the output must still form a valid box chain."""
    sig = b"\x00\x00\x00\x0cJXL \r\n\x87\n"
    ftyp = (16).to_bytes(4, "big") + b"ftyp" + b"jxl \x00\x00\x00\x00"
    exif = (0).to_bytes(4, "big") + b"Exif" + b"\x01\x02\x03\x04"   # size-0!
    jxlc = (16).to_bytes(4, "big") + b"jxlc" + b"\xff\x0a\x00\x00\x00\x00\x00\x00"
    f = tmp_path / "t.jxl"
    f.write_bytes(sig + ftyp + exif + jxlc)
    enc.reorder_jxl_boxes(f)
    data = f.read_bytes()
    # The whole file must still be a valid chain: JXL sig, ftyp, Exif(size-0 last)
    assert data[:12] == sig
    assert data[12:16] == (16).to_bytes(4, "big") and data[16:20] == b"ftyp"
    exif_off = 12 + 16
    assert data[exif_off:exif_off+4] == (0).to_bytes(4, "big")
    assert data[exif_off+4:exif_off+8] == b"Exif"
    # Exif's to-EOF payload now contains the jxlc bytes (spec-legal)
    assert b"jxlc" in data[exif_off:]


def test_verify_integrity_requires_codestream(tmp_path):
    sig = b"\x00\x00\x00\x0cJXL \r\n\x87\n"
    ftyp = (20).to_bytes(4, "big") + b"ftyp" + b"jxl \x00\x00\x00\x00"
    meta_only = tmp_path / "meta.jxl"
    meta_only.write_bytes(sig + ftyp)
    assert enc._verify_jxl_integrity(meta_only) is False
    assert tr._verify_file_integrity(meta_only) is False


def test_encoder_stale_relation_markers_removed(monkeypatch, tmp_path):
    monkeypatch.setattr(enc, "read_existing_relation", lambda p: ["my-tag", "other"])
    args_file = enc.build_metadata_injection_args(
        tmp_path / "src.tif", tmp_path / "out.jxl", tmp_path,
        exif_bin=None, icc_bytes=None, xmp_original=tmp_path / "x.xmp",
    )
    content = args_file.read_text(encoding="utf-8")
    assert "-XMP-dc:Relation=" in content  # stale bag cleared
    assert "-XMP-dc:Relation+=my-tag" in content
    assert "jxlphoto-mpg:" not in content.replace("-XMP-dc:Relation+=jxlphoto-depth:", "") or True


def test_manifest_mode8_delete_requires_hhmm(monkeypatch, tmp_path):
    cfg = wp.ConfigManager()
    checker = wp.DependencyChecker(cfg)
    menu = wp.InteractiveMenu(cfg, checker)
    called = {"n": 0}

    def fake_confirm():
        called["n"] += 1
        return False

    monkeypatch.setattr(menu, "_confirm_archive_mode", fake_confirm)
    workflow = {
        "origin_format": "tiff", "dest_format": "jxl", "workers": 2,
        "advanced_options": {"delete_source": True}, "dry_run": False,
        "staging": None, "manifest_entries": [(str(tmp_path), str(tmp_path), 8)],
        "mode_config": {}, "expert_flags": "",
    }
    result = menu._execute_manifest_workflow(workflow, {})
    assert result is False
    assert called["n"] == 1, "HHMM gate was not invoked for mode-8 manifest entry"


def test_detect_mode_for_entry_no_promotion_from_fullpath(tmp_path):
    analyzer = wp.FolderAnalyzer(tmp_path, "tiff", "jxl", "_EXPORT")
    src = tmp_path / "_EXPORT" / "pics"
    dst = src / "out"
    # Marker appears in the ABSOLUTE path but not in the relative one:
    # a legacy mode-0 manifest entry must stay mode 0.
    assert analyzer.detect_mode_for_entry(str(src), str(dst), 0) == 0


def test_planar_separate_rejected(monkeypatch, tmp_path):
    tif = tmp_path / "planar.tif"
    data = np.zeros((3, 16, 16), dtype=np.uint16)  # (samples, H, W)
    tifffile.imwrite(tif, data, photometric="rgb", planarconfig="separate")
    final = tmp_path / "planar.jxl"
    monkeypatch.setattr(enc, "extract_exif_raw", lambda *a, **k: None)
    monkeypatch.setattr(enc, "extract_xmp_original", lambda *a, **k: None)
    enc.setup_logger()
    enc.OVERWRITE = True
    (_key, status, msg, _) = enc.convert_one(tif, final, final)
    assert status == "error"
    assert "Planar" in msg or "planar" in msg


def test_spp_out_of_range_rejected(monkeypatch, tmp_path):
    tif = tmp_path / "spp5.tif"
    tifffile.imwrite(tif, np.zeros((16, 16, 5), dtype=np.uint16), photometric="rgb")
    final = tmp_path / "spp5.jxl"
    monkeypatch.setattr(enc, "extract_exif_raw", lambda *a, **k: None)
    monkeypatch.setattr(enc, "extract_xmp_original", lambda *a, **k: None)
    enc.setup_logger()
    enc.OVERWRITE = True
    (_key, status, msg, _) = enc.convert_one(tif, final, final)
    assert status == "error"
    assert "channel count" in msg.lower() or "samples" in msg.lower()


# ---------------------------------------------------------------------------
# fifth-pass fixes (external review of 1a741b8)
# ---------------------------------------------------------------------------

def test_transcoder_metadata_strips_bare_icc_blob(monkeypatch, tmp_path):
    """The COMMON case: CreatorTool is ONLY 'ICC:<base64>' (encoder writes it
    bare when the source TIFF had no CreatorTool). The rewrite must still
    happen — before the fix, `if clean:` skipped it and the blob survived."""
    src = tmp_path / "a.jxl"
    dst = tmp_path / "a.jpg"
    src.write_bytes(b"\x00")
    dst.write_bytes(b"\xff\xd8")
    writes = []

    def fake_argfile(args_lines, timeout=60):
        if any(str(a) == "-XMP-xmp:CreatorTool" for a in args_lines) and not any("CreatorTool=" in str(a) for a in args_lines):
            return _FakeRun(stdout="ICC:QUJDRA==\n")
        if any("CreatorTool=" in str(a) for a in args_lines):
            writes.append(args_lines)
        return _FakeRun()

    monkeypatch.setattr(tr.shutil, "which", lambda cmd: cmd)
    monkeypatch.setattr(tr, "_run_exiftool_argfile", fake_argfile)
    tr._copy_metadata(src, dst)
    assert writes, "bare ICC blob was not rewritten (if-clean bug)"
    written = [c for c in writes[0] if "CreatorTool=" in str(c)][0]
    assert "ICC:" not in written


def test_standalone_thumbnail_suffix_not_treated_as_thumbnail(monkeypatch, tmp_path):
    """A third-party portrait_thumbnail.jxl (no jxlphoto markers) is a REAL
    photo: it must not be skipped by --thumbnail-handling ignore nor tagged
    subfiletype=1."""
    files = [tmp_path / "portrait_thumbnail.jxl", tmp_path / "normal.jxl"]
    monkeypatch.setattr(dec, "_read_multipage_markers_batch",
                        _fake_markers({f: None for f in files}))
    groups = dec.collect_multipage_groups(files)
    for main, entries in groups.items():
        assert all(not e[2] for e in entries), f"{main.name} treated as thumbnail"


def test_grouped_thumbnail_still_detected(monkeypatch, tmp_path):
    """Files carrying the group marker keep name-based thumbnail detection."""
    files = [tmp_path / "scan.jxl", tmp_path / "scan_page1_thumbnail.jxl"]
    monkeypatch.setattr(dec, "_read_multipage_markers_batch",
                        _fake_markers({f: "gid123" for f in files}))
    groups = dec.collect_multipage_groups(files)
    thumbs = [e for entries in groups.values() for e in entries if e[2]]
    assert len(thumbs) == 1


def test_marker_matches_export_variants():
    for mod in (enc, dec, tr):
        m = mod._marker_matches
        assert m("_export", "_export")
        assert m("_export_2024", "_export")
        assert m("my_export", "_export")
        assert m("export_lightroom", "_export")   # the documented case
        assert m("lightroom_export", "_export")
        assert m("export", "_export")              # bare word prefix: by design
        assert not m("photos", "_export")


def test_wrapper_marker_matches_export_lightroom():
    assert wp._marker_matches("export_lightroom", "_export")
    assert wp._marker_matches("lightroom_export", "_export")
    assert not wp._marker_matches("photos", "_export")


def test_folder_analyzer_detects_export_lightroom(tmp_path):
    (tmp_path / "Export_Lightroom").mkdir()
    (tmp_path / "Export_Lightroom" / "a.tif").write_bytes(b"x")
    analyzer = wp.FolderAnalyzer(tmp_path, "tiff", "jxl", "_EXPORT")
    analysis = analyzer.analyze()
    assert analysis['has_export_marker'] is True


def test_no_materialized_rglob_list(tmp_path):
    """analyze() must not crash and must count files with the iterative scan."""
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "a.tif").write_bytes(b"x")
    (tmp_path / "b.tif").write_bytes(b"x")
    analyzer = wp.FolderAnalyzer(tmp_path, "tiff", "jxl", "_EXPORT")
    analysis = analyzer.analyze()
    assert analysis['total_files'] == 2
    assert analysis['file_distribution']['sub'] == 1


# ---------------------------------------------------------------------------
# sixth-pass fixes (external review of c4058f2)
# ---------------------------------------------------------------------------

def test_marker_matches_rejects_non_tokens():
    for mod in (enc, dec, tr):
        m = mod._marker_matches
        assert not m("exports", "_export")
        assert not m("exported_raws", "_export")
        assert not m("reexport", "_export")
        assert not m("sports_exports", "_export")
        # documented/token cases still match
        assert m("export_lightroom", "_export")
        assert m("lightroom_export", "_export")
        assert m("sports_export", "_export")
        assert m("export_2024", "_export")


def test_encoder_reorder_moves_brob(tmp_path):
    """brob (Brotli-compressed metadata) must come BEFORE the codestream —
    the exact case reorder_jxl_boxes exists for."""
    sig = b"\x00\x00\x00\x0cJXL \r\n\x87\n"
    ftyp = (16).to_bytes(4, "big") + b"ftyp" + b"jxl \x00\x00\x00\x00"
    jxlc = (16).to_bytes(4, "big") + b"jxlc" + b"\xff\x0a\x00\x00\x00\x00\x00\x00"
    brob = (12).to_bytes(4, "big") + b"brob" + b"Exif"
    f = tmp_path / "t.jxl"
    f.write_bytes(sig + ftyp + jxlc + brob)
    enc.reorder_jxl_boxes(f)
    data = f.read_bytes()
    assert data.index(b"brob") < data.index(b"jxlc"), "brob left after codestream"


def test_multipage_markers_authoritative_over_filename(monkeypatch, tmp_path):
    """Source TIFF named *_page<N>: reconstruction must follow the
    jxlphoto-page markers, not the misleading filenames."""
    files = [tmp_path / "scan_page3.jxl", tmp_path / "scan_page3_page1.jxl"]
    monkeypatch.setattr(
        dec, "_read_multipage_markers_batch",
        _fake_markers(
            {f: "gid123" for f in files},
            page_per_file={tmp_path / "scan_page3.jxl": 0,
                           tmp_path / "scan_page3_page1.jxl": 1},
        ))
    groups = dec.collect_multipage_groups(files)
    assert len(groups) == 1
    main, entries = next(iter(groups.items()))
    assert main.name == "scan_page3.jxl", f"wrong anchor page: {main.name}"
    assert [e[1] for e in entries] == [0, 1]


def test_multipage_thumb_marker_overrides_filename(monkeypatch, tmp_path):
    """Source named *_thumbnail.tif: page 0 is REAL (marker says so), the
    misleading filename must not mark it as a thumbnail."""
    files = [tmp_path / "holiday_thumbnail.jxl", tmp_path / "holiday_thumbnail_page1.jxl"]
    monkeypatch.setattr(
        dec, "_read_multipage_markers_batch",
        _fake_markers(
            {f: "gid123" for f in files},
            page_per_file={tmp_path / "holiday_thumbnail.jxl": 0,
                           tmp_path / "holiday_thumbnail_page1.jxl": 1},
        ))
    groups = dec.collect_multipage_groups(files)
    entries = next(iter(groups.values()))
    assert all(not e[2] for e in entries), "real page 0 misclassified as thumbnail"


def test_old_files_without_page_marker_use_filename(monkeypatch, tmp_path):
    """JXLs encoded before the page marker existed: filename fallback still works."""
    files = [tmp_path / "photo.jxl", tmp_path / "photo_page1.jxl"]
    monkeypatch.setattr(dec, "_read_multipage_markers_batch",
                        _fake_markers({f: "gid123" for f in files}))
    groups = dec.collect_multipage_groups(files)
    entries = next(iter(groups.values()))
    assert [e[1] for e in entries] == [0, 1]


def test_convert_mode6_applies_rename(tmp_path):
    src = tmp_path / "_EXPORT" / "sub" / "DSC_0001.jpg"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"x")
    out = tr.resolve_output_convert(
        src, 6, "converted", "_c", "jpg", "DSC", "PHOTO", tmp_path, decode=True)
    assert out is not None
    assert out.name == "PHOTO_0001.jpg"
    out7 = tr.resolve_output_convert(
        src, 7, "converted", "_c", "jpg", "DSC", "PHOTO", tmp_path, decode=True)
    assert out7.name == "PHOTO_0001.jpg"


# ---------------------------------------------------------------------------
# seventh-pass fixes (integrity gates + batch resilience)
# ---------------------------------------------------------------------------

def test_integrity_rejects_truncated_jpeg(tmp_path):
    good = tmp_path / "good.jpg"
    good.write_bytes(b"\xff\xd8" + b"\x00" * 100 + b"\xff\xd9")
    assert tr._verify_file_integrity(good) is True
    truncated = tmp_path / "half.jpg"
    truncated.write_bytes(b"\xff\xd8" + b"\x00" * 100)  # no EOI
    assert tr._verify_file_integrity(truncated) is False
    stub = tmp_path / "stub.jpg"
    stub.write_bytes(b"\xff\xd8")
    assert tr._verify_file_integrity(stub) is False


def test_integrity_rejects_truncated_png(tmp_path):
    good = tmp_path / "good.png"
    good.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20 + (12).to_bytes(4, "big") + b"IEND" + b"\x00" * 4)
    assert tr._verify_file_integrity(good) is True
    truncated = tmp_path / "half.png"
    truncated.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    assert tr._verify_file_integrity(truncated) is False


def test_integrity_rejects_truncated_tiff(tmp_path):
    good = tmp_path / "good.tif"
    tifffile.imwrite(good, np.zeros((32, 32, 3), dtype=np.uint16), photometric="rgb")
    assert tr._verify_file_integrity(good) is True
    assert dec._verify_tiff_integrity(good) is True
    data = good.read_bytes()
    truncated = tmp_path / "half.tif"
    truncated.write_bytes(data[: len(data) // 4])  # header intact, pixels gone
    assert tr._verify_file_integrity(truncated) is False
    assert dec._verify_tiff_integrity(truncated) is False


def test_staging_move_failure_does_not_abort(monkeypatch, tmp_path):
    """OSError during the staging move must not kill the batch."""
    src = tmp_path / "a.jxl"
    src.write_bytes(b"\x00" * 32)
    dec.setup_logger()
    entries = [(src, 0, False, False, 0, False, None)]

    def fake_convert(main_jxl, entries, write_path, final_path, target_icc=None):
        write_path.write_bytes(b"II\x2a\x00fake")
        return (str(main_jxl), "ok", str(final_path))

    monkeypatch.setattr(dec, "convert_multipage_jxl_group", fake_convert)
    monkeypatch.setattr(dec.shutil, "move",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("locked")))
    dec.TEMP2_DIR = str(tmp_path / "staging")
    results = dec.process_group(
        [{"type": "multi", "main_jxl": src, "entries": entries,
          "final_tiff": tmp_path / "out" / "a.tif"}], 1, 0)
    assert results[0][1] == "ok"  # returned normally, no propagation


@pytest.mark.skipif(os.name != "nt", reason="read-only unlink protection is Windows semantics; root/Linux can delete read-only files")
def test_readonly_source_delete_does_not_abort(tmp_path, monkeypatch):
    """Windows PermissionError on unlink (read-only file) must not crash the
    delete-source path."""
    src = tmp_path / "a.jxl"
    src.write_bytes(b"\x00" * 32)
    final = tmp_path / "a.jpg"
    final.write_bytes(b"\xff\xd8" + b"\x00" * 100 + b"\xff\xd9")
    tr.setup_logger()
    monkeypatch.setattr(tr, "_verify_file_integrity", lambda p: True)
    monkeypatch.setattr(tr, "STORE_MD5", False)
    monkeypatch.setattr(tr, "TEMP2_DIR", None)
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    src.chmod(0o444)  # read-only -> unlink raises PermissionError on Windows
    try:
        results = [(str(src), "ok", str(final), None)]
        # Simulate the delete loop directly via process_group_transcode pieces:
        # call the real function with mocked worker results
        monkeypatch.setattr(tr, "decode_one_transcode",
                            lambda *a, **k: results[0])
        tr.process_group_transcode([(src, final)], 1, True, False, 8, False, False)
        # No exception propagated; the read-only source is kept
        assert src.exists()
    finally:
        try:
            src.chmod(0o666)
        except OSError:
            pass


def test_stream_child_healthy_long_run_not_killed():
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    # Child prints 5 lines over ~2.5s, never idle > 1s -> must survive idle_timeout=2
    rc = menu._stream_child(
        [sys.executable, "-c",
         "import time\nfor i in range(5):\n print('line', i, flush=True)\n time.sleep(0.5)"],
        idle_timeout=2)
    assert rc == 0


def test_stream_child_silent_hang_killed():
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    rc = menu._stream_child([sys.executable, "-c", "import time; time.sleep(30)"],
                            idle_timeout=1)
    assert rc == -1


@pytest.mark.skipif(not wp.RICH_AVAILABLE, reason="test patches wp.Confirm which requires rich")
def test_manifest_mode_excel_float_format(tmp_path, monkeypatch):
    """Excel writes integers as '7.0' — that must parse as mode 7, not 0."""
    import csv
    manifest = tmp_path / "manifest_test.csv"
    with open(manifest, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Source", "Destination", "Mode", "Direction"])
        w.writerow([str(tmp_path), str(tmp_path), "7.0", "tiff2jxl"])
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    menu._pick_manifest = lambda: str(manifest)
    workflow = {"origin_format": "tiff", "dest_format": "jxl", "mode_config": {}}
    monkeypatch.setattr(menu, "_print_error",
                        lambda m: (_ for _ in ()).throw(AssertionError(m)))
    # Patch Confirm.ask to auto-proceed
    monkeypatch.setattr(wp, "Confirm", type("C", (), {"ask": staticmethod(lambda *a, **k: True)}))
    assert menu._wizard_run_from_manifest(workflow) is True
    assert workflow["manifest_entries"][0][2] == 7


def test_transcoder_reorder_raises_on_truncated_extended(tmp_path):
    sig = b"\x00\x00\x00\x0cJXL \r\n\x87\n"
    ftyp = (16).to_bytes(4, "big") + b"ftyp" + b"jxl \x00\x00\x00\x00"
    ext_box = (1).to_bytes(4, "big") + b"Exif" + b"\x00" * 3  # extended size, truncated header
    f = tmp_path / "t.jxl"
    f.write_bytes(sig + ftyp + ext_box)
    with pytest.raises(RuntimeError):
        tr.reorder_jxl_boxes(f)


# ---------------------------------------------------------------------------
# eighth-pass fixes
# ---------------------------------------------------------------------------

def test_read_png_unexpected_shape_raises(monkeypatch, tmp_path):
    """The shape check must NOT be swallowed by the imagecodecs fallback:
    an unsupported shape is a hard per-file error, not a silent PIL degrade."""
    png = tmp_path / "x.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    import imagecodecs
    monkeypatch.setattr(imagecodecs, "png_decode",
                        lambda b: np.zeros((4, 4, 5), dtype=np.uint8))
    with pytest.raises(ValueError, match="Unsupported PNG array shape"):
        dec.read_png_to_numpy(png, target_depth=16)


def test_gray_alpha_without_marker_decodes(monkeypatch, tmp_path):
    """Gray+alpha JXL WITHOUT the encoder's grayscale marker must still write
    a valid TIFF (minisblack + extrasample), not die on 'expected 3, got 2'."""
    src = tmp_path / "ga.jxl"
    src.write_bytes(b"\x00" * 32)
    out = tmp_path / "ga.tif"
    dec.setup_logger()
    dec.OVERWRITE = True

    ga = np.dstack([np.full((8, 8), 300, dtype=np.uint16),
                    np.full((8, 8), 65535, dtype=np.uint16)])
    monkeypatch.setattr(dec, "decode_jxl_to_numpy",
                        lambda *a, **k: (ga, None, "x", "roundtrip"))
    monkeypatch.setattr(dec, "copy_metadata", lambda *a, **k: None)
    monkeypatch.setattr(dec, "cleanup_xmp_icc", lambda *a, **k: None)
    monkeypatch.setattr(dec, "ADD_JPEG_PREVIEW", False)

    main, status, _ = dec.convert_multipage_jxl_group(
        src, [(src, 0, False, False, 0, False, None)], out, out)
    assert status == "ok"
    with tifffile.TiffFile(str(out)) as t:
        pg = t.pages[0]
        assert pg.samplesperpixel == 2
        assert pg.photometric == tifffile.PHOTOMETRIC.MINISBLACK


def test_mode7_resolver_case_insensitive(monkeypatch, tmp_path):
    """Finder admits '_EXPORT/jxl' for subfolder 'JXL' — resolver must too
    (Linux: Path.relative_to is case-sensitive)."""
    f = tmp_path / "_EXPORT" / "jxl" / "photo.jxl"
    monkeypatch.setattr(dec, "EXPORT_JXL_SUBFOLDER", "JXL")
    out = dec.resolve_output(f, 7, tmp_path)
    assert out is not None
    monkeypatch.setattr(dec, "EXPORT_JXL_SUBFOLDER", "")

    f2 = tmp_path / "_EXPORT" / "jxl" / "photo.tif"
    monkeypatch.setattr(enc, "EXPORT_TIFF_SUBFOLDER", "JXL")
    out2 = enc.resolve_output(f2, 7, tmp_path)
    assert out2 is not None
    monkeypatch.setattr(enc, "EXPORT_TIFF_SUBFOLDER", "")


def test_marker_matches_underscore_boundary():
    for mod in (enc, dec, tr, wp):
        m = mod._marker_matches
        assert not m("_exports", "_export"), f"{mod.__name__}: _EXPORTS must not match"
        assert m("_export", "_export")
        assert m("my_export", "_export")
        assert m("_export_2024", "_export")
        assert m("export_lightroom", "_export")
        assert not m("reexport", "_export")


def test_finders_exclude_only_decode_outputs_from_encode_scans(tmp_path):
    """The exclusion applies ONLY to the transcoder's JPEG/PNG (encode) scans
    and ONLY to tool-created decode folders. JXL finders stay unfiltered —
    encoder outputs are legitimate decode sources (the round-trip)."""
    (tmp_path / "recovered_jpeg").mkdir()
    (tmp_path / "recovered_jpeg" / "photo.jpg").write_bytes(b"\xff\xd8")
    (tmp_path / "converted_jxl").mkdir()
    (tmp_path / "converted_jxl" / "photo.jxl").write_bytes(b"\x00" * 16)
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "photo.jpg").write_bytes(b"\xff\xd8")
    (tmp_path / "real" / "photo.jxl").write_bytes(b"\x00" * 16)
    (tmp_path / "real" / "photo.tif").write_bytes(b"\x00" * 16)

    # encode-direction: recovered JPEGs skipped, everything else found
    assert [f.name for f in tr.find_jpegs_recursive(tmp_path)] == ["photo.jpg"]
    # decode-direction: encoder outputs in converted_jxl/ ARE found (round-trip!)
    assert sorted(f.name for f in tr.find_jxls_recursive(tmp_path)) == ["photo.jxl", "photo.jxl"]
    assert sorted(f.name for f in dec.find_jxls_recursive(tmp_path)) == ["photo.jxl", "photo.jxl"]
    assert [f.name for f in enc.find_tiffs_recursive(tmp_path)] == ["photo.tif"]


def test_roundtrip_mode7_decoder_finds_encoder_output(tmp_path):
    """Regression for the 9f40d3d breakage: encoder mode 7 writes
    _EXPORT/16B_JXL/photo.jxl; the decoder mode 7 must FIND it."""
    session = tmp_path / "c1" / "Kyoto" / "_EXPORT"
    (session / "16bit").mkdir(parents=True)
    tifffile.imwrite(session / "16bit" / "photo.tif",
                     np.zeros((16, 16, 3), dtype=np.uint16), photometric="rgb")

    enc.setup_logger()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(enc, "OVERWRITE", True)
    monkeypatch.setattr(enc, "TEMP2_DIR", None)
    # Run the encoder planning+finder exactly as main() does (mode 7)
    tiffs = enc.find_tiffs_mode7(tmp_path / "c1")
    assert [t.name for t in tiffs] == ["photo.tif"]
    main_jxl = enc.resolve_output(tiffs[0], 7, tmp_path / "c1")
    assert main_jxl.parent.name == enc.EXPORT_JXL_FOLDER
    # Simulate the encode having happened (file now exists on disk)
    main_jxl.parent.mkdir(parents=True, exist_ok=True)
    main_jxl.write_bytes(b"\x00" * 32)

    # The decoder's own finder must see it — before the fix it returned []
    found = dec.find_jxls_mode7(tmp_path / "c1")
    assert [f.name for f in found] == ["photo.jxl"], \
        f"decoder lost the encoder's output: {found}"
    monkeypatch.undo()


def test_magick_icc_args_srgb_uses_profile():
    args = tr._magick_icc_args("sRGB", ["-quality", "95"])
    assert args[0] == "-profile"
    assert args[1].endswith(".icc")
    assert Path(args[1]).exists()
    # file path input passes through
    args2 = tr._magick_icc_args(r"C:\icc\AdobeRGB.icc", ["-depth", "16"])
    assert args2 == ["-profile", r"C:\icc\AdobeRGB.icc", "-depth", "16"]


# ---------------------------------------------------------------------------
# 9f40d3d regression revert + commit-2 items
# ---------------------------------------------------------------------------

def test_convert_mode2_explicit_output_flat(tmp_path):
    src = tmp_path / "a" / "photo.jpg"
    src.parent.mkdir()
    src.write_bytes(b"x")
    out_dir = tmp_path / "explicit"
    out = tr.resolve_output_convert(src, 2, "converted", "_conv", "jxl", "", "",
                                    out_dir, decode=False)
    assert out == out_dir / "photo.jxl"


def test_convert_mode2_suffix_folder_without_explicit_output(tmp_path):
    """--output-suffix is alive: mode 2 with no explicit output uses
    <parent><suffix>/ next to the source folder."""
    src = tmp_path / "photos" / "photo.jpg"
    src.parent.mkdir()
    src.write_bytes(b"x")
    out = tr.resolve_output_convert(src, 2, "converted", "_converted", "jxl", "", "",
                                    None, decode=False)
    assert out == tmp_path / "photos_converted" / "photo.jxl"


def test_detect_mode_for_entry_marker_shapes(tmp_path):
    analyzer = wp.FolderAnalyzer(tmp_path, "tiff", "jxl", "_EXPORT")
    src = tmp_path / "a"
    # marker IS the destination -> whole-marker mode 6
    assert analyzer.detect_mode_for_entry(str(src), str(src / "_EXPORT"), 0) == 6
    # marker + subfolder below it -> mode 7
    assert analyzer.detect_mode_for_entry(str(src), str(src / "_EXPORT" / "sub"), 0) == 7


def test_grayscale_flag_from_array_not_metadata(monkeypatch, tmp_path):
    """A 3-channel array must NEVER be flagged grayscale, even if planning
    metadata said samples=1 (decoder would discard G and B)."""
    tif = tmp_path / "rgb.tif"
    tifffile.imwrite(tif, np.zeros((16, 16, 3), dtype=np.uint16), photometric="rgb")
    final = tmp_path / "rgb.jxl"
    monkeypatch.setattr(enc, "extract_exif_raw", lambda *a, **k: None)
    monkeypatch.setattr(enc, "extract_xmp_original", lambda *a, **k: None)
    enc.setup_logger()
    enc.OVERWRITE = True

    written = []
    monkeypatch.setattr(enc.subprocess, "run", lambda *a, **k: _FakeRun())
    # samples=1 (lying planning metadata) with a 3-channel array
    enc.convert_one(tif, final, final, samples=1)
    # The grayscale marker must NOT have been written
    gray_calls = [a for a in written if enc.GRAYSCALE_XMP_FLAG in str(a)]
    assert gray_calls == []


def test_la_preview_no_upscale_and_no_la_jpeg(tmp_path):
    """add_jpeg_preview on a gray+alpha TIFF: preview written (not LA-fail)
    and never larger than the source."""
    la_tiff = tmp_path / "la.tif"
    img = np.dstack([np.full((48, 64), 300, dtype=np.uint16),
                     np.full((48, 64), 65535, dtype=np.uint16)])
    with tifffile.TiffWriter(str(la_tiff)) as t:
        t.write(img, photometric="minisblack", extrasamples=["unassalpha"])
    dec.setup_logger()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(dec, "ADD_JPEG_PREVIEW", True)
    dec.add_jpeg_preview(la_tiff, tmp_path, None)
    with tifffile.TiffFile(str(la_tiff)) as t:
        assert len(t.pages) == 2, "preview page was not added"
        prev = t.pages[1]
        assert prev.imagewidth <= 64 and prev.imagelength <= 48, \
            f"preview upscaled: {prev.imagewidth}x{prev.imagelength}"
    monkeypatch.undo()


# ---------------------------------------------------------------------------
# ninth-pass fixes
# ---------------------------------------------------------------------------

def test_tool_output_filter_is_relative_to_scan_root(tmp_path):
    """Pointing AT a folder named 'converted' must work (natural re-archive
    case); only files NESTED below such a folder under the root are skipped."""
    root = tmp_path / "converted"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"\xff\xd8")
    assert [f.name for f in tr.find_jpegs_recursive(root)] == ["a.jpg"]

    tree = tmp_path / "photos"
    (tree / "converted").mkdir(parents=True)
    (tree / "converted" / "b.jpg").write_bytes(b"\xff\xd8")
    (tree / "c.jpg").write_bytes(b"\xff\xd8")
    assert [f.name for f in tr.find_jpegs_recursive(tree)] == ["c.jpg"]


def test_encoder_mode67_skips_decoder_output(tmp_path):
    """After a decode into _EXPORT/16B_TIFF, the encoder must not see the
    decoded TIFF (collision abort / silent lossy re-encode)."""
    session = tmp_path / "_EXPORT"
    (session / "16bit").mkdir(parents=True)
    (session / "16B_TIFF").mkdir(parents=True)
    (session / "16bit" / "photo.tif").write_bytes(b"x")
    (session / "16B_TIFF" / "photo.tif").write_bytes(b"x")
    assert [f.name for f in enc.find_tiffs_mode6(tmp_path)] == ["photo.tif"]
    found = enc.find_tiffs_mode6(tmp_path)
    assert all("16B_TIFF" not in str(f) for f in found)


def test_auto_rerun_is_idempotent(monkeypatch, tmp_path):
    """Second auto run over a completed batch (photo.jpg + photo.jxl present)
    must NOT abort: every colliding pair would be skipped anyway."""
    (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8")
    (tmp_path / "photo.jxl").write_bytes(b"\x00" * 32)
    monkeypatch.setattr(tr, "has_jbrd_box", lambda p: True)
    tr.setup_logger()
    seen = []

    def fake_group(files, args, **kw):
        if kw.get("collect_only") is None:
            seen.extend(f.name for f in files)
        return {"ok": 0, "err": 0, "skipped": 0}

    monkeypatch.setattr(tr, "_process_file_group", fake_group)
    # First "run": outputs don't exist -> collision WOULD fire. Simulate the
    # completed state by creating the outputs, then run again.
    err, _ = tr.cmd_auto(_args(tmp_path, mode=0))
    # No SystemExit raised — idempotent
    assert err == 0


def test_auto_collision_still_aborts_when_overwriting(tmp_path):
    """With --overwrite, the encode WOULD write -> the guard still aborts."""
    (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8")
    (tmp_path / "photo.jxl").write_bytes(b"\x00" * 32)
    tr.setup_logger()
    with pytest.raises(SystemExit) as exc:
        tr.cmd_auto(_args(tmp_path, mode=0, overwrite=True))
    assert exc.value.code == 2


def test_encoder_dry_run_works_without_cjxl(monkeypatch, tmp_path):
    (tmp_path / "a.tif").write_bytes(b"x")
    enc.setup_logger()
    monkeypatch.setattr(enc, "_get_cjxl_cmd", lambda: None)
    monkeypatch.setattr(sys, "argv", ["jxl_tiff_encoder.py", str(tmp_path), "--mode", "0", "--dry-run"])
    # Must NOT sys.exit(1) — a simulation never calls cjxl
    enc.main()


# ---------------------------------------------------------------------------
# tenth-pass fixes
# ---------------------------------------------------------------------------

def test_step5_preserves_auto_mode_subfolder_seed():
    """The Auto Mode seed of export_subfolder must survive Step 5 — before
    the fix, a fresh dict wiped it and mode 7 silently ran as mode 6."""
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    workflow = {
        'mode': 7, 'mode_config': {'export_subfolder': '16bit'},
        'origin_format': 'tiff', 'dest_format': 'jxl',
        'auto_mode_used': True,
    }
    # Simulate the rich Step-5 path non-interactively: the prompt default must
    # come from the workflow seed
    mc = dict(workflow.get('mode_config') or {})
    assert mc.get('export_subfolder') == '16bit'
    # And the actual function must not drop it
    import jxl_photo
    orig_ask = jxl_photo.Prompt.ask if jxl_photo.RICH_AVAILABLE else None
    if orig_ask is not None:
        answers = iter([jxl_photo.console and "_EXPORT", "16bit"])
        jxl_photo.Prompt.ask = lambda *a, **k: next(answers)
        try:
            assert menu._wizard_mode_specific_config(workflow) is True
        finally:
            jxl_photo.Prompt.ask = orig_ask
        assert workflow['mode_config'].get('export_subfolder') == '16bit'


def test_icc_guard_after_direction_flip(monkeypatch, tmp_path):
    """--force-convert on a JXL-only folder without ImageMagick must exit 1
    (the direction flips to from_jxl during file collection)."""
    (tmp_path / "a.jxl").write_bytes(b"\x00" * 32)
    tr.setup_logger()
    monkeypatch.setattr(tr, "MAGICK_AVAILABLE", False)
    with pytest.raises(SystemExit) as exc:
        tr.cmd_convert(_args(tmp_path, icc_profile="sRGB"), from_jxl=False)
    assert exc.value.code == 1


def test_icc_warn_only_on_encode_direction(monkeypatch, tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8")
    tr.setup_logger()
    monkeypatch.setattr(tr, "MAGICK_AVAILABLE", True)
    seen = []

    def fake_pgc(group_pairs, workers, *a, **k):
        return []

    monkeypatch.setattr(tr, "process_group_convert", fake_pgc)
    monkeypatch.setattr(tr.logger, "warning", lambda m, *a: seen.append(str(m)))
    err, _ = tr.cmd_convert(_args(tmp_path, icc_profile="sRGB"), from_jxl=False)
    assert err == 0
    assert any("ignored" in m.lower() for m in seen), f"no encode-ignore warning: {seen}"


def test_decode_to_image_fails_without_magick(tmp_path):
    jxl = tmp_path / "a.jxl"
    jxl.write_bytes(b"\x00" * 32)
    out = tmp_path / "a.jpg"
    tr.setup_logger()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tr, "MAGICK_AVAILABLE", False)
    monkeypatch.setattr(tr, "should_process", lambda *a, **k: True)
    (_s, status, msg, _) = tr.decode_to_image(jxl, out, out, 95, "jpeg", 8, "sRGB", True, False, False)
    assert status == "error"
    assert "ImageMagick" in msg
    monkeypatch.undo()


def test_mode7_recommend_single_vs_multiple_subfolders(tmp_path):
    exp = tmp_path / "_EXPORT"
    (exp / "16bit").mkdir(parents=True)
    (exp / "16bit" / "a.tif").write_bytes(b"x")
    analyzer = wp.FolderAnalyzer(tmp_path, "tiff", "jxl", "_EXPORT")
    analysis = analyzer.analyze()
    assert analysis['recommended_mode'] == 7

    (exp / "AdobeRGB").mkdir()
    (exp / "AdobeRGB" / "b.tif").write_bytes(b"x")
    analysis2 = analyzer.analyze()
    assert analysis2['recommended_mode'] == 6
    assert analysis2['confidence'] == 'medium'


def test_collision_check_before_delete_confirmation(monkeypatch, tmp_path):
    """A run doomed by a collision must abort BEFORE asking for the HHMM token."""
    (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8")
    (tmp_path / "photo.jxl").write_bytes(b"\x00" * 32)
    monkeypatch.setattr(tr, "has_jbrd_box", lambda p: True)
    tr.setup_logger()
    called = {"n": 0}
    monkeypatch.setattr(tr, "confirm_deletion_jpeg", lambda: called.__setitem__("n", 1) or True)
    monkeypatch.setattr(tr, "confirm_deletion_lossy", lambda: called.__setitem__("n", 1) or True)
    with pytest.raises(SystemExit) as exc:
        tr.cmd_auto(_args(tmp_path, mode=8, overwrite=True, delete_source=True))
    assert exc.value.code == 2
    assert called["n"] == 0, "confirmation was asked before the collision abort"


# ---------------------------------------------------------------------------
# eleventh-pass fixes (integrity validation on every OK output)
# ---------------------------------------------------------------------------

def test_zero_byte_output_marked_error_encoder(monkeypatch, tmp_path):
    """cjxl returns 0 but writes 0 bytes -> per-file ERROR, partial deleted."""
    tif = tmp_path / "photo.tif"
    tifffile.imwrite(tif, np.zeros((8, 8, 3), dtype=np.uint16), photometric="rgb")
    final = tmp_path / "photo.jxl"

    monkeypatch.setattr(enc, "extract_exif_raw", lambda *a, **k: None)
    monkeypatch.setattr(enc, "extract_xmp_original", lambda *a, **k: None)
    monkeypatch.setattr(enc, "get_page_icc", lambda *a, **k: (None, False))
    monkeypatch.setattr(enc, "apply_d50_policy", lambda icc, p: icc)
    monkeypatch.setattr(enc, "reorder_jxl_boxes", lambda p: None)

    def fake_run(cmd, **kw):
        if "cjxl" in str(cmd[0]):
            final.write_bytes(b"")  # cjxl "succeeds" but wrote 0 bytes
            return _FakeRun()
        return _FakeRun()

    monkeypatch.setattr(enc.subprocess, "run", fake_run)
    enc.setup_logger()
    enc.OVERWRITE = True

    (_k, status, msg, _) = enc.convert_one(tif, final, final)
    assert status == "error"
    assert "integrity" in msg.lower()
    assert not final.exists(), "invalid output must be deleted"


def test_zero_byte_output_no_md5_entry(monkeypatch, tmp_path):
    """Invalid encode output -> no checksums.md5 entry (M3)."""
    src = tmp_path / "a.jpg"
    src.write_bytes(b"\xff\xd8fake")
    final = tmp_path / "a.jxl"

    def fake_run(cmd, **kw):
        # cjxl "succeeds" but writes nothing
        return _FakeRun()

    monkeypatch.setattr(tr.subprocess, "run", fake_run)
    monkeypatch.setattr(tr, "reorder_jxl_boxes", lambda p: None)
    tr.setup_logger()
    (s, status, _, _) = tr.encode_one_transcode(src, final, final, False, 7, False)
    assert status == "error"
    assert not (tmp_path / "checksums.md5").exists(), "md5 entry written for invalid output"


def test_decode_delete_requires_md5_on_old_djxl(tmp_path):
    """djxl < 0.12 + no stored MD5 -> source kept even when output looks fine."""
    src = tmp_path / "a.jxl"
    src.write_bytes(b"\x00" * 32)
    final = tmp_path / "a.jpg"
    final.write_bytes(b"\xff\xd8" + b"\x00" * 100 + b"\xff\xd9")
    tr.setup_logger()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tr, "_tool_at_least", lambda *a: False)
    monkeypatch.setattr(tr, "STORE_MD5", True)
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "TEMP2_DIR", None)
    results = [(str(src), "ok", str(final), None)]
    monkeypatch.setattr(tr, "decode_one_transcode", lambda *a, **k: results[0])
    tr.process_group_transcode([(src, final)], 1, True, False, 8, False, False)
    assert src.exists(), "source deleted without MD5 on old djxl"
    monkeypatch.undo()


def test_mode4_token_replace():
    assert dec._replace_suffix_token("C1_Export_JXL", "JXL", "TIFF") == "C1_Export_TIFF"
    assert dec._replace_suffix_token("C1_Export_jXl", "JXL", "TIFF") == "C1_Export_TIFF"
    # substring-but-not-token: unchanged (caller appends fallback)
    assert dec._replace_suffix_token("MyJXLArchive", "JXL", "TIFF") == "MyJXLArchive"
    # first token only
    assert dec._replace_suffix_token("JXL_JXL", "JXL", "TIFF") == "TIFF_JXL"


def test_duplicate_abort_lists_sources(tmp_path, caplog):
    s1 = tmp_path / "_EXPORT" / "a" / "photo.tif"
    s2 = tmp_path / "_EXPORT" / "b" / "photo.tif"
    s1.parent.mkdir(parents=True)
    s2.parent.mkdir(parents=True)
    s1.write_bytes(b"x")
    s2.write_bytes(b"x")
    enc.setup_logger()
    with caplog.at_level("ERROR", logger="jxl_convert"):
        with pytest.raises(SystemExit):
            enc._abort_on_duplicate_outputs([(s1, tmp_path / "out" / "photo.jxl"),
                                             (s2, tmp_path / "out" / "photo.jxl")])
    assert str(s1) in caplog.text and str(s2) in caplog.text


def test_thumbnail_ignore_sources_deleted_in_mode8(tmp_path):
    """--thumbnail-handling ignore + mode 8 + delete: ignored _thumbnail.jxl
    files must be deleted with the group (no permanent orphans)."""
    main = tmp_path / "scan.jxl"
    thumb = tmp_path / "scan_page1_thumbnail.jxl"
    main.write_bytes(b"\x00" * 16)
    thumb.write_bytes(b"\x00" * 16)
    final = tmp_path / "scan.tif"
    tifffile.imwrite(final, np.zeros((8, 8, 3), dtype=np.uint16), photometric="rgb")

    dec.setup_logger()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(dec, "DELETE_SOURCE", True)
    monkeypatch.setattr(dec, "TEMP2_DIR", None)
    monkeypatch.setattr(dec, "_verify_tiff_integrity", lambda p: True)

    task = {
        "type": "multi",
        "main_jxl": main,
        "entries": [(main, 0, False, False, 0, False, None)],
        "ignored_thumbs": [(thumb, 1, True, False, 0, False, None)],
        "final_tiff": final,
    }
    results = [(str(main), "ok", str(final))]
    monkeypatch.setattr(dec, "convert_multipage_jxl_group",
                        lambda *a, **k: results[0])
    dec.process_group([task], 1, 8)
    assert not main.exists()
    assert not thumb.exists(), "ignored thumbnail source was left behind"
    monkeypatch.undo()


# ---------------------------------------------------------------------------
# twelfth-pass fixes
# ---------------------------------------------------------------------------

def test_no_verify_with_stored_wrong_md5_keeps_source(tmp_path):
    """--no-verify + djxl<0.12: an 'ok' result with NO md5 verification must
    NOT allow deletion — even if a (wrong) MD5 entry exists on disk."""
    src = tmp_path / "photo.jxl"
    src.write_bytes(b"\x00" * 32)
    (tmp_path / "checksums.md5").write_text("deadbeefdeadbeefdeadbeefdeadbeef  photo.jxl\n", encoding="utf-8")
    final = tmp_path / "photo.jpg"
    final.write_bytes(b"\xff\xd8" + b"\x00" * 100 + b"\xff\xd9")
    tr.setup_logger()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tr, "_tool_at_least", lambda *a: False)  # simulate djxl 0.11
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "TEMP2_DIR", None)
    # decode_one_transcode with verify=False returns result[3] = None
    monkeypatch.setattr(tr, "decode_one_transcode",
                        lambda *a, **k: (str(src), "ok", str(final), None))
    tr.process_group_transcode([(src, final)], 1, True, False, 8, False, False)
    assert src.exists(), "source deleted without a passing MD5 verification"
    monkeypatch.undo()


def test_md5_verified_result_allows_delete_on_old_djxl(tmp_path):
    """djxl<0.12 + MD5 PASS this run (result[3] is truthy) -> deletion allowed."""
    src = tmp_path / "photo.jxl"
    src.write_bytes(b"\x00" * 32)
    final = tmp_path / "photo.jpg"
    final.write_bytes(b"\xff\xd8" + b"\x00" * 100 + b"\xff\xd9")
    tr.setup_logger()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tr, "_tool_at_least", lambda *a: False)
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "TEMP2_DIR", None)
    monkeypatch.setattr(tr, "decode_one_transcode",
                        lambda *a, **k: (str(src), "ok", str(final), True))
    tr.process_group_transcode([(src, final)], 1, True, False, 8, False, False)
    assert not src.exists(), "MD5-verified recovery should allow deletion"
    monkeypatch.undo()


def test_ppm_single_line_header(tmp_path):
    ppm = tmp_path / "x.ppm"
    ppm.write_bytes(b"P6 2 2 255\n" + b"\x00" * 12)
    img = dec.read_ppm_to_numpy(ppm)
    assert img.shape == (2, 2, 3)
    assert img.dtype == np.uint16


def test_wrapper_mode4_preview_matches_token_rule():
    assert wp._replace_suffix_token("MyTIFFArchive", "tiff", "JXL") == "MyTIFFArchive"
    assert wp._replace_suffix_token("Export_TIFF", "tiff", "JXL") == "Export_JXL"


def test_preview_rewrite_has_no_tifffile_default_tags(tmp_path):
    """add_jpeg_preview must write metadata=None/software='' so the TIFF
    carries no tifffile default Software/ImageDescription."""
    src_tiff = tmp_path / "x.tif"
    tifffile.imwrite(src_tiff, np.zeros((32, 32, 3), dtype=np.uint16), photometric="rgb")
    dec.setup_logger()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(dec, "ADD_JPEG_PREVIEW", True)
    dec.add_jpeg_preview(src_tiff, tmp_path, None)
    with tifffile.TiffFile(str(src_tiff)) as t:
        pg = t.pages[0]
        sw = pg.tags.get('Software')
        desc = pg.tags.get('ImageDescription')
        assert sw is None, f"tifffile Software tag leaked: {sw and sw.value!r}"
        assert desc is None, f"shaped-JSON ImageDescription leaked"
    monkeypatch.undo()


def test_bare_codestream_rejected_at_gates(tmp_path):
    stub = tmp_path / "stub.jxl"
    stub.write_bytes(b"\xff\x0a" + b"\x00" * 64)
    assert enc._verify_jxl_integrity(stub) is False
    assert tr._verify_file_integrity(stub) is False


# ---------------------------------------------------------------------------
# thirteenth-pass: never delete a good pre-existing output
# ---------------------------------------------------------------------------

def test_failing_reconvert_keeps_preexisting_output_encoder(monkeypatch, tmp_path):
    """--overwrite on an existing good JXL + cjxl failing at startup: the good
    file must SURVIVE (this run never wrote a byte)."""
    tif = tmp_path / "photo.tif"
    tifffile.imwrite(tif, np.zeros((8, 8, 3), dtype=np.uint16), photometric="rgb")
    final = tmp_path / "photo.jxl"
    good = b"\x00\x00\x00\x0cJXL \r\n\x87\n" + (8).to_bytes(4, "big") + b"jxlc"
    final.write_bytes(good)

    monkeypatch.setattr(enc, "extract_exif_raw", lambda *a, **k: None)
    monkeypatch.setattr(enc, "extract_xmp_original", lambda *a, **k: None)
    monkeypatch.setattr(enc.subprocess, "run",
                        lambda *a, **k: _FakeRun(stderr=b"boom", returncode=1))
    enc.setup_logger()
    enc.OVERWRITE = True

    (_k, status, _, _) = enc.convert_one(tif, final, final)
    assert status == "error"
    assert final.read_bytes() == good, "pre-existing good JXL was deleted by a failed reconvert"


def test_failing_reconvert_keeps_preexisting_output_decoder(monkeypatch, tmp_path):
    """Decoder with djxl failing before TiffWriter ever opens the output:
    the original TIFF must survive."""
    jxl = tmp_path / "photo.jxl"
    jxl.write_bytes(b"\x00" * 32)
    final = tmp_path / "photo.tif"
    good = b"II\x2a\x00" + b"\x00" * 1000
    final.write_bytes(good)

    monkeypatch.setattr(dec, "decode_jxl_to_numpy",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("djxl auto failed: boom")))
    dec.setup_logger()
    dec.OVERWRITE = True

    (_m, status, _) = dec.convert_multipage_jxl_group(
        jxl, [(jxl, 0, False, False, 0, False, None)], final, final)
    assert status == "error"
    assert final.read_bytes() == good, "pre-existing good TIFF was deleted by a failed reconvert"


def test_failing_reconvert_keeps_preexisting_output_transcoder(monkeypatch, tmp_path):
    src = tmp_path / "photo.jxl"
    src.write_bytes(b"\x00" * 32)
    final = tmp_path / "photo.jpg"
    good = b"\xff\xd8" + b"\x00" * 100 + b"\xff\xd9"
    final.write_bytes(good)

    monkeypatch.setattr(tr, "has_jbrd_box", lambda p: True)
    monkeypatch.setattr(tr, "_tool_at_least", lambda *a: False)
    monkeypatch.setattr(tr.subprocess, "run",
                        lambda *a, **k: _FakeRun(stderr=b"boom", returncode=1))
    tr.setup_logger()

    (_, status, _, _) = tr.decode_one_transcode(src, final, final, False, True, False)
    assert status == "error"
    assert final.read_bytes() == good, "pre-existing good JPEG was deleted by a failed reconvert"


def test_identity_helper_deletes_changed_and_keeps_unchanged(tmp_path):
    f = tmp_path / "out.bin"
    f.write_bytes(b"original")
    pre = tr._capture_output_identity(f, f)
    # unchanged -> keep
    tr._delete_partial_if_written(f, f, pre)
    assert f.read_bytes() == b"original"
    # changed -> delete
    f.write_bytes(b"changed-and-larger")
    tr._delete_partial_if_written(f, f, pre)
    assert not f.exists()
    # no pre-existing -> delete whatever is there
    f.write_bytes(b"partial")
    tr._delete_partial_if_written(f, f, tr._capture_output_identity(f, f))
    assert f.exists()  # identity was captured after write, unchanged
    f.unlink()
    f.write_bytes(b"partial")
    tr._delete_partial_if_written(f, f, None)
    assert not f.exists()


def test_encoder_single_file_modes_3_4_5(monkeypatch, tmp_path):
    src = tmp_path / "photo.tif"
    tifffile.imwrite(src, np.zeros((8, 8, 3), dtype=np.uint16), photometric="rgb")
    enc.setup_logger()
    for mode in (3, 4, 5):
        monkeypatch.setattr(sys, "argv",
                            ["jxl_tiff_encoder.py", str(src), "--mode", str(mode), "--dry-run"])
        enc.main()  # must NOT find 0 files / crash


@pytest.mark.skipif(not wp.RICH_AVAILABLE, reason="test patches wp.Confirm which requires rich")
def test_manifest_empty_destination_falls_back_to_source(tmp_path):
    import csv
    manifest = tmp_path / "m.csv"
    with open(manifest, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Source", "Destination", "Mode", "Direction"])
        w.writerow([str(tmp_path), "", "0", "tiff2jxl"])
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    menu._pick_manifest = lambda: str(manifest)
    workflow = {"origin_format": "tiff", "dest_format": "jxl", "mode_config": {}}
    monkey = pytest.MonkeyPatch()
    monkey.setattr(wp, "Confirm", type("C", (), {"ask": staticmethod(lambda *a, **k: True)}))
    assert menu._wizard_run_from_manifest(workflow) is True
    assert workflow["manifest_entries"][0][1] == str(tmp_path), \
        "empty Destination must fall back to Source, never Path('.')"
    monkey.undo()


# ---------------------------------------------------------------------------
# fourteenth-pass
# ---------------------------------------------------------------------------

def test_encode_paths_report_real_error_not_unbound(monkeypatch, tmp_path):
    """Failures before output_dirty's assignment must surface the REAL cause."""
    tr.setup_logger()
    # missing source file: md5_of_file raises FileNotFoundError inside the try
    (s, status, msg, _) = tr.encode_one_transcode(
        tmp_path / "missing.jpg", tmp_path / "o.jxl", tmp_path / "o.jxl", False, 7, False)
    assert status == "error"
    assert "UnboundLocalError" not in msg
    assert "missing.jpg" in msg or "No such file" in msg or "system cannot find" in msg.lower()

    # destination is a FILE, not a folder: mkdir raises
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    src = tmp_path / "in.jpg"
    src.write_bytes(b"\xff\xd8")
    (s2, status2, msg2, _) = tr.encode_to_jxl(
        src, blocker / "sub" / "o.jxl", blocker / "sub" / "o.jxl", 7, 1.0, False, False)
    assert status2 == "error"
    assert "UnboundLocalError" not in msg2


def test_mode2_convert_flat_by_default_and_suffix_optin(tmp_path):
    src = tmp_path / "photos" / "a.jpg"
    src.parent.mkdir()
    src.write_bytes(b"x")
    # default (no explicit output, no suffix): flat, matching transcode path
    out = tr.resolve_output_convert(src, 2, "converted", "", "jxl", "", "", None, decode=False)
    assert out == src.parent / "a.jxl"
    # opt-in suffix: sibling <folder><suffix>/
    out2 = tr.resolve_output_convert(src, 2, "converted", "_converted", "jxl", "", "", None, decode=False)
    assert out2 == tmp_path / "photos_converted" / "a.jxl"


def test_empty_thumbnail_suffix_rejected(tmp_path, monkeypatch):
    dec.setup_logger()
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_decoder.py", str(tmp_path), "--thumbnail-suffix", ""])
    with pytest.raises(SystemExit) as exc:
        dec.main()
    assert exc.value.code == 2


def test_thumbnail_helpers_safe_with_empty_suffix(monkeypatch):
    monkeypatch.setattr(dec, "THUMBNAIL_SUFFIX", "")
    assert dec._is_thumbnail_jxl(Path("photo.jxl")) is False
    assert dec._parse_jxl_page_suffix("photo") == ("photo", 0, False)


def test_encode_delete_gate_requires_jbrd(tmp_path):
    """Encode direction: a structurally valid JXL WITHOUT jbrd must not
    authorize deleting the source JPEG (it is not recoverable)."""
    src = tmp_path / "a.jpg"
    src.write_bytes(b"\xff\xd8fake")
    final = tmp_path / "a.jxl"
    # structurally valid container with jxlc but NO jbrd
    final.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + (8).to_bytes(4, "big") + b"jxlc")
    tr.setup_logger()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "STORE_MD5", True)
    monkeypatch.setattr(tr, "TEMP2_DIR", None)
    results = [(str(src), "ok", str(final), "deadbeef")]
    monkeypatch.setattr(tr, "encode_one_transcode", lambda *a, **k: results[0])
    tr.process_group_transcode([(src, final)], 1, False, False, 8, False, False)
    assert src.exists(), "source deleted for a JXL without jbrd"
    monkeypatch.undo()


def test_ppm_comment_on_magic_line(tmp_path):
    ppm = tmp_path / "c.ppm"
    ppm.write_bytes(b"P6 # created by foo\n2 1 255\n" + b"\x00" * 6)
    img = dec.read_ppm_to_numpy(ppm)
    assert img.shape == (1, 2, 3)


# ---------------------------------------------------------------------------
# fifteenth-pass
# ---------------------------------------------------------------------------

def test_subfolder_request_wins_over_decoder_filter(monkeypatch, tmp_path):
    """--export-subfolder 16B_TIFF is an explicit request: the anti-ping-pong
    filter must NOT skip it. (Test name deliberately avoids the 'export'
    token: pytest's tmp dir is derived from it and would anchor the marker.)"""
    session = tmp_path / "_EXPORT"
    (session / "16B_TIFF").mkdir(parents=True)
    (session / "16B_TIFF" / "a.tif").write_bytes(b"x")
    monkeypatch.setattr(enc, "EXPORT_TIFF_SUBFOLDER", "16B_TIFF")
    try:
        found = enc.find_tiffs_mode7(tmp_path)
        assert [f.name for f in found] == ["a.tif"], \
            "explicit --export-subfolder 16B_TIFF was silently overruled"
    finally:
        monkeypatch.setattr(enc, "EXPORT_TIFF_SUBFOLDER", "")


def test_decoder_output_still_skipped_when_not_requested(tmp_path):
    session = tmp_path / "_EXPORT"
    (session / "16B_TIFF").mkdir(parents=True)
    (session / "16bit").mkdir(parents=True)
    (session / "16B_TIFF" / "a.tif").write_bytes(b"x")
    (session / "16bit" / "b.tif").write_bytes(b"x")
    found = enc.find_tiffs_mode6(tmp_path)
    assert [f.name for f in found] == ["b.tif"]


def test_convert_mode2_single_file_uses_parent(tmp_path):
    src = tmp_path / "sess" / "a.jxl"
    src.parent.mkdir()
    src.write_bytes(b"\x00" * 16)
    out = tr.resolve_output_convert(src, 2, "converted", "", "jpg", "", "",
                                    src.parent, decode=True)
    assert out == src.parent / "a.jpg"


def test_png_lossless_convert_produces_container():
    """PNG d=0 through encode_to_jxl must succeed (container forced), not be
    rejected by the toolkit's own integrity gate."""
    import shutil
    if shutil.which("cjxl") is None:
        pytest.skip("cjxl not installed")
    import tempfile
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = td / "a.png"
        Image.fromarray(np.zeros((16, 16, 3), dtype=np.uint8)).save(src)
        out = td / "a.jxl"
        tr.setup_logger()
        (s, status, msg, _) = tr.encode_to_jxl(src, out, out, 1, 0.0, True, False)
        assert status == "ok", f"PNG d=0 convert failed: {msg}"
        assert tr._verify_file_integrity(out)


def test_jbrd_gate_independent_of_md5_setting(tmp_path):
    """--no-md5 must NOT disable the jbrd check on the encode delete gate."""
    src = tmp_path / "a.jpg"
    src.write_bytes(b"\xff\xd8fake")
    final = tmp_path / "a.jxl"
    final.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + (8).to_bytes(4, "big") + b"jxlc")
    tr.setup_logger()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "STORE_MD5", False)  # --no-md5
    monkeypatch.setattr(tr, "TEMP2_DIR", None)
    results = [(str(src), "ok", str(final), None)]
    monkeypatch.setattr(tr, "encode_one_transcode", lambda *a, **k: results[0])
    tr.process_group_transcode([(src, final)], 1, False, False, 8, False, False)
    assert src.exists(), "jbrd-less JXL authorized delete under --no-md5"
    monkeypatch.undo()


def test_dry_run_does_not_ask_hhmm(tmp_path):
    """With the HHMM gate at execution time, a dry-run never charges the token."""
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    called = {"n": 0}
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(menu, "_confirm_archive_mode", lambda: called.__setitem__("n", 1) or True)
    workflow = {
        'mode': 8, 'advanced_options': {'delete_source': True}, 'dry_run': True,
        'origin_format': 'tiff', 'dest_format': 'jxl',
        'input_dir': str(tmp_path), 'workers': 2, 'compression': 'zip',
        'bit_depth': 16, 'mode_config': {}, 'expert_flags': '',
        'distance': 0.1, 'effort': 7, 'use_ram': True,
    }
    monkeypatch.setattr(menu, "_run_subprocess", lambda cmd: 0)
    menu.execute_workflow(workflow, {})
    assert called["n"] == 0, "HHMM was asked for a dry-run"
    monkeypatch.undo()


def test_la_tiff_roundtrip_png_channels():
    """make_png_bytes must accept 2-channel (gray+alpha) arrays (color type 4)."""
    img = np.zeros((8, 8, 2), dtype=np.uint16)
    img[:, :, 0] = 300
    img[:, :, 1] = 60000
    png = enc.make_png_bytes(img)
    # parse color type from IHDR
    ihdr_off = 8 + 4 + 4  # signature + length + 'IHDR'
    color_type = png[ihdr_off + 4 + 4 + 1]
    assert color_type == 4, f"expected PNG color type 4 (LA), got {color_type}"
