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


def _select_destination(menu, answers):
    """Drive Step 2 through the plain-text branch with canned keystrokes."""
    workflow = {'origin_format': 'tiff', 'effort': 7}
    with mock.patch.object(wp, "RICH_AVAILABLE", False), \
         mock.patch("builtins.input", side_effect=answers):
        ok = menu._wizard_select_destination(workflow, {'cjxl': True})
    assert ok, "Step 2 should have accepted the selection"
    return workflow


# ─────────────────────────────────────────────
# _sane_distance
# ─────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    (0.05, 0.05),
    ("0.05", 0.05),
    (0, 0.0),
    (99, 15.0),        # clamped to cjxl's ceiling
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

def test_option2_uses_configured_distance(menu, cfg):
    cfg.config.default_distance = 0.05
    workflow = _select_destination(menu, ["2", ""])
    assert workflow['distance'] == 0.05
    assert workflow['conversion_type'] == 'jxl_tiff_encoder'
    assert workflow['dest_format'] == 'jxl'


def test_option2_shows_the_number_in_the_menu(menu, cfg, capsys):
    """The point of the feature is seeing your own value listed, not d=0.1."""
    cfg.config.default_distance = 0.05
    _select_destination(menu, ["2", ""])
    printed = capsys.readouterr().out
    assert "d=0.05" in printed
    assert "0.1" not in printed.split("[3]")[0], "entry [2] must not still read 0.1"


def test_option2_defaults_to_the_historical_recommendation(menu, cfg):
    """Untouched config = exactly the old behaviour, label included."""
    assert cfg.config.default_distance == 0.1
    workflow = _select_destination(menu, ["2", ""])
    assert workflow['distance'] == 0.1
    assert workflow['conversion_type'] == 'jxl_tiff_encoder'


def test_configured_zero_routes_to_the_lossless_encoder(menu, cfg):
    """d=0 is a separate script path; a configured 0 must not run the lossy one."""
    cfg.config.default_distance = 0.0
    workflow = _select_destination(menu, ["2", ""])
    assert workflow['distance'] == 0.0
    assert workflow['conversion_type'] == 'jxl_tiff_encoder_lossless'


def test_corrupt_stored_distance_does_not_break_the_menu(menu, cfg):
    """The config is hand-editable JSON: a string must not reach an f-string
    format spec and take the whole Step 2 menu down."""
    cfg.config.default_distance = "not a number"
    workflow = _select_destination(menu, ["2", ""])
    assert workflow['distance'] == 0.1


def test_fixed_entries_are_unchanged(menu, cfg):
    cfg.config.default_distance = 0.05
    assert _select_destination(menu, ["1", ""])['distance'] == 0.0
    assert _select_destination(menu, ["3", ""])['distance'] == 1.0


# ─────────────────────────────────────────────
# Custom entry [4] remembers the last value
# ─────────────────────────────────────────────

def test_custom_defaults_to_last_used_distance(menu, cfg):
    cfg.config.last_distance = 0.03
    cfg.config.default_distance = 0.05
    workflow = _select_destination(menu, ["4", "", ""])   # accept the default
    assert workflow['distance'] == 0.03


def test_custom_default_survives_a_zero_last_distance(menu, cfg):
    """0.0 is falsy: an `or`-based default would silently convert it to 0.1
    and turn a lossless habit into a lossy run."""
    cfg.config.last_distance = 0.0
    workflow = _select_destination(menu, ["4", "", ""])
    assert workflow['distance'] == 0.0


def test_custom_falls_back_to_the_setting_when_nothing_was_used_yet(menu, cfg):
    cfg.config.last_distance = None
    cfg.config.default_distance = 0.05
    workflow = _select_destination(menu, ["4", "", ""])
    assert workflow['distance'] == 0.05


def test_custom_still_accepts_a_typed_value(menu, cfg):
    cfg.config.default_distance = 0.05
    workflow = _select_destination(menu, ["4", "2.5", ""])
    assert workflow['distance'] == 2.5


# ─────────────────────────────────────────────
# Settings round-trip
# ─────────────────────────────────────────────

def _edit_settings(menu, distance_input):
    """Answers in prompt order: staging, workers, quality, effort, distance,
    confirm-delete, export marker."""
    answers = ["", "", "", "", distance_input, "", ""]
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
    assert cfg.config.default_distance == 15.0


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
    answers = ["", "8", "", "", "", "", ""]   # staging, workers, quality, effort, distance, confirm, marker
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
    answers = ["", "", "", "", "", "", ""]
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
                        lambda self, force=False: {"cjxl": True, "djxl": True})
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


def test_menu_offers_the_manifest_by_name(tmp_path, monkeypatch):
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
    answers = ["", "", "", "", "", "", ""]
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
