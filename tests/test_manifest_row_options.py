#!/usr/bin/env python3
"""Manifest per-row option columns (OutputICC, Resize, Sharpen, RenameFrom,
RenameTo).

The generated manifest carries five extra columns after Direction for the
directions that accept them (jxl2jxl, jxl2jpeg, jxl2png). An empty cell means
"not applied on this row"; a filled cell OVERRIDES the wizard's answer for
that row only. The loader refuses the whole manifest on any invalid value
(a typo must never become a silent "option not applied"), the guards refuse
derivative rows combined with --delete-source or an in-place mode, and the
command builder suppresses --delete-source on derivative rows while the
plain rows keep it.
"""

import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_photo as wp

REPO = Path(__file__).resolve().parent.parent

_OPT_HEADER = ["OutputICC", "Resize", "Sharpen", "RenameFrom", "RenameTo"]


def _menu():
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


def _write_manifest(path, rows, direction="jxl2png", with_opts=True,
                    header=None):
    """rows: list of (source, dest, mode, opts-list-of-5)."""
    hdr = header or (["Source", "Destination", "Mode", "Direction"]
                     + (_OPT_HEADER if with_opts else []))
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(hdr)
        for src, dst, mode, opts in rows:
            row = [str(src), str(dst), mode, direction]
            if with_opts:
                row += list(opts)
            w.writerow(row)


def _load(menu, manifest, origin="jxl", dest="png"):
    row_options = []
    entries = menu._load_manifest_entries(str(manifest), origin, dest,
                                          row_options=row_options)
    return entries, row_options


def _run_manifest(monkeypatch, tmp_path, rows, origin="jxl", dest="png",
                  conv="jxl_to_png", advanced=None, row_options=None):
    """Drive _execute_manifest_workflow with the child processes mocked out.

    rows: list of (source-dir, dest-dir, mode) — dirs are created, each with
    one dummy source file so the collision scan walks something real.
    Returns (ok, cmds)."""
    menu = _menu()
    cmds = []
    monkeypatch.setattr(wp.InteractiveMenu, "_run_subprocess",
                        lambda self, cmd: (cmds.append(cmd), 0)[1])
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_lossy_delete_skipped",
                        lambda self, wf: None)
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode",
                        lambda self: True)
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
        "dry_run": False, "mode_config": {}, "conversion_type": conv,
        "icc_profile": None, "distance": 1.0, "effort": 7,
        "advanced_options": dict(advanced or {}),
        "manifest_entries": entries,
    }
    if row_options is not None:
        wf["manifest_row_options"] = row_options
    ok = menu._execute_manifest_workflow(wf, {"cjxl": True})
    return ok, cmds


# ── loading ─────────────────────────────────────────────────────────────────

def test_option_columns_load_into_row_options(tmp_path):
    src, dst = tmp_path / "A", tmp_path / "out"
    m = tmp_path / "m.csv"
    _write_manifest(m, [(src, dst, 2,
                         ["sRGB", "long:2048", "screen", "ProPhoto", "sRGB"])])
    entries, ro = _load(_menu(), m)
    assert entries == [(str(src), str(dst), 2)]
    assert ro == [{"output_icc": "sRGB", "resize_mode": "long",
                   "resize_value": 2048, "allow_upscale": False,
                   "sharpen": "screen",
                   "rename_from": "ProPhoto", "rename_to": "sRGB"}]


def test_manifest_without_the_columns_still_loads(tmp_path):
    """A 4-column manifest (old or hand-written) is not touched by the new
    parsing — and reports empty option dicts for every row."""
    src, dst = tmp_path / "A", tmp_path / "out"
    m = tmp_path / "m.csv"
    _write_manifest(m, [(src, dst, 2, [])], with_opts=False)
    entries, ro = _load(_menu(), m)
    assert entries == [(str(src), str(dst), 2)]
    assert ro == [{}]


def test_empty_option_row_means_no_options(tmp_path):
    src, dst = tmp_path / "A", tmp_path / "out"
    m = tmp_path / "m.csv"
    _write_manifest(m, [(src, dst, 2, ["sRGB", "", "", "", ""]),
                        (src, dst, 3, ["", "", "", "", ""])])
    entries, ro = _load(_menu(), m)
    # A row with SOME cells filled marks the other columns as explicitly
    # "not applied" (they override the wizard's answers); a row with every
    # cell empty is the same "all cleared" override once the columns exist —
    # the wizard's recipe is NOT inherited.
    _cleared = {"output_icc": None, "resize_mode": None, "resize_value": None,
                "allow_upscale": False, "sharpen": "none",
                "rename_from": "", "rename_to": ""}
    assert ro[0] == dict(_cleared, output_icc="sRGB")
    assert ro[1] == _cleared


def test_bad_header_still_refuses_the_manifest(tmp_path):
    m = tmp_path / "m.csv"
    _write_manifest(m, [], header=["A", "B", "C", "D"])
    entries, _ = _load(_menu(), m)
    assert entries is None


@pytest.mark.parametrize("col,val", [
    ("Resize", "abc"),          # not long:/short:/percent:
    ("Resize", "long:-5"),      # out of range
    ("Resize", "long:2048,5"),  # thousands separator
    ("OutputICC", "NoSuchProfile"),
    ("Sharpen", "strong"),      # not none/screen/print
    ("RenameFrom", "a,b"),      # CSV-breaking character
    ("RenameFrom", ".."),       # path-looking token
])
def test_an_invalid_cell_refuses_the_whole_manifest(tmp_path, col, val):
    src, dst = tmp_path / "A", tmp_path / "out"
    opts = {c: "" for c in _OPT_HEADER}
    opts[col] = val
    if col == "RenameFrom" and val not in ("..",):
        pass  # RenameTo may stay empty only when RenameFrom is empty — not here
    m = tmp_path / "m.csv"
    _write_manifest(m, [(src, dst, 2, [opts[c] for c in _OPT_HEADER])])
    entries, _ = _load(_menu(), m)
    assert entries is None


def test_rename_to_without_rename_from_is_refused(tmp_path):
    src, dst = tmp_path / "A", tmp_path / "out"
    m = tmp_path / "m.csv"
    _write_manifest(m, [(src, dst, 2, ["", "", "", "", "sRGB"])])
    entries, _ = _load(_menu(), m)
    assert entries is None


def test_tiff2jxl_rejects_the_option_columns(tmp_path):
    """The columns are only accepted on jxl2jxl, jxl2jpeg and jxl2png — the
    encoder writes the MASTER and never converts colour or resizes."""
    src, dst = tmp_path / "A", tmp_path / "out"
    m = tmp_path / "m.csv"
    _write_manifest(m, [(src, dst, 2, ["sRGB", "", "", "", ""])],
                    direction="tiff2jxl")
    entries, ro = _load(_menu(), m, origin="tiff", dest="jxl")
    assert entries is None


def test_tiff2jxl_accepts_the_columns_when_every_cell_is_empty(tmp_path):
    src, dst = tmp_path / "A", tmp_path / "out"
    m = tmp_path / "m.csv"
    _write_manifest(m, [(src, dst, 2, ["", "", "", "", ""])],
                    direction="tiff2jxl")
    entries, ro = _load(_menu(), m, origin="tiff", dest="jxl")
    assert entries == [(str(src), str(dst), 2)]
    assert ro == [{}]


# ── the summary shown at confirmation time ──────────────────────────────────

def test_row_options_summary_formats():
    assert wp._row_options_summary({}) == ""
    assert wp._row_options_summary(
        {"output_icc": "sRGB", "resize_mode": "long", "resize_value": 2048,
         "sharpen": "screen"}) == "sRGB · long:2048 · screen"
    assert wp._row_options_summary(
        {"rename_from": "ProPhoto", "rename_to": "sRGB"}) == "ProPhoto→sRGB"
    assert wp._row_options_summary({"output_icc": "AdobeRGB"}) == "AdobeRGB"


def test_confirmation_lists_the_recipe_per_row(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp, "console", None)
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    entries = [("A", "B", 2), ("C", "D", 3)]
    ro = [{"output_icc": "sRGB", "resize_mode": "long", "resize_value": 2048,
           "sharpen": "screen"}, {}]
    assert _menu()._confirm_manifest_entries("m.csv", entries, row_options=ro)
    out = capsys.readouterr().out
    assert "sRGB · long:2048 · screen" in out
    # The plain row shows no recipe marker.
    assert out.index("sRGB · long:2048 · screen") < out.index("C")


# ── commands: the row's recipe reaches only its own child ───────────────────

def test_row_recipe_overrides_the_wizard_answer(tmp_path, monkeypatch):
    rows = [(tmp_path / "A", tmp_path / "oA", 2),
            (tmp_path / "B", tmp_path / "oB", 2)]
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, rows,
        advanced={"resize_mode": "long", "resize_value": 4096,
                  "allow_upscale": False, "sharpen": "print"},
        row_options=[{"resize_mode": "long", "resize_value": 2048,
                      "sharpen": "screen"}, {}])
    assert ok
    assert len(cmds) == 2
    assert cmds[0][cmds[0].index("--resize-long") + 1] == "2048"
    assert cmds[0][cmds[0].index("--sharpen") + 1] == "screen"
    assert cmds[1][cmds[1].index("--resize-long") + 1] == "4096"
    assert cmds[1][cmds[1].index("--sharpen") + 1] == "print"


def test_resize_plus_sharpen_combined_on_one_row(tmp_path, monkeypatch):
    rows = [(tmp_path / "A", tmp_path / "oA", 2)]
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, rows, origin="jxl", dest="jxl",
        conv="jxl_recompress",
        row_options=[{"resize_mode": "short", "resize_value": 1024,
                      "allow_upscale": True, "sharpen": "print"}])
    assert ok
    cmd = cmds[0]
    assert cmd[cmd.index("--resize-short") + 1] == "1024"
    assert "--allow-upscale" in cmd
    assert cmd[cmd.index("--sharpen") + 1] == "print"


def test_output_icc_row_converts_but_never_deletes(tmp_path, monkeypatch):
    """--delete-source stays armed for the plain row; the OutputICC row is a
    derivative and gets no delete flag (mirrors --icc-profile semantics)."""
    rows = [(tmp_path / "A", tmp_path / "oA", 2),
            (tmp_path / "B", tmp_path / "oB", 2)]
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, rows,
        advanced={"delete_source": True},
        row_options=[{"output_icc": "sRGB"}, {}])
    assert ok
    assert cmds[0][cmds[0].index("--icc-profile") + 1] == "sRGB"
    assert "--delete-source" not in cmds[0]
    assert "--delete-source" in cmds[1]


def test_column_resize_with_delete_is_refused_up_front(tmp_path, monkeypatch):
    rows = [(tmp_path / "A", tmp_path / "oA", 2),
            (tmp_path / "B", tmp_path / "oB", 2)]
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, rows,
        advanced={"delete_source": True},
        row_options=[{"resize_mode": "long", "resize_value": 100,
                      "sharpen": "screen"}, {}])
    assert not ok
    assert not cmds


@pytest.mark.parametrize("row_options", [None, [{}, {}]])
def test_wizard_resize_with_delete_is_refused_not_dropped(tmp_path, monkeypatch,
                                                          row_options):
    """A resize answered in the WIZARD plus --delete-source: refused up front,
    exactly like before the option columns. It used to run the resize and
    silently drop the delete the user asked for."""
    rows = [(tmp_path / "A", tmp_path / "oA", 2),
            (tmp_path / "B", tmp_path / "oB", 2)]
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, rows, dest="jpeg", conv="jxl_to_jpeg_force",
        advanced={"delete_source": True, "resize_mode": "long",
                  "resize_value": 2048},
        row_options=row_options)
    assert not ok
    assert not cmds


def test_column_clearing_the_wizard_resize_lets_the_delete_run(tmp_path, monkeypatch):
    """An empty Resize cell removes the wizard's resize on that row: with no
    derivative left anywhere, the delete runs normally."""
    rows = [(tmp_path / "A", tmp_path / "oA", 2)]
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, rows, dest="jpeg", conv="jxl_to_jpeg_force",
        advanced={"delete_source": True, "resize_mode": "long",
                  "resize_value": 2048},
        row_options=[{"resize_mode": None, "resize_value": None,
                      "allow_upscale": False}])
    assert ok
    assert "--delete-source" in cmds[0]
    assert "--resize-long" not in cmds[0]


def test_lossless_jbrd_row_cannot_be_resized(tmp_path, monkeypatch):
    rows = [(tmp_path / "A", tmp_path / "oA", 2)]
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, rows, dest="jpeg", conv="jxl_to_jpeg_lossless",
        row_options=[{"resize_mode": "long", "resize_value": 100,
                      "sharpen": "none"}])
    assert not ok
    assert not cmds


def test_derived_row_in_place_is_refused_up_front(tmp_path, monkeypatch):
    src = tmp_path / "A"
    rows = [(src, src, 8)]
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, rows, origin="jxl", dest="jxl",
        conv="jxl_recompress",
        row_options=[{"resize_mode": "long", "resize_value": 100,
                      "sharpen": "screen"}])
    assert not ok
    assert not cmds


def test_rename_columns_reach_only_their_row(tmp_path, monkeypatch):
    rows = [(tmp_path / "A", tmp_path / "oA", 2),
            (tmp_path / "B", tmp_path / "oB", 2)]
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, rows,
        row_options=[{"rename_from": "ProPhoto", "rename_to": "sRGB"}, {}])
    assert ok
    assert cmds[0][cmds[0].index("--rename-from") + 1] == "ProPhoto"
    assert cmds[0][cmds[0].index("--rename-to") + 1] == "sRGB"
    assert "--rename-from" not in cmds[1]


def test_rename_row_keeps_its_delete_flag(tmp_path, monkeypatch):
    """A rename alone is not a derivative: the row still deletes."""
    rows = [(tmp_path / "A", tmp_path / "oA", 2)]
    ok, cmds = _run_manifest(
        monkeypatch, tmp_path, rows,
        advanced={"delete_source": True},
        row_options=[{"rename_from": "a", "rename_to": "b"}])
    assert ok
    assert "--delete-source" in cmds[0]
    assert "--rename-from" in cmds[0]


# ── the collision scan sees the renamed names ───────────────────────────────

def _collisions(menu, tmp_path, spec, row_renames):
    """spec: list of (dirname, [file names]); all mode 2 into one dest."""
    dest = tmp_path / "out"
    entries = []
    for name, files in spec:
        d = tmp_path / name
        d.mkdir()
        for f in files:
            (d / f).write_bytes(b"x")
        entries.append((str(d), str(dest), 2))
    return menu._manifest_output_collisions(
        entries, {".jxl"}, origin="jxl", dest="jxl",
        export_marker="x", row_renames=row_renames)


def test_same_rename_on_both_rows_is_not_a_false_collision(tmp_path):
    menu = _menu()
    collisions = _collisions(
        menu, tmp_path,
        [("A", ["a_ProPhoto.jxl"]), ("B", ["b_ProPhoto.jxl"])],
        [("ProPhoto", "sRGB"), ("ProPhoto", "sRGB")])
    assert collisions == []


def test_a_renamed_row_collides_with_a_plain_row(tmp_path):
    """a_ProPhoto.jxl --(ProPhoto→sRGB)--> a_sRGB.jxl, landing on the plain
    row's a_sRGB.jxl: the scan must mirror the rename or it sees nothing."""
    menu = _menu()
    collisions = _collisions(
        menu, tmp_path,
        [("A", ["a_ProPhoto.jxl"]), ("B", ["a_sRGB.jxl"])],
        [("ProPhoto", "sRGB"), ("", "")])
    assert collisions, "the rename-induced collision went unseen"


def test_the_rename_stays_case_sensitive_like_the_child(tmp_path):
    """a_PROPHOTO.jxl does NOT match the token ProPhoto (the child's
    replacement is literal and case-sensitive), so no collision exists."""
    menu = _menu()
    collisions = _collisions(
        menu, tmp_path,
        [("A", ["a_PROPHOTO.jxl"]), ("B", ["a_sRGB.jxl"])],
        [("ProPhoto", "sRGB"), ("", "")])
    assert collisions == []


# ── the generator writes the columns for the directions that accept them ────

def _generate(menu, monkeypatch, tmp_path, origin, dest):
    analyzer = wp.FolderAnalyzer(Path("."), origin, dest, "marker")
    monkeypatch.setattr(analyzer, "generate_manifest",
                        lambda analysis, mode: [("S", "D", 1, 2)])
    monkeypatch.setattr(wp, "SCRIPT_DIR", tmp_path)
    out = menu._generate_manifest(analyzer, {}, 2)
    assert out is not None
    with open(out, newline="", encoding="utf-8-sig") as f:
        return list(csv.reader(f))


def test_generator_writes_the_five_option_columns(tmp_path, monkeypatch):
    rows = _generate(_menu(), monkeypatch, tmp_path, "jxl", "png")
    assert rows[0] == ["Source", "Destination", "Mode", "Direction"] + _OPT_HEADER
    assert rows[1] == ["S", "D", "2", "jxl2png", "", "", "", "", ""]


def test_generator_keeps_tiff2jxl_at_four_columns(tmp_path, monkeypatch):
    rows = _generate(_menu(), monkeypatch, tmp_path, "tiff", "jxl")
    assert rows[0] == ["Source", "Destination", "Mode", "Direction"]
    assert rows[1] == ["S", "D", "2", "tiff2jxl"]
