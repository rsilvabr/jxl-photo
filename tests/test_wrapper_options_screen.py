#!/usr/bin/env python3
"""Phase 2 of the wrapper redesign (delivery B): the options the wizard does
not ask about.

* Every child flag the wrapper used to leave in NOT_OFFERED is now an `Opt`
  whose value lives in the run's `extra_options`. The Step 7 summary takes
  `O` and opens the "all options" screen: every entry of the run's
  direction, grouped, with its value; the plain ones change there, written to
  their slot, so the command builder and the saved session see them.
* `requires`/`conflicts` (e.g. --restamp-then-sync never with a delete) are
  checked on the screen and again before a run, before the HHMM token.
* Menu R runs the encoder's --restamp-moved (dry run first), menu C its
  --clear-icc-cache; both are real child runs here, in a hostile folder.

The screens ask through the UI layer: ScriptedUI answers by question id.

Pre-fix proof: JXLPHOTO_SCRIPTS_UNDER_TEST=<the five scripts of the base
commit> — the base wrapper has no ScriptedUI, no extra_options and no R/C.
"""

import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from unittest import mock

import pytest

TESTS = Path(__file__).resolve().parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GOLDEN = _load("golden_argv_cases_b4", TESTS / "test_wrapper_argv_golden.py")
wp = GOLDEN._load_wrapper()
SCRIPTS = GOLDEN.SCRIPTS
STATUS = GOLDEN.STATUS
EVENT = "250118_吾妻山公園_菜の花 [FINAL] 5%"

# direction -> (origin, dest, conversion_type, workflow extras)
DIRS = {
    "tiff2jxl": ("tiff", "jxl", "jxl_tiff_encoder", {"distance": 1.0, "effort": 7}),
    "jxl2tiff": ("jxl", "tiff", "jxl_tiff_decoder", {"compression": "lzw",
                                                     "bit_depth": 16}),
    "jxl2jxl": ("jxl", "jxl", "jxl_recompress", {"distance": 1.0, "effort": 7}),
    "jpeg2jxl": ("jpeg", "jxl", "transcode_lossless", {"effort": 7}),
    "jxl2jpeg": ("jxl", "jpeg", "jxl_to_jpeg_auto", {"quality": 90, "effort": 7}),
    "jxl2png": ("jxl", "png", "jxl_to_png", {"effort": 7, "bit_depth": 16}),
}


class PickUI(wp.ScriptedUI):
    """ScriptedUI that picks rows of the options screen by label: `picks`
    is a list of Opt (pick that row), '/text' (filter) — then Enter."""

    def __init__(self, picks, answers=None):
        super().__init__(answers or {})
        self.picks = list(picks)
        self.screens = []

    def section(self, title):
        super().section(title)
        self.screens.append(len(self.output))

    def _rows(self):
        start = self.screens[-1] if self.screens else 0
        rows = {}
        for line in self.output[start:]:
            m = re.match(r"\s*\[\s*(\d+)\] (.*?)(?: \(!\))?: .*   (--[\w-]+)", line)
            if m:
                rows[(m.group(2), m.group(3))] = m.group(1)
        return rows

    def ask_text(self, qid, prompt, default=""):
        if qid != "opt.screen":
            return super().ask_text(qid, prompt, default)
        self.asked.append(qid)
        if not self.picks:
            return ""
        pick = self.picks.pop(0)
        if isinstance(pick, str):
            return pick
        rows = self._rows()
        assert (pick.label, pick.flag) in rows, (
            f"{pick.id} is not on the screen: {sorted(rows)}")
        return rows[(pick.label, pick.flag)]


def _menu(tmp_path, monkeypatch, ui=None):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    if ui is not None:
        menu._ui = ui
    return menu


def _workflow(tmp_path, direction, mode=6, **over):
    origin, dest, conv, extra = DIRS[direction]
    wf = GOLDEN._subst(GOLDEN._wf(origin, dest, conv, mode, **extra),
                       str(tmp_path / "lib"))
    wf["input_dir"] = str(tmp_path / "lib" / EVENT)
    Path(wf["input_dir"]).mkdir(parents=True, exist_ok=True)
    wf.update(over)
    return wf


def _argv(menu, wf):
    origin, dest = wf["origin_format"], wf["dest_format"]
    script = str(SCRIPTS / GOLDEN._script(origin, dest))
    return [str(a) for a in menu.build_child_cmd(
        script, origin, dest, wf["input_dir"], None, wf["mode"], wf["workers"], wf,
        wf.get("advanced_options") or {}, manifest=False)]


def _has_pair(argv, flag, value):
    return any(a == flag and b == value for a, b in zip(argv, argv[1:]))


def _extras():
    return [o for o in wp.REGISTRY if o.legacy_key.startswith("extra.")]


def _setup_for(opt, direction, tmp_path):
    """A workflow on which `opt` applies, and the answers that set it."""
    mode = 1 if opt.id == "output_name" else 6
    over, adv = {}, {}
    if opt.id == "clean_staging":
        over["staging"] = str(tmp_path / "stg [x] 5%")
    if opt.id.startswith("sharpen_"):
        adv["sharpen"] = "screen"
    if opt.id == "allow_incomplete_groups":
        adv["delete_source"] = True
    wf = _workflow(tmp_path, direction, mode, advanced_options=adv, **over)
    answers, expected = {}, True
    if opt.kind != "flag":
        if opt.choices:
            # The last choice, by the key the prompt offers for it (#606).
            answers[f"opt.{opt.id}"] = wp._choice_keys(opt.choices)[0][-1]
            expected = opt.choices[-1]
        elif opt.kind == "notnone":
            answers[f"opt.{opt.id}"] = "0.8"
            expected = 0.8
        else:
            answers[f"opt.{opt.id}"] = "web [x] 5%"
            expected = "web [x] 5%"
    if opt.danger:
        answers["opt.danger"] = True
    return wf, answers, expected


CASES = [(o.id, d) for o in _extras() for d in sorted(o.directions)]


@pytest.mark.parametrize("opt_id,direction", CASES)
def test_each_extra_option_is_on_its_screen_and_reaches_the_child(
        opt_id, direction, tmp_path, monkeypatch):
    opt = next(o for o in wp.REGISTRY if o.id == opt_id)
    wf, answers, expected = _setup_for(opt, direction, tmp_path)
    ui = PickUI([opt], answers)
    menu = _menu(tmp_path, monkeypatch, ui)
    assert opt.flag not in _argv(menu, wf), "set before the screen changed it"
    menu._all_options_screen(wf)
    assert wf["extra_options"].get(opt.id) == expected, (wf["extra_options"], ui.output)
    argv = _argv(menu, wf)
    if opt.kind == "flag":
        assert opt.flag in argv, argv
    else:
        assert _has_pair(argv, opt.flag, str(expected)), argv
    # The child accepts it (its own parser, the same one as the run's).
    child = wp._expert_flags_problem(wf["origin_format"], wf["dest_format"],
                                     " ".join(argv[3:]).replace("web [x] 5%", "web"))
    assert child is None, child


def test_a_flag_picked_twice_is_unset_again(tmp_path, monkeypatch):
    opt = next(o for o in wp.REGISTRY if o.id == "no_preflight")
    wf = _workflow(tmp_path, "tiff2jxl")
    menu = _menu(tmp_path, monkeypatch, PickUI([opt, opt]))
    menu._all_options_screen(wf)
    assert "no_preflight" not in wf["extra_options"]
    assert "--no-preflight" not in _argv(menu, wf)


def test_zero_is_a_value(tmp_path, monkeypatch):
    """--buffering 0 is the slowest, best-compressing level, not 'unset'."""
    opt = next(o for o in wp.REGISTRY if o.id == "buffering")
    wf = _workflow(tmp_path, "tiff2jxl")
    key = wp._choice_keys(opt.choices)[0][opt.choices.index(0)]   # "0" (#606)
    menu = _menu(tmp_path, monkeypatch, PickUI([opt], {"opt.buffering": key}))
    menu._all_options_screen(wf)
    assert wf["extra_options"]["buffering"] == 0
    assert _has_pair(_argv(menu, wf), "--buffering", "0")
    assert "--buffering 0" in wp._extra_options_summary(wf)


def test_screen_refuses_a_conflicting_option(tmp_path, monkeypatch):
    """--restamp-then-sync with a delete: the child exits 2 (after the HHMM
    token, if the wrapper let it start). The screen does not set it."""
    opt = next(o for o in wp.REGISTRY if o.id == "restamp_then_sync")
    wf = _workflow(tmp_path, "tiff2jxl", advanced_options={"delete_source": True})
    ui = PickUI([opt])
    menu = _menu(tmp_path, monkeypatch, ui)
    menu._all_options_screen(wf)
    assert "restamp_then_sync" not in wf["extra_options"]
    assert any("Not changed" in line and "Delete" in line for line in ui.output), ui.output


def test_a_dangerous_option_asks_first(tmp_path, monkeypatch):
    opt = next(o for o in wp.REGISTRY if o.id == "allow_incomplete_groups")
    wf = _workflow(tmp_path, "jxl2tiff", advanced_options={"delete_source": True})
    ui = PickUI([opt], {"opt.danger": False})
    menu = _menu(tmp_path, monkeypatch, ui)
    menu._all_options_screen(wf)
    assert "allow_incomplete_groups" not in wf["extra_options"]
    assert "opt.danger" in ui.asked


def test_wizard_entries_are_shown_but_not_changed_here(tmp_path, monkeypatch):
    opt = next(o for o in wp.REGISTRY if o.id == "overwrite")
    wf = _workflow(tmp_path, "tiff2jxl", advanced_options={"overwrite": False})
    ui = PickUI([opt])
    menu = _menu(tmp_path, monkeypatch, ui)
    menu._all_options_screen(wf)
    assert wf["advanced_options"] == {"overwrite": False}
    assert any("set in the wizard" in line for line in ui.output)
    assert any("is set in the wizard's own steps" in line for line in ui.output)


def test_filter_narrows_the_screen(tmp_path, monkeypatch):
    wf = _workflow(tmp_path, "tiff2jxl")
    ui = PickUI(["/buffering"])
    menu = _menu(tmp_path, monkeypatch, ui)
    menu._all_options_screen(wf)
    last = ui.output[ui.screens[-1] - 1:]
    rows = [line for line in last if re.match(r"\s*\[\s*\d+\]", line)]
    assert rows and all("buffering" in r.lower() for r in rows), rows


def test_screen_shows_only_the_runs_direction(tmp_path, monkeypatch):
    """The decoder's screen has no encoder-only flag."""
    wf = _workflow(tmp_path, "jxl2tiff")
    ui = PickUI([])
    menu = _menu(tmp_path, monkeypatch, ui)
    menu._all_options_screen(wf)
    text = "\n".join(ui.output)
    assert "--allow-incomplete-groups" in text
    assert "--restamp-then-sync" not in text and "--icc-png-strategy" not in text


# ---------------------------------------------------------------- Step 7

def test_summary_lists_the_options_and_O_opens_the_screen(tmp_path, monkeypatch,
                                                          capsys):
    opt = next(o for o in wp.REGISTRY if o.id == "scan_scope")
    wf = _workflow(tmp_path, "tiff2jxl", extra_options={"buffering": 1})
    # Delivery C: the summary is the review screen; [a] opens the options
    # screen, and the review shown after it names both options.
    ui = PickUI([opt], {"opt.scan_scope": "2", "review.action": ["a", "q"]})
    menu = _menu(tmp_path, monkeypatch, ui)
    assert menu._review_screen(wf, STATUS) == "quit"
    assert wf["extra_options"] == {"buffering": 1, "scan_scope": "raw"}
    out = "\n".join(ui.output)
    assert "[a] all options" in out
    assert "Options: --buffering 1, --scan-scope raw" in out, out


def test_rule_check_stops_the_run_before_any_child(tmp_path, monkeypatch, capsys):
    wf = _workflow(tmp_path, "tiff2jxl", advanced_options={"delete_source": True},
                   extra_options={"restamp_then_sync": True})
    menu = _menu(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=None: calls.append(cmd) or 0)
    monkeypatch.setattr(wp.InteractiveMenu, "_ask_time_token",
                        lambda self, *a, **k: pytest.fail("HHMM token asked"),
                        raising=False)
    assert menu.execute_workflow(wf, STATUS) is False
    assert not calls
    out = " ".join(capsys.readouterr().out.split())
    assert "cannot run together" in out and "--restamp-then-sync" in out, out


def test_a_requirement_is_checked_too(tmp_path, monkeypatch, capsys):
    wf = _workflow(tmp_path, "jxl2tiff",
                   extra_options={"allow_incomplete_groups": True})
    menu = _menu(tmp_path, monkeypatch)
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=None: pytest.fail("child ran"))
    assert menu.execute_workflow(wf, STATUS) is False
    assert "needs" in " ".join(capsys.readouterr().out.split())


def test_extra_options_reach_a_direct_run(tmp_path, monkeypatch):
    wf = _workflow(tmp_path, "jxl2jxl", extra_options={"explorer_thumbnail": "off",
                                                       "recompress_encode_tag": "software"})
    menu = _menu(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=None:
                        (calls.append(list(map(str, cmd))), 0)[1])
    assert menu.execute_workflow(wf, STATUS)
    assert _has_pair(calls[0], "--explorer-thumbnail", "off")
    assert _has_pair(calls[0], "--encode-tag", "software")


def test_extra_options_are_saved_with_the_session(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    cfg.save_last_session(input_dir=str(tmp_path / EVENT),
                          extra_options={"buffering": 0, "scan_scope": "raw"})
    again = wp.ConfigManager()
    assert again.config.last_extra_options == {"buffering": 0, "scan_scope": "raw"}
    raw = json.loads((tmp_path / ".jxl_tools_config.json").read_text(encoding="utf-8"))
    assert raw["last_extra_options"] == {"buffering": 0, "scan_scope": "raw"}


# ---------------------------------------------------------------- menus R / C

# Delivery C renumbered the menu (5 = renamed folders, 8 = ICC cache): the
# letters R and C stay accepted, in either case, as those entries.
@pytest.mark.parametrize("typed,expected", [("r", "5"), ("R", "5"), ("c", "8"),
                                            ("d", "D"), ("s", "S")])
def test_main_menu_takes_the_letters_in_either_case(typed, expected, tmp_path,
                                                    monkeypatch):
    menu = _menu(tmp_path, monkeypatch)
    with mock.patch("builtins.input", side_effect=[typed]):
        assert menu.show_main_menu(False) == expected


def _lib(tmp_path):
    root = tmp_path / "lib" / EVENT
    (root / "01_Z7" / "_EXPORT" / "TIFF16 (2)").mkdir(parents=True)
    return root


def test_restamp_flow_runs_the_encoder_dry_run_first(tmp_path, monkeypatch):
    root = _lib(tmp_path)
    ui = wp.ScriptedUI({"restamp.folder": f'"{root}"', "restamp.mode": "7",
                        "restamp.subfolder": "TIFF16 (2)", "restamp.jxl_folder": "",
                        "restamp.dry_run": True, "restamp.run": True})
    menu = _menu(tmp_path, monkeypatch, ui)
    calls = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=None:
                        (calls.append(list(map(str, cmd))), 0)[1])
    menu.restamp_flow(STATUS)
    marker = menu.config.config.export_marker
    want = [str(root), "--mode", "7", "--export-marker", marker,
            "--export-subfolder", "TIFF16 (2)", "--restamp-moved"]
    assert len(calls) == 2, calls
    assert calls[0][2:] == want + ["--dry-run"]
    assert calls[1][2:] == want
    assert calls[0][1].endswith("jxl_tiff_encoder.py")


def test_restamp_flow_stops_after_a_failed_dry_run(tmp_path, monkeypatch):
    root = _lib(tmp_path)
    ui = wp.ScriptedUI({"restamp.folder": str(root), "restamp.mode": "8",
                        "restamp.dry_run": True})
    menu = _menu(tmp_path, monkeypatch, ui)
    calls = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=None: calls.append(cmd) or 1)
    menu.restamp_flow(STATUS)
    assert len(calls) == 1 and "restamp.run" not in ui.asked


def test_restamp_dry_run_is_a_real_encoder_run(tmp_path, monkeypatch, capsys):
    """The command menu R builds runs: the real encoder, a hostile folder."""
    root = _lib(tmp_path)
    ui = wp.ScriptedUI({"restamp.folder": str(root), "restamp.mode": "7",
                        "restamp.subfolder": "TIFF16 (2)", "restamp.jxl_folder": "",
                        "restamp.dry_run": True, "restamp.run": False})
    menu = _menu(tmp_path, monkeypatch, ui)
    monkeypatch.setattr(wp, "SCRIPT_DIR", SCRIPTS)
    menu.restamp_flow(STATUS)
    out = " ".join(capsys.readouterr().out.split())
    assert "Restamp: 0 would be rewritten" in out, out
    assert "Traceback" not in out


def test_clear_icc_cache_is_a_real_encoder_run(tmp_path, monkeypatch, capsys):
    home = tmp_path / "ホーム [x] 5%"
    cache = home / "AppData" / "jxl-photo" / "icc-cache" / "icc_cache.json"
    linux = home / ".config" / "jxl-photo" / "icc-cache" / "icc_cache.json"
    mac = home / "Library" / "Application Support" / "jxl-photo" / "icc-cache" / "icc_cache.json"
    for p in (cache, linux, mac):
        p.parent.mkdir(parents=True)
        p.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    menu = _menu(tmp_path, monkeypatch, wp.ScriptedUI({"icccache.confirm": True}))
    monkeypatch.setattr(wp, "SCRIPT_DIR", SCRIPTS)
    menu.clear_icc_cache_flow(STATUS)
    assert sum(p.exists() for p in (cache, linux, mac)) == 2, "no cache was cleared"
    assert "ICC cache cleared" in capsys.readouterr().out


def test_clear_icc_cache_asks_first(tmp_path, monkeypatch):
    menu = _menu(tmp_path, monkeypatch, wp.ScriptedUI({"icccache.confirm": False}))
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=None: pytest.fail("ran"))
    menu.clear_icc_cache_flow(STATUS)


def test_main_dispatches_r_and_c(tmp_path, monkeypatch):
    path = tmp_path / ".jxl_tools_config.json"
    path.write_text(json.dumps({"dependencies_checked": True, "presets": {},
                                "show_thumbnail_tip": False}), encoding="utf-8")
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path", lambda self: path)
    monkeypatch.setattr(wp.DependencyChecker, "check_dependencies",
                        lambda self, force=False, persist=True, **kw: dict(STATUS))
    picks = iter(["R", "C", "0"])
    monkeypatch.setattr(wp.InteractiveMenu, "show_main_menu",
                        lambda self, has_last: next(picks))
    seen = []
    monkeypatch.setattr(wp.InteractiveMenu, "restamp_flow",
                        lambda self, status: seen.append("R"))
    monkeypatch.setattr(wp.InteractiveMenu, "clear_icc_cache_flow",
                        lambda self, status: seen.append("C"))
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(sys, "argv", ["jxl_photo.py"])
    try:
        wp.main()
    except SystemExit:
        pass
    assert seen == ["R", "C"]


@pytest.mark.parametrize("typed,expected", [("1", 1), ("0", 0), ("3", 3)])
def test_a_number_is_typed_as_itself(tmp_path, monkeypatch, typed, expected):
    """#606: --buffering's levels were listed by position (key 1 = level 0),
    so typing 1 for "--buffering 1" set the slowest, most memory-hungry level.
    A choice made of numbers is typed as the number; '-' is the default."""
    opt = next(o for o in wp.REGISTRY if o.id == "buffering")
    wf = _workflow(tmp_path, "tiff2jxl")
    menu = _menu(tmp_path, monkeypatch, PickUI([opt], {"opt.buffering": typed}))
    menu._all_options_screen(wf)
    assert wf["extra_options"]["buffering"] == expected
    assert _has_pair(_argv(menu, wf), "--buffering", str(expected))


def test_a_number_choice_has_a_default_key(tmp_path, monkeypatch):
    opt = next(o for o in wp.REGISTRY if o.id == "buffering")
    wf = _workflow(tmp_path, "tiff2jxl", )
    wf.setdefault("extra_options", {})["buffering"] = 1
    menu = _menu(tmp_path, monkeypatch, PickUI([opt], {"opt.buffering": "-"}))
    menu._all_options_screen(wf)
    assert "buffering" not in wf["extra_options"]
