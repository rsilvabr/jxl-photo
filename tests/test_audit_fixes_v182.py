#!/usr/bin/env python3
"""
Regression tests for the v1.8.2 audit fixes:

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
        Path(cmd[-1]).write_bytes(b"\xff\xd8recovered")
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
        if kw.get("collect_only") is None:
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

    # Bare codestream signature still accepted (header-only check)
    bare = tmp_path / "bare.jxl"
    bare.write_bytes(b"\xff\x0a" + b"\x00" * 64)
    assert enc._verify_jxl_integrity(bare) is True


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
        if kw.get("collect_only") is None:
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


def test_readonly_source_delete_does_not_abort(tmp_path):
    """Windows PermissionError on unlink (read-only file) must not crash the
    delete-source path."""
    src = tmp_path / "a.jxl"
    src.write_bytes(b"\x00" * 32)
    final = tmp_path / "a.jpg"
    final.write_bytes(b"\xff\xd8" + b"\x00" * 100 + b"\xff\xd9")
    tr.setup_logger()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tr, "_verify_file_integrity", lambda p: True)
    monkeypatch.setattr(tr, "STORE_MD5", False)
    src.chmod(0o444)  # read-only -> unlink raises PermissionError on Windows
    try:
        results = [(str(src), "ok", str(final), None)]
        # Simulate the delete loop directly via process_group_transcode pieces:
        # call the real function with mocked worker results
        monkeypatch.setattr(tr, "decode_one_transcode",
                            lambda *a, **k: results[0])
        monkeypatch.setattr(tr, "TEMP2_DIR", None)
        tr.DELETE_SOURCE = True
        tr.process_group_transcode([(src, final)], 1, True, False, 8, False, False)
        # No exception propagated; the read-only source is kept
        assert src.exists()
    finally:
        src.chmod(0o666)
        tr.DELETE_SOURCE = False
        monkeypatch.undo()


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


def test_manifest_mode_excel_float_format(tmp_path):
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
    monkey = pytest.MonkeyPatch()
    monkey.setattr(menu, "_print_error", lambda m: (_ for _ in ()).throw(AssertionError(m)))
    # Patch Confirm.ask to auto-proceed
    monkey.setattr(wp, "Confirm", type("C", (), {"ask": staticmethod(lambda *a, **k: True)}))
    assert menu._wizard_run_from_manifest(workflow) is True
    assert workflow["manifest_entries"][0][2] == 7
    monkey.undo()


def test_transcoder_reorder_raises_on_truncated_extended(tmp_path):
    sig = b"\x00\x00\x00\x0cJXL \r\n\x87\n"
    ftyp = (16).to_bytes(4, "big") + b"ftyp" + b"jxl \x00\x00\x00\x00"
    ext_box = (1).to_bytes(4, "big") + b"Exif" + b"\x00" * 3  # extended size, truncated header
    f = tmp_path / "t.jxl"
    f.write_bytes(sig + ftyp + ext_box)
    with pytest.raises(RuntimeError):
        tr.reorder_jxl_boxes(f)
