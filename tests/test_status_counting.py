"""A result status that the end-of-run count does not know is an ERROR, never
silence (refactor plan 261113, A4).

Each backend counts its results with an if/elif chain on the status string
(the recompressor with one sum per status). A new status added to a worker
and forgotten there fell through every branch: the run reported "0 errors"
and exited 0. Now it counts as an error with the reason "internal: status
'<x>' is not counted". This test keeps the shape: every such chain ends with
an `else`, and the recompressor checks for statuses outside its sums.
"""
import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = ("jxl_tiff_encoder.py", "jxl_tiff_decoder.py", "jxl_jpeg_transcoder.py",
           "jxl_recompressor.py")


def _status_test(test) -> bool:
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1
            and isinstance(test.ops[0], (ast.Eq, ast.In))):
        return False
    right = test.comparators[0]
    consts = [right] if isinstance(right, ast.Constant) else getattr(right, "elts", [])
    return bool(consts) and all(isinstance(c, ast.Constant) and isinstance(c.value, str)
                                for c in consts)


def _chains(tree):
    """Every if/elif chain of 3+ links in a for loop, each link testing a string."""
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for loop in ast.walk(fn):
            if not isinstance(loop, ast.For):
                continue
            for stmt in loop.body:
                if not (isinstance(stmt, ast.If) and _status_test(stmt.test)):
                    continue
                links, node = [stmt], stmt
                while len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                    node = node.orelse[0]
                    links.append(node)
                if len(links) >= 3 and all(_status_test(link.test) for link in links):
                    yield fn.name, stmt.lineno, bool(node.orelse)


def test_every_status_count_ends_with_an_else():
    missing = []
    found = 0
    for name in SCRIPTS:
        tree = ast.parse((REPO / name).read_text(encoding="utf-8"))
        for fn, line, has_else in _chains(tree):
            found += 1
            if not has_else:
                missing.append(f"{name}:{line} ({fn})")
    assert found >= 5, "the status chains were not found: the test reads nothing"
    assert not missing, ("a status count without an else lets a new status vanish from "
                         "the summary: " + ", ".join(missing))


def test_the_recompressor_counts_an_unknown_status_as_an_error():
    import sys
    sys.path.insert(0, str(REPO))
    import jxl_recompressor as rec

    counts = rec._count_results({
        "a.jxl": ("ok", "a"), "b.jxl": ("copied", "b"), "c.jxl": ("weird", "c"),
        "d.jxl": ("error", "d"), "e.jxl": ("aborted", "e"),
    })
    assert counts["uncounted"] == [("c.jxl", "internal: status 'weird' is not counted")]
    assert (counts["ok"], counts["copied"], counts["error"], counts["aborted"]) == (1, 1, 1, 1)
