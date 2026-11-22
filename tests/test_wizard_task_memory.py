"""A new job starts from the last run of ITS task, and the last job saved is the
run itself (#704-#709, Fable's full sweep of 2026-11-22, scripts wrap\\repro_*.py).

Before: a new job took the last values of ANY task (an 8-bit PNG export made
"Restore TIFFs" ask for 8-bit TIFFs; copies made in sRGB_JXL sent the next
masters there), the [D] answers of an earlier job rode into the replay of a
later one (--provenance adopt, --no-overwrite-guard: refused by every script
next to a delete, after the HHMM), and a script setting changed in menu S never
reached a job once one had run. The folder is hostile (Japanese, [ ], a space).
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import jxl_photo as wp  # noqa: E402
import jxl_tiff_encoder as enc  # noqa: E402

STATUS = {k: True for k in
          ("cjxl", "djxl", "exiftool", "magick", "tifffile", "pillow", "imagecodecs")}
HOSTILE = "250118_吾妻山公園 [FINAL] x"


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "lib" / HOSTILE
    (r / "_EXPORT" / "TIFF16").mkdir(parents=True)
    return r


@pytest.fixture
def menu(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode", lambda self: True)
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_in_place_replace", lambda self: True)
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600: (seen.append(list(map(str, cmd))), 0)[1])
    return seen


def _job(menu, answers):
    ui = wp.ScriptedUI(answers)
    menu._ui = ui
    menu.new_job_flow(STATUS)
    return ui


def _row(menu, answers, label):
    """The number of the review row called `label` for this job (shown, then q)."""
    ui = _job(menu, dict(answers, **{"review.action": ["q"]}))
    return next(ln.split()[0] for ln in ui.output
                if label in ln and ln.strip()[:2].strip().isdigit())


def _value(cmd, flag):
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


def test_704_a_png_export_does_not_set_the_depth_of_restored_tiffs(menu, calls, root):
    png = {"job.task": "5", "job.folder": str(root), "mode.pick": "3"}
    n = _row(menu, png, "PNG bit depth")
    _job(menu, dict(png, **{"review.action": [n, "r"], "review.png_depth": "8"}))
    assert _value(calls[-1], "--bit-depth") == "8"
    _job(menu, {"job.task": "2", "job.folder": str(root), "job.exclude": "-",
                "mode.pick": "3", "review.action": ["r"]})
    depth = _value(calls[-1], "--depth")
    assert depth in (None, str(wp._child_default("jxl_tiff_decoder", "bit_depth"))), calls[-1]
    # The PNG task still remembers its own answer.
    _job(menu, dict(png, **{"review.action": ["r"]}))
    assert _value(calls[-1], "--bit-depth") == "8"


def test_705_the_copies_folder_does_not_become_the_masters_folder(menu, calls, root):
    copies = {"job.task": "3", "job.folder": str(root), "mode.pick": "6",
              "mode.cfg.marker": None, "job.quality": None}
    _job(menu, dict(copies, **{"mode.cfg.jxl_folder": "sRGB_JXL", "review.action": ["r"]}))
    assert "sRGB_JXL" in calls[-1]
    _job(menu, {"job.task": "1", "job.folder": str(root), "job.exclude": "-", "mode.pick": "6",
                "mode.cfg.marker": None, "mode.cfg.jxl_folder": None, "job.quality": "3",
                "review.action": ["r"]})
    assert "sRGB_JXL" not in calls[-1], calls[-1]
    # Enter on the next copies job: the copies task's own folder.
    _job(menu, dict(copies, **{"mode.cfg.jxl_folder": None, "review.action": ["r"]}))
    assert "sRGB_JXL" in calls[-1]


def _norm(cmd, root):
    return [c.replace(str(root), "<ROOT>") for c in cmd[2:]]


def test_706_a_dry_run_delete_job_does_not_ride_into_the_next_job(menu, calls, root):
    _job(menu, {"job.task": "1", "job.folder": str(root), "job.exclude": "-",
                "mode.pick": "D", "delete.continue": True, "delete.layout": "5",
                "delete.sure": True, "delete.verify": [None], "delete.skipped": True,
                "delete.provenance": "adopt", "delete.adopt_scan": False,
                "job.quality": "3", "review.action": ["d", "q"]})
    assert "--no-adopt-scan" in calls[-1]
    _job(menu, {"job.task": "1", "job.folder": str(root), "job.exclude": "-",
                "mode.pick": "3", "job.quality": "3", "review.action": ["s", "r"],
                "review.preset_name": "SYNC", "review.preset_overwrite": True})
    direct = _norm(calls[-1], root)
    menu._ui = wp.ScriptedUI({"session.folder": None, "session.overwrite": "2",
                              "session.dry_run": False, "session.proceed": True})
    menu._run_saved_session(menu.config.session_snapshot(), STATUS)
    assert _norm(calls[-1], root) == direct
    menu._run_saved_session(wp._preset_session(menu.config.config.presets["SYNC"]), STATUS,
                            answers={"overwrite": False, "dry_run": False})
    assert _norm(calls[-1], root) == direct


def test_706_the_run_after_a_dry_run_keeps_its_delete_answers(menu, calls, root):
    """[d] then [r] in one review: both saves are the same job (the staged
    answers are read, not popped)."""
    _job(menu, {"job.task": "1", "job.folder": str(root), "job.exclude": "-",
                "mode.pick": "D", "delete.continue": True, "delete.layout": "5",
                "delete.sure": True, "delete.verify": [None], "delete.skipped": True,
                "delete.provenance": "content", "job.quality": "3",
                "review.action": ["d", "r"]})
    c = menu.config.config
    assert c.last_delete_skipped is True and c.last_provenance == "content"


def test_707_repeat_of_a_delete_job_keeps_the_guard_on(menu, calls, root):
    base = {"job.task": "1", "job.folder": str(root), "job.exclude": "-",
            "mode.pick": "3", "job.quality": "3"}
    n = _row(menu, base, "Overwrite guard")
    _job(menu, dict(base, **{"review.action": [n, "r"], "review.overwrite_guard": False}))
    assert "--no-overwrite-guard" in calls[-1]
    _job(menu, {"job.task": "1", "job.folder": str(root), "job.exclude": "-",
                "mode.pick": "D", "delete.continue": True, "delete.layout": "3",
                "delete.sure": True, "delete.skipped": False, "job.quality": "3",
                "review.action": ["r"]})
    direct = _norm(calls[-1], root)
    assert "--delete-source" in direct and "--no-overwrite-guard" not in direct
    menu._ui = wp.ScriptedUI({"session.folder": None, "session.overwrite": "2",
                              "session.dry_run": False, "session.proceed": True})
    menu._run_saved_session(menu.config.session_snapshot(), STATUS)
    assert _norm(calls[-1], root) == direct
    # A session saved before #707 (an earlier job's "off" next to a delete):
    # the replay keeps the guard on rather than send a pair every script refuses.
    old = dict(menu.config.session_snapshot(), last_overwrite_guard=False)
    menu._ui = wp.ScriptedUI({"session.folder": None, "session.overwrite": "2",
                              "session.dry_run": False, "session.proceed": True})
    menu._run_saved_session(old, STATUS)
    assert "--no-overwrite-guard" not in calls[-1]
    # The next sync job of the task still offers the guard answer it had.
    ui = _job(menu, dict(base, **{"review.action": ["q"]}))
    guard = next(ln for ln in ui.output if "Overwrite guard" in ln
                 and ln.strip()[:2].strip().isdigit())
    assert "off" in guard.lower(), guard


def test_709_a_script_setting_reaches_the_next_job(menu, calls, root, monkeypatch):
    base = {"job.task": "1", "job.folder": str(root), "job.exclude": "-",
            "mode.pick": "3", "job.quality": "3"}
    _job(menu, dict(base, **{"review.action": ["r"]}))
    assert _value(calls[-1], "--multipage-mode") in (None, enc.MULTIPAGE_TIFF_MODE)
    # Menu S (the overlay) changes the encoder's setting after that job.
    other = "split_all" if enc.MULTIPAGE_TIFF_MODE != "split_all" else "split"
    monkeypatch.setattr(enc, "MULTIPAGE_TIFF_MODE", other)
    _job(menu, dict(base, **{"review.action": ["r"]}))
    assert _value(calls[-1], "--multipage-mode") in (None, other), calls[-1]


def test_709_an_answer_of_the_task_stays_its_own(menu, calls, root):
    """A value the user chose that differs from the script's is remembered."""
    base = {"job.task": "1", "job.folder": str(root), "job.exclude": "-",
            "mode.pick": "3", "job.quality": "3"}
    modes = ("ignore", "skip", "split", "split_all")     # picked by position
    chosen = "ignore" if enc.MULTIPAGE_TIFF_MODE != "ignore" else "skip"
    ui = _job(menu, dict(base, **{"review.action": ["a", "q"], "opt.screen": [""]}))
    row = next(ln.split()[0] for ln in ui.output
               if "--multipage-mode" in ln and ln.strip()[:2].strip().isdigit())
    _job(menu, dict(base, **{"review.action": ["a", "r"], "opt.screen": [row, ""],
                             "opt.multipage_mode": str(modes.index(chosen) + 1)}))
    assert _value(calls[-1], "--multipage-mode") == chosen
    _job(menu, dict(base, **{"review.action": ["r"]}))
    assert _value(calls[-1], "--multipage-mode") == chosen


def test_704_a_config_from_before_keeps_its_last_job_for_that_task(menu):
    """A config saved before task_memory: its last job's values for a new job
    of THAT task, as before; another task starts from the Settings. The
    workers (a property of the PC, like the staging folder) carry across."""
    c = menu.config.config
    c.last_origin_format, c.last_dest_format = "tiff", "jxl"
    c.last_conversion_type = "jxl_tiff_encoder"
    c.last_workers, c.last_effort = 9, 9
    c.default_workers, c.default_effort = 3, 5
    same = menu._new_workflow("archive_tiff")
    assert (same["workers"], same["effort"]) == (9, 9)
    other = menu._new_workflow("derive_jxl")
    assert other["workers"] == 9 and other["effort"] == 5


def test_s6_a_default_changed_in_settings_reaches_a_task_with_its_own_memory(menu, calls, root,
                                                                            monkeypatch):
    """Review S6: menu 9 hands a changed default to the next job; a task that
    remembers its own value (#704) kept the old one (effort 7 after 9 was set)."""
    from unittest import mock
    _job(menu, {"job.task": "1", "job.folder": str(root), "job.exclude": "-",
                "mode.pick": "3", "job.quality": "3", "review.action": ["r"]})
    c = menu.config.config
    assert c.task_memory["archive_tiff"].get("last_effort") == c.last_effort
    new_effort = 9 if c.last_effort != 9 else 8
    # staging, workers, quality, effort, distance, confirm-delete, marker,
    # jxl-thumb tip (Windows), update check
    answers = ["", "", "", str(new_effort), "", "", "", "", ""]
    menu._ui = wp.PlainUI()
    with mock.patch.object(wp, "RICH_AVAILABLE", False), \
            mock.patch("builtins.input", side_effect=answers):
        menu.edit_settings()
    assert menu._new_workflow("archive_tiff")["effort"] == new_effort
