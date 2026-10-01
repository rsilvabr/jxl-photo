#!/usr/bin/env python3
"""Round-43 wrapper regressions (audit 261001, sections 1/6/7 + AI2 errata).

* F-1  — the collision walk (and the replay) filters exclusions ONLY in the
         tiff<->jxl directions, the ones whose child actually receives
         --exclude-folders. Filtering anywhere else hides REAL collisions;
         replaying everywhere produces the spurious "IGNORED" warning.
* F-3  — the non-rich wizard prompt honours its displayed default.
* W1-3 — the wizard stages the exclude answer on the workflow only; a
         cancelled wizard persists nothing (the #319f invariant).
* F-5  — a ';'-only / whitespace-only ExcludeFolders manifest cell is an
         EMPTY cell (keeps the run's value), not an explicit removal.
* F-6  — a hand-edited last_exclude_folders carrying paths ('\' or '/'),
         or a non-string, refuses the session (the #319e pattern).
* W1-1 — hand-edited last_origin_format / last_dest_format refuse the
         session instead of silently routing to the wrong script.
* W1-2 — ExportMarker / ExportJxlFolder filled on an explicit Mode not in
         (6, 7) refuse the manifest, like ExportSubfolder already does.
* W1-4 — comment rows are fully inert in the manifest parser (a commented
         row with a fractional Mode cell refuses nothing).
* W1-5 — the wizard's resize questions validate like _MANIFEST_RESIZE_RE
         (nan/inf/negative percents and non-positive edges re-prompt).
* W1-6 — the Auto Mode / manifest-generator folder filter also skips the
         recompressor's own output folders — for jxl->jxl ONLY (the other
         children process them), asked from the recompressor itself.
* #467 — the wrapper's export marker is always passed, and the modes-6/7
         output folder names are read from each child's own setting.
* W2-1 — the collision scan must not model rename where the child ignores
         it: lossless rows refuse --rename-from up front; auto mode keeps
         the ORIGINAL stem for jbrd files (per-file probe, has_jbrd_box).
* W2-2 — the derivative-in-place guard iterates resolved_entries.
* W2-3 — a child exiting 130 stops the manifest (same stop-everything
         class as exit 2 and -1).
* W2-4 — the abort recap header's parts sum to the total: not-started
         entries get their own bucket.
* F-7  — the run-scoped `_exclude_folder_warned` set resets per run.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_photo as wp
import jxl_jpeg_transcoder as tr

REPO = Path(__file__).resolve().parent.parent

STATUS = {k: True for k in
          ("cjxl", "djxl", "exiftool", "magick", "tifffile", "pillow", "imagecodecs")}

_BASE = ["Destination", "Mode", "Direction"]


def _menu(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


# ---------------------------------------------------------------------------
# F-1a — the collision walk's exclusion filter is direction-gated
# ---------------------------------------------------------------------------

def _tree_with_export(root: Path, ext: str):
    src = root / "src"
    (src / "_EXPORT").mkdir(parents=True)
    (src / f"x{ext}").write_bytes(b"x")
    (src / "_EXPORT" / f"x{ext}").write_bytes(b"x")
    dest = root / "out"
    dest.mkdir()
    entries = [(str(src), str(dest), 2)]
    return entries


def test_f1a_jxl2jxl_walk_sees_the_real_collision(tmp_path, monkeypatch):
    """mode-2 row with row_excludes=['_EXPORT'] and same-named files inside
    and outside _EXPORT — tiff2jxl and jxl2jxl. The recompressor never
    receives the flag, so the jxl2jxl walk must NOT filter: the collision
    is real. (Pre-fix: [] — the walk shrank below the child's file set and
    hid the overwrite.) tiff2jxl, same shape: the child DOES filter -> the
    phantom stays dropped."""
    entries = _tree_with_export(tmp_path, ".jxl")
    menu = _menu(tmp_path, monkeypatch)
    cols = menu._manifest_output_collisions(
        entries, {".jxl"}, origin="jxl", dest="jxl",
        row_excludes=["_EXPORT"])
    assert len(cols) == 1
    # tiff2jxl, same shape: the child DOES filter -> no phantom.
    entries_tif = _tree_with_export(tmp_path / "tif", ".tif")
    assert menu._manifest_output_collisions(
        entries_tif, {".tif"}, origin="tiff", dest="jxl",
        row_excludes=["_EXPORT"]) == []


def test_f1a_jpeg2jxl_walk_also_unfiltered(tmp_path, monkeypatch):
    """Any non tiff<->jxl direction: no filtering."""
    entries = _tree_with_export(tmp_path, ".jpg")
    menu = _menu(tmp_path, monkeypatch)
    assert len(menu._manifest_output_collisions(
        entries, {".jpg"}, origin="jpeg", dest="jxl",
        row_excludes=["_EXPORT"])) == 1


# ---------------------------------------------------------------------------
# F-1b — the replay carries last_exclude_folders only for tiff<->jxl
# ---------------------------------------------------------------------------

def _session(origin="jxl", dest="jxl", conv="jxl_recompress", **over):
    s = {n: None for n in wp.ToolConfig.__dataclass_fields__ if n.startswith("last_")}
    s.update({
        "last_input_dir": None, "last_output_mode": "2", "last_workers": 4,
        "last_effort": 7, "last_distance": 1.0,
        "last_origin_format": origin, "last_dest_format": dest,
        "last_conversion_type": conv,
        "last_advanced_options": {"overwrite": False, "sync": True},
    })
    s.update(over)
    return s


@pytest.fixture
def replay_capture(tmp_path, monkeypatch):
    """A menu whose execute_workflow just records the workflow it is handed."""
    menu = _menu(tmp_path, monkeypatch)
    src = tmp_path / "photos"
    src.mkdir()
    captured = {}

    def fake_exec(self, wf, status):
        captured.update(wf)
        return True

    monkeypatch.setattr(wp.InteractiveMenu, "execute_workflow", fake_exec)
    return menu, captured, src


def _replay_session(src, **over):
    s = _session(last_input_dir=str(src), **over)
    return s


def test_f1b_jxl_replay_does_not_carry_the_stale_exclusion(replay_capture):
    menu, captured, src = replay_capture
    session = _replay_session(src, last_exclude_folders="_STALE")
    ok = menu._run_saved_session(session, STATUS,
                                 answers={"overwrite": False, "dry_run": False})
    assert ok is True
    assert captured.get('exclude_folders') is None


def test_f1b_tiff_replay_still_carries_the_exclusion(replay_capture):
    menu, captured, src = replay_capture
    session = _replay_session(src, **{"last_exclude_folders": "_STALE",
                                      "last_origin_format": "tiff",
                                      "last_dest_format": "jxl",
                                      "last_conversion_type":
                                      "jxl_tiff_encoder"})
    ok = menu._run_saved_session(session, STATUS,
                                 answers={"overwrite": False, "dry_run": False})
    assert ok is True
    assert captured.get('exclude_folders') == "_STALE"


# ---------------------------------------------------------------------------
# F-3 / W1-3 — the non-rich wizard prompt
# ---------------------------------------------------------------------------

def _answer_exclude(tmp_path, monkeypatch, answer, default_saved):
    """Run Step 3: directory prompt (test dir), then the exclude question.
    Returns (workflow_exclude, config_value)."""
    src = tmp_path / "photos"
    src.mkdir()
    menu = _menu(tmp_path, monkeypatch)
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp, "console", None)
    menu.config.config.last_input_dir = str(src)
    menu.config.config.last_exclude_folders = default_saved
    workflow = {"origin_format": "tiff", "dest_format": "jxl"}
    seen = []
    queue = [str(src), answer]

    def fake_input(prompt=""):
        seen.append(prompt)
        return queue.pop(0) if queue else ""

    monkeypatch.setattr("builtins.input", fake_input)
    menu._wizard_select_files(workflow)
    assert any("Exclude folders" in p for p in seen), f"prompts: {seen}"
    return workflow.get('exclude_folders'), menu.config.config.last_exclude_folders


def test_f3_empty_answer_keeps_the_displayed_default(tmp_path, monkeypatch):
    wf_val, cfg_val = _answer_exclude(tmp_path, monkeypatch, "", "_KEEP")
    assert wf_val == "_KEEP"
    # W1-3 boundary condition, too: still nothing on disk.


def test_f3_dash_still_means_no_exclusion(tmp_path, monkeypatch):
    wf_val, _cfg = _answer_exclude(tmp_path, monkeypatch, "-", "_KEEP")
    assert wf_val is None


def test_w13_the_answer_is_staged_not_persisted(tmp_path, monkeypatch):
    wf_val, cfg_val = _answer_exclude(tmp_path, monkeypatch, "RAW", None)
    assert wf_val == "RAW"
    assert cfg_val is None, \
        "a cancelled wizard must not leave the answer in the config (#319f)"


# ---------------------------------------------------------------------------
# F-5 / F-6 — manifest cell and session-shape validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cell", [";", ";;", " ; ", "  "])
def test_f5_semicolon_only_cell_keeps_the_run_value(cell):
    opts, err = wp._parse_manifest_row_options({"excludefolders": cell},
                                               "tiff2jxl", lambda p: p)
    assert err is None
    assert "exclude_folders" not in opts, \
        "an empty-semantics cell must not erase the run's exclusion"


def test_f5_full_cell_is_still_a_value():
    opts, err = wp._parse_manifest_row_options(
        {"excludefolders": "_EXPORT;temp"}, "tiff2jxl", lambda p: p)
    assert err is None
    assert opts["exclude_folders"] == "_EXPORT;temp"


def test_f6_session_refuses_exclude_paths():
    for raw in ("foo\\bar", "foo/bar"):
        err = wp._session_number_error({"last_exclude_folders": raw})
        assert err is not None, raw
        assert "folder NAMES" in err
    assert wp._session_number_error({"last_exclude_folders": "_EXPORT;temp"}) is None


def test_w11_session_refuses_hand_edited_formats():
    assert wp._session_number_error({"last_origin_format": "Tiff"}) is not None
    assert wp._session_number_error({"last_dest_format": "Jpeg"}) is not None
    assert wp._session_number_error(
        {"last_origin_format": "tiff", "last_dest_format": "jxl"}) is None
    assert wp._session_number_error(
        {"last_origin_format": "jxl", "last_dest_format": "png"}) is None


def test_w11_corrupt_session_refuses_instead_of_routing(tmp_path, monkeypatch,
                                                        capsys):
    """End-to-end: a hand-edited 'Tiff' must refuse the session."""
    menu = _menu(tmp_path, monkeypatch)
    launched = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600:
                        (launched.append(list(map(str, cmd))), 0)[1])
    session = _session("Tiff", "jxl", "jxl_tiff_encoder",
                       last_input_dir=str(tmp_path))
    ok = menu._run_saved_session(session, STATUS,
                                 answers={"overwrite": False, "dry_run": False})
    assert ok is False
    assert launched == []
    assert "corrupt" in " ".join(capsys.readouterr().out.split())


# ---------------------------------------------------------------------------
# W1-2 — Export* cells on a non-(6/7) explicit Mode refuse the manifest
# ---------------------------------------------------------------------------

def _write_manifest(path, rows):
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Source"] + _BASE + ["ExportMarker", "ExportJxlFolder"])
        for row in rows:
            w.writerow(row)


def _load_manifest(menu, path, row_options=None, origin="jxl", dest="jxl"):
    return menu._load_manifest_entries(str(path), origin, dest,
                                       row_options=row_options)


def test_w12_export_marker_on_a_mode3_row_refuses(tmp_path, monkeypatch):
    m = tmp_path / "m.csv"
    _write_manifest(m, [[str(tmp_path / "a"), "", 3, "jxl2jxl", "_PRINT", ""]])
    menu = _menu(tmp_path, monkeypatch)
    assert _load_manifest(menu, m, row_options=[]) is None


def test_w12_export_columns_on_a_mode6_row_accept(tmp_path, monkeypatch):
    m = tmp_path / "m.csv"
    _write_manifest(m, [[str(tmp_path / "a"), "", 6, "jxl2jxl", "_PRINT", ""]])
    menu = _menu(tmp_path, monkeypatch)
    assert _load_manifest(menu, m, row_options=[]) is not None


def test_w12_export_columns_on_a_modeless_row_accept(tmp_path, monkeypatch):
    m = tmp_path / "m.csv"
    _write_manifest(m, [[str(tmp_path / "a"), "", "", "jxl2jxl", "_PRINT", ""]])
    menu = _menu(tmp_path, monkeypatch)
    assert _load_manifest(menu, m, row_options=[]) is not None


# ---------------------------------------------------------------------------
# W1-4 — comment rows are fully inert
# ---------------------------------------------------------------------------

def test_w14_commented_row_with_fractional_mode_is_inert(tmp_path, monkeypatch):
    import csv
    m = tmp_path / "m.csv"
    with open(m, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Source"] + _BASE)
        w.writerow(["# old row todavia", str(tmp_path / "b"), 9.5, "jxl2jxl"])
        w.writerow([str(tmp_path / "a"), "", 2, "jxl2jxl"])
    menu = _menu(tmp_path, monkeypatch)
    entries = _load_manifest(menu, m, row_options=[])
    assert entries is not None
    assert len(entries) == 1


def test_w14_real_fractional_mode_still_refuses(tmp_path, monkeypatch):
    import csv
    m = tmp_path / "m.csv"
    with open(m, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Source"] + _BASE)
        w.writerow([str(tmp_path / "a"), "", 9.5, "jxl2jxl"])
    menu = _menu(tmp_path, monkeypatch)
    assert _load_manifest(menu, m, row_options=[]) is None


# ---------------------------------------------------------------------------
# W1-5 — the wizard's resize validation mirrors _MANIFEST_RESIZE_RE
# ---------------------------------------------------------------------------

def _shape_answers(monkeypatch, answers, tmp_path):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp, "console", None)
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(it))
    wf = {"advanced_options": {}}
    wp._ask_output_shaping(wf)
    return wf["advanced_options"]


@pytest.mark.parametrize("bad,good", [
    ("nan", "60"), ("-inf", "60"), ("inf", "60"), ("-2", "60"), ("0", "60"),
])
def test_w15_bad_percent_reprompts(monkeypatch, tmp_path, bad, good):
    adv = _shape_answers(monkeypatch, ["percent", bad, good, "n", "none"],
                         tmp_path)
    assert adv.get("resize_mode") == "percent"
    assert adv.get("resize_value") == float(good)


@pytest.mark.parametrize("bad,good", [("0", "2048"), ("-4", "2048")])
def test_w15_nonpositive_edge_reprompts(monkeypatch, tmp_path, bad, good):
    adv = _shape_answers(monkeypatch, ["long", bad, good, "n", "none"], tmp_path)
    assert adv.get("resize_mode") == "long"
    assert adv.get("resize_value") == int(good)


def test_w15_unparseable_text_still_disables_resize(monkeypatch, tmp_path):
    adv = _shape_answers(monkeypatch, ["long", "not-a-number", "none"], tmp_path)
    assert "resize_mode" not in adv


# ---------------------------------------------------------------------------
# W1-6 — the Auto Mode folder filter skips the recompressor's own outputs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("outname", ["recompressed_jxl", "jxl_recompressed",
                                     "jxl_small", "16B_JXL_small"])
def test_w16_auto_mode_skips_the_recompressor_output_folders(tmp_path, outname):
    (tmp_path / "_EXPORT").mkdir()
    (tmp_path / "_EXPORT" / "m.jxl").write_bytes(b"x")
    sub = tmp_path / "_EXPORT" / outname
    sub.mkdir()
    (sub / "o.jxl").write_bytes(b"x")
    analyzer = wp.FolderAnalyzer(tmp_path, "jxl", "jxl", "_EXPORT")
    analysis = analyzer.analyze()
    mappings = analyzer.compute_folder_mappings(analysis, 6)
    assert mappings and mappings[0][2] == 1, \
        f"{outname!r} must not be counted as a source (mode-6 preview)"


def test_w16_below_root_only(tmp_path):
    """Pointing the analyzer AT a folder named like an output is legitimate."""
    src = tmp_path / "recompressed_jxl"
    (src / "_EXPORT").mkdir(parents=True)
    (src / "_EXPORT" / "m.jxl").write_bytes(b"x")
    analyzer = wp.FolderAnalyzer(src, "jxl", "jxl", "_EXPORT")
    analysis = analyzer.analyze()
    mappings = analyzer.compute_folder_mappings(analysis, 6)
    assert mappings and mappings[0][2] == 1


@pytest.mark.parametrize("dest", ["tiff", "jpeg"])
def test_w16_other_directions_still_count_jxl_small(tmp_path, dest):
    """The decoder (and the transcoder) decode a JXL inside JXL_small/ like any
    other: only the RECOMPRESSOR skips its own output folders, so only the
    jxl->jxl preview may. Skipping them for jxl->tiff under-counted the run
    and could drop an export folder from a generated manifest."""
    (tmp_path / "_EXPORT" / "JXL_small").mkdir(parents=True)
    (tmp_path / "_EXPORT" / "JXL_small" / "o.jxl").write_bytes(b"x")
    analyzer = wp.FolderAnalyzer(tmp_path, "jxl", dest, "_EXPORT")
    mappings = analyzer.compute_folder_mappings(analyzer.analyze(), 6)
    assert mappings and mappings[0][2] == 1, \
        f"jxl->{dest}: a JXL in JXL_small/ is a source for that child"


def test_w16_follows_the_recompressors_configured_folder(tmp_path, monkeypatch):
    """The skip set is asked from the recompressor itself, so an
    EXPORT_JXL_FOLDER edited at the top of that script is honored."""
    import jxl_recompressor as rec
    monkeypatch.setattr(rec, "EXPORT_JXL_FOLDER", "MY_SMALL")
    (tmp_path / "_EXPORT" / "MY_SMALL").mkdir(parents=True)
    (tmp_path / "_EXPORT" / "m.jxl").write_bytes(b"x")
    (tmp_path / "_EXPORT" / "MY_SMALL" / "o.jxl").write_bytes(b"x")
    analyzer = wp.FolderAnalyzer(tmp_path, "jxl", "jxl", "_EXPORT")
    mappings = analyzer.compute_folder_mappings(analyzer.analyze(), 6)
    assert mappings and mappings[0][2] == 1


# ---------------------------------------------------------------------------
# Configurable names — the wrapper never shadows a child's own setting
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("modname,attr,origin,dest", [
    ("jxl_tiff_decoder", "EXPORT_TIFF_FOLDER", "jxl", "tiff"),
    ("jxl_tiff_encoder", "EXPORT_JXL_FOLDER", "tiff", "jxl"),
    ("jxl_recompressor", "EXPORT_JXL_FOLDER", "jxl", "jxl"),
    ("jxl_jpeg_transcoder", "EXPORT_JXL_FOLDER", "jpeg", "jxl"),
    ("jxl_jpeg_transcoder", "EXPORT_JPEG_FOLDER", "jxl", "jpeg"),
])
def test_export_folder_name_reads_the_childs_setting(monkeypatch, modname, attr,
                                                     origin, dest):
    import importlib
    mod = importlib.import_module(modname)
    monkeypatch.setattr(mod, attr, "EDITED_IN_SCRIPT")
    assert wp._export_folder_name(origin, dest) == "EDITED_IN_SCRIPT"


def test_default_export_marker_is_always_passed_to_the_child(tmp_path, monkeypatch):
    """Omitting --export-marker when it equalled "_EXPORT" let a child whose
    EXPORT_MARKER was edited in its script anchor on a DIFFERENT marker than
    the wrapper detected with. The wrapper's marker is always passed."""
    menu = _menu(tmp_path, monkeypatch)
    menu.config.config.export_marker = "_EXPORT"
    wf = {"mode_config": {}, "distance": 0.1, "effort": 7}
    cmd = menu._build_manifest_entry_cmd(
        "jxl_tiff_encoder.py", str(tmp_path), str(tmp_path / "o"), 6,
        "tiff", "jxl", 2, wf, {})
    assert cmd[cmd.index("--export-marker") + 1] == "_EXPORT"


# ---------------------------------------------------------------------------
# W2-1a — RenameFrom refuses on the lossless path, up front
# ---------------------------------------------------------------------------

def _lossless_wf(tmp_path, src, advanced=None, **over):
    wf = {
        "origin_format": "jxl", "dest_format": "jpeg", "mode": 2,
        "input_dir": str(src), "workers": 2, "effort": 7, "quality": 95,
        "staging": None, "dry_run": False, "bit_depth": 16,
        "mode_config": {}, "advanced_options": dict(advanced or {}),
        "conversion_type": "jxl_to_jpeg_lossless", "icc_profile": None,
    }
    wf.update(over)
    return wf


def test_w2_1a_manifest_lossless_rename_refused_up_front(tmp_path, monkeypatch,
                                                         capsys):
    src, dest = tmp_path / "a", tmp_path / "out"
    src.mkdir()
    menu = _menu(tmp_path, monkeypatch)
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_lossy_delete_skipped",
                        lambda self, wf: None)
    monkeypatch.setattr(wp.FolderAnalyzer, "detect_mode_for_entry",
                        lambda self, s, d, original_mode=0: original_mode)
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp, "console", None)
    launched = []
    monkeypatch.setattr(wp.InteractiveMenu, "_run_subprocess",
                        lambda self, cmd: (launched.append(cmd), 0)[1])
    wf = _lossless_wf(tmp_path, src, mode=99)
    wf["manifest_entries"] = [(str(src), str(dest), 2)]
    wf["manifest_row_options"] = [{"rename_from": "x", "rename_to": "y"}]
    ok = menu._execute_manifest_workflow(wf, {"cjxl": True})
    assert ok is False
    assert not launched, "the refusal must come before any child runs"
    out = " ".join(capsys.readouterr().out.split())
    assert "rename-from" in out and "lossless" in out


def test_w2_1a_direct_lossless_rename_refused_up_front(tmp_path, monkeypatch):
    src = tmp_path / "a"
    src.mkdir()
    menu = _menu(tmp_path, monkeypatch)
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp, "console", None)
    launched = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600:
                        (launched.append(cmd), 0)[1])
    wf = _lossless_wf(tmp_path, src,
                      advanced={"rename_from": "x", "rename_to": "y"})
    ok = menu.execute_workflow(wf, {"cjxl": True})
    assert ok is False
    assert not launched


def test_w2_1a_direct_lossless_output_icc_warns_not_refuses(tmp_path,
                                                            monkeypatch,
                                                            capsys):
    src = tmp_path / "a"
    src.mkdir()
    menu = _menu(tmp_path, monkeypatch)
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp, "console", None)
    launched = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600:
                        (launched.append(cmd), 0)[1])
    wf = _lossless_wf(tmp_path, src, advanced={"output_icc": "sRGB"})
    ok = menu.execute_workflow(wf, {"cjxl": True})
    assert ok is True
    assert len(launched) == 1
    out = " ".join(capsys.readouterr().out.split())
    assert "OutputICC is ignored" in out


# ---------------------------------------------------------------------------
# W2-1b — auto mode: a jbrd file keeps its original stem in the scan
# ---------------------------------------------------------------------------

def _w2_1b_call(tmp_path, monkeypatch, conversion_type, jbrd_map):
    src_a = tmp_path / "A"
    src_b = tmp_path / "B"
    dest = tmp_path / "out"
    src_a.mkdir()
    src_b.mkdir()
    dest.mkdir()
    f_a = src_a / "x.jxl"
    f_b = src_b / "x.jxl"
    f_a.write_bytes(b"x")
    f_b.write_bytes(b"x")
    entries = [(str(src_a), str(dest), 2), (str(src_b), str(dest), 2)]
    renames = [("x", "a"), ("", "")]
    real_has = tr.has_jbrd_box

    def fake_has(path):
        return jbrd_map.get(os.path.normcase(str(path)), False)

    monkeypatch.setattr(tr, "has_jbrd_box", fake_has)
    try:
        menu = _menu(tmp_path, monkeypatch)
        return menu._manifest_output_collisions(
            entries, {".jxl"}, origin="jxl", dest="jpeg",
            row_renames=renames, conversion_type=conversion_type)
    finally:
        monkeypatch.undo()
        tr.has_jbrd_box = real_has


def test_w2_1b_auto_mode_jbrd_file_keeps_original_stem(tmp_path, monkeypatch):
    """File A has a jbrd box: the recovery keeps x.jpg; file B has none and
    would be renamed x->a. The scan must see the REAL outputs collide."""
    jbrd_map = {os.path.normcase(str(tmp_path / "A" / "x.jxl")): True,
                os.path.normcase(str(tmp_path / "B" / "x.jxl")): False}
    cols = _w2_1b_call(tmp_path, monkeypatch, "jxl_to_jpeg_auto", jbrd_map)
    assert len(cols) == 1, "a.jbrd recovery writes x.jpg — the collision is real"


def test_w2_1b_auto_mode_no_jbrd_rename_applies(tmp_path, monkeypatch):
    """No jbrd anywhere: B renames a→(nothing), A renames x→a: no output
    shares a name."""
    jbrd_map = {}
    cols = _w2_1b_call(tmp_path, monkeypatch, "jxl_to_jpeg_auto", jbrd_map)
    assert cols == []


def test_w2_1b_lossless_never_renames(tmp_path, monkeypatch):
    """lossless keeps the original name even without jbrd probes."""
    cols = _w2_1b_call(tmp_path, monkeypatch, "jxl_to_jpeg_lossless", {})
    assert len(cols) == 1


# ---------------------------------------------------------------------------
# W2-2 — the derivative-in-place guard iterates resolved_entries
# ---------------------------------------------------------------------------

def test_w2_2_legacy_derivative_in_place_row_refuses(tmp_path, monkeypatch,
                                                     capsys):
    src = tmp_path / "a"
    src.mkdir()
    menu = _menu(tmp_path, monkeypatch)
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_lossy_delete_skipped",
                        lambda self, wf: None)
    # A legacy row (no Mode cell) whose Destination is the Source resolves
    # to mode 0 — in place.
    monkeypatch.setattr(wp.FolderAnalyzer, "detect_mode_for_entry",
                        lambda self, s, d, original_mode=None: 0)
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp, "console", None)
    launched = []
    monkeypatch.setattr(wp.InteractiveMenu, "_run_subprocess",
                        lambda self, cmd: (launched.append(cmd), 0)[1])
    wf = {"origin_format": "jxl", "dest_format": "jxl", "mode": 99,
          "workers": 2, "quality": 95, "bit_depth": 16, "staging": None,
          "dry_run": False, "mode_config": {}, "conversion_type":
          "jxl_recompress", "icc_profile": None, "distance": 1.0, "effort": 7,
          "advanced_options": {},
          "manifest_entries": [(str(src), str(src), None)],
          "manifest_row_options": [{"output_icc": "sRGB"}]}
    ok = menu._execute_manifest_workflow(wf, {"cjxl": True})
    assert ok is False
    assert not launched, "the child must never be asked to run a derivative in place"
    out = " ".join(capsys.readouterr().out.split())
    assert "in place" in out


# ---------------------------------------------------------------------------
# W2-3 / W2-4 — a child exiting 130 stops the manifest, header sums
# ---------------------------------------------------------------------------

def _manifest_2_rows(tmp_path, monkeypatch, rcs, capsys):
    src_a, src_b = tmp_path / "A", tmp_path / "B"
    dest_a, dest_b = tmp_path / "oA", tmp_path / "oB"
    src_a.mkdir()
    src_b.mkdir()
    (src_a / "fA.jxl").write_bytes(b"x")
    (src_b / "fB.jxl").write_bytes(b"x")
    menu = _menu(tmp_path, monkeypatch)
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_lossy_delete_skipped",
                        lambda self, wf: None)
    monkeypatch.setattr(wp.FolderAnalyzer, "detect_mode_for_entry",
                        lambda self, s, d, original_mode=0: original_mode)
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp, "console", None)
    seq = iter(rcs)
    launched = []
    monkeypatch.setattr(wp.InteractiveMenu, "_run_subprocess",
                        lambda self, cmd: (launched.append(cmd), next(seq))[1])
    wf = {"origin_format": "jxl", "dest_format": "jxl", "mode": 99,
          "workers": 2, "quality": 95, "bit_depth": 16, "staging": None,
          "dry_run": False, "mode_config": {}, "conversion_type":
          "jxl_recompress", "icc_profile": None, "distance": 1.0, "effort": 7,
          "advanced_options": {},
          "manifest_entries": [(str(src_a), str(dest_a), 1),
                               (str(src_b), str(dest_b), 1)],
          }
    ok = menu._execute_manifest_workflow(wf, {"cjxl": True})
    return ok, launched, " ".join(capsys.readouterr().out.split())


    ok = menu._execute_manifest_workflow(wf, {"cjxl": True})
    return ok, launched, " ".join(capsys.readouterr().out.split())


def test_w2_3_and_w2_4_rc130_stops_and_header_sums(tmp_path, monkeypatch,
                                                   capsys):
    ok, launched, out = _manifest_2_rows(tmp_path, monkeypatch, [130, 0],
                                         capsys)
    assert ok is False
    assert len(launched) == 1, "a child interrupted with 130 must stop the manifest"
    # W2-4: the header's parts must add up: 2 = 0 ok + 1 failure + 1 not
    # started.
    assert "Manifest complete: 2 entries - 0 ok, 1 with failures, 0 cancelled, 1 not started" in out
    assert "interrupted" in out and "not started" in out


def test_w2_3_rc2_behaviour_unchanged(tmp_path, monkeypatch, capsys):
    ok, launched, out = _manifest_2_rows(tmp_path, monkeypatch, [2, 0], capsys)
    assert ok is False
    assert len(launched) == 1
    assert "Entry aborted (exit 2)" in out


def test_w2_4_normal_run_header_unchanged(tmp_path, monkeypatch, capsys):
    ok, launched, out = _manifest_2_rows(tmp_path, monkeypatch, [0, 0], capsys)
    assert ok is True
    assert len(launched) == 2
    assert "Manifest complete: 2 entries - 2 ok, 0 with failures, 0 cancelled" in out
    assert "not started" not in out


# ---------------------------------------------------------------------------
# F-7 — the run-scoped warned set resets per run
# ---------------------------------------------------------------------------

def test_f7_exclude_warning_repeats_on_the_next_run(tmp_path, monkeypatch,
                                                    capsys):
    """Same menu session, two manifest runs of an unsupported direction: the
    builder's "at most once per RUN" warning must not go mute on the second
    run (pre-fix the set survived on `self`; it is only consulted via the
    builder's at_most_once path)."""
    menu = _menu(tmp_path, monkeypatch)
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_lossy_delete_skipped",
                        lambda self, wf: None)
    monkeypatch.setattr(wp.FolderAnalyzer, "detect_mode_for_entry",
                        lambda self, s, d, original_mode=0: original_mode)
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp, "console", None)
    monkeypatch.setattr(wp.InteractiveMenu, "_run_subprocess",
                        lambda self, cmd: 0)
    src_a, src_b = tmp_path / "A", tmp_path / "B"
    dest_a, dest_b = tmp_path / "oA", tmp_path / "oB"
    src_a.mkdir()
    src_b.mkdir()
    (src_a / "fA.jxl").write_bytes(b"x")
    (src_b / "fB.jxl").write_bytes(b"x")

    def one_run():
        wf = {"origin_format": "jxl", "dest_format": "jxl", "mode": 99,
              "workers": 2, "quality": 95, "bit_depth": 16, "staging": None,
              "dry_run": False, "mode_config": {}, "conversion_type":
              "jxl_recompress", "icc_profile": None, "distance": 1.0,
              "effort": 7, "advanced_options": {},
              "exclude_folders": "_EXPORT",
              "manifest_entries": [(str(src_a), str(dest_a), 1),
                                   (str(src_b), str(dest_b), 1)]}
        assert menu.execute_workflow(wf, {"cjxl": True}) is True
        return capsys.readouterr().out.count("IGNORED")

    first = one_run()
    second = one_run()
    assert first == 1
    assert second == 1, "a run-scoped warning must not stay mute on a second run"
