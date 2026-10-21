#!/usr/bin/env python3
"""cjxl/djxl on paths of 260 characters or more (#541).

libjxl's tools fail on a path at or past MAX_PATH even with Windows long paths
enabled, and every script writes its output first as a uuid temp beside the
final name: ~50 characters on top of the source path. 1459 files of the
owner's library reach the limit that way. _long_codec_paths gives such paths
the extended-length prefix, which the codecs accept.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_jpeg_transcoder as tr
import jxl_recompressor as rec
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent
PFX = "\\\\?\\"
windows = pytest.mark.skipif(os.name != "nt", reason="the prefix is Windows-only")


@windows
@pytest.mark.parametrize("mod", [enc, dec, rec, tr], ids=["enc", "dec", "rec", "tr"])
def test_long_codec_paths_prefixes_only_long_codec_paths(mod):
    long = "E:\\" + "長いフォルダ [x] 5% " * 20 + "\\a.jxl"
    short = "E:\\photos\\a.jxl"
    assert len(long) > 260
    out = mod._long_codec_paths(["cjxl", short, long, "-d", "1", "--effort=7"])
    assert out == ["cjxl", short, PFX + long, "-d", "1", "--effort=7"]
    out = mod._long_codec_paths([r"C:\tools\djxl.exe", Path(long), "-"])
    assert out[1] == PFX + long and out[2] == "-"
    # Already prefixed, and UNC.
    assert mod._long_codec_paths(["djxl", PFX + long])[1] == PFX + long
    unc = "\\\\server\\share\\" + "d" * 250 + "\\a.jxl"
    assert mod._long_codec_paths(["djxl", unc])[1] == PFX + "UNC\\" + unc[2:]
    # exiftool and ImageMagick are never touched ('?' is a magick wildcard).
    for tool in ("exiftool", "magick"):
        assert mod._long_codec_paths([tool, long]) == [tool, long]


_HAVE = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))


@windows
@pytest.mark.skipif(not _HAVE, reason="needs cjxl, djxl and exiftool on PATH")
def test_encode_and_decode_under_a_path_past_max_path(tmp_path):
    deep = tmp_path / "吾妻山 [FINAL] 100%"
    while len(str(deep / "converted_jxl" / ("0" * 33 + "DSC_0001 (2).jxl.tmp"))) < 280:
        deep = deep / "長いフォルダ名 [long] folder"
    src = deep / "DSC_0001 (2).tif"
    src.parent.mkdir(parents=True)
    rng = np.random.default_rng(1)
    tifffile.imwrite(src, rng.integers(0, 65535, (48, 64, 3), dtype=np.uint16),
                     photometric="rgb")
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs")}
    r = subprocess.run([sys.executable, str(REPO / "jxl_tiff_encoder.py"), str(deep),
                        "--mode", "1", "--distance", "1", "--effort", "1",
                        "--no-preflight"], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=300)
    jxl = deep / "converted_jxl" / "DSC_0001 (2).jxl"
    assert r.returncode == 0 and jxl.exists(), r.stdout[-2000:] + r.stderr[-2000:]

    r = subprocess.run([sys.executable, str(REPO / "jxl_tiff_decoder.py"),
                        str(deep / "converted_jxl"), "--mode", "1"],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=300)
    tif = deep / "converted_jxl" / "converted_tiff" / "DSC_0001 (2).tif"
    assert r.returncode == 0 and tif.exists(), r.stdout[-2000:] + r.stderr[-2000:]
