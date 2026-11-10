"""What stayed open after v2.25.3 (round 76, #630-#635).

#630 (transcoder)  JXL -> PNG failed for a JXL made from a JPEG with EXIF and
     no XMP: djxl writes its own copy of the EXIF into the PNG ("Raw profile
     type exif"), and exiftool then fails on that PNG.
#631 (transcoder)  JXL -> JPEG/PNG failed for a JPEG whose EXIF is malformed:
     the codec carries that EXIF into the output as a block and exiftool
     cannot rewrite it.
#632 (the four backends)  JXLPHOTO_SETTINGS naming a missing file ran without
     the overlay and without a line in the log.
#633 (wrapper)  jxlinfo was not part of the dependency check.
#634 (transcoder, decoder, recompressor)  XMP tiff:Orientation kept the
     camera's value where the EXIF Orientation was reset to 1.
#635 (transcoder, recompressor)  Derivatives and colour conversions failed for
     a source whose profile is a standard sRGB one: ImageMagick replaces such
     a profile by an sRGB chunk when it reads or writes a PNG.
Also: one jxlinfo run per file (a small cache), and the decoder's opt-in
--exiftool-ignore-minor (maker notes exiftool cannot parse: an error with a
hint by default, decoded unchecked with the flag, refused with a delete).

Real codecs; every file inside a folder named with Japanese, brackets, a
space and %.

Pre-fix proof: point the tests at the HEAD scripts
    git show HEAD:<script> > <tmp>/<script>     (all five)
    JXLPHOTO_SCRIPTS_UNDER_TEST=<tmp> python -m pytest tests/test_open_items_261110.py
"""
import hashlib
import importlib.util
import json
import os
import shutil
import struct
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
_TOOLS = ("cjxl", "djxl", "exiftool")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")
needs_jxlinfo = pytest.mark.skipif(
    not (all(shutil.which(t) for t in _TOOLS) and shutil.which("jxlinfo")),
    reason="needs cjxl, djxl, exiftool and jxlinfo on PATH")
BACKENDS = ["jxl_tiff_encoder", "jxl_tiff_decoder", "jxl_jpeg_transcoder", "jxl_recompressor"]


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


def _tra(tmp_path, *args):
    return _run(tmp_path, "jxl_jpeg_transcoder.py", *args)


def _folder(tmp_path, name="shoot"):
    d = tmp_path / HOSTILE / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _only(folder, pattern):
    found = sorted(Path(folder).rglob(pattern))
    assert len(found) == 1, (pattern, found)
    return found[0]


def _jpeg(path, seed=1, w=160, h=120):
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    Image.fromarray((rng.random((h, w, 3)) * 255).astype(np.uint8)).save(path, quality=90)
    return path


def _exiftool(args, path):
    """exiftool on `path` run from its folder, by file name only (the folder's
    `[ ]` would be read as a wildcard in the directory part)."""
    path = Path(path)
    return subprocess.run(["exiftool", *args, path.name], cwd=str(path.parent),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def _tags(path, *names):
    r = _exiftool(["-j", "-n", "-G1", *[f"-{n}" for n in names]], path)
    return json.loads(r.stdout)[0] if r.stdout.strip() else {}


def _png_chunks(path):
    """[(type, keyword or None)] of every chunk of a PNG."""
    out = []
    with open(path, "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"
        while True:
            head = f.read(8)
            if len(head) < 8:
                break
            n, name = struct.unpack(">I", head[:4])[0], head[4:8].decode("latin-1")
            data = f.read(n)
            f.read(4)
            key = (data.split(b"\x00", 1)[0].decode("latin-1")
                   if name in ("tEXt", "zTXt", "iTXt") else None)
            out.append((name, key))
    return out


def _jxl_of(tmp_path, jpg, lossy=False):
    """The JXL the transcoder makes of `jpg` (jbrd, or a pixel re-encode),
    moved into a folder of its own."""
    args = (["--force-convert", "--from-jpeg", "--distance", "1"] if lossy
            else ["--force-transcode"])
    rc, out = _tra(tmp_path, jpg.parent, *args, "--mode", "1")
    assert rc == 0, out
    jxl = _only(jpg.parent, "*.jxl")
    d = _folder(tmp_path, jpg.parent.name + "_jxl")
    moved = d / jxl.name
    shutil.move(str(jxl), str(moved))
    return moved


_MODS = {}


def _script_module(name):
    """A script loaded from SCRIPTS under a private name."""
    if name not in _MODS:
        sys.path.insert(0, str(SCRIPTS))
        try:
            spec = importlib.util.spec_from_file_location(
                f"oi261110_{name}", str(SCRIPTS / f"{name}.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        finally:
            sys.path.remove(str(SCRIPTS))
        _MODS[name] = mod
    return _MODS[name]


# ------------------------------------------------------------------- #630
def _camera_exif_only(jpg):
    """A camera-like EXIF and NO XMP: the usual straight-out-of-camera JPEG."""
    r = _exiftool(["-overwrite_original", "-Make=Nikon", "-Model=Z 7", "-ISO=100",
                   "-ExposureTime=0.01", "-FNumber=5.6",
                   "-DateTimeOriginal=2026:01:01 10:00:00"], jpg)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "XMP" not in _exiftool(["-G0", "-a", "-s"], jpg).stdout


@needs_tools
@pytest.mark.parametrize("lossy", [False, True], ids=["jbrd", "lossy"])
def test_630_png_of_a_jpeg_with_exif_and_no_xmp(tmp_path, lossy):
    jpg = _jpeg(_folder(tmp_path, "cam") / "DSC_0001 (2).jpg")
    _camera_exif_only(jpg)
    jxl = _jxl_of(tmp_path, jpg, lossy=lossy)
    rc, out = _tra(tmp_path, jxl.parent, "--force-convert", "--decode", "--format", "png",
                   "--mode", "1")
    assert rc == 0, out
    png = _only(jxl.parent, "*.png")
    t = _tags(png, "IFD0:Make", "ExifIFD:DateTimeOriginal")
    assert t.get("IFD0:Make") == "Nikon", t
    assert t.get("ExifIFD:DateTimeOriginal") == "2026:01:01 10:00:00", t
    chunks = _png_chunks(png)
    assert not [k for _n, k in chunks if k and k.startswith("Raw profile type")], chunks
    assert [n for n, _k in chunks].count("eXIf") == 1, chunks


@needs_tools
def test_630_control_png_of_a_jpeg_without_metadata(tmp_path):
    jpg = _jpeg(_folder(tmp_path, "bare") / "DSC_0001 (2).jpg")
    jxl = _jxl_of(tmp_path, jpg)
    rc, out = _tra(tmp_path, jxl.parent, "--force-convert", "--decode", "--format", "png",
                   "--mode", "1")
    assert rc == 0, out
    assert _only(jxl.parent, "*.png").stat().st_size > 0


@needs_tools
def test_630_drop_djxl_png_profiles_keeps_the_pixels(tmp_path):
    m = _script_module("jxl_jpeg_transcoder")
    jpg = _jpeg(_folder(tmp_path, "unit") / "DSC_0001 (2).jpg")
    _camera_exif_only(jpg)
    d = tmp_path / "plain"                 # cjxl/djxl by hand: an ASCII folder
    d.mkdir()
    subprocess.run(["cjxl", str(jpg), str(d / "a.jxl")], check=True, capture_output=True)
    png = d / "a.png"
    subprocess.run(["djxl", str(d / "a.jxl"), str(png)], check=True, capture_output=True)
    hostile = jpg.parent / "a 5%.png"
    shutil.move(str(png), str(hostile))
    assert ("zTXt", "Raw profile type exif") in _png_chunks(hostile)
    before = np.asarray(Image.open(hostile).convert("RGB")).copy()
    colour = [n for n, _k in _png_chunks(hostile) if n in ("iCCP", "sRGB", "gAMA", "cHRM")]
    assert m._drop_djxl_png_profiles(hostile) is True
    chunks = _png_chunks(hostile)
    assert not [k for _n, k in chunks if k and k.startswith("Raw profile type")], chunks
    assert [n for n, _k in chunks if n in ("iCCP", "sRGB", "gAMA", "cHRM")] == colour
    assert np.array_equal(np.asarray(Image.open(hostile).convert("RGB")), before)
    assert m._drop_djxl_png_profiles(hostile) is False
    assert not list(hostile.parent.glob("*.tmp"))


# ------------------------------------------------------------------- #631
def _malformed_exif():
    """An EXIF block whose IFD1 has a BitsPerSample pointing past its end
    (what one of the owner's JPEGs, written by an HDR program, carries):
    exiftool reads it with a warning and refuses to rewrite it."""
    make = b"TESTCAM\x00"
    ifd0_off = 8
    ifd1_off = ifd0_off + 2 + 12 + 4
    make_off = ifd1_off + 2 + 12 + 4
    ifd0 = (struct.pack("<H", 1) + struct.pack("<HHII", 0x010F, 2, len(make), make_off)
            + struct.pack("<I", ifd1_off))
    ifd1 = (struct.pack("<H", 1) + struct.pack("<HHII", 0x0102, 3, 3, 0x00FFFF00)
            + struct.pack("<I", 0))
    return b"II*\x00" + struct.pack("<I", ifd0_off) + ifd0 + ifd1 + make


def _jpeg_with_exif(path, exif):
    _jpeg(path)
    data = path.read_bytes()
    seg = b"Exif\x00\x00" + exif
    path.write_bytes(data[:2] + b"\xff\xe1" + struct.pack(">H", len(seg) + 2) + seg + data[2:])
    return path


@needs_tools
@pytest.mark.parametrize("fmt", ["jpeg", "png"])
def test_631_decode_of_a_jpeg_with_a_malformed_exif(tmp_path, fmt):
    jpg = _jpeg_with_exif(_folder(tmp_path, "bad") / "DSC_0001 (2).jpg", _malformed_exif())
    jxl = _jxl_of(tmp_path, jpg)
    rc, out = _tra(tmp_path, jxl.parent, "--force-convert", "--decode", "--format", fmt,
                   "--mode", "1")
    assert rc == 0, out
    res = _only(jxl.parent, "*.png" if fmt == "png" else "*.jpg")
    assert _tags(res, "IFD0:Make").get("IFD0:Make") == "TESTCAM"
    with Image.open(res) as im:
        assert im.size == (160, 120)


@needs_tools
def test_631_control_the_bit_exact_recovery_keeps_the_malformed_jpeg(tmp_path):
    jpg = _jpeg_with_exif(_folder(tmp_path, "badrec") / "DSC_0001 (2).jpg", _malformed_exif())
    jxl = _jxl_of(tmp_path, jpg)
    rc, out = _tra(tmp_path, jxl.parent, "--force-transcode", "--decode", "--mode", "1")
    assert rc == 0, out
    back = _only(jxl.parent, "*.jpg")
    assert hashlib.md5(back.read_bytes()).hexdigest() == hashlib.md5(jpg.read_bytes()).hexdigest()


@needs_tools
def test_631_drop_jpeg_codec_metadata_keeps_the_image_and_the_profile(tmp_path):
    m = _script_module("jxl_jpeg_transcoder")
    d = _folder(tmp_path, "unitjpg")
    jpg = d / "a 5%.jpg"
    rng = np.random.default_rng(4)
    icc = Image.new("RGB", (1, 1))
    from PIL import ImageCms
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.fromarray((rng.random((120, 160, 3)) * 255).astype(np.uint8)).save(
        jpg, quality=90, icc_profile=profile)
    del icc
    r = _exiftool(["-overwrite_original", "-Make=Nikon", "-XMP-dc:Title=T",
                   "-IPTC:Keywords=k"], jpg)
    assert r.returncode == 0, r.stdout + r.stderr
    before = np.asarray(Image.open(jpg).convert("RGB")).copy()
    assert m._drop_jpeg_codec_metadata(jpg) is True
    left = _exiftool(["-G0", "-a", "-s"], jpg).stdout
    assert "[EXIF]" not in left and "[XMP]" not in left and "[IPTC]" not in left, left
    assert "[ICC_Profile]" in left, left
    assert np.array_equal(np.asarray(Image.open(jpg).convert("RGB")), before)
    assert m._drop_jpeg_codec_metadata(jpg) is False
    assert not list(d.glob("*.tmp"))


# ------------------------------------------------------------------- #632
def _tiny_job(tmp_path, module):
    """(script, args) of a dry run each backend can plan on one small file."""
    d = _folder(tmp_path, module)
    if module == "jxl_tiff_encoder":
        rng = np.random.default_rng(2)
        tifffile.imwrite(str(d / "DSC_0001 (2).tif"),
                         (rng.random((48, 64, 3)) * 65535).astype(np.uint16), photometric="rgb")
        return ["--mode", "1", "--dry-run"], d
    if module == "jxl_jpeg_transcoder":
        _jpeg(d / "DSC_0001 (2).jpg")
        return ["--force-transcode", "--mode", "1", "--dry-run"], d
    png = d / "x.png"
    Image.fromarray(np.zeros((48, 64, 3), np.uint8)).save(png)
    subprocess.run(["cjxl", str(png), str(d / "DSC_0001 (2).jxl"), "-d", "0", "--container=1"],
                   check=True, capture_output=True, cwd=str(d))
    png.unlink()
    return ["--mode", "1", "--dry-run"], d


@needs_tools
@pytest.mark.parametrize("module", BACKENDS)
def test_632_a_missing_overlay_file_is_named_in_the_log(tmp_path, module):
    args, d = _tiny_job(tmp_path, module)
    missing = tmp_path / HOSTILE / "no such settings.json"
    rc, out = _run(tmp_path, f"{module}.py", d, *args, settings=str(missing))
    flat = " ".join(out.split())
    assert "JXLPHOTO_SETTINGS names a file that does not exist" in flat, out
    assert "Traceback" not in out, out


@needs_tools
@pytest.mark.parametrize("module", BACKENDS)
def test_632_control_no_line_without_the_variable_or_with_a_real_file(tmp_path, module):
    args, d = _tiny_job(tmp_path, module)
    rc, out = _run(tmp_path, f"{module}.py", d, *args, settings="")
    assert "names a file that does not exist" not in " ".join(out.split()), out
    real = tmp_path / HOSTILE / "settings.json"
    real.write_text("{}", encoding="utf-8")
    rc, out = _run(tmp_path, f"{module}.py", d, *args, settings=str(real))
    assert "names a file that does not exist" not in " ".join(out.split()), out


# ------------------------------------------------------------------- #633
def _wrapper():
    return _script_module("jxl_photo")


def test_633_status_line_says_when_jxlinfo_is_missing():
    wp = _wrapper()
    checker = wp.DependencyChecker.__new__(wp.DependencyChecker)
    full = dict.fromkeys(("cjxl", "djxl", "exiftool", "magick", "tifffile", "imagecodecs",
                          "pillow", "rich"), True)
    line = checker.format_status_line({**full, "jxlinfo": False})
    assert "jxlinfo missing" in line and line.startswith("[⚠] cjxl/djxl"), line
    for status in ({**full, "jxlinfo": True}, full):       # `full`: an older saved status
        assert checker.format_status_line(status).startswith("[✓] cjxl/djxl | "), status
    line = checker.format_status_line({**full, "djxl": False, "jxlinfo": False})
    assert line.startswith("[✗] cjxl/djxl (djxl missing)"), line


def test_633_check_dependencies_looks_for_jxlinfo():
    wp = _wrapper()

    class _Cfg:
        def __init__(self):
            import argparse
            self.config = argparse.Namespace(available_features=None)

        def update_tool_paths(self, paths): pass
        def _check_tiff_support(self): return True
        def _check_imagecodecs(self): return True
        def save_config(self): pass

    checker = wp.DependencyChecker(_Cfg())
    checker._detect_tool = lambda cmd: None if cmd == "jxlinfo" else "/usr/bin/x"
    checker._test_tool_execution = lambda p, a: True
    status = checker.check_dependencies(force=True)
    assert status["jxlinfo"] is False and status["cjxl"] is True, status


# ------------------------------------------------------------------- jxlinfo cache
@needs_jxlinfo
@pytest.mark.parametrize("name", ["jxl_tiff_decoder", "jxl_jpeg_transcoder",
                                  "jxl_recompressor"])
def test_jxlinfo_runs_once_per_file(tmp_path, name, monkeypatch):
    m = _script_module(name)
    d = _folder(tmp_path, "cache_" + name)
    png = d / "x.png"
    Image.fromarray(np.zeros((48, 64, 3), np.uint8)).save(png)
    jxl = d / "DSC_0001 (2).jxl"
    subprocess.run(["cjxl", "x.png", "a.jxl", "-d", "1"], check=True, capture_output=True,
                   cwd=str(d))
    shutil.move(str(d / "a.jxl"), str(jxl))
    calls = []
    real = m._run_captured

    def counting(cmd, *a, **k):
        if "jxlinfo" in Path(str(cmd[0])).name.lower():
            calls.append(list(cmd))
        return real(cmd, *a, **k)

    monkeypatch.setattr(m, "_run_captured", counting)
    first = m._jxlinfo_text(jxl)
    assert first and "JPEG XL image" in first
    assert m._jxlinfo_text(jxl) == first
    assert m._codestream_orientation(m._jxlinfo_text(jxl)) == 1
    if name == "jxl_recompressor":
        assert m._jxlinfo_colour(jxl) == ("enum", True)
    assert len(calls) == 1, calls
    # Another file under the same name is asked again.
    Image.fromarray(np.full((80, 96, 3), 90, np.uint8)).save(png)
    subprocess.run(["cjxl", "x.png", "a.jxl", "-d", "0"], check=True, capture_output=True,
                   cwd=str(d))
    os.replace(str(d / "a.jxl"), str(jxl))
    assert "96x80" in m._jxlinfo_text(jxl)
    assert len(calls) == 2, calls


# ------------------------------------------------------------------- #635
# ImageMagick replaces a profile it recognises as a standard sRGB one by an
# sRGB chunk when it reads or writes a PNG. The profile Windows ships is one
# of them; a profile littleCMS builds (the control) is not.
_STD_SRGB = (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "spool"
             / "drivers" / "color" / "sRGB Color Space Profile.icm")
needs_std_srgb = pytest.mark.skipif(
    not (all(shutil.which(t) for t in _TOOLS + ("magick", "jxlinfo")) and _STD_SRGB.is_file()),
    reason="needs cjxl, djxl, exiftool, magick, jxlinfo and the Windows sRGB profile")
_POLICIES = ["--on-unknown", "convert", "--on-regeneration", "convert",
             "--on-downgrade", "convert"]


def _red_jpeg(path, icc):
    """A JPEG of one saturated red, (200, 30, 30), carrying `icc`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.zeros((120, 160, 3), np.uint8)
    arr[:, :] = (200, 30, 30)
    Image.fromarray(arr).save(path, quality=95, subsampling=0, icc_profile=icc)
    return path


def _icc_name(path):
    return _exiftool(["-s3", "-ICC_Profile:ProfileDescription"], path).stdout.strip()


@needs_std_srgb
@pytest.mark.parametrize("fmt", ["jpeg", "png"])
def test_635_resized_decode_of_a_jpeg_with_the_standard_srgb_profile(tmp_path, fmt):
    jpg = _red_jpeg(_folder(tmp_path, "std") / "DSC_0001 (2).jpg", _STD_SRGB.read_bytes())
    jxl = _jxl_of(tmp_path, jpg)
    rc, out = _tra(tmp_path, jxl.parent, "--force-convert", "--decode", "--format", fmt,
                   "--mode", "1", "--resize-long", "80")
    assert rc == 0, out
    res = _only(jxl.parent, "*.png" if fmt == "png" else "*.jpg")
    assert "sRGB" in _icc_name(res), _icc_name(res)
    with Image.open(res) as im:
        assert im.size == (80, 60)
        r, g, b = im.convert("RGB").getpixel((40, 30))
    assert abs(r - 200) <= 4 and abs(g - 30) <= 6 and abs(b - 30) <= 6, (r, g, b)


@needs_std_srgb
def test_635_a_conversion_from_the_standard_srgb_profile_converts(tmp_path):
    """Not merely relabelled: sRGB (200, 30, 30) is about (171, 30, 30) in
    Adobe RGB; an ASSIGNED profile would leave 200."""
    jpg = _red_jpeg(_folder(tmp_path, "conv") / "DSC_0001 (2).jpg", _STD_SRGB.read_bytes())
    jxl = _jxl_of(tmp_path, jpg)
    rc, out = _tra(tmp_path, jxl.parent, "--force-convert", "--decode", "--format", "jpeg",
                   "--mode", "1", "--icc-profile", "AdobeRGB")
    assert rc == 0, out
    res = _only(jxl.parent, "*.jpg")
    assert "Adobe" in _icc_name(res), _icc_name(res)
    with Image.open(res) as im:
        r, _g, _b = im.convert("RGB").getpixel((80, 60))
    assert 160 <= r <= 182, r


@needs_std_srgb
@pytest.mark.parametrize("source", ["jpeg archive", "tiff master"])
def test_635_recompressor_derivative_of_a_standard_srgb_source(tmp_path, source):
    icc = _STD_SRGB.read_bytes()
    if source == "jpeg archive":
        jpg = _red_jpeg(_folder(tmp_path, "rj") / "DSC_0001 (2).jpg", icc)
        jxl = _jxl_of(tmp_path, jpg)
    else:
        d = _folder(tmp_path, "rt")
        rng = np.random.default_rng(6)
        tifffile.imwrite(str(d / "DSC_0001 (2).tif"),
                         (rng.random((120, 160, 3)) * 65535).astype(np.uint16),
                         photometric="rgb", extratags=[(34675, 7, len(icc), icc, True)])
        rc, out = _run(tmp_path, "jxl_tiff_encoder.py", d, "--mode", "1", "--distance", "1",
                       "--no-preflight")
        assert rc == 0, out
        made = _only(d, "*.jxl")
        jxl = _folder(tmp_path, "rt_jxl") / made.name
        shutil.move(str(made), str(jxl))
    rc, out = _run(tmp_path, "jxl_recompressor.py", jxl.parent, "--mode", "1",
                   "--distance", "2", "--resize-long", "80", "--jbrd-policy", "convert",
                   *_POLICIES)
    assert rc == 0, out
    der = [p for p in jxl.parent.rglob("*.jxl") if p != jxl]
    assert len(der) == 1, (der, out)
    info = subprocess.run(["jxlinfo", der[0].name], cwd=str(der[0].parent),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace").stdout
    assert "80x60" in info, info


@needs_std_srgb
def test_635_png_derivative_of_a_tiff_master_keeps_its_iccp(tmp_path):
    icc = _STD_SRGB.read_bytes()
    d = _folder(tmp_path, "tp")
    rng = np.random.default_rng(8)
    tifffile.imwrite(str(d / "DSC_0001 (2).tif"),
                     (rng.random((120, 160, 3)) * 65535).astype(np.uint16),
                     photometric="rgb", extratags=[(34675, 7, len(icc), icc, True)])
    rc, out = _run(tmp_path, "jxl_tiff_encoder.py", d, "--mode", "1", "--distance", "1",
                   "--no-preflight")
    assert rc == 0, out
    made = _only(d, "*.jxl")
    jxl = _folder(tmp_path, "tp_jxl") / made.name
    shutil.move(str(made), str(jxl))
    rc, out = _tra(tmp_path, jxl.parent, "--force-convert", "--decode", "--format", "png",
                   "--mode", "1", "--resize-long", "80")
    assert rc == 0, out
    png = _only(jxl.parent, "*.png")
    assert "iCCP" in [n for n, _k in _png_chunks(png)]
    assert "sRGB" in _icc_name(png), _icc_name(png)


@needs_std_srgb
def test_635_control_a_profile_imagemagick_does_not_recognise(tmp_path):
    from PIL import ImageCms
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    jpg = _red_jpeg(_folder(tmp_path, "ctl") / "DSC_0001 (2).jpg", icc)
    jxl = _jxl_of(tmp_path, jpg)
    rc, out = _tra(tmp_path, jxl.parent, "--force-convert", "--decode", "--format", "jpeg",
                   "--mode", "1", "--resize-long", "80")
    assert rc == 0, out
    with Image.open(_only(jxl.parent, "*.jpg")) as im:
        r, g, b = im.convert("RGB").getpixel((40, 30))
    assert abs(r - 200) <= 4 and abs(g - 30) <= 6 and abs(b - 30) <= 6, (r, g, b)


@needs_std_srgb
@pytest.mark.parametrize("name", ["jxl_jpeg_transcoder", "jxl_recompressor"])
def test_635_png_iccp_reads_the_profile_djxl_wrote(tmp_path, name):
    m = _script_module(name)
    icc = _STD_SRGB.read_bytes()
    jpg = _red_jpeg(tmp_path / "a.jpg", icc)
    subprocess.run(["cjxl", "a.jpg", "a.jxl"], check=True, capture_output=True, cwd=str(tmp_path))
    subprocess.run(["djxl", "a.jxl", "a.png"], check=True, capture_output=True, cwd=str(tmp_path))
    hostile = _folder(tmp_path, "iccp") / "a 5%.png"
    shutil.move(str(tmp_path / "a.png"), str(hostile))
    assert m._png_iccp(hostile) == icc
    assert m._png_iccp(jpg) is None


# ------------------------------------------------------------------- --exiftool-ignore-minor
def _exif_with_unparseable_makernotes():
    """Make = Nokia and 64 bytes exiftool does not recognise as maker notes:
    it reads the file ("[minor] Unrecognized MakerNotes") and, once they are
    in a TIFF, refuses every write to it ("Maker notes could not be parsed") —
    what a real Nokia N73 JPEG of the owner's does."""
    make = b"Nokia\x00"
    note = bytes(range(1, 65))
    ifd0_off = 8
    exif_off = ifd0_off + 2 + 2 * 12 + 4
    make_off = exif_off + 2 + 12 + 4
    note_off = make_off + len(make)
    ifd0 = (struct.pack("<H", 2)
            + struct.pack("<HHII", 0x010F, 2, len(make), make_off)
            + struct.pack("<HHII", 0x8769, 4, 1, exif_off)
            + struct.pack("<I", 0))
    exif = (struct.pack("<H", 1) + struct.pack("<HHII", 0x927C, 7, len(note), note_off)
            + struct.pack("<I", 0))
    return b"II*\x00" + struct.pack("<I", ifd0_off) + ifd0 + exif + make + note


def _nokia_archive(tmp_path, name):
    jpg = _jpeg_with_exif(_folder(tmp_path, name) / "DSC_0001 (2).jpg",
                          _exif_with_unparseable_makernotes())
    return _jxl_of(tmp_path, jpg)


def _dec(tmp_path, *args):
    return _run(tmp_path, "jxl_tiff_decoder.py", *args)


@needs_tools
def test_unparseable_makernotes_are_an_error_with_a_hint_by_default(tmp_path):
    jxl = _nokia_archive(tmp_path, "mn")
    rc, out = _dec(tmp_path, jxl.parent, "--mode", "1")
    flat = " ".join(out.split())
    assert rc == 1, out
    assert "exiftool calls minor" in flat and "--exiftool-ignore-minor decodes them" in flat, out
    assert "-> DSC_0001 (2).tif" in flat, out
    assert "without checking it" in flat, out
    assert jxl.exists() and not list(jxl.parent.rglob("*.tif")), out


@needs_tools
def test_exiftool_ignore_minor_decodes_the_file_and_says_so(tmp_path):
    jxl = _nokia_archive(tmp_path, "mn_on")
    rc, out = _dec(tmp_path, jxl.parent, "--mode", "1", "--exiftool-ignore-minor")
    flat = " ".join(out.split())
    assert rc == 0, out
    assert "exiftool runs with -m" in flat, out
    assert "METADATA WRITTEN UNCHECKED (-m) | DSC_0001 (2).tif" in flat, out
    tif = _only(jxl.parent, "*.tif")
    assert _tags(tif, "IFD0:Make").get("IFD0:Make") == "Nokia"
    rel = _tags(tif, "XMP-dc:Relation").get("XMP-dc:Relation")
    rel = rel if isinstance(rel, list) else [rel]
    assert any(str(v).startswith("jxlphoto-src:") for v in rel), rel
    assert "exiftool calls minor" not in flat, out


@needs_tools
def test_exiftool_ignore_minor_is_refused_with_a_delete(tmp_path):
    jxl = _nokia_archive(tmp_path, "mn_del")
    rc, out = _dec(tmp_path, jxl.parent, "--mode", "1", "--exiftool-ignore-minor",
                   "--delete-source", "--delete-confirm-off")
    assert rc == 2, out
    assert "nobody checked, so the combination is refused" in " ".join(out.split()), out
    assert jxl.exists() and not list(jxl.parent.rglob("*.tif"))


@needs_tools
def test_control_a_normal_archive_gets_no_minor_hint(tmp_path):
    jpg = _jpeg(_folder(tmp_path, "mn_ctl") / "DSC_0001 (2).jpg")
    _camera_exif_only(jpg)
    jxl = _jxl_of(tmp_path, jpg)
    rc, out = _dec(tmp_path, jxl.parent, "--mode", "1")
    assert rc == 0, out
    assert "exiftool-ignore-minor" not in out and "UNCHECKED" not in out, out
    assert _only(jxl.parent, "*.tif").stat().st_size > 0


# ------------------------------------------------ review 261110: the guards
@pytest.mark.parametrize("module", BACKENDS)
def test_review_minus_m_reaches_exiftool_only_from_the_decoder_with_the_setting(
        tmp_path, module, monkeypatch):
    """`-m` is written into the argfile by _run_exiftool_argfile (four
    identical copies) only when the script's own EXIFTOOL_IGNORE_MINOR is on,
    and only the decoder defines that setting."""
    m = _script_module(module)
    seen = []

    def fake_run(cmd, timeout, *a, **k):
        argfile = Path(cmd[cmd.index("-@") + 1])
        seen.append(argfile.read_text(encoding="utf-8").splitlines())

        class R:
            returncode, stdout, stderr = 0, "", ""
        return R()

    monkeypatch.setattr(m, "_run_captured", fake_run)
    m._run_exiftool_argfile(["-ver"])
    assert "-m" not in seen[-1], seen[-1]
    if module == "jxl_tiff_decoder":
        assert m.EXIFTOOL_IGNORE_MINOR is False
        monkeypatch.setattr(m, "EXIFTOOL_IGNORE_MINOR", True)
        m._run_exiftool_argfile(["-ver"])
        assert "-m" in seen[-1], seen[-1]
        assert "EXIFTOOL_IGNORE_MINOR" not in m._OVERLAY_SETTINGS
    else:
        assert not hasattr(m, "EXIFTOOL_IGNORE_MINOR")


@needs_tools
@pytest.mark.parametrize("fmt", ["jpeg", "png"])
def test_review_571_a_copy_that_really_fails_stays_an_error(tmp_path, fmt, monkeypatch):
    """#631's retry is ONE retry, for a JPEG only, after the codec's own
    EXIF/XMP was removed; a metadata copy that fails again is the file's
    error and leaves no output behind (#571)."""
    jpg = _jpeg(_folder(tmp_path, "fail_" + fmt) / "DSC_0001 (2).jpg")
    _camera_exif_only(jpg)
    jxl = _jxl_of(tmp_path, jpg)
    m = _script_module("jxl_jpeg_transcoder")
    m.setup_logger()
    calls = []

    def failing_copy(src, dst):
        calls.append(Path(dst).name)
        raise RuntimeError("metadata copy failed (exiftool rc=1): simulated")

    monkeypatch.setattr(m, "_copy_metadata", failing_copy)
    out = jxl.with_suffix(".png" if fmt == "png" else ".jpg")
    res = m.decode_to_image(jxl, out, out, 95, fmt, 8, None, False, True, False)
    assert res[1] == "error", res
    assert "simulated" in res[2], res
    assert len(calls) == (2 if fmt == "jpeg" else 1), calls
    assert not out.exists()
    assert not [p for p in jxl.parent.iterdir() if p.suffix in (".tmp", ".jpg", ".png")]
