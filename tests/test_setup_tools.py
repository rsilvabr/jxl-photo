#!/usr/bin/env python3
"""`jxl_photo.py setup`: the tools on PATH and the Python packages.

Nothing here reaches the registry: every test reads and writes PATH values
through a _JsonPathStore (conftest pins JXLPHOTO_SETUP_PATH_STORE too, and a
test below proves it). Every folder that holds tools has Japanese characters,
[ ], a space and % in its name (AGENTS.md). Fake tools are .cmd files; the
tests that need no process inject `probe`, which reads the .cmd instead of
running it.
"""

import ast
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import jxl_photo as wp  # noqa: E402

HOSTILE = "工具 [x] 100%off %d"
WINDOWS = os.name == "nt"
pytestmark = pytest.mark.skipif(not WINDOWS, reason="the setup's PATH model is Windows'")

# getattr: the pre-fix proof of the wizard's probe (test 9) imports this file
# against a jxl_photo.py that has no setup at all.
TESTED = {k: v["version"] for k, v in getattr(wp, "_SETUP_TESTED_TOOLS", {
    "libjxl": {"version": "0"}, "exiftool": {"version": "0"}, "magick": {"version": "0"}}).items()}
LINES = {
    "cjxl": f"cjxl v{TESTED['libjxl']} abc [AVX2]",
    "djxl": f"djxl v{TESTED['libjxl']} abc [AVX2]",
    "jxlinfo": "Usage: jxlinfo [-v] INPUT",
    "exiftool": TESTED["exiftool"],
    "magick": f"Version: ImageMagick {TESTED['magick']} Q16-HDRI x64 abc",
}


def tool(folder: Path, name: str, line: str = None, broken: bool = False) -> Path:
    """A fake tool: a .cmd that prints `line` (or fails)."""
    folder.mkdir(parents=True, exist_ok=True)
    line = LINES.get(name.split("(")[0].split("-k")[0], "x") if line is None else line
    p = folder / f"{name}.cmd"
    body = "@echo BROKEN 1>&2\r\n@exit /b 1\r\n" if broken else f"@echo {line}\r\n@exit /b 0\r\n"
    p.write_text(body, encoding="utf-8")
    return p


def libjxl(folder: Path, version: str = None, djxl_version: str = None) -> Path:
    v = version or TESTED["libjxl"]
    tool(folder, "cjxl", f"cjxl v{v} abc")
    tool(folder, "djxl", f"djxl v{djxl_version or v} abc")
    tool(folder, "jxlinfo")
    return folder


def fake_probe(path, args, timeout=20):
    """What running the fake would answer, without a process."""
    text = Path(path).read_text(encoding="utf-8")
    if "BROKEN" in text:
        return wp._Probe(False, 1, "", "BROKEN", False, "exit code 1")
    return wp._Probe(True, 0, re.search(r"@echo (.*)", text).group(1) + "\n", "")


PY_OK = {"executable": sys.executable, "version": "3.12.7", "venv": False,
         "prefix": sys.prefix, "pip": "24.0",
         "packages": dict(getattr(wp, "_SETUP_TESTED_PACKAGES", {})),
         "error": None, "py_launcher": None}


def py_ok():
    return json.loads(json.dumps(PY_OK))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A JSON store, a LOCALAPPDATA and a wizard config of this test only."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("JXLPHOTO_SETUP_PATH_STORE", str(tmp_path / "store.json"))
    cfg = tmp_path / ".jxl_tools_config.json"
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path", lambda self: cfg)
    root = tmp_path / HOSTILE
    root.mkdir()

    class Env:
        pass
    e = Env()
    e.root, e.cfg, e.store = root, cfg, wp._JsonPathStore(tmp_path / "store.json")
    e.backups = tmp_path / "local" / "jxl-photo" / "path-backups"

    def set_path(user=None, system=None, user_type=2, system_type=2):
        if user is not None:
            e.store.write("user", user if isinstance(user, str) else ";".join(map(str, user)),
                          user_type)
        if system is not None:
            e.store.write("system", system if isinstance(system, str)
                          else ";".join(map(str, system)), system_type)
    e.set_path = set_path
    return e


def all_tools(root: Path):
    """libjxl, ExifTool and ImageMagick at the tested versions, three folders."""
    a = libjxl(root / "libjxl")
    b = root / "exiftool"
    tool(b, "exiftool")
    c = root / "magick"
    tool(c, "magick")
    return a, b, c


def codes(report, unit=None):
    return [(f.code, f.severity) for f in report.findings if unit is None or f.unit == unit]


class RecordingUI(wp.ScriptedUI):
    """ScriptedUI that also keeps each question's default."""

    def __init__(self, answers):
        super().__init__(answers)
        self.defaults = []

    def confirm(self, qid, prompt, default=False):
        self.defaults.append((qid, default, prompt))
        return super().confirm(qid, prompt, default)


# --- 1. PATH strings ---------------------------------------------------------

def test_path_entries_keep_raw_text_and_drop_empties():
    assert wp._path_entries(r' C:\a ;;%USERPROFILE%\bin; ;"C:\q r";') == [
        r"C:\a", r"%USERPROFILE%\bin", '"C:\\q r"']
    assert wp._path_entries(None) == []


def test_path_key_normalises():
    assert wp._path_key(r'"C:\Tools\Bin\"') == wp._path_key(r"c:/tools/bin")
    assert wp._path_key("C:\\") == os.path.normcase("C:\\")
    assert wp._path_key(r"%USERPROFILE%\bin") == wp._path_key(
        os.path.join(os.environ["USERPROFILE"], "bin"))


def test_path_edit_keeps_untouched_entries_byte_for_byte():
    value = r"%USERPROFILE%\bin;C:\old;;C:\keep\;c:\OLD\;C:\new"
    out = wp._path_edit(value, remove=[r"C:\old"], prepend=[r"C:\new", r"D:\second"])
    # Every occurrence of a removed or prepended folder goes; the prepended
    # ones come first, in order; %VAR% stays unexpanded; empties dropped.
    assert out == "C:\\new;D:\\second;%USERPROFILE%\\bin;C:\\keep\\"
    assert wp._path_edit("", prepend=[r"C:\x"]) == r"C:\x"


def test_path_remove_later_duplicates_and_replace():
    value = r"C:\a;C:\b;c:\A\;C:\c"
    assert wp._path_remove_later_duplicates(value, r"C:\a") == r"C:\a;C:\b;C:\c"
    assert wp._path_replace(value, r"C:\b", r"C:\B~1") == r"C:\a;C:\B~1;c:\A\;C:\c"


def test_new_process_path_is_system_then_user(env):
    env.set_path(user=r"C:\u1;C:\u2", system=r"C:\s1")
    assert wp._new_process_path(env.store) == [
        (r"C:\s1", "system"), (r"C:\u1", "user"), (r"C:\u2", "user")]


# --- 2. Which copy runs ---------------------------------------------------------

def test_libjxl_winner_is_the_first_on_the_path(env):
    early, late = libjxl(env.root / "early"), libjxl(env.root / "late", "0.12.0")
    env.set_path(user=[early, late])
    rep = wp._diagnose(env.store, fake_probe, "")
    assert Path(rep.units["libjxl"].main.path).parent == early
    assert ("CONFLICT", "warning") in codes(rep, "libjxl")


def test_exiftool_name_order_beats_path_order(env):
    early, late = env.root / "early", env.root / "late"
    tool(early, "exiftool(-k)")
    tool(late, "exiftool")
    env.set_path(user=[early, late])
    rep = wp._diagnose(env.store, fake_probe, "")
    assert Path(rep.units["exiftool"].main.path) == late / "exiftool.cmd"
    # Real processes and a real `which` agree (the scripts' rule).
    assert Path(shutil.which("exiftool", path=f"{early};{late}")).parent == late


def test_find_copies_dedupes_one_file(env):
    a = env.root / "a"
    tool(a, "cjxl")
    assert wp._find_copies("cjxl", [str(a), str(a) + "\\"]) == [a / "cjxl.cmd"]


# --- 3. One test per finding ----------------------------------------------------

def test_missing(env):
    env.set_path(user="")
    rep = wp._diagnose(env.store, fake_probe, "")
    assert ("MISSING", "error") in codes(rep, "libjxl")
    assert ("MISSING", "error") in codes(rep, "exiftool")
    assert ("MISSING", "warning") in codes(rep, "magick")


def test_broken(env):
    a, b, c = all_tools(env.root)
    tool(a, "cjxl", broken=True)
    env.set_path(user=[a, b, c])
    assert ("BROKEN", "error") in codes(wp._diagnose(env.store, fake_probe, ""), "libjxl")


def test_too_old(env):
    a, b, c = all_tools(env.root)
    libjxl(a, "0.10.2")
    env.set_path(user=[a, b, c])
    assert ("TOO_OLD", "error") in codes(wp._diagnose(env.store, fake_probe, ""), "libjxl")


def test_old_exiftool_and_magick(env):
    a, b, c = all_tools(env.root)
    tool(b, "exiftool", "12.10")
    tool(c, "magick", "Version: ImageMagick 6.9.12-98 Q16 x64")
    env.set_path(user=[a, b, c])
    rep = wp._diagnose(env.store, fake_probe, "")
    assert ("OLD", "warning") in codes(rep, "exiftool")
    assert ("OLD", "warning") in codes(rep, "magick")


def test_q8(env):
    a, b, c = all_tools(env.root)
    tool(c, "magick", "Version: ImageMagick 7.1.2-27 Q8 x64")
    env.set_path(user=[a, b, c])
    assert ("Q8", "error") in codes(wp._diagnose(env.store, fake_probe, ""), "magick")


def test_libjxl_split(env):
    a, b, c = all_tools(env.root)
    other = env.root / "other"
    tool(other, "jxlinfo")
    (a / "jxlinfo.cmd").unlink()
    env.set_path(user=[a, b, c, other])
    assert ("LIBJXL_SPLIT", "error") in codes(wp._diagnose(env.store, fake_probe, ""), "libjxl")


def test_libjxl_versions(env):
    a, b, c = all_tools(env.root)
    libjxl(a, "0.13.0", djxl_version="0.12.0")
    env.set_path(user=[a, b, c])
    assert ("LIBJXL_VERSIONS", "error") in codes(wp._diagnose(env.store, fake_probe, ""),
                                                 "libjxl")


def test_shadowed_by_system(env):
    a, b, c = all_tools(env.root)
    old = libjxl(env.root / "system old", "0.12.0")
    env.set_path(user=[a, b, c], system=[old])
    rep = wp._diagnose(env.store, fake_probe, "")
    f = next(f for f in rep.findings if f.code == "SHADOWED_BY_SYSTEM")
    assert f.severity == "warning" and f.fix == ("remove_entries", "system", [str(old)])


@pytest.mark.parametrize("version,severity", [(TESTED["exiftool"], "info"), ("13.10", "warning")])
def test_conflict(env, version, severity):
    a, b, c = all_tools(env.root)
    second = env.root / "second exiftool"
    tool(second, "exiftool", version)
    env.set_path(user=[a, b, c, second])
    f = next(f for f in wp._diagnose(env.store, fake_probe, "").findings if f.code == "CONFLICT")
    assert f.severity == severity and f.fix == ("remove_entries", "user", [str(second)])


def test_conflict_never_offers_a_folder_that_holds_another_winner(env):
    a, b, c = all_tools(env.root)
    tool(a, "exiftool", "13.10")            # loses, but a holds the cjxl that runs
    env.set_path(user=[b, a, c])
    f = next(f for f in wp._diagnose(env.store, fake_probe, "").findings if f.code == "CONFLICT")
    assert f.fix is None and "Not offered for removal" in f.message


def test_stale_entry(env):
    a, b, c = all_tools(env.root)
    gone_u, gone_s = env.root / "gone user", env.root / "gone system"
    env.set_path(user=[a, b, c, gone_u], system=[gone_s])
    found = {(f.code, f.severity, f.fix[1]) for f in wp._diagnose(env.store, fake_probe, "").findings
             if f.code == "STALE_ENTRY"}
    assert found == {("STALE_ENTRY", "warning", "user"), ("STALE_ENTRY", "info", "system")}


def test_an_unknown_variable_is_not_called_stale(env):
    a, b, c = all_tools(env.root)
    env.set_path(user=[a, b, c, r"%JXLPHOTO_NOT_A_VARIABLE_X%\bin"])
    assert not [f for f in wp._diagnose(env.store, fake_probe, "").findings
                if f.code == "STALE_ENTRY"]


def test_duplicate_entry(env):
    a, b, c = all_tools(env.root)
    env.set_path(user=[a, b, c, str(a) + "\\"])
    f = next(f for f in wp._diagnose(env.store, fake_probe, "").findings
             if f.code == "DUPLICATE_ENTRY")
    assert f.severity == "info" and f.fix[0] == "remove_duplicates"


def test_exiftool_ansi(env, monkeypatch):
    a, b, c = all_tools(env.root)
    jp = env.root / "ツール"
    tool(jp, "exiftool", broken=True)
    shutil.rmtree(b)
    env.set_path(user=[a, jp, c])
    f = next(f for f in wp._diagnose(env.store, fake_probe, "").findings
             if f.unit == "exiftool")
    assert (f.code, f.severity) == ("EXIFTOOL_ANSI", "error")
    expect = "short_path" if wp._short_path(jp) and wp._ansi_safe(wp._short_path(jp)) else "download"
    assert f.fix[0] == expect
    monkeypatch.setattr(wp, "_short_path", lambda p: None)
    f = next(f for f in wp._diagnose(env.store, fake_probe, "").findings if f.unit == "exiftool")
    assert f.fix == ("download", "exiftool")


def test_process_only(env):
    here = libjxl(env.root / "launcher only")
    env.set_path(user="")
    rep = wp._diagnose(env.store, fake_probe, str(here))
    f = next(f for f in rep.findings if f.code == "PROCESS_ONLY")
    assert f.severity == "info" and f.unit == "libjxl"


def test_untested(env):
    a, b, c = all_tools(env.root)
    libjxl(a, "0.12.0")
    env.set_path(user=[a, b, c])
    rep = wp._diagnose(env.store, fake_probe, "")
    assert codes(rep, "libjxl") == [("UNTESTED", "info")]
    assert rep.units["libjxl"].main.version == "0.12.0"


def test_everything_tested_has_no_finding(env):
    env.set_path(user=list(all_tools(env.root)))
    assert wp._diagnose(env.store, fake_probe, "").findings == []


def test_real_processes_on_fake_tools_in_a_hostile_folder(env):
    """The real _probe runs a .cmd in a folder with Japanese, [ ], a space
    and %; stdin is NUL."""
    env.set_path(user=list(all_tools(env.root)))
    rep = wp._diagnose(env.store, process_path="")
    assert rep.findings == []
    assert rep.units["magick"].main.version == TESTED["magick"]
    assert rep.units["magick"].main.quantum == "Q16"


# --- 4. Writes ------------------------------------------------------------------

def test_write_path_backs_up_first_and_logs(env):
    env.set_path(user=r"C:\a;%USERPROFILE%\bin", user_type=1)
    before = env.store.read("user")
    ok = wp._write_path(env.store, "user", r"C:\new;C:\a;%USERPROFILE%\bin",
                        expected_before=before[0], expected_type=before[1], reason="test",
                        say=lambda s: None)
    assert ok
    assert env.store.read("user") == (r"C:\new;C:\a;%USERPROFILE%\bin", 1)   # REG_SZ kept
    (backup,) = env.backups.glob("*_user.json")
    data = json.loads(backup.read_text(encoding="utf-8"))
    assert data["before"] == r"C:\a;%USERPROFILE%\bin" and data["after"].startswith(r"C:\new")
    assert data["type"] == 1 and data["toolkit_version"] == wp.TOOLKIT_VERSION
    assert "user written (test)" in (env.backups / "changes.log").read_text(encoding="utf-8")


def test_write_path_creates_a_missing_value_as_expand_sz(env):
    assert env.store.read("user") == (None, None)
    assert wp._write_path(env.store, "user", r"C:\x", expected_before=None, expected_type=None,
                          reason="new", say=lambda s: None)
    assert env.store.read("user") == (r"C:\x", wp._REG_EXPAND_SZ)


def test_write_path_refuses_when_the_value_changed_meanwhile(env):
    env.set_path(user=r"C:\a")
    said = []
    env.store.write("user", r"C:\someone else", 2)
    ok = wp._write_path(env.store, "user", r"C:\new;C:\a", expected_before=r"C:\a",
                        expected_type=2, reason="t", say=said.append)
    assert not ok and "changed meanwhile" in said[0]
    assert env.store.read("user") == (r"C:\someone else", 2)
    assert not env.backups.exists() or not list(env.backups.glob("*.json"))


def test_undo_restores_before(env):
    env.set_path(user=r"C:\a")
    assert wp._write_path(env.store, "user", r"C:\new;C:\a", expected_before=r"C:\a",
                          expected_type=2, reason="t", say=lambda s: None)
    ui = RecordingUI({"setup.undo": None})
    assert wp._undo_last_change(env.store, ui)
    assert env.store.read("user") == (r"C:\a", 2)
    assert ("setup.undo", True) in [(q, d) for q, d, _ in ui.defaults]
    assert len(list(env.backups.glob("*_user*.json"))) == 2      # the undo is backed up too


def test_undo_asks_with_default_no_when_the_value_changed_since(env):
    env.set_path(user=r"C:\a")
    wp._write_path(env.store, "user", r"C:\new;C:\a", expected_before=r"C:\a",
                   expected_type=2, reason="t", say=lambda s: None)
    env.store.write("user", r"C:\other", 2)
    ui = RecordingUI({"setup.undo": None})
    assert not wp._undo_last_change(env.store, ui)
    assert env.store.read("user") == (r"C:\other", 2)
    assert ("setup.undo", False) in [(q, d) for q, d, _ in ui.defaults]


# --- 5. The system PATH plan (the elevated side, in-process) ----------------------

def _plan(env, new_value, before, btype=2):
    plan, sha = wp._write_system_plan(new_value, before, btype, "test")
    return plan, sha


def test_system_plan_writes(env):
    env.set_path(system=r"C:\s1;C:\s2")
    plan, sha = _plan(env, r"C:\s2", r"C:\s1;C:\s2")
    assert wp._apply_system_path_plan(env.store, plan, sha) == 0
    assert env.store.read("system") == (r"C:\s2", 2)
    assert json.loads(Path(str(plan) + ".result.json").read_text())["code"] == 0
    assert list(env.backups.glob("*_system.json"))


def test_system_plan_refuses_a_value_changed_meanwhile(env):
    env.set_path(system=r"C:\s1;C:\s2")
    plan, sha = _plan(env, r"C:\s2", r"C:\s1;C:\s2")
    env.store.write("system", r"C:\changed", 2)
    assert wp._apply_system_path_plan(env.store, plan, sha) == 3
    assert env.store.read("system") == (r"C:\changed", 2)
    assert json.loads(Path(str(plan) + ".result.json").read_text())["code"] == 3


def test_system_plan_refuses_a_wrong_sha256(env):
    env.set_path(system=r"C:\s1")
    plan, sha = _plan(env, r"C:\x", r"C:\s1")
    assert wp._apply_system_path_plan(env.store, plan, "0" * 64) == 4
    plan.write_text(plan.read_text().replace("C:", "D:"))
    assert wp._apply_system_path_plan(env.store, plan, sha) == 4
    assert env.store.read("system") == (r"C:\s1", 2)
    assert json.loads(Path(str(plan) + ".result.json").read_text())["code"] == 4


def test_setup_main_runs_the_plan(env):
    env.set_path(system=r"C:\s1;C:\s2")
    plan, sha = _plan(env, r"C:\s1", r"C:\s1;C:\s2")
    assert wp._setup_main(["--apply-system-path", str(plan), "--sha256", sha]) == 0
    assert env.store.read("system") == (r"C:\s1", 2)


# --- 6. The session --------------------------------------------------------------

def _session(env, answers, ui_cls=wp.ScriptedUI, **kw):
    ui = ui_cls(answers)
    rc = wp._setup_session(ui, env.store, wp._setup_default_args(), probe=fake_probe,
                           python_report=py_ok, process_path="", **kw)
    return rc, ui


def test_session_nothing_on_path_then_a_folder(env):
    a = libjxl(env.root / "libjxl")
    env.set_path(user=r"%USERPROFILE%\bin")
    rc, ui = _session(env, {"setup.main": ["1", "0"], "setup.unit": "p",
                            "setup.folder": str(a / "cjxl.cmd"), "setup.confirm": True})
    assert rc == 0
    assert env.store.read("user") == (f"{a};%USERPROFILE%\\bin", 2)
    assert list(env.backups.glob("*_user.json"))
    assert wp._SETUP_RESTART_NOTE in ui.output
    assert json.loads(env.cfg.read_text(encoding="utf-8"))["dependencies_checked"] is False


def test_session_switch_exiftool_by_choice(env):
    """Everything works, the person points ExifTool at another folder anyway;
    the old folder holds another program, so its removal defaults to No."""
    a, b, c = all_tools(env.root)
    (b / "other tool.exe").write_bytes(b"MZ")
    new = env.root / "exiftool 13.60"
    tool(new, "exiftool", "13.60")
    env.set_path(user=[a, b, c])
    rc, ui = _session(env, {"setup.main": ["2", "0"], "setup.unit": "p",
                            "setup.folder": str(new), "setup.confirm": [True, True],
                            "setup.remove_old": None}, RecordingUI)
    entries = wp._path_entries(env.store.read("user")[0])
    assert entries == [str(new), str(a), str(b), str(c)]
    remove_q = [(d, p) for q, d, p in ui.defaults if q == "setup.remove_old"]
    assert remove_q and remove_q[0][0] is False and "other tool.exe" in remove_q[0][1]
    assert any("not the version this release was tested with" in p for _, _, p in ui.defaults)


def test_session_old_folder_with_nothing_else_defaults_to_remove(env):
    a, b, c = all_tools(env.root)
    new = env.root / "exiftool new"
    tool(new, "exiftool")
    env.set_path(user=[a, b, c])
    _session(env, {"setup.main": ["2", "0"], "setup.unit": "p", "setup.folder": str(new),
                   "setup.confirm": True, "setup.remove_old": None})
    assert wp._path_entries(env.store.read("user")[0]) == [str(new), str(a), str(c)]


def test_session_f_fixes_a_stale_user_entry(env):
    a, b, c = all_tools(env.root)
    gone = env.root / "gone"
    env.set_path(user=[a, gone, b, c])
    rc, ui = _session(env, {"setup.main": ["f", "0"], "setup.confirm": True})
    assert wp._path_entries(env.store.read("user")[0]) == [str(a), str(b), str(c)]


def test_session_f_removes_a_shadowing_system_entry_through_the_plan(env):
    a, b, c = all_tools(env.root)
    old = libjxl(env.root / "system old", "0.12.0")
    env.set_path(user=[a, b, c], system=[old, r"C:\Windows"])
    rc, ui = _session(env, {"setup.main": ["f", "0"], "setup.elevate": True})
    assert env.store.read("system") == (r"C:\Windows", 2)
    assert "setup.elevate" in ui.asked


def test_session_declined_confirmation_writes_nothing(env):
    a = libjxl(env.root / "libjxl")
    env.set_path(user=r"C:\keep")
    store_before = (env.store.file).read_bytes()
    _session(env, {"setup.main": ["1", "0"], "setup.unit": "p", "setup.folder": str(a),
                   "setup.confirm": False})
    assert env.store.file.read_bytes() == store_before
    assert not env.backups.exists() or not list(env.backups.iterdir())


def test_session_refuses_a_folder_without_libjxl(env):
    half = env.root / "half"
    tool(half, "cjxl")
    env.set_path(user="")
    rc, ui = _session(env, {"setup.main": ["1", "0"], "setup.unit": "p",
                            "setup.folder": [str(half), ""]})
    assert any("djxl, jxlinfo not in" in line for line in ui.output)
    assert env.store.read("user") == ("", 2)


def test_session_says_everything_works(env):
    env.set_path(user=list(all_tools(env.root)))
    rc, ui = _session(env, {"setup.main": "0"})
    assert "Everything works." in ui.output


def test_setup_main_maps_ctrl_c_and_eof(env, monkeypatch, capsys):
    for exc, word in ((KeyboardInterrupt, "Ctrl+C"), (EOFError, "end of input")):
        def boom(*a, **k):
            raise exc()
        monkeypatch.setattr(wp, "_setup_session", boom)
        assert wp._setup_main([]) == 130
        assert word in capsys.readouterr().out


def test_setup_refuses_in_the_exe(monkeypatch, capsys):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert wp._setup_main(["--check"]) == 2
    assert "carries its own tools" in capsys.readouterr().out


# --- 7. --check ------------------------------------------------------------------

def test_check_exit_codes(env):
    env.set_path(user=list(all_tools(env.root)))
    assert wp._setup_check(env.store, fake_probe, py_ok, "") == 0
    env.set_path(user="")
    assert wp._setup_check(env.store, fake_probe, py_ok, "") == 1


def test_check_writes_nothing(env, capsys):
    env.set_path(user=list(all_tools(env.root)) + [env.root / "gone"])
    env.cfg.write_text('{"dependencies_checked": true}', encoding="utf-8")
    store_bytes, cfg_bytes = env.store.file.read_bytes(), env.cfg.read_bytes()
    rc = wp._setup_main(["--check"])
    assert rc in (0, 1)
    assert "Tools" in capsys.readouterr().out
    assert env.store.file.read_bytes() == store_bytes
    assert env.cfg.read_bytes() == cfg_bytes
    assert not (env.backups.parent).exists()


# --- 8. Python and pip --------------------------------------------------------------

class _R:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_python_report_and_pip_commands():
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        assert kw.get("stdin") is subprocess.DEVNULL
        if argv[1:3] == ["-m", "pip"]:
            return _R(1, "", "No module named pip")
        return _R(0, json.dumps({"numpy": None, "tifffile": "2026.6.1", "imagecodecs": None,
                                 "pillow": "12.3.0", "rich": "15.0.0"}) + "\n")
    py = wp._python_report(run=run, which=lambda name: None)
    assert py["pip"] is None and py["packages"]["numpy"] is None
    found = {f.code: f for f in wp._python_findings(py)}
    assert found["PACKAGES_MISSING"].severity == "error"
    assert "numpy, imagecodecs" in found["PACKAGES_MISSING"].message
    assert "NO_PIP" in found
    assert wp._pip_command("i", py) == [sys.executable, "-m", "pip", "install", "numpy",
                                        "imagecodecs"]
    assert wp._pip_command("t", py)[4:] == [f"{n}=={v}" for n, v in
                                            wp._SETUP_TESTED_PACKAGES.items()]
    assert wp._pip_command("u", py)[4] == "--upgrade"
    # The packages are asked in a process of their own, never imported here.
    assert calls[1][:2] == [sys.executable, "-c"]


def test_no_pip_offers_ensurepip():
    ran = []
    ui = wp.ScriptedUI({"setup.confirm": False})
    wp._setup_packages(ui, dict(py_ok(), pip=None), run=lambda argv, **k: ran.append(argv))
    assert ran == []
    assert any("ensurepip --upgrade" in line for line in ui.output)
    assert any("python.org with 'pip' ticked" in line for line in ui.output)
    ui = wp.ScriptedUI({"setup.confirm": True})
    wp._setup_packages(ui, dict(py_ok(), pip=None), run=lambda argv, **k: (ran.append(argv),
                                                                           _R(1))[1])
    assert ran == [[sys.executable, "-m", "ensurepip", "--upgrade"]]
    assert any("python.org" in line for line in ui.output)


def test_packages_menu_runs_pip_without_capture():
    ran = []
    py = dict(py_ok(), packages=dict(py_ok()["packages"], rich=None))
    ui = wp.ScriptedUI({"setup.packages": "i", "setup.confirm": True})
    wp._setup_packages(ui, py, run=lambda argv, **k: (ran.append((argv, k)), _R(0))[1])
    assert ran == [([sys.executable, "-m", "pip", "install", "rich"], {})]


def test_py_launcher_differs_is_noted():
    def run(argv, **kw):
        if argv[0] == "py.exe":
            return _R(0, r"C:\Other\python.exe" + "\n")
        if argv[1:3] == ["-m", "pip"]:
            return _R(0, "pip 24.0 from x\n")
        return _R(0, json.dumps(PY_OK["packages"]) + "\n")
    py = wp._python_report(run=run, which=lambda name: "py.exe")
    f = next(f for f in wp._python_findings(py) if f.code == "PY_LAUNCHER_DIFFERS")
    assert f.severity == "info" and r"C:\Other\python.exe" in f.message


# --- 9. A8: the wizard's own probe reads no console ----------------------------------

def test_wizard_tool_probe_does_not_wait_for_stdin(tmp_path):
    """A tool that reads stdin (exiftool(-k)'s pause): the wizard's probe ran
    it with the console's stdin and it waited the whole 10 s timeout, read as
    missing. Run in a child whose stdin is an open pipe nobody writes."""
    folder = tmp_path / HOSTILE
    folder.mkdir()
    reader = folder / "reads stdin.cmd"
    reader.write_text("@set /p x=\r\n@exit /b 0\r\n", encoding="utf-8")
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import jxl_photo as wp\n"
            "print('RESULT', wp.DependencyChecker(None)._test_tool_execution(sys.argv[2], []))\n")
    p = subprocess.Popen([sys.executable, "-c", code, str(REPO), str(reader)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out = p.stdout.read()        # the pipe on stdin stays open meanwhile
        err = p.stderr.read()
        p.wait(timeout=60)
    finally:
        p.stdin.close()
    assert b"RESULT True" in out, (out, err)


def test_menu_d_offers_the_setup_and_checks_again(env, monkeypatch):
    env.cfg.write_text(json.dumps({"dependencies_checked": True, "presets": {},
                                   "show_thumbnail_tip": False}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(wp.DependencyChecker, "check_dependencies",
                        lambda self, force=False, persist=True, **kw:
                        (calls.append(("check", force)), {"cjxl": True, "djxl": True})[1])
    picks = iter(["D", "0"])
    monkeypatch.setattr(wp.InteractiveMenu, "show_main_menu", lambda self, has_last: next(picks))
    monkeypatch.setattr(wp, "_update_check_start", lambda c: (lambda wait=0.0: None))
    monkeypatch.setattr(wp, "_update_check_now", lambda c: ("Release check: off.", None))
    monkeypatch.setattr(wp, "_thumb_update_notice", lambda c: None)
    monkeypatch.setattr(wp, "_setup_session",
                        lambda ui, store, args, **k: calls.append(("setup", type(store).__name__)))
    ui = wp.ScriptedUI({"deps.setup": True})
    monkeypatch.setattr(wp.InteractiveMenu, "ui", property(lambda self: ui))
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(sys, "argv", ["jxl_photo.py"])
    wp.main()
    assert ui.asked == ["deps.setup"]
    i = calls.index(("setup", "_JsonPathStore"))
    assert calls[i + 1] == ("check", True)


def test_the_start_refusal_names_the_setup(env, monkeypatch, capsys):
    monkeypatch.setattr(wp.DependencyChecker, "check_dependencies",
                        lambda self, force=False, persist=True, **kw: {})
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(sys, "argv", ["jxl_photo.py"])
    with pytest.raises(SystemExit):
        wp.main()
    assert "jxl_photo.py setup' to find or install them." in capsys.readouterr().out


# --- 10. Real tools ------------------------------------------------------------------

def _real(name):
    path = shutil.which(name)
    if not path:
        pytest.skip(f"{name} not on PATH")
    return Path(path)


def test_real_tools_diagnose_and_set_folder(env):
    cjxl, djxl, jxlinfo = _real("cjxl"), _real("djxl"), _real("jxlinfo")
    exif = (shutil.which("exiftool") or shutil.which("exiftool(-k)"))
    if not exif:
        pytest.skip("exiftool not on PATH")
    magick = _real("magick")
    folders = list(dict.fromkeys(str(p.parent) for p in (cjxl, djxl, jxlinfo, Path(exif), magick)))
    env.set_path(user=folders)
    rep = wp._diagnose(env.store, process_path="")
    errors = [f for f in rep.findings if f.severity == "error"]
    assert not errors, errors
    for key in ("libjxl", "exiftool", "magick"):
        assert rep.units[key].main.version, key
    hostile = env.root / "libjxl copy"
    hostile.mkdir()
    for p in (cjxl, djxl, jxlinfo):
        shutil.copy2(p, hostile / p.name)
    problem, info, _ = wp._check_tool_folder(wp._setup_unit("libjxl"), hostile)
    assert problem is None and info["version"] == rep.units["libjxl"].main.version


def test_real_exiftool_in_a_non_ansi_folder_and_its_short_name(env):
    exif = shutil.which("exiftool") or shutil.which("exiftool(-k)")
    if not exif or not (Path(exif).parent / "exiftool_files").is_dir():
        pytest.skip("no Windows ExifTool with exiftool_files on PATH")
    jp = env.root / "ツール"
    jp.mkdir()
    shutil.copy2(exif, jp / Path(exif).name)
    shutil.copytree(Path(exif).parent / "exiftool_files", jp / "exiftool_files")
    a, _, c = all_tools(env.root)
    env.set_path(user=[a, jp, c])
    rep = wp._diagnose(env.store, process_path="")
    f = next(f for f in rep.findings if f.unit == "exiftool")
    assert f.code == "EXIFTOOL_ANSI", f
    short = wp._short_path(jp)
    if not short or not wp._ansi_safe(short):
        pytest.skip("no 8.3 short name on this volume")
    assert f.fix == ("short_path", str(jp))
    ui = wp.ScriptedUI({"setup.confirm": True})
    assert wp._setup_short_path(ui, env.store, str(jp))
    rep = wp._diagnose(env.store, process_path="")
    assert rep.units["exiftool"].main.ok and not [g for g in rep.findings
                                                 if g.severity == "error"]


# --- 11. Part B: the tested download ---------------------------------------------------

class _Response:
    def __init__(self, data):
        self.data, self.pos = data, 0
        self.headers = {"Content-Length": str(len(data))}

    def read(self, n):
        chunk = self.data[self.pos:self.pos + n]
        self.pos += len(chunk)
        return chunk

    def close(self):
        pass


@pytest.fixture
def libjxl_archive(tmp_path, monkeypatch):
    """A zip like libjxl's release (bin/ + licenses/) made from this PC's
    real cjxl, djxl and jxlinfo, put in the tested table for this test."""
    import hashlib
    cjxl, djxl, jxlinfo = _real("cjxl"), _real("djxl"), _real("jxlinfo")
    tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    if not tar.is_file():
        pytest.skip("no tar.exe")
    version = wp._parse_version("libjxl", "cjxl", wp._probe(cjxl, ["--version"]))[0]
    stage = tmp_path / "stage"
    (stage / "bin").mkdir(parents=True)
    (stage / "licenses" / "brotli").mkdir(parents=True)
    (stage / "licenses" / "brotli" / "LICENSE").write_text("MIT", encoding="utf-8")
    # ExifTool's real zip extracts its folders read-only: Windows then
    # refused to remove the temp extract folder (found by the manual run).
    os.chmod(stage / "licenses" / "brotli", stat.S_IREAD)
    for p in (cjxl, djxl, jxlinfo):
        shutil.copy2(p, stage / "bin" / p.name)
    archive = tmp_path / "x.zip"
    subprocess.run([str(tar), "-a", "-cf", str(archive), "-C", str(stage), "bin", "licenses"],
                   check=True, stdin=subprocess.DEVNULL, capture_output=True)
    data = archive.read_bytes()
    spec = {"version": version, "url": "https://example.invalid/x.zip",
            "sha256": hashlib.sha256(data).hexdigest(), "archive": "x.zip"}
    monkeypatch.setitem(wp._SETUP_TESTED_TOOLS, "libjxl", spec)
    return data, spec


def test_download_installs_checks_and_reuses(env, libjxl_archive):
    data, spec = libjxl_archive
    base = wp._tools_base(str(env.root / "tools"))
    if base is None or not wp._ansi_safe(str(base)):
        pytest.skip("no ANSI-safe short name for the hostile folder")
    ui = wp.ScriptedUI({"setup.download": True})
    got = wp._download_tested("libjxl", base, ui,
                              opener=lambda req, timeout=None: _Response(data))
    assert got == base / f"libjxl-{spec['version']}"
    assert (got / "VERSION.txt").read_text(encoding="utf-8") == \
        f"{spec['version']}\n{spec['sha256']}\n"
    assert {p.name for p in got.iterdir()} >= {"cjxl.exe", "djxl.exe", "jxlinfo.exe", "licenses"}
    assert [p.name for p in Path(base).iterdir()] == [got.name]       # no .part, .tmp, archive

    def no_network(*a, **k):
        raise AssertionError("downloaded again")
    ui = wp.ScriptedUI({})
    assert wp._download_tested("libjxl", base, ui, opener=no_network) == got
    assert any("already in" in line for line in ui.output)
    shutil.rmtree(got)                  # plain: the installed copy is writable


def test_download_with_a_wrong_sha256_deletes_it(env, libjxl_archive, monkeypatch):
    data, spec = libjxl_archive
    monkeypatch.setitem(wp._SETUP_TESTED_TOOLS, "libjxl", dict(spec, sha256="0" * 64))
    base = env.root / "tools"
    ran = []
    real_run = subprocess.run
    monkeypatch.setattr(wp.subprocess, "run", lambda *a, **k: (ran.append(a), real_run(*a, **k))[1])
    ui = wp.ScriptedUI({"setup.download": True})
    assert wp._download_tested("libjxl", base, ui,
                              opener=lambda req, timeout=None: _Response(data)) is None
    assert list(base.iterdir()) == []
    assert ran == []                                    # nothing extracted, nothing run
    assert any("nothing was run or installed" in line for line in ui.output)


def test_download_declined_or_too_big(env, libjxl_archive):
    data, spec = libjxl_archive
    base = env.root / "tools"
    ui = wp.ScriptedUI({"setup.download": False})
    assert wp._download_tested("libjxl", base, ui,
                               opener=lambda req, timeout=None: _Response(data)) is None
    big = _Response(b"")
    big.headers = {"Content-Length": str(wp._SETUP_MAX_DOWNLOAD + 1)}
    ui = wp.ScriptedUI({})
    assert wp._download_tested("libjxl", base, ui, opener=lambda req, timeout=None: big) is None
    assert not base.exists() or list(base.iterdir()) == []


# --- 12. The tested versions stay in step ----------------------------------------------

def test_tested_versions_match_the_exe_and_install_md():
    build = REPO.parent / "jxl-photo-exe" / "build.py"
    text = (REPO / "docs" / "install.md").read_text(encoding="utf-8")
    sentence = next(line for line in text.splitlines() if line.startswith("Tested with"))
    for key, name in (("libjxl", "libjxl"), ("exiftool", "exiftool"), ("magick", "ImageMagick")):
        assert f"{name} {wp._SETUP_TESTED_TOOLS[key]['version']}" in sentence, key
    for name, version in wp._SETUP_TESTED_PACKAGES.items():
        assert version in sentence, name
    if not build.is_file():
        pytest.skip("../jxl-photo-exe is not here")
    tree = ast.parse(build.read_text(encoding="utf-8"))
    consts = {t.id: ast.literal_eval(node.value) for node in tree.body
              if isinstance(node, ast.Assign) for t in node.targets
              if isinstance(t, ast.Name) and t.id in ("TOOLS", "REQUIREMENTS")}
    for key, exe_key in (("libjxl", "libjxl"), ("exiftool", "exiftool"), ("magick", "imagemagick")):
        url, sha = consts["TOOLS"][exe_key]
        assert (wp._SETUP_TESTED_TOOLS[key]["url"], wp._SETUP_TESTED_TOOLS[key]["sha256"]) == \
            (url, sha), key
    pins = dict(r.split("==") for r in consts["REQUIREMENTS"] if not r.startswith("pyinstaller"))
    assert pins == wp._SETUP_TESTED_PACKAGES


# --- 13. Nothing under the suite reaches the registry ----------------------------------

def test_the_suite_never_reaches_the_registry():
    assert isinstance(wp._path_store(), wp._JsonPathStore)
    real = Path(os.path.expanduser("~")) / "AppData" / "Local"
    assert os.path.normcase(os.environ["LOCALAPPDATA"]) != os.path.normcase(str(real))


# --- 14. The review's fixes (261127_Opus_review_setup-tools.md) ----------------------

def test_the_suite_refuses_every_registry_write(monkeypatch):
    """S2: even a test that drops JXLPHOTO_SETUP_PATH_STORE (and so gets the
    real _PathStore) cannot write the registry: conftest refuses winreg's
    writers. Safe before the fix too: a None key and a None handle write
    nothing."""
    import contextlib
    import winreg
    with pytest.raises(Exception) as e:
        winreg.SetValueEx(None, "Path", 0, 2, "x")
    assert "test suite" in str(e.value)
    monkeypatch.delenv("JXLPHOTO_SETUP_PATH_STORE")
    store = wp._path_store()
    assert type(store).__name__ == "_PathStore"
    monkeypatch.setattr(wp._PathStore, "_open",
                        staticmethod(lambda scope, access: contextlib.nullcontext(None)))
    with pytest.raises(Exception) as e:
        store.write("user", "x", 2)
    assert "test suite" in str(e.value)


def test_conflict_in_a_shared_folder_is_not_a_suggested_removal(env):
    """S1: a losing copy does no harm; its folder's other programs would
    leave the PATH with it (a scoop shims folder, a tools folder): [f] never
    offers that, [r] still can."""
    a, b, c = all_tools(env.root)
    shared = env.root / "shared bin"
    tool(shared, "exiftool", "13.10")
    (shared / "other tool.exe").write_bytes(b"MZ")
    env.set_path(user=[a, b, c, shared])
    f = next(f for f in wp._diagnose(env.store, fake_probe, "").findings if f.code == "CONFLICT")
    assert f.fix is None and "other tool.exe" in f.message


def test_shadowed_names_what_else_leaves_with_the_system_entry(env):
    a, b, c = all_tools(env.root)
    old = libjxl(env.root / "system old", "0.12.0")
    (old / "other tool.exe").write_bytes(b"MZ")
    env.set_path(user=[a, b, c], system=[old])
    f = next(f for f in wp._diagnose(env.store, fake_probe, "").findings
             if f.code == "SHADOWED_BY_SYSTEM")
    assert f.fix == ("remove_entries", "system", [str(old)]) and "other tool.exe" in f.message
    rc, ui = _session(env, {"setup.main": ["f", "0"], "setup.elevate": False})
    assert any("other tool.exe" in line and "leave the PATH too" in line for line in ui.output)
    assert env.store.read("system") == (str(old), 2)


def test_the_plan_result_file_is_this_users(env):
    """S3: the plan folder is mkdtemp's (SYSTEM, Administrators, OWNER RIGHTS
    only, Python 3.12.4+): a result file the elevated step CREATED there
    would belong to Administrators (or another admin) and this user could
    not read it. The non-elevated side creates it; the elevated side writes
    into that same file."""
    env.set_path(system=r"C:\s1;C:\s2")
    plan, sha = wp._write_system_plan(r"C:\s2", r"C:\s1;C:\s2", 2, "t")
    result = Path(str(plan) + ".result.json")
    assert result.is_file()
    ino = os.stat(result).st_ino
    assert wp._apply_system_path_plan(env.store, plan, sha) == 0
    assert os.stat(result).st_ino == ino
    assert json.loads(result.read_text(encoding="utf-8"))["code"] == 0


def test_path_diff_counts_the_entries_left_alone(env):
    """S4: a removal was subtracted from the entries left alone."""
    ui = wp.ScriptedUI({})
    wp._show_path_diff(ui, env.store, "user", r"C:\b;C:\a;C:\c", r"C:\new;C:\a;C:\c")
    assert "2 other entries unchanged." in ui.output[-1]
    ui = wp.ScriptedUI({})
    wp._show_path_diff(ui, env.store, "user", r"C:\a;C:\gone;C:\b;C:\c", r"C:\a;C:\b;C:\c")
    assert "3 other entries unchanged." in ui.output[-1]
    ui = wp.ScriptedUI({})
    wp._show_path_diff(ui, env.store, "user", r"C:\a;C:\b;C:\new", r"C:\new;C:\a;C:\b")
    assert "2 other entries unchanged." in ui.output[-1]


def test_a_folder_whose_path_has_a_semicolon_is_refused(env):
    """S5: written into PATH, `lib;jxl` is two entries, neither of them the
    folder."""
    a = libjxl(env.root / "lib;jxl")
    env.set_path(user=r"C:\keep")
    rc, ui = _session(env, {"setup.main": ["1", "0"], "setup.unit": "p",
                            "setup.folder": [str(a), ""], "setup.confirm": True})
    assert env.store.read("user") == (r"C:\keep", 2)
    assert any("cannot be a PATH entry" in line for line in ui.output)


def test_undo_never_takes_a_backup_of_another_store(env, tmp_path):
    """S6: a by-hand try on a JSON copy (JXLPHOTO_SETUP_PATH_STORE) leaves its
    backups in the same folder: [z] on the registry must not restore one."""
    env.set_path(user=r"C:\a")
    assert wp._write_path(env.store, "user", r"C:\new;C:\a", expected_before=r"C:\a",
                          expected_type=2, reason="t", say=lambda s: None)
    other = wp._JsonPathStore(tmp_path / "other store.json")
    other.write("user", r"C:\real", 2)
    ui = wp.ScriptedUI({"setup.undo": None})
    assert not wp._undo_last_change(other, ui)
    assert "setup.undo" not in ui.asked
    assert other.read("user") == (r"C:\real", 2)
    ui = wp.ScriptedUI({"setup.undo": None})
    assert wp._undo_last_change(env.store, ui)
    assert env.store.read("user") == (r"C:\a", 2)
