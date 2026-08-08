#!/usr/bin/env python3
"""Regressions for the round-29 audit fixes (bugs #277-#281).

Every one was reproduced against the shipped code before the fix:

  * #277 — exit 2 means both "argparse rejected this command line" and "the run
    stopped to protect your files", and the wrapper reported both as the
    second. A wrapper that emitted a flag the child does not have looked, from
    the outside, exactly like a duplicate-output abort, and sent the user
    hunting for a collision that never existed. The manifest recap made it
    worse by adding "Nothing was deleted" — which is also not something exit 2
    can promise, since the disk-full abort fires part way through a run.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_photo as wp

REPO = Path(__file__).resolve().parent.parent


def _menu():
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


# ── #277: argparse exit 2 is not a safety abort ─────────────────────────────

def test_usage_error_captured_from_child_output():
    """argparse writes '<prog>: error: ...' to stderr and exits 2."""
    menu = _menu()
    rc = menu._stream_child(
        [sys.executable, str(REPO / "jxl_tiff_decoder.py"),
         "nonexistent_input", "--provenance", "adopt"])
    assert rc == 2
    assert menu._last_child_usage_error is not None
    assert "invalid choice" in menu._last_child_usage_error


def test_safety_abort_is_not_reported_as_usage_error():
    """A child that exits 2 without argparse's error line stays a safety abort."""
    menu = _menu()
    rc = menu._stream_child(
        [sys.executable, "-c",
         "print('Aborting: duplicate output destinations'); raise SystemExit(2)"])
    assert rc == 2
    assert menu._last_child_usage_error is None


def test_usage_error_does_not_survive_into_the_next_run():
    """A stale usage error would mislabel the NEXT run's safety abort."""
    menu = _menu()
    menu._stream_child(
        [sys.executable, str(REPO / "jxl_tiff_decoder.py"),
         "nonexistent_input", "--provenance", "adopt"])
    assert menu._last_child_usage_error is not None
    menu._stream_child([sys.executable, "-c", "raise SystemExit(2)"])
    assert menu._last_child_usage_error is None


def test_usage_error_regex_ignores_ordinary_error_lines():
    """The children log plenty of lines containing 'error'; only argparse's own
    '<prog>.py: error: ...' shape counts."""
    for line in ("ERROR: required tool(s) not found in PATH: cjxl",
                 "  -> C:/photos/a.tif (error: could not read)",
                 "2026-01-01 12:00:00 | ERROR | conversion error: bad file"):
        assert wp._CHILD_USAGE_ERROR_RE.match(line) is None
    m = wp._CHILD_USAGE_ERROR_RE.match(
        "jxl_tiff_decoder.py: error: argument --provenance: invalid choice: 'adopt'")
    assert m is not None
    assert m.group(2).startswith("argument --provenance")


def test_single_run_exit2_message_names_the_usage_error(monkeypatch, capsys):
    """The message a user actually reads must say the command was rejected and
    that nothing was touched — not that a safety check fired."""
    menu = _menu()
    printed = []
    monkeypatch.setattr(menu, "_print_error", lambda m: printed.append(m))

    def _fake_stream(cmd, idle_timeout=3600):
        menu._last_child_usage_error = "argument --provenance: invalid choice: 'adopt'"
        return 2

    monkeypatch.setattr(menu, "_stream_child", _fake_stream)
    workflow = {
        "origin_format": "jxl", "dest_format": "tiff", "mode": 0,
        "input_dir": str(REPO), "output_dir": str(REPO),
        "workers": 1, "mode_config": {}, "advanced_options": {},
        "compression": "lzw", "bit_depth": 16,
    }
    menu.execute_workflow(workflow, {})
    blob = "\n".join(printed)
    assert "invalid choice" in blob
    assert "Nothing ran" in blob
    assert "safety check" not in blob


def test_manifest_exit2_does_not_promise_nothing_was_deleted():
    """Exit 2 is also the disk-full abort, which fires part way through a run,
    and completed entries did their own deleting."""
    src = (REPO / "jxl_photo.py").read_text(encoding="utf-8")
    assert "Nothing was deleted; fix the cause" not in src, (
        "the manifest abort message still promises nothing was deleted")
    assert "stay deleted" in src
