#!/usr/bin/env python3
"""Deleting the source is now available in EVERY mode, not just mode 8.

The mode decides WHERE the output lands; deleting the source is a separate
opt-in. Mode 8 was never architecturally special — every gate in the delete
blocks certifies THIS run's output at its FINAL path, which is mode-independent
— it was just the only mode allowed to reach them.

Three groups of tests:

  * the gates themselves still hold in every mode (and still refuse when the
    output is missing, stale, or failed its integrity check);
  * the CONFIRMATION follows the deletion, not the mode. The wrapper's cmd
    builder has always appended `--delete-source --delete-confirm-off` for any
    mode, so the three HHMM gates keyed on `mode == 8` would have let a mode-3
    delete run with no confirmation anywhere in the chain;
  * --verify-roundtrip, the opt-in pixel gate in front of the unlink.

The wizard's [D] entry is covered at the bottom: deletion is not a layout, so
it is not a mode — [D] asks for the layout and sets delete_source next to it.
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

STATUS = {k: True for k in
          ("cjxl", "djxl", "exiftool", "magick", "tifffile", "pillow", "imagecodecs")}

ALL_MODES = [0, 1, 2, 3, 4, 5, 6, 7, 8]


@pytest.fixture
def menu(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


@pytest.fixture
def launched(monkeypatch):
    calls = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600: (calls.append(list(map(str, cmd))), 0)[1])
    return calls


def _out(capsys) -> str:
    return " ".join(capsys.readouterr().out.split())


def _tiff(path: Path, value: int = 1000):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.full((16, 16, 3), value, np.uint16),
                     photometric="rgb")


def _jxl_stub(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)


# ---------------------------------------------------------------------------
# The gate itself: every mode may delete
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ALL_MODES)
def test_encoder_deletes_in_every_mode(tmp_path, monkeypatch, mode):
    """Pre-fix only mode 8 reached the delete block at all."""
    src = tmp_path / "photo.tif"
    _tiff(src)
    final = tmp_path / "out" / "photo.jxl"
    final.parent.mkdir()
    final.write_bytes(b"jxl")

    enc.setup_logger()
    monkeypatch.setattr(enc, "DELETE_SOURCE", True)
    monkeypatch.setattr(enc, "TEMP2_DIR", None)
    monkeypatch.setattr(enc, "VERIFY_ROUNDTRIP", False)
    monkeypatch.setattr(enc, "_verify_jxl_integrity", lambda p: True)
    monkeypatch.setattr(enc, "convert_one",
                        lambda t, w, f, p=0, *a, **k: ((str(t), p), "ok", str(f), t))

    enc.process_group([(src, final, 0, False, 0, 3)], 1, mode)
    assert not src.exists(), f"mode {mode} did not delete the source"


@pytest.mark.parametrize("mode", [0, 3, 8])
def test_decoder_deletes_in_every_mode(tmp_path, monkeypatch, mode):
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    final = tmp_path / "out" / "photo.tif"
    final.parent.mkdir()
    _tiff(final)

    dec.setup_logger()
    monkeypatch.setattr(dec, "DELETE_SOURCE", True)
    monkeypatch.setattr(dec, "TEMP2_DIR", None)
    monkeypatch.setattr(dec, "_verify_tiff_integrity", lambda p: True)
    monkeypatch.setattr(dec, "convert_multipage_jxl_group",
                        lambda *a, **k: (str(src), "ok", str(final)))

    task = {"type": "multi", "main_jxl": src,
            "entries": [(src, 0, False, False, 0, False, None)],
            "ignored_thumbs": [], "final_tiff": final}
    dec.process_group([task], 1, mode)
    assert not src.exists(), f"mode {mode} did not delete the source"


@pytest.mark.parametrize("mode", [0, 3, 8])
def test_transcoder_deletes_in_every_mode(tmp_path, monkeypatch, mode):
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8\xff\xd9")
    final = tmp_path / "out" / "photo.jxl"
    final.parent.mkdir()
    final.write_bytes(b"jxl")

    tr.setup_logger()
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "TEMP2_DIR", None)
    monkeypatch.setattr(tr, "STORE_MD5", False)
    monkeypatch.setattr(tr, "_verify_file_integrity", lambda p: True)
    monkeypatch.setattr(tr, "has_jbrd_box", lambda p: True)
    monkeypatch.setattr(tr, "encode_one_transcode",
                        lambda s, w, f, *a, **k: (str(s), "ok", str(f), None))

    tr.process_group_transcode([(src, final)], 1, decode=False, verify=False,
                               mode=mode, reconvert_val=False, smart=False)
    assert not src.exists(), f"mode {mode} did not delete the source"


# --- and still refuses when it should (no over-fix) -------------------------

def test_encoder_keeps_source_when_output_missing(tmp_path, monkeypatch):
    src = tmp_path / "photo.tif"
    _tiff(src)
    final = tmp_path / "out" / "photo.jxl"        # never created

    enc.setup_logger()
    monkeypatch.setattr(enc, "DELETE_SOURCE", True)
    monkeypatch.setattr(enc, "TEMP2_DIR", None)
    monkeypatch.setattr(enc, "VERIFY_ROUNDTRIP", False)
    monkeypatch.setattr(enc, "convert_one",
                        lambda t, w, f, p=0, *a, **k: ((str(t), p), "ok", str(f), t))

    enc.process_group([(src, final, 0, False, 0, 3)], 1, 3)
    assert src.exists()


def test_encoder_keeps_source_when_integrity_fails(tmp_path, monkeypatch):
    src = tmp_path / "photo.tif"
    _tiff(src)
    final = tmp_path / "out" / "photo.jxl"
    final.parent.mkdir()
    final.write_bytes(b"broken")

    enc.setup_logger()
    monkeypatch.setattr(enc, "DELETE_SOURCE", True)
    monkeypatch.setattr(enc, "TEMP2_DIR", None)
    monkeypatch.setattr(enc, "VERIFY_ROUNDTRIP", False)
    monkeypatch.setattr(enc, "_verify_jxl_integrity", lambda p: False)
    monkeypatch.setattr(enc, "convert_one",
                        lambda t, w, f, p=0, *a, **k: ((str(t), p), "ok", str(f), t))

    enc.process_group([(src, final, 0, False, 0, 3)], 1, 3)
    assert src.exists()


def test_encoder_keeps_source_when_staging_move_failed(tmp_path, monkeypatch):
    """A stale file at the final path must not certify this run's output."""
    src = tmp_path / "photo.tif"
    _tiff(src)
    staging = tmp_path / "stg"
    staging.mkdir()
    final = tmp_path / "out" / "photo.jxl"
    final.parent.mkdir()
    final.write_bytes(b"stale but valid-looking")

    enc.setup_logger()
    monkeypatch.setattr(enc, "DELETE_SOURCE", True)
    monkeypatch.setattr(enc, "TEMP2_DIR", str(staging))
    monkeypatch.setattr(enc, "VERIFY_ROUNDTRIP", False)
    monkeypatch.setattr(enc, "_verify_jxl_integrity", lambda p: True)
    # The worker "succeeds" but never writes the staging file, so the move fails.
    monkeypatch.setattr(enc, "convert_one",
                        lambda t, w, f, p=0, *a, **k: ((str(t), p), "ok", str(f), t))

    enc.process_group([(src, final, 0, False, 0, 3)], 1, 3)
    assert src.exists()


# ---------------------------------------------------------------------------
# The confirmation follows the deletion, not the mode
# ---------------------------------------------------------------------------

def _wf(mode, **over):
    wf = {
        "mode": mode, "origin_format": "tiff", "dest_format": "jxl",
        "input_dir": ".", "workers": 2, "effort": 7, "distance": 0.1,
        "staging": "", "use_ram": True, "conversion_type": "jxl_tiff_encoder",
        "advanced_options": {"delete_source": True},
    }
    wf.update(over)
    return wf


@pytest.mark.parametrize("mode", [0, 3, 8])
def test_execute_workflow_charges_hhmm_in_every_mode(menu, launched, monkeypatch,
                                                     capsys, mode):
    """Pre-fix the token was only charged for mode 8 — while the cmd builder
    sent --delete-confirm-off regardless."""
    asked = []
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode",
                        lambda self: (asked.append(True), False)[1])

    ok = menu.execute_workflow(_wf(mode), STATUS)
    assert ok is False
    assert asked == [True], f"mode {mode} did not ask"
    assert launched == [], "nothing may run when the token is declined"


def test_execute_workflow_dry_run_never_charges(menu, launched, monkeypatch):
    asked = []
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode",
                        lambda self: (asked.append(True), True)[1])
    menu.execute_workflow(_wf(3, dry_run=True), STATUS)
    assert asked == []


def test_execute_workflow_without_delete_never_charges(menu, launched, monkeypatch):
    asked = []
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode",
                        lambda self: (asked.append(True), True)[1])
    menu.execute_workflow(_wf(3, advanced_options={}), STATUS)
    assert asked == []


def test_manifest_charges_hhmm_for_a_mode_3_entry(menu, monkeypatch, tmp_path):
    src = tmp_path / "A"
    _tiff(src / "a.tif")
    asked = []
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode",
                        lambda self: (asked.append(True), False)[1])
    monkeypatch.setattr(wp.InteractiveMenu, "_manifest_needs_collision_scan",
                        lambda self, e, m: False)

    wf = {"manifest_entries": [(str(src), str(src), 3)],
          "origin_format": "tiff", "dest_format": "jxl", "workers": 2,
          "advanced_options": {"delete_source": True}, "dry_run": False}
    assert menu._execute_manifest_workflow(wf, STATUS) is False
    assert asked == [True], "a mode-3 manifest delete was never confirmed"


def test_unattended_preset_refuses_delete_in_any_mode(menu, launched, tmp_path, capsys):
    """--run-preset must refuse a destructive preset whatever its mode."""
    src = tmp_path / "photos"
    src.mkdir()
    session = {k: None for k in wp.ToolConfig.__dataclass_fields__ if k.startswith("last_")}
    session.update(last_input_dir=str(src), last_output_mode="3",
                   last_origin_format="tiff", last_dest_format="jxl",
                   last_workers=2, last_effort=7,
                   last_advanced_options={"delete_source": True})

    ok = menu._run_saved_session(session, STATUS,
                                 answers={"overwrite": False, "dry_run": False})
    assert ok is False, "a mode-3 delete preset ran unattended"
    assert launched == []
    assert "cannot be given unattended" in _out(capsys)


def test_child_asks_for_itself_in_mode_3(tmp_path):
    """Without --delete-confirm-off the child must run its OWN confirmation.

    stdin is closed, so the prompt hits EOF and the run exits 3 (declined)
    rather than deleting. Pre-fix mode 3 skipped the prompt AND the delete.
    """
    _tiff(tmp_path / "a.tif")
    r = subprocess.run(
        [sys.executable, str(REPO / "jxl_tiff_encoder.py"), str(tmp_path),
         "--mode", "3", "--distance", "0", "--delete-source"],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert r.returncode == 3, r.stdout + r.stderr
    assert (tmp_path / "a.tif").exists()


# ---------------------------------------------------------------------------
# --verify-roundtrip
# ---------------------------------------------------------------------------

def _real_encode(src_tiff: Path, out_jxl: Path, distance: float):
    """Encode with the real cjxl, the same way convert_one does."""
    img = tifffile.imread(str(src_tiff))
    if img.dtype == np.uint8:
        img = img.astype(np.uint16) * 257
    png = out_jxl.with_suffix(".png")
    png.write_bytes(enc.make_png_bytes(img))
    cmd = ["cjxl", str(png), str(out_jxl), "-d", str(distance), "--effort", "3"]
    if distance > 0:
        cmd.append("--container=1")
    subprocess.run(cmd, capture_output=True, check=True, timeout=300)


def _structured(path: Path, scale: float = 1.0, seed: int = 5):
    """A small image with real structure — pure noise is destroyed by lossy."""
    y, x = np.mgrid[0:96, 0:128]
    base = ((np.sin(x / 9.0) * 0.25 + np.cos(y / 11.0) * 0.25 + 0.5) * 60000 * scale)
    img = np.stack([base, base * 0.8, base * 0.6], axis=2).astype(np.uint16)
    tifffile.imwrite(str(path), img, photometric="rgb")


def test_verify_lossless_accepts_identical(tmp_path):
    src, jxl = tmp_path / "a.tif", tmp_path / "a.jxl"
    _structured(src)
    _real_encode(src, jxl, 0.0)
    ok, detail = enc._verify_roundtrip_page(src, 0, jxl, 0.0)
    assert ok, detail
    assert "identical" in detail


def test_verify_lossless_rejects_a_different_image(tmp_path):
    src, other, jxl = tmp_path / "a.tif", tmp_path / "b.tif", tmp_path / "b.jxl"
    _structured(src)
    _structured(other, scale=0.5, seed=9)
    _real_encode(other, jxl, 0.0)
    ok, detail = enc._verify_roundtrip_page(src, 0, jxl, 0.0)
    assert not ok
    assert "pixel-identical" in detail


def test_verify_lossy_accepts_the_same_image(tmp_path):
    src, jxl = tmp_path / "a.tif", tmp_path / "a.jxl"
    _structured(src)
    _real_encode(src, jxl, 1.0)
    ok, detail = enc._verify_roundtrip_page(src, 0, jxl, 1.0)
    assert ok, detail


def test_verify_lossy_rejects_a_black_encode(tmp_path):
    """The documented scanner-ICC failure: cjxl emits a near-black image that
    passes every structural check."""
    src, black, jxl = tmp_path / "a.tif", tmp_path / "black.tif", tmp_path / "black.jxl"
    _structured(src)
    tifffile.imwrite(str(black), np.zeros((96, 128, 3), np.uint16), photometric="rgb")
    _real_encode(black, jxl, 1.0)
    ok, detail = enc._verify_roundtrip_page(src, 0, jxl, 1.0)
    assert not ok
    assert "brightness" in detail


def test_verify_rejects_a_shape_mismatch(tmp_path):
    src, other, jxl = tmp_path / "a.tif", tmp_path / "b.tif", tmp_path / "b.jxl"
    _structured(src)
    tifffile.imwrite(str(other), np.full((32, 32, 3), 3000, np.uint16), photometric="rgb")
    _real_encode(other, jxl, 0.0)
    ok, detail = enc._verify_roundtrip_page(src, 0, jxl, 0.0)
    assert not ok
    assert "shape" in detail


def test_verify_failure_is_not_a_pass(tmp_path):
    """A check that cannot RUN must read as failed, never as verified."""
    src = tmp_path / "a.tif"
    _structured(src)
    ok, detail = enc._verify_roundtrip_page(src, 0, tmp_path / "missing.jxl", 0.0)
    assert not ok
    assert "could not verify" in detail


def test_verify_failure_blocks_the_delete(tmp_path, monkeypatch):
    src = tmp_path / "photo.tif"
    _tiff(src)
    final = tmp_path / "out" / "photo.jxl"
    final.parent.mkdir()
    final.write_bytes(b"jxl")

    enc.setup_logger()
    monkeypatch.setattr(enc, "DELETE_SOURCE", True)
    monkeypatch.setattr(enc, "TEMP2_DIR", None)
    monkeypatch.setattr(enc, "VERIFY_ROUNDTRIP", True)
    monkeypatch.setattr(enc, "_verify_jxl_integrity", lambda p: True)
    monkeypatch.setattr(enc, "_verify_roundtrip_page",
                        lambda *a, **k: (False, "pixels do not match"))
    monkeypatch.setattr(enc, "convert_one",
                        lambda t, w, f, p=0, *a, **k: ((str(t), p), "ok", str(f), t))

    enc.process_group([(src, final, 0, False, 0, 3)], 1, 3)
    assert src.exists(), "a failed round-trip must keep the source"


def test_verify_pass_allows_the_delete(tmp_path, monkeypatch):
    src = tmp_path / "photo.tif"
    _tiff(src)
    final = tmp_path / "out" / "photo.jxl"
    final.parent.mkdir()
    final.write_bytes(b"jxl")

    enc.setup_logger()
    monkeypatch.setattr(enc, "DELETE_SOURCE", True)
    monkeypatch.setattr(enc, "TEMP2_DIR", None)
    monkeypatch.setattr(enc, "VERIFY_ROUNDTRIP", True)
    monkeypatch.setattr(enc, "_verify_jxl_integrity", lambda p: True)
    monkeypatch.setattr(enc, "_verify_roundtrip_page", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(enc, "convert_one",
                        lambda t, w, f, p=0, *a, **k: ((str(t), p), "ok", str(f), t))

    enc.process_group([(src, final, 0, False, 0, 3)], 1, 3)
    assert not src.exists()


def test_verify_checks_every_page_of_a_split(tmp_path, monkeypatch):
    """One bad page of a multi-page split keeps the whole source."""
    src = tmp_path / "scan.tif"
    _tiff(src)
    out = tmp_path / "out"
    out.mkdir()
    f0, f2 = out / "scan.jxl", out / "scan_page2.jxl"
    f0.write_bytes(b"jxl")
    f2.write_bytes(b"jxl")

    enc.setup_logger()
    monkeypatch.setattr(enc, "DELETE_SOURCE", True)
    monkeypatch.setattr(enc, "TEMP2_DIR", None)
    monkeypatch.setattr(enc, "VERIFY_ROUNDTRIP", True)
    monkeypatch.setattr(enc, "_verify_jxl_integrity", lambda p: True)
    monkeypatch.setattr(enc, "_verify_roundtrip_page",
                        lambda t, page, j, d: (page == 0, "page 2 is wrong"))
    monkeypatch.setattr(enc, "convert_one",
                        lambda t, w, f, p=0, *a, **k: ((str(t), p), "ok", str(f), t))

    enc.process_group([(src, f0, 0, False, 0, 3), (src, f2, 2, False, 0, 3)], 1, 3)
    assert src.exists()


def test_verify_without_delete_is_a_no_op_with_a_warning(tmp_path):
    _tiff(tmp_path / "a.tif")
    r = subprocess.run(
        [sys.executable, str(REPO / "jxl_tiff_encoder.py"), str(tmp_path),
         "--mode", "0", "--distance", "0", "--verify-roundtrip"],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no effect without --delete-source" in r.stdout
    assert (tmp_path / "a.tif").exists()


def test_wrapper_emits_verify_only_alongside_delete(menu, launched, monkeypatch):
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode", lambda self: True)

    menu.execute_workflow(_wf(3, advanced_options={"delete_source": True,
                                                   "verify_roundtrip": True}), STATUS)
    assert "--verify-roundtrip" in launched[-1]

    menu.execute_workflow(_wf(3, advanced_options={"verify_roundtrip": True}), STATUS)
    assert "--verify-roundtrip" not in launched[-1], (
        "the flag is a delete gate; without --delete-source it must not be sent")


# ---------------------------------------------------------------------------
# --delete-skipped: finishing an interrupted archive
# ---------------------------------------------------------------------------

def _skip_run(tmp_path, monkeypatch, *, delete_skipped, integrity=True,
              verify=None, staging=None, mtime_newer=True):
    """One source whose output ALREADY exists, so convert_one reports SKIP.

    Returns the source path so the caller can assert on its survival.
    """
    src = tmp_path / "photo.tif"
    _tiff(src)
    final = tmp_path / "out" / "photo.jxl"
    final.parent.mkdir()
    final.write_bytes(b"already archived")
    # The output must look up to date for smart sync to skip it.
    stamp = src.stat().st_mtime + (100 if mtime_newer else -100)
    import os as _os
    _os.utime(final, (stamp, stamp))

    enc.setup_logger()
    monkeypatch.setattr(enc, "DELETE_SOURCE", True)
    monkeypatch.setattr(enc, "DELETE_SKIPPED", delete_skipped)
    monkeypatch.setattr(enc, "OVERWRITE", "smart")
    monkeypatch.setattr(enc, "TEMP2_DIR", staging)
    monkeypatch.setattr(enc, "VERIFY_ROUNDTRIP", verify is not None)
    monkeypatch.setattr(enc, "_verify_jxl_integrity", lambda p: integrity)
    if verify is not None:
        monkeypatch.setattr(enc, "_verify_roundtrip_page",
                            lambda *a, **k: (verify, "stubbed"))
    enc.process_group([(src, final, 0, False, 0, 3)], 1, 3)
    return src


def test_skipped_source_is_kept_by_default(tmp_path, monkeypatch):
    """The historical rule, unchanged: a SKIP blocks the delete."""
    src = _skip_run(tmp_path, monkeypatch, delete_skipped=False)
    assert src.exists()


def test_delete_skipped_removes_an_already_archived_source(tmp_path, monkeypatch):
    src = _skip_run(tmp_path, monkeypatch, delete_skipped=True)
    assert not src.exists()


def test_delete_skipped_still_requires_the_integrity_check(tmp_path, monkeypatch):
    """The structural floor is what makes this safe at all — a SKIP proves
    nothing on its own, so a broken output must still keep the source."""
    src = _skip_run(tmp_path, monkeypatch, delete_skipped=True, integrity=False)
    assert src.exists()


def test_delete_skipped_honours_a_failed_roundtrip(tmp_path, monkeypatch):
    src = _skip_run(tmp_path, monkeypatch, delete_skipped=True, verify=False)
    assert src.exists()


def test_delete_skipped_passes_with_a_good_roundtrip(tmp_path, monkeypatch):
    src = _skip_run(tmp_path, monkeypatch, delete_skipped=True, verify=True)
    assert not src.exists()


def test_delete_skipped_works_with_staging_configured(tmp_path, monkeypatch):
    """The trap: a skipped page is never in moved_finals (nothing was staged),
    so the staging gate would block it and the flag would silently do nothing."""
    staging = tmp_path / "stg"
    staging.mkdir()
    src = _skip_run(tmp_path, monkeypatch, delete_skipped=True, staging=str(staging))
    assert not src.exists(), "the staging gate swallowed the skipped delete"


def test_stale_output_is_not_classified_as_archived(tmp_path, monkeypatch):
    """An output OLDER than its source is not a skip at all — it is reconverted,
    and the normal (stronger) delete path applies. --delete-skipped must not
    widen to cover it, or a stale archive would be treated as a finished one.
    """
    import os as _os
    src = tmp_path / "photo.tif"
    _tiff(src)
    final = tmp_path / "photo.jxl"
    final.write_bytes(b"stale")

    monkeypatch.setattr(enc, "OVERWRITE", "smart")
    stamp = src.stat().st_mtime - 100
    _os.utime(final, (stamp, stamp))
    assert enc._would_skip(src, final) is False, "an out-of-date output is not archived"

    stamp = src.stat().st_mtime + 100
    _os.utime(final, (stamp, stamp))
    assert enc._would_skip(src, final) is True

    final.unlink()
    assert enc._would_skip(src, final) is False, "a missing output is never a skip"

    # --overwrite reconverts everything, so nothing is ever classified archived.
    final.write_bytes(b"x")
    _os.utime(final, (stamp, stamp))
    monkeypatch.setattr(enc, "OVERWRITE", True)
    assert enc._would_skip(src, final) is False


def test_delete_skipped_still_blocked_by_discarded_pages(tmp_path, monkeypatch):
    """A source that lost real pages keeps its veto whatever else is on."""
    import os as _os
    src = tmp_path / "photo.tif"
    _tiff(src)
    final = tmp_path / "out" / "photo.jxl"
    final.parent.mkdir()
    final.write_bytes(b"archived")
    stamp = src.stat().st_mtime + 100
    _os.utime(final, (stamp, stamp))

    enc.setup_logger()
    monkeypatch.setattr(enc, "DELETE_SOURCE", True)
    monkeypatch.setattr(enc, "DELETE_SKIPPED", True)
    monkeypatch.setattr(enc, "OVERWRITE", "smart")
    monkeypatch.setattr(enc, "TEMP2_DIR", None)
    monkeypatch.setattr(enc, "VERIFY_ROUNDTRIP", False)
    monkeypatch.setattr(enc, "_verify_jxl_integrity", lambda p: True)
    monkeypatch.setattr(enc, "_discarded_real_page_sources",
                        {_os.path.normcase(str(src))})

    enc.process_group([(src, final, 0, False, 0, 3)], 1, 3)
    assert src.exists()


def test_delete_skipped_needs_every_page_of_a_split(tmp_path, monkeypatch):
    """One page freshly converted, one skipped: both must clear their own gate."""
    import os as _os
    src = tmp_path / "scan.tif"
    _tiff(src)
    out = tmp_path / "out"
    out.mkdir()
    f0, f2 = out / "scan.jxl", out / "scan_page2.jxl"
    f0.write_bytes(b"archived")            # exists -> skipped
    stamp = src.stat().st_mtime + 100
    _os.utime(f0, (stamp, stamp))
    # f2 does not exist -> converted this run

    enc.setup_logger()
    monkeypatch.setattr(enc, "DELETE_SOURCE", True)
    monkeypatch.setattr(enc, "DELETE_SKIPPED", True)
    monkeypatch.setattr(enc, "OVERWRITE", "smart")
    monkeypatch.setattr(enc, "TEMP2_DIR", None)
    monkeypatch.setattr(enc, "VERIFY_ROUNDTRIP", False)
    # page 2's output is never actually written -> exists() fails -> KEEP
    monkeypatch.setattr(enc, "_verify_jxl_integrity", lambda p: True)
    real_convert = enc.convert_one
    monkeypatch.setattr(enc, "convert_one",
                        lambda t, w, f, p=0, *a, **k: (real_convert(t, w, f, p)
                                                       if p == 0 else
                                                       ((str(t), p), "ok", str(f), t)))
    enc.process_group([(src, f0, 0, False, 0, 3), (src, f2, 2, False, 0, 3)], 1, 3)
    assert src.exists(), "a missing page's output must still veto the delete"


def test_delete_skipped_without_delete_source_is_a_no_op(tmp_path):
    _tiff(tmp_path / "a.tif")
    r = subprocess.run(
        [sys.executable, str(REPO / "jxl_tiff_encoder.py"), str(tmp_path),
         "--mode", "0", "--distance", "0", "--delete-skipped"],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no effect without --delete-source" in r.stdout
    assert (tmp_path / "a.tif").exists()


def test_dry_run_previews_what_delete_skipped_would_remove(tmp_path):
    """A destructive option that cannot be previewed is the wrong opt-in."""
    _tiff(tmp_path / "a.tif")
    out = tmp_path / "out"
    out.mkdir()
    # A real, valid JXL at the destination, newer than the source.
    subprocess.run([sys.executable, str(REPO / "jxl_tiff_encoder.py"), str(tmp_path),
                    str(out), "--mode", "2", "--distance", "0"],
                   capture_output=True, text=True, timeout=300, check=True)
    r = subprocess.run(
        [sys.executable, str(REPO / "jxl_tiff_encoder.py"), str(tmp_path), str(out),
         "--mode", "2", "--distance", "0", "--delete-source", "--delete-skipped",
         "--dry-run"],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "would DELETE 1 already-archived source(s)" in r.stdout
    assert (tmp_path / "a.tif").exists(), "a dry run must not delete anything"


def test_wrapper_emits_delete_skipped_only_alongside_delete(menu, launched, monkeypatch):
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode", lambda self: True)
    menu.execute_workflow(_wf(3, advanced_options={"delete_source": True,
                                                   "delete_skipped": True}), STATUS)
    assert "--delete-skipped" in launched[-1]
    menu.execute_workflow(_wf(3, advanced_options={"delete_skipped": True}), STATUS)
    assert "--delete-skipped" not in launched[-1]


# ---------------------------------------------------------------------------
# The wizard's [D] entry
# ---------------------------------------------------------------------------

MODES_STUB = [(str(i), f"mode {i}", "") for i in range(9)]


def _gateway(menu, monkeypatch, answers, layout="3", origin="tiff", dest="jxl",
             conv="jxl_tiff_encoder"):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda *a: next(it))
    wf = {"origin_format": origin, "dest_format": dest, "input_dir": ".",
          "mode": None, "conversion_type": conv}
    ok = menu._wizard_delete_gateway(wf, MODES_STUB)
    return ok, wf


def test_D_arms_delete_on_the_chosen_layout(menu, monkeypatch):
    # gate 1 yes, layout 3, gate 2 yes, verify no, delete-skipped no
    ok, wf = _gateway(menu, monkeypatch, ["y", "3", "y", "n", "n"])
    assert ok is True
    assert wf["mode"] == 3
    assert wf["delete_source"] is True
    assert wf.get("verify_roundtrip") is False
    assert wf.get("delete_skipped") is False


def test_D_can_arm_verify_too(menu, monkeypatch):
    # mode 5 collapses folders, so a provenance answer is asked too
    ok, wf = _gateway(menu, monkeypatch, ["y", "5", "y", "y", "n", ""])
    assert wf["mode"] == 5
    assert wf["verify_roundtrip"] is True


# --- provenance: asked only where an output can be claimed by another source --

@pytest.mark.parametrize("layout", ["2", "4", "5", "6", "7"])
def test_D_asks_provenance_for_collapsing_modes(menu, monkeypatch, layout, capsys):
    ok, wf = _gateway(menu, monkeypatch, ["y", layout, "y", "n", "n", ""])
    assert wf["provenance"] == "path", "Enter must give the cheap, safe default"
    out = _out(capsys)
    assert "MOVED FOLDERS" in out, "the slow option must say when it is needed"


@pytest.mark.parametrize("layout", ["0", "1", "3", "8"])
def test_D_skips_provenance_when_the_output_cannot_be_claimed(menu, monkeypatch, layout):
    """Modes 0/1/3/8 derive the output from the source's own folder, so there is
    nothing to confuse — do not ask a question that has no meaning."""
    ok, wf = _gateway(menu, monkeypatch, ["y", layout, "y", "n", "n"])
    assert wf["mode"] == int(layout)
    assert "provenance" not in wf


def test_D_can_choose_content_matching(menu, monkeypatch, capsys):
    ok, wf = _gateway(menu, monkeypatch, ["y", "5", "y", "n", "n", "content"])
    assert wf["provenance"] == "content"
    assert "noticeably longer" in _out(capsys)


def test_wrapper_emits_provenance_for_the_tiff_directions(menu, launched, monkeypatch):
    """Both the encoder and the decoder record provenance markers, so both take
    the flag. The transcoder does not have it yet."""
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode", lambda self: True)
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_lossy_delete_skipped",
                        lambda self, workflow: True)
    adv = {"delete_source": True, "provenance": "content"}

    menu.execute_workflow(_wf(5, advanced_options=adv), STATUS)
    assert "--provenance" in launched[-1] and "content" in launched[-1]

    menu.execute_workflow(_wf(5, origin_format="jxl", dest_format="tiff",
                              conversion_type="jxl_tiff_decoder",
                              compression="zip", bit_depth=16,
                              advanced_options=adv), STATUS)
    assert "--provenance" in launched[-1]

    menu.execute_workflow(_wf(5, origin_format="jpeg", dest_format="jxl",
                              conversion_type="transcode_lossless",
                              advanced_options=adv), STATUS)
    assert "--provenance" not in launched[-1]


def test_D_can_arm_delete_skipped(menu, monkeypatch):
    ok, wf = _gateway(menu, monkeypatch, ["y", "3", "y", "y", "y"])
    assert wf["verify_roundtrip"] is True
    assert wf["delete_skipped"] is True


def test_D_warns_when_delete_skipped_has_no_pixel_check(menu, monkeypatch, capsys):
    """The one combination that deletes a master on a file this run did not
    write, with nothing comparing the pixels. It must say so."""
    _gateway(menu, monkeypatch, ["y", "3", "y", "n", "y"])
    out = _out(capsys)
    assert "structurally valid" in out


def test_D_first_gate_declined_arms_nothing(menu, monkeypatch):
    """A mis-keyed D must cost nothing."""
    monkeypatch.setattr(wp.InteractiveMenu, "_wizard_select_mode",
                        lambda self, workflow: "BACK")
    ok, wf = _gateway(menu, monkeypatch, ["n"])
    assert ok == "BACK"
    assert "delete_source" not in wf


def test_D_second_gate_declined_arms_nothing(menu, monkeypatch):
    monkeypatch.setattr(wp.InteractiveMenu, "_wizard_select_mode",
                        lambda self, workflow: "BACK")
    ok, wf = _gateway(menu, monkeypatch, ["y", "3", "n"])
    assert ok == "BACK"
    assert "delete_source" not in wf


def test_D_preview_counts_the_files_the_mode_would_see(menu, monkeypatch, tmp_path):
    """Flat for modes 0/1, recursive for the rest — the count is what makes a
    wrong folder visible before the token is charged."""
    _tiff(tmp_path / "root.tif")
    _tiff(tmp_path / "sub" / "a.tif")
    _tiff(tmp_path / "sub" / "b.tif")
    wf = {"input_dir": str(tmp_path), "origin_format": "tiff", "dest_format": "jxl"}
    assert menu._count_origin_files(wf, 0) == 1     # flat
    assert menu._count_origin_files(wf, 3) == 3     # recursive


def test_plain_mode_8_no_longer_arms_delete(menu, monkeypatch):
    """Mode 8 is 'in-place recursive'. Deleting is [D], and only [D]."""
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    it = iter(["8"])
    monkeypatch.setattr("builtins.input", lambda *a: next(it))
    wf = {"origin_format": "tiff", "dest_format": "jxl", "input_dir": "."}
    assert menu._wizard_select_mode(wf) is True
    assert wf["mode"] == 8
    assert wf.get("delete_source") is not True
