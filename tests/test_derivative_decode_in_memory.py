"""#680 — a derivative's decode goes through memory, not a 16-bit PNG.

Every derivative (the transcoder's resized JPEG, the recompressor's sRGB or
resized JXL) had djxl write a 16-bit PNG into the temp folder: 232 MB and 8.6 s
of libpng deflate inside djxl for a 45 MP master — more than half of a resized
JPEG. djxl now writes a PAM to stdout, read into memory, and ImageMagick reads
the samples raw from stdin. Only what the PNG did not decide takes that route:
an RGB/RGBA master that carries its ICC in XMP, decoded in its own space, whose
PAM has the depth the PNG would have had (djxl writes a PAM at the file's own
depth whatever --bits_per_sample says), and — for a JPEG — an EXIF resolution
to hand over to the JFIF header. Everything else keeps the PNG route.

Each case runs the script twice in one process with the clock frozen: the
memory route and the PNG route must give the same file, byte for byte. Real
codecs, in a folder named with Japanese, brackets, a space and `100%off %d`.
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

import jxl_jpeg_transcoder as tr
import jxl_photo_common as common
import jxl_recompressor as rec
from _icc_fixtures import grey_toe_icc, romm_toe_icc

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "吾妻山 [派生] 100%off %d"
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


def _master(tmp_path, name, arr, icc, photometric="rgb", resolution=(300, 300)):
    """A master made by the encoder from a TIFF (lossy d=1), as the toolkit makes them."""
    src = tmp_path / HOSTILE / "tiff"
    src.mkdir(parents=True, exist_ok=True)
    extra = [(34675, "B", len(icc), icc, True)] if icc else []
    kw = {"resolution": resolution, "resolutionunit": 2} if resolution else {}
    tifffile.imwrite(src / f"{name}.tif", arr, photometric=photometric, extratags=extra, **kw)
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"),
           "JXLPHOTO_SETTINGS": "", "JXLPHOTO_JXL_THUMB": "0"}
    r = subprocess.run([sys.executable, str(REPO / "jxl_tiff_encoder.py"), str(src), "--mode", "1",
                        "--distance", "1", "--effort", "1", "--no-preflight", "--overwrite"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=600, env=env)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return src / "converted_jxl" / f"{name}.jxl"


def _master8(tmp_path, name):
    """An 8-bit JXL (the encoder's masters are always 16-bit: this is a JPEG's
    or another program's) carrying a real master's metadata: the ICC token in
    XMP and the EXIF resolution."""
    m16 = _master(tmp_path, name + " 16", _photo(400, 600, 3, 65535), romm_toe_icc())
    folder = tmp_path / HOSTILE / "eight"
    folder.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    png = folder / "src.png"
    Image.fromarray(_photo(400, 600, 3, 255)).save(png)
    icc = folder / "romm.icc"
    icc.write_bytes(romm_toe_icc())
    jxl = folder / f"{name}.jxl"
    subprocess.run(["cjxl", str(png), str(jxl), "-d", "1", "-e", "1", "--container=1",
                    "-x", f"icc_pathname={icc}"], check=True, capture_output=True, timeout=300)
    png.unlink()
    icc.unlink()
    af = tmp_path / "copy.args"
    lines = ["-charset", "filename=utf8", "-overwrite_original", "-tagsfromfile", str(m16),
             "-all:all", str(jxl)]
    af.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run(["exiftool", "-@", str(af)], check=True, capture_output=True, timeout=120)
    return jxl


def _canon(data: bytes) -> bytes:
    """A PNG without the wall-clock dates ImageMagick writes (tIME, date:* text)."""
    import struct
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data
    out, i = [data[:8]], 8
    while i + 8 <= len(data):
        n, typ = struct.unpack(">I4s", data[i:i + 8])
        body = data[i + 8:i + 8 + n]
        if not (typ == b"tIME" or (typ in (b"tEXt", b"zTXt", b"iTXt") and body.startswith(b"date:"))):
            out.append(data[i:i + 12 + n])
        i += 12 + n
    return b"".join(out)


def _photo(h, w, channels, maxval, seed=3):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    planes = [x * maxval // (w - 1), y * maxval // (h - 1), np.full((h, w), maxval // 3),
              np.full((h, w), maxval)]
    base = np.stack(planes[:channels], -1).squeeze()
    noise = rng.integers(-maxval // 30, maxval // 30, base.shape)
    return np.clip(base + noise, 0, maxval).astype(np.uint16 if maxval > 255 else np.uint8)


def _run_main(mod, argv):
    old = sys.argv
    sys.argv = [mod.__name__] + [str(a) for a in argv]
    try:
        try:
            mod.main()
        except SystemExit as e:
            assert e.code in (0, None), f"{mod.__name__} exited {e.code}"
    finally:
        sys.argv = old


def _both_routes(monkeypatch, mod, helper, argv_for, out_dir, tmp_path):
    """(memory-route file bytes, PNG-route file bytes, how many decodes took memory)."""
    took = []
    real = getattr(mod, helper)

    def spy(*a, **k):
        r = real(*a, **k)
        took.append(r is not None)
        return r

    monkeypatch.setattr(mod, helper, spy)
    _run_main(mod, argv_for(out_dir / "new"))
    monkeypatch.setattr(mod, helper, lambda *a, **k: None)
    _run_main(mod, argv_for(out_dir / "old"))
    new = sorted(p for p in (out_dir / "new").rglob("*") if p.is_file() and p.name != "checksums.md5")
    old = sorted(p for p in (out_dir / "old").rglob("*") if p.is_file() and p.name != "checksums.md5")
    assert [p.name for p in new] == [p.name for p in old] and new, (new, old)
    return [_canon(p.read_bytes()) for p in new], [_canon(p.read_bytes()) for p in old], took


JPEG_ARGS = ["--mode", "2", "--force-convert", "--decode", "--format", "jpeg", "--quality", "70",
             "--icc-profile", "sRGB"]


@needs_tools
@pytest.mark.parametrize("case", ["rgb16", "rgba16"])
def test_resized_jpeg_takes_memory_and_is_the_same_file(tmp_path, monkeypatch, frozen, case):
    arr = _photo(400, 600, 3 if case == "rgb16" else 4, 65535)
    jxl = _master(tmp_path, f"写真 {case}", arr, romm_toe_icc())
    out = tmp_path / HOSTILE / "jpeg"
    argv = lambda o: [jxl.parent, o] + JPEG_ARGS + ["--resize-long", "300", "--sharpen", "screen"]
    new, old, took = _both_routes(monkeypatch, tr, "_decode_in_memory_for_magick", argv, out, tmp_path)
    assert took == [True], took
    assert new == old
    from PIL import Image
    with Image.open(out / "new" / f"写真 {case}.jpg") as im:
        assert im.info.get("dpi") == (300, 300), im.info     # the JFIF density came over


@needs_tools
def test_resized_jxl_takes_memory_and_is_the_same_file(tmp_path, monkeypatch, frozen):
    jxl = _master(tmp_path, "写真 rec", _photo(400, 600, 3, 65535), romm_toe_icc())
    out = tmp_path / HOSTILE / "jxl"
    argv = lambda o: [jxl.parent, o, "--mode", "2", "--output-icc", "sRGB", "--resize-long", "300",
                      "--sharpen", "screen", "--distance", "1", "--effort", "1",
                      "--on-unknown", "convert", "--on-regeneration", "convert",
                      "--on-downgrade", "convert", "--no-preflight"]
    new, old, took = _both_routes(monkeypatch, rec, "_decode_in_memory_for_derivative", argv, out, tmp_path)
    assert took == [True], took
    assert new == old


@needs_tools
@pytest.mark.parametrize("case", ["grey16", "png output", "8-bit master",
                                  "full-size 8-bit conversion", "no resolution"])
def test_what_keeps_the_png_route(tmp_path, monkeypatch, frozen, case):
    if case == "grey16":
        jxl = _master(tmp_path, "灰色", _photo(400, 600, 1, 65535), grey_toe_icc(), "minisblack")
    elif case == "8-bit master":
        jxl = _master8(tmp_path, "八")
    elif case == "no resolution":
        jxl = _master(tmp_path, "無", _photo(400, 600, 3, 65535), romm_toe_icc(), resolution=None)
    else:
        jxl = _master(tmp_path, "写真", _photo(400, 600, 3, 65535), romm_toe_icc())
    out = tmp_path / HOSTILE / "out"
    if case == "png output":
        args = ["--mode", "2", "--force-convert", "--decode", "--format", "png", "--bit-depth", "16",
                "--icc-profile", "sRGB", "--resize-long", "300"]
    elif case == "full-size 8-bit conversion":
        args = JPEG_ARGS
    else:
        args = JPEG_ARGS + ["--resize-long", "300"]
    argv = lambda o: [jxl.parent, o] + args
    new, old, took = _both_routes(monkeypatch, tr, "_decode_in_memory_for_magick", argv, out, tmp_path)
    assert not any(took), took
    assert new == old


@needs_tools
def test_an_8bit_source_keeps_the_png_route_in_the_recompressor(tmp_path, monkeypatch, frozen):
    jxl = _master8(tmp_path, "八 rec")
    out = tmp_path / HOSTILE / "jxl8"
    argv = lambda o: [jxl.parent, o, "--mode", "2", "--output-icc", "sRGB", "--resize-long", "300",
                      "--distance", "1", "--effort", "1", "--on-unknown", "convert",
                      "--on-regeneration", "convert", "--on-downgrade", "convert", "--no-preflight"]
    new, old, took = _both_routes(monkeypatch, rec, "_decode_in_memory_for_derivative", argv, out, tmp_path)
    assert took == [False], took
    assert new == old


@needs_tools
def test_the_memory_route_writes_no_png(tmp_path, monkeypatch):
    jxl = _master(tmp_path, "写真 tmp", _photo(400, 600, 3, 65535), romm_toe_icc())
    seen = []
    real = tr._run_captured

    def spy(cmd, timeout, *a, **k):
        seen.append([str(c) for c in cmd])
        return real(cmd, timeout, *a, **k)

    monkeypatch.setattr(tr, "_run_captured", spy)
    _run_main(tr, [jxl.parent, tmp_path / HOSTILE / "o"] + JPEG_ARGS + ["--resize-long", "300"])
    djxl_png = [c for c in seen if os.path.basename(c[0]).lower().startswith("djxl")
                and any(a.endswith(".png") for a in c)]
    assert not djxl_png, djxl_png


@needs_tools
def test_master_tags_are_read_once_per_file(tmp_path, monkeypatch):
    """#680: the master's CreatorTool/Relation/resolution come from one exiftool
    process for the run (kept while the file keeps its size and date)."""
    jxl = _master(tmp_path, "一回", _photo(200, 300, 3, 65535), romm_toe_icc())
    old = jxl.stat().st_mtime - 3600
    os.utime(jxl, (old, old))           # a settled file, as masters are
    calls = []
    real = tr._run_exiftool_argfile

    def spy(args, *a, **k):
        if "-IFD0:XResolution#" in args:
            calls.append(args[-1])
        return real(args, *a, **k)

    monkeypatch.setattr(tr, "_run_exiftool_argfile", spy)
    monkeypatch.setattr(tr, "_SOURCE_TAGS_CACHE", {})
    a = tr._read_creator_and_relation(jxl)
    b = tr._read_creator_and_relation(jxl)
    c = tr._exif_resolution(jxl)
    assert a == b and c == (2, 300, 300) and len(calls) == 1, (calls, c)
    newer = old + 60
    os.utime(jxl, (newer, newer))       # another date: read again
    tr._read_creator_and_relation(jxl)
    assert len(calls) == 2
    os.utime(jxl, None)                 # just written: never kept
    tr._read_creator_and_relation(jxl)
    tr._read_creator_and_relation(jxl)
    assert len(calls) == 4
