"""Every `g.<name>` a script can reach in jxl_photo_common.py exists in that script.

A common function that takes the script's namespace `g` reads the script's
settings, logger, state and helpers through it at call time. A name one script
lacks is an AttributeError only on the path that reads it, often a rare one —
nothing else catches it (Fable's full sweep, G5, 2026-11-22: none missing then).

Reach: the common names the script uses (`_common.X`: its bindings and shims),
what those call inside common, and through g (`g._f(...)` is the script's own
binding of `_f`, followed into common again). A read guarded by
`_script_get`/`getattr`/`hasattr` with the name as a constant is optional.
"""
import ast
import importlib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SCRIPTS = ("jxl_tiff_encoder", "jxl_tiff_decoder", "jxl_recompressor",
           "jxl_jpeg_transcoder", "jxl_photo")


def _common_functions():
    tree = ast.parse((REPO / "jxl_photo_common.py").read_text(encoding="utf-8"))
    funcs = {}
    classes = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            funcs[node.name] = node
        elif isinstance(node, ast.ClassDef):
            methods = [m for m in node.body if isinstance(m, ast.FunctionDef)]
            for m in methods:
                funcs[f"{node.name}.{m.name}"] = m
            classes[node.name] = [f"{node.name}.{m.name}" for m in methods]
    return funcs, classes


def _reads(fn, known):
    """(g-reads {name: line}, the common names it calls or passes, optional names)."""
    greads, calls, optional = {}, set(), set()
    for x in ast.walk(fn):
        if (isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name) and x.value.id == "g"
                and x.attr != "_g"):              # the namespace itself (_script_get)
            greads.setdefault(x.attr, x.lineno)
        if isinstance(x, ast.Call) and isinstance(x.func, ast.Name):
            calls.add(x.func.id)
            if (x.func.id in ("_script_get", "getattr", "hasattr") and len(x.args) >= 2
                    and isinstance(x.args[1], ast.Constant)):
                optional.add(x.args[1].value)
        if isinstance(x, ast.Name) and x.id in known:
            calls.add(x.id)
    return greads, calls, optional


FUNCS, CLASSES = _common_functions()
INFO = {name: _reads(fn, FUNCS) for name, fn in FUNCS.items()}
for _cls, _methods in CLASSES.items():
    INFO[_cls] = ({}, set(_methods), set())


def _missing_in(script: str):
    mod = importlib.import_module(script)
    tree = ast.parse((REPO / f"{script}.py").read_text(encoding="utf-8"))
    todo = [x.attr for x in ast.walk(tree)
            if isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name)
            and x.value.id == "_common" and x.attr in INFO]
    seen, missing = set(), {}
    while todo:
        name = todo.pop()
        if name in seen or name not in INFO:
            continue
        seen.add(name)
        greads, calls, optional = INFO[name]
        todo.extend(c for c in calls if c in INFO and c not in seen)
        for attr, line in greads.items():
            if hasattr(mod, attr):
                if attr in INFO and attr not in seen:
                    todo.append(attr)          # the script's binding, back in common
            elif attr not in optional:
                missing.setdefault(attr, []).append(f"{name} (line {line})")
    return seen, missing


@pytest.mark.parametrize("script", SCRIPTS)
def test_every_g_name_a_script_reaches_exists_in_it(script):
    seen, missing = _missing_in(script)
    least = 5 if script == "jxl_photo" else 20      # the wizard binds few of them
    assert len(seen) > least, f"{script}: the reach analysis found almost nothing ({len(seen)})"
    assert not missing, (f"{script} lacks names common reads through g: "
                         + "; ".join(f"g.{a} <- {', '.join(w[:3])}"
                                     for a, w in sorted(missing.items())))
