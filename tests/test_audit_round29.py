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
  * #278 — --provenance adopt exists only in the encoder, but the wizard
    offered it in every collapsing mode and all six emission sites passed the
    answer through, so a JXL->TIFF or JPEG<->JXL delete run built a command
    line that died at argparse. The decoder's own --none warning told the user
    to pass a flag the decoder does not have.
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


# ── #278: adopt is an encoder-only flag ─────────────────────────────────────

CHILD_ADOPT_SUPPORT = [
    ("jxl_tiff_encoder.py", True),
    ("jxl_tiff_decoder.py", False),
    ("jxl_jpeg_transcoder.py", False),
]


@pytest.mark.parametrize("script,supported", CHILD_ADOPT_SUPPORT)
def test_which_children_actually_accept_adopt(script, supported):
    """The predicate the wrapper trusts must match the argparse reality."""
    r = subprocess.run(
        [sys.executable, str(REPO / script), "in", "--provenance", "adopt"],
        capture_output=True, text=True)
    rejected = "invalid choice: 'adopt'" in (r.stderr + r.stdout)
    assert rejected is not supported, f"{script}: adopt support changed"


def test_supports_provenance_adopt_matches_the_children():
    assert wp._supports_provenance_adopt("tiff", "jxl") is True
    for origin, dest in (("jxl", "tiff"), ("jpeg", "jxl"), ("jxl", "jpeg"),
                         ("jxl", "png"), ("png", "jxl")):
        assert wp._supports_provenance_adopt(origin, dest) is False


@pytest.mark.parametrize("origin,dest", [("jxl", "tiff"), ("jpeg", "jxl"),
                                         ("jxl", "jpeg"), ("jxl", "png")])
def test_stored_adopt_is_downgraded_not_emitted(origin, dest, capsys):
    """A saved session or preset carrying adopt from a TIFF->JXL run must not
    be replayed at a script that has no such choice."""
    menu = _menu()
    cmd = []
    menu._append_provenance_flags(cmd, {"provenance": "adopt", "adopt_scan": False},
                                  origin, dest)
    assert "adopt" not in cmd
    assert "--no-adopt-scan" not in cmd
    assert cmd == ["--provenance", "path"], cmd


def test_adopt_still_emitted_for_the_encoder():
    menu = _menu()
    cmd = []
    menu._append_provenance_flags(cmd, {"provenance": "adopt", "adopt_scan": False},
                                  "tiff", "jxl")
    assert cmd == ["--provenance", "adopt", "--no-adopt-scan"]


def test_no_adopt_scan_is_never_emitted_without_adopt():
    """It is an encoder-only flag AND inert without adopt; the decoder's two
    emission sites used to append it for path/content runs as well."""
    menu = _menu()
    for pv in ("path", "content"):
        cmd = []
        menu._append_provenance_flags(cmd, {"provenance": pv, "adopt_scan": False},
                                      "tiff", "jxl")
        assert cmd == ["--provenance", pv]


def test_no_emission_site_passes_provenance_by_hand():
    """Six sites had already drifted; they must all go through the one helper."""
    src = (REPO / "jxl_photo.py").read_text(encoding="utf-8")
    assert "cmd.extend(['--provenance'" not in src.replace(
        "cmd.extend(['--provenance', pv])", "")


def test_wizard_builds_its_choices_from_the_predicate():
    """Gating the OFFER, not just the emission: a menu entry the target script
    cannot accept is worse than no menu entry at all. The wizard used to hand
    Prompt.ask a hardcoded three-item list for every direction."""
    src = (REPO / "jxl_photo.py").read_text(encoding="utf-8")
    assert '["path", "content", "adopt"]' not in src
    assert '(path/content/adopt)' not in src
    assert "_pv_choices = [\"path\", \"content\"] + ([\"adopt\"] if _adopt_ok else [])" in src
    assert "_adopt_ok = _supports_provenance_adopt(origin, dest)" in src


def test_decoder_none_warning_does_not_name_a_flag_it_lacks():
    """The remedy the message named was impossible to follow."""
    src = (REPO / "jxl_tiff_decoder.py").read_text(encoding="utf-8")
    assert "pass --provenance adopt" not in src
    assert "--provenance adopt has no counterpart here" in src


def test_bug_tracker_no_longer_claims_adopt_in_three_scripts():
    doc = (REPO / "docs" / "bug_tracking_since_v1.0.md").read_text(encoding="utf-8")
    row = next(l for l in doc.splitlines() if l.startswith("| 271 |"))
    assert "encoder, decoder, transcoder" not in row


@pytest.mark.parametrize("readme", [
    "README_jxl_tiff_encoder.md", "README_jxl_tiff_decoder.md",
    "README_jxl_jpeg_transcoder.md",
])
def test_provenance_is_documented_in_every_backend_readme(readme):
    doc = (REPO / "docs" / readme).read_text(encoding="utf-8")
    assert "--provenance" in doc
    if readme == "README_jxl_tiff_encoder.md":
        assert "--no-adopt-scan" in doc
        assert "path|content|adopt" in doc
    else:
        # Must say plainly that it has no adopt, since the wrapper's menu and
        # the encoder's docs both mention one.
        assert "NO `adopt`" in doc
