"""Every run-scoped global a main() sets from a flag is put back at the next
main() (refactor plan 261113, A1; the recompressor's export marker and folders
were the live case, B-1).

In production each backend runs one main() per process, but the suite, a
script imported by another one and any future in-process caller run several:
a global that a flag sets only when it is given (`if args.x is not None:
X = args.x`) leaks into the next run unless the script's reset list restores
it -- the class of #442 and #529, each "fixed" by adding one name to a list.
This reads the scripts' AST, so a new flag cannot ship without its reset.
"""
import ast
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import jxl_recompressor as rec  # noqa: E402

SCRIPTS = ("jxl_tiff_encoder.py", "jxl_tiff_decoder.py", "jxl_jpeg_transcoder.py",
           "jxl_recompressor.py")


def _reset_names(tree: ast.Module) -> set:
    """The names of the script's per-run reset: _RUN_DEFAULTS (restored to the
    import-time value) or the decoder's _RUN_ASSIGNABLE (restored to the value
    before the previous run)."""
    names = set()
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            continue
        target = node.targets[0].id
        if target == "_RUN_DEFAULTS" and isinstance(node.value, ast.Dict):
            names |= {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
        elif target == "_RUN_ASSIGNABLE" and isinstance(node.value, (ast.Tuple, ast.List)):
            names |= {e.value for e in node.value.elts if isinstance(e, ast.Constant)}
    return names


def _stored(nodes) -> set:
    out = set()
    for stmt in nodes:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                out.add(n.id)
            elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name):
                out.add(n.target.id)
    return out


def _uncovered(fn: ast.FunctionDef, resets: set) -> list:
    declared = {name for n in ast.walk(fn) if isinstance(n, ast.Global) for name in n.names}
    # A plain top-level assignment runs on every call: it resets the name itself.
    every_run = set()
    for stmt in fn.body:
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            for t in targets:
                every_run |= {n.id for n in ast.walk(t) if isinstance(n, ast.Name)}
    sometimes = set()
    for stmt in fn.body:
        if isinstance(stmt, (ast.If, ast.Try, ast.For, ast.While, ast.With)):
            sometimes |= _stored([stmt])
    return sorted((sometimes & declared) - every_run - resets)


def _entry_points():
    for name in SCRIPTS:
        tree = ast.parse((REPO / name).read_text(encoding="utf-8"))
        resets = _reset_names(tree)
        for fn in tree.body:
            if isinstance(fn, ast.FunctionDef) and (fn.name == "main" or fn.name.startswith("cmd_")):
                yield f"{name}::{fn.name}", fn, resets


@pytest.mark.parametrize("label,fn,resets", list(_entry_points()),
                         ids=[e[0] for e in _entry_points()])
def test_every_flag_set_global_is_reset_at_the_next_run(label, fn, resets):
    missing = _uncovered(fn, resets)
    assert not missing, (
        f"{label} assigns {missing} under `global` only when a flag is given, and "
        f"nothing puts them back before the next main(): add them to the script's "
        f"_RUN_DEFAULTS (the decoder: _RUN_ASSIGNABLE), or reset them unconditionally "
        f"at the top of the function")


def test_the_reset_lists_are_found():
    """The test above reads nothing if the lists move or change shape."""
    for name in SCRIPTS:
        tree = ast.parse((REPO / name).read_text(encoding="utf-8"))
        assert len(_reset_names(tree)) >= 10, name


@pytest.mark.skipif(not all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool")),
                    reason="main() checks the tools before it plans")
def test_the_recompressor_puts_the_export_settings_back_between_two_runs(tmp_path, monkeypatch):
    """B-1: --export-marker/--export-subfolder/--export-jxl-folder are assigned
    only when given; a second main() without them kept the first run's values."""
    root = tmp_path / "250118_吾妻山 [FINAL] 100%off"
    root.mkdir()
    shipped = (rec.EXPORT_MARKER, rec.EXPORT_JXL_SUBFOLDER, rec.EXPORT_JXL_FOLDER)

    def run(*extra):
        monkeypatch.setattr(sys, "argv", ["jxl_recompressor.py", str(root), "--mode", "7",
                                          "--dry-run", *extra])
        try:
            rec.main()
        except SystemExit:
            pass

    run("--export-marker", "_OUT", "--export-subfolder", "SUB", "--export-jxl-folder", "XJXL")
    assert (rec.EXPORT_MARKER, rec.EXPORT_JXL_SUBFOLDER, rec.EXPORT_JXL_FOLDER) == (
        "_OUT", "SUB", "XJXL"), "the flags did not apply: the test proves nothing"
    run()
    assert (rec.EXPORT_MARKER, rec.EXPORT_JXL_SUBFOLDER, rec.EXPORT_JXL_FOLDER) == shipped
