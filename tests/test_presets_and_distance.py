#!/usr/bin/env python3
"""Regressions for the configurable TIFF->JXL distance (Settings -> Step 2).

Before this, Step 2 offered only d=0 / 0.1 / 1.0 and a Custom entry, so anyone
working at another distance (0.05 here) had to open Custom and retype the number
on every single run. Entry [2] is now driven by `default_distance`.

The traps these tests exist for:
  * d=0 is a DIFFERENT code path (`jxl_tiff_encoder_lossless`) — a configured 0
    must land there, not on a d=0 lossy run;
  * 0.0 is falsy, so any `or`-based default silently turns it into 0.1;
  * the config file is hand-editable JSON with no type checking.
"""

import re
import sys
import unittest.mock as mock
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_photo as wp


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """A ConfigManager pointed at a throwaway file — never the user's real one."""
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    return wp.ConfigManager()


@pytest.fixture
def menu(cfg):
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


def _squeeze(text):
    """The lists pad each distance so their dashes line up ("d=0    — lossless");
    the assertions read it as "d=0 — lossless"."""
    return re.sub(r"(d=[0-9.]+) +—", r"\1 —", text)


def _select_destination(menu, answers):
    """Drive the quality question of "Archive TIFFs as JXL masters" (the old
    Step 2 entries, delivery C's job.quality) through the plain-text UI with
    canned keystrokes. The old Step 2 asked the effort right after; the
    review asks it now, so a trailing answer for it is simply not read."""
    workflow = {'origin_format': 'tiff', 'dest_format': 'jxl', 'effort': 7,
                '_task': 'archive_tiff', 'conversion_type': 'jxl_tiff_encoder'}
    with mock.patch.object(wp, "RICH_AVAILABLE", False), \
         mock.patch("builtins.input", side_effect=answers):
        menu._ui = wp.PlainUI()
        ok = menu._job_quality(workflow)
    assert ok, "the quality question should have accepted the selection"
    return workflow


# ─────────────────────────────────────────────
# _sane_distance
# ─────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    (0.05, 0.05),
    ("0.05", 0.05),
    (0, 0.0),
    (99, 25.0),        # clamped to cjxl's ceiling
    (20, 20.0),        # 15-25 is valid (v2.22.5)
    (-3, 0.0),
    ("abc", 0.1),      # unparseable -> fallback
    ("0,05", 0.1),     # comma decimal (pt-BR keyboard) -> fallback, not 5.0
    (None, 0.1),
    (float("nan"), 0.1),
])
def test_sane_distance(raw, expected):
    assert wp._sane_distance(raw) == expected


def test_sane_distance_honors_custom_fallback():
    assert wp._sane_distance("garbage", fallback=0.05) == 0.05


# ─────────────────────────────────────────────
# Step 2 entry [2] follows the setting
# ─────────────────────────────────────────────

def test_enter_takes_the_configured_distance(menu, cfg):
    cfg.config.default_distance = 0.05
    workflow = _select_destination(menu, ["", ""])
    assert workflow['distance'] == 0.05
    assert workflow['conversion_type'] == 'jxl_tiff_encoder'
    assert workflow['dest_format'] == 'jxl'


def test_the_menu_names_each_distance(menu, cfg, capsys):
    _select_destination(menu, ["", ""])
    printed = _squeeze(capsys.readouterr().out)
    # v2.22.3: the tip sits under the list, the scan line names the review row
    assert printed.index("lossy about the same as above") > printed.index("[7] your distance")
    assert "Film scans row" in printed
    for text in ("d=0 — lossless", "d=0.05 — finest lossy available",
                 "d=0.1 — near-lossless (recommended)", "d=0.5 — high-quality lossy",
                 "d=0.7 — visually lossless (keeps the grain texture)",
                 "d=1 — visually lossless (JPEG XL's definition)",
                 "your distance", "lossy about the same as above"):
        assert text in printed, text


def test_enter_defaults_to_the_historical_recommendation(menu, cfg):
    """Untouched config = exactly the old behaviour."""
    assert cfg.config.default_distance == 0.1
    workflow = _select_destination(menu, ["", ""])
    assert workflow['distance'] == 0.1
    assert workflow['conversion_type'] == 'jxl_tiff_encoder'


def test_configured_zero_routes_to_the_lossless_encoder(menu, cfg):
    """d=0 is a separate script path; a configured 0 must not run the lossy one."""
    cfg.config.default_distance = 0.0
    workflow = _select_destination(menu, ["", ""])
    assert workflow['distance'] == 0.0
    assert workflow['conversion_type'] == 'jxl_tiff_encoder_lossless'


def test_corrupt_stored_distance_does_not_break_the_menu(menu, cfg):
    """The config is hand-editable JSON: a string must not reach an f-string
    format spec and take the whole quality menu down."""
    cfg.config.default_distance = "not a number"
    workflow = _select_destination(menu, ["", ""])
    assert workflow['distance'] == 0.1


def test_fixed_entries(menu, cfg):
    cfg.config.default_distance = 0.05
    for key, d in (("1", 0.0), ("2", 0.05), ("3", 0.1), ("4", 0.5), ("5", 0.7),
                   ("6", 1.0)):
        assert _select_destination(menu, [key, ""])['distance'] == d


def test_a_distance_off_the_list_is_the_custom_default(menu, cfg):
    cfg.config.last_distance = None
    cfg.config.default_distance = 0.03
    assert _select_destination(menu, ["", "", ""])['distance'] == 0.03


# ─────────────────────────────────────────────
# Custom entry [6] remembers the last value
# ─────────────────────────────────────────────

def test_custom_defaults_to_last_used_distance(menu, cfg):
    cfg.config.last_distance = 0.03
    cfg.config.default_distance = 0.05
    workflow = _select_destination(menu, ["7", "", ""])   # accept the default
    assert workflow['distance'] == 0.03


def test_custom_default_survives_a_zero_last_distance(menu, cfg):
    """0.0 is falsy: an `or`-based default would silently convert it to 0.1
    and turn a lossless habit into a lossy run."""
    cfg.config.last_distance = 0.0
    workflow = _select_destination(menu, ["7", "", ""])
    assert workflow['distance'] == 0.0


def test_custom_falls_back_to_the_setting_when_nothing_was_used_yet(menu, cfg):
    cfg.config.last_distance = None
    cfg.config.default_distance = 0.05
    workflow = _select_destination(menu, ["7", "", ""])
    assert workflow['distance'] == 0.05


def test_custom_still_accepts_a_typed_value(menu, cfg):
    cfg.config.default_distance = 0.05
    workflow = _select_destination(menu, ["7", "2.5", ""])
    assert workflow['distance'] == 2.5


# ─────────────────────────────────────────────
# Settings round-trip
# ─────────────────────────────────────────────

def _edit_settings(menu, distance_input):
    """Answers in prompt order: staging, workers, quality, effort, distance,
    confirm-delete, export marker, jxl-thumb tip (Windows), update check."""
    answers = ["", "", "", "", distance_input, "", "", "", ""]
    with mock.patch.object(wp, "RICH_AVAILABLE", False), \
         mock.patch("builtins.input", side_effect=answers):
        menu.edit_settings()


def test_settings_persist_distance_to_disk(menu, cfg):
    _edit_settings(menu, "0.05")
    assert cfg.config.default_distance == 0.05
    reloaded = wp.ConfigManager()
    assert reloaded.config.default_distance == 0.05, "the value must survive a restart"


def test_settings_clamp_distance(menu, cfg):
    _edit_settings(menu, "99")
    assert cfg.config.default_distance == 25.0


def test_settings_reject_garbage_and_keep_the_old_value(menu, cfg):
    cfg.config.default_distance = 0.05
    _edit_settings(menu, "abc")
    assert cfg.config.default_distance == 0.05


def test_settings_empty_input_keeps_current(menu, cfg):
    cfg.config.default_distance = 0.05
    _edit_settings(menu, "")
    assert cfg.config.default_distance == 0.05


def test_old_config_without_the_key_still_loads(tmp_path, monkeypatch):
    """Configs written before this feature have no default_distance."""
    path = tmp_path / ".jxl_tools_config.json"
    path.write_text('{"default_workers": 12, "last_distance": 0.05}', encoding="utf-8")
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path", lambda self: path)
    loaded = wp.ConfigManager()
    assert loaded.config.default_distance == 0.1
    assert loaded.config.default_workers == 12


# ─────────────────────────────────────────────
# Settings actually reach the next run
# ─────────────────────────────────────────────

def test_changing_workers_in_settings_applies_to_the_next_run(menu, cfg):
    """The 'repeat resets my workers' complaint: Settings used to write
    default_workers while every run read last_workers, so editing did nothing."""
    cfg.config.last_workers = 12
    answers = ["", "8", "", "", "", "", "", "", ""]   # ... marker, tip, update check
    with mock.patch.object(wp, "RICH_AVAILABLE", False), \
         mock.patch("builtins.input", side_effect=answers):
        menu.edit_settings()
    assert cfg.config.default_workers == 8
    assert cfg.config.last_workers == 8, "the value the next run reads must follow the setting"


def test_untouched_settings_do_not_clobber_the_last_run(menu, cfg):
    """Walking through the screen pressing Enter must not reset a session that
    deliberately ran with different values."""
    cfg.config.last_workers = 12
    cfg.config.last_effort = 9
    cfg.config.last_distance = 0.05
    answers = ["", "", "", "", "", "", "", "", ""]
    with mock.patch.object(wp, "RICH_AVAILABLE", False), \
         mock.patch("builtins.input", side_effect=answers):
        menu.edit_settings()
    assert cfg.config.last_workers == 12
    assert cfg.config.last_effort == 9
    assert cfg.config.last_distance == 0.05


def _write_manifest(path, rows, direction="tiff2jxl"):
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Source", "Destination", "Mode", "Direction"])
        for src, dst, mode in rows:
            w.writerow([src, dst, mode, direction])
    return str(path)


def _saved_manifest_session(tmp_path, manifest, **overrides):
    """A config file describing a finished mode-99 run."""
    import json
    data = {
        "last_input_dir": str(tmp_path),
        "last_output_mode": "99",
        "last_manifest_path": manifest,
        "last_origin_format": "tiff",
        "last_dest_format": "jxl",
        "last_conversion_type": "jxl_tiff_encoder",
        "last_workers": 12,
        "last_effort": 9,
        "last_distance": 0.05,
        "dependencies_checked": True,
    }
    data.update(overrides)
    path = tmp_path / ".jxl_tools_config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _repeat_via_main(monkeypatch, config_path, answers):
    """Drive main() through 'Repeat last workflow' and capture the workflow that
    would have been executed. Returns the captured workflow, or None."""
    captured = []
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path", lambda self: config_path)
    monkeypatch.setattr(wp.DependencyChecker, "check_dependencies",
                        lambda self, force=False, persist=True, **kw:
                        {"cjxl": True, "djxl": True})
    monkeypatch.setattr(wp.InteractiveMenu, "execute_workflow",
                        lambda self, wf, st: (captured.append(wf), True)[1])
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(sys, "argv", ["jxl_photo.py"])
    with mock.patch("builtins.input", side_effect=answers):
        wp.main()
    return captured[0] if captured else None


# Menu "2", then sync (Enter), no dry run (Enter), proceed (Enter), then exit.
REPEAT_ANSWERS = ["2", "", "", "", "0"]


def test_manifest_run_can_be_repeated(tmp_path, monkeypatch):
    """The whole point: re-running a manifest in sync used to require walking
    through the entire wizard again, because mode 99 was never saved."""
    src = tmp_path / "shoot"
    src.mkdir()
    manifest = _write_manifest(tmp_path / "manifest_x.csv", [(str(src), str(src), 6)])
    cfg_path = _saved_manifest_session(tmp_path, manifest)

    workflow = _repeat_via_main(monkeypatch, cfg_path, REPEAT_ANSWERS)

    assert workflow is not None, "the repeat never reached execution"
    assert workflow['mode'] == 99
    assert workflow['manifest_entries'] == [(str(src), str(src), 6)]
    assert workflow['manifest_path'] == manifest


def test_manifest_repeat_rereads_the_csv(tmp_path, monkeypatch):
    """Entries are re-read, never replayed from a stored copy — so folders added
    or removed in Excel between runs are honoured."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    manifest_file = tmp_path / "manifest_x.csv"
    manifest = _write_manifest(manifest_file, [(str(a), str(a), 6)])
    cfg_path = _saved_manifest_session(tmp_path, manifest)

    # The user edits the manifest between runs.
    _write_manifest(manifest_file, [(str(a), str(a), 6), (str(b), str(b), 6)])

    workflow = _repeat_via_main(monkeypatch, cfg_path, REPEAT_ANSWERS)
    assert len(workflow['manifest_entries']) == 2, "the edited CSV must be picked up"


def test_manifest_repeat_keeps_the_saved_parameters(tmp_path, monkeypatch):
    src = tmp_path / "shoot"
    src.mkdir()
    manifest = _write_manifest(tmp_path / "manifest_x.csv", [(str(src), str(src), 6)])
    cfg_path = _saved_manifest_session(tmp_path, manifest)

    workflow = _repeat_via_main(monkeypatch, cfg_path, REPEAT_ANSWERS)
    assert workflow['workers'] == 12
    assert workflow['effort'] == 9
    assert workflow['distance'] == 0.05


def test_manifest_repeat_defaults_to_sync(tmp_path, monkeypatch):
    """Pressing Enter at the prompt must mean sync, not overwrite — a recurring
    library run that overwrote everything would re-encode the whole archive."""
    src = tmp_path / "shoot"
    src.mkdir()
    manifest = _write_manifest(tmp_path / "manifest_x.csv", [(str(src), str(src), 6)])
    cfg_path = _saved_manifest_session(tmp_path, manifest)

    workflow = _repeat_via_main(monkeypatch, cfg_path, REPEAT_ANSWERS)
    assert workflow['advanced_options']['sync'] is True
    assert workflow['advanced_options']['overwrite'] is False
    assert workflow['dry_run'] is False


def test_manifest_repeat_refuses_a_direction_mismatch(tmp_path, monkeypatch):
    """The guard that stops a tiff2jxl manifest from being replayed by a
    jxl2tiff session has to protect the repeat too, not only the wizard."""
    src = tmp_path / "shoot"
    src.mkdir()
    manifest = _write_manifest(tmp_path / "manifest_x.csv",
                               [(str(src), str(src), 6)], direction="jxl2tiff")
    cfg_path = _saved_manifest_session(tmp_path, manifest)

    workflow = _repeat_via_main(monkeypatch, cfg_path, ["2", "0"])
    assert workflow is None, "a mismatched manifest must never reach execution"


def test_menu_disables_repeat_when_the_manifest_is_gone(tmp_path, monkeypatch):
    manifest = str(tmp_path / "deleted.csv")
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: _saved_manifest_session(tmp_path, manifest))
    cm = wp.ConfigManager()
    menu = wp.InteractiveMenu(cm, wp.DependencyChecker(cm))
    with mock.patch.object(wp, "RICH_AVAILABLE", False), \
         mock.patch("builtins.input", side_effect=["2", "0"]):
        choice = menu.show_main_menu(True)
    assert choice == "0", "entry [2] must be rejected while its CSV is missing"


def test_menu_offers_the_manifest_by_name(tmp_path, monkeypatch, capsys):
    src = tmp_path / "shoot"
    src.mkdir()
    manifest = _write_manifest(tmp_path / "manifest_20260727.csv", [(str(src), str(src), 6)])
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: _saved_manifest_session(tmp_path, manifest))
    cm = wp.ConfigManager()
    menu = wp.InteractiveMenu(cm, wp.DependencyChecker(cm))
    with mock.patch.object(wp, "RICH_AVAILABLE", False), \
         mock.patch("builtins.input", side_effect=["2"]):
        menu.show_main_menu(True)
    assert "(manifest: manifest_20260727.csv)" in capsys.readouterr().out


def test_a_normal_run_clears_the_stored_manifest(tmp_path, monkeypatch):
    """Otherwise the menu would offer to repeat a manifest that has nothing to
    do with the session actually saved."""
    manifest = _write_manifest(tmp_path / "manifest_x.csv", [(str(tmp_path), str(tmp_path), 6)])
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: _saved_manifest_session(tmp_path, manifest))
    cm = wp.ConfigManager()
    assert cm.config.last_manifest_path == manifest

    cm.save_last_session(input_dir=str(tmp_path), output_mode="6", manifest_path=None)
    assert cm.config.last_manifest_path is None


def test_omitting_manifest_path_keeps_the_stored_one(tmp_path, monkeypatch):
    """save_last_session is called from several places; only the ones that know
    about manifests should touch the field."""
    manifest = _write_manifest(tmp_path / "manifest_x.csv", [(str(tmp_path), str(tmp_path), 6)])
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: _saved_manifest_session(tmp_path, manifest))
    cm = wp.ConfigManager()
    cm.save_last_session(workers=6)
    assert cm.config.last_manifest_path == manifest


def test_settings_screen_flags_an_overridden_default(menu, cfg, capsys):
    cfg.config.default_workers = 4
    cfg.config.last_workers = 12
    answers = ["", "", "", "", "", "", "", "", ""]
    with mock.patch.object(wp, "RICH_AVAILABLE", False), \
         mock.patch("builtins.input", side_effect=answers):
        menu.edit_settings()
    assert "last run used 12" in capsys.readouterr().out


# ─────────────────────────────────────────────
# Named presets
# ─────────────────────────────────────────────

def _presets_menu(menu, answers):
    with mock.patch.object(wp, "RICH_AVAILABLE", False), \
         mock.patch("builtins.input", side_effect=answers):
        menu.presets_menu({"cjxl": True, "djxl": True})


def test_session_snapshot_covers_every_last_field(cfg):
    """Built from the dataclass fields, so a future last_* setting is included
    without anyone remembering to update a list."""
    snapshot = cfg.session_snapshot()
    expected = {f for f in wp.ToolConfig.__dataclass_fields__ if f.startswith("last_")}
    assert set(snapshot) == expected
    assert "last_manifest_path" in snapshot


def test_save_and_run_a_preset(tmp_path, monkeypatch, cfg):
    src = tmp_path / "shoot"
    src.mkdir()
    cfg.config.last_input_dir = str(src)
    cfg.config.last_output_mode = "1"
    cfg.config.last_origin_format, cfg.config.last_dest_format = "tiff", "jxl"
    cfg.config.last_conversion_type = "jxl_tiff_encoder"
    cfg.config.last_workers, cfg.config.last_distance = 12, 0.05

    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    _presets_menu(menu, ["S", "sync-teste-d005", "B"])
    assert "sync-teste-d005" in cfg.config.presets
    assert wp.ConfigManager().config.presets, "the preset must survive a restart"

    captured = []
    monkeypatch.setattr(wp.InteractiveMenu, "execute_workflow",
                        lambda self, wf, st: (captured.append(wf), True)[1])
    # run preset 1: folder (Enter), sync (Enter), dry-run (Enter), proceed (Enter)
    _presets_menu(menu, ["1", "", "", "", ""])
    assert captured, "selecting a preset must run it"
    assert captured[0]['workers'] == 12
    assert captured[0]['distance'] == 0.05
    assert captured[0]['input_dir'] == str(src)


def test_preset_of_a_manifest_run(tmp_path, monkeypatch, cfg):
    """The case this whole feature exists for: a recurring manifest sync kept
    under its own name, unaffected by whatever ran last."""
    src = tmp_path / "shoot"
    src.mkdir()
    manifest = _write_manifest(tmp_path / "manifest_nightly.csv", [(str(src), str(src), 6)])
    cfg.config.last_input_dir = str(src)
    cfg.config.last_output_mode = "99"
    cfg.config.last_manifest_path = manifest
    cfg.config.last_origin_format, cfg.config.last_dest_format = "tiff", "jxl"
    cfg.config.last_conversion_type = "jxl_tiff_encoder"
    cfg.config.last_workers = 12

    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    _presets_menu(menu, ["S", "nightly", "B"])

    # A later, unrelated run overwrites the "last workflow" slot...
    cfg.config.last_output_mode = "1"
    cfg.config.last_manifest_path = None
    cfg.config.last_workers = 4

    captured = []
    monkeypatch.setattr(wp.InteractiveMenu, "execute_workflow",
                        lambda self, wf, st: (captured.append(wf), True)[1])
    _presets_menu(menu, ["1", "", "", ""])   # manifest: no input-folder question

    assert captured, "the preset must still be runnable"
    assert captured[0]['mode'] == 99
    assert captured[0]['manifest_path'] == manifest
    assert captured[0]['workers'] == 12, "the preset's own workers, not the newer run's"


def test_preset_list_describes_the_workflow(tmp_path, cfg, capsys):
    manifest = _write_manifest(tmp_path / "manifest_nightly.csv", [(str(tmp_path), str(tmp_path), 6)])
    cfg.config.presets = {"nightly": {
        "last_origin_format": "tiff", "last_dest_format": "jxl",
        "last_output_mode": "99", "last_manifest_path": manifest,
        "last_workers": 12, "last_distance": 0.05, "saved_at": "2026-07-27 21:00",
    }}
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    _presets_menu(menu, ["B"])
    out = capsys.readouterr().out
    assert "nightly" in out
    assert "manifest_nightly.csv" in out
    assert "workers 12" in out and "d=0.05" in out


def test_delete_a_preset(cfg):
    cfg.config.presets = {"a": {"last_input_dir": "x"}, "b": {"last_input_dir": "y"}}
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    _presets_menu(menu, ["D", "1", "y", "B"])
    assert set(cfg.config.presets) == {"b"}


def test_delete_requires_confirmation(cfg):
    cfg.config.presets = {"a": {"last_input_dir": "x"}}
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    _presets_menu(menu, ["D", "1", "", "B"])
    assert set(cfg.config.presets) == {"a"}


def test_saving_over_an_existing_preset_asks_first(cfg):
    cfg.config.last_input_dir = "old"
    cfg.config.presets = {"nightly": {"last_input_dir": "keep-me"}}
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    _presets_menu(menu, ["S", "nightly", "", "B"])          # declines the overwrite
    assert cfg.config.presets["nightly"]["last_input_dir"] == "keep-me"
    _presets_menu(menu, ["S", "nightly", "y", "B"])         # accepts it
    assert cfg.config.presets["nightly"]["last_input_dir"] == "old"


@pytest.mark.parametrize("bad_name", ["", "   ", "x" * 41, "bad\x00name"])
def test_invalid_preset_names_are_rejected(cfg, bad_name):
    cfg.config.last_input_dir = "somewhere"
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    _presets_menu(menu, ["S", bad_name, "B"])
    assert not cfg.config.presets


def test_cannot_save_a_preset_before_any_run(cfg):
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    _presets_menu(menu, ["S", "B"])
    assert not cfg.config.presets


def test_config_without_presets_key_still_loads(tmp_path, monkeypatch):
    path = tmp_path / ".jxl_tools_config.json"
    path.write_text('{"last_workers": 12}', encoding="utf-8")
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path", lambda self: path)
    loaded = wp.ConfigManager()
    assert loaded.config.presets == {}


# ─────────────────────────────────────────────
# --run-preset (unattended: Task Scheduler / cron)
# ─────────────────────────────────────────────

def _config_with_preset(tmp_path, name="nightly", **preset):
    import json
    data = {
        "last_input_dir": str(tmp_path),
        "dependencies_checked": True,
        "presets": {name: {
            "last_input_dir": str(tmp_path),
            "last_output_mode": "6",
            "last_origin_format": "tiff",
            "last_dest_format": "jxl",
            "last_conversion_type": "jxl_tiff_encoder",
            "last_workers": 12,
            "last_distance": 0.05,
            **preset,
        }},
    }
    path = tmp_path / ".jxl_tools_config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _run_cli(monkeypatch, config_path, argv):
    """Run main() as the CLI would. Returns (exit_code, workflow-or-None)."""
    captured = []
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path", lambda self: config_path)
    monkeypatch.setattr(wp.DependencyChecker, "check_dependencies",
                        lambda self, force=False, persist=True, **kw:
                        {"cjxl": True, "djxl": True})
    monkeypatch.setattr(wp.InteractiveMenu, "execute_workflow",
                        lambda self, wf, st: (captured.append(wf), True)[1])
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(sys, "argv", ["jxl_photo.py"] + argv)
    # Any prompt at all would hang a scheduled task — make one an instant failure.
    monkeypatch.setattr("builtins.input",
                        lambda *a, **k: pytest.fail("--run-preset must not prompt"))
    with pytest.raises(SystemExit) as exit_info:
        wp.main()
    return exit_info.value.code, (captured[0] if captured else None)


def test_run_preset_executes_without_any_prompt(tmp_path, monkeypatch):
    code, workflow = _run_cli(monkeypatch, _config_with_preset(tmp_path), ["--run-preset", "nightly"])
    assert code == 0
    assert workflow['workers'] == 12
    assert workflow['distance'] == 0.05
    assert workflow['mode'] == 6


def test_run_preset_defaults_to_sync_not_overwrite(tmp_path, monkeypatch):
    """A scheduled job that re-encoded the whole archive every night because it
    silently defaulted to overwrite would be a disaster."""
    _, workflow = _run_cli(monkeypatch, _config_with_preset(tmp_path), ["--run-preset", "nightly"])
    assert workflow['advanced_options']['sync'] is True
    assert workflow['advanced_options']['overwrite'] is False
    assert workflow['dry_run'] is False


def test_run_preset_honors_the_flags(tmp_path, monkeypatch):
    _, workflow = _run_cli(monkeypatch, _config_with_preset(tmp_path),
                           ["--run-preset", "nightly", "--overwrite", "--dry-run"])
    assert workflow['advanced_options']['overwrite'] is True
    assert workflow['advanced_options']['sync'] is False
    assert workflow['dry_run'] is True


def test_run_preset_never_inherits_the_stored_dry_run(tmp_path, monkeypatch):
    """The saved session may have been a simulation; without the flag the run is
    real, and vice versa — the decision is always the caller's."""
    cfg_path = _config_with_preset(tmp_path, last_advanced_options={"sync": True, "dry_run": True})
    _, workflow = _run_cli(monkeypatch, cfg_path, ["--run-preset", "nightly"])
    assert workflow['dry_run'] is False


def test_run_preset_of_a_manifest(tmp_path, monkeypatch):
    src = tmp_path / "shoot"
    src.mkdir()
    manifest = _write_manifest(tmp_path / "manifest_nightly.csv", [(str(src), str(src), 6)])
    cfg_path = _config_with_preset(tmp_path, last_output_mode="99", last_manifest_path=manifest)
    code, workflow = _run_cli(monkeypatch, cfg_path, ["--run-preset", "nightly"])
    assert code == 0
    assert workflow['mode'] == 99
    assert workflow['manifest_entries'] == [(str(src), str(src), 6)]


def test_unknown_preset_exits_with_an_error(tmp_path, monkeypatch, capsys):
    code, workflow = _run_cli(monkeypatch, _config_with_preset(tmp_path), ["--run-preset", "typo"])
    assert code == 2 and workflow is None
    out = capsys.readouterr().out
    assert "typo" in out and "nightly" in out, "the available names must be listed"


def test_deleting_presets_refuse_to_run_unattended(tmp_path, monkeypatch, capsys):
    """Mode 8 + delete_source is gated behind an HHMM token nobody can type in a
    scheduled task; skipping that gate would let cron delete originals."""
    cfg_path = _config_with_preset(tmp_path, last_output_mode="8",
                                   last_advanced_options={"sync": True, "delete_source": True})
    code, workflow = _run_cli(monkeypatch, cfg_path, ["--run-preset", "nightly"])
    assert code == 1 and workflow is None
    assert "deletes source files" in capsys.readouterr().out


def test_deleting_preset_may_still_be_simulated(tmp_path, monkeypatch):
    cfg_path = _config_with_preset(tmp_path, last_output_mode="8",
                                   last_advanced_options={"sync": True, "delete_source": True})
    code, workflow = _run_cli(monkeypatch, cfg_path, ["--run-preset", "nightly", "--dry-run"])
    assert code == 0 and workflow['dry_run'] is True


def test_run_preset_fails_when_the_folder_is_gone(tmp_path, monkeypatch):
    cfg_path = _config_with_preset(tmp_path, last_input_dir=str(tmp_path / "deleted"))
    code, workflow = _run_cli(monkeypatch, cfg_path, ["--run-preset", "nightly"])
    assert code == 1 and workflow is None


def test_list_presets_prints_and_exits(tmp_path, monkeypatch, capsys):
    code, _ = _run_cli(monkeypatch, _config_with_preset(tmp_path), ["--list-presets"])
    assert code == 0
    out = capsys.readouterr().out
    assert "nightly" in out and "workers 12" in out


@pytest.mark.parametrize("argv", [["--dry-run"], ["--overwrite"]])
def test_flags_without_run_preset_are_rejected(tmp_path, monkeypatch, argv):
    """A stray --dry-run that silently did nothing would run a REAL conversion
    for someone who believed they asked for a simulation."""
    code, _ = _run_cli(monkeypatch, _config_with_preset(tmp_path), argv)
    assert code == 2, "argparse must reject it instead of ignoring it"


# ─────────────────────────────────────────────
# "Make smaller or resized JXL copies" offers d=5 (2026-11-06)
# ─────────────────────────────────────────────

def _derive_quality(menu, answers):
    workflow = {'origin_format': 'jxl', 'dest_format': 'jxl', '_task': 'derive_jxl',
                'conversion_type': 'jxl_recompressor'}
    with mock.patch.object(wp, "RICH_AVAILABLE", False), \
         mock.patch("builtins.input", side_effect=answers):
        menu._ui = wp.PlainUI()
        assert menu._job_quality(workflow)
    return workflow


def test_copies_offer_distance_5(menu, capsys):
    """d=5 sits after d=3 and before the custom entry; above 5 there is no
    named entry (libjxl halves the resolution itself from d=10)."""
    workflow = _derive_quality(menu, ["6"])
    printed = _squeeze(capsys.readouterr().out)
    assert workflow['distance'] == 5.0
    assert "[6] d=5 — about quality 46" in printed
    assert "[7] your distance (0-25)" in printed
    assert [v for v, _n, _x in wp._RECOMPRESS_CHOICES] == [0.5, 0.7, 1.0, 2.0, 3.0, 5.0]
    assert wp._distance_label(5) == "about quality 46"


def test_both_lists_offer_0_7_and_name_the_quality_scale(menu, capsys):
    """0.7 in both lists (2026-11-05); 0.7 and 1 share "visually lossless",
    so the review names them with their note; d=2/3/5 by cjxl's -q scale."""
    assert 0.7 in [v for v, _n, _x in wp._DISTANCE_CHOICES]
    assert wp._distance_label(0.7) == "visually lossless (keeps the grain texture)"
    assert wp._distance_label(1) == "visually lossless (JPEG XL's definition)"
    assert wp._distance_label(0.5) == "high-quality lossy"      # unique: no note
    _derive_quality(menu, [""])
    printed = _squeeze(capsys.readouterr().out)
    assert "[4] d=2 — about quality 79 (phones, sharing)" in printed
    assert "[5] d=3 — about quality 68" in printed
    # q = 100 - (d - 0.1) / 0.09, cjxl's own mapping
    for d, q in ((2.0, 79), (3.0, 68), (5.0, 46)):
        assert round(100 - (d - 0.1) / 0.09) == q


def test_copies_custom_entry_still_takes_any_distance(menu):
    workflow = _derive_quality(menu, ["7", "25"])
    assert workflow['distance'] == 25.0


# ─────────────────────────────────────────────
# Typical file sizes next to the distances (2026-11-06)
# ─────────────────────────────────────────────

@pytest.mark.parametrize("mb, text", [
    (172.9, "173 MB"), (10.0, "10 MB"), (5.46, "5.5 MB"), (1.0, "1.0 MB"),
    (0.57, "570 KB"), (0.08, "80 KB")])
def test_size_text(mb, text):
    assert wp._size_text(mb) == text


def test_every_offered_distance_has_a_measured_size():
    for v, _n, _x in wp._DISTANCE_CHOICES + wp._RECOMPRESS_CHOICES:
        assert v in wp._TYPICAL_SIZE_MB, v
        assert all(c.strip() for c in wp._size_cells(v))


def test_the_quality_list_shows_sizes_per_resolution(menu, cfg, capsys):
    _select_destination(menu, ["", ""])
    printed = _squeeze(capsys.readouterr().out)
    lines = printed.splitlines()
    header = next(l for l in lines if "45 MP" in l)
    assert "24 MP" in header and "12 MP" in header
    lossless = next(l for l in lines if "[1] d=0 — lossless" in l)
    assert lossless.rstrip().endswith("43 MB") and "173 MB" in lossless
    assert "lossy about the same as above" in printed


def test_your_distance_shows_every_distance_first(menu, cfg, capsys):
    workflow = _select_destination(menu, ["7", "0.3"])
    printed = _squeeze(capsys.readouterr().out)
    assert workflow['distance'] == 0.3
    table = printed[printed.index("Typical file size"):]
    for row in ("TIFF 16-bit, ZIP", "JXL sizes from a 16-bit TIFF", "d=0.05 47 / 23 / 11 MB",
                "JXL d=0.3", "JXL d=25",
                "JXL d=10 — libjxl halves the resolution"):
        assert row in table, row


def test_copies_list_shows_sizes_without_tiff_rows(menu, capsys):
    workflow = _derive_quality(menu, ["7", "7"])
    printed = _squeeze(capsys.readouterr().out)
    assert workflow['distance'] == 7.0
    assert "[6] d=5 — about quality 46" in printed and "1.1 MB" in printed
    assert "Typical file size" in printed and "TIFF 16-bit" not in printed
    assert "full-size copy" in printed


def test_lossless_shows_its_8bit_size_on_the_list(menu, cfg, capsys):
    """173 MB is a 16-bit TIFF's lossless file: a title over the size columns
    says 16-bit, and the note gives an 8-bit TIFF's lossless size, so it does
    not scare someone who exports 8-bit TIFFs."""
    workflow = _select_destination(menu, ["", ""])
    printed = _squeeze(capsys.readouterr().out)
    lines = printed.splitlines()
    h = next(n for n, l in enumerate(lines) if "45 MP" in l)
    title = lines[h - 1]
    assert title.strip() == "JXL size from a 16-bit TIFF (typical)"
    assert len(title.rstrip()) == len(lines[h].rstrip())      # flush right, over the sizes
    i = next(n for n, l in enumerate(lines) if "[1] d=0 — lossless" in l)
    assert "173 MB" in lines[i] and "*" not in printed
    assert ("From an 8-bit TIFF: lossless is 43 / 21 / 10 MB (about the size of d=0.05), "
            "lossy about the same as above.") in " ".join(printed.split())
    assert workflow['distance'] == 0.1


def test_the_dashes_line_up(menu, capsys):
    """Each list pads its distances to the longest one, so every dash sits in
    the same column (the owner's request, 2026-11-06)."""
    _derive_quality(menu, [""])
    out = capsys.readouterr().out
    cols = {l.index("—") for l in out.splitlines() if re.match(r"\s*\[\d\] d=", l)}
    assert len(cols) == 1, cols


def test_copies_title_their_sizes(menu, capsys):
    _derive_quality(menu, [""])
    lines = _squeeze(capsys.readouterr().out).splitlines()
    h = next(n for n, l in enumerate(lines) if "45 MP" in l)
    assert lines[h - 1].strip() == "JXL size of a full-size copy (typical)"


def test_the_8bit_line_is_not_an_answer(menu, cfg):
    """An empty key is a line to read: typing nothing takes the default and a
    key past the list is asked again."""
    workflow = _select_destination(menu, ["8", "2", ""])
    assert workflow['distance'] == 0.05


def test_scripted_ui_refuses_the_display_line():
    ui = wp.ScriptedUI({"q": ""})
    with pytest.raises(AssertionError):
        ui.choose("q", "Q", [("1", "a", "x"), ("", "b", "y")], columns=["c"])
