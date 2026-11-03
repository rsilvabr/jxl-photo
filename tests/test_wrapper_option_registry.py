#!/usr/bin/env python3
"""The wrapper's option registry (phase 1 of plan 261103, wrapper redesign).

Every flag the wrapper passes to a child is an `Opt` in jxl_photo.REGISTRY, and
one builder (build_child_cmd) turns a run into argv for the direct run and the
manifest alike. These tests keep the registry honest against the children:

  * every `--flag` a child's argparse declares is in the registry (for a
    direction that runs on that child), in NOT_OFFERED with a reason, or in
    MAINTENANCE_FLAGS — a child gaining a flag fails here until someone
    decides what the wrapper does with it;
  * every flag the registry can emit exists in each child of its directions,
    with the same choices;
  * the builder emits nothing that is not in the registry (on the golden
    cases of test_wrapper_argv_golden.py, which pin the argv itself).

Pre-fix proof: on the base commit jxl_photo has no REGISTRY, so every test
here fails at its first attribute access.
"""

import ast
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MODULES = ("jxl_tiff_encoder", "jxl_tiff_decoder", "jxl_jpeg_transcoder",
           "jxl_recompressor")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GOLDEN = _load("golden_argv_cases", Path(__file__).resolve().parent
               / "test_wrapper_argv_golden.py")


@pytest.fixture(scope="module")
def wp():
    return GOLDEN._load_wrapper()


def _choices(node):
    """argparse choices as a set of strings, or None when not a literal."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "range"):
        try:
            return {str(v) for v in range(*[ast.literal_eval(a) for a in node.args])}
        except Exception:
            return None
    try:
        return {str(v) for v in ast.literal_eval(node)}
    except Exception:
        return None


def _child_flags(module: str) -> dict:
    """{flag: choices-or-None} of every add_argument() in the child."""
    tree = ast.parse((REPO / f"{module}.py").read_text(encoding="utf-8"))
    out = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            choices = None
            for kw in node.keywords:
                if kw.arg == "choices":
                    choices = _choices(kw.value)
            for arg in node.args:
                if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                        and arg.value.startswith("--")):
                    out[arg.value] = choices
    return out


CHILD = {m: _child_flags(m) for m in MODULES}


def _opt_flags(opt):
    return [opt.flag] + ([opt.neg_flag] if opt.neg_flag else []) + list(opt.extra_flags)


def test_every_child_flag_is_declared(wp):
    missing = []
    for module in MODULES:
        emitted = set()
        for d, m in wp.DIRECTION_MODULE.items():
            if m == module:
                emitted |= wp._registry_flags(d)
        for flag in CHILD[module]:
            key = f"{module}:{flag}"
            if flag in emitted or key in wp.NOT_OFFERED or key in wp.MAINTENANCE_FLAGS:
                continue
            missing.append(key)
    assert not missing, (
        "child flags the wrapper neither passes nor declares: "
        f"{missing} — add an Opt to REGISTRY, or a NOT_OFFERED entry saying why")


def test_registry_flags_exist_in_their_children(wp):
    bad = []
    for opt in wp.REGISTRY:
        for d in opt.directions:
            module = wp.DIRECTION_MODULE[d]
            bad += [f"{opt.id}: {f} ({module})" for f in _opt_flags(opt)
                    if f not in CHILD[module]]
    assert not bad, f"the registry names flags a child does not accept: {bad}"


def test_registry_choices_match_the_children(wp):
    bad = []
    for opt in wp.REGISTRY:
        if not opt.choices:
            continue
        mine = {str(c) for c in opt.choices}
        theirs = [CHILD[wp.DIRECTION_MODULE[d]].get(opt.flag) for d in opt.directions]
        theirs = [t for t in theirs if t is not None]
        assert theirs, f"{opt.id}: choices given but no child declares any"
        union = set().union(*theirs)
        if not mine <= union:
            bad.append(f"{opt.id}: {sorted(mine - union)} not accepted")
        # A plain entry offers exactly what each child accepts; a policy
        # entry maps its values (adopt is downgraded, jpg is spelled jpeg).
        if opt.kind != "policy":
            bad += [f"{opt.id}: {sorted(mine)} != {sorted(t)}" for t in theirs if t != mine]
    assert not bad, bad


def test_entries_are_well_formed(wp):
    groups = {g for g, _title in wp.GROUPS}
    ids = [o.id for o in wp.REGISTRY]
    assert len(ids) == len(set(ids)), "duplicate registry ids"
    for o in wp.REGISTRY:
        assert o.label.strip() and o.help.strip(), o.id
        assert o.group in groups, (o.id, o.group)
        assert o.level in wp.LEVELS, (o.id, o.level)
        assert o.directions and o.directions <= set(wp.DIRECTIONS), o.id
        assert o.kind in ("flag", "value", "notnone", "always", "tri", "switch",
                          "policy"), (o.id, o.kind)
        assert o.flag.startswith("--"), o.id
        if o.kind in ("tri", "switch"):
            assert o.neg_flag.startswith("--"), o.id
        if o.kind == "policy":
            assert callable(getattr(wp.InteractiveMenu, o.policy, None)), (o.id, o.policy)
        else:
            assert o.legacy_key or o.value is not None, o.id
            if o.legacy_key:
                assert o.legacy_key.split(".", 1)[0] in (
                    "advanced", "workflow", "mode_config"), o.id


def test_not_offered_entries_are_real_and_unused(wp):
    for table in (wp.NOT_OFFERED, wp.MAINTENANCE_FLAGS):
        for key, why in table.items():
            module, _, flag = key.partition(":")
            assert module in MODULES and flag in CHILD[module], f"stale entry {key}"
            assert why.strip(), key
            emitted = set()
            for d, m in wp.DIRECTION_MODULE.items():
                if m == module:
                    emitted |= wp._registry_flags(d)
            assert flag not in emitted, f"{key} is declared AND emitted"


@pytest.mark.parametrize("name", sorted(GOLDEN.CASES))
def test_builder_emits_only_registry_flags(name, wp, tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    argv = GOLDEN._argv_for(wp, menu, monkeypatch, name, tmp_path / "case")
    _kind, wf, _row = GOLDEN.CASES[name]
    tail = set(wp._split_expert_flags(wf.get("expert_flags") or ""))
    direction = wp._direction(wf["origin_format"], wf["dest_format"])
    allowed = wp._registry_flags(direction)
    stray = [a for a in argv if a.startswith("--") and a not in allowed and a not in tail]
    assert not stray, f"{name}: flags emitted outside the registry: {stray}"
