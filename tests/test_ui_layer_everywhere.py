"""Every question of the wrapper goes through the UI layer (v2.22.1).

v2.22.0 moved the new job flow and its review screen onto UI/ScriptedUI, but
the mode step ([A]/[M]/[D]), Settings, the presets menu, the manifest
questions, the repair/thumbnail flows and the main menu still called input()
and Prompt/Confirm.ask directly, one copy per renderer. Here: no such call
is left outside the UI classes, and the migrated flows run end to end from a
ScriptedUI with input() turned into an error (so nothing can sneak past it).

Folders carry Japanese characters, [ ], a space and % (AGENTS.md).
"""

import ast
import builtins
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)
sys.path.insert(0, str(SCRIPTS))
import jxl_photo as wp  # noqa: E402

HOSTILE = "250118_吾妻山 [FINAL] 5% (2)"
UI_CLASSES = {"UI", "PlainUI", "RichUI", "ScriptedUI"}
RAW_PROMPTS = {"input", "Prompt.ask", "Confirm.ask", "IntPrompt.ask"}


def test_no_prompt_outside_the_ui_layer():
    src = (SCRIPTS / "jxl_photo.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    allowed = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in UI_CLASSES:
            allowed.update(id(n) for n in ast.walk(node))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and id(node) not in allowed:
            name = ast.get_source_segment(src, node.func)
            if name in RAW_PROMPTS:
                found.append(f"{name} at line {node.lineno}")
    assert not found, "questions asked outside the UI layer: " + ", ".join(found)


@pytest.fixture
def menu(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)

    def no_input(*_a, **_k):
        raise AssertionError("input() called outside the UI layer")
    monkeypatch.setattr(builtins, "input", no_input)
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


@pytest.fixture
def launched(monkeypatch):
    calls = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, *a, **k: (calls.append(list(map(str, cmd))), 0)[1])
    return calls


def _folder(tmp_path):
    folder = tmp_path / HOSTILE
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def test_main_menu(menu):
    menu._ui = wp.ScriptedUI({"menu.main": ["x", "d"]})
    assert menu.show_main_menu(False) == "D"
    assert menu._ui.asked == ["menu.main", "menu.main"]          # x was refused
    assert any("Invalid choice" in line for line in menu._ui.output)


def test_settings(menu):
    menu._ui = wp.ScriptedUI({
        "settings.staging": "none", "settings.workers": "7", "settings.quality": "abc",
        "settings.effort": "9", "settings.distance": "0.05",
        "settings.confirm_delete": False, "settings.marker": "_EXPORT2",
        "settings.thumbnail_tip": None})
    quality = menu.config.config.default_quality
    menu.edit_settings()
    cfg = menu.config.config
    assert (cfg.default_workers, cfg.default_effort, cfg.default_distance) == (7, 9, 0.05)
    assert cfg.default_quality == quality                  # not a number: kept
    assert cfg.confirm_delete is False and cfg.export_marker == "_EXPORT2"
    assert cfg.staging_dir is None


def test_presets_save_and_delete(menu, tmp_path):
    folder = _folder(tmp_path)
    cfg = menu.config.config
    cfg.last_input_dir, cfg.last_origin, cfg.last_dest, cfg.last_mode = (
        str(folder), "tiff", "jxl", 1)
    menu._ui = wp.ScriptedUI({"presets.menu": ["s", "s", "d", "b"],
                              "presets.name": ["吾妻山 [x] 5%", "吾妻山 [x] 5%"],
                              "presets.overwrite": [True],
                              "presets.delete": ["1"], "presets.delete_sure": [True]})
    menu.presets_menu({})
    assert menu._ui.asked == ["presets.menu", "presets.name", "presets.menu", "presets.name",
                              "presets.overwrite", "presets.menu", "presets.delete",
                              "presets.delete_sure", "presets.menu"]
    assert not (cfg.presets or {})


def test_a_preset_runs_through_the_session_questions(menu, launched, tmp_path):
    folder = _folder(tmp_path)
    (folder / "_EXPORT" / "TIFF16").mkdir(parents=True)
    cfg = menu.config.config
    cfg.last_input_dir, cfg.last_origin, cfg.last_dest, cfg.last_mode = (
        str(folder), "tiff", "jxl", 1)
    menu.config.save_preset("吾妻山", menu.config.session_snapshot())
    menu._ui = wp.ScriptedUI({"presets.menu": ["1"], "session.folder": None,
                              "session.overwrite": "1", "session.dry_run": True,
                              "session.proceed": True})
    status = {k: True for k in ("cjxl", "djxl", "exiftool", "magick", "tifffile",
                                "pillow", "imagecodecs")}
    menu.presets_menu(status)
    assert launched, "nothing ran"
    cmd = launched[-1]
    assert str(folder) in cmd and "--dry-run" in cmd and "--overwrite" in cmd


def test_explorer_thumbnails(menu, launched, tmp_path):
    folder = _folder(tmp_path)
    menu._ui = wp.ScriptedUI({"thumbs.folder": f'"{folder}"', "thumbs.recursive": False,
                              "thumbs.policy": "3", "thumbs.dry": True})
    menu.explorer_thumbnail_flow({"djxl": True, "exiftool": True, "magick": True})
    cmd = launched[0]
    assert cmd[2] == str(folder) and cmd[cmd.index("--mode") + 1] == "0"
    assert cmd[cmd.index("--explorer-thumbnail") + 1] == "off" and "--dry-run" in cmd


def test_repair_jbrd(menu, launched, tmp_path):
    folder = _folder(tmp_path)
    menu._ui = wp.ScriptedUI({"jbrd.folder": str(folder), "jbrd.audit": False})
    menu.repair_jbrd_flow({"djxl": True, "exiftool": True})
    assert launched[0][2] == str(folder) and "--dry-run" not in launched[0]


def test_mode_details_view(menu):
    # "x" is no mode: asked again; the panels print with [FINAL]-safe plain text.
    menu._ui = wp.ScriptedUI({"mode.details": ["3"]})
    wf = {"origin_format": "tiff", "dest_format": "jxl", "input_dir": "."}
    assert menu._show_mode_details_and_select(wf) is True
    assert wf["mode"] == 3 and menu._ui.asked == ["mode.details"]
    assert any("Mode Detailed Explanations" in line for line in menu._ui.output)


def test_mode_step_settings(menu, tmp_path):
    folder = _folder(tmp_path)
    menu._ui = wp.ScriptedUI({"mode.cfg.output_dir": str(folder / "out [1]")})
    wf = {"origin_format": "tiff", "dest_format": "jxl", "input_dir": str(folder),
          "mode": 2, "mode_config": {}}
    assert menu._wizard_mode_specific_config(wf) is True
    assert menu._ui.asked == ["mode.cfg.output_dir"]
    assert wf["mode_config"]["output_dir"] == str(folder / "out [1]")


def test_rich_renderer_draws_the_main_menu(tmp_path, monkeypatch):
    rich = pytest.importorskip("rich")
    from rich.console import Console
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    rec = Console(record=True, width=200, file=open(os.devnull, "w", encoding="utf-8"))
    monkeypatch.setattr(wp, "RICH_AVAILABLE", True)
    monkeypatch.setattr(wp, "console", rec)
    monkeypatch.setattr(wp.Prompt, "ask", staticmethod(lambda *a, **k: "0"))
    cfg = wp.ConfigManager()
    m = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    assert isinstance(m.ui, wp.RichUI)
    assert m.show_main_menu(False) == "0"
    assert "Main Menu" in rec.export_text()
