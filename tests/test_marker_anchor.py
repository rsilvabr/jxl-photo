#!/usr/bin/env python3
"""E-H of the 2026-11-03 audit: modes 6/7 anchor on the export marker at or
below the input root, never on a matching folder above it.

The finders and the resolvers took the FIRST folder of the absolute path that
matched the marker. A library kept under 'Lightroom_Export' or 'C1_Export'
(both match '_EXPORT' by the documented rules) anchored there: the masters of
`.../Lightroom_Export/2025/<event>/_EXPORT/TIFF16/x.tif` went to
`.../Lightroom_Export/16B_JXL/<event>/01_Z7/_EXPORT/TIFF16/x.jxl`.
`_marker_anchor_index` (parity-pinned in the four scripts) takes the first
match at or below the root, else the closest one above (a run pointed inside
an export folder); a path not under the root keeps the old rule.

Pre-fix proof: JXL_SCRIPTS_UNDER_TEST=<the five scripts of the base commit>.
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

EVENT = "250118_吾妻山公園_菜の花 [FINAL] 5%"
NAME = "DSC_0001 (2)"


def _tree(tmp_path, ext):
    """<tmp>/Lightroom_Export/2025/<event>/01_Z7/_EXPORT/<sub>/<name><ext>"""
    root = tmp_path / "Lightroom_Export" / "2025"
    f = root / EVENT / "01_Z7" / enc.EXPORT_MARKER / "TIFF16 (2)" / f"{NAME}{ext}"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" if ext == ".jxl" else b"II*\x00")
    return root, f


def _expected_dir(f, folder):
    return f.parent.parent / folder


@pytest.mark.parametrize("mod", [enc, dec, tra, rec], ids=lambda m: m.__name__)
def test_helper_prefers_the_marker_below_the_root(mod, tmp_path):
    root, f = _tree(tmp_path, ".jxl")
    parts = list(f.parts[:-1])
    idx = mod._marker_anchor_index(parts, root, enc.EXPORT_MARKER.lower())
    assert parts[idx] == enc.EXPORT_MARKER, parts[idx]


@pytest.mark.parametrize("mod", [enc, dec, tra, rec], ids=lambda m: m.__name__)
def test_helper_inside_the_export_folder_takes_the_closest(mod, tmp_path):
    """A run pointed AT `_EXPORT/TIFF16` (or at `_EXPORT` itself) anchors on
    that `_EXPORT`, not on the outer 'Lightroom_Export'."""
    _root, f = _tree(tmp_path, ".jxl")
    parts = list(f.parts[:-1])
    m = enc.EXPORT_MARKER.lower()
    for root in (f.parent, f.parent.parent, f):
        idx = mod._marker_anchor_index(parts, root, m)
        assert parts[idx] == enc.EXPORT_MARKER, (root, parts[idx])


@pytest.mark.parametrize("mod", [enc, dec, tra, rec], ids=lambda m: m.__name__)
def test_helper_keeps_the_old_rule_outside_the_root(mod, tmp_path):
    """Control: a path that is not under the root (or no root) anchors on
    the first match from the top, as before."""
    _root, f = _tree(tmp_path, ".jxl")
    parts = list(f.parts[:-1])
    m = enc.EXPORT_MARKER.lower()
    for root in (None, tmp_path / "elsewhere [x]"):
        idx = mod._marker_anchor_index(parts, root, m)
        assert parts[idx] == "Lightroom_Export", (root, parts[idx])


@pytest.mark.parametrize("mode", [6, 7])
def test_encoder_mode6_7_output_sits_under_the_real_marker(tmp_path, monkeypatch, mode):
    root, f = _tree(tmp_path, ".tif")
    monkeypatch.setattr(enc, "EXPORT_TIFF_SUBFOLDER", "")
    out = enc.resolve_output(f, mode, root)
    assert out == _expected_dir(f, enc.EXPORT_JXL_FOLDER) / f"{NAME}.jxl", out


@pytest.mark.parametrize("mode", [6, 7])
def test_decoder_mode6_7_output_sits_under_the_real_marker(tmp_path, monkeypatch, mode):
    root, f = _tree(tmp_path, ".jxl")
    monkeypatch.setattr(dec, "EXPORT_JXL_SUBFOLDER", "")
    out = dec.resolve_output(f, mode, root)
    assert out.parent == _expected_dir(f, dec.EXPORT_TIFF_FOLDER), out
    assert dec.find_jxls_mode6(root) == [f]


@pytest.mark.parametrize("mode", [6, 7])
def test_recompressor_mode6_7_output_sits_under_the_real_marker(tmp_path, monkeypatch,
                                                                mode):
    root, f = _tree(tmp_path, ".jxl")
    monkeypatch.setattr(rec, "EXPORT_JXL_SUBFOLDER", "")
    out = rec.resolve_output(f, mode, root)
    assert out == _expected_dir(f, rec.EXPORT_JXL_FOLDER) / f"{NAME}.jxl", out


def test_transcoder_mode6_output_sits_under_the_real_marker(tmp_path):
    root, f = _tree(tmp_path, ".jpg")
    out = tra.resolve_output_transcode(f, 6, root, False)
    assert out == _expected_dir(f, tra.EXPORT_JXL_FOLDER) / f"{NAME}.jxl", out


def test_wrapper_marker_dir_is_the_deepest_match(tmp_path):
    src = tmp_path / "Lightroom_Export" / "2025" / EVENT / "01_Z7" / "_EXPORT" / "TIFF16 (2)"
    got = wp.InteractiveMenu._entry_marker_dir(str(src), "_EXPORT")
    assert got == str(src.parent), got
