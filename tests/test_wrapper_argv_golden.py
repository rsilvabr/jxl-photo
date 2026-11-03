#!/usr/bin/env python3
"""Golden argv of the wrapper's command builders (Delivery A, phase 1).

The wrapper used to build each child's command line twice: inline in
execute_workflow (a direct run) and in _build_manifest_entry_cmd (one manifest
row). Phase 1 of AI_tools/261103_Claude_plan_wrapper-redesign.md replaces both
with one registry-driven builder. This test is the safety net of that
refactor: the argv of a matrix of workflows — every direction, the usual
modes, deletion, derivatives, staging, exclusions, renames, expert flags — was
captured from the code BEFORE the refactor into
tests/fixtures/wrapper_argv_golden.json, and the builders must keep producing
the same command lines.

The comparison is canonical, not textual: the positionals must be equal and in
place, the expert-flag tail equal and LAST (argparse's last-one-wins makes its
order matter), and the remaining (flag, value) pairs equal as a multiset — the
two old builders emitted the same flags in different orders, and argparse does
not care about the order of distinct options.

Regenerate ONLY from a commit whose argv is known good:
    JXLPHOTO_REGEN_ARGV_GOLDEN=1 python -m pytest tests/test_wrapper_argv_golden.py
(the file then records what the current code produces, so a regeneration
after a change would silently bless that change).
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "wrapper_argv_golden.json"
REGEN = os.environ.get("JXLPHOTO_REGEN_ARGV_GOLDEN") == "1"

STATUS = {k: True for k in
          ("cjxl", "djxl", "exiftool", "magick", "tifffile", "pillow", "imagecodecs")}

# A hostile tree (AGENTS.md, Verification): Japanese, [ ], spaces, ( ) and %.
EVENT = "250118_吾妻山公園_菜の花 [FINAL]/01_Z7"
SCRIPT_OF = {("tiff", "jxl"): "jxl_tiff_encoder.py",
             ("jxl", "tiff"): "jxl_tiff_decoder.py",
             ("jxl", "jxl"): "jxl_recompressor.py"}


def _script(origin, dest):
    return SCRIPT_OF.get((origin, dest), "jxl_jpeg_transcoder.py")


def _wf(origin, dest, conv, mode, **over):
    """A workflow dict as the wizard hands it to execute_workflow."""
    wf = {
        "input_dir": "<ROOT>/" + EVENT,
        "origin_format": origin, "dest_format": dest, "conversion_type": conv,
        "mode": mode, "workers": 6, "staging": None, "icc_profile": None,
        "use_ram": True, "compression": None, "bit_depth": None,
        "add_preview": True, "dry_run": False, "advanced_options": {},
        "expert_flags": "", "mode_config": {}, "exclude_folders": None,
    }
    wf.update(over)
    return wf


# name -> (kind, workflow, manifest row (source, dest, mode) or None)
CASES = {
    # --- TIFF -> JXL (encoder) ---------------------------------------------
    "enc_mode7_full": ("direct", _wf(
        "tiff", "jxl", "jxl_tiff_encoder", 7, distance=0.1, effort=7,
        staging="<ROOT>/stg 5%",
        exclude_folders="tmp;old (2)",
        mode_config={"export_subfolder": "TIFF16", "export_jxl_folder": "16B_JXL"},
        advanced_options={"sync": True, "overwrite": False, "d50_patch": "auto",
                          "encode_tag": "xmp", "multipage_mode": "split",
                          "thumbnail_mode": "auto", "thumbnail_suffix": "_thumbnail",
                          "scan_distance": "0"}), None),
    "enc_mode8_delete": ("direct", _wf(
        "tiff", "jxl", "jxl_tiff_encoder", 8, distance=0.0, effort=9,
        use_ram=False, dry_run=True,
        advanced_options={"overwrite": True, "sync": False, "strip": True,
                          "modular": "on", "delete_source": True,
                          "verify_roundtrip": True, "delete_skipped": True,
                          "provenance": "content"}), None),
    "enc_mode2_expert": ("direct", _wf(
        "tiff", "jxl", "jxl_tiff_encoder", 2, distance=1.0, effort=7,
        mode_config={"output_dir": "<ROOT>/out [2]"},
        expert_flags='--buffering 1 --no-preflight',
        advanced_options={"sync": True, "overwrite_guard": False}), None),
    # --- JXL -> TIFF (decoder) ---------------------------------------------
    "dec_mode6_matrix": ("direct", _wf(
        "jxl", "tiff", "jxl_tiff_decoder", 6, compression="lzw", bit_depth=8,
        add_preview=False, staging="<ROOT>/stg 5%", exclude_folders="16B_JXL_small",
        advanced_options={"matrix": True, "target_icc": "sRGB",
                          "no_icc_cleanup": True, "thumbnail_handling": "ignore",
                          "thumbnail_suffix": "_t", "no_reconstruct_multipage": True,
                          "depth_policy": "force16", "sync": True}), None),
    "dec_mode8_delete": ("direct", _wf(
        "jxl", "tiff", "jxl_tiff_decoder", 8,
        advanced_options={"basic": True, "delete_source": True,
                          "delete_skipped": True, "overwrite": True}), None),
    "dec_mode0_guard_off": ("direct", _wf(
        "jxl", "tiff", "jxl_tiff_decoder", 0,
        advanced_options={"none": True, "overwrite_guard": False, "sync": True}), None),
    # --- JXL -> JXL (recompressor) -----------------------------------------
    "rec_mode7_derivative": ("direct", _wf(
        "jxl", "jxl", "jxl_recompress", 7, distance=1.0, effort=9,
        staging="<ROOT>/stg 5%", exclude_folders="old",
        mode_config={"export_subfolder": "16B_JXL", "export_jxl_folder": "16B_JXL_sRGB"},
        advanced_options={"output_icc": "sRGB", "resize_mode": "long",
                          "resize_value": 2048, "allow_upscale": False,
                          "sharpen": "screen", "rename_from": "ProPhoto",
                          "rename_to": "sRGB", "rederive_on_encode_change": True,
                          "rederive_on_lower_effort": False,
                          "on_downgrade": "copy", "on_regeneration": "convert",
                          "on_unknown": "convert", "on_scan": "skip",
                          "jbrd_policy": "copy", "no_keep_smaller": True,
                          "sync": True}), None),
    "rec_mode8_in_place": ("direct", _wf(
        "jxl", "jxl", "jxl_recompress", 8, distance=2.0, effort=7,
        advanced_options={"on_downgrade": "copy", "sync": True}), None),
    "rec_mode1_delete": ("direct", _wf(
        "jxl", "jxl", "jxl_recompress", 1, distance=1.0, effort=7,
        advanced_options={"delete_source": True, "verify_roundtrip": True,
                          "delete_skipped": True, "provenance": "path",
                          "overwrite": True}), None),
    "rec_mode2_out": ("direct", _wf(
        "jxl", "jxl", "jxl_recompress", 2, distance=3.0, effort=7,
        mode_config={"output_dir": "<ROOT>/small (1)"},
        advanced_options={"sync": True}), None),
    # --- transcoder ---------------------------------------------------------
    "tra_jpeg_lossless_mode3_delete": ("direct", _wf(
        "jpeg", "jxl", "transcode_lossless", 3, effort=7,
        advanced_options={"no_md5": True, "delete_source": True,
                          "delete_skipped": True, "sync": True}), None),
    "tra_jpeg_lossy_mode2": ("direct", _wf(
        "jpeg", "jxl", "convert_lossy", 2, distance=1.5, effort=7,
        mode_config={"output_dir": "<ROOT>/out [2]"},
        advanced_options={"output_suffix": "_jxl", "overwrite": True}), None),
    "tra_jxl2jpeg_auto_mode6_shaping": ("direct", _wf(
        "jxl", "jpeg", "jxl_to_jpeg_auto", 6, quality=90, effort=7,
        icc_profile="sRGB", exclude_folders="old",
        advanced_options={"resize_mode": "percent", "resize_value": 50.0,
                          "allow_upscale": False, "sharpen": "print",
                          "sync": True}), None),
    "tra_jxl2jpeg_lossless_mode8_delete": ("direct", _wf(
        "jxl", "jpeg", "jxl_to_jpeg_lossless", 8, effort=7,
        advanced_options={"delete_source": True, "auto_repair_jbrd": True,
                          "no_verify": True}), None),
    "tra_jxl2png_mode1": ("direct", _wf(
        "jxl", "png", "jxl_to_png", 1, effort=7, bit_depth=16,
        advanced_options={"sync": True}), None),
    # The lossy jxl_to_jpeg_force + delete case it replaced is refused now
    # (T-B, tests/test_lossy_decode_delete.py); expert flags + an expert
    # delete are pinned on the bit-exact recovery instead.
    "tra_jxl2jpeg_lossless_expert_delete": ("direct", _wf(
        "jxl", "jpeg", "jxl_to_jpeg_lossless", 0, effort=7,
        expert_flags="--delete-source --compress-metadata",
        advanced_options={"overwrite": True}), None),
    # --- manifest rows ------------------------------------------------------
    "m_enc_mode6": ("manifest", _wf(
        "tiff", "jxl", "jxl_tiff_encoder", 99, distance=0.1, effort=7,
        staging="<ROOT>/stg 5%", exclude_folders="tmp",
        mode_config={"export_marker": "_EXPORT"},
        advanced_options={"sync": True, "multipage_mode": "split",
                          "thumbnail_mode": "auto", "scan_distance": "same"}),
        ("<ROOT>/" + EVENT, "<ROOT>/" + EVENT, 6)),
    "m_enc_mode0_dest": ("manifest", _wf(
        "tiff", "jxl", "jxl_tiff_encoder", 99, distance=0.5, effort=7,
        use_ram=False,
        advanced_options={"delete_source": True, "verify_roundtrip": True,
                          "provenance": "adopt", "adopt_scan": False}),
        ("<ROOT>/" + EVENT, "<ROOT>/archive [x]", 0)),
    "m_dec_mode7": ("manifest", _wf(
        "jxl", "tiff", "jxl_tiff_decoder", 99, compression="zip", bit_depth=16,
        mode_config={"export_subfolder": "16B_JXL"},
        advanced_options={"depth_policy": "preserve_original", "sync": True}),
        ("<ROOT>/" + EVENT, "<ROOT>/" + EVENT, 7)),
    "m_rec_mode8_in_place": ("manifest", _wf(
        "jxl", "jxl", "jxl_recompress", 99, distance=1.0, effort=7,
        advanced_options={"on_downgrade": "skip", "on_regeneration": "copy",
                          "sync": True}),
        ("<ROOT>/" + EVENT, "", 8)),
    "m_rec_mode2_dest_rename": ("manifest", _wf(
        "jxl", "jxl", "jxl_recompress", 99, distance=2.0, effort=7,
        mode_config={"export_jxl_folder": "16B_JXL_small"},
        advanced_options={"output_icc": "AdobeRGB", "rename_from": "ProPhoto",
                          "rename_to": "", "sync": True}),
        ("<ROOT>/" + EVENT, "<ROOT>/small (1)", 2)),
    "m_tra_jxl2jpeg_delete_icc": ("manifest", _wf(
        "jxl", "jpeg", "jxl_to_jpeg_auto", 99, quality=85, effort=7,
        icc_profile="sRGB", exclude_folders="old",
        advanced_options={"delete_source": True, "delete_skipped": True,
                          "sync": True}),
        ("<ROOT>/" + EVENT, "<ROOT>/" + EVENT, 6)),
    "m_tra_jxl2jpeg_force_rename_shaping": ("manifest", _wf(
        "jxl", "jpeg", "jxl_to_jpeg_force", 99, quality=90, effort=7,
        advanced_options={"resize_mode": "long", "resize_value": 1600,
                          "allow_upscale": True, "sharpen": "screen",
                          "rename_from": "16B", "rename_to": "web",
                          "delete_source": True, "overwrite": True}),
        ("<ROOT>/" + EVENT, "<ROOT>/web [1600]", 2)),
    "m_tra_jpeg_lossless_mode2": ("manifest", _wf(
        "jpeg", "jxl", "transcode_lossless", 99, effort=9,
        dry_run=True, expert_flags="--no-md5",
        advanced_options={"delete_source": True, "no_verify": True}),
        ("<ROOT>/" + EVENT, "<ROOT>/jxl out", 2)),
    "m_tra_jxl2png_mode0_file": ("manifest", _wf(
        "jxl", "png", "jxl_to_png", 99, effort=7, bit_depth=8,
        advanced_options={"sync": True}),
        ("<ROOT>/" + EVENT + "/DSC_0001 (2).jxl",
         "<ROOT>/" + EVENT + "/DSC_0001 (2).jxl", 0)),
}


def _load_wrapper():
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(
            "golden_jxl_photo", str(SCRIPTS / "jxl_photo.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(SCRIPTS))
    return mod


def _subst(value, root: str):
    """Replace the <ROOT> placeholder inside a (possibly nested) value."""
    if isinstance(value, str):
        return value.replace("<ROOT>", root)
    if isinstance(value, dict):
        return {k: _subst(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [_subst(v, root) for v in value]
    if isinstance(value, tuple):
        return tuple(_subst(v, root) for v in value)
    return value


def _unsubst(argv, root: str):
    out = []
    for a in argv:
        a = str(a)
        if a == sys.executable:
            a = "<PY>"
        a = a.replace(str(SCRIPTS), "<SCRIPTS>").replace(root, "<ROOT>")
        out.append(a.replace("\\", "/"))
    return out


def _argv_for(wp, menu, monkeypatch, name, tmp_path):
    kind, wf, row = CASES[name]
    root = str(tmp_path)
    wf = _subst(json.loads(json.dumps(wf)), root)
    Path(wf["input_dir"]).mkdir(parents=True, exist_ok=True)
    if kind == "direct":
        calls = []
        monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                            lambda self, cmd, idle_timeout=None:
                            (calls.append(list(map(str, cmd))), 0)[1])
        monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode", lambda self: True)
        monkeypatch.setattr(wp.InteractiveMenu, "_confirm_in_place_replace",
                            lambda self: True)
        monkeypatch.setattr(wp.InteractiveMenu, "_confirm_lossy_delete_skipped",
                            lambda self, workflow: None)
        assert menu.execute_workflow(wf, STATUS), f"{name}: execute_workflow refused"
        assert len(calls) == 1, f"{name}: expected one child, got {len(calls)}"
        argv = calls[0]
    else:
        source, dest_path, mode = _subst(row, root)
        script = str(SCRIPTS / _script(wf["origin_format"], wf["dest_format"]))
        argv = menu._build_manifest_entry_cmd(
            script, source, dest_path, mode, wf["origin_format"], wf["dest_format"],
            wf["workers"], wf, wf.get("advanced_options", {}))
    return _unsubst(argv, root)


def _canonical(argv, expert_flags: str, wp):
    """(positionals, sorted (flag, value) pairs, expert tail)."""
    argv = list(argv)
    tail = wp._split_expert_flags(expert_flags or "")
    rest_end = len(argv)
    if tail:
        # The expert tokens are the last ones, possibly followed by the
        # --delete-confirm-off _append_expert_flags adds for a delete there.
        end = len(argv) - (1 if argv[-1] == "--delete-confirm-off"
                           and tail[-1] != "--delete-confirm-off" else 0)
        assert argv[end - len(tail):end] == tail, (argv, tail)
        rest_end = end - len(tail)
        tail = argv[rest_end:]
    i = 0
    positionals = []
    while i < rest_end and not argv[i].startswith("--"):
        positionals.append(argv[i])
        i += 1
    pairs = []
    while i < rest_end:
        flag = argv[i]
        assert flag.startswith("--"), (argv, i)
        if i + 1 < rest_end and not argv[i + 1].startswith("--"):
            pairs.append((flag, argv[i + 1]))
            i += 2
        else:
            pairs.append((flag, None))
            i += 1
    return positionals, sorted(pairs, key=lambda p: (p[0], p[1] or "")), tail


@pytest.fixture
def wp():
    return _load_wrapper()


@pytest.fixture
def menu(wp, tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


def _golden():
    if not FIXTURE.exists():
        return {}
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(CASES))
def test_builder_argv_matches_the_golden(name, wp, menu, monkeypatch, tmp_path):
    argv = _argv_for(wp, menu, monkeypatch, name, tmp_path / "case")
    if REGEN:
        data = _golden()
        data[name] = argv
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(data, ensure_ascii=False, indent=1,
                                      sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip("golden regenerated")
    golden = _golden()
    assert name in golden, f"{name} has no golden argv — regenerate from a known-good commit"
    expert = CASES[name][1].get("expert_flags", "")
    assert _canonical(argv, expert, wp) == _canonical(golden[name], expert, wp), (
        f"{name}: the command line changed\n  now:    {argv}\n  golden: {golden[name]}")


def test_every_golden_case_still_exists():
    """A case removed from CASES must also leave the fixture (no stale blessing)."""
    if REGEN:
        pytest.skip("regenerating")
    assert set(_golden()) == set(CASES)
