"""Round 31 — a second archive cycle of a multi-page scan (bug #303).

The shape that started this: a film scan is [real, thumbnail, IR]. With the
default `--multipage-mode split --thumbnail-mode exclude` the encoder writes
pages {0, 2}, because excluded thumbnails do NOT renumber the real pages. Decode
that and the reconstructed TIFF is [real, IR] — so re-encoding it in the same
folder writes pages {0, 1} and leaves `_page2.jxl` behind.

The group id used to be a hash of the SOURCE PATH alone, which does not change
between those two runs, so all three files claimed the same group. The decoder
merged them and wrote a TIFF with the IR page twice, reporting `0 errors` and
exiting 0.

Three defences, tested here:
  * the encoder's group id now identifies the SPLIT, so a leftover cannot join
    a later group at all;
  * the encoder names leftovers of a previous split at archive time;
  * the decoder refuses to merge a group with more members than the split
    recorded — resolving it via jxlphoto-srcsum when it can, and failing closed
    (standalone decodes + a real error) when it cannot. This is what repairs
    archives already written by the old encoder.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def enc():
    return _load("enc_r31", REPO / "jxl_tiff_encoder.py")


@pytest.fixture
def dec():
    return _load("dec_r31", REPO / "jxl_tiff_decoder.py")


# ---------------------------------------------------------------------------
# encoder: the group id identifies the SPLIT, not just the source
# ---------------------------------------------------------------------------

def _items(enc_mod, tiff: Path, out: Path, pages):
    """group_items tuples: (tiff, final_jxl, page_idx, is_thumb, subfiletype, samples)."""
    return [(tiff, out / enc_mod._page_output_name(tiff.stem, p, th), p, th, 0, 3)
            for p, th in pages]


def _group_ids(enc_mod, tiff: Path, out: Path, pages, monkeypatch):
    """Run process_group's planning far enough to read the group ids it assigns."""
    captured = {}

    def _fake_convert_one(tiff_path, write_path, final_path, page_idx=0,
                          is_thumbnail=False, subfiletype=0, samples=3,
                          multipage_group=None, multipage_total=None):
        captured[(str(tiff_path), page_idx)] = multipage_group
        return ((str(tiff_path), page_idx), "ok", str(final_path), tiff_path)

    monkeypatch.setattr(enc_mod, "convert_one", _fake_convert_one)
    monkeypatch.setattr(enc_mod, "DELETE_SOURCE", False)
    enc_mod.process_group(_items(enc_mod, tiff, out, pages), workers=1, mode=2)
    return captured


def test_group_id_changes_when_the_split_shape_changes(enc, tmp_path, monkeypatch):
    """{0, 2} and {0, 1} are different splits of the same file and must not
    share a group id — that sharing is what let a leftover be merged in."""
    tiff = tmp_path / "scan.tif"
    tiff.write_bytes(b"II*\x00")
    out = tmp_path / "out"
    out.mkdir()

    first = _group_ids(enc, tiff, out, [(0, False), (2, False)], monkeypatch)
    second = _group_ids(enc, tiff, out, [(0, False), (1, False)], monkeypatch)

    id_first = first[(str(tiff), 0)]
    id_second = second[(str(tiff), 0)]
    assert id_first and id_second
    assert id_first != id_second


def test_group_id_is_stable_when_nothing_changes(enc, tmp_path, monkeypatch):
    """Re-encoding the same structure must keep the id: the outputs simply
    overwrite each other, which is what a sync run is for."""
    tiff = tmp_path / "scan.tif"
    tiff.write_bytes(b"II*\x00")
    out = tmp_path / "out"
    out.mkdir()

    a = _group_ids(enc, tiff, out, [(0, False), (2, False)], monkeypatch)
    b = _group_ids(enc, tiff, out, [(0, False), (2, False)], monkeypatch)
    assert a[(str(tiff), 0)] == b[(str(tiff), 0)]


# ---------------------------------------------------------------------------
# encoder: leftovers of a previous split are named at archive time
# ---------------------------------------------------------------------------

def test_stale_page_outputs_are_reported(enc, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    tiff = tmp_path / "scan.tif"
    tiff.write_bytes(b"II*\x00")
    # This run writes {0, 1}; _page2.jxl is left from an earlier {0, 2} split.
    for name in ("scan.jxl", "scan_page1.jxl", "scan_page2.jxl"):
        (out / name).write_bytes(b"\x00")

    tasks = [(tiff, out / "scan.jxl", out / "scan.jxl", 0, False, 0, 3, "gid", 2),
             (tiff, out / "scan_page1.jxl", out / "scan_page1.jxl", 1, False, 0, 3, "gid", 2)]
    stale = enc._warn_stale_split_outputs(tasks)
    assert [p.name for p in stale] == ["scan_page2.jxl"]


def test_stale_scan_ignores_files_of_other_sources(enc, tmp_path):
    """`other_page1.jxl` belongs to a source this run is not splitting."""
    out = tmp_path / "out"
    out.mkdir()
    tiff = tmp_path / "scan.tif"
    tiff.write_bytes(b"II*\x00")
    (out / "scan.jxl").write_bytes(b"\x00")
    (out / "other_page1.jxl").write_bytes(b"\x00")
    (out / "unrelated.jxl").write_bytes(b"\x00")

    tasks = [(tiff, out / "scan.jxl", out / "scan.jxl", 0, False, 0, 3, "gid", 2)]
    assert enc._warn_stale_split_outputs(tasks) == []


def test_stale_scan_is_silent_without_a_split(enc, tmp_path):
    """A standalone conversion (group_id None) leaves no page trail to check."""
    out = tmp_path / "out"
    out.mkdir()
    tiff = tmp_path / "scan.tif"
    tiff.write_bytes(b"II*\x00")
    (out / "scan_page2.jxl").write_bytes(b"\x00")

    tasks = [(tiff, out / "scan.jxl", out / "scan.jxl", 0, False, 0, 3, None, None)]
    assert enc._warn_stale_split_outputs(tasks) == []


# ---------------------------------------------------------------------------
# decoder: a group with MORE members than the split recorded
# ---------------------------------------------------------------------------

def _entry(dec_mod, path: Path, page: int, thumb: bool = False):
    return (path, page, thumb, False, 0, False, 16)


def test_srcsum_singles_out_the_real_split(dec):
    """Pages 0 and 1 share a srcsum; the leftover page 2 does not."""
    a, b, c = Path("scan.jxl"), Path("scan_page1.jxl"), Path("scan_page2.jxl")
    entries = [_entry(dec, a, 0), _entry(dec, b, 1), _entry(dec, c, 2)]
    srcsum = {str(a): "aaaa", str(b): "aaaa", str(c): "bbbb"}

    kept, orphans = dec._split_group_by_srcsum(entries, 2, srcsum)
    assert [e[0].name for e in kept] == ["scan.jxl", "scan_page1.jxl"]
    assert [e[0].name for e in orphans] == ["scan_page2.jxl"]


def test_srcsum_refuses_when_every_member_shares_one(dec):
    """No evidence at all: 'cannot tell' must never guess."""
    a, b, c = Path("scan.jxl"), Path("scan_page1.jxl"), Path("scan_page2.jxl")
    entries = [_entry(dec, a, 0), _entry(dec, b, 1), _entry(dec, c, 2)]
    srcsum = {str(a): "aaaa", str(b): "aaaa", str(c): "aaaa"}
    assert dec._split_group_by_srcsum(entries, 2, srcsum) == (None, None)


def test_srcsum_refuses_when_a_member_has_no_marker(dec):
    a, b, c = Path("scan.jxl"), Path("scan_page1.jxl"), Path("scan_page2.jxl")
    entries = [_entry(dec, a, 0), _entry(dec, b, 1), _entry(dec, c, 2)]
    srcsum = {str(a): "aaaa", str(b): "aaaa", str(c): None}
    assert dec._split_group_by_srcsum(entries, 2, srcsum) == (None, None)


def test_srcsum_refuses_when_two_buckets_are_complete(dec):
    a, b, c, d = (Path("s.jxl"), Path("s_page1.jxl"),
                  Path("t.jxl"), Path("t_page1.jxl"))
    entries = [_entry(dec, a, 0), _entry(dec, b, 1),
               _entry(dec, c, 2), _entry(dec, d, 3)]
    srcsum = {str(a): "aaaa", str(b): "aaaa", str(c): "bbbb", str(d): "bbbb"}
    assert dec._split_group_by_srcsum(entries, 2, srcsum) == (None, None)


def _markers(**over):
    base = {'group': None, 'inherited': False, 'subfiletype': 0, 'grayscale': False,
            'depth': 16, 'page': None, 'pages': None, 'thumb': False, 'srcsum': None}
    base.update(over)
    return base


def test_leftover_is_split_out_instead_of_merged(dec, tmp_path, monkeypatch):
    """End to end through collect_multipage_groups: the leftover becomes its
    own group and the real split keeps exactly its recorded pages."""
    files = {}
    for name, page, ss in (("scan.jxl", 0, "aaaa"),
                           ("scan_page1.jxl", 1, "aaaa"),
                           ("scan_page2.jxl", 2, "bbbb")):
        p = tmp_path / name
        p.write_bytes(b"\x00")
        files[str(p)] = _markers(group="G", page=page, pages=2, srcsum=ss)

    monkeypatch.setattr(dec, "_read_multipage_markers_batch", lambda jxls: files)
    groups = dec.collect_multipage_groups([Path(p) for p in files])

    by_name = {k.name: [e[0].name for e in v] for k, v in groups.items()}
    assert by_name["scan.jxl"] == ["scan.jxl", "scan_page1.jxl"]
    assert by_name["scan_page2.jxl"] == ["scan_page2.jxl"]
    assert dec._group_conflicts == []


def test_unresolvable_conflict_refuses_to_merge_and_is_an_error(dec, tmp_path, monkeypatch):
    files = {}
    for name, page in (("scan.jxl", 0), ("scan_page1.jxl", 1), ("scan_page2.jxl", 2)):
        p = tmp_path / name
        p.write_bytes(b"\x00")
        files[str(p)] = _markers(group="G", page=page, pages=2, srcsum="same")

    monkeypatch.setattr(dec, "_read_multipage_markers_batch", lambda jxls: files)
    groups = dec.collect_multipage_groups([Path(p) for p in files])

    # Every member decodes on its own — nothing is lost, nothing is merged.
    assert all(len(v) == 1 for v in groups.values())
    assert sorted(k.name for k in groups) == [
        "scan.jxl", "scan_page1.jxl", "scan_page2.jxl"]
    # ...and the run must not report success.
    assert len(dec._group_conflicts) == 1


def test_a_truncated_group_is_still_only_a_warning(dec, tmp_path, monkeypatch):
    """Fewer members than recorded keeps its old behaviour: decode what is
    there, warn, and let the delete gate keep the sources."""
    files = {}
    for name, page in (("scan.jxl", 0),):
        p = tmp_path / name
        p.write_bytes(b"\x00")
        files[str(p)] = _markers(group="G", page=page, pages=3, srcsum="aaaa")

    monkeypatch.setattr(dec, "_read_multipage_markers_batch", lambda jxls: files)
    groups = dec.collect_multipage_groups([Path(p) for p in files])

    assert len(groups) == 1
    assert dec._group_conflicts == []
    assert set(dec._incomplete_groups.values()) == {"truncated"}


def test_a_complete_group_is_untouched(dec, tmp_path, monkeypatch):
    """The film-scan shape {0, 2} with a recorded count of 2 is COMPLETE — an
    index gap is not a missing page."""
    files = {}
    for name, page, ss in (("scan.jxl", 0, "aaaa"), ("scan_page2.jxl", 2, "aaaa")):
        p = tmp_path / name
        p.write_bytes(b"\x00")
        files[str(p)] = _markers(group="G", page=page, pages=2, srcsum=ss)

    monkeypatch.setattr(dec, "_read_multipage_markers_batch", lambda jxls: files)
    groups = dec.collect_multipage_groups([Path(p) for p in files])

    assert len(groups) == 1
    assert [e[1] for e in next(iter(groups.values()))] == [0, 2]
    assert dec._incomplete_groups == {}
    assert dec._group_conflicts == []


# ---------------------------------------------------------------------------
# wrapper: the manifest path reaches the same delete gates as [D]
# ---------------------------------------------------------------------------

@pytest.fixture
def menu(tmp_path, monkeypatch):
    """A menu on a throwaway config — never the user's real one."""
    wp = _load("wp_r31", REPO / "jxl_photo.py")
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    m = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    m._wp = wp
    return m


def _manifest_run(menu, monkeypatch, entries, answer_delete=True):
    """Drive _wizard_run_from_manifest far enough to see the delete gates."""
    wp = menu._wp
    asked = {}

    monkeypatch.setattr(menu, "_pick_manifest", lambda: "m.csv")
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(menu, "_load_manifest_entries", lambda *a, **k: entries)
    monkeypatch.setattr(menu, "_confirm_manifest_entries", lambda *a, **k: True)
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr("builtins.input", lambda *a: "y" if answer_delete else "n")

    def _spy(workflow, collapses, scope_label):
        asked["collapses"] = collapses
        asked["scope"] = scope_label
        workflow["verify_roundtrip"] = True

    monkeypatch.setattr(menu, "_ask_delete_options", _spy)

    wf = {"origin_format": "tiff", "dest_format": "jxl", "mode_config": {}}
    assert menu._wizard_run_from_manifest(wf)
    return wf, asked


def test_manifest_delete_asks_the_same_gates_as_the_D_menu(menu, monkeypatch, tmp_path):
    """A mode-8 manifest that deletes must be offered round-trip verification
    and a provenance choice — it used to reach the unlink with neither."""
    entries = [(str(tmp_path / "a"), str(tmp_path / "a"), 8),
               (str(tmp_path / "b"), str(tmp_path / "b"), 6)]
    wf, asked = _manifest_run(menu, monkeypatch, entries)

    assert wf["delete_source"] is True
    assert asked, "the manifest path never reached the delete gates"
    # Mode 6 collapses folder structure, so the provenance question applies.
    assert asked["collapses"] is True
    assert "6" in asked["scope"]
    assert wf["verify_roundtrip"] is True


def test_manifest_without_collapsing_modes_skips_the_provenance_question(
        menu, monkeypatch, tmp_path):
    entries = [(str(tmp_path / "a"), str(tmp_path / "a"), 8)]
    _wf, asked = _manifest_run(menu, monkeypatch, entries)
    assert asked["collapses"] is False


def test_manifest_declining_the_delete_asks_nothing(menu, monkeypatch, tmp_path):
    entries = [(str(tmp_path / "a"), str(tmp_path / "a"), 8)]
    wf, asked = _manifest_run(menu, monkeypatch, entries, answer_delete=False)
    assert "delete_source" not in wf
    assert asked == {}


def test_legacy_manifest_mode_counts_as_collapsing(menu, monkeypatch, tmp_path):
    """No Mode cell: the mode is detected per folder later and can come back
    6 or 7. Unknown must fail towards asking."""
    entries = [(str(tmp_path / "a"), str(tmp_path / "a"), 8),
               (str(tmp_path / "b"), str(tmp_path / "b"), None)]
    _wf, asked = _manifest_run(menu, monkeypatch, entries)
    assert asked["collapses"] is True
    assert "?" in asked["scope"]


# ---------------------------------------------------------------------------
# wrapper: a preset shows the number its direction is actually steered by
# ---------------------------------------------------------------------------

def test_decode_preset_shows_quality_not_a_stale_distance(menu):
    """save_last_session only overwrites last_distance when a run supplies one,
    so a JXL->JPEG preset carries the distance of the TIFF run before it."""
    session = {
        "last_origin_format": "jxl", "last_dest_format": "jpeg",
        "last_conversion_type": "jxl_to_jpeg_auto",
        "last_output_mode": "0", "last_input_dir": "G:\\x",
        "last_workers": 8, "last_quality": 95, "last_distance": 0.05,
    }
    line = menu._wp.InteractiveMenu._describe_session(session)
    assert "q=95" in line
    assert "d=" not in line


def test_tiff_preset_still_shows_distance(menu):
    session = {
        "last_origin_format": "tiff", "last_dest_format": "jxl",
        "last_conversion_type": "jxl_tiff_encoder",
        "last_output_mode": "6", "last_input_dir": "G:\\x",
        "last_workers": 8, "last_quality": 95, "last_distance": 0.05,
    }
    line = menu._wp.InteractiveMenu._describe_session(session)
    assert "d=0.05" in line


# ---------------------------------------------------------------------------
# decoder: a scalar dc:Relation is one value, not a comma-separated list
# ---------------------------------------------------------------------------

def test_scalar_relation_is_not_split_on_commas(dec, tmp_path, monkeypatch):
    """_read_source_markers_batch has always read the whole string; this copy
    used to split it and would tear a user value like "Smith, John" in two."""
    seen = {}

    class _R:
        returncode = 0
        stdout = '[{"SourceFile": "%s", "Relation": "Smith, John"}]'

    p = tmp_path / "a.jxl"
    p.write_bytes(b"\x00")

    def _fake_run(cmd, **kw):
        r = _R()
        r.stdout = r.stdout % str(p).replace("\\", "/")
        return r

    monkeypatch.setattr(dec.subprocess, "run", _fake_run)
    out = dec._read_multipage_markers_batch([p])
    info = out[str(p)]
    seen.update(info)
    # A user value carries none of our markers; the point is that it was not
    # torn into "Smith" and "John" on the way in.
    assert info["group"] is None and info["srcsum"] is None
