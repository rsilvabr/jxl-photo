"""Round 87 — decodes that were only intermediates stay in memory.

#673: the round-trip check before a delete (`_decode_jxl_for_verify`) had djxl
write a 16-bit PNG into the temp folder and read it back: 236 MB and 8.7 s of
libpng deflate inside djxl for a 45 MP page, most of a deleting run. djxl now
writes a PAM to stdout, read into memory (`_run_to_memory`) and turned into the
same array (`_pam_array`). A PAM that cannot be carried exactly (a 12-bit file:
the PNG holds it scaled to 16 bits) still goes through the PNG.

Real codecs: every JXL in a folder named with Japanese, brackets, a space and
`100%off %d`; the temp folder has Japanese and a space.
"""
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

import jxl_recompressor as rec
import jxl_tiff_encoder as enc

HOSTILE = "吾妻山 [メモリ] 100%off %d"
_TOOLS = ("cjxl", "djxl")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")


def _pam(path, arr, maxval, tupltype):
    h, w, depth = arr.shape
    hdr = (f"P7\nWIDTH {w}\nHEIGHT {h}\nDEPTH {depth}\nMAXVAL {maxval}\n"
           f"TUPLTYPE {tupltype}\nENDHDR\n").encode()
    path.write_bytes(hdr + arr.astype(">u2" if maxval > 255 else "u1").tobytes())


def _jxl(folder, name, channels, maxval, tupltype, distance="0", h=300, w=400):
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(len(name))
    y, x = np.mgrid[0:h, 0:w]
    planes = [x * maxval // (w - 1), y * maxval // (h - 1),
              np.full((h, w), maxval // 3), (x + y) * maxval // (w + h)]
    arr = np.clip(np.stack(planes[:channels], -1)
                  + rng.integers(-maxval // 40, maxval // 40, (h, w, channels)), 0, maxval)
    src = folder / (name + ".pam")
    _pam(src, arr, maxval, tupltype)
    out = folder / (name + ".jxl")
    r = subprocess.run(["cjxl", str(src), str(out), "-d", distance, "-e", "1"],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    src.unlink()
    return out


def _png_route(jxl, tmp):
    """The array as before #673: djxl -> PNG -> imagecodecs."""
    import imagecodecs
    png = tmp / "reference.png"
    subprocess.run(["djxl", str(jxl), str(png)], check=True, capture_output=True, timeout=300)
    return imagecodecs.png_decode(png.read_bytes())


CASES = [
    ("rgb16", 3, 65535, "RGB", "0", True),
    ("rgb16 lossy", 3, 65535, "RGB", "1", True),
    ("rgb8", 3, 255, "RGB", "0", True),
    ("gray16", 1, 65535, "GRAYSCALE", "0", True),
    ("gray8 lossy", 1, 255, "GRAYSCALE", "1", True),
    ("graya16", 2, 65535, "GRAYSCALE_ALPHA", "0", True),
    ("rgba16", 4, 65535, "RGB_ALPHA", "0", True),
    ("rgb12 (the PNG route)", 3, 4095, "RGB", "0", False),
]


@needs_tools
@pytest.mark.parametrize("mod", [enc, rec], ids=["encoder", "recompressor"])
@pytest.mark.parametrize("name,channels,maxval,tupl,distance,in_memory", CASES,
                         ids=[c[0] for c in CASES])
def test_verify_decode_same_array_nothing_written(tmp_path, mod, name, channels, maxval,
                                                  tupl, distance, in_memory):
    jxl = _jxl(tmp_path / HOSTILE, name, channels, maxval, tupl, distance)
    tmp = tmp_path / "一時 verify"
    tmp.mkdir()
    got = mod._decode_jxl_for_verify(jxl, tmp)
    ref_tmp = tmp_path / "ref"
    ref_tmp.mkdir()
    want = _png_route(jxl, ref_tmp)
    assert got.shape == want.shape and got.dtype == want.dtype, (got.shape, got.dtype,
                                                                  want.shape, want.dtype)
    assert np.array_equal(got, want)
    assert got.flags.writeable
    # The decode left nothing in the temp folder, except on the PNG route.
    assert (tmp / "verify.png").exists() is (not in_memory), sorted(p.name for p in tmp.iterdir())


@needs_tools
def test_verify_decode_still_refuses_a_lossy_icc_blob(tmp_path, monkeypatch):
    jxl = _jxl(tmp_path / HOSTILE, "blob", 3, 65535, "RGB", "1")
    monkeypatch.setattr(enc, "_decoded_in_original_space", lambda *a, **k: False)
    tmp = tmp_path / "一時"
    tmp.mkdir()
    with pytest.raises(RuntimeError, match="lossy ICC blob"):
        enc._decode_jxl_for_verify(jxl, tmp, require_original_space=True)


@needs_tools
def test_verify_decode_raises_when_djxl_fails(tmp_path):
    bad = tmp_path / HOSTILE / "壊れた.jxl"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 64)
    tmp = tmp_path / "一時"
    tmp.mkdir()
    with pytest.raises(RuntimeError, match="djxl"):
        enc._decode_jxl_for_verify(bad, tmp)


def test_pam_array_shapes_and_refusals():
    def hdr(w, h, d, m, t):
        return f"P7\nWIDTH {w}\nHEIGHT {h}\nDEPTH {d}\nMAXVAL {m}\nTUPLTYPE {t}\nENDHDR\n".encode()
    a = enc._pam_array(hdr(2, 1, 3, 65535, "RGB") + bytes([0, 1, 0, 2, 0, 3, 1, 0, 2, 0, 3, 0]))
    assert a.dtype == np.uint16 and a.shape == (1, 2, 3)
    assert a.tolist() == [[[1, 2, 3], [256, 512, 768]]]
    g = enc._pam_array(hdr(2, 2, 1, 255, "GRAYSCALE") + bytes([1, 2, 3, 4]))
    assert g.dtype == np.uint8 and g.shape == (2, 2) and g.flags.writeable
    assert enc._pam_array(hdr(2, 1, 3, 4095, "RGB") + bytes(12)) is None      # 12-bit
    assert enc._pam_array(hdr(2, 1, 3, 65535, "RGB") + bytes(11)) is None     # short
    assert enc._pam_array(hdr(2, 1, 3, 65535, "RGB") + bytes(13)) is None     # trailing
    assert enc._pam_array(b"P6\n2 1\n255\n" + bytes(6)) is None
