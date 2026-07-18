#!/usr/bin/env python3
"""
Tests for the two v1.8.1 minor fixes:

- D50 patch stats deduplicate profiles (per-page counting in multipage splits)
- DependencyChecker.check_dependencies honors the force parameter (cache)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_tiff_encoder as enc
import jxl_photo


# ---------------------------------------------------------------------------
# D50 unique profile hashes
# ---------------------------------------------------------------------------

def test_d50_unique_profile_hashes(monkeypatch, tmp_path):
    monkeypatch.setattr(enc, "D50_PATCH_MODE", "on")
    enc._d50_patched_hashes.clear()
    for k in enc._d50_patch_count:
        enc._d50_patch_count[k] = 0

    icc_a = bytes(80) + b"\x00" * 100   # >= 80 bytes, wrong D50 (zeros)
    icc_b = bytes(80) + b"\x01" * 100

    # Same ICC twice (e.g. multipage pages sharing one profile): counts once
    enc.apply_d50_policy(icc_a, tmp_path / "page0.tif")
    enc.apply_d50_policy(icc_a, tmp_path / "page2.tif")
    assert len(enc._d50_patched_hashes) == 1
    assert enc._d50_patch_count["applied"] == 2  # per-page counter unchanged

    # A different profile: counts separately
    enc.apply_d50_policy(icc_b, tmp_path / "other.tif")
    assert len(enc._d50_patched_hashes) == 2

    # Already-correct ICC: applied branch but no patch needed, not in the set
    CORRECT_D50 = bytes.fromhex("0000f6d6000100000000d32d")
    icc_c = bytes(68) + CORRECT_D50 + b"\x00" * 100
    enc.apply_d50_policy(icc_c, tmp_path / "already.tif")
    assert len(enc._d50_patched_hashes) == 2


# ---------------------------------------------------------------------------
# DependencyChecker force cache
# ---------------------------------------------------------------------------

class _FakeCfg:
    def __init__(self):
        self.config = argparse.Namespace(available_features=None)

    def update_tool_paths(self, paths): pass
    def _check_tiff_support(self): return True
    def _check_imagecodecs(self): return True
    def save_config(self): pass


def test_check_dependencies_honors_force(monkeypatch):
    checker = jxl_photo.DependencyChecker(_FakeCfg())
    calls = []
    monkeypatch.setattr(checker, "_detect_tool", lambda cmd: calls.append(cmd) or "/usr/bin/x")
    monkeypatch.setattr(checker, "_test_tool_execution", lambda p, a: True)

    s1 = checker.check_dependencies()
    assert calls == ['cjxl', 'djxl', 'exiftool', 'magick']

    # Second call without force: cached, no re-detection
    s2 = checker.check_dependencies()
    assert calls == ['cjxl', 'djxl', 'exiftool', 'magick']
    assert s1 == s2

    # force=True bypasses the cache and re-detects
    checker.check_dependencies(force=True)
    assert calls == ['cjxl', 'djxl', 'exiftool', 'magick'] * 2
