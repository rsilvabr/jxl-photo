#!/usr/bin/env python3
"""Manifest per-row export columns: ExportMarker, ExportSubfolder,
ExportJxlFolder.

The three columns live after Direction (any order, case-insensitive like the
others). An EMPTY cell keeps the run's value — unlike the five derivative
columns, where empty means "not applied" — so filling one row never resets the
others. A filled cell overrides the run's marker/subfolder/output folder for
that row only, all the way down to the child's own finder and resolver (the
collision mirror imports the child and must run each row under its own trio).
ExportJxlFolder is only accepted on tiff2jxl/jxl2jxl; the decoder and the
transcoder have no such flag.
"""

import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_photo as wp

REPO = Path(__file__).resolve().parent.parent

_EXPORT_HEADER = ["ExportMarker", "ExportSubfolder", "ExportJxlFolder"]

_ALL_DIRECTIONS = [
    ("tiff2jxl", "tiff", "jxl"),
    ("jxl2tiff", "jxl", "tiff"),
    ("jpeg2jxl", "jpeg", "jxl"),
    ("jxl2jpeg", "jxl", "jpeg"),
    ("jxl2png", "jxl", "png"),
    ("jxl2jxl", "jxl", "jxl"),
]


def _menu():
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


def _write_manifest(path, rows, direction="tiff2jxl", header=None):
    hdr = header or (["Source", "Destination", "Mode", "Direction"]
                     + _EXPORT_HEADER)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(hdr)
        for row in rows:
            w.writerow(row)


def _load(menu, manifest, direction="tiff2jxl"):
    origin, dest = direction.split("2")
    row_options = []
    entries = menu._load_manifest_entries(str(manifest), origin, dest,
                                          row_options=row_options)
    return entries, row_options


def _run_manifest(monkeypatch, tmp_path, rows, origin="tiff", dest="jxl",
                  conv="jxl_tiff_encoder", advanced=None, row_options=None,
                  mode_config=None, mock_detect=True):
    """Drive _execute_manifest_workflow with the child processes mocked out.

    rows: list of (source-dir, dest-dir, mode) — dirs are created, each with
    one dummy source file so the collision scan walks something real.
    Returns (ok, cmds). mock_detect=False keeps the real mode detection (for
    the legacy-manifest per-row marker test)."""
    menu = _menu()
    cmds = []
    monkeypatch.setattr(wp.InteractiveMenu, "_run_subprocess",
                        lambda self, cmd: (cmds.append(cmd), 0)[1])
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_lossy_delete_skipped",
                        lambda self, wf: None)
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode",
                        lambda self: True)
    if mock_detect:
        monkeypatch.setattr(wp.FolderAnalyzer, "detect_mode_for_entry",
                            lambda self, s, d, original_mode=0: original_mode)
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp, "console", None)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")

    ext = {"jxl": ".jxl", "tiff": ".tif", "jpeg": ".jpg"}[origin]
    entries = []
    for i, (src, dst, mode) in enumerate(rows):
        src.mkdir(parents=True, exist_ok=True)
        (src / f"foto{i}{ext}").write_bytes(b"x")
        entries.append((str(src), str(dst), mode))
    wf = {
        "origin_format": origin, "dest_format": dest, "mode": 99,
        "workers": 2, "quality": 95, "bit_depth": 16, "staging": None,
        "dry_run": False, "mode_config": dict(mode_config or {}),
        "conversion_type": conv, "icc_profile": None, "distance": 1.0,
        "effort": 7, "advanced_options": dict(advanced or {}),
        "manifest_entries": entries,
    }
    if row_options is not None:
        wf["manifest_row_options"] = row_options
    ok = menu._execute_manifest_workflow(wf, {"cjxl": True})
    return ok, cmds


# ── loading ─────────────────────────────────────────────────────────────────

def test_export_columns_load_into_row_options(tmp_path):
    src, dst = tmp_path / "A", tmp_path / "out"
    m = tmp_path / "m.csv"
    _write_manifest(m, [(src, dst, 7, "tiff2jxl", "_PRINT", "TIFF16",
                         "PRINT_JXL")])
    entries, ro = _load(_menu(), m)
    assert entries == [(str(src), str(dst), 7)]
    assert ro == [{"export_marker": "_PRINT", "export_subfolder": "TIFF16",
                   "export_jxl_folder": "PRINT_JXL"}]


def test_manifest_option_note_mentions_the_export_semantics(tmp_path, capsys):
    src, dst = tmp_path / "A", tmp_path / "out"
    m = tmp_path / "m.csv"
    _write_manifest(m, [(src, dst, 6, "tiff2jxl", "_PRINT", "", "")])
    entries, _ = _load(_menu(), m)
    assert entries is not None
    out = " ".join(capsys.readouterr().out.split())      # undo console wrapping
    assert "ExportMarker, ExportSubfolder, ExportJxlFolder" in out
    assert ("an empty ExportMarker/ExportSubfolder/ExportJxlFolder cell keeps "
            "the run's value") in out
    # No derivative column here, so nothing may claim "empty = not applied".
    assert "not applied" not in out


def test_manifest_option_note_puts_excludefolders_with_the_kept_columns(tmp_path,
                                                                        capsys):
    """Round 43 follow-up: the note said "an empty cell means 'not applied'" for
    EVERY column, but an empty ExcludeFolders (like an empty Export*) cell keeps
    the run's value — the opposite. The user's own upgraded manifest printed it."""
    src = tmp_path / "A"
    m = tmp_path / "m.csv"
    m.write_text("Source,Destination,Mode,Direction,ExcludeFolders,ExportMarker,"
                 "ExportSubfolder,ExportJxlFolder\n"
                 f"{src},{src},6,tiff2jxl,,,,\n", encoding="utf-8-sig")
    entries, ro = _load(_menu(), m)
    assert entries is not None and ro == [{}]
    out = " ".join(capsys.readouterr().out.split())
    assert ("an empty ExcludeFolders/ExportMarker/ExportSubfolder/ExportJxlFolder "
            "cell keeps the run's value") in out
    assert "not applied" not in out
    assert "ExcludeFolders '-' means no exclusion on that row" in out


def test_manifest_option_note_keeps_not_applied_for_derivative_columns(tmp_path,
                                                                       capsys):
    src = tmp_path / "A"
    m = tmp_path / "m.csv"
    m.write_text("Source,Destination,Mode,Direction,OutputICC,Resize,Sharpen,"
                 "RenameFrom,RenameTo,ExportMarker\n"
                 f"{src},{tmp_path / 'o'},2,jxl2jxl,sRGB,,,,,\n", encoding="utf-8-sig")
    entries, _ = _load(_menu(), m, direction="jxl2jxl")
    assert entries is not None
    out = " ".join(capsys.readouterr().out.split())
    assert ("an empty OutputICC/Resize/Sharpen/RenameFrom/RenameTo cell means "
            "'not applied'") in out
    assert "an empty ExportMarker cell keeps the run's value" in out


def test_unknown_column_is_still_refused(tmp_path, capsys):
    src, dst = tmp_path / "A", tmp_path / "out"
    m = tmp_path / "m.csv"
    _write_manifest(m, [(src, dst, 6, "tiff2jxl", "_PRINT")],
                    header=["Source", "Destination", "Mode", "Direction",
                            "ExportFolders"])
    entries, _ = _load(_menu(), m)
    assert entries is None
    assert "ExportFolders" in capsys.readouterr().out


def test_empty_export_cells_write_no_key(tmp_path):
    src, dst = tmp_path / "A", tmp_path / "out"
    m = tmp_path / "m.csv"
    _write_manifest(m, [(src, dst, 6, "tiff2jxl", "", "", "")])
    entries, ro = _load(_menu(), m)
    assert entries == [(str(src), str(dst), 6)]
    assert ro == [{}]


def test_empty_export_cells_leave_the_run_config_untouched():
    """Empty is 'keep the run's value': _row_effective_options must not clear
    a mode_config the wizard/preset answered."""
    mc = {"export_jxl_folder": "PRINT_JXL", "export_subfolder": "TIFF16"}
    workflow = {"mode_config": mc}
    wf_row, _ = wp._row_effective_options(workflow, {}, {}, "tiff", "jxl")
    assert wf_row["mode_config"] == mc
    assert workflow["mode_config"] == mc


@pytest.mark.parametrize("direction,origin,dest", _ALL_DIRECTIONS)
def test_marker_and_subfolder_are_accepted_in_every_direction(
        tmp_path, direction, origin, dest):
    src, dst = tmp_path / "A", tmp_path / "out"
    m = tmp_path / "m.csv"
    _write_manifest(m, [(src, dst, 7, direction, "_PRINT", "TIFF16", "")],
                    direction=direction)
    entries, ro = _load(_menu(), m, direction)
    assert entries is not None
    assert ro[0]["export_marker"] == "_PRINT"
    assert ro[0]["export_subfolder"] == "TIFF16"


@pytest.mark.parametrize("direction", ["jxl2tiff", "jxl2jpeg", "jxl2png",
                                       "jpeg2jxl"])
def test_export_jxl_folder_is_refused_outside_tiff2jxl_and_jxl2jxl(
        tmp_path, direction, capsys):
    src, dst = tmp_path / "A", tmp_path / "out"
    m = tmp_path / "m.csv"
    _write_manifest(m, [(src, dst, 6, direction, "", "", "PRINT_JXL")],
                    direction=direction)
    entries, _ = _load(_menu(), m, direction)
    assert entries is None
    assert "ExportJxlFolder" in capsys.readouterr().out


@pytest.mark.parametrize("col,val", [
    ("ExportMarker", "a/b"),
    ("ExportSubfolder", ".."),
    ("ExportJxlFolder", "."),
])
def test_invalid_export_cell_refuses_the_manifest(tmp_path, col, val, capsys):
    src, dst = tmp_path / "A", tmp_path / "out"
    cells = {c: "" for c in _EXPORT_HEADER}
    cells[col] = val
    m = tmp_path / "m.csv"
    _write_manifest(m, [(src, dst, 6, "tiff2jxl", cells["ExportMarker"],
                         cells["ExportSubfolder"], cells["ExportJxlFolder"])])
    entries, _ = _load(_menu(), m)
    assert entries is None
    assert col in capsys.readouterr().out


def test_subfolder_on_a_non_mode_7_row_is_refused(tmp_path, capsys):
    """--export-subfolder only means anything in mode 7; on any other explicit
    Mode a filled ExportSubfolder cell refuses the manifest (a legacy row
    without a Mode cell stays accepted — its mode is resolved downstream)."""
    src, dst = tmp_path / "A", tmp_path / "out"
    hdr = ["Source", "Destination", "Mode", "Direction", "ExportSubfolder"]

    m6 = tmp_path / "m6.csv"
    _write_manifest(m6, [(src, dst, 6, "tiff2jxl", "TIFF16")], header=hdr)
    entries, _ = _load(_menu(), m6)
    assert entries is None
    out = capsys.readouterr().out
    assert "column ExportSubfolder only applies to Mode 7 rows" in out
    assert "row is mode 6" in out

    m7 = tmp_path / "m7.csv"
    _write_manifest(m7, [(src, dst, 7, "tiff2jxl", "TIFF16")], header=hdr)
    entries, ro = _load(_menu(), m7)
    assert entries is not None
    assert ro == [{"export_subfolder": "TIFF16"}]

    legacy = tmp_path / "legacy.csv"
    _write_manifest(legacy, [(src, dst, None, "tiff2jxl", "TIFF16")], header=hdr)
    entries, ro = _load(_menu(), legacy)
    assert entries is not None
    assert ro == [{"export_subfolder": "TIFF16"}]


# ── merge per row ───────────────────────────────────────────────────────────

def test_row_override_wins_and_the_run_dict_is_copied():
    mc = {"export_marker": "_EXPORT", "export_subfolder": "TIFF16",
          "export_jxl_folder": "16B_JXL"}
    workflow = {"mode_config": mc}
    wf_row, _ = wp._row_effective_options(
        workflow, {},
        {"export_marker": "_PRINT", "export_jxl_folder": "PRINT_JXL"},
        "tiff", "jxl")
    assert wf_row["mode_config"] == {"export_marker": "_PRINT",
                                     "export_subfolder": "TIFF16",
                                     "export_jxl_folder": "PRINT_JXL"}
    assert workflow["mode_config"] == mc, "the run's mode_config was mutated"


def test_row_options_summary_shows_the_export_overrides():
    summary = wp._row_options_summary({"export_marker": "_PRINT",
                                       "export_subfolder": "TIFF16",
                                       "export_jxl_folder": "PRINT_JXL"})
    assert summary == "marker:_PRINT · sub:TIFF16 · out:PRINT_JXL"


# ── commands: the row's trio reaches only its own child ─────────────────────

def test_row_export_trio_reaches_only_its_own_command(tmp_path, monkeypatch):
    rows = [(tmp_path / "A", tmp_path / "oA", 6),
            (tmp_path / "B", tmp_path / "oB", 6)]
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, rows,
        row_options=[{"export_marker": "X_PRINT",
                      "export_jxl_folder": "PRINT_JXL"}, {}])
    assert ok
    assert len(cmds) == 2
    assert cmds[0][cmds[0].index("--export-marker") + 1] == "X_PRINT"
    assert cmds[0][cmds[0].index("--export-jxl-folder") + 1] == "PRINT_JXL"
    # Row B gets the RUN's marker (always passed since round 43, so the child
    # never falls back to its own script setting), never row A's X_PRINT.
    assert cmds[1][cmds[1].index("--export-marker") + 1] != "X_PRINT"
    assert "--export-jxl-folder" not in cmds[1]


def test_jxl_to_tiff_row_marker_never_gets_the_jxl_folder(tmp_path, monkeypatch):
    rows = [(tmp_path / "A", tmp_path / "oA", 6)]
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, rows, origin="jxl", dest="tiff",
        row_options=[{"export_marker": "_PRINT",
                      "export_jxl_folder": "PRINT_JXL"}])
    assert ok
    assert cmds[0][cmds[0].index("--export-marker") + 1] == "_PRINT"
    assert "--export-jxl-folder" not in cmds[0]


# ── per-row mode detection and the derived mode-7 subfolder ─────────────────

def test_legacy_entry_detects_mode_6_with_its_own_marker(tmp_path, monkeypatch):
    """No Mode cell: the row resolves 6 because its ExportMarker matches the
    Destination, even though the run's marker is the default _EXPORT."""
    src = tmp_path / "Fotos"
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, [(src, src / "_PRINT", None)],
        row_options=[{"export_marker": "_PRINT"}], mock_detect=False)
    assert ok
    assert cmds[0][cmds[0].index("--mode") + 1] == "6"
    assert cmds[0][cmds[0].index("--export-marker") + 1] == "_PRINT"


def test_derived_subfolder_reaches_a_row_with_its_own_export_override(
        tmp_path, monkeypatch):
    """The mode-7 derivation fills the RUN's subfolder after the per-row dicts
    were built; a row holding its own Export* copy must still inherit it."""
    rows = [(tmp_path / "s1" / "_EXPORT" / "TIFF16", tmp_path / "o1", 7),
            (tmp_path / "s2" / "_EXPORT" / "TIFF16", tmp_path / "o2", 7)]
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, rows,
        row_options=[{"export_jxl_folder": "PRINT_JXL"}, {}])
    assert ok
    assert len(cmds) == 2
    for cmd in cmds:
        assert cmd[cmd.index("--export-subfolder") + 1] == "TIFF16"
    assert cmds[0][cmds[0].index("--export-jxl-folder") + 1] == "PRINT_JXL"


def test_own_subfolder_wins_the_derivation_and_skips_the_warning(
        tmp_path, monkeypatch):
    """A row with ExportSubfolder neither derives nor warns — even with a
    Source that has no <marker>/<subfolder> shape (otherwise the underivable
    warning would ask 'Run anyway?' and the mocked 'n' would abort)."""
    rows = [(tmp_path / "s1", tmp_path / "o1", 7),
            (tmp_path / "s2" / "_EXPORT" / "TIFF16", tmp_path / "o2", 7)]
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, rows,
        row_options=[{"export_subfolder": "sRGB"}, {}])
    assert ok
    assert cmds[0][cmds[0].index("--export-subfolder") + 1] == "sRGB"
    assert cmds[1][cmds[1].index("--export-subfolder") + 1] == "TIFF16"


def test_underivable_warning_mentions_the_column(tmp_path, monkeypatch, capsys):
    """A mode-7 row whose Source is above the marker and carries no own
    ExportSubfolder cannot be derived — the warning must name the column that
    fixes it (the mocked 'n' then declines the run)."""
    rows = [(tmp_path / "s1", tmp_path / "o1", 7)]
    ok, cmds = _run_manifest(monkeypatch, tmp_path, rows)
    assert not ok
    assert not cmds
    assert "ExportSubfolder" in capsys.readouterr().out


# ── the executor validates the output folder per row ────────────────────────

def test_output_folder_matching_the_marker_is_refused_before_any_child(
        tmp_path, monkeypatch, capsys):
    rows = [(tmp_path / "A", tmp_path / "oA", 6)]
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, rows,
        row_options=[{"export_jxl_folder": "_EXPORT"}])
    assert not ok
    assert not cmds
    assert "matches the export marker" in capsys.readouterr().out


def test_output_folder_equal_to_the_input_subfolder_is_refused(
        tmp_path, monkeypatch, capsys):
    rows = [(tmp_path / "A", tmp_path / "oA", 7)]
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, rows,
        row_options=[{"export_subfolder": "TIFF16",
                      "export_jxl_folder": "TIFF16"}])
    assert not ok
    assert not cmds
    assert "input subfolder itself" in capsys.readouterr().out


def test_default_folder_against_a_row_marker_is_refused(tmp_path, monkeypatch,
                                                        capsys):
    """ExportMarker=16B without its own folder: the child's default 16B_JXL
    matches the marker (would become a second anchor) — refuse up front."""
    rows = [(tmp_path / "A", tmp_path / "oA", 6)]
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, rows,
        row_options=[{"export_marker": "16B"}])
    assert not ok
    assert not cmds
    assert "matches the export marker" in capsys.readouterr().out


# ── collision scan decision and per-row resolution ──────────────────────────

def test_different_markers_in_disjoint_trees_skip_the_scan(tmp_path, monkeypatch,
                                                           capsys):
    calls = []
    monkeypatch.setattr(wp.InteractiveMenu, "_manifest_output_collisions",
                        lambda self, *a, **k: (calls.append(k), [])[1])
    rows = [(tmp_path / "A", tmp_path / "oA", 6),
            (tmp_path / "B", tmp_path / "oB", 6)]
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, rows,
        row_options=[{"export_marker": "_PRINT"}, {"export_marker": "X_SITE"}])
    assert ok
    assert not calls, "disjoint trees with per-row markers need no scan"
    assert "Collision check: skipped" in capsys.readouterr().out


def test_scan_skipped_when_only_the_output_folders_differ(tmp_path, monkeypatch,
                                                          capsys):
    calls = []
    monkeypatch.setattr(wp.InteractiveMenu, "_manifest_output_collisions",
                        lambda self, *a, **k: (calls.append(k), [])[1])
    rows = [(tmp_path / "A", tmp_path / "oA", 6),
            (tmp_path / "B", tmp_path / "oB", 6)]
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, rows,
        row_options=[{"export_jxl_folder": "PRINT_JXL"},
                     {"export_jxl_folder": "SCREEN_JXL"}])
    assert ok
    assert not calls
    assert "Collision check: skipped" in capsys.readouterr().out


def test_one_effective_marker_is_passed_to_the_skip_check(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(wp.InteractiveMenu, "_manifest_needs_collision_scan",
                        lambda self, entries, marker, row_markers=None:
                        (seen.append(row_markers), False)[1])
    rows = [(tmp_path / "A", tmp_path / "oA", 6),
            (tmp_path / "B", tmp_path / "oB", 6)]
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, rows,
        row_options=[{"export_marker": "_PRINT"}, {"export_marker": "_PRINT"}])
    assert ok
    assert seen == [["_PRINT", "_PRINT"]], "the per-row markers were not passed"


def test_nested_marker_dirs_force_the_scan(tmp_path):
    """_EXPORT and _EXPORT/SITE are different keys, so the exact-marker
    comparison cannot see that one row's anchor sits inside the other's —
    the nesting itself must force the scan."""
    entries = [
        (str(tmp_path / "root" / "s_PRINT" / "A"), str(tmp_path / "o1"), 6),
        (str(tmp_path / "root" / "s_PRINT" / "b_SITE" / "C"),
         str(tmp_path / "o2"), 6),
    ]
    assert _menu()._manifest_needs_collision_scan(
        entries, "_EXPORT", row_markers=["_PRINT", "_SITE"]) is True


def test_collision_mirror_resolves_each_row_under_its_own_folder(tmp_path):
    import jxl_tiff_encoder as enc
    before = enc.EXPORT_JXL_FOLDER
    menu = _menu()
    a = tmp_path / "X_EXPORT" / "A"
    b = tmp_path / "X_EXPORT" / "B"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / "foto.tif").write_bytes(b"x")
    (b / "foto.tif").write_bytes(b"x")
    entries = [(str(a), str(a), 6), (str(b), str(b), 6)]
    assert menu._manifest_output_collisions(
        entries, {".tif"}, origin="tiff", dest="jxl",
        row_export=[("_EXPORT", None, "PRINT_JXL"),
                    ("_EXPORT", None, None)]) == []
    collisions = menu._manifest_output_collisions(
        entries, {".tif"}, origin="tiff", dest="jxl",
        row_export=[("_EXPORT", None, None)] * 2)
    assert collisions, "the shared default folder collision went unseen"
    assert enc.EXPORT_JXL_FOLDER == before, "the folder leaked past the call"


# ── the generator writes the export columns for mode 6/7 rows ────────────────

_OPT5 = ["OutputICC", "Resize", "Sharpen", "RenameFrom", "RenameTo"]
_EXPORT3 = ["ExportMarker", "ExportSubfolder", "ExportJxlFolder"]
_EXCL = ["ExcludeFolders"]


def _generate(menu, monkeypatch, tmp_path, origin, dest, entry_mode):
    analyzer = wp.FolderAnalyzer(Path("."), origin, dest, "marker")
    monkeypatch.setattr(analyzer, "generate_manifest",
                        lambda analysis, mode: [("S", "D", 1, entry_mode)])
    monkeypatch.setattr(wp, "SCRIPT_DIR", tmp_path)
    out = menu._generate_manifest(analyzer, {}, entry_mode)
    assert out is not None
    with open(out, newline="", encoding="utf-8-sig") as f:
        return list(csv.reader(f)), out


@pytest.mark.parametrize("origin,dest,entry_mode,extra", [
    ("tiff", "jxl", 6, _EXCL + _EXPORT3),
    ("jxl", "tiff", 7, _EXCL + _EXPORT3[:2]),
    ("jxl", "jxl", 6, _OPT5 + _EXPORT3),
    ("tiff", "jxl", 2, _EXCL),
    ("jxl", "tiff", 2, _EXCL),
    ("jxl", "jxl", 2, _OPT5),
])
def test_generator_writes_the_export_columns_for_modes_6_7(
        tmp_path, monkeypatch, origin, dest, entry_mode, extra):
    rows, _out = _generate(_menu(), monkeypatch, tmp_path, origin, dest,
                           entry_mode)
    assert rows[0] == ["Source", "Destination", "Mode", "Direction"] + extra
    assert rows[1] == (["S", "D", str(entry_mode), f"{origin}2{dest}"]
                       + [""] * len(extra))


def test_generated_manifest_round_trips(tmp_path, monkeypatch):
    rows, out = _generate(_menu(), monkeypatch, tmp_path, "tiff", "jxl", 6)
    assert rows[0][-3:] == _EXPORT3
    ro = []
    entries = _menu()._load_manifest_entries(out, "tiff", "jxl",
                                              row_options=ro)
    assert entries is not None
    assert ro == [{}], "the empty generated cells must not become row options"
