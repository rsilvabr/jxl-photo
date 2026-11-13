"""The sRGB profile the recompressor generates carries a fixed creation date.

Pillow's (littleCMS's) built-in sRGB profile records the moment it is made in
its ICC header (bytes 24-35), so two identical derivatives made at different
times differed in their jxlphoto-iccprofile token — the only place those bytes
reach a file (cjxl writes the sRGB colour space as an enum). The derivative's
recipe is the label ("sRGB"), never the profile bytes, so no existing
derivative is re-derived for the new date. Real codecs, in a folder with
Japanese, [ ], a space and `100%off %d`.
"""
import base64
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import tifffile

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import jxl_photo_common as common  # noqa: E402
import jxl_recompressor as rec  # noqa: E402

HOSTILE = "伊豆沼 [tmp] 100%off %d"
TOKEN = "jxlphoto-iccprofile:"
_TOOLS = ("cjxl", "djxl", "exiftool", "magick")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")


def test_the_generated_profile_has_the_fixed_date():
    pytest.importorskip("PIL.ImageCms")
    from PIL import ImageCms
    icc = common._generated_srgb_icc()
    assert struct.unpack(">6H", icc[24:36]) == common._GENERATED_ICC_DATE
    pillow = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    assert len(icc) == len(pillow)
    assert icc[:24] == pillow[:24] and icc[36:84] == pillow[36:84] and icc[100:] == pillow[100:]
    assert icc == common._generated_srgb_icc()
    assert rec._resolve_output_icc("sRGB") == ("sRGB", icc)


def _run(tmp_path, script, *args):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"), "JXLPHOTO_JXL_THUMB": "0",
           "JXLPHOTO_SETTINGS": ""}
    r = subprocess.run([sys.executable, str(REPO / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=900, env=env, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, (r.stdout + r.stderr)[-6000:]


def _icc_token(jxl: Path) -> bytes:
    _creator, tokens = rec._read_creator_and_relation(jxl)
    found = [base64.b64decode(t[len(TOKEN):]) for t in tokens if t.startswith(TOKEN)]
    assert len(found) == 1, [t[:40] for t in tokens]
    return found[0]


@needs_tools
def test_two_derivatives_made_apart_carry_the_same_profile(tmp_path):
    pytest.importorskip("PIL.ImageCms")
    ev = tmp_path / HOSTILE / "260101_写真 [FINAL] 5%"
    tif = ev / "TIFF16" / "DSC_0001 (2).tif"
    tif.parent.mkdir(parents=True)
    x = np.linspace(0.1, 0.9, 96)
    yy, xx = np.meshgrid(x, x, indexing="ij")
    arr = np.stack([(xx * 65535), (yy * 65535), ((1 - xx) * 65535)], axis=2).astype("uint16")
    tifffile.imwrite(str(tif), arr, photometric="rgb")
    _run(tmp_path, "jxl_tiff_encoder.py", tif.parent, ev / "16B_JXL", "--distance", "1",
         "--effort", "7", "--workers", "1")
    tokens = []
    for n in (1, 2):
        out = ev / f"sRGB {n}"
        _run(tmp_path, "jxl_recompressor.py", ev / "16B_JXL", out, "--mode", "2",
             "--workers", "1", "--distance", "2", "--effort", "7",
             "--on-downgrade", "convert", "--output-icc", "sRGB")
        tokens.append(_icc_token(next(out.rglob("*.jxl"))))
        time.sleep(1.1)        # the header's clock counts whole seconds
    assert tokens[0] == tokens[1]
    assert struct.unpack(">6H", tokens[0][24:36]) == common._GENERATED_ICC_DATE
