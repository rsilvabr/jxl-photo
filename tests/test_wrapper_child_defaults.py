#!/usr/bin/env python3
"""Round 51: the wizard's defaults are the child scripts' settings.

The wizard puts the multi-page and thumbnail modes, the D50 patch, the encode
tag, RAM staging, TIFF compression, bit depth, the JPEG preview, the depth
policy, provenance and the recompressor's distance on the child's command
line. Its default for each was a literal copy of the child's shipped value
(`last_multipage_mode or 'split'`), so an edit at the top of a script
(MULTIPAGE_TIFF_MODE = "split_all" in jxl_tiff_encoder.py) was overridden by
every wizard run and preset that had no remembered answer of its own. Those
defaults now read the child (_child_default); a remembered `last_*` answer
still comes first.

Pre-fix proof: point the tests at the HEAD wrapper (the children are the
repository's, imported from sys.path):
    git show HEAD:jxl_photo.py > <tmp>/jxl_photo.py
    $env:JXLPHOTO_SCRIPTS_UNDER_TEST = "<tmp>"
"""

import importlib.util
import os
import re
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)
sys.path.insert(0, str(REPO))
import jxl_jpeg_transcoder as tra  # noqa: E402
import jxl_recompressor as rec  # noqa: E402
import jxl_tiff_decoder as dec  # noqa: E402
import jxl_tiff_encoder as enc  # noqa: E402

STATUS = {k: True for k in
          ("cjxl", "djxl", "exiftool", "magick", "tifffile", "pillow", "imagecodecs")}

# A user's edits at the top of each child script.
ENCODER_EDIT = {
    "MULTIPAGE_TIFF_MODE": "split_all", "THUMBNAIL_MODE": "include",
    "THUMBNAIL_SUFFIX": "_thumb", "D50_PATCH_MODE": "off",
    "ENCODE_TAG_MODE": "software", "USE_RAM_FOR_PNG": False,
    "EMBED_JPEG_THUMBNAIL": True, "PROVENANCE_CHECK": "content",
}
DECODER_EDIT = {
    "THUMBNAIL_HANDLING": "ignore", "THUMBNAIL_SUFFIX": "_t",
    "DEPTH_POLICY": "force16", "TIFF_COMPRESSION": "lzw",
    "DJXL_OUTPUT_DEPTH": 8, "ADD_JPEG_PREVIEW": False,
    "RECONSTRUCT_MULTIPAGE": False, "PROVENANCE_CHECK": "content",
}


def _load_wrapper():
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(
            "r51_jxl_photo", str(SCRIPTS / "jxl_photo.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(SCRIPTS))
    return mod


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


@pytest.fixture
def launched(wp, monkeypatch):
    calls = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600:
                        (calls.append(list(map(str, cmd))), 0)[1])
    return calls


@pytest.fixture
def edited_encoder(monkeypatch):
    for name, value in ENCODER_EDIT.items():
        monkeypatch.setattr(enc, name, value)


@pytest.fixture
def edited_decoder(monkeypatch):
    for name, value in DECODER_EDIT.items():
        monkeypatch.setattr(dec, name, value)


def _session(wp, **over):
    s = {n: None for n in wp.ToolConfig.__dataclass_fields__ if n.startswith("last_")}
    s.update({"last_output_mode": "0", "last_workers": 4, "last_effort": 7})
    s.update(over)
    return s


def _flag(cmd, name):
    return cmd[cmd.index(name) + 1] if name in cmd else None


# ---------------------------------------------------------------------------
# Step 6A answered "no": the options the wizard passes without asking
# ---------------------------------------------------------------------------

def test_skipped_advanced_options_follow_the_encoder(menu, monkeypatch, edited_encoder):
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    monkeypatch.setattr(menu, "_wizard_parameters_expert", lambda wf: True)
    wf = {"origin_format": "tiff", "dest_format": "jxl",
          "conversion_type": "jxl_tiff_encoder", "overwrite_mode": "2"}

    assert menu._wizard_parameters_advanced(wf, STATUS)

    adv = wf["advanced_options"]
    assert adv["multipage_mode"] == "split_all"
    assert adv["thumbnail_mode"] == "include"
    assert adv["thumbnail_suffix"] == "_thumb"
    assert adv["d50_patch"] == "off"
    assert adv["encode_tag"] == "software"


def test_skipped_advanced_options_follow_the_decoder(menu, monkeypatch, edited_decoder):
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    monkeypatch.setattr(menu, "_wizard_parameters_expert", lambda wf: True)
    wf = {"origin_format": "jxl", "dest_format": "tiff",
          "conversion_type": "jxl_tiff_decoder", "overwrite_mode": "2"}

    assert menu._wizard_parameters_advanced(wf, STATUS)

    adv = wf["advanced_options"]
    assert adv["thumbnail_handling"] == "ignore"
    assert adv["thumbnail_suffix"] == "_t"
    assert adv["depth_policy"] == "force16"
    assert adv["no_reconstruct_multipage"] is True


def test_a_remembered_answer_still_wins(menu, monkeypatch, edited_encoder):
    menu.config.config.last_multipage_mode = "skip"
    menu.config.config.last_thumbnail_suffix = "_mine"
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    monkeypatch.setattr(menu, "_wizard_parameters_expert", lambda wf: True)
    wf = {"origin_format": "tiff", "dest_format": "jxl",
          "conversion_type": "jxl_tiff_encoder", "overwrite_mode": "2"}

    assert menu._wizard_parameters_advanced(wf, STATUS)

    adv = wf["advanced_options"]
    assert adv["multipage_mode"] == "skip"
    assert adv["thumbnail_suffix"] == "_mine"
    assert adv["thumbnail_mode"] == "include"  # not remembered: the encoder's


# ---------------------------------------------------------------------------
# Step 6A answered "yes", every question answered with Enter
# ---------------------------------------------------------------------------

def test_advanced_encoder_questions_offer_the_encoder_settings(menu, monkeypatch, edited_encoder):
    prompts = []
    monkeypatch.setattr("builtins.input",
                        lambda p="": prompts.append(p) or ("y" if not prompts[:-1] else ""))
    monkeypatch.setattr(menu, "_wizard_parameters_expert", lambda wf: True)
    wf = {"origin_format": "tiff", "dest_format": "jxl",
          "conversion_type": "jxl_tiff_encoder", "overwrite_mode": "2"}

    assert menu._wizard_parameters_advanced(wf, STATUS)

    adv = wf["advanced_options"]
    assert adv["encode_tag"] == "software"
    assert adv["embed_thumbnail"] is True
    assert adv["multipage_mode"] == "split_all"
    assert adv["thumbnail_suffix"] == "_thumb"
    shown = "\n".join(prompts)
    assert "[software]" in shown and "[split_all]" in shown and "[_thumb]" in shown


def test_advanced_decoder_questions_offer_the_decoder_settings(menu, monkeypatch, edited_decoder):
    prompts = []
    monkeypatch.setattr("builtins.input",
                        lambda p="": prompts.append(p) or ("y" if not prompts[:-1] else ""))
    monkeypatch.setattr(menu, "_wizard_parameters_expert", lambda wf: True)
    wf = {"origin_format": "jxl", "dest_format": "tiff",
          "conversion_type": "jxl_tiff_decoder", "overwrite_mode": "2"}

    assert menu._wizard_parameters_advanced(wf, STATUS)

    adv = wf["advanced_options"]
    assert adv["thumbnail_handling"] == "ignore"
    assert adv["thumbnail_suffix"] == "_t"
    assert adv["no_reconstruct_multipage"] is True
    assert adv["depth_policy"] == "force16"


def test_a_typo_falls_back_to_the_decoder_setting(menu, monkeypatch, edited_decoder):
    answers = iter(["y", "n", "", "", "thumbs", "", "", "sixteen"])
    monkeypatch.setattr("builtins.input", lambda p="": next(answers, ""))
    monkeypatch.setattr(menu, "_wizard_parameters_expert", lambda wf: True)
    wf = {"origin_format": "jxl", "dest_format": "tiff",
          "conversion_type": "jxl_tiff_decoder", "overwrite_mode": "2"}

    assert menu._wizard_parameters_advanced(wf, STATUS)

    adv = wf["advanced_options"]
    assert adv["thumbnail_handling"] == "ignore"
    assert adv["depth_policy"] == "force16"


# ---------------------------------------------------------------------------
# The wizard's starting values and Step 6
# ---------------------------------------------------------------------------

def test_the_wizard_starts_from_the_children(menu, monkeypatch, edited_encoder, edited_decoder):
    seen = {}
    monkeypatch.setattr(menu, "_wizard_select_origin",
                        lambda wf, st: seen.update(wf) or False)

    assert menu.run_wizard(STATUS) is None

    assert seen["use_ram"] is False
    assert seen["compression"] == "lzw"
    assert seen["add_preview"] is False


def test_step6_tiff_output_offers_the_decoder_settings(wp, menu, monkeypatch, edited_decoder):
    seen = {}
    monkeypatch.setattr(menu, "_wizard_select_origin",
                        lambda wf, st: seen.update(wf) or False)
    menu.run_wizard(STATUS)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda p="": prompts.append(p) or "")
    wf = dict(seen, origin_format="jxl", dest_format="tiff",
              conversion_type="jxl_tiff_decoder", mode=1, staging="")

    menu._wizard_parameters_basic(wf, STATUS)

    assert wf["bit_depth"] == 8
    assert wf["compression"] == "lzw"
    assert wf["add_preview"] is False
    shown = "\n".join(prompts)
    assert "Bit depth (8/16) [8]" in shown


def test_step6_recompressor_distance_is_the_recompressor_setting(menu, monkeypatch):
    monkeypatch.setattr(rec, "CJXL_DISTANCE", 2.5)
    seen = {}
    monkeypatch.setattr(menu, "_wizard_select_origin",
                        lambda wf, st: seen.update(wf) or False)
    menu.run_wizard(STATUS)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda p="": prompts.append(p) or "")
    wf = dict(seen, origin_format="jxl", dest_format="jxl",
              conversion_type="jxl_recompress", mode=1, staging="")

    menu._wizard_parameters_basic(wf, STATUS)

    assert wf["distance"] == 2.5
    assert any("[2.5]" in p for p in prompts if "distance" in p.lower())


def test_png_bit_depth_is_the_transcoders_setting(menu, monkeypatch):
    monkeypatch.setattr(tra, "PNG_DEFAULT_BIT_DEPTH", 8)
    seen = {}
    monkeypatch.setattr(menu, "_wizard_select_origin",
                        lambda wf, st: seen.update(wf) or False)
    menu.run_wizard(STATUS)
    monkeypatch.setattr("builtins.input", lambda p="": "")
    wf = dict(seen, origin_format="jxl", dest_format="png",
              conversion_type="jxl_to_png", mode=1, staging="")

    menu._wizard_parameters_basic(wf, STATUS)

    assert wf["bit_depth"] == 8


def test_provenance_question_offers_the_child_setting(menu, monkeypatch, edited_decoder):
    # delete already-converted? n — match by: <enter>
    monkeypatch.setattr("builtins.input", mock.Mock(side_effect=["n", ""]))
    wf = {"origin_format": "jxl", "dest_format": "tiff"}

    menu._ask_delete_options(wf, collapses=True, scope_label="Mode 5 drops folder structure")

    assert wf["provenance"] == "content"


def test_multipage_summary_reads_the_encoder(wp, menu, monkeypatch):
    monkeypatch.setattr(enc, "MULTIPAGE_TIFF_MODE", "ignore")
    label, warn = menu._multipage_summary({"advanced_options": {}})
    assert label.startswith("ignore") and warn is True


# ---------------------------------------------------------------------------
# Presets and "repeat last" without stored answers: the child command line
# ---------------------------------------------------------------------------

def test_preset_tiff_run_passes_the_encoder_settings(wp, menu, launched, tmp_path,
                                                     edited_encoder):
    src = tmp_path / "photos"
    src.mkdir()
    session = _session(wp, last_input_dir=str(src), last_distance=0.1,
                       last_origin_format="tiff", last_dest_format="jxl",
                       last_conversion_type="jxl_tiff_encoder")

    assert menu._run_saved_session(session, STATUS,
                                   answers={"overwrite": False, "dry_run": False})

    cmd = launched[-1]
    assert _flag(cmd, "--multipage-mode") == "split_all"
    assert _flag(cmd, "--thumbnail-mode") == "include"
    assert _flag(cmd, "--thumbnail-suffix") == "_thumb"
    assert _flag(cmd, "--d50-patch") == "off"
    assert _flag(cmd, "--encode-tag") == "software"
    assert "--no-ram" in cmd and "--ram" not in cmd


def test_preset_tiff_output_passes_the_decoder_settings(wp, menu, launched, tmp_path,
                                                        edited_decoder):
    src = tmp_path / "jxls"
    src.mkdir()
    session = _session(wp, last_input_dir=str(src),
                       last_origin_format="jxl", last_dest_format="tiff",
                       last_conversion_type="jxl_tiff_decoder")

    assert menu._run_saved_session(session, STATUS,
                                   answers={"overwrite": False, "dry_run": False})

    cmd = launched[-1]
    assert _flag(cmd, "--compression") == "lzw"
    assert _flag(cmd, "--depth") == "8"
    assert "--no-preview" in cmd
    assert _flag(cmd, "--thumbnail-handling") == "ignore"
    assert _flag(cmd, "--thumbnail-suffix") == "_t"
    assert _flag(cmd, "--depth-policy") == "force16"
    assert "--no-reconstruct-multipage" in cmd


# ---------------------------------------------------------------------------
# The wrapper's own export marker
# ---------------------------------------------------------------------------

def test_an_unset_row_marker_is_the_wrappers_setting(wp, monkeypatch):
    monkeypatch.setattr(wp.ToolConfig, "export_marker", "_OUT")
    src = str(Path("F:/shoot/_OUT/TIFF16"))
    assert wp.InteractiveMenu._entry_marker_dir(src, None) == str(Path("F:/shoot/_OUT"))


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def test_the_fallbacks_are_the_shipped_settings(wp):
    """A child that cannot be imported falls back to these values: they must
    be the scripts' own shipped settings."""
    children = {"jxl_tiff_encoder": enc, "jxl_tiff_decoder": dec,
                "jxl_jpeg_transcoder": tra, "jxl_recompressor": rec}
    for module, options in wp._CHILD_OPTION_DEFAULTS.items():
        for option, (name, fallback) in options.items():
            assert getattr(children[module], name) == fallback, (module, option)


_LITERAL_FALLBACK = re.compile(
    r"last_(multipage_mode|thumbnail_mode|thumbnail_suffix|thumbnail_handling|"
    r"depth_policy|compression|bit_depth|d50_patch|encode_tag|provenance)"
    r"""['")]*\)?\s+or\s+['"0-9]""")
_LITERAL_GET = re.compile(
    r"""\.get\('(compression|bit_depth|add_preview|d50_patch|encode_tag)',\s*['"0-9TF]""")


def test_no_literal_default_for_a_child_option():
    src = (SCRIPTS / "jxl_photo.py").read_text(encoding="utf-8")
    hits = [f"{i}: {line.strip()}" for i, line in enumerate(src.splitlines(), 1)
            if _LITERAL_FALLBACK.search(line) or _LITERAL_GET.search(line)]
    assert hits == []
