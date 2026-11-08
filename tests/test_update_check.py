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
    # No answer does not count as asked (owner's request, 2026-11-08): the
    # next run of the same day asks again.
    assert cfg.config.update_checked_on is None
    assert not cfg.config_path.exists()                     # nothing written
    asked.answer = "v99.0.0"
    assert wp._update_notice(cfg, today="2026-11-04")
    assert asked == ["jxl-photo", "jxl-photo"]


def test_no_answer_in_the_background_leaves_the_day_unasked(cfg, asked, monkeypatch):
    monkeypatch.setattr(wp, "TOOLKIT_VERSION", "2.24.0")
    cfg.config.update_checked_on = "2026-11-06"
    cfg.config.update_latest_seen = "v2.25.0"
    asked.answer = None
    poll = wp._update_check_start(cfg)
    assert _wait_for(lambda: asked == ["jxl-photo"])
    notice = poll(5.0)
    assert "earlier run on 2026-11-06" in notice[0]          # the kept answer, dated
    assert wp.ConfigManager().config.update_checked_on in (None, "2026-11-06")


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


def test_a_slow_network_never_holds_the_menu(cfg, monkeypatch):
    """v2.23.0: GitHub is asked in the background; the menu waits half a
    second at most, and the answer shows up at a later poll."""
    import threading
    import time
    monkeypatch.setattr(wp, "TOOLKIT_VERSION", "2.22.0")
    release = threading.Event()

    def slow(repo, timeout=3):
        release.wait(10)
        return "v99.0.0"
    monkeypatch.setattr(wp, "_latest_release_tag", slow)
    poll = wp._update_check_start(cfg)
    t0 = time.monotonic()
    assert poll(0.5) is None                     # not in yet: no notice, no wait
    assert time.monotonic() - t0 < 2
    assert cfg.config.update_checked_on is None  # nothing written by the thread
    release.set()
    deadline = time.monotonic() + 10
    notice = None
    while notice is None and time.monotonic() < deadline:
        notice = poll(0.2)
    assert notice and "v99.0.0" in notice[0]
    assert wp.ConfigManager().config.update_latest_seen == "v99.0.0"


def test_already_asked_today_starts_no_thread(cfg, asked, monkeypatch):
    from datetime import date
    monkeypatch.setattr(wp, "TOOLKIT_VERSION", "2.22.0")
    cfg.config.update_checked_on = date.today().isoformat()
    cfg.config.update_latest_seen = "v99.0.0"
    assert wp._update_check_start(cfg)(0)[0].startswith("A new release is out")
    assert asked == []


# ---------------------------------------------------------------------------
# jxl-thumb: installed, and up to date? (owner's request, 2026-11-06)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="Windows file versions")
def test_dll_file_version_reads_a_real_dll():
    v = wp._dll_file_version(Path(os.environ["SystemRoot"]) / "System32" / "kernel32.dll")
    assert v and wp._version_key(v), v
    assert wp._dll_file_version(Path(__file__)) is None        # no VERSIONINFO


def test_the_suite_never_sees_an_installed_jxl_thumb():
    assert os.environ.get("JXLPHOTO_JXL_THUMB") == "0"
    assert wp._jxl_thumb_dll_version() is None


@pytest.mark.parametrize("installed,latest,news", [
    ("0.2.1", "v0.2.2", True), ("0.2.1", "v0.3.0", True),
    ("0.2.1", "v0.2.1", False), ("0.2.2", "v0.2.1", False),
    (None, "v0.2.2", False),                      # not installed: nothing to update
    ("0.2.1", None, False)])                      # never asked
def test_thumb_update_notice(cfg, monkeypatch, installed, latest, news):
    monkeypatch.setattr(wp, "_jxl_thumb_dll_version", lambda: installed)
    cfg.config.thumb_latest_seen = latest
    notice = wp._thumb_update_notice(cfg)
    assert bool(notice) is news, notice
    if news:
        assert f"jxl-thumb {installed} is installed; {latest.lstrip('v')} is out" in notice
        assert "[T]" in notice


def test_thumb_update_notice_is_off_with_the_release_check(cfg, monkeypatch):
    monkeypatch.setattr(wp, "_jxl_thumb_dll_version", lambda: "0.2.1")
    cfg.config.thumb_latest_seen = "v0.2.2"
    cfg.config.check_for_updates = False
    assert wp._thumb_update_notice(cfg) is None
    cfg.config.check_for_updates = True
    monkeypatch.setenv("JXLPHOTO_NO_UPDATE_CHECK", "1")
    assert wp._thumb_update_notice(cfg) is None


def test_the_daily_ask_includes_jxl_thumb_only_when_installed(cfg, asked, monkeypatch):
    monkeypatch.setattr(wp, "_jxl_thumb_dll_version", lambda: None)
    wp._update_notice(cfg, today="2026-11-06")
    assert "jxl-thumb" not in asked
    monkeypatch.setattr(wp, "_jxl_thumb_dll_version", lambda: "0.2.1")
    asked.clear()
    wp._update_notice(cfg, today="2026-11-07")
    assert asked == [wp._update_repo(), "jxl-thumb"]
    assert cfg.config.thumb_latest_seen == "v99.0.0"
    saved = json.loads(Path(cfg.config_path).read_text(encoding="utf-8"))
    assert saved["thumb_latest_seen"] == "v99.0.0"
    asked.clear()
    wp._update_notice(cfg, today="2026-11-07")                 # once a day
    assert asked == []


def test_the_background_ask_keeps_the_jxl_thumb_answer(cfg, asked, monkeypatch):
    monkeypatch.setattr(wp, "_jxl_thumb_dll_version", lambda: "0.2.1")
    poll = wp._update_check_start(cfg)
    poll(5.0)
    assert "jxl-thumb" in asked
    assert wp.ConfigManager().config.thumb_latest_seen == "v99.0.0"


# ---------------------------------------------------------------------------
# The answer survives a run that never draws the menu again (owner's request,
# 2026-11-08: "não é um programa que a pessoa volte pro menu... abre de novo").
# Pre-fix proof: git show HEAD:jxl_photo.py into a tmp folder, then
#   $env:JXLPHOTO_SCRIPTS_UNDER_TEST = "<tmp>"
#   python -m pytest tests/test_update_check.py -q -k "earlier_run or keeps_its or d_in_the or only_its"
# ---------------------------------------------------------------------------

def _wait_for(pred, seconds=10):
    import time
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_the_thread_keeps_its_answer_without_a_poll(cfg, asked, monkeypatch):
    """The run goes straight into a job (no poll after the first half
    second) and is closed: the answer must already be on disk."""
    monkeypatch.setattr(wp, "TOOLKIT_VERSION", "2.22.0")
    cfg.config.presets = {"MOBILE": {"last_input_dir": "G:/x"}}
    cfg.save_config()
    wp._update_check_start(cfg)                      # never polled
    assert _wait_for(lambda: wp.ConfigManager().config.update_latest_seen == "v99.0.0")
    again = wp.ConfigManager().config
    assert again.presets == {"MOBILE": {"last_input_dir": "G:/x"}}   # nothing else lost


def test_the_answer_rewrites_only_its_keys(cfg):
    """save_update_answer edits the file on disk, not a dump of the memory:
    a key the menu thread wrote and this copy never saw survives."""
    cfg.save_config()
    data = json.loads(cfg.config_path.read_text(encoding="utf-8"))
    data["last_input_dir"] = "G:/2025/250118_吾妻山公園 [FINAL]"
    cfg.config_path.write_text(json.dumps(data), encoding="utf-8")
    cfg.save_update_answer("2026-11-08", "v99.0.0", None)
    saved = json.loads(cfg.config_path.read_text(encoding="utf-8"))
    assert saved["last_input_dir"] == "G:/2025/250118_吾妻山公園 [FINAL]"
    assert saved["update_latest_seen"] == "v99.0.0"
    assert saved["update_checked_on"] == "2026-11-08"


def test_a_corrupt_file_is_not_overwritten_by_the_answer(cfg):
    cfg.config_path.write_text("{not json", encoding="utf-8")
    cfg.save_update_answer("2026-11-08", "v99.0.0", None)
    assert cfg.config_path.read_text(encoding="utf-8") == "{not json"
    assert cfg.config.update_latest_seen == "v99.0.0"


def test_the_next_run_shows_an_earlier_runs_answer_with_its_date(cfg, monkeypatch):
    """A slow network on the next day: the kept answer shows at once."""
    import threading
    monkeypatch.setattr(wp, "TOOLKIT_VERSION", "2.24.0")
    cfg.config.update_checked_on = "2026-11-06"
    cfg.config.update_latest_seen = "v2.25.0"
    release = threading.Event()
    monkeypatch.setattr(wp, "_latest_release_tag",
                        lambda repo, timeout=3: (release.wait(10), "v2.25.0")[1])
    try:
        notice = wp._update_check_start(cfg)(0.2)
        assert notice and "v2.25.0" in notice[0]
        assert "earlier run on 2026-11-06" in notice[0] and "D in the menu" in notice[0]
    finally:
        release.set()


def test_asked_earlier_today_says_so(cfg, asked, monkeypatch):
    from datetime import date
    monkeypatch.setattr(wp, "TOOLKIT_VERSION", "2.24.0")
    cfg.config.update_checked_on = date.today().isoformat()
    cfg.config.update_latest_seen = "v2.25.0"
    notice = wp._update_check_start(cfg)(0)
    assert f"earlier run on {date.today().isoformat()}" in notice[0]
    assert asked == []


def test_a_fresh_answer_carries_no_date(cfg, asked, monkeypatch):
    monkeypatch.setattr(wp, "TOOLKIT_VERSION", "2.22.0")
    poll = wp._update_check_start(cfg)
    assert _wait_for(lambda: poll(0.2) is not None)
    assert "earlier run" not in poll()[0]


@pytest.mark.parametrize("answer,expect,news", [
    ("v99.0.0", "A new release is out: v99.0.0", True),
    ("v2.22.0", "up to date", False),
    (None, "GitHub did not answer", False)])
def test_d_in_the_menu_asks_now(cfg, asked, monkeypatch, answer, expect, news):
    from datetime import date
    monkeypatch.setattr(wp, "TOOLKIT_VERSION", "2.22.0")
    cfg.config.update_checked_on = date.today().isoformat()   # asked today already
    cfg.config.update_latest_seen = "v2.22.0"
    asked.answer = answer
    line, notice = wp._update_check_now(cfg)
    assert asked == ["jxl-photo"]
    assert expect in line and bool(notice) is news
    assert "earlier run" not in line
    if answer:
        assert wp.ConfigManager().config.update_latest_seen == answer


def test_d_respects_the_off_switch(cfg, asked):
    cfg.config.check_for_updates = False
    line, notice = wp._update_check_now(cfg)
    assert notice is None and "off" in line and asked == []


def test_d_in_the_main_loop_offers_u(tmp_path, monkeypatch, asked, capsys):
    monkeypatch.setattr(wp, "TOOLKIT_VERSION", "2.22.0")
    monkeypatch.setattr(wp, "_update_check_start", lambda c: (lambda wait=0.0: None))
    menus = _main(tmp_path, monkeypatch, [], picks=("D", "0"))
    assert menus[0] is None                      # nothing known before D
    assert menus[1] and menus[1][0] == "v99.0.0"
    assert "A new release is out: v99.0.0" in capsys.readouterr().out
