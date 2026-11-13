"""The manifest's own pre-run steps, one by one and in order (refactor F4.7).

_execute_manifest_workflow refuses or asks before the first child for six
reasons that only a whole manifest shows: a bit-exact JPEG recovery asked to
resize or rename (a _GATE_RULES rule), a mode-7 row whose subfolder cannot be
told, a bad output folder name, two Sources that overlap, two rows writing
the same output, and one row's output being another row's Source. Each one
is pinned here: what it refuses unattended, what an attended "no" does, and
which message comes first when a manifest has two problems.

Every folder is hostile (Japanese, brackets, a space, %): these steps compare
paths.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_photo as wp  # noqa: E402

HOSTILE = "250118_吾妻山公園 [FINAL] 100%off %d"


def _run(monkeypatch, tmp_path, capsys, rows, *, origin="jxl", dest="png",
         conv="jxl_to_png", advanced=None, row_options=None, unattended=False,
         answer="n", mode_config=None):
    """Drive _execute_manifest_workflow with the children mocked out. rows:
    (source dir, dest dir, mode); each source gets one dummy file. Returns
    (ok, child commands, printed text)."""
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    cmds = []
    monkeypatch.setattr(wp.InteractiveMenu, "_run_subprocess",
                        lambda self, cmd: (cmds.append(cmd), 0)[1])
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_lossy_delete_skipped",
                        lambda self, wf: None)
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode", lambda self: True)
    monkeypatch.setattr(wp.InteractiveMenu, "_render_manifest_summary",
                        lambda self, *a, **k: None)
    monkeypatch.setattr(wp.FolderAnalyzer, "detect_mode_for_entry",
                        lambda self, s, d, original_mode=0: original_mode)
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp, "console", None)
    monkeypatch.setattr("builtins.input", lambda *a, **k: answer)
    ext = {"jxl": ".jxl", "tiff": ".tif", "jpeg": ".jpg"}[origin]
    entries = []
    for i, (src, dst, mode) in enumerate(rows):
        src.mkdir(parents=True, exist_ok=True)
        (src / f"DSC_000{i} (2){ext}").write_bytes(b"x")
        entries.append((str(src), str(dst) if dst else "", mode))
    wf = {
        "origin_format": origin, "dest_format": dest, "mode": 99,
        "workers": 1, "quality": 95, "bit_depth": 16, "staging": None,
        "dry_run": False, "mode_config": dict(mode_config or {}),
        "conversion_type": conv, "icc_profile": None, "distance": 1.0,
        "effort": 7, "advanced_options": dict(advanced or {}),
        "manifest_entries": entries, "unattended": unattended,
    }
    if row_options is not None:
        wf["manifest_row_options"] = row_options
    ok = menu._execute_manifest_workflow(wf, {})
    return ok, cmds, " ".join(capsys.readouterr().out.split())


# --- a bit-exact JPEG recovery (_GATE_RULES, "manifest") ---------------------

@pytest.mark.parametrize("opts,why", [
    ({"resize_mode": "long", "resize_value": 100, "sharpen": "none"}, "resized/sharpened"),
    ({"sharpen": "screen"}, "resized/sharpened"),
    ({"rename_from": "ProPhoto", "rename_to": "sRGB"}, "keeps the original file names"),
])
def test_a_lossless_recovery_row_cannot_resize_or_rename(tmp_path, monkeypatch, capsys,
                                                         opts, why):
    root = tmp_path / HOSTILE
    ok, cmds, out = _run(monkeypatch, tmp_path, capsys, [(root / "A", root / "oA", 2)],
                         dest="jpeg", conv="jxl_to_jpeg_lossless", row_options=[opts])
    assert not ok and not cmds
    assert why in out and "row(s): 1" in out


def test_a_lossless_recovery_row_only_warns_about_output_icc(tmp_path, monkeypatch, capsys):
    root = tmp_path / HOSTILE
    ok, cmds, out = _run(monkeypatch, tmp_path, capsys, [(root / "A", root / "oA", 2)],
                         dest="jpeg", conv="jxl_to_jpeg_lossless",
                         row_options=[{"output_icc": "srgb"}])
    assert ok and len(cmds) == 1
    assert "OutputICC is ignored by a bit-exact JPEG reconstruction" in out


# --- mode 7 without a subfolder --------------------------------------------

def test_a_mode7_row_without_a_subfolder_is_refused_unattended(tmp_path, monkeypatch, capsys):
    src = tmp_path / HOSTILE / "no marker here"
    ok, cmds, out = _run(monkeypatch, tmp_path, capsys, [(src, "", 7)], origin="tiff",
                         dest="jxl", conv="tiff_to_jxl", unattended=True)
    assert not ok and not cmds
    assert "Refusing to run unattended: write the mode-7 Sources" in out


@pytest.mark.parametrize("answer,runs", [("n", False), ("y", True)])
def test_a_mode7_row_without_a_subfolder_asks_when_attended(tmp_path, monkeypatch, capsys,
                                                             answer, runs):
    src = tmp_path / HOSTILE / "no marker here"
    ok, cmds, out = _run(monkeypatch, tmp_path, capsys, [(src, "", 7)], origin="tiff",
                         dest="jxl", conv="tiff_to_jxl", answer=answer)
    assert "subfolder cannot be determined" in out
    assert ok is runs and bool(cmds) is runs


def test_a_mode7_subfolder_is_derived_from_the_sources(tmp_path, monkeypatch, capsys):
    """One subfolder named by every mode-7 Source: it reaches the child."""
    src = tmp_path / HOSTILE / "2025_EXPORT" / "TIFF16 [a]"
    ok, cmds, out = _run(monkeypatch, tmp_path, capsys, [(src, "", 7)], origin="tiff",
                         dest="jxl", conv="tiff_to_jxl")
    assert ok and len(cmds) == 1
    assert "processing only subfolder 'TIFF16 [a]'" in out
    i = cmds[0].index("--export-subfolder")
    assert cmds[0][i + 1] == "TIFF16 [a]"


# --- overlapping Sources ----------------------------------------------------

def test_nested_sources_are_refused_unattended(tmp_path, monkeypatch, capsys):
    root = tmp_path / HOSTILE
    rows = [(root, tmp_path / "o1", 2), (root / "sub [b]", tmp_path / "o2", 2)]
    ok, cmds, out = _run(monkeypatch, tmp_path, capsys, rows, unattended=True)
    assert not ok and not cmds
    assert "duplicate or nested Source folders" in out
    assert "split the manifest so each folder is covered by exactly one entry" in out


@pytest.mark.parametrize("answer,runs", [("n", False), ("y", True)])
def test_nested_sources_ask_when_attended(tmp_path, monkeypatch, capsys, answer, runs):
    root = tmp_path / HOSTILE
    rows = [(root, tmp_path / "o1", 2), (root / "sub [b]", tmp_path / "o2", 2)]
    ok, cmds, _out = _run(monkeypatch, tmp_path, capsys, rows, answer=answer)
    assert ok is runs and (len(cmds) == 2) is runs


# --- one row's output is another row's Source --------------------------------

def test_an_output_feeding_another_source_is_refused_unattended(tmp_path, monkeypatch,
                                                                 capsys):
    root = tmp_path / HOSTILE
    rows = [(root / "A", root / "B out", 2), (root / "B out", root / "C", 2)]
    ok, cmds, out = _run(monkeypatch, tmp_path, capsys, rows, unattended=True)
    assert not ok and not cmds
    assert "chain outputs into inputs" in out
    assert "reads folders that another entry of the same run writes" in out


@pytest.mark.parametrize("answer,runs", [("n", False), ("y", True)])
def test_an_output_feeding_another_source_asks_when_attended(tmp_path, monkeypatch, capsys,
                                                              answer, runs):
    root = tmp_path / HOSTILE
    rows = [(root / "A", root / "B out", 2), (root / "B out", root / "C", 2)]
    ok, cmds, _out = _run(monkeypatch, tmp_path, capsys, rows, answer=answer)
    assert ok is runs and (len(cmds) == 2) is runs


# --- the order ----------------------------------------------------------------

def test_the_overlap_is_reported_before_the_chain(tmp_path, monkeypatch, capsys):
    """A manifest with both problems, unattended: the overlap step runs first
    and stops the run; the chain step never speaks."""
    root = tmp_path / HOSTILE
    rows = [(root / "A", root / "A" / "out [x]", 2), (root / "A" / "out [x]", tmp_path / "o", 2)]
    ok, cmds, out = _run(monkeypatch, tmp_path, capsys, rows, unattended=True)
    assert not ok and not cmds
    assert "duplicate or nested Source folders" in out
    assert "chain outputs into inputs" not in out


def test_the_lossless_rule_is_reported_before_the_overlap(tmp_path, monkeypatch, capsys):
    root = tmp_path / HOSTILE
    rows = [(root, tmp_path / "o1", 2), (root / "sub [b]", tmp_path / "o2", 2)]
    ok, cmds, out = _run(monkeypatch, tmp_path, capsys, rows, dest="jpeg",
                         conv="jxl_to_jpeg_lossless", unattended=True,
                         row_options=[{"sharpen": "screen"}, {}])
    assert not ok and not cmds
    assert "resized/sharpened" in out
    assert "duplicate or nested Source folders" not in out
