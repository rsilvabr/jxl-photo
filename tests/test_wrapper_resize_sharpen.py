#!/usr/bin/env python3
"""Wrapper: the resize/sharpening derivative answers of Step 6.

They reach the child commands only for the transcoder's decode direction and
the recompressor, are cleared by a "none" answer, and are refused next to
--delete-source / in-place modes (a derivative never deletes and has no
in-place form).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_photo as wp

REPO = Path(__file__).resolve().parent.parent

_SEEDED = {"resize_mode": "long", "resize_value": 2048, "allow_upscale": True,
           "sharpen": "screen"}


def _menu():
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


def _capture_direct_cmd(monkeypatch, workflow):
    menu = _menu()
    cmds = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600: (cmds.append(cmd), 0)[1])
    assert menu.execute_workflow(workflow, {"cjxl": True})
    assert cmds, "no child command was built"
    return cmds[0]


def _jxl_to_jxl_workflow(tmp_path, mode=7, advanced=None, **overrides):
    workflow = {
        "origin_format": "jxl", "dest_format": "jxl", "mode": mode,
        "input_dir": str(tmp_path), "workers": 2, "effort": 7,
        "distance": 1.0, "staging": None, "dry_run": False,
        "mode_config": {}, "advanced_options": dict(advanced or {}),
        "conversion_type": "jxl_recompress",
    }
    workflow.update(overrides)
    return workflow


def _jxl_to_png_workflow(tmp_path, advanced=None, conv="jxl_to_png"):
    return {
        "origin_format": "jxl", "dest_format": "png", "mode": 1,
        "input_dir": str(tmp_path), "workers": 2, "effort": 7, "quality": 95,
        "staging": None, "dry_run": False, "bit_depth": 16,
        "mode_config": {}, "advanced_options": dict(advanced or {}),
        "conversion_type": conv, "icc_profile": None,
    }


# ── command builders ────────────────────────────────────────────────────────

def test_direct_recompressor_emits_the_shaping_flags(tmp_path, monkeypatch):
    cmd = _capture_direct_cmd(monkeypatch, _jxl_to_jxl_workflow(
        tmp_path, advanced=dict(_SEEDED)))
    assert cmd[cmd.index("--resize-long") + 1] == "2048"
    assert "--allow-upscale" in cmd
    assert cmd[cmd.index("--sharpen") + 1] == "screen"


def test_manifest_recompressor_emits_the_shaping_flags(tmp_path):
    menu = _menu()
    wf = _jxl_to_jxl_workflow(tmp_path, advanced=dict(_SEEDED))
    cmd = menu._build_manifest_entry_cmd(
        "script.py", str(tmp_path), str(tmp_path), 7,
        "jxl", "jxl", 2, wf, wf["advanced_options"])
    assert cmd[cmd.index("--resize-long") + 1] == "2048"
    assert cmd[cmd.index("--sharpen") + 1] == "screen"


def test_direct_decode_emits_the_shaping_flags(tmp_path, monkeypatch):
    cmd = _capture_direct_cmd(monkeypatch, _jxl_to_png_workflow(
        tmp_path, advanced=dict(_SEEDED)))
    assert cmd[cmd.index("--resize-long") + 1] == "2048"
    assert cmd[cmd.index("--sharpen") + 1] == "screen"


def test_manifest_decode_emits_the_shaping_flags(tmp_path):
    menu = _menu()
    wf = _jxl_to_png_workflow(tmp_path, advanced=dict(_SEEDED))
    cmd = menu._build_manifest_entry_cmd(
        "script.py", str(tmp_path), str(tmp_path), 1,
        "jxl", "png", 2, wf, wf["advanced_options"])
    assert cmd[cmd.index("--resize-long") + 1] == "2048"


@pytest.mark.parametrize("origin,dest,conv", [
    ("jxl", "jpeg", "jxl_to_jpeg_lossless"),   # byte-exact recovery has no pixels
    ("jpeg", "jxl", "transcode_lossless"),     # encode direction
    ("jpeg", "jxl", "convert_lossy"),          # encode direction
])
def test_encode_and_lossless_directions_never_emit_the_shaping_flags(
        tmp_path, monkeypatch, origin, dest, conv):
    wf = _jxl_to_png_workflow(tmp_path, advanced=dict(_SEEDED), conv=conv)
    wf["origin_format"] = origin
    wf["dest_format"] = dest
    cmd = _capture_direct_cmd(monkeypatch, wf)
    assert "--resize-long" not in cmd
    assert "--sharpen" not in cmd


# ── Step 6 answers ──────────────────────────────────────────────────────────

def test_ask_output_shaping_none_clears_every_key(monkeypatch):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    wf = {"advanced_options": dict(_SEEDED)}
    wp._ask_output_shaping(wf)
    assert not any(k in wf["advanced_options"] for k in _SEEDED)


def test_ask_output_shaping_plain_answers(monkeypatch):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    answers = iter(["long", "2048", "y", "print"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    wf = {"advanced_options": {}}
    wp._ask_output_shaping(wf)
    adv = wf["advanced_options"]
    assert adv["resize_mode"] == "long"
    assert adv["resize_value"] == 2048
    assert adv["allow_upscale"] is True
    assert adv["sharpen"] == "print"


def test_ask_output_shaping_invalid_value_disables_resize(monkeypatch):
    """A typo must not become a 1 px output: the plain prompt drops the resize
    (and still keeps the sharpening answer)."""
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    answers = iter(["long", "not-a-number", "print"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    wf = {"advanced_options": {}}
    wp._ask_output_shaping(wf)
    adv = wf["advanced_options"]
    assert "resize_mode" not in adv
    assert "resize_value" not in adv
    assert adv["sharpen"] == "print"


def test_drop_derivative_options_clears_the_shaping_keys():
    adv = dict(_SEEDED)
    adv["output_icc"] = "sRGB"
    wp._drop_derivative_options(adv)
    assert adv == {}


def test_append_derivative_flags_modes():
    for mode, flag in (("long", "--resize-long"), ("short", "--resize-short"),
                       ("percent", "--resize-percent")):
        cmd = []
        wp._append_derivative_flags(
            cmd, {"resize_mode": mode, "resize_value": 50, "sharpen": "none"})
        assert cmd == [flag, "50"]


# ── guards ──────────────────────────────────────────────────────────────────

def test_recompressor_derivative_with_delete_is_refused(tmp_path, monkeypatch):
    menu = _menu()
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda *a, **k: pytest.fail("the child must not run"))
    wf = _jxl_to_jxl_workflow(tmp_path, advanced=dict(_SEEDED, delete_source=True))
    assert menu.execute_workflow(wf, {"cjxl": True}) is False


def test_decode_derivative_with_delete_is_refused(tmp_path, monkeypatch):
    menu = _menu()
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda *a, **k: pytest.fail("the child must not run"))
    wf = _jxl_to_png_workflow(tmp_path, advanced=dict(_SEEDED, delete_source=True))
    assert menu.execute_workflow(wf, {"cjxl": True}) is False


@pytest.mark.parametrize("mode", [0, 8])
def test_recompressor_derivative_in_place_is_refused(tmp_path, monkeypatch, mode):
    menu = _menu()
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda *a, **k: pytest.fail("the child must not run"))
    wf = _jxl_to_jxl_workflow(tmp_path, mode=mode, advanced=dict(_SEEDED))
    assert menu.execute_workflow(wf, {"cjxl": True}) is False


def test_manifest_refuses_shaping_in_place_rows_up_front(tmp_path, monkeypatch):
    menu = _menu()
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda *a, **k: pytest.fail("the child must not run"))
    src = tmp_path / "src"
    src.mkdir()
    wf = _jxl_to_jxl_workflow(tmp_path, mode=99, advanced=dict(_SEEDED))
    wf["manifest_entries"] = [(str(src), str(tmp_path / "out"), 2),
                              (str(src), str(src), 8)]
    assert menu.execute_workflow(wf, {"cjxl": True}) is False


def test_step6_resize_answers_survive_advanced(monkeypatch, tmp_path):
    """The advanced step rebuilds advanced_options from scratch; the shaping
    answers must ride along like output_icc does."""
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    wf = _jxl_to_jxl_workflow(tmp_path, advanced=dict(_SEEDED))
    _menu()._wizard_parameters_advanced(wf, {"cjxl": True})
    assert wf["advanced_options"].get("resize_mode") == "long"
    assert wf["advanced_options"].get("sharpen") == "screen"
