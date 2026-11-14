"""#681 — the decoder's JXL -> TIFF without its intermediate PNG and without
rebuilding the TIFF for the preview.

djxl wrote a 16-bit PNG into the temp folder (232 MB, 8.5 s of libpng deflate
and 1.4 s to read it back for a 45 MP master); then add_jpeg_preview read the
finished TIFF back, compressed its page again and copied the result over it
(2 x 251 MB); then two exiftool commands each rewrote the whole TIFF to delete
the Software/ImageDescription tifffile had put on the preview page. Now the
pixels come from a PAM in memory, the preview is page 1 of the first writer,
written without those tags. The TIFF is byte for byte the same.

Real codecs, in a folder named with Japanese, brackets, a space and `100%off %d`.
"""
import datetime as dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

import jxl_photo_common as common
import jxl_tiff_decoder as dec
from _icc_fixtures import grey_toe_icc, romm_toe_icc

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "吾妻山 [復号] 100%off %d"
_TOOLS = ("cjxl", "djxl", "exiftool", "magick")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")


@pytest.fixture
def frozen(monkeypatch):
    fixed = dt.datetime(2026, 11, 14, 12, 0, 0)

    class Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.replace(tzinfo=dt.timezone.utc).astimezone(tz)

    monkeypatch.setattr(common, "datetime", Frozen)


def _photo(h, w, channels, seed=3):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    planes = [x * 65535 // (w - 1), y * 65535 // (h - 1), np.full((h, w), 20000),
              np.full((h, w), 65535)]
    base = np.stack(planes[:channels], -1).squeeze()
    return np.clip(base + rng.integers(-2000, 2000, base.shape), 0, 65535).astype(np.uint16)


def _masters(tmp_path):
    src = tmp_path / HOSTILE / "tiff"
    src.mkdir(parents=True)
    kw = {"resolution": (300, 300), "resolutionunit": 2}
    tifffile.imwrite(src / "写真 rgb.tif", _photo(300, 400, 3), photometric="rgb",
                     extratags=[(34675, "B", len(romm_toe_icc()), romm_toe_icc(), True)], **kw)
    tifffile.imwrite(src / "写真 rgba.tif", _photo(300, 400, 4), photometric="rgb",
                     extrasamples=["unassalpha"],
                     extratags=[(34675, "B", len(romm_toe_icc()), romm_toe_icc(), True)], **kw)
    tifffile.imwrite(src / "灰色 (2).tif", _photo(300, 400, 1), photometric="minisblack",
                     extratags=[(34675, "B", len(grey_toe_icc()), grey_toe_icc(), True)], **kw)
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"),
           "JXLPHOTO_SETTINGS": "", "JXLPHOTO_JXL_THUMB": "0"}
    r = subprocess.run([sys.executable, str(REPO / "jxl_tiff_encoder.py"), str(src), "--mode", "1",
                        "--distance", "1", "--effort", "1", "--no-preflight"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=600, env=env)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return src / "converted_jxl"


def _run_main(argv):
    old = sys.argv
    sys.argv = ["jxl_tiff_decoder"] + [str(a) for a in argv]
    try:
        try:
            dec.main()
        except SystemExit as e:
            assert e.code in (0, None), f"decoder exited {e.code}"
    finally:
        sys.argv = old


@needs_tools
@pytest.mark.parametrize("extra", [[], ["--none"]], ids=["roundtrip", "none"])
def test_the_tiff_is_the_same_and_no_png_is_written(tmp_path, monkeypatch, frozen, extra):
    jxls = _masters(tmp_path)
    seen = []
    real = dec._run_captured

    def spy(cmd, timeout, *a, **k):
        seen.append([str(c) for c in cmd])
        return real(cmd, timeout, *a, **k)

    monkeypatch.setattr(dec, "_run_captured", spy)
    _run_main([jxls, tmp_path / HOSTILE / "new", "--mode", "2", "--workers", "2"] + extra)
    pngs = [c for c in seen if os.path.basename(c[0]).lower().startswith("djxl")
            and any(a.endswith(".png") for a in c)]
    assert not pngs, f"djxl decoded to a PNG: {pngs}"
    # The same run through the PNG route (#681's fallback) gives the same files.
    monkeypatch.setattr(dec, "_decode_planes_in_memory", lambda *a, **k: None)
    _run_main([jxls, tmp_path / HOSTILE / "old", "--mode", "2", "--workers", "2"] + extra)
    new = sorted((tmp_path / HOSTILE / "new").glob("*.tif"))
    old = sorted((tmp_path / HOSTILE / "old").glob("*.tif"))
    assert [p.name for p in new] == [p.name for p in old] and len(new) == 3
    for a, b in zip(new, old):
        assert a.read_bytes() == b.read_bytes(), a.name
    if not extra:
        with tifffile.TiffFile(new[0]) as t:
            assert len(t.pages) == 2 and int(t.pages[1].subfiletype) == 1


@needs_tools
def test_a_12bit_file_decodes_through_the_png(tmp_path):
    """djxl's PAM holds a 12-bit file at MAXVAL 4095; the PNG holds it scaled
    to 16 bits: that one keeps the PNG route."""
    folder = tmp_path / HOSTILE
    folder.mkdir(parents=True)
    arr = (np.random.default_rng(2).integers(0, 4095, (40, 60, 3))).astype(">u2")
    pam = folder / "a.pam"
    pam.write_bytes(b"P7\nWIDTH 60\nHEIGHT 40\nDEPTH 3\nMAXVAL 4095\nTUPLTYPE RGB\nENDHDR\n"
                    + arr.tobytes())
    jxl = folder / "十二 bit.jxl"
    subprocess.run(["cjxl", str(pam), str(jxl), "-d", "0", "-e", "1"], check=True,
                   capture_output=True, timeout=300)
    assert dec._decode_planes_in_memory(jxl, [], 16) is None
    tmp = tmp_path / "一時"
    tmp.mkdir()
    pixels, _icc, _reason, mode = dec.decode_jxl_to_numpy(jxl, tmp, target_depth=16)
    assert pixels.dtype == np.uint16 and pixels.shape == (40, 60, 3)
    assert list(tmp.glob("*.png")), "the 12-bit file did not take the PNG route"
    want = (arr.astype(np.uint16).astype(np.float64) * 65535 / 4095)
    assert np.abs(pixels.astype(np.float64) - want).max() <= 16      # scaled to 16 bits
