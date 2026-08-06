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
# The wizard's [D] entry
# ---------------------------------------------------------------------------

MODES_STUB = [(str(i), f"mode {i}", "") for i in range(9)]


def _gateway(menu, monkeypatch, answers, layout="3", origin="tiff", dest="jxl"):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda *a: next(it))
    wf = {"origin_format": origin, "dest_format": dest, "input_dir": ".",
          "mode": None}
    ok = menu._wizard_delete_gateway(wf, MODES_STUB)
    return ok, wf


def test_D_arms_delete_on_the_chosen_layout(menu, monkeypatch):
    # gate 1 yes, layout 3, gate 2 yes, verify no
    ok, wf = _gateway(menu, monkeypatch, ["y", "3", "y", "n"])
    assert ok is True
    assert wf["mode"] == 3
    assert wf["delete_source"] is True
    assert wf.get("verify_roundtrip") is False


def test_D_can_arm_verify_too(menu, monkeypatch):
    ok, wf = _gateway(menu, monkeypatch, ["y", "5", "y", "y"])
    assert wf["mode"] == 5
    assert wf["verify_roundtrip"] is True


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
