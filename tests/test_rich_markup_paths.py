"""User text shown through rich keeps its brackets, and end-of-input at a
prompt cancels instead of a traceback.

rich reads a bracketed word starting with a lowercase letter as a style tag
and drops it: the Step 7 summary and the 'Executing:' line showed
'E:\\吾妻山 wizard [test] 100%\\src' as 'E:\\吾妻山 wizard  100%\\src'.
(Found by driving the wizard in a real console, 2026-10-22.)
"""
import io
import json
import sys

import pytest
from rich.console import Console

import jxl_photo

FOLDER_NAME = "吾妻山 wizard [test] 100%"


@pytest.fixture
def screen(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(jxl_photo, "console",
                        Console(file=out, width=400, color_system=None, highlight=False))
    monkeypatch.setattr(jxl_photo, "RICH_AVAILABLE", True)
    return out


@pytest.fixture
def menu(tmp_path, monkeypatch):
    path = tmp_path / ".jxl_tools_config.json"
    monkeypatch.setattr(jxl_photo.ConfigManager, "_get_config_path", lambda self: path)
    config = jxl_photo.ConfigManager()
    return jxl_photo.InteractiveMenu(config, jxl_photo.DependencyChecker(config))


def _tiff_workflow(src):
    return {
        "origin_format": "tiff", "dest_format": "jxl", "conversion_type": "jxl_tiff_encoder",
        "mode": 1, "input_dir": str(src), "workers": 2, "staging": None,
        "distance": 0.1, "effort": 7, "use_ram": True, "advanced_options": {"sync": True},
        "mode_config": {}, "dry_run": False,
    }


def test_summary_keeps_the_brackets(tmp_path, screen, menu, monkeypatch):
    src = tmp_path / FOLDER_NAME / "src"
    src.mkdir(parents=True)
    monkeypatch.setattr(jxl_photo.Prompt, "ask", lambda *a, **k: "no")
    menu._wizard_confirm(_tiff_workflow(src))
    assert "[test]" in screen.getvalue()


def test_executing_line_keeps_the_brackets(tmp_path, screen, menu, monkeypatch):
    src = tmp_path / FOLDER_NAME / "src"
    src.mkdir(parents=True)
    monkeypatch.setattr(jxl_photo.InteractiveMenu, "_stream_child", lambda self, cmd: 0)
    menu.execute_workflow(_tiff_workflow(src), {"cjxl": True, "djxl": True})
    shown = screen.getvalue()
    assert "Executing:" in shown
    line = shown.split("Executing:", 1)[1]
    assert "[test]" in line, line[:400]


def test_error_and_success_messages_keep_the_brackets(screen, menu):
    menu._print_error(r"Invalid directory: E:\fotos [final]")
    menu._print_success(r"Manifest saved to E:\fotos [final]\m.csv")
    assert screen.getvalue().count("[final]") == 2


def test_end_of_input_at_a_prompt_cancels(tmp_path, monkeypatch, capsys):
    path = tmp_path / ".jxl_tools_config.json"
    path.write_text(json.dumps({"dependencies_checked": True}), encoding="utf-8")
    monkeypatch.setattr(jxl_photo.ConfigManager, "_get_config_path", lambda self: path)
    monkeypatch.setattr(jxl_photo.DependencyChecker, "check_dependencies",
                        lambda self, force=False, persist=True, **kw:
                        {"cjxl": True, "djxl": True})

    def closed(self, has_last):
        raise EOFError
    monkeypatch.setattr(jxl_photo.InteractiveMenu, "show_main_menu", closed)
    monkeypatch.setattr(jxl_photo, "RICH_AVAILABLE", False)
    monkeypatch.setattr(sys, "argv", ["jxl_photo.py"])
    with pytest.raises(SystemExit) as exit_info:
        jxl_photo.main()
    assert exit_info.value.code == 130
    assert "Cancelled" in capsys.readouterr().out
