"""The wizard's one-line tip when Windows Explorer has no JXL thumbnail handler.

Read-only (one registry lookup), Windows only, and never in a --run-preset /
--list-presets run. JXLPHOTO_THUMBNAIL_TIP replaces the text (a packaged build
names its own install command) and an empty value turns it off.
"""
import json
import sys
import types

import pytest

import jxl_photo

HANDLER = r".jxl\ShellEx\{e357fccd-a995-4576-b01f-234630154e96}"


def _fake_winreg(values):
    """A winreg whose HKEY_CLASSES_ROOT holds only `values` {subkey: default}."""
    class _Key:
        def __init__(self, sub):
            self.sub = sub

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(root, sub):
        if sub not in values:
            raise FileNotFoundError(sub)
        return _Key(sub)

    def QueryValue(key, name):
        return values[key.sub]

    return types.SimpleNamespace(HKEY_CLASSES_ROOT=object(), OpenKey=OpenKey,
                                 QueryValue=QueryValue)


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(jxl_photo.os, "name", "nt")
    monkeypatch.delenv("JXLPHOTO_THUMBNAIL_TIP", raising=False)

    def use(values):
        monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(values))
    return use


def test_no_handler_shows_the_tip(windows):
    windows({})
    tip = jxl_photo._jxl_thumbnail_tip()
    assert tip and jxl_photo.JXL_THUMB_URL in tip


@pytest.mark.parametrize("sub", [
    HANDLER,
    r"SystemFileAssociations\.jxl\ShellEx\{e357fccd-a995-4576-b01f-234630154e96}",
])
def test_any_handler_means_no_tip(windows, sub):
    # jxl-thumb, or the Microsoft JPEG XL extension's WIC handler.
    windows({sub: "{C7657C4A-9F68-40fa-A4DF-96BC08EB3551}"})
    assert jxl_photo._jxl_thumbnail_tip() is None


def test_an_empty_key_is_no_handler(windows):
    # The SystemFileAssociations key exists with an empty value on a stock
    # Windows 11: it registers nothing.
    windows({r"SystemFileAssociations\.jxl\ShellEx\{e357fccd-a995-4576-b01f-234630154e96}": ""})
    assert jxl_photo._jxl_thumbnail_tip() is not None


def test_env_replaces_or_silences_the_tip(windows, monkeypatch):
    windows({})
    monkeypatch.setenv("JXLPHOTO_THUMBNAIL_TIP", "Run x.exe thumbnails install")
    assert jxl_photo._jxl_thumbnail_tip() == "Run x.exe thumbnails install"
    monkeypatch.setenv("JXLPHOTO_THUMBNAIL_TIP", "")
    assert jxl_photo._jxl_thumbnail_tip() is None


def test_not_windows_no_tip(monkeypatch):
    monkeypatch.setattr(jxl_photo.os, "name", "posix")
    assert jxl_photo._jxl_thumbnail_tip() is None


def test_a_registry_error_never_blocks(windows, monkeypatch):
    def boom(*a):
        raise RuntimeError("registry unavailable")
    windows({})
    monkeypatch.setattr(sys.modules["winreg"], "OpenKey", boom)
    assert jxl_photo._jxl_thumbnail_tip() is None


def _run_main(tmp_path, monkeypatch, argv, **config):
    """main() as the CLI runs it, the menu answering 0 (exit) at once."""
    path = tmp_path / ".jxl_tools_config.json"
    path.write_text(json.dumps({"dependencies_checked": True, "presets": {}, **config}),
                    encoding="utf-8")
    monkeypatch.setattr(jxl_photo.ConfigManager, "_get_config_path", lambda self: path)
    monkeypatch.setattr(jxl_photo.DependencyChecker, "check_dependencies",
                        lambda self, force=False, persist=True, **kw:
                        {"cjxl": True, "djxl": True})
    monkeypatch.setattr(jxl_photo.InteractiveMenu, "show_main_menu",
                        lambda self, has_last: "0")
    monkeypatch.setattr(jxl_photo, "RICH_AVAILABLE", False)
    monkeypatch.setattr(jxl_photo, "_jxl_thumbnail_tip", lambda: "THUMBTIP-MARKER")
    monkeypatch.setattr(sys, "argv", ["jxl_photo.py"] + argv)
    try:
        jxl_photo.main()
    except SystemExit:
        pass


def test_tip_shows_in_the_menu(tmp_path, monkeypatch, capsys):
    _run_main(tmp_path, monkeypatch, [])
    assert "THUMBTIP-MARKER" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["--list-presets"], ["--run-preset", "missing"]])
def test_no_tip_in_unattended_runs(tmp_path, monkeypatch, capsys, argv):
    # A scheduled run's log is no place for advice about Explorer.
    _run_main(tmp_path, monkeypatch, argv)
    assert "THUMBTIP-MARKER" not in capsys.readouterr().out


def test_hidden_in_the_settings_means_no_tip(tmp_path, monkeypatch, capsys):
    _run_main(tmp_path, monkeypatch, [], show_thumbnail_tip=False)
    assert "THUMBTIP-MARKER" not in capsys.readouterr().out


def _settings_menu(tmp_path, monkeypatch, tip):
    path = tmp_path / ".jxl_tools_config.json"
    monkeypatch.setattr(jxl_photo.ConfigManager, "_get_config_path", lambda self: path)
    monkeypatch.setattr(jxl_photo, "_jxl_thumbnail_tip", lambda: tip)
    monkeypatch.setattr(jxl_photo, "RICH_AVAILABLE", False)
    config = jxl_photo.ConfigManager()
    return config, jxl_photo.InteractiveMenu(config, jxl_photo.DependencyChecker(config))


def test_settings_screen_turns_the_tip_off_and_on(tmp_path, monkeypatch):
    config, menu = _settings_menu(tmp_path, monkeypatch, "a tip")
    # staging, workers, quality, effort, distance, confirm, marker, tip
    answers = iter(["", "", "", "", "", "", "", "n"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    menu.edit_settings()
    assert jxl_photo.ConfigManager().config.show_thumbnail_tip is False
    # Hidden: still asked, even though the menu no longer shows it.
    config, menu = _settings_menu(tmp_path, monkeypatch, "a tip")
    answers = iter(["", "", "", "", "", "", "", "y"])
    menu.edit_settings()
    assert jxl_photo.ConfigManager().config.show_thumbnail_tip is True


def test_settings_screen_does_not_ask_with_a_handler(tmp_path, monkeypatch):
    config, menu = _settings_menu(tmp_path, monkeypatch, None)
    answers = iter(["", "", "", "", "", "", ""])   # no 8th prompt
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    menu.edit_settings()
    assert config.config.show_thumbnail_tip is True
