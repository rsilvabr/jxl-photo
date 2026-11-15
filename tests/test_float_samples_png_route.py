"""#683 — a JXL djxl writes no PAM for still decodes through the PNG.

djxl writes no PAM for 32-bit float samples ("JxlDecoderSetImageOutBitDepth
failed", libjxl 0.13; to stdout or to a file); its PNG writer takes them, at
16 bits. Round 87 moved two decodes into memory and kept the PNG route for a
PAM it cannot carry (a 12-bit file) — but not for a djxl that writes no PAM at
all, which raised instead:

- the round-trip check before a delete (`_decode_jxl_for_verify`, #673): the
  recompressor's --verify-roundtrip said "could not verify" and kept a float
  source that v2.30.1 checked (fail closed, but a check lost);
- the plain recompression's profile probe (`_recompress_encode`, #674): a
  float file whose colour is an ICC profile failed ("djxl: ...") where
  v2.30.1 recompressed it.

Real codecs, in a folder named with Japanese, brackets, a space and
`100%off %d`; the temp folder has Japanese and a space.
"""
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

import jxl_recompressor as rec
import jxl_tiff_encoder as enc
from _icc_fixtures import romm_toe_icc

HOSTILE = "吾妻山 [浮動] 100%off %d"
_TOOLS = ("cjxl", "djxl", "jxlinfo")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")


def _float_jxl(folder, name, icc=None, h=48, w=64):
    """A lossless JXL with 32-bit float samples (cjxl reads them from a PFM),
    tagged with `icc` when given."""
    folder.mkdir(parents=True, exist_ok=True)
    y, x = np.mgrid[0:h, 0:w]
    rgb = np.stack([x / (w - 1), y / (h - 1), np.full((h, w), 0.3)], -1).astype("<f4")
    pfm = folder / (name + ".pfm")
    pfm.write_bytes(f"PF\n{w} {h}\n-1.0\n".encode() + rgb[::-1].tobytes())
    out = folder / (name + ".jxl")
    cmd = ["cjxl", str(pfm), str(out), "-d", "0", "-e", "1", "--container=1"]
    if icc is not None:
        prof = folder / (name + ".icc")
        prof.write_bytes(icc)
        cmd += ["-x", f"icc_pathname={prof}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    pfm.unlink()
    return out


def _png_route(jxl, tmp):
    """The array of v2.30.1: djxl -> PNG -> imagecodecs."""
    import imagecodecs
    png = tmp / "reference.png"
    subprocess.run(["djxl", str(jxl), str(png)], check=True, capture_output=True, timeout=300)
    return imagecodecs.png_decode(png.read_bytes())


@needs_tools
def test_djxl_writes_no_pam_for_float_samples(tmp_path):
    """The premise: no PAM from djxl, a PNG yes. (A later djxl that writes
    one only makes the tests below take the memory route.)"""
    jxl = _float_jxl(tmp_path / HOSTILE, "浮動 (1)")
    assert "float" in subprocess.run(["jxlinfo", str(jxl)], capture_output=True,
                                     text=True, timeout=60).stdout
    ref = tmp_path / "ref"
    ref.mkdir()
    assert _png_route(jxl, ref).dtype == np.uint16
    r = subprocess.run(["djxl", str(jxl), "-", "--output_format=pam"],
                       capture_output=True, timeout=300)
    if r.returncode == 0 and r.stdout:
        pytest.skip("this djxl writes a PAM for float samples")
    assert r.returncode != 0 and not r.stdout


@needs_tools
@pytest.mark.parametrize("mod", [enc, rec], ids=["encoder", "recompressor"])
@pytest.mark.parametrize("original_space", [False, True], ids=["plain", "original space"])
def test_verify_decode_takes_the_png_route_for_float_samples(tmp_path, mod, original_space):
    jxl = _float_jxl(tmp_path / HOSTILE, "浮動 [2]", icc=romm_toe_icc())
    tmp = tmp_path / "一時 verify"
    tmp.mkdir()
    got = mod._decode_jxl_for_verify(jxl, tmp, require_original_space=original_space)
    ref = tmp_path / "ref"
    ref.mkdir()
    want = _png_route(jxl, ref)
    assert got.shape == want.shape and got.dtype == want.dtype
    assert np.array_equal(got, want)


@needs_tools
def test_verify_roundtrip_of_a_float_source_runs(tmp_path):
    """The recompressor's check before a delete: a float source and its
    lossless copy compare pixel for pixel, as in v2.30.1."""
    jxl = _float_jxl(tmp_path / HOSTILE, "浮動 (3)")
    copy = jxl.with_name("写し (3).jxl")
    shutil.copyfile(jxl, copy)
    rec.setup_logger()
    assert rec._verify_roundtrip_jxl(jxl, copy, 0, 0) == (True, "pixel-identical")


@needs_tools
@pytest.mark.parametrize("distance,skip", [(0.0, False), (1.0, True)])
def test_plain_recompression_of_a_float_profile_file(tmp_path, monkeypatch, distance, skip):
    """The profile probe of a float file whose colour is an ICC profile: the
    PNG decode answers it (and the "skip" route reuses that PNG)."""
    jxl = _float_jxl(tmp_path / HOSTILE, "走査 [4]", icc=romm_toe_icc())
    assert rec._jxlinfo_colour(jxl)[0] != "enum"
    monkeypatch.setattr(rec, "CJXL_DISTANCE", distance)
    monkeypatch.setattr(rec, "CJXL_EFFORT", 1)
    rec.setup_logger()
    out = jxl.with_name("出力 (4).jxl")
    route, skip_icc = rec._recompress_encode(jxl, out)
    assert route == "encoded" and out.exists() and out.stat().st_size > 0
    assert (skip_icc is not None) is skip
    if skip:
        assert skip_icc == romm_toe_icc()
