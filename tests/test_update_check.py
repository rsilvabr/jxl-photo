"""The menu's "a new release is out" line (the owner's request, 2026-11-04):
notify only, GitHub asked at most once a day, only by the interactive menu
(never --run-preset or --list-presets), betas ignored, off with
check_for_updates = false or JXLPHOTO_NO_UPDATE_CHECK=1. No test here
touches the network: _latest_release_tag is replaced in every one.

Pre-fix proof (fails on the beta, which has no update check):
    $env:JXLPHOTO_SCRIPTS_UNDER_TEST = "<tmp with git show f7cbcba:...>"
    python -m pytest tests/test_update_check.py -q
"""

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)
sys.path.insert(0, str(SCRIPTS))
import jxl_photo as wp  # noqa: E402

STATUS = {k: True for k in
          ("cjxl", "djxl", "exiftool", "magick", "tifffile", "pillow", "imagecodecs")}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / "ホーム [x] 5%" / ".jxl_tools_config.json")
    (tmp_path / "ホーム [x] 5%").mkdir()
    monkeypatch.delenv("JXLPHOTO_NO_UPDATE_CHECK", raising=False)
    return wp.ConfigManager()


class _Asked(list):
    """The repositories GitHub was asked about, and the tag it answers."""
    answer = "v99.0.0"


@pytest.fixture
def asked(monkeypatch):
    calls = _Asked()

    def fake(repo, timeout=3):
        calls.append(repo)
        return calls.answer
    monkeypatch.setattr(wp, "_latest_release_tag", fake)
    return calls


@pytest.mark.parametrize("text,key", [
    ("v2.22.0", (2, 22, 0, 1)), ("2.22.0_beta1", (2, 22, 0, 0)),
    ("v2.22.0-exe1", (2, 22, 0, 1)), ("v2.22.0-exe3", (2, 22, 0, 1)),
    ("nonsense", None), (None, None), ("v2.22", None)])
def test_version_key(text, key):
    assert wp._version_key(text) == key


@pytest.mark.parametrize("mine,latest,news", [
    ("2.22.0", "v2.22.1", True), ("2.22.0", "v2.23.0", True), ("2.22.0", "v3.0.0", True),
    ("2.22.0", "v2.22.0", False), ("2.22.0", "v2.21.0", False),
    ("2.22.0_beta1", "v2.22.0", True),          # the release a beta was for
    ("2.22.0_beta1", "v2.21.0", False),
    ("2.22.0", "v2.22.0-exe2", False),          # a new package of the same release
    ("2.22.0", "garbage", False)])
def test_what_counts_as_news(cfg, asked, monkeypatch, mine, latest, news):
    monkeypatch.setattr(wp, "TOOLKIT_VERSION", mine)
    asked.answer = latest if wp._version_key(latest) else None
    cfg.config.update_latest_seen = None
    notice = wp._update_notice(cfg, today="2026-11-04")
    assert bool(notice) is news
    if news:
        assert latest in notice[0] and notice[1].endswith("/releases/latest")


def test_github_is_asked_at_most_once_a_day(cfg, asked, monkeypatch):
    monkeypatch.setattr(wp, "TOOLKIT_VERSION", "2.22.0")
    assert wp._update_notice(cfg, today="2026-11-04")
    assert wp._update_notice(cfg, today="2026-11-04")       # the kept answer
    assert asked == ["jxl-photo"]
    saved = json.loads(cfg.config_path.read_text(encoding="utf-8"))
    assert saved["update_checked_on"] == "2026-11-04"
    assert saved["update_latest_seen"] == "v99.0.0"
    wp._update_notice(cfg, today="2026-11-05")
    assert len(asked) == 2


def test_no_network_says_nothing(cfg, asked, monkeypatch):
    monkeypatch.setattr(wp, "TOOLKIT_VERSION", "2.22.0")
    asked.answer = None
    assert wp._update_notice(cfg, today="2026-11-04") is None
    assert cfg.config.update_checked_on == "2026-11-04"     # not asked again today


@pytest.mark.parametrize("how", ["config", "env"])
def test_it_can_be_turned_off(cfg, asked, monkeypatch, how):
    if how == "config":
        cfg.config.check_for_updates = False
    else:
        monkeypatch.setenv("JXLPHOTO_NO_UPDATE_CHECK", "1")
    assert wp._update_notice(cfg, today="2026-11-04") is None
    assert asked == []


def test_the_windows_program_follows_its_own_releases(cfg, asked, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(wp, "TOOLKIT_VERSION", "2.22.0")
    asked.answer = "v2.23.0-exe1"
    notice = wp._update_notice(cfg, today="2026-11-04")
    assert asked == ["jxl-photo-exe"]
    assert "jxl-photo-exe/releases/latest" in notice[1]


def _main(tmp_path, monkeypatch, argv, picks=("0",)):
    path = tmp_path / ".jxl_tools_config.json"
    path.write_text(json.dumps({"dependencies_checked": True, "presets": {},
                                "show_thumbnail_tip": False}), encoding="utf-8")
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path", lambda self: path)
    monkeypatch.setattr(wp.DependencyChecker, "check_dependencies",
                        lambda self, force=False, persist=True, **kw: dict(STATUS))
    it = iter(picks)
    menus = []
    monkeypatch.setattr(wp.InteractiveMenu, "show_main_menu",
                        lambda self, has_last: (menus.append(getattr(self, "_update_available", None)),
                                                next(it))[1])
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(sys, "argv", ["jxl_photo.py"] + argv)
    monkeypatch.delenv("JXLPHOTO_NO_UPDATE_CHECK", raising=False)
    try:
        wp.main()
    except SystemExit:
        pass
    return menus


def test_the_menu_shows_it_and_u_opens_the_page(tmp_path, monkeypatch, asked, capsys):
    monkeypatch.setattr(wp, "TOOLKIT_VERSION", "2.22.0")
    opened = []
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    menus = _main(tmp_path, monkeypatch, [], picks=("U", "0"))
    assert menus[0] and menus[0][0] == "v99.0.0"
    assert "A new release is out: v99.0.0" in capsys.readouterr().out
    assert opened == ["https://github.com/rsilvabr/jxl-photo/releases/latest"]


@pytest.mark.parametrize("argv", [["--list-presets"], ["--run-preset", "nothing"]])
def test_unattended_and_listing_never_ask(tmp_path, monkeypatch, asked, argv):
    _main(tmp_path, monkeypatch, argv)
    assert asked == []


def test_the_menu_offers_u_only_with_news(tmp_path, monkeypatch, asked):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    cfg_path = tmp_path / "c.json"
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path", lambda self: cfg_path)
    c = wp.ConfigManager()
    menu = wp.InteractiveMenu(c, wp.DependencyChecker(c))
    import builtins
    answers = iter(["u", "0"])
    monkeypatch.setattr(builtins, "input", lambda *a: next(answers))
    assert menu.show_main_menu(False) == "0"          # no news: U is not an option
    menu._update_available = ("v99.0.0", "https://example")
    answers = iter(["u"])
    assert menu.show_main_menu(False) == "U"
