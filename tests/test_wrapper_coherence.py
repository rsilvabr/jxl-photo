#!/usr/bin/env python3
"""Bugs #284-#286 — three places where the wrapper's gates and its previews
disagreed with what the run actually does.

  * #284 — _confirm_lossy_delete_skipped was called AFTER the mode-99 dispatch,
    so a manifest run never reached it. The extra confirmation for the one
    combination with no provenance of any kind (lossy + --delete-skipped)
    vanished for the runs that touch the most files.
  * #285 — the manifest delete offer was keyed on mode 8 but the answer is
    run-wide: the cmd builder appends --delete-source to EVERY entry, and the
    children honour it in every mode. A manifest of mode-8 rows plus mode-3 rows
    asked about the mode-8 ones and deleted the mode-3 sources too.
  * #286 — the [D] count preview ignored the export marker for the transcoder
    directions. Bug #269 fixed this by asking the child's own finder, but the
    transcoder has no mode-6/7 finder, so JPEG<->JXL kept the raw extension
    count: "23 file(s)" for a run that touches 3.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_photo as wp

REPO = Path(__file__).resolve().parent.parent


def _menu():
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


# ── #284: the lossy gate must reach the manifest path ───────────────────────

def test_manifest_workflow_asks_the_lossy_delete_skipped_question(tmp_path, monkeypatch):
    menu = _menu()
    asked = []
    monkeypatch.setattr(menu, "_confirm_lossy_delete_skipped",
                        lambda wf: asked.append(wf))
    # Stop right after the gate: the entry loop is not what this tests.
    monkeypatch.setattr(menu, "_confirm_archive_mode", lambda: False)

    (tmp_path / "src").mkdir()
    workflow = {
        "origin_format": "jpeg", "dest_format": "jxl",
        "conversion_type": "convert_lossy", "workers": 1,
        "manifest_entries": [(str(tmp_path / "src"), str(tmp_path / "out"), 3)],
        "manifest_path": "m.csv", "mode_config": {},
        "advanced_options": {"delete_source": True, "delete_skipped": True},
    }
    menu._execute_manifest_workflow(workflow, {})
    assert asked, "the manifest path never asked the lossy delete-skipped question"


def test_the_gate_turns_delete_skipped_off_where_the_builder_can_see_it(monkeypatch):
    """It mutates advanced_options in place; the cmd builder reads that dict."""
    menu = _menu()
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    adv = {"delete_source": True, "delete_skipped": True}
    workflow = {"advanced_options": adv, "conversion_type": "convert_lossy"}
    menu._confirm_lossy_delete_skipped(workflow)
    assert adv["delete_skipped"] is False


def test_the_gate_is_asked_once_per_run(monkeypatch):
    """execute_workflow and the manifest path both call it; a repeated y/N is
    exactly what trains people to answer by reflex."""
    menu = _menu()
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    calls = []
    monkeypatch.setattr("builtins.input", lambda *a: calls.append(1) or "y")
    workflow = {"advanced_options": {"delete_source": True, "delete_skipped": True},
                "conversion_type": "convert_lossy"}
    menu._confirm_lossy_delete_skipped(workflow)
    menu._confirm_lossy_delete_skipped(workflow)
    assert len(calls) == 1


# ── #285: the manifest delete question must describe what it does ───────────

def _manifest_prompt(monkeypatch, tmp_path, entries, answer="n"):
    """Drive _wizard_run_from_manifest far enough to capture the delete prompt."""
    manifest = tmp_path / "m.csv"
    manifest.write_text("Source,Destination,Mode\n", encoding="utf-8")

    menu = _menu()
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(menu, "_pick_manifest", lambda: str(manifest))
    monkeypatch.setattr(menu, "_load_manifest_entries", lambda *a: entries)
    monkeypatch.setattr(menu, "_confirm_manifest_entries", lambda *a: True)

    printed = []
    monkeypatch.setattr("builtins.print",
                        lambda *a, **k: printed.append(" ".join(str(x) for x in a)))
    monkeypatch.setattr("builtins.input", lambda *a: answer)

    workflow = {"origin_format": "tiff", "dest_format": "jxl", "mode_config": {}}
    menu._wizard_run_from_manifest(workflow)
    return "\n".join(printed), workflow


def test_a_mixed_manifest_says_the_delete_covers_every_entry(monkeypatch, tmp_path):
    entries = [("A", "A", 8), ("B", "B", 3), ("C", "C", 8)]
    text, _ = _manifest_prompt(monkeypatch, tmp_path, entries)
    assert "ALL 3 entries" in text, text
    assert "mode 3" in text, "the rows that get deleted anyway were not named"


def test_an_all_mode8_manifest_does_not_invent_other_modes(monkeypatch, tmp_path):
    entries = [("A", "A", 8), ("B", "B", 8)]
    text, _ = _manifest_prompt(monkeypatch, tmp_path, entries)
    assert "all 2 mode-8 entries" in text, text
    assert "including the mode" not in text


def test_declining_says_every_entry_keeps_its_originals(monkeypatch, tmp_path):
    entries = [("A", "A", 8), ("B", "B", 3)]
    text, workflow = _manifest_prompt(monkeypatch, tmp_path, entries)
    assert not workflow.get("delete_source")
    assert "Every entry will run WITHOUT deleting" in text


def test_accepting_still_arms_the_delete(monkeypatch, tmp_path):
    entries = [("A", "A", 8), ("B", "B", 3)]
    _, workflow = _manifest_prompt(monkeypatch, tmp_path, entries, answer="y")
    assert workflow["delete_source"] is True


# ── #286: the count preview must apply the marker filter ────────────────────

def _tree_with_marker(root: Path, exts=("jpg",)):
    """3 files inside _EXPORT, 20 outside — the shape of #269."""
    for ext in exts:
        (root / "_EXPORT" / "shoot").mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (root / "_EXPORT" / "shoot" / f"in{i}.{ext}").write_bytes(b"x")
        (root / "other").mkdir(parents=True, exist_ok=True)
        for i in range(20):
            (root / "other" / f"out{i}.{ext}").write_bytes(b"x")


@pytest.mark.parametrize("mode", [6, 7])
def test_transcoder_count_preview_honours_the_export_marker(tmp_path, mode):
    _tree_with_marker(tmp_path)
    menu = _menu()
    workflow = {"origin_format": "jpeg", "dest_format": "jxl",
                "input_dir": str(tmp_path), "mode_config": {}}
    n = menu._count_origin_files(workflow, mode)
    assert n == 3, f"mode {mode} counted {n}, so the marker filter was ignored"


def test_transcoder_count_preview_is_unchanged_for_ordinary_modes(tmp_path):
    """The resolver returns a path for every mode but 6/7, so the filter must
    not quietly drop anything elsewhere."""
    _tree_with_marker(tmp_path)
    menu = _menu()
    workflow = {"origin_format": "jpeg", "dest_format": "jxl",
                "input_dir": str(tmp_path), "mode_config": {}}
    assert menu._count_origin_files(workflow, 3) == 23


def test_a_custom_marker_is_used_and_then_restored(tmp_path):
    import jxl_jpeg_transcoder as tr
    (tmp_path / "MINE_x" / "s").mkdir(parents=True)
    for i in range(4):
        (tmp_path / "MINE_x" / "s" / f"a{i}.jpg").write_bytes(b"x")
    (tmp_path / "plain").mkdir()
    (tmp_path / "plain" / "b.jpg").write_bytes(b"x")

    before = tr.EXPORT_MARKER
    menu = _menu()
    workflow = {"origin_format": "jpeg", "dest_format": "jxl",
                "input_dir": str(tmp_path), "mode_config": {"export_marker": "MINE"}}
    assert menu._count_origin_files(workflow, 6) == 4
    assert tr.EXPORT_MARKER == before, "the preview leaked its marker into the module"
