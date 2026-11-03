#!/usr/bin/env python3
"""Phase 2 of the wrapper plan: the children's command line is a function,
and the wrapper checks expert flags against it before a run.

build_parser() moves each script's add_argument block out of main() without
changing one flag (the encoder, the decoder and the recompressor; the
transcoder already had it): the option strings must equal the AST of the
file. The wrapper parses the expert flags with that parser
(parse_known_args) and refuses a typo, another script's flag or a value
outside its choices BEFORE the HHMM token — the child used to exit 2 after
the token was charged.

Pre-fix proof: JXL_SCRIPTS_UNDER_TEST=<the five scripts of the base commit>.
"""

import ast
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_OVERRIDE = os.environ.get("JXL_SCRIPTS_UNDER_TEST")
SCRIPTS = Path(_OVERRIDE).resolve() if _OVERRIDE else _REPO
sys.path.insert(0, str(SCRIPTS))

import importlib  # noqa: E402

import jxl_photo as wp  # noqa: E402

MODULES = ("jxl_tiff_encoder", "jxl_tiff_decoder", "jxl_jpeg_transcoder",
           "jxl_recompressor")
HOSTILE = "250118_吾妻山公園_菜の花 [FINAL] 5%"


def _ast_flags(module):
    tree = ast.parse((SCRIPTS / f"{module}.py").read_text(encoding="utf-8"))
    out = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            out |= {a.value for a in node.args
                    if isinstance(a, ast.Constant) and isinstance(a.value, str)
                    and a.value.startswith("-")}
    return out


@pytest.mark.parametrize("module", MODULES)
def test_build_parser_declares_exactly_the_files_flags(module):
    parser = importlib.import_module(module).build_parser()
    got = {s for a in parser._actions for s in a.option_strings} - {"-h", "--help"}
    assert got == _ast_flags(module) - {"-h", "--help"}


@pytest.mark.parametrize("origin,dest,flags,bad", [
    ("tiff", "jxl", "--bufering 1", "--bufering"),
    ("jxl", "tiff", "--compress-metadata", "--compress-metadata"),
    ("tiff", "jxl", "--effort 99", "invalid choice"),
])
def test_bad_expert_flags_are_named(origin, dest, flags, bad):
    why = wp._expert_flags_problem(origin, dest, flags)
    assert why and bad in why, why


@pytest.mark.parametrize("origin,dest,flags", [
    ("tiff", "jxl", "--buffering 1 --no-preflight"),
    ("jxl", "jpeg", "--sharpen-sigma 0.8 --compress-metadata"),
    ("jxl", "jxl", "--scan-scope raw"),
])
def test_good_expert_flags_pass(origin, dest, flags):
    assert wp._expert_flags_problem(origin, dest, flags) is None


def test_a_typo_is_refused_before_the_token(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    calls = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=None: calls.append(cmd) or 0)
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode",
                        lambda self: calls.append("token") or True)
    d = tmp_path / HOSTILE
    d.mkdir()
    wf = {"input_dir": str(d), "origin_format": "tiff", "dest_format": "jxl",
          "conversion_type": "jxl_tiff_encoder", "mode": 1, "workers": 2,
          "staging": None, "icc_profile": None, "use_ram": True,
          "compression": None, "bit_depth": None, "add_preview": True,
          "dry_run": False, "advanced_options": {"delete_source": True},
          "expert_flags": "--bufering 1", "mode_config": {}, "exclude_folders": None}
    assert not menu.execute_workflow(wf, {k: True for k in ("cjxl", "djxl", "exiftool")})
    assert calls == [], calls
