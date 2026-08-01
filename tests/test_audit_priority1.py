#!/usr/bin/env python3
"""Regressions for the priority-1 audit fixes:

  * E1/E2 — FALSE POSITIVES (kept as locking tests): the audit suspected the
    exiftool `-s` output was a bare value breaking get_exif_software and
    read_existing_creator_tool. Proven wrong against real exiftool + a real
    Capture One TIFF: single `-s` still prints "Tag : value", the parse was
    correct, and D50 "auto" already fired. No code change; tests pin the
    real output format.
  * W3 — the children's argparse accepts unambiguous abbreviations
    (allow_abbrev=True), so `--delete-s` deletes sources exactly like
    `--delete-source`, but _flags_request_delete only matched the full
    spelling: a preset with `--delete-s --delete-c` passed the unattended
    gate and deleted originals with no confirmation anywhere.
  * T1 — auto mode with `--bit-depth 16` and no `--format` (default JPEG)
    resolved .png output paths but sent fmt="jpeg" to the worker, so djxl
    ran the JPEG branch without --bits_per_sample=16 (8-bit PNGs, silently).
    The JPEG+16-bit -> PNG pre-switch tested `args.format == "jpeg"`, which
    is False when args.format is None (or "jpg").
  * D1 — mode 8 + staging + a FAILED move over a pre-existing output: the
    stale old file at the final path passed exists()+integrity and the
    source was deleted even though this run's verified output never left
    staging. The delete gate must certify THIS run's output, not whatever
    was already sitting there. (Same hole in all three children.)
"""

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_tiff_encoder as enc
import jxl_tiff_decoder as dec
import jxl_jpeg_transcoder as tr
import jxl_photo as wp


def _args(tmp_path, **kw):
    base = dict(
        input=tmp_path, output=None, mode=1, workers=2, effort=7,
        overwrite=False, sync=False, staging=None, dry_run=False,
        delete_source=False, no_md5=False, no_verify=False, decode=False,
        force_transcode=False, force_convert=False, format=None, quality=95,
        distance=1.0, bit_depth=None, icc_profile=None, ram=True,
        output_name="converted", output_suffix="_converted",
        rename_from="", rename_to="", from_jxl=False, from_jpeg=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


# ---------------------------------------------------------------------------
# E1/E2 — LOCKING tests (the audit's suspected bug here was a FALSE POSITIVE,
# proven against real exiftool + a real Capture One TIFF): exiftool with a
# single -s still prints "Tag Name        : value" (only -s -s -s prints the
# bare value), so the " : " parse was already correct and D50 "auto" already
# fired. These tests pin the REAL output format so a future "simplification"
# of the parser (or a switch to -s -s -s in the argfile) gets caught.
# ---------------------------------------------------------------------------

def _stub_exiftool_value(monkeypatch, value):
    monkeypatch.setattr(enc, "_get_exiftool_cmd", lambda: "exiftool")
    monkeypatch.setattr(enc, "subprocess",
                        SimpleNamespace(run=lambda *a, **k: SimpleNamespace(
                            returncode=0, stdout=value)))
    enc.get_exif_software.cache_clear()


def test_get_exif_software_parses_real_s_output(monkeypatch):
    """Verified against ExifTool 13.x: single -s pads the tag name and keeps
    the " : " separator."""
    _stub_exiftool_value(monkeypatch,
                         "Software                        : Capture One Windows\n")
    assert enc.get_exif_software("photo.tif") == "Capture One Windows"


def test_d50_auto_fires_for_capture_one_software(monkeypatch):
    _stub_exiftool_value(monkeypatch,
                         "Software                        : Capture One Windows\n")
    monkeypatch.setattr(enc, "D50_PATCH_MODE", "auto")
    assert enc.should_apply_d50_patch("photo.tif") is True


def test_read_existing_creator_tool_preserves_icc_prefix(monkeypatch, tmp_path):
    """Real -s output of an "ICC:<base64>" CreatorTool is
    "Creator Tool : ICC:<base64>" — the parse must stop at the FIRST " : "
    and keep the ICC: prefix, or the stale-blob cleanup is defeated."""
    xmp = tmp_path / "x.xmp"
    xmp.write_text("<x/>", encoding="utf-8")
    monkeypatch.setattr(enc, "_run_exiftool_argfile",
                        lambda *a, **k: SimpleNamespace(
                            returncode=0, stdout="Creator Tool : ICC:QUJDRA==\n"))
    assert enc.read_existing_creator_tool(xmp) == "ICC:QUJDRA=="


def test_stale_icc_stripped_with_real_reader(monkeypatch, tmp_path):
    """End to end: with the real reader (only exiftool stubbed), an old
    ICC blob in CreatorTool must be dropped, not carried forward."""
    monkeypatch.setattr(enc, "_run_exiftool_argfile",
                        lambda *a, **k: SimpleNamespace(
                            returncode=0,
                            stdout="Creator Tool : OldApp | ICC:T0xESUJD\n"))
    xmp = tmp_path / "x.xmp"
    xmp.write_text("<x/>", encoding="utf-8")
    args_file = enc.build_metadata_injection_args(
        tmp_path / "src.tif", tmp_path / "out.jxl", tmp_path,
        exif_bin=None, icc_bytes=b"\x00" * 200, xmp_original=xmp,
    )
    content = args_file.read_text(encoding="utf-8")
    creator_line = [ln for ln in content.splitlines() if "CreatorTool=" in ln][0]
    assert "T0xESUJD" not in creator_line, "stale ICC blob survived"
    assert creator_line.count("ICC:") == 1


# ---------------------------------------------------------------------------
# W3 — _flags_request_delete must catch argparse abbreviations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flags", [
    "--delete-s",
    "--delete-so",
    "--delete_s",
    "--delete-s --delete-c",
    '--effort 9 --delete-source --delete-c',
])
def test_delete_flag_abbreviations_are_detected(flags):
    assert wp._flags_request_delete(flags) is True


@pytest.mark.parametrize("flags", [
    # Ambiguous prefixes (argparse rejects these itself: they also match
    # --delete-confirm-off), the confirm flag alone, and near-misses.
    "--delete",
    "--delete-",
    "--delete-c",
    "--delete-confirm-off",
    "--delete-source-never",
    "--d",
    "--effort 9",
    None,
    "",
])
def test_non_delete_or_ambiguous_flags_are_not_flagged(flags):
    assert wp._flags_request_delete(flags) is False


def test_abbreviated_delete_is_refused_unattended(tmp_path, monkeypatch, capsys):
    """The full gate: a preset storing `--delete-s --delete-c` must be
    refused by the unattended path exactly like the full spelling."""
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    launched = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600: (launched.append(cmd), 0)[1])

    src = tmp_path / "photos"
    src.mkdir()
    session = {n: None for n in wp.ToolConfig.__dataclass_fields__ if n.startswith("last_")}
    session.update({
        "last_output_mode": "8",
        "last_workers": 4,
        "last_effort": 7,
        "last_distance": 0.1,
        "last_origin_format": "tiff",
        "last_dest_format": "jxl",
        "last_conversion_type": "jxl_tiff_encoder",
        "last_advanced_options": {"overwrite": False, "sync": True},
        "last_input_dir": str(src),
        "last_expert_flags": "--delete-s --delete-c",
    })
    status = {k: True for k in
              ("cjxl", "djxl", "exiftool", "magick", "tifffile", "pillow", "imagecodecs")}

    ok = menu._run_saved_session(session, status,
                                 answers={"overwrite": False, "dry_run": False})

    assert ok is False
    assert launched == [], "child launched with an abbreviated delete flag unattended"
    assert "cannot be given unattended" in " ".join(capsys.readouterr().out.split())


# ---------------------------------------------------------------------------
# T1 — 16-bit + default/JPEG format must switch to PNG BEFORE pairs are built
# ---------------------------------------------------------------------------

def test_auto_bitdepth16_without_format_switches_to_png(monkeypatch, tmp_path):
    """cmd_auto: --bit-depth 16 with no --format (args.format is None).
    Pre-fix the switch tested `args.format == 'jpeg'` and never fired."""
    (tmp_path / "a.jxl").write_bytes(b"\x00" * 32)
    tr.setup_logger()
    monkeypatch.setattr(tr, "has_jbrd_box", lambda p: False)
    seen = {}

    def fake_group(files, args, **kw):
        if kw.get("collect_only") is not None:
            kw["collect_only"].extend((f, f.parent / (f.stem + ".png")) for f in files)
            return None
        seen["format"] = args.format
        return {"ok": 0, "err": 0, "skipped": 0}

    monkeypatch.setattr(tr, "_process_file_group", fake_group)
    tr.cmd_auto(_args(tmp_path, format=None, bit_depth=16))
    assert seen.get("format") == "png", f"worker got fmt={seen.get('format')!r} (8-bit PNG path)"


def test_convert_jpg_alias_bitdepth16_switches_to_png(monkeypatch, tmp_path):
    """cmd_convert: --format jpg --bit-depth 16. The alias also missed the
    `== 'jpeg'` check."""
    (tmp_path / "a.jxl").write_bytes(b"\x00" * 32)
    tr.setup_logger()
    captured = {}

    def fake_pgc(group_pairs, workers, *a, **kw):
        captured["fmt"] = kw.get("fmt", a[3] if len(a) > 3 else None)
        captured["ext"] = group_pairs[0][1].suffix
        return [], set()

    monkeypatch.setattr(tr, "process_group_convert", fake_pgc)
    tr.cmd_convert(_args(tmp_path, format="jpg", bit_depth=16), from_jxl=True)
    assert captured["fmt"] == "png"
    assert captured["ext"] == ".png", "pairs were built for .jpg but worker gets PNG"


# ---------------------------------------------------------------------------
# D1 — a failed staging move must block mode-8 deletion (all three children)
# ---------------------------------------------------------------------------

def _raise_locked(*a, **k):
    raise OSError("destination locked")


def test_decoder_mode8_failed_staging_move_keeps_source(tmp_path, monkeypatch):
    """The stale pre-existing TIFF passes exists()+integrity, but the fresh
    output never left staging — the JXL source must be KEPT."""
    staging = tmp_path / "staging"
    main = tmp_path / "scan.jxl"
    main.write_bytes(b"\x00" * 16)
    final = tmp_path / "scan.tif"
    tifffile.imwrite(final, np.zeros((8, 8, 3), dtype=np.uint16), photometric="rgb")

    dec.setup_logger()
    monkeypatch.setattr(dec, "DELETE_SOURCE", True)
    monkeypatch.setattr(dec, "TEMP2_DIR", str(staging))
    monkeypatch.setattr(dec, "_verify_tiff_integrity", lambda p: True)
    monkeypatch.setattr(dec.shutil, "move", _raise_locked)

    def fake_convert(main_jxl, entries, write_path, final_tiff, target_icc):
        write_path.parent.mkdir(parents=True, exist_ok=True)
        write_path.write_bytes(b"fresh tiff")
        return (str(main_jxl), "ok", str(final_tiff))

    monkeypatch.setattr(dec, "convert_multipage_jxl_group", fake_convert)
    task = {
        "type": "multi",
        "main_jxl": main,
        "entries": [(main, 0, False, False, 0, False, None)],
        "ignored_thumbs": [],
        "final_tiff": final,
    }
    dec.process_group([task], 1, 8)
    assert main.exists(), "source deleted although the fresh TIFF never left staging"


def test_encoder_mode8_failed_staging_move_keeps_source(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    tiff = tmp_path / "photo.tif"
    tiff.write_bytes(b"\x00" * 16)
    final = tmp_path / "photo.jxl"
    final.write_bytes(b"\x00" * 32)  # stale pre-existing output

    enc.setup_logger()
    monkeypatch.setattr(enc, "DELETE_SOURCE", True)
    monkeypatch.setattr(enc, "TEMP2_DIR", str(staging))
    monkeypatch.setattr(enc, "_verify_jxl_integrity", lambda p: True)
    monkeypatch.setattr(enc.shutil, "move", _raise_locked)

    def fake_convert_one(t, w, f, p, th, sft, spl, g):
        w.parent.mkdir(parents=True, exist_ok=True)
        w.write_bytes(b"fresh jxl")
        return ((str(t), p), "ok", str(f), "md5")

    monkeypatch.setattr(enc, "convert_one", fake_convert_one)
    items = [(tiff, final, 0, False, 0, 3)]
    enc.process_group(items, 1, 8)
    assert tiff.exists(), "source deleted although the fresh JXL never left staging"


def test_transcoder_mode8_failed_staging_move_keeps_source(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    src = tmp_path / "photo.jxl"
    src.write_bytes(b"\x00" * 32)
    final = tmp_path / "photo.jpg"
    final.write_bytes(b"\xff\xd8" + b"\x00" * 100 + b"\xff\xd9")

    tr.setup_logger()
    monkeypatch.setattr(tr, "_tool_at_least", lambda *a: True)  # djxl>=0.12
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "TEMP2_DIR", str(staging))
    monkeypatch.setattr(tr, "_verify_file_integrity", lambda p: True)
    monkeypatch.setattr(tr.shutil, "move", _raise_locked)

    def fake_decode(s, w, f, verify, reconvert_val, smart):
        w.parent.mkdir(parents=True, exist_ok=True)
        w.write_bytes(b"\xff\xd8fresh\xff\xd9")
        return (str(s), "ok", str(f), True)

    monkeypatch.setattr(tr, "decode_one_transcode", fake_decode)
    tr.process_group_transcode([(src, final)], 1, True, False, 8, False, False)
    assert src.exists(), "source deleted although the fresh JPEG never left staging"


def test_transcoder_successful_move_still_deletes(tmp_path, monkeypatch):
    """The new gate must not become a blanket KEEP: a move that worked still
    allows deletion."""
    staging = tmp_path / "staging"
    src = tmp_path / "photo.jxl"
    src.write_bytes(b"\x00" * 32)
    final = tmp_path / "photo.jpg"

    tr.setup_logger()
    monkeypatch.setattr(tr, "_tool_at_least", lambda *a: True)
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "TEMP2_DIR", str(staging))
    monkeypatch.setattr(tr, "_verify_file_integrity", lambda p: True)

    def fake_decode(s, w, f, verify, reconvert_val, smart):
        w.parent.mkdir(parents=True, exist_ok=True)
        w.write_bytes(b"\xff\xd8fresh\xff\xd9")
        return (str(s), "ok", str(f), True)

    monkeypatch.setattr(tr, "decode_one_transcode", fake_decode)
    tr.process_group_transcode([(src, final)], 1, True, False, 8, False, False)
    assert not src.exists(), "successful staged delivery must still allow deletion"
    assert final.read_bytes() == b"\xff\xd8fresh\xff\xd9"


def test_process_group_convert_reports_moved_finals(tmp_path, monkeypatch):
    """Plumbing for the two convert-path delete gates: the returned
    moved_finals set must contain only moves that actually happened."""
    staging = tmp_path / "staging"
    src = tmp_path / "a.jxl"
    src.write_bytes(b"\x00" * 32)
    final = tmp_path / "a.png"

    tr.setup_logger()
    monkeypatch.setattr(tr, "TEMP2_DIR", str(staging))

    def fake_decode(s, w, f, *a):
        w.parent.mkdir(parents=True, exist_ok=True)
        w.write_bytes(b"png")
        return (str(s), "ok", str(f), None)

    monkeypatch.setattr(tr, "decode_to_image", fake_decode)
    _, moved = tr.process_group_convert(
        [(src, final)], 1, direction="from_jxl", quality=95, distance=1.0,
        fmt="png", bit_depth=16, output_icc=None, use_ram=False, effort=7,
        reconvert_val=False, use_internal_srgb=False, smart=False)
    import os
    assert os.path.normcase(str(final)) in moved

    # And with the move failing, it must be empty.
    src2 = tmp_path / "b.jxl"
    src2.write_bytes(b"\x00" * 32)
    final2 = tmp_path / "b.png"
    monkeypatch.setattr(tr.shutil, "move", _raise_locked)
    _, moved2 = tr.process_group_convert(
        [(src2, final2)], 1, direction="from_jxl", quality=95, distance=1.0,
        fmt="png", bit_depth=16, output_icc=None, use_ram=False, effort=7,
        reconvert_val=False, use_internal_srgb=False, smart=False)
    assert moved2 == set()
