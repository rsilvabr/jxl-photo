#!/usr/bin/env python3
"""Phase 4 of the wrapper redesign: the script settings overlay.

Each script reads, right after its settings block, its section of a JSON file
(JXLPHOTO_SETTINGS; else .jxl_script_settings.json next to the scripts; else in
the home folder) and takes only the names of its own _OVERLAY_SETTINGS table,
each checked against its type and range. The safety settings never come from
it. A value it takes is the script's setting for the run (the per-run reset
restores it), and every run logs what it took. The wrapper's menu S writes the
file.

Every run here is a real child Python with the overlay in a hostile folder
(Japanese, [ ], a space, %). The suite itself runs with JXLPHOTO_SETTINGS=""
(conftest), so a real overlay on this PC never reaches it.

Pre-fix proof: JXLPHOTO_SCRIPTS_UNDER_TEST=<the five scripts of the base
commit> — no script there reads an overlay.
"""

import ast
import json
import re
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)
MODULES = ("jxl_tiff_encoder", "jxl_tiff_decoder", "jxl_jpeg_transcoder",
           "jxl_recompressor")
HOSTILE = "設定 [x] 5% (2)"


def _overlay(tmp_path, data) -> Path:
    path = tmp_path / HOSTILE / ".jxl_script_settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _child(code: str, overlay, tmp_path, extra_env=None):
    """Run `code` in a fresh Python next to the scripts; its last stdout line
    is JSON."""
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"),
           "PYTHONIOENCODING": "utf-8"}
    if overlay is None:
        env.pop("JXLPHOTO_SETTINGS", None)
    else:
        env["JXLPHOTO_SETTINGS"] = str(overlay)
    env.update(extra_env or {})
    r = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(SCRIPTS)!r})\n"
                        + code], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, cwd=str(tmp_path), timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def _values(module, names, overlay, tmp_path, extra_env=None):
    code = (f"import json, {module} as m\n"
            f"print(json.dumps({{'v': {{n: (list(getattr(m, n)) if isinstance(getattr(m, n), tuple) "
            f"else getattr(m, n)) for n in {list(names)!r}}}, "
            f"'o': {{k: (str(v) if k == 'path' else v) for k, v in m._SETTINGS_OVERLAY.items()}}}}))")
    return _child(code, overlay, tmp_path, extra_env)


# ---------------------------------------------------------------- the tables

@pytest.fixture(scope="module")
def mods():
    old = os.environ.get("JXLPHOTO_SETTINGS")
    os.environ["JXLPHOTO_SETTINGS"] = ""
    sys.path.insert(0, str(SCRIPTS))
    try:
        import importlib
        return {m: importlib.import_module(m) for m in MODULES}
    finally:
        sys.path.remove(str(SCRIPTS))
        if old is None:
            os.environ.pop("JXLPHOTO_SETTINGS", None)
        else:
            os.environ["JXLPHOTO_SETTINGS"] = old


@pytest.mark.parametrize("module", MODULES)
def test_each_table_accepts_the_scripts_own_values(module, mods):
    m = mods[module]
    for name, spec in m._OVERLAY_SETTINGS.items():
        value = getattr(m, name)
        ok, why = m._overlay_value(spec, list(value) if isinstance(value, tuple) else value)
        assert ok, f"{module}.{name} = {value!r} fails its own spec {spec}: {why}"


def test_safety_settings_are_the_same_everywhere_and_never_offered(mods):
    first = mods[MODULES[0]]._OVERLAY_SAFETY_SETTINGS
    for module, m in mods.items():
        assert m._OVERLAY_SAFETY_SETTINGS == first, module
        assert not (set(m._OVERLAY_SETTINGS) & first), (module, set(m._OVERLAY_SETTINGS) & first)
        # The safety names this script has are real settings of it.
        for name in ("DELETE_SOURCE", "DELETE_CONFIRM", "OVERWRITE_GUARD"):
            assert hasattr(m, name), (module, name)


def _parser_choices(module):
    tree = ast.parse((SCRIPTS / f"{module}.py").read_text(encoding="utf-8"))
    out = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            for kw in node.keywords:
                if kw.arg == "choices":
                    try:
                        values = set(ast.literal_eval(kw.value))
                    except Exception:
                        continue
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and str(arg.value).startswith("--"):
                            out[arg.value] = values
    return out


# setting -> the flag that sets it per run (where one exists)
FLAG_OF = {
    "ENCODE_TAG_MODE": "--encode-tag", "ICC_PNG_STRATEGY": "--icc-png-strategy",
    "D50_PATCH_MODE": "--d50-patch", "MULTIPAGE_TIFF_MODE": "--multipage-mode",
    "THUMBNAIL_MODE": "--thumbnail-mode", "SCAN_SCOPE": "--scan-scope",
    "EXPLORER_THUMBNAIL": "--explorer-thumbnail", "CJXL_BUFFERING": "--buffering",
    "THUMBNAIL_HANDLING": "--thumbnail-handling", "DEPTH_POLICY": "--depth-policy",
    "DJXL_OUTPUT_DEPTH": "--depth", "TIFF_COMPRESSION": "--compression",
    "ON_DOWNGRADE": "--on-downgrade", "ON_REGENERATION": "--on-regeneration",
    "ON_UNKNOWN": "--on-unknown", "ON_SCAN": "--on-scan", "JBRD_POLICY": "--jbrd-policy",
    "PNG_DEFAULT_BIT_DEPTH": "--bit-depth", "JPEG_ORIENTATION": "--jpeg-orientation",
}


@pytest.mark.parametrize("module", MODULES)
def test_choices_are_what_the_flag_takes(module, mods):
    flags = _parser_choices(module)
    for name, spec in mods[module]._OVERLAY_SETTINGS.items():
        if spec[0] != "choice" or name not in FLAG_OF or FLAG_OF[name] not in flags:
            continue
        mine = {c for c in spec[1:] if c is not None}
        theirs = flags[FLAG_OF[name]]
        assert mine <= theirs, f"{module}.{name}: {mine - theirs} not accepted by {FLAG_OF[name]}"


# ---------------------------------------------------------------- real imports

def test_the_overlay_sets_checks_and_refuses(tmp_path):
    path = _overlay(tmp_path, {"jxl_tiff_encoder": {
        "EXPLORER_THUMBNAIL_SIZE": 512, "EXPORT_JXL_FOLDER": "16B_JXL [master] 5%",
        "SCANNER_SOFTWARE": ["SilverFast", "VueScan 9"],
        "DELETE_SOURCE": True, "OVERWRITE_GUARD": False,      # safety: never
        "CJXL_EFFORT": "9", "SCAN_DISTANCE": 99,               # wrong type / range
        "EXPORT_MARKER": "a/b", "NOT_A_SETTING": 1,            # bad name / unknown
        "SRC_PREFIX": "x"}})                                   # internal: not offered
    r = _values("jxl_tiff_encoder", ["EXPLORER_THUMBNAIL_SIZE", "EXPORT_JXL_FOLDER",
                                     "SCANNER_SOFTWARE", "DELETE_SOURCE", "OVERWRITE_GUARD",
                                     "CJXL_EFFORT", "SCAN_DISTANCE", "EXPORT_MARKER",
                                     "SRC_PREFIX"], path, tmp_path)
    clean = _values("jxl_tiff_encoder", ["DELETE_SOURCE", "OVERWRITE_GUARD", "CJXL_EFFORT",
                                         "SCAN_DISTANCE", "EXPORT_MARKER", "SRC_PREFIX"],
                    "", tmp_path)
    v = r["v"]
    assert v["EXPLORER_THUMBNAIL_SIZE"] == 512
    assert v["EXPORT_JXL_FOLDER"] == "16B_JXL [master] 5%"
    assert v["SCANNER_SOFTWARE"] == ["SilverFast", "VueScan 9"]
    for name in ("DELETE_SOURCE", "OVERWRITE_GUARD", "CJXL_EFFORT", "SCAN_DISTANCE",
                 "EXPORT_MARKER", "SRC_PREFIX"):
        assert v[name] == clean["v"][name], name
    assert sorted(r["o"]["applied"]) == ["EXPLORER_THUMBNAIL_SIZE", "EXPORT_JXL_FOLDER",
                                         "SCANNER_SOFTWARE"]
    text = " ".join(r["o"]["warnings"])
    for name in ("DELETE_SOURCE", "OVERWRITE_GUARD", "CJXL_EFFORT", "SCAN_DISTANCE",
                 "EXPORT_MARKER", "NOT_A_SETTING", "SRC_PREFIX"):
        assert name in text, (name, text)
    assert r["o"]["path"] == str(path)


def test_a_derived_setting_follows_its_source(tmp_path):
    one = _overlay(tmp_path, {"jxl_recompressor": {"CJXL_TIMEOUT": 4321}})
    assert _values("jxl_recompressor", ["CJXL_TIMEOUT", "EXIFTOOL_TIMEOUT"], one,
                   tmp_path)["v"] == {"CJXL_TIMEOUT": 4321, "EXIFTOOL_TIMEOUT": 4321}
    both = _overlay(tmp_path, {"jxl_recompressor": {"CJXL_TIMEOUT": 4321,
                                                    "EXIFTOOL_TIMEOUT": 77}})
    assert _values("jxl_recompressor", ["CJXL_TIMEOUT", "EXIFTOOL_TIMEOUT"], both,
                   tmp_path)["v"] == {"CJXL_TIMEOUT": 4321, "EXIFTOOL_TIMEOUT": 77}


def test_a_broken_file_changes_nothing(tmp_path):
    path = tmp_path / HOSTILE / "broken.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"jxl_tiff_decoder": {"DJXL_OUTPUT_DEPTH": 8', encoding="utf-8")
    r = _values("jxl_tiff_decoder", ["DJXL_OUTPUT_DEPTH"], path, tmp_path)
    assert r["v"]["DJXL_OUTPUT_DEPTH"] == _values("jxl_tiff_decoder", ["DJXL_OUTPUT_DEPTH"],
                                                  "", tmp_path)["v"]["DJXL_OUTPUT_DEPTH"]
    assert r["o"]["applied"] == [] and "not read" in r["o"]["warnings"][0]


def test_empty_variable_turns_it_off_and_the_home_file_is_found(tmp_path):
    home = tmp_path / "ホーム [x] y"
    _overlay(home, {})  # make the folder
    (home / ".jxl_script_settings.json").write_text(
        json.dumps({"jxl_jpeg_transcoder": {"JPEG_DEFAULT_QUALITY": 77}}), encoding="utf-8")
    env = {"HOME": str(home), "USERPROFILE": str(home)}
    if (SCRIPTS / ".jxl_script_settings.json").exists():
        pytest.skip("a portable overlay sits next to the scripts")
    found = _values("jxl_jpeg_transcoder", ["JPEG_DEFAULT_QUALITY"], None, tmp_path, env)
    off = _values("jxl_jpeg_transcoder", ["JPEG_DEFAULT_QUALITY"], "", tmp_path, env)
    assert found["v"]["JPEG_DEFAULT_QUALITY"] == 77
    assert found["o"]["path"] == str(home / ".jxl_script_settings.json")
    assert off["v"]["JPEG_DEFAULT_QUALITY"] != 77 and off["o"]["path"] == "None"


@pytest.mark.parametrize("module,name,flag,flag_value,overlay_value", [
    ("jxl_tiff_encoder", "CJXL_EFFORT", "--effort", "9", 3),
    ("jxl_tiff_decoder", "DJXL_OUTPUT_DEPTH", "--depth", "16", 8),
    ("jxl_recompressor", "CJXL_EFFORT", "--effort", "9", 4),
])
def test_a_second_run_in_the_same_process_keeps_the_overlay(
        module, name, flag, flag_value, overlay_value, tmp_path):
    """The per-run reset (_RUN_DEFAULTS / the decoder's _prev_run_globals)
    restores the OVERLAY's value, not the literal in the file."""
    path = _overlay(tmp_path, {module: {name: overlay_value}})
    lib = tmp_path / "lib [a] b" / "_EXPORT"
    lib.mkdir(parents=True)
    code = (f"import json, {module} as m\n"
            "def run(argv):\n"
            f"    sys.argv = ['{module}.py', {str(lib.parent)!r}, '--mode', '6', '--dry-run'] + argv\n"
            "    try:\n        m.main()\n    except SystemExit:\n        pass\n"
            f"run([{flag!r}, {flag_value!r}]); first = m.{name}\n"
            f"run([]); print(json.dumps([first, m.{name}]))")
    assert _child(code, path, tmp_path) == [int(flag_value), overlay_value]


@pytest.mark.parametrize("module,args", [
    ("jxl_tiff_encoder", ["--mode", "6", "--dry-run"]),
    ("jxl_tiff_decoder", ["--mode", "6", "--dry-run"]),
    ("jxl_recompressor", ["--mode", "6", "--dry-run"]),
    ("jxl_jpeg_transcoder", ["--mode", "0", "--dry-run"]),
])
def test_every_run_logs_what_it_took(module, args, tmp_path):
    name = {"jxl_tiff_decoder": "JPEG_PREVIEW_SIZE",
            "jxl_jpeg_transcoder": "JPEG_DEFAULT_QUALITY"}.get(module, "CJXL_EFFORT")
    value = {"JPEG_PREVIEW_SIZE": 300, "JPEG_DEFAULT_QUALITY": 91}.get(name, 5)
    path = _overlay(tmp_path, {module: {name: value, "DELETE_CONFIRM": False}})
    lib = tmp_path / "lib [a] b"
    lib.mkdir()
    env = {**os.environ, "JXLPHOTO_SETTINGS": str(path),
           "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"), "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, str(SCRIPTS / f"{module}.py"), str(lib)] + args,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env, stdin=subprocess.DEVNULL, timeout=300)
    out = " ".join((r.stdout + r.stderr).split())
    assert f"Script settings overlay: {path}: 1 value(s): {name}" in out, out
    assert "DELETE_CONFIRM is a safety setting" in out, out
    assert "Traceback" not in out, out


def test_the_sibling_readers_see_the_overlay(tmp_path):
    """Modes 6/7 skip the other tools' folders by their settings (#565): a
    folder renamed in the overlay must be skipped (or kept, for masters) like
    one renamed in the file."""
    path = _overlay(tmp_path, {"jxl_recompressor": {"EXPORT_JXL_FOLDER": "MOBILE [x]"},
                               "jxl_tiff_decoder": {"EXPORT_TIFF_FOLDER": "TIFF out (2)"}})
    code = ("import json, jxl_tiff_decoder as d, jxl_tiff_encoder as e\n"
            "print(json.dumps([d._sibling_str_settings('jxl_recompressor.py', "
            "{'EXPORT_JXL_FOLDER'}), sorted(e._DECODER_OUTPUT_FOLDERS)]))")
    sib, enc = _child(code, path, tmp_path)
    assert sib == {"EXPORT_JXL_FOLDER": "MOBILE [x]"}
    assert "tiff out (2)" in enc


# ---------------------------------------------------------------- wrapper menu S

def _menu(tmp_path, monkeypatch, answers):
    sys.path.insert(0, str(SCRIPTS))
    import jxl_photo as wp
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    menu._ui = wp.ScriptedUI(answers)
    return wp, menu


def test_menu_s_writes_checks_and_reverts(tmp_path, monkeypatch):
    path = tmp_path / HOSTILE / ".jxl_script_settings.json"
    monkeypatch.setenv("JXLPHOTO_SETTINGS", str(path))
    wp, menu = _menu(tmp_path, monkeypatch, {
        "settings.script": ["1", "1", "1", "0"],
        # set 512; refuse 99999 and "abc"; revert with "-"
        "settings.pick": ["/explorer_thumbnail_size", "1", "",
                          "/explorer_thumbnail_size", "1", "1", "",
                          "/explorer_thumbnail_size", "1", ""],
        "settings.value": ["512", "99999", "abc", "-"],
    })
    menu.script_settings_flow()
    ui = menu._ui
    assert not path.exists() or json.loads(path.read_text(encoding="utf-8")) == {}, \
        path.read_text(encoding="utf-8")
    text = "\n".join(ui.output)
    # v2.23.0: a table row "#  NAME  value  changed here"
    assert re.search(r"EXPLORER_THUMBNAIL_SIZE\s+512\s+changed here", text), text
    assert "must be a whole number from 64 to 2048" in text, text
    assert "back to the script's value" in text


def test_menu_s_sets_a_choice_and_the_script_takes_it(tmp_path, monkeypatch):
    path = tmp_path / HOSTILE / ".jxl_script_settings.json"
    monkeypatch.setenv("JXLPHOTO_SETTINGS", str(path))
    wp, menu = _menu(tmp_path, monkeypatch, {
        "settings.script": ["4", "0"],
        "settings.pick": ["/on_downgrade", "1", ""],
        "settings.choice": ["3"],             # ask, copy, [skip], convert
    })
    menu.script_settings_flow()
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "jxl_recompressor": {"ON_DOWNGRADE": "skip"}}
    assert _values("jxl_recompressor", ["ON_DOWNGRADE"], path, tmp_path)["v"] == {
        "ON_DOWNGRADE": "skip"}


def test_menu_s_never_lists_a_safety_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("JXLPHOTO_SETTINGS", str(tmp_path / HOSTILE / "o.json"))
    wp, menu = _menu(tmp_path, monkeypatch, {"settings.script": ["1", "2", "3", "4", "0"],
                                             "settings.pick": ["", "", "", ""]})
    menu.script_settings_flow()
    text = "\n".join(menu._ui.output)
    for name in ("DELETE_SOURCE", "DELETE_CONFIRM", "OVERWRITE_GUARD", "VERIFY_ROUNDTRIP",
                 "PROVENANCE_CHECK", "SRC_PREFIX"):
        assert not re.search(rf"^\s*\d+\s+{name}\s", text, re.M), name
    assert re.search(r"^\s*\d+\s+EXPORT_JXL_FOLDER\s", text, re.M)
    assert re.search(r"^\s*\d+\s+JPEG_PREVIEW_SIZE\s", text, re.M)


def test_menu_s_is_off_with_an_empty_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("JXLPHOTO_SETTINGS", "")
    wp, menu = _menu(tmp_path, monkeypatch, {})
    menu.script_settings_flow()
    assert any("overlay is OFF" in line for line in menu._ui.output)


def test_main_menu_offers_s(tmp_path, monkeypatch):
    wp, menu = _menu(tmp_path, monkeypatch, {})
    menu._ui = wp.ScriptedUI({"menu.main": "s"})
    assert menu.show_main_menu(False) == "S"


@pytest.mark.parametrize("module,key,name,typed,expected", [
    ("2", "jxl_tiff_decoder", "DJXL_OUTPUT_DEPTH", "16", 16),
    ("1", "jxl_tiff_encoder", "CJXL_BUFFERING", "1", 1),
    ("1", "jxl_tiff_encoder", "CJXL_BUFFERING", "none", None),
])
def test_menu_s_a_number_is_typed_as_itself(tmp_path, monkeypatch, module, key, name,
                                            typed, expected):
    """#606: menu S listed number choices by position (DJXL_OUTPUT_DEPTH:
    1 = 8, 2 = 16; CJXL_BUFFERING: 1 = null, 2 = 0), so the value a user
    typed was not the value written."""
    path = tmp_path / HOSTILE / ".jxl_script_settings.json"
    monkeypatch.setenv("JXLPHOTO_SETTINGS", str(path))
    wp, menu = _menu(tmp_path, monkeypatch, {
        "settings.script": [module, "0"],
        "settings.pick": [f"/{name.lower()}", "1", ""],
        "settings.choice": [typed],
    })
    menu.script_settings_flow()
    assert json.loads(path.read_text(encoding="utf-8")) == {key: {name: expected}}


def test_the_original_icc_copy_is_a_safety_setting(tmp_path):
    """#607: EMBED_ICC_IN_JXL = False makes a lossy master without the
    byte-exact copy of its TIFF's profile, and a deleting run still deletes
    the TIFF (the pixel check does not compare profiles). Such a choice may
    be made in the script, never by a file the run does not show."""
    path = _overlay(tmp_path, {"jxl_tiff_encoder": {"EMBED_ICC_IN_JXL": False}})
    r = _values("jxl_tiff_encoder", ["EMBED_ICC_IN_JXL"], path, tmp_path)
    assert r["v"]["EMBED_ICC_IN_JXL"] is True
    assert r["o"]["applied"] == []
    assert "EMBED_ICC_IN_JXL is a safety setting" in " ".join(r["o"]["warnings"])


@pytest.mark.parametrize("module", ["jxl_tiff_encoder", "jxl_recompressor"])
def test_worker_memory_fraction_above_one_is_taken(tmp_path, module):
    """The scripts take any fraction (the owner's execution copy runs 1.2,
    trusting the per-cjxl estimate's ~10% margin); the overlay refused
    everything above 1.0, so that edit could not move into it."""
    path = _overlay(tmp_path, {module: {"WORKER_MEMORY_FRACTION": 1.2}})
    r = _values(module, ["WORKER_MEMORY_FRACTION"], path, tmp_path)
    assert r["v"]["WORKER_MEMORY_FRACTION"] == 1.2, r["o"]["warnings"]


def test_the_wizard_no_longer_sends_users_into_the_scripts():
    """Step 4 told users that folder names like 'converted_jxl' "require
    editing the scripts directly" — menu S changes them now."""
    src = (SCRIPTS / "jxl_photo.py").read_text(encoding="utf-8")
    assert "edit the scripts directly" not in src
    assert "require editing the scripts directly" not in src
    # v2.23.0: the scripts' settings are named by their menu path (9 → 2).
    assert src.count("menu 9 → 2") >= 1
