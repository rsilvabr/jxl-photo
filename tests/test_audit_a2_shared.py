#!/usr/bin/env python3
"""A2 of the 2026-11-03 audit — the helpers duplicated across the backends.

  E-C (#575) _long_codec_paths (4 copies) skipped RELATIVE paths, so an input
             given relative to a deep working folder reached cjxl/djxl past
             260 characters without the \\\\?\\ prefix.
  E-F (#578) --clean-staging of one run swept the outputs another run had
             waiting in a shared staging folder (4 copies of _clean_staging).
  E-I (#583) reorder_jxl_boxes (3 copies) held the file twice in memory and
             built the output with a quadratic `out +=`.

Every fixture path is hostile: Japanese, `[ ]`, a space, `%`, parentheses.

Pre-fix proof (must fail on the base commit's scripts):

    git show 488e8b6:<script>.py > <tmp>/a2pre/<script>.py   (the 4 backends)
    JXL_SCRIPTS_UNDER_TEST=<tmp>/a2pre python -m pytest tests/test_audit_a2_shared.py -q
"""

import importlib
import os
import shutil
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

import numpy as np
import pytest
import tifffile

_REPO = Path(__file__).resolve().parent.parent
_OVERRIDE = os.environ.get("JXL_SCRIPTS_UNDER_TEST")
_SCRIPTS = Path(_OVERRIDE).resolve() if _OVERRIDE else _REPO
sys.path.insert(0, str(_SCRIPTS))

HOSTILE = "250118_吾妻山公園_菜の花 [FINAL] 5%"
BACKENDS = ["jxl_tiff_encoder", "jxl_tiff_decoder", "jxl_jpeg_transcoder",
            "jxl_recompressor"]
REORDERERS = ["jxl_tiff_encoder", "jxl_jpeg_transcoder", "jxl_recompressor"]
_HAVE = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
real = pytest.mark.skipif(not _HAVE, reason="needs cjxl, djxl and exiftool on PATH")
windows = pytest.mark.skipif(os.name != "nt", reason="the 260-character limit is Windows'")


def _deep_dir(tmp_path, total=215) -> Path:
    """A real folder whose absolute path is about `total` characters."""
    d = tmp_path / HOSTILE
    i = 0
    while len(str(d)) < total - 20:
        i += 1
        d = d / f"深い [d{i}] 5%"
    d.mkdir(parents=True)
    return d


# ===========================================================================
# E-C (#575)
# ===========================================================================

@windows
@pytest.mark.parametrize("name", BACKENDS)
def test_ec_relative_long_path_gets_the_prefix(name, tmp_path, monkeypatch):
    mod = importlib.import_module(name)
    cwd = _deep_dir(tmp_path)
    monkeypatch.chdir(cwd)
    rel = os.path.join("sub 吾妻 [x] (1)", "another long folder name 5%", "DSC_0001 (2).png")
    assert len(os.path.join(str(cwd), rel)) >= mod._LONG_PATH_THRESHOLD
    out = mod._long_codec_paths(["cjxl", rel, "out.jxl", "-d", "0.1", "--effort", "7"])
    assert out[1] == "\\\\?\\" + os.path.abspath(rel), out[1]
    assert out[2] == "out.jxl", "a short relative path must stay as it is"
    assert out[3:] == ["-d", "0.1", "--effort", "7"], "option values were rewritten"


@real
@windows
def test_ec_real_cjxl_relative_long_path(tmp_path):
    cwd = _deep_dir(tmp_path, total=205)
    src = Path("TIFF16 [吾妻] (1)") / "a much longer export folder name 5%"
    (cwd / src).mkdir(parents=True)
    tifffile.imwrite(str(cwd / src / "DSC_0001 (2).tif"),
                     np.full((32, 48, 3), 30000, np.uint16), photometric="rgb")
    out_rel = Path("16B_JXL [吾妻] (1)") / "a much longer output folder name 5% (export 2025)"
    final = cwd / out_rel / "DSC_0001 (2).jxl"
    assert len(str(final)) >= 260, len(str(final))
    r = subprocess.run([sys.executable, str(_SCRIPTS / "jxl_tiff_encoder.py"),
                        str(src), str(out_rel), "--mode", "0", "--distance", "0",
                        "--effort", "3", "--no-preflight"], cwd=str(cwd),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, stdin=subprocess.DEVNULL)
    assert final.exists(), (r.stdout + r.stderr)[-3000:]


