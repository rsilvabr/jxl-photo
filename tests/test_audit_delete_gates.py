#!/usr/bin/env python3
"""Regressions for the delete gates the v1.8.4 audit found holes in.

The wrapper promises that a scheduled `--run-preset` run can never delete
originals on its own. Three ways around that gate existed:

  * `--delete-source` typed into the free-form EXPERT FLAGS field. Expert flags
    go to the child's argv verbatim and never touch advanced_options, which is
    the only thing the gates inspected. With `--delete-confirm-off` next to it
    the child does not ask either, so a Task Scheduler run deleted source TIFFs
    with nobody confirming anything.
  * A MANIFEST preset (mode 99) whose entries are mode 8. The gate tested
    `int(last_mode) == 8`, and 99 is not 8, so the run fell through to the
    manifest executor — which was safe (EOF on the HHMM prompt) but only after
    printing an interactive confirmation prompt into the scheduler log.
  * A manifest entry containing '..' was skipped with a log warning while the
    run continued. The end-of-run summary counts only the entries that loaded,
    so a dropped folder was indistinguishable from one that synced cleanly.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_photo as wp


STATUS = {k: True for k in
          ("cjxl", "djxl", "exiftool", "magick", "tifffile", "pillow", "imagecodecs")}


@pytest.fixture
def menu(tmp_path, monkeypatch):
    """A menu on a throwaway config — never the user's real one."""
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


@pytest.fixture
def launched(monkeypatch):
    """Capture every child argv the wrapper would run, and run none of them."""
    calls = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600: (calls.append(list(map(str, cmd))), 0)[1])
    return calls


def _out(capsys) -> str:
    """Captured stdout with whitespace collapsed.

    rich wraps at the terminal width, so a phrase this file asserts on can be
    split across two lines. Collapsing first keeps the assertions about the
    MESSAGE rather than about where rich happened to break it.
    """
    return " ".join(capsys.readouterr().out.split())


def _session(**over):
    s = {n: None for n in wp.ToolConfig.__dataclass_fields__ if n.startswith("last_")}
    s.update({
        "last_output_mode": "8",
        "last_workers": 4,
        "last_effort": 7,
        "last_distance": 0.1,
        "last_origin_format": "tiff",
        "last_dest_format": "jxl",
        "last_conversion_type": "jxl_tiff_encoder",
        "last_advanced_options": {"overwrite": False, "sync": True},
    })
    s.update(over)
    return s


# --------------------------------------------------------------------------
# _flags_request_delete
# --------------------------------------------------------------------------

@pytest.mark.parametrize("flags", [
    "--delete-source",
    "--delete-source --delete-confirm-off",
    "--effort 9 --delete-source",
    '--staging "E:\\my dir" --delete-source',
])
def test_delete_flag_is_detected(flags):
    assert wp._flags_request_delete(flags) is True


@pytest.mark.parametrize("flags", [
    None, "", "--effort 9", "--staging E:\\temp",
    # Must not fire on a flag that merely starts with the same text.
    "--delete-source-never",
])
def test_non_delete_flags_are_not_flagged(flags):
    assert wp._flags_request_delete(flags) is False


# --------------------------------------------------------------------------
# --run-preset (unattended)
# --------------------------------------------------------------------------

def test_expert_flag_delete_is_refused_unattended(menu, launched, tmp_path, capsys):
    """The hole: delete requested via expert flags, gate never fired.

    Asserting on the REFUSAL TEXT, not just on a False return: a wrapper that
    bailed for some unrelated reason (a missing child script, say) would also
    return False and launch nothing, and this test would pass without the gate
    existing at all.
    """
    src = tmp_path / "photos"
    src.mkdir()
    session = _session(last_input_dir=str(src),
                       last_expert_flags="--delete-source --delete-confirm-off")

    ok = menu._run_saved_session(session, STATUS,
                                 answers={"overwrite": False, "dry_run": False})

    assert ok is False
    assert launched == [], "child was launched with --delete-source unattended"
    out = _out(capsys)
    assert "cannot be given unattended" in out
    assert "expert flags" in out


def test_expert_flag_delete_survives_a_mode_override(menu, launched, tmp_path, capsys):
    """argparse takes the LAST --mode, so expert flags can override the stored
    one. The gate must not trust the stored mode when the flags ask to delete."""
    src = tmp_path / "photos"
    src.mkdir()
    session = _session(last_output_mode="0", last_input_dir=str(src),
                       last_expert_flags="--mode 8 --delete-source --delete-confirm-off")

    ok = menu._run_saved_session(session, STATUS,
                                 answers={"overwrite": False, "dry_run": False})

    assert ok is False
    assert launched == []
    assert "cannot be given unattended" in _out(capsys)


def test_expert_flag_delete_is_allowed_when_simulating(menu, launched, tmp_path):
    """--dry-run converts nothing, so the gate must not block it."""
    src = tmp_path / "photos"
    src.mkdir()
    session = _session(last_input_dir=str(src), last_expert_flags="--delete-source")

    menu._run_saved_session(session, STATUS,
                            answers={"overwrite": False, "dry_run": True})

    assert len(launched) == 1
    assert "--dry-run" in launched[0]


def test_manifest_preset_of_mode_8_entries_is_refused_unattended(menu, launched, tmp_path, capsys):
    """mode 99 is not 8: the gate missed it and the run reached the manifest
    executor, which printed an HHMM prompt into the scheduler log."""
    a = tmp_path / "shootA"; a.mkdir()
    b = tmp_path / "shootB"; b.mkdir()
    manifest = tmp_path / "m.csv"
    manifest.write_text(
        "Source,Destination,Mode,Direction\n"
        f"{a},{a},8,tiff2jxl\n"
        f"{b},{b},8,tiff2jxl\n", encoding="utf-8-sig")

    session = _session(last_output_mode="99", last_input_dir=str(tmp_path),
                       last_manifest_path=str(manifest),
                       last_advanced_options={"overwrite": False, "sync": True,
                                              "delete_source": True})

    ok = menu._run_saved_session(session, STATUS,
                                 answers={"overwrite": False, "dry_run": False})

    assert ok is False
    assert launched == []
    out = _out(capsys)
    assert "cannot be given unattended" in out
    # The whole point: no interactive prompt is dumped into the scheduler log.
    assert "DELETE ORIGINALS MODE" not in out


def test_plain_mode_8_preset_still_refused_unattended(menu, launched, tmp_path, capsys):
    """The original guard must keep working."""
    src = tmp_path / "photos"
    src.mkdir()
    session = _session(last_input_dir=str(src),
                       last_advanced_options={"overwrite": False, "sync": True,
                                              "delete_source": True})

    ok = menu._run_saved_session(session, STATUS,
                                 answers={"overwrite": False, "dry_run": False})

    assert ok is False
    assert launched == []
    assert "mode 8 + delete_source" in _out(capsys)


def test_harmless_preset_still_runs_unattended(menu, launched, tmp_path):
    """The gates must not become a blanket refusal."""
    src = tmp_path / "photos"
    src.mkdir()
    session = _session(last_output_mode="0", last_input_dir=str(src),
                       last_expert_flags="--effort 9")

    ok = menu._run_saved_session(session, STATUS,
                                 answers={"overwrite": False, "dry_run": False})

    assert ok is True
    assert len(launched) == 1
    assert "--delete-source" not in launched[0]


# --------------------------------------------------------------------------
# Attended menu run: expert-flag deletion must still charge the HHMM token
# --------------------------------------------------------------------------

def test_expert_flag_delete_is_gated_in_the_menu_too(menu, launched, monkeypatch, tmp_path):
    src = tmp_path / "photos"
    src.mkdir()
    asked = []
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode",
                        lambda self: (asked.append(True), False)[1])

    workflow = {
        'mode': 0, 'input_dir': str(src), 'workers': 4, 'effort': 7,
        'distance': 0.1, 'origin_format': 'tiff', 'dest_format': 'jxl',
        'conversion_type': 'jxl_tiff_encoder', 'use_ram': True,
        'advanced_options': {'overwrite': False, 'sync': True},
        'expert_flags': '--delete-source --delete-confirm-off',
    }

    assert menu.execute_workflow(workflow, STATUS) is False
    assert asked, "HHMM confirmation was never asked for an expert-flag delete"
    assert launched == []


# --------------------------------------------------------------------------
# Manifest path traversal
# --------------------------------------------------------------------------

def test_traversal_entry_refuses_the_whole_manifest(menu, tmp_path, capsys):
    good = tmp_path / "ok"
    good.mkdir()
    manifest = tmp_path / "m.csv"
    manifest.write_text(
        "Source,Destination,Mode,Direction\n"
        f"{good},{good},0,tiff2jxl\n"
        f"{tmp_path}\\..\\evil,{good},0,tiff2jxl\n", encoding="utf-8-sig")

    entries = menu._load_manifest_entries(str(manifest), "tiff", "jxl")

    assert entries is None, "traversal row was skipped and the run shrank silently"
    assert "Path traversal" in capsys.readouterr().out


def test_clean_manifest_still_loads(menu, tmp_path):
    good = tmp_path / "ok"
    good.mkdir()
    manifest = tmp_path / "m.csv"
    manifest.write_text(
        "Source,Destination,Mode,Direction\n"
        f"{good},{good},0,tiff2jxl\n", encoding="utf-8-sig")

    entries = menu._load_manifest_entries(str(manifest), "tiff", "jxl")

    assert entries is not None and len(entries) == 1
