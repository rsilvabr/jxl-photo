"""A JPEG decoded from the JXL of a rotated JPEG gets its original orientation
back (v2.26.0), and the XMP mirror of a reset Orientation goes (#634).

A JXL made from a JPEG with EXIF Orientation 2-8 keeps the pixels as the JPEG
stored them plus an orientation in its codestream; every decode turns them
upright. JPEG_ORIENTATION = "original" (the default) turns a JPEG output back:
stored pixel order + the original EXIF Orientation. A recompression loses the
codestream orientation, so it records `jxlphoto-orientation:<n>` once.
PNG and TIFF outputs stay upright with Orientation 1.

Real codecs; every file inside a folder named with Japanese, brackets, a
space and %. The red block of the test picture is at the stored top-left;
upright (Orientation 6) it is at the top-right.

Pre-fix proof: point the tests at the HEAD scripts
    git show HEAD:<script> > <tmp>/<script>     (all five)
    JXLPHOTO_SCRIPTS_UNDER_TEST=<tmp> python -m pytest tests/test_orientation_261110.py
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

HOSTILE = "伊豆沼 [tmp] 100%"
TOKEN = "jxlphoto-orientation:"
_TOOLS = ("cjxl", "djxl", "exiftool", "jxlinfo", "magick")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")
RECOMPRESS = ["--on-unknown", "convert", "--on-regeneration", "convert",
              "--on-downgrade", "convert"]


# ----------------------------------------------------------------- helpers
def _run(tmp_path, script, *args, settings="", timeout=600):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"),
           "JXLPHOTO_SETTINGS": settings, "JXLPHOTO_JXL_THUMB": "0",
           "JXLPHOTO_NO_UPDATE_CHECK": "1"}
    r = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, env=env,
                       stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout + r.stderr


def _tra(tmp_path, *args, **kw):
    return _run(tmp_path, "jxl_jpeg_transcoder.py", *args, **kw)


def _rec(tmp_path, *args):
    return _run(tmp_path, "jxl_recompressor.py", *args)


def _dec(tmp_path, *args):
    return _run(tmp_path, "jxl_tiff_decoder.py", *args)


def _enc(tmp_path, *args):
    return _run(tmp_path, "jxl_tiff_encoder.py", *args)


def _folder(tmp_path, name):
    d = tmp_path / HOSTILE / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _only(folder, pattern, but=None):
    found = sorted(p for p in Path(folder).rglob(pattern) if p != but)
    assert len(found) == 1, (pattern, found)
    return found[0]


def _exiftool(args, path):
    path = Path(path)
    return subprocess.run(["exiftool", *args, path.name], cwd=str(path.parent),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def _tags(path, *names):
    r = _exiftool(["-j", "-n", "-G1", *[f"-{n}" for n in names]], path)
    return json.loads(r.stdout)[0] if r.stdout.strip() else {}


def _orientation(path):
    """(EXIF Orientation, XMP tiff:Orientation) of a file; None = absent."""
    t = _tags(path, "IFD0:Orientation", "XMP-tiff:Orientation")
    return t.get("IFD0:Orientation"), t.get("XMP-tiff:Orientation")


def _relation(path):
    rel = _tags(path, "XMP-dc:Relation").get("XMP-dc:Relation")
    return [] if rel is None else ([str(v) for v in rel] if isinstance(rel, list) else [str(rel)])


def _tokens(path):
    return [t for t in _relation(path) if t.startswith(TOKEN)]


def _size(path):
    with Image.open(path) as im:
        return im.size


def _red_at(path, x, y):
    with Image.open(path) as im:
        r, g, b = im.convert("RGB").getpixel((x, y))
    return r > 180 and g < 90 and b < 90


def _corner_jpeg(path, orientation=6, w=300, h=200):
    """A smooth 300x200 JPEG, red block at the stored top-left, a camera-like
    EXIF with this Orientation and the same value in XMP tiff:Orientation (as
    Lightroom writes it)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.zeros((h, w, 3), np.uint8)
    arr[:, :] = (30, 60, 200)
    arr[:40, :60] = (255, 0, 0)
    Image.fromarray(arr).save(path, quality=95, subsampling=0)
    r = _exiftool(["-overwrite_original", "-n", f"-IFD0:Orientation={orientation}",
                   f"-XMP-tiff:Orientation={orientation}", "-Make=Nikon", "-Model=Z 7",
                   "-ISO=100", "-DateTimeOriginal=2026:01:01 10:00:00",
                   "-XMP-dc:Title=Test"], path)
    assert r.returncode == 0, r.stdout + r.stderr
    return path


def _jxl_of(tmp_path, name, orientation=6, lossy=False):
    """(jpeg, jxl): the transcoder's JXL of a rotated JPEG (jbrd, or a pixel
    re-encode), the JXL moved into a folder of its own."""
    d = _folder(tmp_path, name)
    jpg = _corner_jpeg(d / "DSC_0001 (2).jpg", orientation)
    args = (["--force-convert", "--from-jpeg", "--distance", "0.5"] if lossy
            else ["--force-transcode"])
    rc, out = _tra(tmp_path, d, *args, "--mode", "1")
    assert rc == 0, out
    jxl = _only(d, "*.jxl")
    j = _folder(tmp_path, name + "_jxl")
    moved = j / jxl.name
    shutil.move(str(jxl), str(moved))
    return jpg, moved


def _recompressed(tmp_path, jxl, name, *extra):
    """The recompressor's re-encode of `jxl`, moved into a folder of its own."""
    rc, out = _rec(tmp_path, jxl.parent, "--mode", "1", "--distance", "3",
                   "--no-keep-smaller", "--jbrd-policy", "convert", *RECOMPRESS, *extra)
    assert rc == 0, out
    res = _only(jxl.parent, "*.jxl", but=jxl)
    j = _folder(tmp_path, name)
    moved = j / res.name
    shutil.move(str(res), str(moved))
    return moved


def _decode(tmp_path, jxl, fmt, *extra, settings=""):
    rc, out = _tra(tmp_path, jxl.parent, "--force-convert", "--decode", "--format", fmt,
                   "--mode", "1", *extra, settings=settings)
    assert rc == 0, out
    return _only(jxl.parent, "*.png" if fmt == "png" else "*.jpg")


_MODS = {}


def _script_module(name):
    if name not in _MODS:
        sys.path.insert(0, str(SCRIPTS))
        try:
            spec = importlib.util.spec_from_file_location(
                f"or261110_{name}", str(SCRIPTS / f"{name}.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        finally:
            sys.path.remove(str(SCRIPTS))
        _MODS[name] = mod
    return _MODS[name]


# ------------------------------------------------- the JPEG comes back as stored
@needs_tools
@pytest.mark.parametrize("lossy", [False, True], ids=["jbrd", "lossy"])
def test_jpeg_of_a_rotated_jpeg_comes_back_as_stored(tmp_path, lossy):
    _jpg, jxl = _jxl_of(tmp_path, "a", lossy=lossy)
    res = _decode(tmp_path, jxl, "jpeg")
    assert _size(res) == (300, 200), _size(res)
    assert _orientation(res) == (6, None), _orientation(res)
    assert _red_at(res, 5, 10) and not _red_at(res, 290, 10)
    assert not _tokens(res)
    assert _tags(res, "IFD0:Make").get("IFD0:Make") == "Nikon"


@needs_tools
@pytest.mark.parametrize("n", [2, 3, 4, 5, 7, 8])
def test_every_orientation_is_turned_back_exactly(tmp_path, n):
    jpg, jxl = _jxl_of(tmp_path, f"o{n}", orientation=n)
    res = _decode(tmp_path, jxl, "jpeg")
    assert _size(res) == (300, 200), _size(res)
    assert _orientation(res)[0] == n
    a = np.asarray(Image.open(jpg).convert("RGB")).astype(int)
    b = np.asarray(Image.open(res).convert("RGB")).astype(int)
    assert np.abs(a - b).mean() < 3, np.abs(a - b).mean()


@needs_tools
def test_upright_is_what_v2_25_3_wrote(tmp_path):
    _jpg, jxl = _jxl_of(tmp_path, "up")
    res = _decode(tmp_path, jxl, "jpeg", "--jpeg-orientation", "upright")
    assert _size(res) == (200, 300), _size(res)
    assert _orientation(res) == (1, None), _orientation(res)
    assert _red_at(res, 190, 10) and not _red_at(res, 5, 10)


@needs_tools
def test_the_overlay_sets_jpeg_orientation(tmp_path):
    _jpg, jxl = _jxl_of(tmp_path, "ov")
    overlay = tmp_path / HOSTILE / "settings.json"
    overlay.write_text(json.dumps({"jxl_jpeg_transcoder": {"JPEG_ORIENTATION": "upright"}}),
                       encoding="utf-8")
    res = _decode(tmp_path, jxl, "jpeg", settings=str(overlay))
    assert _size(res) == (200, 300), _size(res)
    assert _orientation(res)[0] == 1


@needs_tools
def test_a_png_stays_upright_and_loses_the_xmp_mirror(tmp_path):
    _jpg, jxl = _jxl_of(tmp_path, "png")
    res = _decode(tmp_path, jxl, "png")
    assert _size(res) == (200, 300), _size(res)
    assert _orientation(res) in ((1, None), (None, None)), _orientation(res)
    assert _red_at(res, 190, 10) and not _red_at(res, 5, 10)


@needs_tools
def test_below_quality_90_the_turned_back_jpeg_keeps_444(tmp_path):
    _jpg, jxl = _jxl_of(tmp_path, "q85")
    res = _decode(tmp_path, jxl, "jpeg", "--quality", "85")
    assert _size(res) == (300, 200)
    r = _exiftool(["-s3", "-n", "-File:YCbCrSubSampling"], res)
    assert r.stdout.strip() == "1 1", r.stdout


@needs_tools
def test_a_resized_jpeg_derivative_is_turned_back_with_its_dimensions(tmp_path):
    _jpg, jxl = _jxl_of(tmp_path, "der")
    res = _decode(tmp_path, jxl, "jpeg", "--resize-long", "150")
    assert _size(res) == (150, 100), _size(res)
    assert _orientation(res) == (6, None), _orientation(res)
    assert _red_at(res, 3, 5) and not _red_at(res, 145, 5)
    t = _tags(res, "ExifIFD:ExifImageWidth", "ExifIFD:ExifImageHeight")
    assert (t.get("ExifIFD:ExifImageWidth"), t.get("ExifIFD:ExifImageHeight")) == (150, 100), t
    assert not _tokens(res)
    assert any(v.startswith("jxlphoto-derived:") for v in _relation(res))


@needs_tools
def test_a_rotated_jpeg_with_the_standard_srgb_profile_comes_back_as_stored(tmp_path):
    """The un-rotation goes through ImageMagick, which drops a standard sRGB
    iCCP when it reads the decoded PNG (#635): the profile has to survive."""
    std = (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "spool"
           / "drivers" / "color" / "sRGB Color Space Profile.icm")
    if not std.is_file():
        pytest.skip("needs the Windows sRGB profile")
    d = _folder(tmp_path, "std")
    jpg = _corner_jpeg(d / "DSC_0001 (2).jpg")
    with Image.open(jpg) as im:
        exif = im.info.get("exif")
        im.save(d / "tmp.jpg", quality=95, subsampling=0, icc_profile=std.read_bytes(),
                **({"exif": exif} if exif else {}))
    os.replace(d / "tmp.jpg", jpg)
    assert _orientation(jpg)[0] == 6
    rc, out = _tra(tmp_path, d, "--force-transcode", "--mode", "1")
    assert rc == 0, out
    made = _only(d, "*.jxl")
    jxl = _folder(tmp_path, "std_jxl") / made.name
    shutil.move(str(made), str(jxl))
    res = _decode(tmp_path, jxl, "jpeg")
    assert _size(res) == (300, 200), _size(res)
    assert _orientation(res)[0] == 6
    assert _red_at(res, 5, 10) and not _red_at(res, 290, 10)
    name = _exiftool(["-s3", "-ICC_Profile:ProfileDescription"], res).stdout.strip()
    assert "sRGB" in name, name


# ------------------------------------------------- the recompressor's record
@needs_tools
@pytest.mark.parametrize("lossy", [False, True], ids=["jbrd", "lossy"])
def test_a_recompression_records_the_orientation_once(tmp_path, lossy):
    _jpg, jxl = _jxl_of(tmp_path, "r", lossy=lossy)
    once = _recompressed(tmp_path, jxl, "r_once")
    assert _tokens(once) == [TOKEN + "6"], _relation(once)
    assert _orientation(once) == (1, None), _orientation(once)
    twice = _recompressed(tmp_path, once, "r_twice", "--distance", "5")
    assert _tokens(twice) == [TOKEN + "6"], _relation(twice)
    res = _decode(tmp_path, twice, "jpeg")
    assert _size(res) == (300, 200), _size(res)
    assert _orientation(res) == (6, None), _orientation(res)
    assert _red_at(res, 5, 10) and not _red_at(res, 290, 10)
    assert not _tokens(res)


@needs_tools
def test_a_png_of_a_recompressed_copy_is_upright_without_the_token(tmp_path):
    _jpg, jxl = _jxl_of(tmp_path, "rp", lossy=True)
    once = _recompressed(tmp_path, jxl, "rp_once")
    res = _decode(tmp_path, once, "png")
    assert _size(res) == (200, 300), _size(res)
    assert _orientation(res)[0] in (1, None)
    assert not _tokens(res)


@needs_tools
def test_a_recompressor_derivative_records_it_too(tmp_path):
    _jpg, jxl = _jxl_of(tmp_path, "rd")
    der = _recompressed(tmp_path, jxl, "rd_der", "--resize-long", "150")
    assert _tokens(der) == [TOKEN + "6"], _relation(der)
    assert any(v.startswith("jxlphoto-derived:") for v in _relation(der))
    res = _decode(tmp_path, der, "jpeg")
    assert _size(res) == (150, 100), _size(res)
    assert _orientation(res)[0] == 6
    assert _red_at(res, 3, 5) and not _red_at(res, 145, 5)


@needs_tools
def test_the_decoder_and_the_encoder_carry_the_record(tmp_path):
    _jpg, jxl = _jxl_of(tmp_path, "t", lossy=True)
    once = _recompressed(tmp_path, jxl, "t_once")
    rc, out = _dec(tmp_path, once.parent, "--mode", "1")
    assert rc == 0, out
    tif = _only(once.parent, "*.tif")
    assert tifffile.imread(str(tif)).shape[:2] == (300, 200)
    assert _orientation(tif) == (1, None), _orientation(tif)
    assert _tokens(tif) == [TOKEN + "6"], _relation(tif)
    t = _folder(tmp_path, "t_tif")
    shutil.move(str(tif), str(t / tif.name))
    rc, out = _enc(tmp_path, t, "--mode", "1", "--distance", "0", "--no-preflight")
    assert rc == 0, out
    master = _only(t, "*.jxl")
    assert _tokens(master) == [TOKEN + "6"], _relation(master)
    m = _folder(tmp_path, "t_master")
    shutil.move(str(master), str(m / master.name))
    res = _decode(tmp_path, m / master.name, "jpeg")
    assert _size(res) == (300, 200), _size(res)
    assert _orientation(res)[0] == 6
    assert _red_at(res, 5, 10) and not _red_at(res, 290, 10)


# ------------------------------------------------- #634: the XMP mirror
@needs_tools
def test_634_the_decoders_tiff_loses_the_xmp_mirror(tmp_path):
    _jpg, jxl = _jxl_of(tmp_path, "m")
    rc, out = _dec(tmp_path, jxl.parent, "--mode", "1")
    assert rc == 0, out
    tif = _only(jxl.parent, "*.tif")
    assert tifffile.imread(str(tif)).shape[:2] == (300, 200)
    assert _orientation(tif) == (1, None), _orientation(tif)
    assert _tags(tif, "XMP-dc:Title").get("XMP-dc:Title") == "Test"


# ------------------------------------------------- controls that must not change
@needs_tools
def test_control_bit_exact_recovery(tmp_path):
    jpg, jxl = _jxl_of(tmp_path, "c1")
    rc, out = _tra(tmp_path, jxl.parent, "--force-transcode", "--decode", "--mode", "1")
    assert rc == 0, out
    back = _only(jxl.parent, "*.jpg")
    assert hashlib.md5(back.read_bytes()).hexdigest() == hashlib.md5(jpg.read_bytes()).hexdigest()


@needs_tools
def test_control_a_jbrd_archive_is_copied_verbatim_without_a_token(tmp_path):
    _jpg, jxl = _jxl_of(tmp_path, "c2")
    rc, out = _rec(tmp_path, jxl.parent, "--mode", "1", "--distance", "3", *RECOMPRESS)
    assert rc == 0, out
    copy = _only(jxl.parent, "*.jxl", but=jxl)
    assert copy.read_bytes() == jxl.read_bytes()


@needs_tools
def test_control_a_tiff_master_keeps_the_tiffs_orientation(tmp_path):
    d = _folder(tmp_path, "c3")
    tif = d / "DSC_0001 (2).tif"
    rng = np.random.default_rng(5)
    tifffile.imwrite(str(tif), (rng.random((200, 300, 3)) * 65535).astype(np.uint16),
                     photometric="rgb")
    r = _exiftool(["-overwrite_original", "-n", "-IFD0:Orientation=6",
                   "-XMP-tiff:Orientation=6", "-Make=Nikon"], tif)
    assert r.returncode == 0, r.stdout + r.stderr
    rc, out = _enc(tmp_path, d, "--mode", "1", "--distance", "0", "--no-preflight")
    assert rc == 0, out
    jxl = _only(d, "*.jxl")
    assert not _tokens(jxl)
    j = _folder(tmp_path, "c3_jxl")
    shutil.move(str(jxl), str(j / jxl.name))
    res = _decode(tmp_path, j / jxl.name, "jpeg")
    assert _size(res) == (300, 200), _size(res)
    assert _orientation(res) == (6, 6), _orientation(res)
    once = _recompressed(tmp_path, j / jxl.name, "c3_once", "--distance", "1")
    assert not _tokens(once)
    assert _orientation(once) == (6, 6), _orientation(once)


# ------------------------------------------------- units
def test_unrotate_table_and_token_reader(tmp_path, monkeypatch):
    m = _script_module("jxl_jpeg_transcoder")
    assert sorted(m._UNROTATE_MAGICK) == [2, 3, 4, 5, 6, 7, 8]
    assert m.ORIENTATION_PREFIX == TOKEN
    assert _script_module("jxl_recompressor").ORIENTATION_PREFIX == TOKEN
    f = tmp_path / "x.jxl"
    f.write_bytes(b"x")
    for info, tokens, want in (
            ("Orientation: 6 (x)\n", [], 6),
            ("Orientation: 6 (x)\n", [TOKEN + "8"], 6),          # the codestream wins
            ("Orientation: 1 (Normal)\n", [TOKEN + "8", "user value"], 8),
            ("Orientation: 1 (Normal)\n", ["jxlphoto-origin:jpeg"], None),
            ("Orientation: 1 (Normal)\n", [TOKEN + "1"], None),
            ("Orientation: 1 (Normal)\n", [TOKEN + "x"], None),
            (None, [TOKEN + "3"], 3)):                           # no jxlinfo: the token still counts
        monkeypatch.setattr(m, "_jxlinfo_text", lambda p, _i=info: _i)
        monkeypatch.setattr(m, "_read_creator_and_relation", lambda p, _t=tokens: ("", _t))
        assert m._stored_orientation(f) == want, (info, tokens)

    def boom(p):
        raise RuntimeError("unreadable")
    monkeypatch.setattr(m, "_jxlinfo_text", lambda p: "Orientation: 1 (Normal)\n")
    monkeypatch.setattr(m, "_read_creator_and_relation", boom)
    assert m._stored_orientation(f) is None


@needs_tools
def test_without_imagemagick_the_jpeg_comes_out_upright(tmp_path, monkeypatch):
    _jpg, jxl = _jxl_of(tmp_path, "nomagick")
    m = _script_module("jxl_jpeg_transcoder")
    m.setup_logger()
    monkeypatch.setattr(m, "MAGICK_AVAILABLE", False)
    monkeypatch.setattr(m, "JPEG_ORIENTATION", "original")
    m._JXLINFO_NOTES.discard("no-magick-orientation")
    out = jxl.with_suffix(".jpg")
    res = m.decode_to_image(jxl, out, out, 95, "jpeg", 8, None, False, True, False)
    assert res[1] == "ok", res
    assert _size(out) == (200, 300), _size(out)
    assert _orientation(out)[0] == 1
    assert "no-magick-orientation" in m._JXLINFO_NOTES
