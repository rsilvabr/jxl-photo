"""The wizard's bugs of Fable's second sweep (2026-11-22, after v2.31.0-rc5,
AI_tools/261122_Fable_review_full-sweep-2.md), one test each, made from its
reproduction scripts (wizA, wizB). The child scripts are not run: every child
command line the wizard builds is captured (_stream_child). Every folder is
hostile (Japanese, [ ], a space).
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import jxl_photo as wp  # noqa: E402

STATUS = {k: True for k in
          ("cjxl", "djxl", "exiftool", "magick", "tifffile", "pillow", "imagecodecs")}
HOSTILE = "250118_吾妻山公園 [FINAL] x"


@pytest.fixture
def menu(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode", lambda self: True)
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_in_place_replace", lambda self: True)
    cfg = wp.ConfigManager()
    m = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    m._ui = wp.ScriptedUI({})
    return m


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600: (seen.append(list(map(str, cmd))), 0)[1])
    return seen


def _tiff(path: Path, seed=1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    tifffile.imwrite(str(path), rng.integers(0, 65535, (32, 48, 3), dtype=np.uint16),
                     photometric="rgb")
    return path


def _jpeg(path: Path, seed=1) -> Path:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    Image.fromarray(rng.integers(0, 255, (32, 48, 3), dtype=np.uint8)).save(path, quality=90)
    return path


def _wf(origin, dest, conv, entries, **kw):
    wf = {
        "origin_format": origin, "dest_format": dest, "mode": 99,
        "workers": 1, "quality": 95, "bit_depth": 16, "staging": None,
        "dry_run": False, "mode_config": {},
        "conversion_type": conv, "icc_profile": None, "distance": 1.0,
        "effort": 3, "advanced_options": {},
        "manifest_entries": entries, "unattended": False,
        "expert_flags": "",
    }
    wf.update(kw)
    return wf


def _manifest(menu, tmp_path, origin, dest, rows):
    csv = tmp_path / "manifest [test].csv"
    csv.write_text("Source,Destination,Mode,Direction\n"
                   + "".join(f"{s},{d},{m},{direction}\n" for s, d, m, direction in rows),
                   encoding="utf-8-sig")
    row_opts = []
    entries = menu._load_manifest_entries(str(csv), origin, dest, row_options=row_opts)
    return entries, row_opts


# --- D1: the "No" to deleting already-converted originals reaches every row -------

def test_d1_no_to_delete_skipped_in_a_lossy_manifest_reaches_every_row(
        tmp_path, menu, calls):
    """The answer turned delete_skipped off in the run's options only: each row
    had its own copy, made before the question, and the child was sent
    --delete-skipped (Fable's p11: the JPEGs already converted were DELETED
    after "Already-converted originals will be KEPT")."""
    src = tmp_path / "lib" / HOSTILE / "JPEG"
    _jpeg(src / "IMG_0001 (2).jpg")
    menu._ui = wp.ScriptedUI({"delete.lossy_skipped": False})
    wf = _wf("jpeg", "jxl", "convert_lossy", [(str(src), str(src), 1)],
             advanced_options={"delete_source": True, "delete_skipped": True})
    menu._execute_manifest_workflow(wf, {})
    assert any("KEPT" in line for line in menu.ui.output), menu.ui.output
    assert calls, "no child ran"
    assert all("--delete-skipped" not in c for c in calls), calls
    assert any("--delete-source" in c for c in calls), calls


# --- D4: two file rows of one folder with one stem are a collision -----------------

@pytest.mark.parametrize("mode", [0, 1])
def test_d4_two_file_rows_of_one_folder_with_one_stem_are_a_collision(
        tmp_path, menu, calls, mode):
    """`DSC_0001 (2).tif` and `DSC_0001 (2).tiff`, one row each: both write
    `DSC_0001 (2).jxl`, and the collision walk was skipped ("no two entries can
    share an output folder"): the second TIFF was never archived, or it
    replaced the first one's master (Fable's p1, p1b)."""
    lib = tmp_path / "lib" / HOSTILE / "TIFF"
    a = _tiff(lib / "DSC_0001 (2).tif", 1)
    b = _tiff(lib / "DSC_0001 (2).tiff", 2)
    entries, row_opts = _manifest(menu, tmp_path, "tiff", "jxl",
                                  [(a, "", mode, "tiff2jxl"), (b, "", mode, "tiff2jxl")])
    assert menu._manifest_needs_collision_scan(entries, "_EXPORT")
    wf = _wf("tiff", "jxl", "jxl_tiff_encoder", entries, manifest_row_options=row_opts,
             distance=0.0)
    assert menu._execute_manifest_workflow(wf, {}) is False
    assert any("to the same output" in line for line in menu.ui.output), menu.ui.output
    assert calls == []


def _job(menu, answers):
    menu._ui = wp.ScriptedUI(answers)
    menu.new_job_flow(STATUS)
    return menu._ui


def _value(cmd, flag):
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


@pytest.fixture
def children_restored(monkeypatch):
    """Every setting and snapshot _refresh_child_overlays may change in the
    child modules this process has loaded, put back after the test (the
    suite's other tests share those modules)."""
    for module, _label in wp._OVERLAY_SCRIPTS:
        mod = sys.modules.get(module)
        if mod is None:
            continue
        for name in mod._OVERLAY_SETTINGS:
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, getattr(mod, name))
        monkeypatch.setattr(mod, "_SETTINGS_OVERLAY", mod._SETTINGS_OVERLAY)
        rd = getattr(mod, "_RUN_DEFAULTS", None)
        if isinstance(rd, dict):
            for key in list(rd):
                monkeypatch.setitem(rd, key, rd[key])
        if hasattr(mod, "_DECODER_OUTPUT_FOLDERS"):
            monkeypatch.setattr(mod, "_DECODER_OUTPUT_FOLDERS", mod._DECODER_OUTPUT_FOLDERS)


def test_d5_a_setting_changed_in_menu_s_reaches_the_next_job(
        tmp_path, menu, calls, monkeypatch, children_restored):
    """The encoder module the wizard had imported kept the value it read at its
    import; the wizard sent that old value on the command line, where it wins
    over the file (Fable's h_menuS_stale: the overlay said split_all, the job
    was sent --multipage-mode split). #709's test changed the module's
    attribute itself and could not see it."""
    import jxl_tiff_encoder as enc
    overlay = tmp_path / "overlay [S] 設定.json"
    monkeypatch.setenv("JXLPHOTO_SETTINGS", str(overlay))
    root = tmp_path / "lib" / HOSTILE
    (root / "_EXPORT" / "TIFF16").mkdir(parents=True)
    base = {"job.task": "1", "job.folder": str(root), "job.exclude": "-",
            "mode.pick": "3", "job.quality": "3", "review.action": ["r"]}
    _job(menu, base)
    before = _value(calls[-1], "--multipage-mode")
    assert before in (None, enc.MULTIPAGE_TIFF_MODE)
    values = list(enc._OVERLAY_SETTINGS["MULTIPAGE_TIFF_MODE"][1:])
    other = "split_all" if enc.MULTIPAGE_TIFF_MODE != "split_all" else "split"
    keys, _dflt = wp._choice_keys(values)
    menu._ui = wp.ScriptedUI({
        "settings.script": ["1", "0"],
        "settings.pick": [str(list(enc._OVERLAY_SETTINGS).index("MULTIPAGE_TIFF_MODE") + 1), ""],
        "settings.choice": keys[values.index(other)],
    })
    menu.script_settings_flow()
    assert json.loads(overlay.read_text(encoding="utf-8")) == {
        "jxl_tiff_encoder": {"MULTIPAGE_TIFF_MODE": other}}
    _job(menu, base)
    assert _value(calls[-1], "--multipage-mode") in (None, other), calls[-1]
    assert enc.MULTIPAGE_TIFF_MODE == other
    # Back to the script's value: the module follows the file again.
    script_value = wp._script_setting_literals("jxl_tiff_encoder")["MULTIPAGE_TIFF_MODE"][0]
    menu._ui = wp.ScriptedUI({
        "settings.script": ["1", "0"],
        "settings.pick": [str(list(enc._OVERLAY_SETTINGS).index("MULTIPAGE_TIFF_MODE") + 1), ""],
        "settings.choice": "0",
    })
    menu.script_settings_flow()
    assert enc.MULTIPAGE_TIFF_MODE == script_value


def _lib(tmp_path) -> Path:
    """A library with one file of each kind (never read: the children do not run)."""
    root = tmp_path / "lib" / HOSTILE
    (root / "_EXPORT" / "TIFF16").mkdir(parents=True)
    (root / "_EXPORT" / "TIFF16" / "DSC_0001 (2).tif").write_bytes(b"II*\0" + b"\0" * 16)
    (root / "_EXPORT" / "16B_JXL").mkdir(parents=True)
    (root / "_EXPORT" / "16B_JXL" / "DSC_0001 (2).jxl").write_bytes(b"\xff\x0a" + b"\0" * 16)
    (root / "a (1).jxl").write_bytes(b"\xff\x0a" + b"\0" * 16)
    return root


_MENU9_ENTER = {"settings.staging": None, "settings.workers": None, "settings.quality": None,
                "settings.effort": None, "settings.distance": None,
                "settings.confirm_delete": None, "settings.marker": None,
                "settings.thumbnail_tip": None, "settings.update_check": None}


def _menu9(menu, **answers):
    menu._ui = wp.ScriptedUI(dict(_MENU9_ENTER, **answers))
    menu.edit_settings()


def _replay(menu, session, answers=None, folder=None):
    menu._ui = wp.ScriptedUI({"session.folder": folder, "session.overwrite": "2",
                              "session.dry_run": False, "session.proceed": True,
                              "delete.lossy_skipped": [True, True]})
    return menu._run_saved_session(session, STATUS, answers=answers)


# --- D7: an auto JPEG export is not the memory of "Get the original JPEGs back" ----

def test_d7_an_auto_export_is_no_task_s_memory(menu):
    """jxl_to_jpeg_auto is offered by both JPEG tasks; _task_of reads it as
    "recover_jpeg", and the first recovery took the export's settings (its
    JPG_web folder, quality 70, the forced decode: Fable's g) — with a config
    of rc5 too, until a recovery had run once."""
    c = menu.config.config
    c.last_origin_format, c.last_dest_format = "jxl", "jpeg"
    c.last_conversion_type = "jxl_to_jpeg_auto"
    c.last_quality = 70
    c.last_mode_config = {"export_jpeg_folder": "JPG_web"}
    for memory in ({}, {"export_jpeg": {"last_quality": 70}}):      # before rc5, rc5
        c.task_memory = memory
        seen = menu._task_memory("recover_jpeg")
        assert seen is not c
        assert seen.last_quality is None and not seen.last_mode_config


def test_d7_a_config_of_before_rc5_still_gives_its_last_job_to_its_task(menu):
    c = menu.config.config
    c.task_memory = {}
    c.last_origin_format, c.last_dest_format = "jxl", "jpeg"
    c.last_conversion_type = "jxl_to_jpeg_lossless"
    c.last_workers = 5
    assert menu._task_memory("recover_jpeg") is c


# --- D8: menu 9's TIFF distance is not another task's ------------------------------

def test_d8_menu_9_s_tiff_distance_leaves_another_task_s_job_alone(tmp_path, menu, calls):
    """Fable's a: a JXL -> JXL copies job, then 'Distance for TIFF -> JXL' set
    to 0.05 in menu 9: "Repeat last job" ran the copies at d=0.05."""
    root = _lib(tmp_path)
    _job(menu, {"job.task": "3", "job.folder": str(root), "mode.pick": "6",
                "mode.cfg.marker": None, "mode.cfg.jxl_folder": "sRGB_JXL",
                "job.quality": None, "review.action": ["r"]})
    d0 = _value(calls[-1], "--distance")
    _replay(menu, menu.config.session_snapshot())
    assert _value(calls[-1], "--distance") == d0
    _menu9(menu, **{"settings.distance": "0.05"})
    _replay(menu, menu.config.session_snapshot())
    assert "jxl_recompressor" in calls[-1][1]
    assert _value(calls[-1], "--distance") == d0 != "0.05"


def test_d8_menu_9_s_tiff_distance_is_a_tiff_job_s_next_value(tmp_path, menu, calls):
    root = _lib(tmp_path)
    _job(menu, {"job.task": "1", "job.folder": str(root), "job.exclude": "-",
                "mode.pick": "3", "job.quality": "3", "review.action": ["r"]})
    _menu9(menu, **{"settings.distance": "0.7"})
    _replay(menu, menu.config.session_snapshot())
    assert "jxl_tiff_encoder" in calls[-1][1]
    assert _value(calls[-1], "--distance") == "0.7"


# --- D9: menu 9's staging folder is the next job's ---------------------------------

def test_d9_menu_9_s_staging_folder_reaches_the_next_job(tmp_path, menu, calls):
    """Fable's e (E): once a job had run, its staging folder won over menu 9's
    — a new one, and 'none' too."""
    root = _lib(tmp_path)
    s1 = tmp_path / "staging old [SSD] 一"
    s2 = tmp_path / "staging new [NVMe] 二"
    s1.mkdir()
    s2.mkdir()
    base = {"job.task": "1", "job.folder": str(root), "job.exclude": "-",
            "mode.pick": "3", "job.quality": "3", "review.action": ["r"]}
    _menu9(menu, **{"settings.staging": str(s1)})
    _job(menu, base)
    assert _value(calls[-1], "--staging") == str(s1)
    _menu9(menu, **{"settings.staging": str(s2)})
    _job(menu, base)
    assert _value(calls[-1], "--staging") == str(s2)
    _menu9(menu, **{"settings.staging": "none"})
    _job(menu, base)
    assert _value(calls[-1], "--staging") not in (str(s1), str(s2))


# --- D10: a preset of a mode-6/7 job keeps the marker it ran with ------------------

def test_d10_a_preset_keeps_the_marker_its_job_ran_with(tmp_path, menu, calls):
    """Fable's e (F): saved with the wrapper's marker (Enter), the preset ran
    with the marker menu 9 named on the day of the replay."""
    root = _lib(tmp_path)
    (root / "OUT" / "TIFF16").mkdir(parents=True)
    marker = menu.config.config.export_marker
    _job(menu, {"job.task": "1", "job.folder": str(root), "job.exclude": "-",
                "mode.pick": "6", "mode.cfg.marker": None, "mode.cfg.jxl_folder": None,
                "job.quality": "3", "review.action": ["s", "r"],
                "review.preset_name": "NIGHTLY"})
    ran = _value(calls[-1], "--export-marker") or marker
    _menu9(menu, **{"settings.marker": "OUT"})
    _replay(menu, wp._preset_session(menu.config.config.presets["NIGHTLY"]),
            answers={"overwrite": False, "dry_run": False})
    assert (_value(calls[-1], "--export-marker") or "OUT") == ran
    _replay(menu, menu.config.session_snapshot())
    assert (_value(calls[-1], "--export-marker") or "OUT") == ran


# --- D11: "Repeat last job" of a mode-2 job keeps (and shows) its output folder ----

def test_d11_the_same_folder_typed_with_a_backslash_keeps_the_output_folder(
        tmp_path, menu, calls, capsys):
    """Fable's l: the panel never showed the output folder, and the same input
    folder typed with a '\\' at the end sent the outputs to <parent>\\output."""
    root = _lib(tmp_path)
    dest = tmp_path / "出力 [JPEG] chosen dest"
    _job(menu, {"job.task": "4", "job.folder": str(root), "mode.pick": "2",
                "mode.cfg.output_dir": str(dest), "job.jpeg_quality": None,
                "review.action": ["r"]})
    assert calls[-1][3] == str(dest)
    _replay(menu, menu.config.session_snapshot(), folder=str(root) + "\\")
    assert calls[-1][3] == str(dest), calls[-1]


def test_d11_the_replay_shows_the_output_folder_of_a_mode_2_job(
        tmp_path, menu, calls, capsys):
    root = _lib(tmp_path)
    dest = tmp_path / "出力 [JPEG] chosen dest"
    _job(menu, {"job.task": "4", "job.folder": str(root), "mode.pick": "2",
                "mode.cfg.output_dir": str(dest), "job.jpeg_quality": None,
                "review.action": ["r"]})
    capsys.readouterr()
    _replay(menu, menu.config.session_snapshot())
    assert calls[-1][3] == str(dest)
    out = capsys.readouterr().out
    assert f"Output folder: {dest}" in out, out


# --- D13: a mode-2 Destination inside its Source replaces files in place ----------

def _real_jxl(path: Path, seed: int) -> Path:
    import shutil
    import subprocess
    from PIL import Image
    if not shutil.which("cjxl"):
        pytest.skip("needs cjxl")
    path.parent.mkdir(parents=True, exist_ok=True)
    png = path.with_suffix(".png")
    rng = np.random.default_rng(seed)
    Image.fromarray(rng.integers(0, 255, (32, 48, 3), dtype=np.uint8)).save(png)
    subprocess.run(["cjxl", str(png), str(path), "-d", "0", "-e", "1"],
                   capture_output=True, check=True)
    png.unlink()
    return path


def test_d13_a_mode_2_destination_inside_its_source_is_an_in_place_row(
        tmp_path, menu, calls, monkeypatch):
    """The recompressor's flat recursive run puts the JXLs already in the
    Destination back on themselves: its own HHMM came up mid-manifest, and with
    no stdin the row was "Cancelled" while the run said it went well (Fable's p7)."""
    src = tmp_path / "lib" / HOSTILE
    out = src / "out [web]"
    _real_jxl(src / "DSC_0001 (2).jxl", 1)
    _real_jxl(out / "DSC_0002 (2).jxl", 2)
    assert wp._recompress_entry_in_place(str(src), str(out), 2)
    asked = []
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_in_place_replace",
                        lambda self: (asked.append(1), True)[1])
    wf = _wf("jxl", "jxl", "jxl_recompress", [(str(src), str(out), 2)], distance=1.0)
    assert menu._execute_manifest_workflow(wf, {}) is True
    assert asked, "the wizard's own in-place confirmation was not asked"
    assert calls and "--delete-confirm-off" in calls[-1], calls
    # Unattended: refused before anything runs (no one can type the token).
    calls.clear()
    wf = _wf("jxl", "jxl", "jxl_recompress", [(str(src), str(out), 2)], distance=1.0,
             unattended=True)
    assert menu._execute_manifest_workflow(wf, {}) is False
    assert calls == []


def _mode2_inside(tmp_path):
    src = tmp_path / "lib" / HOSTILE
    out = src / "web [copies]"
    _real_jxl(src / "DSC_0001 (2).jxl", 1)
    _real_jxl(out / "DSC_0002 (2).jxl", 2)
    return src, out


def test_m1_a_direct_mode_2_run_inside_its_input_asks_the_in_place_confirmation(
        tmp_path, menu, calls, monkeypatch):
    """The review of rc6, M1: D13's in-place test reached the DIRECT run's
    command (--delete-confirm-off) but not its confirmation, keyed on modes
    0/8: the recompressor replaced the output folder's JXLs in place and
    nobody was asked (rc5: the child asked its own HHMM)."""
    src, out = _mode2_inside(tmp_path)
    asked = []
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_in_place_replace",
                        lambda self: (asked.append(1), False)[1])      # declined
    wf = _wf("jxl", "jxl", "jxl_recompress", [], mode=2, input_dir=str(src),
             mode_config={"output_dir": str(out)})
    assert menu.execute_workflow(wf, STATUS) is False
    assert asked == [1] and calls == []
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_in_place_replace",
                        lambda self: (asked.append(2), True)[1])       # typed
    assert menu.execute_workflow(dict(wf, advanced_options={}), STATUS) is True
    assert asked == [1, 2] and calls and "--delete-confirm-off" in calls[-1]


def test_m1_an_unattended_preset_of_such_a_run_is_refused(tmp_path, menu, calls):
    src, out = _mode2_inside(tmp_path)
    session = {"last_input_dir": str(src), "last_output_mode": "2",
               "last_origin_format": "jxl", "last_dest_format": "jxl",
               "last_conversion_type": "jxl_recompress", "last_workers": 1,
               "last_mode_config": {"output_dir": str(out)}}
    assert menu._run_saved_session(session, STATUS,
                                   answers={"overwrite": False, "dry_run": False}) is False
    assert calls == []
    assert any("replaces the source JXLs in place" in line for line in menu.ui.output)


def test_m1_a_mode_2_run_elsewhere_asks_nothing(tmp_path, menu, calls, monkeypatch):
    src, out = _mode2_inside(tmp_path)
    elsewhere = tmp_path / "copies [web] 別"
    asked = []
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_in_place_replace",
                        lambda self: (asked.append(1), True)[1])
    wf = _wf("jxl", "jxl", "jxl_recompress", [], mode=2, input_dir=str(src),
             mode_config={"output_dir": str(elsewhere)})
    assert menu.execute_workflow(wf, STATUS) is True
    assert asked == [] and calls and "--delete-confirm-off" not in calls[-1]


def test_review2_s1_a_row_filled_by_an_earlier_row_gets_no_confirm_off(
        tmp_path, menu, monkeypatch):
    """rc6's second review, S1: row 1 writes into row 2's Destination (inside
    row 2's Source). Up front that folder was empty — no in-place confirmation;
    row 2's command, built after row 1 ran, read the disk again and got
    --delete-confirm-off: the recompressor replaced row 1's output in place
    with nobody asked. The stand-in child writes row 1's output, as the real
    one does."""
    import shutil
    lib = tmp_path / "lib" / HOSTILE
    other = tmp_path / "other shoot [B] 別"
    web = lib / "web2 [chain]"
    _real_jxl(lib / "DSC_0001 (2).jxl", 1)
    made = _real_jxl(other / "DSC_0003 (2).jxl", 3)
    calls = []

    def child(self, cmd, idle_timeout=3600):
        calls.append(list(map(str, cmd)))
        if str(other) in calls[-1]:                       # row 1: its output appears
            web.mkdir(parents=True, exist_ok=True)
            shutil.copy2(made, web / made.name)
        return 0

    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child", child)
    asked = []
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_in_place_replace",
                        lambda self: (asked.append(1), True)[1])
    menu._ui = wp.ScriptedUI({"manifest.run_anyway_cross": True})
    wf = _wf("jxl", "jxl", "jxl_recompress",
             [(str(other), str(web), 2), (str(lib), str(web), 2)], distance=1.0)
    menu._execute_manifest_workflow(wf, {})
    assert len(calls) == 2, calls
    assert asked or "--delete-confirm-off" not in calls[1], (asked, calls[1])


def test_review2_n1_a_derivative_into_a_folder_of_its_input_holding_jxls_is_refused_first(
        tmp_path, menu, calls, monkeypatch):
    """rc6's second review, N1: M1's wider in-place test asked the in-place
    HHMM for a mode-2 derivative (or rename) whose output folder inside the
    input holds JXLs, then the recompressor refused it (exit 2)."""
    src, out = _mode2_inside(tmp_path)
    asked = []
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_in_place_replace",
                        lambda self: (asked.append(1), True)[1])
    for adv in ({"output_icc": "sRGB"}, {"rename_from": "DSC", "rename_to": "IMG"}):
        wf = _wf("jxl", "jxl", "jxl_recompress", [], mode=2, input_dir=str(src),
                 mode_config={"output_dir": str(out)}, advanced_options=adv)
        assert menu.execute_workflow(wf, STATUS) is False, adv
    assert asked == [] and calls == []
    assert wp._derivative_in_place_rows([(str(src), str(out), 2)]) == [(str(src), str(out), 2)]


def test_review2_n2_a_lone_delete_skipped_in_the_flags_conflicts_with_nothing(tmp_path):
    """rc6's second review, N2: --exiftool-ignore-minor --delete-skipped, no
    delete: the decoder accepts it (--delete-skipped alone is inert)."""
    wf = _wf("jxl", "tiff", "jxl_tiff_decoder", [], mode=3, input_dir=str(tmp_path),
             expert_flags="--exiftool-ignore-minor --delete-skipped")
    assert wp._registry_rule_problem(wf) is None
    wf["expert_flags"] += " --delete-source"
    assert wp._registry_rule_problem(wf)


@pytest.mark.parametrize("origin,dest,conv,flag", [
    ("tiff", "jxl", "jxl_tiff_encoder", "--restamp-moved"),
    ("jxl", "jxl", "jxl_recompress", "--refresh-explorer-thumbnails"),
], ids=["restamp-moved", "refresh-thumbnails"])
def test_review2_n3_a_maintenance_flag_next_to_a_delete_is_refused_first(
        tmp_path, menu, calls, hhmm, origin, dest, conv, flag):
    """rc6's second review, N3: refused by the script after the HHMM."""
    root = _lib(tmp_path)
    wf = _wf(origin, dest, conv, [], mode=3, input_dir=str(root),
             advanced_options={"delete_source": True}, expert_flags=flag)
    assert menu.execute_workflow(wf, STATUS) is False
    assert calls == [] and hhmm == []


@pytest.mark.skipif(os.name != "nt", reason="a Windows junction")
def test_review2_n5_an_output_folder_reached_through_a_junction_is_inside(tmp_path):
    """rc6's second review, N5: compared as text, a junction to the output
    folder was 'elsewhere' (the recompressor's samefile saw it inside and
    asked its own HHMM behind the wizard's pipe)."""
    import subprocess
    src, out = _mode2_inside(tmp_path)
    link = tmp_path / "link [j] 接続"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(out)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"mklink /J failed: {r.stdout} {r.stderr}")
    assert wp._recompress_entry_in_place(str(src), str(link), 2)


def test_d13_no_in_place_when_the_destination_holds_no_jxl_or_is_elsewhere(tmp_path):
    src = tmp_path / "lib" / HOSTILE
    out = src / "out [web]"
    _real_jxl(src / "DSC_0001 (2).jxl", 1)
    out.mkdir()
    assert not wp._recompress_entry_in_place(str(src), str(out), 2)
    elsewhere = tmp_path / "copies [web]"
    _real_jxl(elsewhere / "DSC_0002 (2).jxl", 2)
    assert not wp._recompress_entry_in_place(str(src), str(elsewhere), 2)


def test_d13_a_cancelled_entry_of_an_unattended_manifest_is_not_a_success(
        tmp_path, menu, monkeypatch):
    """A child's own question with no stdin ends it with exit 3: the scheduled
    run must not exit 0 as if the row had been done."""
    src = tmp_path / "lib" / HOSTILE / "JPEG"
    _jpeg(src / "IMG_0001 (2).jpg")
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600: 3)
    for unattended, want in ((True, False), (False, True)):
        wf = _wf("jpeg", "jxl", "transcode_lossless", [(str(src), str(src), 1)],
                 unattended=unattended)
        assert menu._execute_manifest_workflow(wf, {}) is want, unattended


# --- D14: a missing Source is its row's error, not the manifest's -----------------

def test_d14_a_missing_source_skips_its_row_and_runs_the_others(
        tmp_path, menu, monkeypatch):
    """A drive not plugged in: the child refused the row as a usage error
    (exit 2), the manifest stopped every other row and called it "a WRAPPER
    bug" (Fable's p6). The owner's call: that row is an error, the others run."""
    lib = tmp_path / "lib" / HOSTILE / "2025 [ok]"
    _tiff(lib / "DSC_0001 (2).tif")
    missing = tmp_path / "unplugged [Q] 外付け" / "2026"
    calls = []

    def child(self, cmd, idle_timeout=3600):        # what the real child answers
        calls.append(list(map(str, cmd)))
        if str(missing) in calls[-1]:
            self._last_child_usage_error = "the input folder does not exist"
            return 2
        return 0

    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child", child)
    entries, row_opts = _manifest(menu, tmp_path, "tiff", "jxl",
                                  [(missing, "", 3, "tiff2jxl"), (lib, "", 3, "tiff2jxl")])
    wf = _wf("tiff", "jxl", "jxl_tiff_encoder", entries, manifest_row_options=row_opts,
             distance=0.0)
    assert menu._execute_manifest_workflow(wf, {}) is False      # an error at the end
    assert len(calls) == 1 and str(lib) in calls[0], calls       # the other row ran
    shown = " ".join(menu.ui.output)
    assert "Source not found" in shown and "WRAPPER bug" not in shown, shown


# --- D16: a CSV the csv module cannot read is a message, not a traceback -----------

def test_d16_a_cell_over_the_csv_limit_is_refused_with_a_message(tmp_path, menu):
    csv = tmp_path / "manifest [huge].csv"
    csv.write_text("Source,Destination,Mode,Direction\n"
                   f"{'x' * 140_000},,3,tiff2jxl\n", encoding="utf-8-sig")
    assert menu._load_manifest_entries(str(csv), "tiff", "jxl") is None
    assert any("cannot be read as CSV" in line for line in menu.ui.output), menu.ui.output
    menu._view_manifest(str(csv), tmp_path)                       # no traceback either


def test_d16_a_csv_saved_with_semicolons_names_the_separator(tmp_path, menu):
    """Excel in some languages saves CSV with ';': every row became ONE cell,
    every Source a path that does not exist (Fable's p5)."""
    lib = tmp_path / "lib" / HOSTILE
    _tiff(lib / "DSC_0001 (2).tif")
    csv = tmp_path / "manifest [excel].csv"
    csv.write_text(f"Source;Destination;Mode;Direction\n{lib};;3;tiff2jxl\n",
                   encoding="utf-8-sig")
    assert menu._load_manifest_entries(str(csv), "tiff", "jxl") is None
    assert any("separates its columns with ';'" in line for line in menu.ui.output)


# --- D15: a delete typed in the expert flags counts like [D] before the HHMM -------

def _row_no(ui, label):
    return next(ln.split()[0] for ln in ui.output
                if label in ln and ln.strip()[:2].strip().isdigit())


@pytest.fixture
def hhmm(monkeypatch):
    asked = []
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode",
                        lambda self: (asked.append("archive"), True)[1])
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_in_place_replace",
                        lambda self: (asked.append("in place"), True)[1])
    return asked


def test_d15_the_task_s_guard_off_does_not_reach_a_job_that_deletes_from_its_flags(
        tmp_path, menu, calls, hhmm):
    """Fable's b: the guard 'off' the task remembered stayed on a job whose
    delete was typed in [x]: the HHMM was asked, then every script refused the
    pair (exit 2). And "Repeat last job" did the same, without #707's note."""
    root = _lib(tmp_path)
    base = {"job.task": "1", "job.folder": str(root), "job.exclude": "-",
            "mode.pick": "3", "job.quality": "3"}
    ui = _job(menu, dict(base, **{"review.action": ["q"]}))
    n = _row_no(ui, "Overwrite guard")
    _job(menu, dict(base, **{"review.action": [n, "r"], "review.overwrite_guard": False}))
    assert "--no-overwrite-guard" in calls[-1]
    ui = _job(menu, dict(base, **{"review.action": ["x", "r"],
                                  "review.expert": "--delete-source"}))
    assert "--delete-source" in calls[-1] and "--no-overwrite-guard" not in calls[-1]
    assert any("guard stays on" in line for line in ui.output), ui.output
    assert any("--delete-source in the expert flags" in line for line in ui.output)
    _replay(menu, menu.config.session_snapshot())
    assert "--delete-source" in calls[-1] and "--no-overwrite-guard" not in calls[-1]


def test_d15_a_conflict_with_a_delete_typed_in_the_flags_is_refused_before_the_hhmm(
        tmp_path, menu, calls, hhmm):
    """Fable's b (variant): --restamp-then-sync on the [a] screen and
    --delete-source in [x]: the HHMM was asked, then the encoder exited 2."""
    root = _lib(tmp_path)
    base = {"job.task": "1", "job.folder": str(root), "job.exclude": "-",
            "mode.pick": "3", "job.quality": "3"}
    ui = _job(menu, dict(base, **{"review.action": ["a", "q"], "opt.screen": [""]}))
    rr = next(ln.split()[0] for ln in ui.output if "--restamp-then-sync" in ln
              and ln.strip()[:2].strip().isdigit())
    n = len(calls)
    _job(menu, dict(base, **{"review.action": ["a", "x", "r"], "opt.screen": [rr, ""],
                             "review.expert": "--delete-source"}))
    assert len(calls) == n and hhmm == [], (calls[n:], hhmm)


def test_d15_no_overwrite_guard_typed_next_to_a_delete_is_refused_before_the_hhmm(
        tmp_path, menu, calls, hhmm):
    root = _lib(tmp_path)
    wf = _wf("tiff", "jxl", "jxl_tiff_encoder", [], mode=3, input_dir=str(root),
             expert_flags="--delete-source --no-overwrite-guard")
    assert menu.execute_workflow(wf, STATUS) is False
    assert calls == [] and hhmm == []


@pytest.mark.parametrize("flags", ["--resize-long 2048", "--output-icc sRGB"])
def test_d15_a_derivative_typed_in_the_flags_is_refused_before_the_hhmm(
        tmp_path, menu, calls, hhmm, flags):
    """Fable's b2: a derivative in [x] of a JXL -> JXL job that deletes ([D])
    or replaces in place (mode 0): the HHMM, then the recompressor's exit 2."""
    root = _lib(tmp_path)
    for mode, adv in ((3, {"delete_source": True}), (0, {})):
        wf = _wf("jxl", "jxl", "jxl_recompress", [], mode=mode, input_dir=str(root),
                 advanced_options=dict(adv), expert_flags=flags)
        assert menu.execute_workflow(wf, STATUS) is False, (mode, flags)
    assert calls == [] and hhmm == []


_S1_CASES = [
    ("tiff", "jxl", "jxl_tiff_encoder", "--restamp-then-sync"),
    ("jxl", "tiff", "jxl_tiff_decoder", "--exiftool-ignore-minor"),
    ("jxl", "tiff", "jxl_tiff_decoder", "--none"),
    ("jpeg", "jxl", "convert_lossy", "--icc-profile sRGB"),
    ("jpeg", "jxl", "convert_lossy", "--resize-long 16"),
]


@pytest.mark.parametrize("delete_from", ["D", "flags"])
@pytest.mark.parametrize("origin,dest,conv,flags", _S1_CASES,
                         ids=[c[3].split()[0] for c in _S1_CASES])
def test_s1_an_option_typed_in_the_flags_that_refuses_a_delete_is_refused_first(
        tmp_path, menu, calls, hhmm, origin, dest, conv, flags, delete_from):
    """The review of rc6, S1: such an option typed in [x] next to a delete
    (from [D] or from the flags) reached the child, which refused it with
    exit 2 after the HHMM — the docs said it was refused before."""
    root = _lib(tmp_path)
    adv = {"delete_source": True} if delete_from == "D" else {}
    extra = "" if delete_from == "D" else "--delete-source "
    wf = _wf(origin, dest, conv, [], mode=3, input_dir=str(root),
             advanced_options=adv, expert_flags=extra + flags)
    assert menu.execute_workflow(wf, STATUS) is False
    assert calls == [] and hhmm == [], (calls, hhmm)


def test_n3_the_bit_exact_recovery_keeps_its_delete_next_to_an_ignored_profile(
        tmp_path, menu, calls, hhmm):
    """The review of rc6, N3: the transcoder's bit-exact route ignores
    --icc-profile and accepts the delete; the wizard refused it."""
    root = _lib(tmp_path)
    wf = _wf("jxl", "jpeg", "jxl_to_jpeg_lossless", [], mode=3, input_dir=str(root),
             advanced_options={"delete_source": True}, expert_flags="--icc-profile sRGB")
    assert menu.execute_workflow(wf, STATUS) is True
    assert calls and "--delete-source" in calls[-1]


@pytest.mark.parametrize("variant", ["lossy", "icc"])
def test_d15_a_flags_delete_on_rows_that_never_delete_is_refused_before_the_hhmm(
        tmp_path, menu, calls, hhmm, variant):
    """Fable's p10: a JXL -> JPEG manifest with --delete-source in the expert
    flags: the HHMM was charged, then the first child refused the pair and the
    manifest called it a wrapper bug. The direct run refused it before."""
    src = tmp_path / "lib" / HOSTILE
    _real_jxl(src / "DSC_0001 (2).jxl", 1)
    out = tmp_path / "lib" / "out [x]"
    row_opts = [{"output_icc": "sRGB"}] if variant == "icc" else None
    wf = _wf("jxl", "jpeg", "jxl_to_jpeg_force", [(str(src), str(out), 2)],
             expert_flags="--delete-source",
             **({"manifest_row_options": row_opts} if row_opts else {}))
    assert menu._execute_manifest_workflow(wf, {}) is False
    assert calls == [] and hhmm == []
    shown = " ".join(menu.ui.output)
    assert "WRAPPER bug" not in shown and "expert flags" in shown, shown


def test_d15_a_manifest_whose_rows_all_lose_their_delete_asks_no_hhmm(
        tmp_path, menu, calls, hhmm):
    """[D] on a manifest of lossy JPEG exports only: every row's delete is
    taken out (they never delete), and the 'will be DELETED' HHMM was asked."""
    src = tmp_path / "lib" / HOSTILE
    _real_jxl(src / "DSC_0001 (2).jxl", 1)
    out = tmp_path / "lib" / "out [x]"
    wf = _wf("jxl", "jpeg", "jxl_to_jpeg_force", [(str(src), str(out), 2)],
             advanced_options={"delete_source": True})
    assert menu._execute_manifest_workflow(wf, {}) is True
    assert hhmm == [], hhmm
    assert calls and all("--delete-source" not in c for c in calls)


# --- The small ones (section 1.4 of the report) ------------------------------------

@pytest.mark.parametrize("typed", ["nan", "inf", "-inf"])
def test_a_typed_nan_is_not_a_number(menu, typed):
    """'nan' clamped to the lower bound: a distance of 0, lossless (Fable's m)."""
    menu._ui = wp.ScriptedUI({"q": typed})
    assert menu._ask_number("q", "Distance", 0.1, 0.0, 25.0) == 0.1
    assert any("Not a number" in line for line in menu.ui.output)


def test_menu_9_no_longer_asks_a_delete_confirmation_nothing_reads(menu):
    menu._ui = wp.ScriptedUI(dict(_MENU9_ENTER))
    menu.edit_settings()
    assert "settings.confirm_delete" not in menu._ui.asked, menu._ui.asked


def test_s3_menu_s_remakes_the_other_tools_folder_names(
        tmp_path, menu, monkeypatch, children_restored):
    """The review of rc6, S3: the decoder's list of the other tools' output
    folders (#565) is read once; after menu S renamed the recompressor's
    EXPORT_JXL_FOLDER, the wizard's in-process decoder still skipped only the
    old name, a fresh decoder process the new one."""
    import jxl_recompressor as rec
    import jxl_tiff_decoder as dec
    overlay = tmp_path / "overlay [S3] 設定.json"
    monkeypatch.setenv("JXLPHOTO_SETTINGS", str(overlay))
    assert "web_small" not in dec._other_tool_output_folders()[0]      # cached now
    names = list(rec._OVERLAY_SETTINGS)
    menu._ui = wp.ScriptedUI({
        "settings.script": ["4", "0"],
        "settings.pick": [str(names.index("EXPORT_JXL_FOLDER") + 1), ""],
        "settings.value": "WEB_small",
    })
    menu.script_settings_flow()
    assert rec.EXPORT_JXL_FOLDER == "WEB_small"
    assert "web_small" in dec._other_tool_output_folders()[0]


def test_d4_a_file_row_whose_output_is_another_row_s_source_is_seen(tmp_path, menu):
    """Row 1 a FILE (mode 1 writes <its folder>/recompressed_jxl), row 2 that
    folder: the chain warning was never computed (Fable's p1c)."""
    lib = tmp_path / "lib" / HOSTILE
    lib.mkdir(parents=True)
    (lib / "DSC_0001 (2).jxl").write_bytes(b"\xff\x0a")
    out_dir = lib / "recompressed_jxl"
    out_dir.mkdir()
    (out_dir / "old.jxl").write_bytes(b"\xff\x0a")
    entries = [(str(lib / "DSC_0001 (2).jxl"), str(lib / "DSC_0001 (2).jxl"), 1),
               (str(out_dir), str(out_dir), 1)]
    assert menu._manifest_needs_collision_scan(entries, "_EXPORT")
