#!/usr/bin/env python3
"""Regressions for the priority-3 audit fixes (consistency across copies):

  * E5/T2 — _replace_suffix_token gave up when the FIRST regex match failed
    the left-boundary test: 'MyTIFF_TIFF' became 'MyTIFF_TIFF_JXL' instead of
    'MyTIFF_JXL'. All four copies now scan for the first TOKEN-VALID match.
  * E6 — _marker_matches' endswith had no left anchor, so a custom
    EXPORT_MARKER='EXPORT' would match a folder named 'ReExport'.
  * E3/D2 — --clean-staging only ran when --staging was ALSO passed; with
    staging from the script setting (the documented way) it was silently
    inert.
  * E7/D7 — the version-floor check accepted 0.11.0/0.11.1 while the warning
    text and the README name 0.11.2 as the minimum.

  (E4 — "real-run summary omits marker skips" — was a FALSE POSITIVE:
  files outside the marker are filtered by find_tiffs_mode6/7 before
  planning, so skipped_by_mode is unreachable and skipped_files is always 0;
  dry-run and real-run summaries already agree. No code change.)
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_tiff_encoder as enc
import jxl_tiff_decoder as dec
import jxl_jpeg_transcoder as tr
import jxl_photo as wp

ALL_MODULES = [enc, dec, tr, wp]


# ---------------------------------------------------------------------------
# E5/T2 — _replace_suffix_token: first token-valid match wins
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mod", ALL_MODULES)
def test_suffix_token_finds_later_valid_token(mod):
    assert mod._replace_suffix_token("MyTIFF_TIFF", "TIFF", "JXL") == "MyTIFF_JXL"


@pytest.mark.parametrize("mod", ALL_MODULES)
def test_suffix_token_existing_cases_unchanged(mod):
    # Token at start, only first token replaced, substrings still refused,
    # case-insensitive.
    assert mod._replace_suffix_token("TIFF_JXL", "TIFF", "JXL") == "JXL_JXL"
    assert mod._replace_suffix_token("JXL_JXL", "JXL", "TIFF") == "TIFF_JXL"
    assert mod._replace_suffix_token("MyJXLArchive", "JXL", "TIFF") == "MyJXLArchive"
    assert mod._replace_suffix_token("C1_Export_jXl", "JXL", "TIFF") == "C1_Export_TIFF"


# ---------------------------------------------------------------------------
# E6 — _marker_matches: endswith needs a left anchor
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mod", ALL_MODULES)
def test_bare_marker_does_not_match_reexport(mod):
    assert mod._marker_matches("reexport", "export") is False
    assert mod._marker_matches("reexports", "export") is False
    # ...but a real token boundary still matches.
    assert mod._marker_matches("my_export", "export") is True
    assert mod._marker_matches("export", "export") is True


@pytest.mark.parametrize("mod", ALL_MODULES)
def test_default_marker_cases_unchanged(mod):
    assert mod._marker_matches("_export", "_export") is True
    assert mod._marker_matches("_export_2024", "_export") is True
    assert mod._marker_matches("my_export", "_export") is True
    assert mod._marker_matches("export_lightroom", "_export") is True
    assert mod._marker_matches("_exports", "_export") is False
    assert mod._marker_matches("exported_raws", "_export") is False
    assert mod._marker_matches("backup_export_old", "_export") is False


# ---------------------------------------------------------------------------
# E3/D2 — --clean-staging must work with script-configured staging
# ---------------------------------------------------------------------------

def test_encoder_clean_staging_without_staging_flag(monkeypatch, tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    photos = tmp_path / "photos"
    photos.mkdir()
    calls = []
    monkeypatch.setattr(enc, "TEMP2_DIR", str(staging))  # the script setting
    monkeypatch.setattr(enc, "_clean_staging", lambda d: calls.append(d))
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_encoder.py", str(photos), "--mode", "0",
                         "--clean-staging"])
    try:
        enc.main()
    except SystemExit:
        pass
    assert calls == [str(staging)], "--clean-staging was inert without --staging"


def test_decoder_clean_staging_without_staging_flag(monkeypatch, tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    photos = tmp_path / "photos"
    photos.mkdir()
    calls = []
    monkeypatch.setattr(dec, "TEMP2_DIR", str(staging))
    monkeypatch.setattr(dec, "_clean_staging", lambda d: calls.append(d))
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_decoder.py", str(photos), "--mode", "0",
                         "--clean-staging"])
    try:
        dec.main()
    except SystemExit:
        pass
    assert calls == [str(staging)], "--clean-staging was inert without --staging"


def test_clean_staging_without_any_staging_warns(monkeypatch, tmp_path, caplog):
    photos = tmp_path / "photos"
    photos.mkdir()
    calls = []
    monkeypatch.setattr(enc, "TEMP2_DIR", None)
    monkeypatch.setattr(enc, "_clean_staging", lambda d: calls.append(d))
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_encoder.py", str(photos), "--mode", "0",
                         "--clean-staging"])
    try:
        enc.main()
    except SystemExit:
        pass
    assert calls == []
    assert any("nothing to clean" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# E7/D7 — version floor must match the documented 0.11.2
# ---------------------------------------------------------------------------

def test_encoder_warns_on_0110_and_0111(monkeypatch):
    for v in ((0, 11, 0), (0, 11, 1)):
        warnings = []
        monkeypatch.setattr(enc, "_tool_version", lambda exe, _v=v: _v)
        monkeypatch.setattr(enc.logger, "warning", lambda m, *a: warnings.append(m))
        enc._warn_if_libjxl_too_old("cjxl")
        assert warnings, f"no warning for {v}"


def test_encoder_no_warning_on_0112(monkeypatch):
    warnings = []
    monkeypatch.setattr(enc, "_tool_version", lambda exe: (0, 11, 2))
    monkeypatch.setattr(enc.logger, "warning", lambda m, *a: warnings.append(m))
    enc._warn_if_libjxl_too_old("cjxl")
    assert not warnings


def test_decoder_warns_on_0111(monkeypatch):
    warnings = []
    monkeypatch.setattr(dec, "subprocess",
                        SimpleNamespace(run=lambda *a, **k: SimpleNamespace(
                            stdout="djxl v0.11.1", stderr="")))
    monkeypatch.setattr(dec.logger, "warning", lambda m, *a: warnings.append(m))
    dec._warn_if_libjxl_too_old("djxl")
    assert warnings
