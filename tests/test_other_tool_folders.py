#!/usr/bin/env python3
"""X-B of the 2026-11-03 audit: modes 6/7 of the decoder and the recompressor
skip the OTHER tools' JXL output folders below the export marker.

Under `_EXPORT/` the encoder's masters (`16B_JXL/`) sit next to the
recompressor's derivatives (`16B_JXL_small/`) and the transcoder's JPEG
archives (`JXL_jpeg/`), with the same file names. The decoder's and the
recompressor's mode 6/7 finders took every JXL below the marker, so the
master and its derivative/archive resolved to the SAME output: the run
aborted on the duplicate (exit 2) — every run of such a preset — or, with
other names, decoded a derivative as a master. The encoder already had the
mirror rule for the decoder's TIFF folders (_decoder_output_folders).

Now both finders skip those folders (configured value AND shipped default,
read from the siblings' settings with `ast`), never the encoder's master
folder, never mode 7's explicitly requested subfolder; the wrapper's manifest
collision walk mirrors it.

Pre-fix proof (must fail on the base commit's scripts):

    mkdir <tmp>/xbpre
    git show HEAD:<each of the five scripts> > <tmp>/xbpre/<same name>
    JXL_SCRIPTS_UNDER_TEST=<tmp>/xbpre python -m pytest tests/test_other_tool_folders.py -q
"""

import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_OVERRIDE = os.environ.get("JXL_SCRIPTS_UNDER_TEST")
sys.path.insert(0, str(Path(_OVERRIDE).resolve()) if _OVERRIDE else str(_REPO))

import jxl_jpeg_transcoder as tra  # noqa: E402
import jxl_photo as wp  # noqa: E402
import jxl_recompressor as rec  # noqa: E402
import jxl_tiff_decoder as dec  # noqa: E402
import jxl_tiff_encoder as enc  # noqa: E402

EVENT = "250118_吾妻山公園_菜の花_Canola [FINAL] 5%"
NAME = "DSC_0001 (2).jxl"
_JXL_SIG = b"\x00\x00\x00\x0cJXL \r\n\x87\n"


def _stub(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_JXL_SIG + b"\x00" * 32)
    return path


def _tree(tmp_path) -> dict:
    """One photo's event folder after the encoder, the recompressor (mode 6
    derivative, and a mode 4 fallback folder) and the transcoder ran."""
    exp = tmp_path / EVENT / "01_Z7" / enc.EXPORT_MARKER
    return {
        "master": _stub(exp / enc.EXPORT_JXL_FOLDER / NAME),
        "derivative": _stub(exp / rec.EXPORT_JXL_FOLDER / NAME),
        "fallback": _stub(exp / f"web_{rec.JXL_SUFFIX_REPLACE}" / NAME),
        "archive": _stub(exp / tra.EXPORT_JXL_FOLDER / NAME),
    }


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    for m in (dec, rec):
        if hasattr(m, "_other_tool_folders_cache"):
            monkeypatch.setattr(m, "_other_tool_folders_cache", None)
    yield


def _found(finder, root):
    return sorted(str(p) for p in finder(root))


# ---------------------------------------------------------------------------
# The finders
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("finder", ["find_jxls_mode6", "find_jxls_mode7"])
def test_decoder_finds_only_the_master(tmp_path, monkeypatch, finder):
    t = _tree(tmp_path)
    monkeypatch.setattr(dec, "EXPORT_JXL_SUBFOLDER", "")
    assert _found(getattr(dec, finder), tmp_path) == [str(t["master"])]


@pytest.mark.parametrize("finder", ["find_jxls_mode6", "find_jxls_mode7"])
def test_recompressor_finds_only_the_master(tmp_path, monkeypatch, finder):
    t = _tree(tmp_path)
    monkeypatch.setattr(rec, "EXPORT_JXL_SUBFOLDER", "")
    assert _found(getattr(rec, finder), tmp_path) == [str(t["master"])]


def test_mode7_explicit_subfolder_still_wins(tmp_path, monkeypatch):
    """Asking for the transcoder's folder by name in mode 7 is legitimate."""
    t = _tree(tmp_path)
    monkeypatch.setattr(dec, "EXPORT_JXL_SUBFOLDER", tra.EXPORT_JXL_FOLDER)
    assert _found(dec.find_jxls_mode7, tmp_path) == [str(t["archive"])]


def test_master_folder_is_never_skipped(tmp_path, monkeypatch):
    """An encoder configured to write its masters into a folder that has
    another tool's name keeps them visible."""
    fake_enc = tmp_path / "fake_encoder.py"
    fake_enc.write_text(f'EXPORT_JXL_FOLDER = "{tra.EXPORT_JXL_FOLDER}"\n', encoding="utf-8")
    monkeypatch.setattr(dec, "_MASTER_FOLDER_SOURCE",
                        (str(fake_enc), "EXPORT_JXL_FOLDER", "16B_JXL"))
    names, _suffixes, master = dec._other_tool_output_folders()
    assert master == tra.EXPORT_JXL_FOLDER.lower()
    assert master not in names
    assert not dec._skip_other_tool_output([master], honor_requested_subfolder=False)


def test_configured_sibling_setting_is_skipped_too(tmp_path, monkeypatch):
    """A user who renamed the transcoder's folder: both the configured name
    and the shipped default are skipped (read with ast, nothing executed)."""
    fake_tra = tmp_path / "fake_transcoder.py"
    fake_tra.write_text('raise SystemExit("must not run")\n'
                        'EXPORT_JXL_FOLDER = "アーカイブ [JPEG]"\n', encoding="utf-8")
    monkeypatch.setattr(rec, "_OTHER_TOOL_FOLDER_SOURCES",
                        ((str(fake_tra), "EXPORT_JXL_FOLDER", "JXL_jpeg"),))
    names, _s, _m = rec._other_tool_output_folders()
    assert {"アーカイブ [jpeg]", "jxl_jpeg"} <= names, names


def test_tables_follow_the_siblings():
    """The decoder's copy of the recompressor's fixed names, and every real
    sibling setting, are in the skip set."""
    assert dec._OTHER_TOOL_FIXED_FOLDERS == rec._RECOMPRESSOR_OUTPUT_FOLDERS
    dnames, dsuffixes, dmaster = dec._other_tool_output_folders()
    rnames, _rs, rmaster = rec._other_tool_output_folders()
    for v in (rec.EXPORT_JXL_FOLDER, tra.EXPORT_JXL_FOLDER, tra.JXL_SIBLING_FOLDER,
              tra.CONVERTED_JXL_FOLDER):
        assert v.lower() in dnames, v
    for v in (tra.EXPORT_JXL_FOLDER, tra.JXL_SIBLING_FOLDER, tra.CONVERTED_JXL_FOLDER):
        assert v.lower() in rnames, v
    assert "_" + rec.JXL_SUFFIX_REPLACE.lower() in dsuffixes
    assert dmaster == rmaster == enc.EXPORT_JXL_FOLDER.lower()


def test_flat_and_recursive_finders_stay_unfiltered(tmp_path):
    """Control: only modes 6/7 changed — mode 2's recursive scan still sees
    the transcoder's archive (the decoder must find round-trip sources)."""
    t = _tree(tmp_path)
    assert str(t["archive"]) in _found(dec.find_jxls_recursive, tmp_path)


# ---------------------------------------------------------------------------
# The wrapper's manifest collision walk
# ---------------------------------------------------------------------------

def _menu():
    menu = wp.InteractiveMenu.__new__(wp.InteractiveMenu)
    menu.config = wp.ConfigManager()
    return menu


@pytest.mark.parametrize("origin,dest", [("jxl", "tiff"), ("jxl", "jxl")])
def test_wrapper_collision_walk_mirrors_the_skip(tmp_path, origin, dest):
    _tree(tmp_path)
    # A row with an empty Destination is not walked at all; modes 6/7 ignore
    # the cell, so any value makes the walk resolve every file.
    collisions = _menu()._manifest_output_collisions(
        [(str(tmp_path / EVENT), str(tmp_path / "unused [dest]"), 6)], {".jxl"},
        origin=origin, dest=dest)
    assert collisions == [], collisions


def test_wrapper_collision_walk_still_sees_a_real_clash(tmp_path):
    """Control: two masters with one name in two event folders of ONE row
    still collide in mode 2 (flat) — the skip did not blind the walk."""
    a = _stub(tmp_path / EVENT / "A" / enc.EXPORT_MARKER / enc.EXPORT_JXL_FOLDER / NAME)
    b = _stub(tmp_path / EVENT / "B" / enc.EXPORT_MARKER / enc.EXPORT_JXL_FOLDER / NAME)
    collisions = _menu()._manifest_output_collisions(
        [(str(tmp_path / EVENT), str(tmp_path / "flat [out]"), 2)], {".jxl"},
        origin="jxl", dest="tiff")
    assert {str(collisions[0][0]), str(collisions[0][1])} == {str(a), str(b)}, collisions
