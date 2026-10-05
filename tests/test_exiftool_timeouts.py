#!/usr/bin/env python3
"""Per-file exiftool calls get the codec's patience (EXIFTOOL_TIMEOUT).

The 2026-10-06 scheduled MOBILE run lost twelve 45 MP derivatives at once:
with 17 workers on a busy hard disk, exiftool exceeded its fixed 60/120 s
limit, and the recompressor reported each one as "codec timed out after 900s".
Every per-file exiftool call in the four backends now uses EXIFTOOL_TIMEOUT
(defaults to the script's codec timeout); only the planning-time batch reads
keep their per-batch limit. The recompressor names the tool that timed out.
"""

import ast
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# script -> its codec timeout setting
SCRIPTS = {
    "jxl_tiff_encoder": "CJXL_TIMEOUT",
    "jxl_tiff_decoder": "DJXL_TIMEOUT",
    "jxl_jpeg_transcoder": "CODEC_TIMEOUT",
    "jxl_recompressor": "CJXL_TIMEOUT",
}
# Not per-file: the helper itself and the planning-time batch readers.
EXEMPT = {"_run_exiftool_argfile", "_read_mpg_markers", "_tool_version"}


def _exiftool_calls(src: str):
    """(line, function, timeout node) of every exiftool subprocess call."""
    rows = []

    class V(ast.NodeVisitor):
        stack = []

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node):
            name = (getattr(node.func, "id", None)
                    or getattr(node.func, "attr", None))
            seg = ast.get_source_segment(src, node) or ""
            if (name == "_run_exiftool_argfile"
                    or (name == "_run_captured" and "exiftool" in seg[:200])):
                val = next((kw.value for kw in node.keywords
                            if kw.arg == "timeout"), None)
                if val is None and name == "_run_captured" and len(node.args) >= 2:
                    val = node.args[1]
                rows.append((node.lineno, self.stack[-1] if self.stack else "",
                             val))
            self.generic_visit(node)

    V().visit(ast.parse(src))
    return rows


@pytest.mark.parametrize("script", sorted(SCRIPTS))
def test_per_file_exiftool_calls_use_the_setting(script):
    src = (REPO / f"{script}.py").read_text(encoding="utf-8")
    calls = [(ln, fn, val) for ln, fn, val in _exiftool_calls(src)
             if fn not in EXEMPT and not fn.endswith("_batch")]
    assert calls, "no per-file exiftool call found — the scan is broken"
    bad = [(ln, fn, ast.unparse(val) if val is not None else "<default 60>")
           for ln, fn, val in calls
           if not (isinstance(val, ast.Name) and val.id == "EXIFTOOL_TIMEOUT")]
    assert not bad, f"{script}: per-file exiftool calls with a fixed timeout: {bad}"


@pytest.mark.parametrize("script", sorted(SCRIPTS))
def test_exiftool_timeout_follows_the_codec_timeout(script):
    mod = importlib.import_module(script)
    assert mod.EXIFTOOL_TIMEOUT == getattr(mod, SCRIPTS[script])
    # Defined AS the codec setting, so editing that one line moves both.
    src = (REPO / f"{script}.py").read_text(encoding="utf-8")
    assert f"\nEXIFTOOL_TIMEOUT = {SCRIPTS[script]}\n" in src


def test_recompressor_names_the_tool_that_timed_out():
    import jxl_recompressor as rec
    exc = subprocess.TimeoutExpired(
        [r"C:\Tools\exiftool.exe", "-@", "args.txt"], 120)
    assert rec._timeout_detail(exc) == "exiftool timed out after 120s"
    exc = subprocess.TimeoutExpired(["djxl", "a.jxl", "b.png"], rec.CJXL_TIMEOUT)
    assert rec._timeout_detail(exc) == f"djxl timed out after {rec.CJXL_TIMEOUT}s"
