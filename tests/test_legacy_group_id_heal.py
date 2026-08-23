"""Mixed-version multi-page archives: the v2.0.2 group-id change vs old splits.

v2.0.2 (bug #303) changed the split group id from sha256(source path) to
sha256(path | produced page set). The change was unconditional, so archives
split by v2.0.0/v2.0.1 carry the LEGACY id — and re-encoding one lost page of
such an archive stamped it with the NEW id while its skipped siblings kept the
old one. The decoder then saw two truncated groups where every page was in fact
present, and advised the user to go looking for a page that wasn't missing.

Covered here:
  * the encoder adopts the legacy id when the existing siblings unanimously
    carry it, so a re-encoded page heals the archive back into one group;
  * non-unanimous sibling ids (or a sibling the run no longer produces) keep
    the new formula — adoption is proven, never guessed;
  * the decoder recognizes the legacy+new mixture structurally (one folder, one
    stem, one srcsum, disjoint pages, together exactly the recorded split size)
    and advises re-encoding the split instead of hunting for a phantom page.
"""
from __future__ import annotations

import hashlib
import importlib.util
import logging
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
    return _load("enc_leg", REPO / "jxl_tiff_encoder.py")


@pytest.fixture
def dec():
    return _load("dec_leg", REPO / "jxl_tiff_decoder.py")


def _legacy_id(tiff: Path) -> str:
    """The pre-v2.0.2 formula: sha256 of the resolved source path alone."""
    return hashlib.sha256(str(tiff.resolve()).encode("utf-8")).hexdigest()[:16]


def _new_id(tiff: Path, pages) -> str:
    """The v2.0.2 formula: path + the page set the split produces."""
    key = str(tiff.resolve())
    blob = key + "|" + ",".join(f"{p}{'t' if t else ''}" for p, t in sorted(pages))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _planned_ids(enc_mod, tiff: Path, out: Path, pages, monkeypatch, marks=None):
    """Run process_group's planning far enough to read the group ids it assigns."""
    captured = {}

    def _fake_convert_one(tiff_path, write_path, final_path, page_idx=0,
                          is_thumbnail=False, subfiletype=0, samples=3,
                          multipage_group=None, multipage_total=None):
        captured[(str(tiff_path), page_idx)] = multipage_group
        return ((str(tiff_path), page_idx), "ok", str(final_path), tiff_path)

    monkeypatch.setattr(enc_mod, "convert_one", _fake_convert_one)
    monkeypatch.setattr(enc_mod, "DELETE_SOURCE", False)
    if marks is not None:
        # raising=False: pre-fix code has no such reader, and the boundary
        # tests below legitimately PASS there (the old code always used the new
        # formula); only the adoption test must fail against it.
        monkeypatch.setattr(enc_mod, "_read_group_markers_batch",
                            lambda outs: {str(o): marks.get(o.name) for o in outs},
                            raising=False)
    items = [(tiff, out / enc_mod._page_output_name(tiff.stem, p, th), p, th, 0, 3)
             for p, th in pages]
    enc_mod.process_group(items, workers=1, mode=2)
    return captured


# ---------------------------------------------------------------------------
# encoder: a re-encoded page joins its legacy siblings
# ---------------------------------------------------------------------------

def test_reencoded_page_adopts_the_legacy_id(enc, tmp_path, monkeypatch):
    """Pages 0 and 2 of a v2.0.0/v2.0.1 archive survived with the legacy id;
    page 1 is being re-encoded. All three planned pages must be stamped with
    the legacy id so the archive heals into one group."""
    tiff = tmp_path / "scan.tif"
    tiff.write_bytes(b"II*\x00")
    out = tmp_path / "out"
    out.mkdir()
    (out / "scan.jxl").write_bytes(b"\x00")
    (out / "scan_page2.jxl").write_bytes(b"\x00")
    legacy = _legacy_id(tiff)

    pages = [(0, False), (1, False), (2, False)]
    ids = _planned_ids(enc, tiff, out, pages, monkeypatch,
                       marks={"scan.jxl": legacy, "scan_page2.jxl": legacy})

    assert set(ids.values()) == {legacy}


def test_healed_archive_is_one_complete_group_to_the_decoder(enc, dec, tmp_path, monkeypatch):
    """The point of adoption: the decoder merges legacy siblings and the
    re-encoded page without any INCOMPLETE verdict."""
    tiff = tmp_path / "scan.tif"
    tiff.write_bytes(b"II*\x00")
    legacy = _legacy_id(tiff)
    files = {}
    for name, page in (("scan.jxl", 0), ("scan_page1.jxl", 1), ("scan_page2.jxl", 2)):
        p = tmp_path / name
        p.write_bytes(b"\x00")
        files[str(p)] = {'group': legacy, 'inherited': False, 'subfiletype': 0,
                         'grayscale': False, 'depth': 16, 'page': page, 'pages': 3,
                         'thumb': False, 'srcsum': "aaaa"}

    monkeypatch.setattr(dec, "_read_multipage_markers_batch", lambda jxls: files)
    groups = dec.collect_multipage_groups([Path(p) for p in files])

    assert len(groups) == 1
    assert sorted(e[1] for e in next(iter(groups.values()))) == [0, 1, 2]
    assert dec._incomplete_groups == {}
    assert dec._group_conflicts == []


def test_mixed_sibling_ids_keep_the_new_formula(enc, tmp_path, monkeypatch):
    """The siblings disagree about the id: nothing is proven, so adoption must
    not happen — guessing an id would merge pages that may not belong."""
    tiff = tmp_path / "scan.tif"
    tiff.write_bytes(b"II*\x00")
    out = tmp_path / "out"
    out.mkdir()
    (out / "scan.jxl").write_bytes(b"\x00")
    (out / "scan_page2.jxl").write_bytes(b"\x00")

    pages = [(0, False), (1, False), (2, False)]
    ids = _planned_ids(enc, tiff, out, pages, monkeypatch,
                       marks={"scan.jxl": _legacy_id(tiff),
                              "scan_page2.jxl": "0" * 16})

    assert set(ids.values()) == {_new_id(tiff, pages)}


def test_a_sibling_outside_the_page_set_keeps_the_new_formula(enc, tmp_path, monkeypatch):
    """Legacy id or not, a page this run no longer produces is a leftover of a
    changed structure (bug #303's shape) — adopting the legacy id would invite
    it back into the group."""
    tiff = tmp_path / "scan.tif"
    tiff.write_bytes(b"II*\x00")
    out = tmp_path / "out"
    out.mkdir()
    (out / "scan.jxl").write_bytes(b"\x00")
    (out / "scan_page2.jxl").write_bytes(b"\x00")
    legacy = _legacy_id(tiff)

    pages = [(0, False), (1, False)]
    ids = _planned_ids(enc, tiff, out, pages, monkeypatch,
                       marks={"scan.jxl": legacy, "scan_page2.jxl": legacy})

    assert set(ids.values()) == {_new_id(tiff, pages)}


# ---------------------------------------------------------------------------
# decoder: the legacy+new mixture gets mixed-version advice
# ---------------------------------------------------------------------------

def _markers(**over):
    base = {'group': None, 'inherited': False, 'subfiletype': 0, 'grayscale': False,
            'depth': 16, 'page': None, 'pages': None, 'thumb': False, 'srcsum': None}
    base.update(over)
    return base


def test_mixed_version_archive_gets_reencode_advice(dec, tmp_path, monkeypatch, caplog):
    """Legacy-id pages {0, 2} + new-id page {1}, one srcsum, declared 3: every
    page is present, so the advice must say mixed-version and point at
    re-encoding — not at a page that isn't missing."""
    files = {}
    for name, page, gid in (("scan.jxl", 0, "LEGACYIDLEGACYID"),
                            ("scan_page2.jxl", 2, "LEGACYIDLEGACYID"),
                            ("scan_page1.jxl", 1, "NEWIDNEWIDNEWID1")):
        p = tmp_path / name
        p.write_bytes(b"\x00")
        files[str(p)] = _markers(group=gid, page=page, pages=3, srcsum="aaaa")

    monkeypatch.setattr(dec, "_read_multipage_markers_batch", lambda jxls: files)
    with caplog.at_level(logging.WARNING):
        groups = dec.collect_multipage_groups([Path(p) for p in files])

    # Still two groups, still fail-closed: the decoder does not merge across
    # group ids, advice or no advice.
    assert len(groups) == 2
    assert set(dec._incomplete_groups.values()) == {"truncated"}
    assert "MIXED-VERSION" in caplog.text
    assert "--overwrite" in caplog.text
    assert "point the run at the folder" not in caplog.text


def test_a_genuinely_truncated_group_keeps_the_old_advice(dec, tmp_path, monkeypatch, caplog):
    """One group, one page short, nothing else in the folder: a page really is
    missing, and the advice must still send the user after it."""
    files = {}
    for name, page in (("scan.jxl", 0), ("scan_page2.jxl", 2)):
        p = tmp_path / name
        p.write_bytes(b"\x00")
        files[str(p)] = _markers(group="G", page=page, pages=3, srcsum="aaaa")

    monkeypatch.setattr(dec, "_read_multipage_markers_batch", lambda jxls: files)
    with caplog.at_level(logging.WARNING):
        dec.collect_multipage_groups([Path(p) for p in files])

    assert set(dec._incomplete_groups.values()) == {"truncated"}
    assert "point the run at the folder holding every page" in caplog.text
    assert "MIXED-VERSION" not in caplog.text
