"""Delivery C, part 3: the job flow by task and its review screen.

The safety net: for every task, in modes 1 and 6, the command line of a job
started with [r] on the review (every default taken) is the one the OLD
wizard (Steps 1-7, Enter everywhere, YES) produced — captured in
tests/fixtures/review_default_argv.json from the code before the old wizard
was removed (regenerate only from such a commit, with
JXLPHOTO_REGEN_REVIEW_GOLDEN=1). Then: [d] runs dry and comes back, [r] with
a delete still charges the HHMM token, [s] saves a v2 preset, [b] goes back
to the mode step, the rows reach the command line, the menu's new numbers.

The folder has Japanese characters, [ ], a space and % (AGENTS.md).
"""

import builtins
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
GOLDEN = REPO / "tests" / "fixtures" / "review_default_argv.json"
HOSTILE = "250118_吾妻山 [FINAL] 5% (2)"

# task -> (old Step 1 key, old Step 2 key, the new flow's quality answers)
TASK_KEYS = {
    "archive_tiff": ("2", "2", {"job.quality": "2"}),
    "restore_tiff": ("3", "5", {}),
    "derive_jxl": ("3", "6", {"job.distance": None}),
    "export_jpeg": ("3", "3", {"job.jpeg_quality": None}),
    "export_png": ("3", "4", {}),
    "archive_jpeg": ("1", "1", {}),
    "recover_jpeg": ("3", "2", {}),
    "reencode_jpeg": ("1", "2", {"job.distance": None}),
}
CASES = [(t, m) for t in TASK_KEYS for m in ("1", "6")]


def _lib(tmp_path):
    root = tmp_path / "lib" / HOSTILE
    (root / "_EXPORT" / "TIFF16").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def menu(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


@pytest.fixture
def launched(monkeypatch):
    calls = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600: (calls.append(list(map(str, cmd))), 0)[1])
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_in_place_replace", lambda self: True)
    return calls


def _norm(cmd, root):
    out = []
    for c in cmd:
        c = c.replace(str(root), "<ROOT>").replace(str(wp.SCRIPT_DIR), "<SCRIPTS>")
        out.append("<PY>" if c == sys.executable else c)
    return out


def _mode_step_input(monkeypatch, mode, extra=None):
    """The mode step (and Step 5) still read input(): answer by prompt."""
    answers = dict(extra or {})

    def fake(prompt=""):
        p = prompt.strip()
        if p.startswith("Mode (") or "Select mode" in p:
            return mode
        for key, value in answers.items():
            if key in p:
                return value
        return ""
    monkeypatch.setattr(builtins, "input", fake)


def _old_wizard_argv(menu, launched, monkeypatch, tmp_path, task, mode):
    okey, dkey, _q = TASK_KEYS[task]
    root = _lib(tmp_path)
    selects = [okey, dkey]

    def fake(prompt=""):
        p = prompt.strip()
        if p.startswith("Select ("):
            return selects.pop(0)
        if p.startswith("Directory"):
            return str(root)
        if "Exclude folders" in p:
            return "-"
        if p.startswith("Mode ("):
            return mode
        if "Configure advanced options" in p or "Add custom" in p:
            return "n"
        if p == ">":
            return "YES"
        return ""
    monkeypatch.setattr(builtins, "input", fake)
    wf = menu.run_wizard(STATUS)
    assert wf, f"{task} mode {mode}: the old wizard cancelled"
    del launched[:]
    assert menu.execute_workflow(wf, STATUS)
    return _norm(launched[-1], root)


def _new_job_argv(menu, launched, monkeypatch, tmp_path, task, mode, review=("r",),
                  extra_answers=None):
    _okey, _dkey, quality = TASK_KEYS[task]
    root = _lib(tmp_path)
    tid_key = str([t[0] for t in wp.TASKS].index(task) + 1)
    answers = {"job.task": tid_key, "job.folder": str(root), "job.exclude": "-",
               "review.action": list(review), **quality, **(extra_answers or {})}
    menu._ui = wp.ScriptedUI(answers)
    _mode_step_input(monkeypatch, mode)
    del launched[:]
    menu.new_job_flow(STATUS)
    return [_norm(c, root) for c in launched]


@pytest.mark.skipif(os.environ.get("JXLPHOTO_REGEN_REVIEW_GOLDEN") != "1",
                    reason="regenerates the fixture from the OLD wizard")
def test_regenerate_the_golden_from_the_old_wizard(menu, launched, monkeypatch, tmp_path):
    golden = {}
    for task, mode in CASES:
        sub = tmp_path / f"{task}_{mode}"
        golden[f"{task}/{mode}"] = _old_wizard_argv(menu, launched, monkeypatch, sub, task, mode)
    GOLDEN.write_text(json.dumps(golden, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


@pytest.mark.parametrize("task,mode", CASES)
def test_a_job_run_with_its_defaults_is_the_old_wizards_run(menu, launched, monkeypatch,
                                                           tmp_path, task, mode):
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    runs = _new_job_argv(menu, launched, monkeypatch, tmp_path, task, mode)
    assert runs, "nothing ran"
    assert runs[-1] == golden[f"{task}/{mode}"]


def test_d_runs_dry_and_comes_back(menu, launched, monkeypatch, tmp_path):
    runs = _new_job_argv(menu, launched, monkeypatch, tmp_path, "archive_tiff", "1",
                         review=("d", "q"))
    assert len(runs) == 1 and "--dry-run" in runs[0]
    assert menu._ui.answers["review.action"] == []          # asked again after the dry run


def test_b_goes_back_to_the_mode_step(menu, launched, monkeypatch, tmp_path):
    seen = []
    real = wp.InteractiveMenu._wizard_select_mode
    monkeypatch.setattr(wp.InteractiveMenu, "_wizard_select_mode",
                        lambda self, wf: (seen.append(1), real(self, wf))[1])
    runs = _new_job_argv(menu, launched, monkeypatch, tmp_path, "export_png", "1",
                         review=("b", "r"))
    assert len(seen) == 2 and len(runs) == 1


def test_rows_reach_the_command_line(menu, launched, monkeypatch, tmp_path):
    # rows of an archive job: 1 RAM, 2 workers, 3 distance, 4 effort, 5 staging, ...
    stg = tmp_path / "stg [x] 5%"
    runs = _new_job_argv(
        menu, launched, monkeypatch, tmp_path, "archive_tiff", "1",
        review=("2", "3", "4", "5", "r"),
        extra_answers={"review.workers": "3", "review.distance": "0.05",
                       "review.effort": "9", "review.staging": str(stg)})
    cmd = runs[-1]
    assert cmd[cmd.index("--workers") + 1] == "3"
    assert cmd[cmd.index("--distance") + 1] == "0.05"
    assert cmd[cmd.index("--effort") + 1] == "9"
    assert any("stg [x] 5%" in c for c in cmd)


def test_the_guard_row_turns_the_guard_off(menu, launched, monkeypatch, tmp_path):
    menu._ui = None
    wf = _review_workflow(menu, tmp_path, "export_png")
    rows = menu._review_rows(wf, STATUS)
    n = str([r[0] for r in rows].index("overwrite_guard") + 1)
    menu._ui = wp.ScriptedUI({"review.action": [n, "r"], "review.overwrite_guard": False})
    assert menu._review_screen(wf, STATUS) == "ran"
    assert "--no-overwrite-guard" in launched[-1]
    assert wf["_pending_session_fields"]["last_overwrite_guard"] is False \
        if "_pending_session_fields" in wf else menu.config.config.last_overwrite_guard is False


def _review_workflow(menu, tmp_path, task, mode=1, **over):
    _tid, _label, origin, dest, convs = wp._TASK_BY_ID[task]
    wf = menu._new_workflow()
    wf.update(origin_format=origin, dest_format=dest, conversion_type=convs[0], _task=task,
              input_dir=str(_lib(tmp_path)), mode=mode, mode_config={})
    wf.update(over)
    menu._review_init(wf, STATUS)
    return wf


def test_r_with_a_delete_charges_the_hhmm_token(menu, launched, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode",
                        lambda self: (calls.append(1), True)[1])
    wf = _review_workflow(menu, tmp_path, "archive_tiff", delete_source=True)
    wf["distance"] = 0.1
    menu._advanced_defaults(wf)
    menu._ui = wp.ScriptedUI({"review.action": ["r"]})
    assert menu._review_screen(wf, STATUS) == "ran"
    assert calls == [1] and "--delete-source" in launched[-1]


def test_r_without_a_delete_asks_no_token(menu, launched, monkeypatch, tmp_path):
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode",
                        lambda self: pytest.fail("token asked for a run that deletes nothing"))
    wf = _review_workflow(menu, tmp_path, "archive_tiff", distance=0.1)
    menu._ui = wp.ScriptedUI({"review.action": ["r"]})
    assert menu._review_screen(wf, STATUS) == "ran"


def test_s_saves_a_v2_preset_and_the_last_job(menu, launched, tmp_path):
    wf = _review_workflow(menu, tmp_path, "derive_jxl", distance=1.0)
    menu._ui = wp.ScriptedUI({"review.action": ["s", "q"], "review.preset_name": "WEB [x] 5%"})
    assert menu._review_screen(wf, STATUS) == "quit"
    saved = json.loads((tmp_path / ".jxl_tools_config.json").read_text(encoding="utf-8"))
    p = saved["presets"]["WEB [x] 5%"]
    assert p["schema"] == 2 and p["job"]["task"] == "derive_jxl"
    assert saved["last_input_dir"] == wf["input_dir"]
    assert launched == []                                   # saving runs nothing


def test_c_shows_the_command_without_running(menu, launched, tmp_path):
    wf = _review_workflow(menu, tmp_path, "restore_tiff")
    menu._ui = wp.ScriptedUI({"review.action": ["c", "q"]})
    menu._review_screen(wf, STATUS)
    assert launched == []
    assert any("jxl_tiff_decoder.py" in line for line in menu._ui.output)


def test_x_refuses_an_expert_flag_the_script_does_not_know(menu, launched, tmp_path):
    wf = _review_workflow(menu, tmp_path, "restore_tiff")
    menu._ui = wp.ScriptedUI({"review.action": ["x", "q"], "review.expert": "--no-such-flag"})
    menu._review_screen(wf, STATUS)
    assert not wf.get("expert_flags")
    assert any("Not changed" in line for line in menu._ui.output)


def test_the_conversion_row_switches_export_jpeg_to_auto(menu, launched, tmp_path):
    wf = _review_workflow(menu, tmp_path, "export_jpeg")
    rows = menu._review_rows(wf, STATUS)
    n = str([r[0] for r in rows].index("conversion") + 1)
    menu._ui = wp.ScriptedUI({"review.action": [n, "r"], "review.conversion": "2"})
    assert menu._review_screen(wf, STATUS) == "ran"
    assert "--from-jxl" in launched[-1] and "--force-convert" not in launched[-1]


def test_a_derivative_is_not_offered_in_place(menu, tmp_path):
    wf = _review_workflow(menu, tmp_path, "derive_jxl", mode=8, distance=1.0)
    rows = {r[0]: r for r in menu._review_rows(wf, STATUS)}
    assert rows["derivative"][3] is None and "output_icc" not in rows


def test_a_task_the_pc_cannot_run_is_refused(menu, tmp_path):
    status = dict(STATUS, tifffile=False)
    menu._ui = wp.ScriptedUI({"job.task": ["1", "3"]})       # archive TIFFs, then derive
    assert menu._job_pick_task(status) == "derive_jxl"
    assert any("needs tifffile" in line for line in menu._ui.output)


@pytest.mark.parametrize("choice,flow", [("1", "new"), ("4", "manifest"), ("5", "restamp"),
                                         ("6", "thumbs"), ("7", "jbrd"), ("8", "icc"),
                                         ("R", "restamp"), ("C", "icc")])
def test_main_menu_numbers(tmp_path, monkeypatch, choice, flow):
    path = tmp_path / ".jxl_tools_config.json"
    path.write_text(json.dumps({"dependencies_checked": True, "presets": {},
                                "show_thumbnail_tip": False}), encoding="utf-8")
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path", lambda self: path)
    monkeypatch.setattr(wp.DependencyChecker, "check_dependencies",
                        lambda self, force=False, persist=True, **kw: dict(STATUS))
    picks = iter([choice, "0"])
    monkeypatch.setattr(wp.InteractiveMenu, "show_main_menu", lambda self, has_last: next(picks))
    seen = []
    monkeypatch.setattr(wp.InteractiveMenu, "new_job_flow",
                        lambda self, status, manifest=False:
                        seen.append("manifest" if manifest else "new"))
    for name, tag in (("restamp_flow", "restamp"), ("explorer_thumbnail_flow", "thumbs"),
                      ("repair_jbrd_flow", "jbrd"), ("clear_icc_cache_flow", "icc")):
        monkeypatch.setattr(wp.InteractiveMenu, name, lambda self, status, tag=tag: seen.append(tag))
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(sys, "argv", ["jxl_photo.py"])
    try:
        wp.main()
    except SystemExit:
        pass
    assert seen == [flow]
