#!/usr/bin/env python3
"""Round 43 transcoder fixes (261001_audit_consolidated.md, section 4 + Errata):

T-1. --dry-run spawned `cjxl --version` on cmd_convert (:4606) and cmd_auto
     (:4999) — `_warn_distance_clamp(..., _min_effective_distance("cjxl"))`
     probes the tool eagerly. The contract is "a dry run runs no subprocess"
     (test_transcoder_fixes.py only passed full-suite because another test
     warmed the _tool_version cache — order-dependent masking). The floor is
     now resolved only on a real run; NON-dry runs warn exactly as before.
T-2. encode_to_jxl (the convert/-auto to_jxl worker) never wrote checksums.md5
     for its jbrd outputs (a --force-convert -d 0 JPEG keeps the jbrd box and
     must NEVER carry XMP markers): a second run in a collapsing mode with
     --delete-source failed closed with the misleading "no checksum to prove
     it (was it written with --no-md5?)". It now stores the source md5 and
     the JXL self-hash exactly like encode_one_transcode, respecting
     --no-md5; process_group_convert redistributes staged checksums to the
     destination like process_group_transcode.
T-3. cmd_auto now warns that --icc-profile/--to-srgb is ignored for the
     JPEG/PNG -> JXL encode groups, once — the warning cmd_convert already
     prints for the to_jxl direction and the --resize-*/--sharpen warning
     cmd_auto already prints.
Class. main() assigned its run-scoped globals one-way (`if args.x:`), so a
     second in-process run inherited the first run's armed flags (AI2, same
     class as the encoder #431 fix). Every CLI-derived global is now assigned
     unconditionally from args at main() entry.

Pre-fix proof: `git show HEAD:jxl_jpeg_transcoder.py > <tmp>/jxl_jpeg_transcoder.py`
then run this file against a copy of the repo tree that loads the OLD script —
each test below fails there and passes on the fixed code.
"""

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_jpeg_transcoder as tr


class _FakeRun:
    def __init__(self, stdout="", stderr="", returncode=0):
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
        rename_from="", rename_to="", summary_json=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


_JXL_SIG = b"\x00\x00\x00\x0cJXL \r\n\x87\n"
_JBRD_STUB = _JXL_SIG + (16).to_bytes(4, "big") + b"jbrd" + b"\x00" * 8
_FAKE_JPEG = b"\xff\xd8" + b"\x00" * 16 + b"\xff\xd9"


@pytest.fixture(autouse=True)
def _reset_globals():
    # _tool_version's lru_cache is the reason the audit's regression was
    # masked in the full suite (a warmed cache turns the eager dry-run probe
    # into a no-op). Clear it around every test so each proves its case
    # standalone.
    tr._tool_version.cache_clear()
    tr._run_summary.clear()
    yield
    tr._tool_version.cache_clear()
    tr.DELETE_SOURCE = False
    tr.DELETE_CONFIRM = True
    tr.DELETE_SKIPPED = False
    tr.TEMP2_DIR = None
    tr.STORE_MD5 = True
    tr.PROVENANCE_CHECK = "path"
    tr.EXPORT_MARKER = "_EXPORT"
    tr.EXPORT_JPEG_SUBFOLDER = ""
    tr.AUTO_REPAIR_JBRD = False
    tr._reset_abort()
    tr._FORCE_REDERIVE.clear()
    if hasattr(tr, "_delete_stats"):
        tr._delete_stats.update({"deleted": 0, "deleted_archived": 0, "kept": 0})
    tr._run_summary.clear()


# ===========================================================================
# T-1 - a dry run runs no subprocess
# ===========================================================================

def test_dry_run_convert_runs_no_subprocess(monkeypatch, tmp_path):
    (tmp_path / "a.jpg").write_bytes(_FAKE_JPEG)
    tr.setup_logger()
    calls = []
    monkeypatch.setattr(tr.subprocess, "run",
                        lambda *a, **k: calls.append(a[0]) or _FakeRun())
    tr.cmd_convert(_args(tmp_path, dry_run=True), from_jxl=False)
    assert calls == []


def test_dry_run_auto_runs_no_subprocess(monkeypatch, tmp_path):
    (tmp_path / "a.jpg").write_bytes(_FAKE_JPEG)
    tr.setup_logger()
    calls = []
    monkeypatch.setattr(tr.subprocess, "run",
                        lambda *a, **k: calls.append(a[0]) or _FakeRun())
    tr.cmd_auto(_args(tmp_path, dry_run=True))
    assert calls == []


def test_nondry_run_still_gets_the_clamp_warning(monkeypatch, tmp_path):
    """On a REAL run the clamp probe + warning behave exactly as before:
    the floor is resolved and the warning receives it."""
    (tmp_path / "a.jpg").write_bytes(_FAKE_JPEG)
    tr.setup_logger()
    probed, clamped = [], []
    monkeypatch.setattr(tr, "_min_effective_distance",
                        lambda exe: probed.append(exe) or 0.05)
    monkeypatch.setattr(tr, "_warn_distance_clamp",
                        lambda d, floor: clamped.append((d, floor)))
    monkeypatch.setattr(tr.subprocess, "run", lambda *a, **k: _FakeRun())
    monkeypatch.setattr(tr, "process_group_convert",
                        lambda *a, **k: ([], set()))
    tr.cmd_convert(_args(tmp_path), from_jxl=False)
    assert probed == ["cjxl"], "a real run must resolve the cjxl floor"
    assert clamped == [(1.0, 0.05)]


def test_nondry_auto_still_gets_the_clamp_warning(monkeypatch, tmp_path):
    (tmp_path / "a.jpg").write_bytes(_FAKE_JPEG)
    tr.setup_logger()
    probed, clamped = [], []
    monkeypatch.setattr(tr, "_min_effective_distance",
                        lambda exe: probed.append(exe) or 0.05)
    monkeypatch.setattr(tr, "_warn_distance_clamp",
                        lambda d, floor: clamped.append((d, floor)))
    monkeypatch.setattr(tr.subprocess, "run", lambda *a, **k: _FakeRun())
    monkeypatch.setattr(tr, "process_group_transcode",
                        lambda *a, **k: [])
    tr.cmd_auto(_args(tmp_path))
    assert probed == ["cjxl"]
    assert clamped == [(1.0, 0.05)]


# ===========================================================================
# T-2 - encode_to_jxl records checksums for its jbrd outputs
# ===========================================================================

def _encode_jbrd_stub(monkeypatch, tmp_path):
    """Run the real encode_to_jxl with cjxl stubbed to write a jbrd-carrying
    JXL stub. Returns (src, final, result)."""
    src = tmp_path / "photo.jpg"
    src.write_bytes(_FAKE_JPEG)
    final = tmp_path / "photo.jxl"

    def fake_cjxl(cmd, capture_output=True, **kw):
        Path(cmd[2]).write_bytes(_JBRD_STUB)
        return _FakeRun()

    monkeypatch.setattr(tr.subprocess, "run", fake_cjxl)
    monkeypatch.setattr(tr, "reorder_jxl_boxes", lambda p: None)
    monkeypatch.setattr(tr, "_verify_file_integrity", lambda p: True)
    result = tr.encode_to_jxl(src, final, final, effort=7, distance=0.0,
                              reconvert_val=False, smart=False)
    return src, final, result


def test_jbrd_convert_output_records_checksums(monkeypatch, tmp_path):
    src, final, result = _encode_jbrd_stub(monkeypatch, tmp_path)
    assert result[1] == "ok"
    # result carries the source md5 and the JXL self-hash (the staging
    # redistribution rides on these).
    assert result[3] == tr.md5_of_file(src)
    assert result[4] == tr.md5_of_file(final)
    assert final.exists() and tr.has_jbrd_box(final)
    assert tr.read_md5_db(final) == tr.md5_of_file(src)
    assert tr.read_jxl_self_hash_db(final) == tr.md5_of_file(final)


def test_jbrd_convert_respects_no_md5(monkeypatch, tmp_path):
    monkeypatch.setattr(tr, "STORE_MD5", False)
    src, final, result = _encode_jbrd_stub(monkeypatch, tmp_path)
    assert result[1] == "ok"
    assert not (final.parent / tr.CHECKSUMS_FILENAME).exists()
    assert tr.read_md5_db(final) is None


def test_second_run_provenance_filter_accepts_jbrd_output(monkeypatch,
                                                          tmp_path):
    """The workflow the audit described: first run archives the JPEG with
    --force-convert -d 0; a second collapsing run with --delete-source must
    be able to PROVE the output instead of refusing with "no checksum to
    prove it (was it written with --no-md5?)". Stub codecs as the round-39
    transcoder tests do."""
    src, final, _ = _encode_jbrd_stub(monkeypatch, tmp_path)
    # The jbrd output never carries markers (they break reconstruction) —
    # the db is the only provenance this path has.
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "_run_collapses_structure", lambda *a, **k: True)
    monkeypatch.setattr(tr, "setup_logger", lambda: None)
    kept, refused = tr._provenance_filter(
        [(src, final)], 2, output_arg=None, source_root=tmp_path)
    assert refused == []
    assert kept == [(src, final)]


# ===========================================================================
# T-3 - cmd_auto says --icc-profile/--to-srgb is ignored for JPEG/PNG groups
# ===========================================================================

def test_auto_warns_icc_ignored_for_jpeg_png_groups(monkeypatch, tmp_path):
    (tmp_path / "a.jpg").write_bytes(_FAKE_JPEG)
    (tmp_path / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    fake = _FakeLogger()
    monkeypatch.setattr(tr, "setup_logger", lambda: None)
    monkeypatch.setattr(tr, "logger", fake)
    monkeypatch.setattr(tr, "MAGICK_AVAILABLE", True)
    monkeypatch.setattr(tr, "_tool_version", lambda exe: (0, 12, 0))
    monkeypatch.setattr(tr, "process_group_transcode", lambda *a, **k: [])
    monkeypatch.setattr(tr, "process_group_convert", lambda *a, **k: ([], set()))
    tr.cmd_auto(_args(tmp_path, icc_profile="sRGB"))
    hits = [w for w in fake.warnings
            if "--icc-profile/--to-srgb is ignored for JPEG/PNG -> JXL" in w]
    assert len(hits) == 1, fake.warnings


def test_auto_does_not_warn_icc_without_jpeg_png_groups(monkeypatch, tmp_path):
    """Control: decode/lossy-only runs honour the profile — no warning."""
    (tmp_path / "a.jxl").write_bytes(_JXL_SIG + b"\x00" * 8)
    fake = _FakeLogger()
    monkeypatch.setattr(tr, "setup_logger", lambda: None)
    monkeypatch.setattr(tr, "logger", fake)
    monkeypatch.setattr(tr, "MAGICK_AVAILABLE", True)
    monkeypatch.setattr(tr, "_tool_version", lambda exe: (0, 12, 0))
    monkeypatch.setattr(tr, "process_group_transcode", lambda *a, **k: [])
    monkeypatch.setattr(tr, "process_group_convert", lambda *a, **k: ([], set()))
    tr.cmd_auto(_args(tmp_path, icc_profile="sRGB"))
    assert not [w for w in fake.warnings
                if "--icc-profile/--to-srgb is ignored" in w], fake.warnings


# ===========================================================================
# Class - main() resets every run-scoped global unconditionally
# ===========================================================================

def _main_argv(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["jxl_jpeg_transcoder.py"] + argv)


def test_second_main_run_resets_run_scoped_globals(monkeypatch, tmp_path):
    (tmp_path / "a.jpg").write_bytes(_FAKE_JPEG)

    armed = [str(tmp_path), "--dry-run", "--delete-source", "--delete-skipped",
             "--delete-confirm-off", "--provenance", "content",
             "--export-marker", "_OTHER", "--export-subfolder", "SUB",
             "--auto-repair-jbrd", "--no-md5"]
    _main_argv(monkeypatch, armed)
    tr.main()
    assert tr.DELETE_SOURCE is True
    assert tr.DELETE_CONFIRM is False
    assert tr.DELETE_SKIPPED is True
    assert tr.PROVENANCE_CHECK == "content"
    assert tr.EXPORT_MARKER == "_OTHER"
    assert tr.EXPORT_JPEG_SUBFOLDER == "SUB"
    assert tr.AUTO_REPAIR_JBRD is True
    assert tr.STORE_MD5 is False

    plain = [str(tmp_path), "--dry-run"]
    _main_argv(monkeypatch, plain)
    tr.main()
    assert tr.DELETE_SOURCE is False
    assert tr.DELETE_CONFIRM is True
    assert tr.DELETE_SKIPPED is False
    assert tr.PROVENANCE_CHECK == "path"
    assert tr.EXPORT_MARKER == "_EXPORT"
    assert tr.EXPORT_JPEG_SUBFOLDER == ""
    assert tr.AUTO_REPAIR_JBRD is False
    assert tr.STORE_MD5 is True


def test_main_keeps_the_settings_edited_in_the_script(monkeypatch, tmp_path):
    """The reset restores each global to its SCRIPT setting (the value at the
    top of jxl_jpeg_transcoder.py, captured at import), never to a hardcoded
    literal: a user who set EXPORT_MARKER = "_PRINT" (or turned DELETE_CONFIRM
    off, or set TEMP2_DIR) in the file must not see it silently reverted. The
    first round-43 fix assigned "_EXPORT"/"path"/True literally."""
    (tmp_path / "a.jpg").write_bytes(_FAKE_JPEG)
    staging = tmp_path / "ssd_staging"
    for name, value in (("EXPORT_MARKER", "_PRINT"),
                        ("EXPORT_JPEG_SUBFOLDER", "WEB"),
                        ("PROVENANCE_CHECK", "content"),
                        ("DELETE_CONFIRM", False),
                        ("STORE_MD5", False),
                        ("TEMP2_DIR", str(staging))):
        monkeypatch.setitem(tr._RUN_DEFAULTS, name, value)
    _main_argv(monkeypatch, [str(tmp_path), "--dry-run"])
    tr.main()
    assert tr.EXPORT_MARKER == "_PRINT"
    assert tr.EXPORT_JPEG_SUBFOLDER == "WEB"
    assert tr.PROVENANCE_CHECK == "content"
    assert tr.DELETE_CONFIRM is False
    assert tr.STORE_MD5 is False
    assert tr.TEMP2_DIR == str(staging)


# ---------------------------------------------------------------------------
# Real codec: a JPEG -> JXL --force-convert -d 0 run completes end to end
# ---------------------------------------------------------------------------

import shutil as _shutil
import subprocess as _subprocess


@pytest.mark.skipif(_shutil.which("cjxl") is None or _shutil.which("exiftool") is None,
                    reason="cjxl/exiftool not installed")
def test_real_force_convert_d0_run_completes(tmp_path):
    """REAL cjxl, the whole script as a subprocess. #444 made encode_to_jxl
    return the source md5 and the JXL self-hash as extra tuple members, and
    cmd_convert's summary loop still unpacked four names: every JPEG/PNG -> JXL
    --force-convert run crashed with a ValueError right after converting —
    before the summary and the delete gate. The stubbed tests above call
    encode_to_jxl alone and could not see it (caught by the real-photo
    battery)."""
    import numpy as np
    from PIL import Image
    src = tmp_path / "in"
    src.mkdir()
    rng = np.random.default_rng(7)
    Image.fromarray(rng.integers(0, 255, (64, 96, 3), dtype=np.uint8)).save(
        src / "j.jpg", quality=90)
    out = tmp_path / "out"
    r = _subprocess.run([sys.executable, str(Path(tr.__file__)), str(src), str(out),
                         "--mode", "2", "--force-convert", "--distance", "0"],
                        stdin=_subprocess.DEVNULL, capture_output=True, text=True,
                        cwd=str(tmp_path))
    assert "Traceback" not in (r.stdout + r.stderr), r.stdout + r.stderr
    assert r.returncode == 0, r.stdout + r.stderr
    assert (out / "j.jxl").exists()
    assert "jxl-md5" in (out / "checksums.md5").read_text(encoding="utf-8")
