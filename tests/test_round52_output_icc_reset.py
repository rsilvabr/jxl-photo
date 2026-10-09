#!/usr/bin/env python3
"""Round 52 / #529 — the recompressor's OUTPUT_ICC is reset between runs.

main() assigned OUTPUT_ICC only when --output-icc was given, and OUTPUT_ICC
was missing from _RUN_DEFAULTS, so a second main() in the same process
without the flag inherited the first run's target and planned a
colour-converted DERIVATIVE. Only an in-process caller could hit it (the
wrapper runs each script as a subprocess); found while reviewing
AI_tools/261009_DeepSeek_report_cjxl-slots.md.

Pre-fix proof: run this file against `git show 088b267:jxl_recompressor.py`
(the second run still sees OUTPUT_ICC == "sRGB" and DERIVATIVE True).
"""

import ast
import re
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_recompressor as rec

# main() leaves this run state set when it returns; restore it for the tests
# that run after this module.
_MAIN_STATE = (
    "OUTPUT_ICC", "_OUTPUT_ICC_LABEL", "_OUTPUT_ICC_BYTES", "_OUTPUT_ICC_PATH",
    "_SRGB_ICC_PATH", "_FORCE_REDERIVE", "RESIZE_MODE", "RESIZE_VALUE",
    "ALLOW_UPSCALE", "SHARPEN", "SHARPEN_SIGMA", "SHARPEN_GAIN",
    "SHARPEN_THRESHOLD", "DERIVATIVE", "_DERIVED_LABEL",
)


@pytest.fixture(autouse=True)
def _restore_main_state():
    saved = {name: getattr(rec, name) for name in _MAIN_STATE if hasattr(rec, name)}
    yield
    for name, value in saved.items():
        setattr(rec, name, value)


def _shipped_output_icc():
    """The OUTPUT_ICC setting as written at the top of the script (the module
    global itself may already carry a previous run's value)."""
    src = Path(rec.__file__).read_text(encoding="utf-8")
    return ast.literal_eval(re.search(r"^OUTPUT_ICC = (.+)$", src, re.M).group(1))


def _run_main(monkeypatch, tmp_path, extra_argv, seen):
    src_dir = tmp_path / "in"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "a.jxl").write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)

    def fake_pg(items, workers):
        seen.append((rec.OUTPUT_ICC, rec.DERIVATIVE))
        return ({str(it["src"]): ("ok", str(it["final"])) for it in items},
                {str(it["src"]) for it in items})

    monkeypatch.setattr(rec, "process_group", fake_pg)
    monkeypatch.setattr(
        rec, "_read_encode_params_batch",
        lambda paths: {str(p): {"desc": "", "software": "", "params": None,
                                "gen": 0, "pixels": 0} for p in paths})
    monkeypatch.setattr(rec, "emit_summary_json", lambda *a, **k: None)
    monkeypatch.setattr(rec, "_get_cjxl_cmd", lambda: "cjxl")
    monkeypatch.setattr(shutil, "which", lambda c: "C:/tools/x")
    monkeypatch.setattr(
        sys, "argv",
        ["jxl_recompressor.py", str(src_dir), str(tmp_path / "out"), "--mode", "2",
         "--workers", "1", "--on-unknown", "convert", "--no-preflight"]
        + list(extra_argv))
    try:
        rec.main()
    except SystemExit as e:
        assert e.code in (0, None), f"unexpected exit {e.code}"


def test_second_run_without_output_icc_is_not_a_derivative(tmp_path, monkeypatch):
    seen = []
    _run_main(monkeypatch, tmp_path / "first", ["--output-icc", "sRGB"], seen)
    _run_main(monkeypatch, tmp_path / "second", [], seen)

    shipped = _shipped_output_icc()
    assert seen[0] == ("sRGB", True)
    assert seen[1] == (shipped, bool(shipped)), (
        f"the second run inherited --output-icc from the first: {seen[1]}")
