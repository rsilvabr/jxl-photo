#!/usr/bin/env python3
"""
Regressions for the findings of an independent (second-opinion) audit.

All of these were reproduced against the shipped code before being fixed:

- ignored thumbnails were DELETED in mode 8 although their pixels never
  reached the TIFF (covered in test_audit_fixes_v181_full.py)
- no startup check for djxl/cjxl/exiftool: a missing exiftool silently
  degraded every multi-page group to standalone pages, and mode 8 then
  deleted the JXLs that held the markers
- the wrapper's status line reported "cjxl/djxl OK" looking only at cjxl
- mixed-case extensions (Photo.Tif, Photo.Jxl) were skipped on
  case-sensitive filesystems
- a group whose page 0 is a thumbnail was written as scan_page1.tif
- modes 6/7 with a single FILE input silently converted nothing
- no minimum libjxl version check
"""

import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_jpeg_transcoder as tr
import jxl_photo as wrapper
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc


# ---------------------------------------------------------------------------
# startup checks for external tools
# ---------------------------------------------------------------------------

def test_decoder_exits_when_exiftool_missing(monkeypatch):
    """Without exiftool the multi-page markers cannot be read at all: every
    page decodes standalone and mode 8 then deletes the JXLs holding them.
    One clear error beats a silent structural downgrade."""
    dec.setup_logger()
    monkeypatch.setattr(dec, "_exiftool_cmd", "exiftool_that_does_not_exist")
    with pytest.raises(SystemExit) as exc:
        dec._check_external_tools(dry_run=False)
    assert exc.value.code == 1


def test_decoder_dry_run_only_warns(monkeypatch, caplog):
    """A simulation converts nothing, so a missing tool must not abort it."""
    dec.setup_logger()
    monkeypatch.setattr(dec, "_exiftool_cmd", "exiftool_that_does_not_exist")
    with caplog.at_level("WARNING"):
        dec._check_external_tools(dry_run=True)   # must NOT raise
    assert any("Missing external tool" in r.message for r in caplog.records)


def test_libjxl_minimum_version_warning(monkeypatch, caplog):
    """libjxl < 0.11 fails on every file with a cryptic message (the default
    RAM pipeline needs a cjxl that reads PNG from stdin)."""
    enc.setup_logger()
    monkeypatch.setattr(enc, "_tool_version", lambda exe: (0, 7, 0))
    with caplog.at_level("WARNING"):
        enc._warn_if_libjxl_too_old("cjxl")
    assert any("older than the supported minimum" in r.message for r in caplog.records)

    caplog.clear()
    monkeypatch.setattr(enc, "_tool_version", lambda exe: (0, 12, 0))
    with caplog.at_level("WARNING"):
        enc._warn_if_libjxl_too_old("cjxl")
    assert not caplog.records, "a supported version must stay quiet"


# ---------------------------------------------------------------------------
# wrapper status line must not vouch for djxl by looking at cjxl
# ---------------------------------------------------------------------------

def _status(**overrides):
    base = dict(cjxl=True, djxl=True, exiftool=True, magick=True,
                tifffile=True, imagecodecs=True, pillow=True, rich=True)
    base.update(overrides)
    cm = wrapper.ConfigManager()
    return wrapper.DependencyChecker(cm).format_status_line(base).split(" | ")[0]


def test_status_line_reports_missing_djxl():
    assert "✓" in _status()
    for missing in ("cjxl", "djxl"):
        line = _status(**{missing: False})
        assert "✗" in line, f"{missing} missing must show a cross"
        assert missing in line, f"{missing} must be named as the missing one"
    both = _status(cjxl=False, djxl=False)
    assert "cjxl/djxl missing" in both


# ---------------------------------------------------------------------------
# mixed-case extensions on case-sensitive filesystems
# ---------------------------------------------------------------------------

def test_finders_are_case_insensitive(tmp_path):
    """`Photo.Tif` / `Photo.Jxl` were skipped in silence on Linux/macOS."""
    img = np.zeros((8, 8, 3), dtype=np.uint16)
    for name in ("a.tif", "B.TIF", "Mixed.Tif", "d.TIFF", "e.TiFf"):
        tifffile.imwrite(str(tmp_path / name), img, photometric="rgb")
    found = {f.name for f in enc.find_files_mode0(tmp_path)}
    assert found == {"a.tif", "B.TIF", "Mixed.Tif", "d.TIFF", "e.TiFf"}
    assert {f.name for f in enc.find_tiffs_recursive(tmp_path)} == found

    for name in ("x.jxl", "Y.JXL", "Mixed.Jxl"):
        (tmp_path / name).write_bytes(b"\x00")
    assert {f.name for f in dec.find_jxls_flat(tmp_path)} == {"x.jxl", "Y.JXL", "Mixed.Jxl"}

    for name in ("p.jpg", "Q.JPG", "Mixed.Jpeg", "r.JfIf"):
        (tmp_path / name).write_bytes(b"\xff\xd8\xff\xd9")
    assert {f.name for f in tr.find_jpegs_flat(tmp_path)} == {
        "p.jpg", "Q.JPG", "Mixed.Jpeg", "r.JfIf"}

    (tmp_path / "s.PnG").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert {f.name for f in tr.find_pngs_flat(tmp_path)} == {"s.PnG"}


def test_finders_ignore_unrelated_extensions(tmp_path):
    """Control: the loosened matching must not start sweeping up everything."""
    for name in ("note.txt", "photo.tif.bak", "checksums.md5", "archive.tiffx"):
        (tmp_path / name).write_bytes(b"x")
    assert enc.find_files_mode0(tmp_path) == []
    assert dec.find_jxls_flat(tmp_path) == []


# ---------------------------------------------------------------------------
# output name when the group anchor is not page 0
# ---------------------------------------------------------------------------

def test_group_named_after_original_stem(tmp_path):
    """Page 0 of the TIFF was a thumbnail, so the anchor is scan_page1.jxl —
    the output must still be scan.tif."""
    anchor = tmp_path / "scan_page1.jxl"
    entries = [(anchor, 1, False, False, 0, False, None),
               (tmp_path / "scan_page2.jxl", 2, False, False, 0, False, None),
               (tmp_path / "scan_thumbnail.jxl", 0, True, False, 1, False, None)]
    assert dec._group_naming_path(anchor, entries).name == "scan.jxl"


def test_standalone_keeps_its_own_name(tmp_path):
    """A third-party photo_page2.jxl is a whole image, not page 2 of anything:
    renaming it to photo.tif would be wrong (and could collide)."""
    lone = tmp_path / "photo_page2.jxl"
    entries = [(lone, 2, False, False, 0, False, None)]
    assert dec._group_naming_path(lone, entries).name == "photo_page2.jxl"


def test_normal_group_name_unchanged(tmp_path):
    """The usual case (anchor IS page 0) must be untouched."""
    anchor = tmp_path / "holiday.jxl"
    entries = [(anchor, 0, False, False, 0, False, None),
               (tmp_path / "holiday_page1.jxl", 1, False, False, 0, False, None)]
    assert dec._group_naming_path(anchor, entries).name == "holiday.jxl"
