#!/usr/bin/env python3
"""The common module, jxl_photo_common.py: one copy of each shared helper.

The rules that make moving a helper out of the scripts safe (plan 261112,
AGENTS.md "The common module"):

1. One copy. A name the common module defines is bound in a script only as
   `NAME = _common.NAME` (a pure helper) or as the one-line shim
   `def NAME(*a, **k): return _common.NAME(_G, *a, **k)` (a helper that reads
   the script), and the form matches the function: a shim for a function whose
   first parameter is `g`, a plain binding for one without.
2. No name is read there without a definition. Every global a function of the
   common module reads is the module's own, an import or a builtin: a helper
   moved with a bare `TEMP_DIR` would raise NameError only on the path that
   uses it.
3. `__file__`, `__name__`, `logger` and `globals()` are never read directly
   there. They are the SCRIPT's: the xmpMM:History entry names the script,
   and each script logs to its own logger.
4. The common module defines no setting: nothing a script lists as overlay-
   able, as a safety setting or as a run-scoped global.
5. A test double reaches the code that moved there. conftest's `monkeypatch`
   mirrors a setattr of a script's binding into the common module for the
   test, when the common code that script uses reads the name directly (as it
   did when that code lived in the script) — on the MonkeyPatch class, so a
   `MonkeyPatch.context()` is mirrored too; the `mock.patch` forms, which
   it cannot see, are refused statically here.
6. Each script loads the common module of ITS OWN folder, and refuses to start
   without one or with one of another version.
"""

import ast
import builtins
import importlib.util
import re
import shutil
import subprocess
import symtable
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

COMMON = REPO / "jxl_photo_common.py"
SCRIPTS = ["jxl_tiff_encoder.py", "jxl_tiff_decoder.py", "jxl_jpeg_transcoder.py",
           "jxl_recompressor.py", "jxl_photo.py"]
BACKENDS = SCRIPTS[:4]
# The owner's library has all of these in one path (AGENTS.md, Verification).
HOSTILE = "250118_吾妻山公園 [FINAL] (2) 100%off %d"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _common_defs() -> dict:
    return {n.name: n for n in _tree(COMMON).body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


def _takes_g(node) -> bool:
    return (isinstance(node, ast.FunctionDef) and bool(node.args.args)
            and node.args.args[0].arg == "g")


def _is_shim(node: ast.FunctionDef) -> bool:
    """`def NAME(*a, **k): return _common.NAME(_G, *a, **k)`."""
    a = node.args
    if a.args or a.posonlyargs or a.kwonlyargs or not a.vararg or not a.kwarg:
        return False
    if len(node.body) != 1 or not isinstance(node.body[0], ast.Return):
        return False
    call = node.body[0].value
    return (isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name) and call.func.value.id == "_common"
            and call.func.attr == node.name
            and len(call.args) == 2
            and isinstance(call.args[0], ast.Name) and call.args[0].id == "_G"
            and isinstance(call.args[1], ast.Starred)
            and isinstance(call.args[1].value, ast.Name) and call.args[1].value.id == a.vararg.arg
            and len(call.keywords) == 1 and call.keywords[0].arg is None
            and isinstance(call.keywords[0].value, ast.Name)
            and call.keywords[0].value.id == a.kwarg.arg)


def _binding_of(stmt):
    """NAME for `NAME = _common.NAME`, else None."""
    if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and isinstance(stmt.value, ast.Attribute)
            and isinstance(stmt.value.value, ast.Name) and stmt.value.value.id == "_common"
            and stmt.value.attr == stmt.targets[0].id):
        return stmt.targets[0].id
    return None


def _function_globals(source: str, filename: str):
    """{function name: set of globals it reads}, nested functions included."""
    table = symtable.symtable(source, filename, "exec")
    out = {}

    def walk(t, names):
        for s in t.get_symbols():
            if s.is_global() and (s.is_referenced() or s.is_assigned()):
                names.add(s.get_name())
        for c in t.get_children():
            walk(c, names)

    for child in table.get_children():
        names = set()
        walk(child, names)
        out[child.get_name()] = names
    return out


def _module_names(tree: ast.Module) -> set:
    names = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".")[0] for a in n.names)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                names.update(x.id for x in ast.walk(t) if isinstance(x, ast.Name))
    return names


# ---------------------------------------------------------------------------
# 1. One copy
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("script", SCRIPTS)
def test_c1_a_common_name_is_bound_never_copied(script):
    common = _common_defs()
    problems = []
    for stmt in _tree(REPO / script).body:
        if isinstance(stmt, (ast.FunctionDef, ast.ClassDef)) and stmt.name in common:
            if not (isinstance(stmt, ast.FunctionDef) and _is_shim(stmt)):
                problems.append(f"line {stmt.lineno}: {stmt.name} is defined again "
                                "(a second copy of a jxl_photo_common helper)")
            elif not _takes_g(common[stmt.name]):
                problems.append(f"line {stmt.lineno}: {stmt.name} is a shim, but the "
                                "common function takes no g: bind it by name instead")
        elif isinstance(stmt, ast.FunctionDef) and _is_shim(stmt):
            problems.append(f"line {stmt.lineno}: shim {stmt.name} names a function "
                            "jxl_photo_common does not define")
        name = _binding_of(stmt)
        if name and name in common and _takes_g(common[name]):
            problems.append(f"line {stmt.lineno}: {name} is bound by name, but it "
                            "takes g: it needs the shim")
    assert not problems, f"{script}:\n  " + "\n  ".join(problems)


# ---------------------------------------------------------------------------
# 2 + 3. Every global read there is defined there; the script's own names
# go through g
# ---------------------------------------------------------------------------

def test_c2_every_global_the_common_module_reads_is_defined_there():
    tree = _tree(COMMON)
    defined = _module_names(tree) | set(dir(builtins))
    problems = []
    for func, names in _function_globals(COMMON.read_text(encoding="utf-8"),
                                         str(COMMON)).items():
        for n in sorted(names - defined):
            problems.append(f"{func} reads {n}, which jxl_photo_common does not define "
                            f"(a script's global: read it as g.{n})")
    assert not problems, "\n".join(problems)


def test_c3_the_scripts_own_names_are_never_read_directly():
    forbidden = {"__file__", "__name__", "logger", "globals", "vars"}
    problems = []
    for func, names in _function_globals(COMMON.read_text(encoding="utf-8"),
                                         str(COMMON)).items():
        for n in sorted(names & forbidden):
            problems.append(f"{func} reads {n} directly: it is the calling script's "
                            f"(g.{n} / _script_get(g, ...))")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# 4. No setting in the common module
# ---------------------------------------------------------------------------

def test_c4_the_common_module_defines_no_setting():
    import jxl_tiff_encoder, jxl_tiff_decoder, jxl_jpeg_transcoder, jxl_recompressor  # noqa: E401
    common_names = {n for n in _module_names(_tree(COMMON)) if n.lstrip("_").isupper()}
    problems = []
    for mod in (jxl_tiff_encoder, jxl_tiff_decoder, jxl_jpeg_transcoder, jxl_recompressor):
        settings = set(getattr(mod, "_OVERLAY_SETTINGS", {}) or {})
        settings |= set(getattr(mod, "_OVERLAY_SAFETY_SETTINGS", ()) or ())
        settings |= set(getattr(mod, "_RUN_DEFAULTS", {}) or {})
        settings |= set(getattr(mod, "_RUN_ASSIGNABLE", ()) or ())
        for n in sorted(common_names & settings):
            problems.append(f"{n} is a setting of {mod.__name__}: it stays at the top "
                            "of the script, never in jxl_photo_common")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# 5. Test doubles the common module would not see
# ---------------------------------------------------------------------------

def _direct_names() -> set:
    names = set()
    for n in _function_globals(COMMON.read_text(encoding="utf-8"), str(COMMON)).values():
        names |= n
    return names


def test_c5_a_double_on_the_script_reaches_the_common_code(tmp_path, monkeypatch):
    folder = tmp_path / HOSTILE
    folder.mkdir()
    (folder / "fake_common_c5.py").write_text(
        "def inner():\n    return 'real'\n\n"
        "def outer():\n    return inner()\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("fake_common_c5",
                                                  folder / "fake_common_c5.py")
    common = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(common)
    script = types.ModuleType("fake_script_c5")
    script._common = common
    script.inner = common.inner
    script.outer = common.outer
    script.own = lambda: "own"
    # outer() lives in the common module and calls inner() directly: the
    # double on the script must reach it, as it did when outer was the script's.
    monkeypatch.setattr(script, "inner", lambda: "fake")
    assert script.outer() == "fake"
    # A name the common code does not read is not mirrored.
    monkeypatch.setattr(script, "own", lambda: "fake")
    assert not hasattr(common, "own")
    monkeypatch.undo()
    assert common.inner() == "real" and script.outer() == "real"


def test_c5_a_monkeypatch_context_is_mirrored_too(tmp_path):
    """`pytest.MonkeyPatch.context()` and a hand-made MonkeyPatch() are not
    the `monkeypatch` fixture, so the mirror sits on the class."""
    folder = tmp_path / HOSTILE
    folder.mkdir()
    (folder / "fake_common_c5b.py").write_text(
        "def inner():\n    return 'real'\n\n"
        "def outer():\n    return inner()\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("fake_common_c5b",
                                                  folder / "fake_common_c5b.py")
    common = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(common)
    script = types.ModuleType("fake_script_c5b")
    script._common = common
    script.inner = common.inner
    script.outer = common.outer
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(script, "inner", lambda: "fake")
        assert script.outer() == "fake"
    assert common.inner() == "real" and script.outer() == "real"
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(script, "inner", lambda: "fake")
        assert script.outer() == "fake"
    finally:
        mp.undo()
    assert common.inner() == "real" and script.outer() == "real"


def test_c5_a_tool_version_double_reaches_the_moved_version_check():
    """The case that showed it: `_tool_at_least` lives in the common module
    and reads `_tool_version` directly. A double put on the script through a
    MonkeyPatch context must decide its answer, whatever djxl is installed."""
    import jxl_jpeg_transcoder as tr
    for fake, expected in (((0, 11, 1), False), ((0, 12, 0), True)):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(tr, "_tool_version", lambda exe, v=fake: v)
            assert tr._tool_at_least("djxl", 0, 12) is expected


def test_c5_no_mock_patch_on_a_name_the_common_module_reads():
    direct = _direct_names() - set(dir(builtins))
    scripts = "|".join(s[:-3] for s in SCRIPTS)
    patterns = [
        # patch.object(module, "NAME")
        re.compile(r"""patch\.object\(\s*(\w+)\s*,\s*["'](\w+)["']"""),
        # patch("jxl_tiff_encoder.NAME")
        re.compile(r"""patch\(\s*["'](%s)\.(\w+)["']""" % scripts),
    ]
    problems = []
    for path in sorted((REPO / "tests").glob("*.py")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for pattern in patterns:
                for m in pattern.finditer(line):
                    if m.group(2) in direct:
                        problems.append(f"{path.name}:{i}: mock.patch of {m.group(1)}."
                                        f"{m.group(2)} — jxl_photo_common reads "
                                        f"{m.group(2)} directly and would not see it "
                                        "(use monkeypatch.setattr, which is mirrored)")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# 6. Each script loads the common module of its own folder
# ---------------------------------------------------------------------------

def _copy(folder: Path, names) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copy2(REPO / name, folder / name)


def test_c6_a_script_from_another_folder_loads_that_folders_common(tmp_path):
    folder = tmp_path / HOSTILE / "old copy"
    _copy(folder, ["jxl_tiff_encoder.py", "jxl_photo_common.py"])
    with open(folder / "jxl_photo_common.py", "a", encoding="utf-8") as f:
        f.write("\n_WHICH_COPY = 'the old copy'\n")
    import jxl_tiff_encoder  # noqa: F401  (the repo's common is loaded first)
    spec = importlib.util.spec_from_file_location("enc_other_folder_c6",
                                                  folder / "jxl_tiff_encoder.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert getattr(mod._common, "_WHICH_COPY", None) == "the old copy"
    assert Path(mod._common.__file__).resolve().parent == folder.resolve()
    assert not hasattr(sys.modules["jxl_photo_common"], "_WHICH_COPY")


@pytest.mark.parametrize("script", SCRIPTS)
def test_c6_a_script_runs_with_its_common_and_refuses_without(tmp_path, script):
    folder = tmp_path / HOSTILE
    _copy(folder, [script, "jxl_photo_common.py"])
    run = lambda: subprocess.run(  # noqa: E731
        [sys.executable, str(folder / script), "--version"], capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=120,
        stdin=subprocess.DEVNULL, cwd=str(tmp_path))
    r = run()
    assert r.returncode == 0, r.stdout + r.stderr

    text = (folder / "jxl_photo_common.py").read_text(encoding="utf-8")
    (folder / "jxl_photo_common.py").write_text(
        re.sub(r'(?m)^TOOLKIT_VERSION = "[^"]+"', 'TOOLKIT_VERSION = "0.0.1"', text),
        encoding="utf-8")
    r = run()
    assert r.returncode != 0
    assert "copy every file of the release together" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr

    (folder / "jxl_photo_common.py").unlink()
    r = run()
    assert r.returncode != 0
    assert "jxl_photo_common.py is missing" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr
