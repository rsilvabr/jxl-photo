"""Plan 261120 srgb-fast — the recompressor's --output-icc colour conversion in
Python (littleCMS's math for two matrix/shaper profiles, in numpy) and the PNG
handed to cjxl on stdin, instead of ImageMagick and a temporary conv.png.

Unit tests: which profiles are accepted, the conversion against ImageMagick on
every 16-bit value (never more than 1 apart), the chunked loop. Real-codec
tests (cjxl, djxl, exiftool, magick): the two routes give the same derivative
(pixels within 1, same markers, same colour description), what keeps
ImageMagick (resize, sharpening, a table curve, the setting off, grey), alpha,
the fallback after a failure, the memory budget, the tables built once per run.
Every file lives in a folder named with Japanese, brackets, a space and
`100%off %d`.
"""
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

import jxl_photo_common as common
import jxl_recompressor as rec
from _icc_fixtures import grey_toe_icc, romm_toe_icc

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "吾妻山 [派生] 100%off %d"
_TOOLS = ("cjxl", "djxl", "exiftool", "magick", "jxlinfo")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")

PROPHOTO = ([(0.7347, 0.2653), (0.1596, 0.8404), (0.0366, 0.0001)], (0.3457, 0.3585))
GIB = 2 ** 30


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

def _prophoto_icc() -> bytes:
    return rec._build_matrix_trc_icc("ProPhoto g2.2 (test)", PROPHOTO[0], PROPHOTO[1], 563 / 256)


def _assemble(base: bytes, tags: dict) -> bytes:
    """A profile with the header of `base` and exactly these tags (signature -> bytes)."""
    offset = 128 + 4 + 12 * len(tags)
    table, data = b"", b""
    for sig, body in tags.items():
        while (offset + len(data)) % 4:
            data += b"\0"
        table += sig.encode("latin-1") + struct.pack(">II", offset + len(data), len(body))
        data += body
    body = struct.pack(">I", len(tags)) + table + data
    out = bytearray(base[:128] + body)
    struct.pack_into(">I", out, 0, len(out))
    return bytes(out)


def _s15(v: float) -> bytes:
    return struct.pack(">i", round(v * 65536))


def _para(ftype: int, params) -> bytes:
    return b"para" + b"\0" * 4 + struct.pack(">HH", ftype, 0) + b"".join(_s15(p) for p in params)


def _with_curve(trc: bytes, base: bytes = None) -> bytes:
    """ProPhoto's colorants with `trc` (a tag body) as the one curve of R, G and B."""
    base = base or _prophoto_icc()
    tags = {k: v for k, v in common._icc_tag_table(base).items()
            if k not in ("rTRC", "gTRC", "bTRC")}
    tags.update(rTRC=trc, gTRC=trc, bTRC=trc)
    return _assemble(base, tags)


_SRGB_FORM = (2.4, 1 / 1.055, 0.055 / 1.055, 1 / 12.92, 0.04045)


def _para_srgb_icc() -> bytes:
    return _with_curve(_para(3, _SRGB_FORM))


def _para_gamma_icc() -> bytes:
    return _with_curve(_para(0, (2.2,)))


def _pillow_srgb() -> bytes:
    return rec._generated_srgb_icc()


# ---------------------------------------------------------------------------
# 1 / 2 — which profiles
# ---------------------------------------------------------------------------

def test_profiles_accepted():
    for name, icc in (("Pillow sRGB", _pillow_srgb()), ("AdobeRGB", rec._adobe_rgb_icc_bytes()),
                      ("ProPhoto g2.2", _prophoto_icc()), ("para type 3", _para_srgb_icc()),
                      ("para type 0", _para_gamma_icc())):
        got = common._matrix_shaper_profile(icc)
        assert got is not None, name
        matrix, curves = got
        assert matrix.shape == (3, 3) and len(curves) == 3, name


def test_profiles_refused():
    srgb = _pillow_srgb()
    with_lut = _assemble(srgb, {**common._icc_tag_table(srgb), "A2B0": b"mft2" + b"\0" * 60})
    lab = srgb[:20] + b"Lab " + srgb[24:]
    not_black = _with_curve(_para(3, (2.4, 1 / 1.055, 0.1, 1 / 12.92, 0.0)))
    para_type1 = _with_curve(_para(1, (2.4, 1 / 1.055, 0.055 / 1.055)))
    refused = {
        "table curve": romm_toe_icc(), "grey": grey_toe_icc(), "A2B0 tag": with_lut,
        "Lab PCS": lab, "truncated": srgb[:140], "curve not from black": not_black,
        "para type 1": para_type1, "empty": b"", "not an icc": b"not an icc" * 20,
    }
    for name, icc in refused.items():
        assert common._matrix_shaper_profile(icc) is None, name
    # and the transform is refused if either side is
    assert common._matrix_shaper_transform(romm_toe_icc(), srgb) is None
    assert common._matrix_shaper_transform(srgb, romm_toe_icc()) is None
    assert common._matrix_shaper_transform(srgb, _prophoto_icc()) is not None


# ---------------------------------------------------------------------------
# 3 — against ImageMagick, every 16-bit value
# ---------------------------------------------------------------------------

def _every_value_image(channels: int, w=300, h=256, cube=False):
    """(h, w, channels) uint16: each colour channel holds every value 0..65535
    at least once; alpha is a ramp. The three channels are sorted lists, so the
    colours are near-neutral photographic ones (a pixel's R, G and B differ by
    a fraction of a percent), and the pixels are then shuffled so neighbours
    differ. cube=True: the channels are shuffled independently instead, which
    fills the whole RGB cube with colours far outside the target's gamut."""
    n = w * h
    rng = np.random.default_rng(7)
    planes = []
    for _ in range(3):
        base = np.concatenate([np.arange(65536), rng.integers(0, 65536, n - 65536)])
        planes.append(rng.permutation(base) if cube else np.sort(base))
    if channels == 4:
        planes.append(np.arange(n) * 65535 // (n - 1))
    img = np.stack(planes, -1)
    if not cube:
        img = img[rng.permutation(n)]
    return img.reshape(h, w, channels).astype(np.uint16)


_PAIRS = {
    "prophoto->srgb": (_prophoto_icc, _pillow_srgb),
    "prophoto->adobe": (_prophoto_icc, rec._adobe_rgb_icc_bytes),
    "adobe->srgb": (rec._adobe_rgb_icc_bytes, _pillow_srgb),
    "srgb->prophoto": (_pillow_srgb, _prophoto_icc),
    "para3->prophoto": (_para_srgb_icc, _prophoto_icc),
}


def _magick_reference(tmp_path, src_icc, dst_icc, img):
    """ImageMagick's conversion of `img` exactly as _derive_pixels' memory route
    runs it (raw samples on stdin), read back as a uint16 array."""
    imagecodecs = pytest.importorskip("imagecodecs")
    h, w, channels = img.shape
    work = tmp_path / HOSTILE
    work.mkdir(exist_ok=True)
    icc_dir = tmp_path / "icc"
    icc_dir.mkdir(exist_ok=True)
    (icc_dir / "src.icc").write_bytes(src_icc)
    (icc_dir / "dst.icc").write_bytes(dst_icc)
    out = work / "magick 結果.png"
    cmd = ["magick", "-size", f"{w}x{h}", "-depth", "16", "-endian", "MSB",
           "rgba:-" if channels == 4 else "rgb:-", "+size", "+depth", "+endian",
           "+profile", "*", "-profile", str(icc_dir / "src.icc"),
           "-intent", "Relative", "-black-point-compensation",
           "-profile", str(icc_dir / "dst.icc"), "-depth", "16",
           "-define", "png:compression-level=0", "-define", "png:preserve-iCCP=true",
           "png:" + common._magick_out_path(out)]
    r = subprocess.run(cmd, input=img.astype(">u2").tobytes(), capture_output=True, timeout=300)
    assert r.returncode == 0 and out.exists(), r.stderr[-2000:]
    ref = imagecodecs.png_decode(out.read_bytes())
    assert ref.shape == img.shape and ref.dtype == np.uint16
    return ref


def _mine(src_icc, dst_icc, img):
    h, w, channels = img.shape
    transform = common._matrix_shaper_transform(src_icc, dst_icc)
    mine = common._convert_matrix_shaper(img.astype(">u2").tobytes(), w, h, channels, transform)
    assert mine.shape == img.shape and mine.dtype == np.uint16
    return mine


@needs_tools
@pytest.mark.parametrize("channels", [3, 4])
@pytest.mark.parametrize("pair", sorted(_PAIRS))
def test_conversion_matches_imagemagick(tmp_path, pair, channels):
    src_icc, dst_icc = (f() for f in _PAIRS[pair])
    img = _every_value_image(channels)
    ref = _magick_reference(tmp_path, src_icc, dst_icc, img)
    mine = _mine(src_icc, dst_icc, img)
    diff = np.abs(mine[..., :3].astype(np.int32) - ref[..., :3].astype(np.int32))
    assert diff.max() <= 1, (pair, int(diff.max()), int((diff > 1).sum()))
    if channels == 4:
        assert np.array_equal(mine[..., 3], img[..., 3])


# The target's curve form decides how a colour far outside its gamut behaves.
_SRGB_FORM_TARGETS = {"prophoto->srgb", "adobe->srgb"}          # + Pillow's sRGB
_PURE_GAMMA_TARGETS = {"prophoto->adobe", "srgb->prophoto", "para3->prophoto"}


@needs_tools
@pytest.mark.parametrize("pair", sorted(_PAIRS))
def test_out_of_gamut_cube(tmp_path, pair):
    """Every RGB value of each channel shuffled independently: the whole cube,
    mostly far outside the target's gamut (real photos and in-gamut colours are
    the test above: never more than 1).

    A target with an sRGB-form curve (a linear segment of slope 12.92 at the
    dark end): never more than 1. A pure-gamma target (AdobeRGB, ProPhoto): a
    channel that falls almost to black after the matrix, out of gamut, is read
    where y^(1/2.2) is steep, and float32 noise there reaches 2-3 levels — as
    ImageMagick's own result does against the exact computation. Measured case:
    ProPhoto g2.2 -> AdobeRGB, source [35609 16564 3893], green channel: ours 36,
    ImageMagick 34, exact (float64) 35.26 — both are float32 and fall one on
    each side of the exact value. So: at most 1 apart where ImageMagick's value
    is 1024 or more, at most 3 below that."""
    src_icc, dst_icc = (f() for f in _PAIRS[pair])
    img = _every_value_image(3, cube=True)
    ref = _magick_reference(tmp_path, src_icc, dst_icc, img).astype(np.int32)
    diff = np.abs(_mine(src_icc, dst_icc, img).astype(np.int32) - ref)
    if pair in _SRGB_FORM_TARGETS or pair not in _PURE_GAMMA_TARGETS:
        assert diff.max() <= 1, (pair, int(diff.max()))
    else:
        assert diff[ref >= 1024].max() <= 1, (pair, int(diff[ref >= 1024].max()))
        assert diff.max() <= 3, (pair, int(diff.max()))


# ---------------------------------------------------------------------------
# 11 — the chunked loop
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("w,h,channels,chunk", [(301, 257, 3, 1000), (301, 257, 4, 1000),
                                                (50, 40, 3, 7), (65536, 1, 3, 4096)])
def test_convert_chunks(monkeypatch, w, h, channels, chunk):
    rng = np.random.default_rng(11)
    pixels = rng.integers(0, 65536, (h, w, channels)).astype(">u2").tobytes()
    transform = common._matrix_shaper_transform(_prophoto_icc(), _pillow_srgb())
    whole = common._convert_matrix_shaper(pixels, w, h, channels, transform)
    monkeypatch.setattr(common, "_CONVERT_CHUNK_PIXELS", chunk)
    small = common._convert_matrix_shaper(pixels, w, h, channels, transform)
    assert np.array_equal(whole, small)


def test_convert_reads_a_memoryview_with_a_header_offset():
    """The recompressor hands over the PAM payload as a memoryview slice."""
    rng = np.random.default_rng(5)
    body = rng.integers(0, 65536, (20, 30, 3)).astype(">u2").tobytes()
    transform = common._matrix_shaper_transform(_prophoto_icc(), _pillow_srgb())
    a = common._convert_matrix_shaper(body, 30, 20, 3, transform)
    b = common._convert_matrix_shaper(memoryview(b"P7\nHEADER\n" + body)[10:], 30, 20, 3, transform)
    assert np.array_equal(a, b)


# ---------------------------------------------------------------------------
# Real runs
# ---------------------------------------------------------------------------

def _photo(h, w, channels, seed=3):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    planes = [x * 65535 // (w - 1), y * 65535 // (h - 1), np.full((h, w), 65535 // 3),
              np.full((h, w), 65535)]
    base = np.stack(planes[:channels], -1).squeeze()
    noise = rng.integers(-2000, 2000, base.shape)
    return np.clip(base + noise, 0, 65535).astype(np.uint16)


def _encode_masters(folder: Path, arrays: dict, icc: bytes, photometric="rgb", distance="0.05"):
    """Masters made by the encoder (as the toolkit makes them) from 16-bit TIFFs
    carrying `icc`; returns the folder holding the .jxl files."""
    src = folder / "tiff"
    src.mkdir(parents=True, exist_ok=True)
    extra = [(34675, "B", len(icc), icc, True)]
    for name, arr in arrays.items():
        tifffile.imwrite(src / f"{name}.tif", arr, photometric=photometric, extratags=extra)
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(folder / "logs"),
           "JXLPHOTO_SETTINGS": "", "JXLPHOTO_JXL_THUMB": "0"}
    r = subprocess.run([sys.executable, str(REPO / "jxl_tiff_encoder.py"), str(src), "--mode", "1",
                        "--distance", distance, "--effort", "1", "--no-preflight", "--overwrite"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=600, env=env)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return src / "converted_jxl"


@pytest.fixture(scope="module")
def prophoto_masters(tmp_path_factory):
    """One RGB and one RGBA ProPhoto g2.2 master, made once for the module."""
    if not all(shutil.which(t) for t in _TOOLS):
        pytest.skip("needs the codecs")
    base = tmp_path_factory.mktemp("fastcolour") / HOSTILE
    rgb = _encode_masters(base / "rgb", {"写真 rgb": _photo(300, 400, 3)}, _prophoto_icc())
    rgba = _encode_masters(base / "rgba", {"写真 rgba": _photo(300, 400, 4)}, _prophoto_icc())
    return {"rgb": rgb / "写真 rgb.jxl", "rgba": rgba / "写真 rgba.jxl"}


def _staged(tmp_path, master: Path, name="in") -> Path:
    """A copy of the master alone in a hostile input folder."""
    d = tmp_path / HOSTILE / name
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(master, d / master.name)
    return d


_DERIVE = ["--mode", "2", "--output-icc", "sRGB", "--effort", "1", "--workers", "1",
           "--on-unknown", "convert", "--on-regeneration", "convert",
           "--on-downgrade", "convert", "--no-preflight"]


def _run_rec(argv, capsys):
    old = sys.argv
    sys.argv = ["jxl_recompressor"] + [str(a) for a in argv]
    try:
        try:
            rec.main()
        except SystemExit as e:
            assert e.code in (0, None), f"exited {e.code}\n{capsys.readouterr().out[-3000:]}"
    finally:
        sys.argv = old
    return capsys.readouterr().out


def _derive(tmp_path, monkeypatch, capsys, master, tag, *, fast=True, distance="0", extra=()):
    """(output .jxl, console text) of one run of the recompressor over a copy of `master`."""
    monkeypatch.setattr(rec, "FAST_COLOR_CONVERSION", fast)
    src = _staged(tmp_path, master, "in " + tag)
    out = tmp_path / HOSTILE / ("out " + tag)
    text = _run_rec([src, out] + _DERIVE + ["--distance", distance] + list(extra), capsys)
    made = sorted(out.rglob("*.jxl"))
    assert len(made) == 1, (made, text[-3000:])
    return made[0], text


def _decode(jxl: Path):
    r = subprocess.run(["djxl", str(jxl), "-", "--output_format=pam"], capture_output=True,
                       timeout=300)
    assert r.returncode == 0, r.stderr[-1000:]
    arr = common._pam_array(r.stdout)
    assert arr is not None and arr.dtype == np.uint16
    return arr


def _tokens(jxl: Path) -> list:
    # exiftool reads its command line in the ANSI code page: the path goes in
    # an argfile read as UTF-8 (AGENTS, "Verification")
    import tempfile
    with tempfile.TemporaryDirectory() as plain:        # the argfile's own path stays ASCII
        args = Path(plain) / "tokens.args"
        args.write_text(rec._ARGFILE_CHARSET + "-j\n-s\n-s\n-XMP-dc:Relation\n" + str(jxl) + "\n",
                        encoding="utf-8")
        r = subprocess.run(["exiftool", "-@", str(args)], capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
    rel = json.loads(r.stdout)[0].get("Relation", [])
    return [str(t).strip() for t in (rel if isinstance(rel, list) else [rel])]


def _colour_lines(jxl: Path) -> list:
    r = subprocess.run(["jxlinfo", str(jxl)], capture_output=True, text=True, timeout=120)
    keys = ("Color space", "White point", "Primaries", "Transfer function", "Rendering intent",
            "bit", "ICC")
    return [ln.strip() for ln in r.stdout.splitlines() if any(k in ln for k in keys)]


def _psnr(a, b) -> float:
    mse = np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)
    return float("inf") if mse == 0 else 10 * np.log10(65535.0 ** 2 / mse)


@needs_tools
def test_lossless_derivative_same_pixels_both_routes(tmp_path, monkeypatch, capsys, prophoto_masters):
    spy = []
    real = rec._run_captured
    real_memory = rec._run_to_memory

    def watch(cmd, timeout, *a, **k):
        spy.append([str(c) for c in cmd])
        return real(cmd, timeout, *a, **k)

    def watch_memory(cmd, timeout, *a, **k):
        spy.append([str(c) for c in cmd])
        return real_memory(cmd, timeout, *a, **k)

    monkeypatch.setattr(rec, "_run_captured", watch)
    # ImageMagick's PNG comes back in memory since v2.31.0 (_run_to_memory).
    monkeypatch.setattr(rec, "_run_to_memory", watch_memory)
    fast, text_fast = _derive(tmp_path, monkeypatch, capsys, prophoto_masters["rgb"], "fast")
    managed_in_python = list(spy)
    spy.clear()
    slow, text_slow = _derive(tmp_path, monkeypatch, capsys, prophoto_masters["rgb"], "slow",
                              fast=False)
    assert "Colour conversion: 1 in Python, 0 with ImageMagick" in text_fast, text_fast[-2000:]
    assert "Colour conversion: 0 in Python, 1 with ImageMagick" in text_slow, text_slow[-2000:]
    assert not [c for c in managed_in_python if "-black-point-compensation" in c], \
        "the Python route still ran an ImageMagick conversion"
    assert [c for c in spy if "-black-point-compensation" in c], "the control run did not use magick"

    a, b = _decode(fast), _decode(slow)
    assert a.shape == b.shape
    assert np.abs(a.astype(np.int32) - b.astype(np.int32)).max() <= 1
    assert _tokens(fast) == _tokens(slow)
    assert _colour_lines(fast) == _colour_lines(slow)
    assert any(t.startswith("jxlphoto-derived:sRGB") for t in _tokens(fast))


@needs_tools
def test_lossy_derivative_is_srgb(tmp_path, monkeypatch, capsys, prophoto_masters):
    fast, _ = _derive(tmp_path, monkeypatch, capsys, prophoto_masters["rgb"], "fast", distance="1")
    slow, _ = _derive(tmp_path, monkeypatch, capsys, prophoto_masters["rgb"], "slow", fast=False,
                      distance="1")
    info = subprocess.run(["jxlinfo", str(fast)], capture_output=True, text=True, timeout=120).stdout
    assert "Primaries: sRGB" in info and "16-bit" in info, info
    assert _psnr(_decode(fast), _decode(slow)) >= 50.0
    assert _colour_lines(fast) == _colour_lines(slow)


@needs_tools
def test_alpha_is_kept(tmp_path, monkeypatch, capsys, prophoto_masters):
    fast, text = _derive(tmp_path, monkeypatch, capsys, prophoto_masters["rgba"], "fast")
    slow, _ = _derive(tmp_path, monkeypatch, capsys, prophoto_masters["rgba"], "slow", fast=False)
    assert "Colour conversion: 1 in Python, 0 with ImageMagick" in text, text[-2000:]
    master = _decode(prophoto_masters["rgba"])
    a, b = _decode(fast), _decode(slow)
    assert a.shape[-1] == 4 and a.shape == b.shape == master.shape
    assert np.array_equal(a[..., 3], master[..., 3])
    assert np.abs(a[..., :3].astype(np.int32) - b[..., :3].astype(np.int32)).max() <= 1


@needs_tools
@pytest.mark.parametrize("case", ["resize", "sharpen", "table curve", "setting off"])
def test_imagemagick_keeps_these(tmp_path, monkeypatch, capsys, prophoto_masters, case):
    master, extra, fast = prophoto_masters["rgb"], [], True
    if case == "resize":
        extra = ["--resize-long", "300"]
    elif case == "sharpen":
        extra = ["--sharpen", "screen"]
    elif case == "table curve":
        folder = _encode_masters(tmp_path / HOSTILE / "toe", {"表 toe": _photo(300, 400, 3)},
                                 romm_toe_icc(), distance="1")
        master = folder / "表 toe.jxl"
    else:
        fast = False
    _out, text = _derive(tmp_path, monkeypatch, capsys, master, "k", fast=fast, extra=extra)
    assert "Colour conversion: 0 in Python, 1 with ImageMagick" in text, text[-2000:]


@needs_tools
def test_grey_master_is_not_converted(tmp_path, monkeypatch, capsys):
    folder = _encode_masters(tmp_path / HOSTILE / "grey", {"灰色": _photo(300, 400, 1)},
                             grey_toe_icc(), photometric="minisblack", distance="1")
    _out, text = _derive(tmp_path, monkeypatch, capsys, folder / "灰色.jxl", "g", distance="1")
    assert "Colour conversion" not in text, text[-2000:]


@needs_tools
def test_python_failure_falls_back(tmp_path, monkeypatch, capsys, prophoto_masters):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(rec, "_convert_matrix_shaper", boom)
    out, text = _derive(tmp_path, monkeypatch, capsys, prophoto_masters["rgb"], "boom")
    assert out.exists()
    assert "Colour conversion in Python failed (boom): using ImageMagick" in text, text[-2000:]
    assert "Colour conversion: 0 in Python, 1 with ImageMagick" in text, text[-2000:]
    ref, _ = _derive(tmp_path, monkeypatch, capsys, prophoto_masters["rgb"], "ref", fast=False)
    assert np.abs(_decode(out).astype(np.int32) - _decode(ref).astype(np.int32)).max() <= 1


@needs_tools
def test_tables_built_once_per_run(tmp_path, monkeypatch, capsys):
    folder = _encode_masters(tmp_path / HOSTILE / "three",
                             {f"写真 {i}": _photo(120, 160, 3, seed=i) for i in range(3)},
                             _prophoto_icc())
    calls = []
    real = rec._matrix_shaper_transform

    def spy(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(rec, "_matrix_shaper_transform", spy)
    monkeypatch.setattr(rec, "FAST_COLOR_CONVERSION", True)
    argv = [folder, tmp_path / HOSTILE / "o1"] + _DERIVE + ["--distance", "0"]
    argv[argv.index("--workers") + 1] = "3"
    text = _run_rec(argv, capsys)
    assert "Colour conversion: 3 in Python, 0 with ImageMagick" in text, text[-2000:]
    assert len(calls) == 1, calls
    argv[1] = tmp_path / HOSTILE / "o2"
    text = _run_rec(argv, capsys)
    assert "Colour conversion: 3 in Python, 0 with ImageMagick" in text, text[-2000:]
    assert len(calls) == 2, calls          # the cache is per run, the count too


# ---------------------------------------------------------------------------
# 9 — the memory budget
# ---------------------------------------------------------------------------

def _gb_per_cjxl(caplog):
    m = re.search(r"~([0-9.]+) GB per cjxl", caplog.text)
    assert m, caplog.text
    return float(m.group(1))


@pytest.mark.parametrize("resize", [None, "long"])
def test_memory_budget_counts_the_png(monkeypatch, caplog, resize):
    import logging
    pixels = 200_000_000
    monkeypatch.setattr(rec, "_available_commit_bytes", lambda: 400 * GIB)
    monkeypatch.setattr(rec, "_available_physical_bytes", lambda: 10 ** 15)
    monkeypatch.setattr(rec, "WORKER_MEMORY_LIMIT", "both")
    monkeypatch.setattr(rec, "WORKER_MEMORY_FRACTION", 1.0)
    monkeypatch.setattr(rec, "VERIFY_ROUNDTRIP", False)
    monkeypatch.setattr(rec, "OUTPUT_ICC", "sRGB")
    monkeypatch.setattr(rec, "RESIZE_MODE", resize)
    seen = {}
    for fast in (False, True):
        monkeypatch.setattr(rec, "FAST_COLOR_CONVERSION", fast)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=rec.logger.name):
            rec._memory_capped_pipeline(8, pixels, 1.0, 7)
        seen[fast] = _gb_per_cjxl(caplog)
    grown = seen[True] - seen[False]
    if resize:
        assert grown == 0, seen            # a resize keeps ImageMagick: nothing to add
    else:
        expected = pixels * rec._FAST_PNG_BYTES_PER_PIXEL / GIB
        assert abs(grown - expected) <= 0.11, (seen, expected)


def test_fast_colour_possible_follows_the_run(monkeypatch):
    monkeypatch.setattr(rec, "FAST_COLOR_CONVERSION", True)
    monkeypatch.setattr(rec, "OUTPUT_ICC", "sRGB")
    monkeypatch.setattr(rec, "RESIZE_MODE", None)
    monkeypatch.setattr(rec, "SHARPEN", "none")
    assert rec._fast_colour_possible()
    monkeypatch.setattr(rec, "SHARPEN", "screen")
    assert not rec._fast_colour_possible()
    monkeypatch.setattr(rec, "SHARPEN", "none")
    monkeypatch.setattr(rec, "OUTPUT_ICC", None)
    assert not rec._fast_colour_possible()
    monkeypatch.setattr(rec, "OUTPUT_ICC", "sRGB")
    monkeypatch.setattr(rec, "FAST_COLOR_CONVERSION", False)
    assert not rec._fast_colour_possible()


def test_the_setting_is_an_overlay_setting():
    assert rec._OVERLAY_SETTINGS["FAST_COLOR_CONVERSION"] == ("bool",)
    assert rec.FAST_COLOR_CONVERSION is True


@needs_tools
def test_v2_the_copys_thumbnail_comes_from_the_converted_pixels(tmp_path, monkeypatch, capsys):
    """V2 (rc5): with the colour converted in Python, the Explorer thumbnail is
    made from the converted image, in the target profile, before cjxl — the
    finished copy is not decoded again. Lossless: the thumbnail the decode
    made of the copy, up to the two sRGB profiles' rounding."""
    from PIL import Image
    master = _encode_masters(tmp_path / HOSTILE / "big", {"写真 big": _photo(800, 1000, 3)},
                             _prophoto_icc(), distance="0") / "写真 big.jxl"
    routes = []
    for name in ("_thumbnail_from_array", "_make_explorer_thumbnail"):
        def spy(*a, _real=getattr(rec, name), _n=name, **k):
            routes.append((_n, _real(*a, **k)))
            return routes[-1][1]
        monkeypatch.setattr(rec, name, spy)
    out, text = _derive(tmp_path, monkeypatch, capsys, master, "thumb",
                        extra=["--explorer-thumbnail", "on"])
    assert [n for n, _j in routes] == ["_thumbnail_from_array"], (routes, text[-2000:])
    data = out.read_bytes()
    i = data.find(b"jxlphoto-thumbnail:v1:")
    assert i >= 0, "no thumbnail box"
    stored = Image.open(io.BytesIO(data[data.index(bytes([0xFF, 0xD8]), i):])).convert("RGB")
    work = tmp_path / "decode"
    work.mkdir()
    ref = Image.open(io.BytesIO(common._make_explorer_thumbnail(rec._G, out, work))).convert("RGB")
    d = np.abs(np.asarray(stored, np.int16) - np.asarray(ref, np.int16))
    assert d.mean() < 0.5 and d.max() <= 8, (float(d.mean()), int(d.max()))
