#!/usr/bin/env python3
"""`--exclude-folders`: folder-NAME exclusions from discovery (plan §6.2).

The flag turns "every TIFF/JXL under the root, except the `_EXPORT` trees"
into a real run. It is a segment match, case-insensitive, evaluated
RELATIVE TO THE INPUT ROOT: the root's own name and its ancestors never
exclude, pointing the input AT an excluded name still processes it, and a
path that somehow falls outside the root fails CLOSED. The same helper is
duplicated in the encoder, the decoder and the wrapper; the wrapper's copy
filters the manifest collision walk, which never passes through a child's
finders.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_photo as wp
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent


def _menu(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


@pytest.fixture
def menu(tmp_path, monkeypatch):
    return _menu(tmp_path, monkeypatch)


def _fake_tree(root: Path):
    """The plan §6.2 tree: f is at the root, x inside a marker tree, y inside
    a look-alike that must NOT match, z in a sibling root."""
    (root / "JobA").mkdir(parents=True, exist_ok=True)
    (root / "JobA" / "f.tif").write_bytes(b"x")
    (root / "JobA" / "_EXPORT" / "TIFF16").mkdir(parents=True)
    (root / "JobA" / "_EXPORT" / "TIFF16" / "x.tif").write_bytes(b"x")
    (root / "JobA" / "My_EXPORT_photos").mkdir(parents=True)
    (root / "JobA" / "My_EXPORT_photos" / "y.tif").write_bytes(b"x")
    (root / "JobB").mkdir(parents=True, exist_ok=True)
    (root / "JobB" / "z.tif").write_bytes(b"x")


# ── helper unit: segment match, anchored at the root, fail-closed ───────────

@pytest.mark.parametrize("helper", [enc._path_excluded_below_root,
                                    dec._path_excluded_below_root,
                                    wp._path_excluded_below_root])
def test_helper_matches_a_folder_segment(helper, tmp_path):
    assert helper(tmp_path / "_export" / "x.tif", tmp_path, ("_export",)) is True


@pytest.mark.parametrize("helper", [enc._path_excluded_below_root,
                                    dec._path_excluded_below_root,
                                    wp._path_excluded_below_root])
def test_helper_match_is_case_insensitive(helper, tmp_path):
    assert helper(tmp_path / "_EXPORT" / "x.tif", tmp_path, ("_export",)) is True


@pytest.mark.parametrize("helper", [enc._path_excluded_below_root,
                                    dec._path_excluded_below_root,
                                    wp._path_excluded_below_root])
def test_helper_never_matches_a_longer_name(helper, tmp_path):
    """`_export` must not bite My_EXPORT_photos: segment, not substring."""
    assert helper(tmp_path / "My_EXPORT_photos" / "y.tif", tmp_path,
                  ("_export",)) is False


@pytest.mark.parametrize("helper", [enc._path_excluded_below_root,
                                    dec._path_excluded_below_root,
                                    wp._path_excluded_below_root])
def test_helper_never_excludes_the_root_itself_or_an_ancestor(helper, tmp_path):
    """The root's own name is above the root, so it cannot exclude."""
    root = tmp_path / "_EXPORT"
    assert helper(root / "f.tif", root, ("_export",)) is False
    assert helper(root / "deep" / "f.tif", root, ("_export",)) is False


@pytest.mark.parametrize("helper", [enc._path_excluded_below_root,
                                    dec._path_excluded_below_root,
                                    wp._path_excluded_below_root])
def test_helper_root_pointed_at_the_excluded_name_keeps_the_file(helper, tmp_path):
    """An empty relative path means nothing is BELOW the root."""
    root = tmp_path / "_EXPORT"
    assert helper(root / "x.tif", root, ("_export",)) is False


@pytest.mark.parametrize("helper", [enc._path_excluded_below_root,
                                    dec._path_excluded_below_root,
                                    wp._path_excluded_below_root])
def test_helper_fails_closed_outside_the_root(helper, tmp_path):
    """Not under the root (should not happen): test the whole path."""
    root = tmp_path / "root"
    outside = tmp_path / "_export" / "x.tif"
    assert helper(outside, root, ("_export",)) is True


@pytest.mark.parametrize("helper", [enc._path_excluded_below_root,
                                    dec._path_excluded_below_root,
                                    wp._path_excluded_below_root])
def test_helper_without_names_or_root_is_inert(helper, tmp_path):
    assert helper(tmp_path / "_export" / "x.tif", tmp_path, ()) is False
    assert helper(tmp_path / "_export" / "x.tif", None, ("_export",)) is False


# ── finder level: the module global filters discovery ───────────────────────

def test_encoder_finder_excludes_the_named_tree(tmp_path, monkeypatch):
    _fake_tree(tmp_path)
    monkeypatch.setattr(enc, "EXCLUDE_FOLDERS", ("_export",))
    found = {p.name for p in enc.find_tiffs_recursive(tmp_path)}
    assert found == {"f.tif", "y.tif", "z.tif"}


def test_encoder_finder_still_processes_when_root_is_the_excluded_name(
        tmp_path, monkeypatch):
    _fake_tree(tmp_path)
    monkeypatch.setattr(enc, "EXCLUDE_FOLDERS", ("_export",))
    found = {p.name for p in enc.find_tiffs_recursive(tmp_path / "JobA" / "_EXPORT")}
    assert found == {"x.tif"}


def test_decoder_finder_excludes_the_named_tree(tmp_path, monkeypatch):
    (tmp_path / "JobA").mkdir(parents=True)
    (tmp_path / "JobA" / "f.jxl").write_bytes(b"x")
    (tmp_path / "JobA" / "_EXPORT" / "TIFF16").mkdir(parents=True)
    (tmp_path / "JobA" / "_EXPORT" / "TIFF16" / "x.jxl").write_bytes(b"x")
    (tmp_path / "JobA" / "My_EXPORT_photos").mkdir(parents=True)
    (tmp_path / "JobA" / "My_EXPORT_photos" / "y.jxl").write_bytes(b"x")
    (tmp_path / "JobB").mkdir(parents=True)
    (tmp_path / "JobB" / "z.jxl").write_bytes(b"x")

    monkeypatch.setattr(dec, "EXCLUDE_FOLDERS", ("_export",))
    found = {p.name for p in dec.find_jxls_recursive(tmp_path)}
    assert found == {"f.jxl", "y.jxl", "z.jxl"}
    assert {p.name for p in dec.find_jxls_recursive(tmp_path / "JobA" / "_EXPORT")} \
        == {"x.jxl"}


def test_mode6_composes_with_the_exclusion(tmp_path, monkeypatch):
    """Excluding one marker subfolder leaves the other marker trees."""
    (tmp_path / "_EXPORT" / "TIFF16").mkdir(parents=True)
    (tmp_path / "_EXPORT" / "TIFF16" / "a.tif").write_bytes(b"x")
    (tmp_path / "_EXPORT" / "OTHER").mkdir(parents=True)
    (tmp_path / "_EXPORT" / "OTHER" / "b.tif").write_bytes(b"x")
    monkeypatch.setattr(enc, "EXCLUDE_FOLDERS", ("tiff16",))
    assert [p.name for p in enc.find_tiffs_mode6(tmp_path)] == ["b.tif"]


# ── CLI validation: folder NAMES, not paths ─────────────────────────────────

def test_encoder_cli_rejects_a_path_in_exclude_folders(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    r = subprocess.run(
        [sys.executable, str(REPO / "jxl_tiff_encoder.py"), str(src),
         "--exclude-folders", "foo\\bar"],
        capture_output=True, text=True, cwd=str(tmp_path),
        stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 2
    assert "folder NAMES, not paths" in (r.stdout + r.stderr)


# ── wrapper command builders ────────────────────────────────────────────────

def _capture_cmd(menu, monkeypatch, workflow):
    cmds = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600: (cmds.append(cmd), 0)[1])
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp, "console", None)
    assert menu.execute_workflow(workflow, {"cjxl": True})
    assert cmds, "no child command was built"
    return cmds[0]


def _tiff_workflow(tmp_path, **over):
    wf = {"origin_format": "tiff", "dest_format": "jxl", "mode": 7,
          "input_dir": str(tmp_path), "workers": 2, "effort": 7,
          "distance": 0.05, "use_ram": True, "staging": None,
          "dry_run": False, "mode_config": {},
          "advanced_options": {"sync": True},
          "conversion_type": "jxl_tiff_encoder"}
    wf.update(over)
    return wf


def _jxl_tiff_workflow(tmp_path, **over):
    wf = {"origin_format": "jxl", "dest_format": "tiff", "mode": 7,
          "input_dir": str(tmp_path), "workers": 2, "compression": "zip",
          "bit_depth": 16, "add_preview": True, "staging": None,
          "dry_run": False, "mode_config": {},
          "advanced_options": {"sync": True},
          "conversion_type": "jxl_tiff_decoder"}
    wf.update(over)
    return wf


def _jxl_jxl_workflow(tmp_path, **over):
    wf = {"origin_format": "jxl", "dest_format": "jxl", "mode": 7,
          "input_dir": str(tmp_path), "workers": 2, "effort": 7,
          "distance": 1.0, "staging": None, "dry_run": False,
          "mode_config": {}, "advanced_options": {"sync": True},
          "conversion_type": "jxl_recompress"}
    wf.update(over)
    return wf


def test_tiff_to_jxl_builder_emits_the_flag(menu, tmp_path, monkeypatch):
    cmd = _capture_cmd(menu, monkeypatch,
                       _tiff_workflow(tmp_path, exclude_folders="_EXPORT"))
    assert cmd[cmd.index("--exclude-folders") + 1] == "_EXPORT"


def test_jxl_to_tiff_builder_emits_the_flag(menu, tmp_path, monkeypatch):
    cmd = _capture_cmd(menu, monkeypatch,
                       _jxl_tiff_workflow(tmp_path, exclude_folders="_EXPORT"))
    assert cmd[cmd.index("--exclude-folders") + 1] == "_EXPORT"


def test_builder_omits_the_flag_when_empty(menu, tmp_path, monkeypatch):
    for value in (None, ""):
        cmd = _capture_cmd(menu, monkeypatch,
                           _tiff_workflow(tmp_path, exclude_folders=value))
        assert "--exclude-folders" not in cmd


def test_jxl_to_jxl_warns_and_omits_the_flag(menu, tmp_path, monkeypatch, capsys):
    cmd = _capture_cmd(menu, monkeypatch,
                       _jxl_jxl_workflow(tmp_path, exclude_folders="_EXPORT"))
    assert "--exclude-folders" not in cmd
    assert "IGNORED" in capsys.readouterr().out


# ── #297-class pin: the delete panel counts what the child will see ─────────

def test_count_origin_files_matches_the_finder_under_exclusion(menu, tmp_path):
    (tmp_path / "_EXPORT" / "TIFF16").mkdir(parents=True)
    (tmp_path / "_EXPORT" / "TIFF16" / "x.tif").write_bytes(b"x")
    (tmp_path / "f.tif").write_bytes(b"x")
    workflow = {"input_dir": str(tmp_path), "origin_format": "tiff",
                "dest_format": "jxl", "exclude_folders": "_EXPORT"}
    # NOTE: no monkeypatch of enc.EXCLUDE_FOLDERS. The exclusion must reach the
    # child's finder through _count_origin_files -> _with_child_marker (which
    # sets the child global itself). Pre-setting the global made this pin
    # vacuous: it passed even if the workflow value were never plumbed.
    assert menu._count_origin_files(workflow, 8) == 1
    # ...and it equals the child's finder run under the SAME exclusion, applied
    # the same way the wrapper applies it.
    with wp._with_child_marker(enc, None, None, None, "_EXPORT"):
        expected = len(enc.find_tiffs_recursive(tmp_path))
    assert menu._count_origin_files(workflow, 8) == expected


# ── collision walk filters the excluded tree (plan §4.5) ────────────────────

def test_collision_walk_skips_the_excluded_tree(menu, tmp_path):
    src = tmp_path / "src"
    (src / "_EXPORT").mkdir(parents=True)
    (src / "x.tif").write_bytes(b"x")
    (src / "_EXPORT" / "x.tif").write_bytes(b"x")
    dest = tmp_path / "out"
    entries = [(str(src), str(dest), 2)]

    assert menu._manifest_output_collisions(
        entries, {".tif"}, origin="tiff", dest="jxl",
        row_excludes=["_EXPORT"]) == []

    collisions = menu._manifest_output_collisions(
        entries, {".tif"}, origin="tiff", dest="jxl", row_excludes=None)
    assert collisions, "the phantom collision the filter exists to drop"


# ── manifest ExcludeFolders cell semantics ──────────────────────────────────

def _parse(cells):
    return wp._parse_manifest_row_options(cells, "tiff2jxl", lambda p: p)


def test_manifest_filled_cell_is_a_string():
    opts, err = _parse({"excludefolders": "_EXPORT;temp"})
    assert err is None
    assert opts["exclude_folders"] == "_EXPORT;temp"


def test_manifest_empty_cell_keeps_the_run_value():
    opts, err = _parse({"excludefolders": ""})
    assert err is None
    assert "exclude_folders" not in opts


@pytest.mark.parametrize("cell", ["-", "none", "NONE"])
def test_manifest_removal_cell_is_present_none(cell):
    opts, err = _parse({"excludefolders": cell})
    assert err is None
    assert "exclude_folders" in opts
    assert opts["exclude_folders"] is None


def test_manifest_path_cell_refuses_the_whole_manifest():
    opts, err = _parse({"excludefolders": "foo\\bar"})
    assert opts is None
    assert "folder NAMES, not paths" in err


def test_row_removal_beats_the_run_value():
    workflow = {"exclude_folders": "_EXPORT"}
    wf_row, _ = wp._row_effective_options(workflow, {},
                                          {"exclude_folders": None},
                                          "tiff", "jxl")
    assert wf_row["exclude_folders"] is None
    assert workflow["exclude_folders"] == "_EXPORT", "the run dict was mutated"


# ── session replay: a hand-edited list is refused ───────────────────────────

@pytest.mark.parametrize("raw", ["_EXPORT", "", None])
def test_session_accepts_string_or_empty_exclude_folders(raw):
    assert wp._session_number_error({"last_exclude_folders": raw}) is None


def test_session_refuses_a_non_string_exclude_folders():
    err = wp._session_number_error({"last_exclude_folders": ["_EXPORT"]})
    assert err is not None and "exclude_folders" in err
