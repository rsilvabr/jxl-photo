"""Delivery C, part 2: the pre-run refusals live in one table (_GATE_RULES),
called by the three paths that start a run: execute_workflow (direct and,
for the common rules, the manifest), _execute_manifest_workflow (per row) and
_run_saved_session (unattended presets). The gate tests of the paths
themselves (test_audit_*, test_delete_*, test_manifest_*, ...) pass without
edits; these check the table: each rule refuses on its own path and is not
run on the others, and no path grew an inline refusal outside it.

Pre-fix proof (fails on the beta, which has no _job_gates):
    $env:JXLPHOTO_SCRIPTS_UNDER_TEST = "<tmp with git show f7cbcba:...>"
    python -m pytest tests/test_job_gates.py -q
"""

import ast
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)
sys.path.insert(0, str(SCRIPTS))
import jxl_photo as wp  # noqa: E402

PATHS = ("common", "direct", "manifest", "preset")


class _Menu:
    def __init__(self):
        self.errors = []

    def _print_error(self, msg):
        self.errors.append(msg)


def _wf(**over):
    wf = {"origin_format": "jxl", "dest_format": "jxl", "conversion_type": "jxl_recompress",
          "mode": 1, "advanced_options": {}, "expert_flags": "", "extra_options": {},
          "input_dir": "x"}
    wf.update(over)
    return wf


CASES = {
    "ambiguous_delete_flags": (_wf(expert_flags="--delete"), {}, "ambiguous abbreviation"),
    "mode_in_expert_flags": (_wf(expert_flags="--mode 8"), {}, "the mode is chosen in the wizard"),
    "expert_flags_parse": (_wf(expert_flags="--no-such-flag"), {}, "Expert flags cannot run"),
    "direct_derivatives": (_wf(mode=8, advanced_options={"output_icc": "sRGB"}), {},
                           "cannot run in place"),
    "preset_unattended": ({}, {"session": {"last_advanced_options": {"delete_source": True}},
                               "dry_run": False, "origin": "tiff", "dest": "jxl", "mode": 1},
                          "cannot be given unattended"),
}


def _rule(name):
    return next(r for r in wp._GATE_RULES if r[0] == name)


@pytest.mark.parametrize("name", sorted(CASES))
def test_each_rule_refuses_on_its_own_path_only(name):
    wf, ctx, text = CASES[name]
    _n, paths, _fn = _rule(name)
    (path,) = tuple(paths)
    m = _Menu()
    assert wp._job_gates(m, dict(wf), path, **ctx) is True
    assert any(text in e for e in m.errors), m.errors
    for other in PATHS:
        if other in paths:
            continue
        m2 = _Menu()
        # The other paths never run this rule (they may run their own ones on
        # the same workflow; none of those refuses these cases).
        try:
            wp._job_gates(m2, dict(wf), other, **ctx)
        except KeyError:
            pass          # a path's rules need their own context: not run here
        assert not any(text in e for e in m2.errors), (other, m2.errors)


def test_the_manifest_rule_refuses_a_derivative_row_with_a_delete():
    m = _Menu()
    ctx = {"origin": "jxl", "dest": "jxl", "advanced": {"delete_source": True},
           "row_effs": [({}, {}), ({}, {"output_icc": "sRGB"})],
           "resolved_entries": [("a", "a", 1), ("b", "b", 1)]}
    assert wp._job_gates(m, _wf(), "manifest", **ctx) is True
    assert "derivative row(s): 2" in m.errors[0]


def test_a_dry_run_preset_is_not_refused():
    m = _Menu()
    ctx = dict(CASES["preset_unattended"][1], dry_run=True)
    assert wp._job_gates(m, {}, "preset", **ctx) is False and m.errors == []


def test_a_clean_run_passes_every_path():
    for path in ("common", "direct"):
        m = _Menu()
        assert wp._job_gates(m, _wf(), path) is False, (path, m.errors)


def test_rule_order_is_the_old_order():
    assert [r[0] for r in wp._GATE_RULES] == [
        "ambiguous_delete_flags", "expert_flags_parse", "registry_rules",
        "mode_in_expert_flags", "direct_derivatives", "manifest_derivatives",
        "preset_unattended"]


def _method(name):
    tree = ast.parse((SCRIPTS / "jxl_photo.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "InteractiveMenu")
    return next(f for f in cls.body if isinstance(f, ast.FunctionDef) and f.name == name)


def _errors_before(fn, stop_attr):
    """The _print_error texts a method prints before its first call of
    `stop_attr` (the refusals made before the run is prepared)."""
    texts = []
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == stop_attr):
            stop = node.lineno
            break
    else:
        stop = fn.end_lineno
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_print_error" and node.lineno < stop):
            texts.append(ast.unparse(node.args[0])[:60] if node.args else "")
    return texts


def test_execute_workflow_refuses_only_through_the_table_before_the_token():
    """Before the lossy delete-skipped confirmation, execute_workflow prints
    only the missing-script errors itself: every other refusal is a rule."""
    texts = _errors_before(_method("execute_workflow"), "_confirm_lossy_delete_skipped")
    assert all("Script not found" in t or "Ensure scripts" in t for t in texts), texts


def test_the_preset_path_refuses_unattended_only_through_the_table():
    texts = _errors_before(_method("_run_saved_session"), "execute_workflow")
    assert not any("unattended" in t for t in texts), texts
