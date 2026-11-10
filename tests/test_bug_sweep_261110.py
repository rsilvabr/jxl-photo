"""The 2026-11-10 bug sweep (#620-#629).

#620 (encoder)  A TIFF with Photoshop layers (tag 37724) or a private tag too
     large to carry was archived flattened and then deleted by
     --delete-source: it is kept now.
#621 (encoder)  10/12-bit (and 2/4-bit) TIFFs were archived unscaled, 16x too
     dark: refused now.
#622 (decoder, transcoder)  A float, deeper-than-16-bit or (decoder) animated
     JXL was deleted after a 16-bit integer / first-frame decode.
#623 (transcoder, decoder, recompressor)  A JXL made from a rotated JPEG came
     out rotated twice: the copied EXIF Orientation is reset to 1.
#624 (encoder, decoder, transcoder)  "MOVE FAILED" out of staging counted as
     success (summary "0 errors", exit 0).
#625-#628 (wrapper, transcoder)  Literals over the recompressor's settings, a
     preset replay with literal effort/quality/distance, the "Script
     settings" text, a checksums.md5 message.

Real codecs; every file inside a folder named with Japanese, brackets, a
space and %.

Pre-fix proof: point the tests at the HEAD scripts
    git show HEAD:<script> > <tmp>/<script>     (all five)
    JXLPHOTO_SCRIPTS_UNDER_TEST=<tmp> python -m pytest tests/test_bug_sweep_261110.py
"""
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import tifffile
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)
# The in-process imports below (children read by the wrapper) come from the
# repository; the SCRIPTS copy only replaces what a test names explicitly.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

HOSTILE = "伊豆沼 [tmp] 100%"
_TOOLS = ("cjxl", "djxl", "exiftool")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")
needs_jxlinfo = pytest.mark.skipif(
    not (all(shutil.which(t) for t in _TOOLS) and shutil.which("jxlinfo")),
    reason="needs cjxl, djxl, exiftool and jxlinfo on PATH")
windows_only = pytest.mark.skipif(os.name != "nt", reason="needs Windows file locking")


# ----------------------------------------------------------------- helpers
def _run(tmp_path, script, *args, timeout=600):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"),
           "JXLPHOTO_SETTINGS": "", "JXLPHOTO_JXL_THUMB": "0",
           "JXLPHOTO_NO_UPDATE_CHECK": "1"}
    r = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, env=env,
                       stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout + r.stderr


def _enc(tmp_path, *args):
    return _run(tmp_path, "jxl_tiff_encoder.py", *args)


def _dec(tmp_path, *args):
    return _run(tmp_path, "jxl_tiff_decoder.py", *args)


def _tra(tmp_path, *args):
    return _run(tmp_path, "jxl_jpeg_transcoder.py", *args)


def _rec(tmp_path, *args):
    return _run(tmp_path, "jxl_recompressor.py", *args)


def _md5(p):
    return hashlib.md5(Path(p).read_bytes()).hexdigest()


def _folder(tmp_path, name="shoot"):
    d = tmp_path / HOSTILE / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tif16(path, seed=1, extratags=None, w=64, h=48):
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    arr = (rng.random((h, w, 3)) * 65535).astype(np.uint16)
    tifffile.imwrite(str(path), arr, photometric="rgb", extratags=extratags or [])
    return path


def _jpeg(path, seed=1, w=160, h=120):
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    Image.fromarray((rng.random((h, w, 3)) * 255).astype(np.uint8)).save(path, quality=90)
    return path


def _plain_jxl(path, seed=3, extra=()):
    """A lossless JXL (container, no toolkit marker) made from an 8-bit PNG."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    png = path.with_suffix(".png")
    Image.fromarray((rng.random((48, 64, 3)) * 255).astype(np.uint8)).save(png)
    subprocess.run(["cjxl", str(png), str(path), "-d", "0", "--container=1", *extra],
                   check=True, capture_output=True)
    png.unlink()
    return path


def _jpeg_corner(path, w=300, h=200):
    """A smooth JPEG with a red block in the top-left corner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.zeros((h, w, 3), np.uint8)
    arr[:, :] = (30, 60, 200)
    arr[:40, :60] = (255, 0, 0)
    Image.fromarray(arr).save(path, quality=95, subsampling=0)
    return path


def _exiftool(args, path):
    """exiftool on `path` run from its folder, by file name only: the library
    folder's `[ ]` would be read as a wildcard in the directory part."""
    path = Path(path)
    return subprocess.run(["exiftool", *args, path.name], cwd=str(path.parent),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def _orient(path, value):
    """Give a JPEG a camera-like EXIF with this Orientation. An EXIF holding
    only the Orientation hits the separate JXL -> PNG failure of D1 of the
    sweep (an exiftool "IFD0 pointer" error, not in this plan), so the block
    carries the usual camera fields too."""
    r = _exiftool(["-overwrite_original", f"-Orientation#={value}", "-Make=Nikon",
                   "-Model=Z 7", "-ISO=100", "-ExposureTime=0.01", "-FNumber=5.6",
                   "-DateTimeOriginal=2026:01:01 10:00:00", "-Software=Lightroom",
                   "-XMP-dc:Title=Test"], path)
    assert r.returncode == 0, r.stdout + r.stderr


def _exif_orientation(path):
    r = _exiftool(["-s3", "-n", "-IFD0:Orientation"], path)
    out = r.stdout.strip()
    return int(out) if out else None


def _size(path):
    with Image.open(path) as im:
        return im.size


def _only(folder, pattern):
    found = sorted(folder.rglob(pattern))
    assert len(found) == 1, (pattern, found)
    return found[0]


def _hold_open_and_run(final, fn):
    """Run `fn` while another reader holds `final` open without
    FILE_SHARE_DELETE (what a viewer or an antivirus does)."""
    with open(final, "rb"):
        return fn()


def _newer(path, seconds=30):
    st = Path(path).stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + seconds * 1_000_000_000))


# ------------------------------------------------------------------- #624
@needs_tools
@windows_only
def test_624_encoder_move_failure_is_an_error(tmp_path):
    d = _folder(tmp_path)
    src = _tif16(d / "DSC_0001 (2).tif")
    staging = tmp_path / "stage"
    rc, out = _enc(tmp_path, d, "--mode", "1", "--distance", "0", "--staging", staging)
    assert rc == 0, out
    final = _only(d, "*.jxl")
    before = _md5(final)
    # EDITED, not just touched: since #638 a newer TIFF with the same bytes is
    # a skip, and this test needs a real re-encode to fail the move.
    _tif16(src, seed=2)
    _newer(src)
    rc, out = _hold_open_and_run(final, lambda: _enc(
        tmp_path, d, "--mode", "1", "--distance", "0", "--sync", "--staging", staging))
    assert "MOVE FAILED" in out, out
    assert rc == 1, out
    assert re.search(r"\b1 errors?\b", out), out
    assert _md5(final) == before
    assert src.exists()


@needs_tools
@windows_only
def test_624_decoder_move_failure_is_an_error(tmp_path):
    d = _folder(tmp_path)
    jxl = _plain_jxl(d / "DSC_0002 (2).jxl")
    staging = tmp_path / "stage"
    rc, out = _dec(tmp_path, d, "--mode", "1", "--staging", staging)
    assert rc == 0, out
    final = _only(d, "*.tif")
    before = _md5(final)
    _newer(jxl)
    rc, out = _hold_open_and_run(final, lambda: _dec(
        tmp_path, d, "--mode", "1", "--sync", "--staging", staging))
    assert "MOVE FAILED" in out, out
    assert rc == 1, out
    assert re.search(r"\b1 errors?\b", out), out
    assert _md5(final) == before
    assert jxl.exists()


@needs_tools
@windows_only
def test_624_transcoder_move_failure_is_an_error_transcode(tmp_path):
    d = _folder(tmp_path)
    src = _jpeg(d / "DSC_0001 (2).jpg")
    staging = tmp_path / "stage"
    rc, out = _tra(tmp_path, d, "--force-transcode", "--mode", "1", "--staging", staging)
    assert rc == 0, out
    final = _only(d, "*.jxl")
    before = _md5(final)
    _newer(src)
    rc, out = _hold_open_and_run(final, lambda: _tra(
        tmp_path, d, "--force-transcode", "--mode", "1", "--sync", "--staging", staging))
    assert "MOVE FAILED" in out, out
    assert rc == 1, out
    assert re.search(r"\b1 errors?\b", out), out
    assert _md5(final) == before
    assert src.exists()


@needs_tools
@windows_only
def test_624_transcoder_move_failure_is_an_error_convert(tmp_path):
    d = _folder(tmp_path)
    src = _jpeg(d / "DSC_0001 (2).jpg")
    staging = tmp_path / "stage"
    args = (d, "--force-convert", "--from-jpeg", "--distance", "1", "--mode", "1",
            "--staging", staging)
    rc, out = _tra(tmp_path, *args)
    assert rc == 0, out
    final = _only(d, "*.jxl")
    before = _md5(final)
    _newer(src)
    rc, out = _hold_open_and_run(final, lambda: _tra(tmp_path, *args, "--sync"))
    assert "MOVE FAILED" in out, out
    assert rc == 1, out
    assert re.search(r"\b1 errors?\b", out), out
    assert _md5(final) == before
    assert src.exists()


# ------------------------------------------------------------------- #620
def _layers_data():
    return b"Adobe Photoshop Document Data Block\x00" + os.urandom(100_000)


def _layered_tif(path):
    data = _layers_data()
    return _tif16(path, extratags=[(37724, 7, len(data), data, True)])


@needs_tools
def test_620_layered_tiff_is_archived_but_never_deleted(tmp_path):
    d = _folder(tmp_path)
    src = _layered_tif(d / "retouch layers (2).tif")
    args = (d, "--mode", "1", "--distance", "0", "--delete-source", "--delete-confirm-off")
    rc, out = _enc(tmp_path, *args)
    assert rc == 0, out
    assert _only(d, "*.jxl").exists()
    assert src.exists(), out
    assert "KEEP source (Photoshop layers" in out, out
    # A second run (skipped archive) with --delete-skipped keeps it too.
    rc, out = _enc(tmp_path, *args, "--delete-skipped")
    assert rc == 0, out
    assert src.exists(), out


@needs_tools
def test_620_control_a_plain_tiff_is_still_deleted(tmp_path):
    d = _folder(tmp_path)
    src = _tif16(d / "plain (2).tif")
    rc, out = _enc(tmp_path, d, "--mode", "1", "--distance", "0",
                   "--delete-source", "--delete-confirm-off")
    assert rc == 0, out
    assert _only(d, "*.jxl").exists()
    assert not src.exists(), out


@needs_tools
def test_620_photoshop_resources_tag_alone_does_not_block(tmp_path):
    """Tag 34377 (paths, resources) is on almost every Photoshop TIFF."""
    d = _folder(tmp_path)
    data = b"8BIM" + os.urandom(200)
    src = _tif16(d / "res (2).tif", extratags=[(34377, 1, len(data), data, True)])
    rc, out = _enc(tmp_path, d, "--mode", "1", "--distance", "0",
                   "--delete-source", "--delete-confirm-off")
    assert rc == 0, out
    assert not src.exists(), out


@needs_tools
def test_620_oversized_private_tag_keeps_the_source(tmp_path):
    d = _folder(tmp_path)
    data = os.urandom(5000)
    src = _tif16(d / "big tag (2).tif", extratags=[(50000, 7, len(data), data, True)])
    rc, out = _enc(tmp_path, d, "--mode", "1", "--distance", "0",
                   "--delete-source", "--delete-confirm-off")
    assert rc == 0, out
    assert _only(d, "*.jxl").exists()
    assert src.exists(), out
    assert "KEEP source" in out, out


@needs_tools
def test_620_dry_run_previews_the_keep(tmp_path):
    d = _folder(tmp_path)
    src = _layered_tif(d / "retouch layers (2).tif")
    # archive first, so the dry run has a skipped archive to judge
    rc, out = _enc(tmp_path, d, "--mode", "1", "--distance", "0")
    assert rc == 0, out
    rc, out = _enc(tmp_path, d, "--mode", "1", "--distance", "0", "--delete-source",
                   "--delete-skipped", "--delete-confirm-off", "--dry-run")
    assert src.exists()
    assert "Photoshop layers" in out, out


# ------------------------------------------------------------------- #621
@needs_tools
def test_621_twelve_bit_tiff_is_refused_and_kept(tmp_path):
    d = _folder(tmp_path)
    arr = np.tile(np.linspace(0, 4095, 64).astype(np.uint16), (48, 1))
    rgb = np.stack([arr, arr, arr], axis=-1)
    src = d / "rgb12 (2).tif"
    tifffile.imwrite(str(src), rgb, photometric="rgb", bitspersample=12)
    rc, out = _enc(tmp_path, d, "--mode", "1", "--distance", "0",
                   "--delete-source", "--delete-confirm-off")
    assert rc == 1, out
    assert "Unsupported TIFF bit depth: 12" in out, out
    assert src.exists()
    assert not list(d.rglob("*.jxl"))


def _held_by_encoder(tif_path):
    """What the encoder's delete gates would keep this TIFF for (#620's
    record), read from the SCRIPTS copy of the encoder."""
    m = _script_module("jxl_tiff_encoder")
    m._unarchived_content_sources.clear()
    with tifffile.TiffFile(str(tif_path)) as tif:
        m._note_unarchivable_content(tif, tif_path)
    return m._unarchived_content_sources.get(os.path.normcase(str(tif_path)))


def test_621_twelve_bit_tiff_is_never_taken_as_archived(tmp_path):
    """Found in the review of the fixes: a master v2.25.2 wrote from a 12-bit
    TIFF holds the values unscaled, and `--sync --delete-source
    --delete-skipped` deleted the TIFF on its strength (reproduced with the
    v2.25.2 encoder writing the master). This version refuses to encode such
    a TIFF, so that master cannot be made here; the check is the record every
    delete gate of the encoder reads before deleting."""
    d = _folder(tmp_path)
    arr = np.tile(np.linspace(0, 4095, 64).astype(np.uint16), (48, 1))
    src = d / "rgb12 (2).tif"
    tifffile.imwrite(str(src), np.stack([arr, arr, arr], axis=-1), photometric="rgb",
                     bitspersample=12)
    held = _held_by_encoder(src)
    assert held and "12" in held and "bit samples" in held, held
    # control: 8- and 16-bit TIFFs are not held
    assert _held_by_encoder(_tif16(d / "rgb16 (2).tif")) is None
    t8 = d / "rgb8 (2).tif"
    tifffile.imwrite(str(t8), np.zeros((48, 64, 3), np.uint8), photometric="rgb")
    assert _held_by_encoder(t8) is None


@needs_tools
def test_621_control_sixteen_bit_passes(tmp_path):
    d = _folder(tmp_path)
    src = _tif16(d / "rgb16 (2).tif")
    rc, out = _enc(tmp_path, d, "--mode", "1", "--distance", "0")
    assert rc == 0, out
    assert _only(d, "*.jxl").exists()
    assert src.exists()


# ------------------------------------------------------------ G3 helpers
_MODS = {}


def _script_module(name):
    """A script loaded from SCRIPTS under a private name (never the
    repository's copy when the pre-fix proof points SCRIPTS elsewhere)."""
    if name not in _MODS:
        sys.path.insert(0, str(SCRIPTS))
        try:
            spec = importlib.util.spec_from_file_location(
                f"bs261110_{name}", str(SCRIPTS / f"{name}.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        finally:
            sys.path.remove(str(SCRIPTS))
        _MODS[name] = mod
    return _MODS[name]


JXLINFO_REAL = """JPEG XL file format container (ISO/IEC 18181-2)
JPEG XL image, 300x200, lossy, 8-bit RGB
Orientation: 6 (90 degrees clockwise)
"""
JXLINFO_ANIM = "JPEG XL animation, 96x64, (possibly) lossless, 8-bit RGB\n"
JXLINFO_FLOAT = ("JPEG XL image, 96x64, (possibly) lossless, "
                 "32-bit float (8 exponent bits) RGB\n")
JXLINFO_16 = "JPEG XL image, 96x64, (possibly) lossless, 16-bit RGB\n"


@pytest.mark.parametrize("name", ["jxl_tiff_decoder", "jxl_jpeg_transcoder",
                                  "jxl_recompressor"])
def test_623_codestream_orientation_parses_jxlinfo(name):
    m = _script_module(name)
    assert m._codestream_orientation(JXLINFO_REAL) == 6
    assert m._codestream_orientation("Orientation: 1 (identity)\n") == 1
    assert m._codestream_orientation(JXLINFO_16) is None
    assert m._codestream_orientation("") is None
    assert m._codestream_orientation(None) is None


@pytest.mark.parametrize("name", ["jxl_tiff_decoder", "jxl_jpeg_transcoder"])
def test_622_reduced_by_integer_decode_reads_jxlinfo(name):
    m = _script_module(name)
    f = m._reduced_by_integer_decode
    assert f(JXLINFO_FLOAT, frames_kept=False)
    assert f(JXLINFO_FLOAT, frames_kept=True)
    assert f(JXLINFO_ANIM, frames_kept=False)
    assert f(JXLINFO_ANIM, frames_kept=True) is None
    assert f(JXLINFO_16, frames_kept=False) is None
    assert f(JXLINFO_REAL, frames_kept=False) is None
    assert f("JPEG XL image, 8x8, lossy, 32-bit RGB\n", frames_kept=False)
    assert f("", frames_kept=False) is None
    assert f(None, frames_kept=False) is None


def _red_at(path_or_array, x, y):
    if isinstance(path_or_array, np.ndarray):
        r, g, b = (int(v) for v in path_or_array[y, x][:3])
        if path_or_array.dtype == np.uint16:
            r, g, b = r >> 8, g >> 8, b >> 8
    else:
        with Image.open(path_or_array) as im:
            r, g, b = im.convert("RGB").getpixel((x, y))
    return r > 180 and g < 90 and b < 90


def _rotated_jbrd(tmp_path, name="rot"):
    """A jbrd JXL of a 300x200 JPEG (red block top-left, Orientation 6),
    moved into a folder of its own; returns (jpeg, jxl)."""
    d = _folder(tmp_path, name)
    jpg = _jpeg_corner(d / "DSC_0001 (2).jpg")
    _orient(jpg, 6)
    rc, out = _tra(tmp_path, d, "--force-transcode", "--mode", "1")
    assert rc == 0, out
    jxl = _only(d, "*.jxl")
    out_dir = _folder(tmp_path, name + "_jxl")
    moved = out_dir / jxl.name
    shutil.move(str(jxl), str(moved))
    return jpg, moved


# ------------------------------------------------------------------- #623
@needs_jxlinfo
@pytest.mark.parametrize("fmt", ["jpeg", "png"])
def test_623_transcoder_decode_of_a_rotated_jpeg_archive_is_upright(tmp_path, fmt):
    _jpg, jxl = _rotated_jbrd(tmp_path)
    d = jxl.parent
    # --jpeg-orientation upright: what v2.25.3 wrote. The default since
    # v2.26.0 (the original orientation) is in test_orientation_261110.py.
    rc, out = _tra(tmp_path, d, "--force-convert", "--decode", "--format", fmt, "--mode", "1",
                   "--jpeg-orientation", "upright")
    assert rc == 0, out
    res = _only(d, "*.png" if fmt == "png" else "*.jpg")
    assert _size(res) == (200, 300), (res, _size(res))
    assert _exif_orientation(res) in (1, None), _exif_orientation(res)
    assert _red_at(res, 190, 10) and not _red_at(res, 5, 10)


@needs_jxlinfo
def test_623_decoder_tiff_of_a_rotated_jpeg_archive_is_upright(tmp_path):
    _jpg, jxl = _rotated_jbrd(tmp_path)
    d = jxl.parent
    rc, out = _dec(tmp_path, d, "--mode", "1")
    assert rc == 0, out
    tif = _only(d, "*.tif")
    arr = tifffile.imread(str(tif))
    assert arr.shape[:2] == (300, 200), arr.shape
    assert _exif_orientation(tif) in (1, None), _exif_orientation(tif)
    assert _red_at(arr, 190, 10) and not _red_at(arr, 5, 10)


@needs_jxlinfo
@pytest.mark.skipif(not shutil.which("magick"), reason="needs ImageMagick")
def test_623_recompressor_derivative_resets_orientation(tmp_path):
    _jpg, jxl = _rotated_jbrd(tmp_path)
    d = jxl.parent
    rc, out = _rec(tmp_path, d, "--mode", "1", "--jbrd-policy", "convert", "--distance", "1",
                   "--resize-long", "150", "--on-unknown", "convert",
                   "--on-regeneration", "convert", "--on-downgrade", "convert")
    assert rc == 0, out
    res = [p for p in d.rglob("*.jxl") if p != jxl]
    assert len(res) == 1, (res, out)
    assert _exif_orientation(res[0]) == 1, (_exif_orientation(res[0]), out)


@needs_jxlinfo
def test_623_recompressor_plain_recompression_resets_orientation(tmp_path):
    d = _folder(tmp_path, "lossy")
    jpg = _jpeg_corner(d / "DSC_0001 (2).jpg")
    _orient(jpg, 6)
    rc, out = _tra(tmp_path, d, "--force-convert", "--from-jpeg", "--distance", "0.5",
                   "--mode", "1")
    assert rc == 0, out
    jxl = _only(d, "*.jxl")
    j = _folder(tmp_path, "lossy_jxl")
    moved = j / jxl.name
    shutil.move(str(jxl), str(moved))
    rc, out = _rec(tmp_path, j, "--mode", "1", "--distance", "3", "--no-keep-smaller",
                   "--on-unknown", "convert", "--on-regeneration", "convert",
                   "--on-downgrade", "convert")
    assert rc == 0, out
    res = [p for p in j.rglob("*.jxl") if p != moved]
    assert len(res) == 1, (res, out)
    assert _exif_orientation(res[0]) == 1, (_exif_orientation(res[0]), out)


# controls that must not change
@needs_jxlinfo
def test_623_control_bit_exact_recovery_keeps_the_jpeg_and_its_tag(tmp_path):
    jpg, jxl = _rotated_jbrd(tmp_path)
    d = jxl.parent
    rc, out = _tra(tmp_path, d, "--force-transcode", "--decode", "--mode", "1")
    assert rc == 0, out
    res = _only(d, "*.jpg")
    assert _md5(res) == _md5(jpg)
    assert _exif_orientation(res) == 6


@needs_jxlinfo
def test_623_control_tiff_chain_keeps_orientation_six(tmp_path):
    d = _folder(tmp_path, "tiffchain")
    tif = _tif16(d / "DSC_0001 (2).tif", w=300, h=200)
    _orient(tif, 6)
    rc, out = _enc(tmp_path, d, "--mode", "1", "--distance", "0")
    assert rc == 0, out
    jxl = _only(d, "*.jxl")
    j = _folder(tmp_path, "tiffchain_jxl")
    shutil.copy(str(jxl), str(j / jxl.name))
    rc, out = _dec(tmp_path, j, "--mode", "1")
    assert rc == 0, out
    back = _only(j, "*.tif")
    assert tifffile.imread(str(back)).shape[:2] == (200, 300)
    assert _exif_orientation(back) == 6
    j2 = _folder(tmp_path, "tiffchain_jxl2")
    shutil.copy(str(jxl), str(j2 / jxl.name))
    rc, out = _tra(tmp_path, j2, "--force-convert", "--decode", "--format", "jpeg",
                   "--mode", "1")
    assert rc == 0, out
    out_jpg = _only(j2, "*.jpg")
    assert _size(out_jpg) == (300, 200)
    assert _exif_orientation(out_jpg) == 6


# ------------------------------------------------------------------- #622
def _pfm(path, w=96, h=64, peak=4.0):
    ramp = np.linspace(0.0, peak, w, dtype="<f4")
    arr = np.tile(ramp, (h, 1))
    rgb = np.stack([arr, arr * 0.5, arr * 0.25], axis=-1).astype("<f4")
    with open(path, "wb") as f:
        f.write(f"PF\n{w} {h}\n-1.0\n".encode("ascii"))
        f.write(np.flipud(rgb).tobytes())
    return path


def _float_jxl(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    pfm = _pfm(path.with_suffix(".pfm"))
    subprocess.run(["cjxl", str(pfm), str(path), "-d", "0"], check=True, capture_output=True)
    pfm.unlink()
    return path


def _anim_jxl(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = [Image.fromarray(np.full((64, 96, 3), v, np.uint8)) for v in (20, 120, 220)]
    apng = path.with_suffix(".png")
    frames[0].save(apng, save_all=True, append_images=frames[1:], duration=100, loop=0)
    subprocess.run(["cjxl", str(apng), str(path), "-d", "0"], check=True, capture_output=True)
    apng.unlink()
    return path


def _master16(tmp_path, folder, name="DSC_0009 (2)"):
    """A normal 16-bit master written by the encoder, beside nothing else."""
    t = _tif16(folder / f"{name}.tif")
    rc, out = _enc(tmp_path, folder, "--mode", "0", "--distance", "0")
    assert rc == 0, out
    t.unlink()
    return folder / f"{name}.jxl"


@needs_jxlinfo
def test_622_decoder_keeps_float_and_animated_jxl(tmp_path):
    d = _folder(tmp_path, "dec622")
    ctl = _master16(tmp_path, d)
    hdr = _float_jxl(d / "hdr (2).jxl")
    anim = _anim_jxl(d / "anim (2).jxl")
    assert ctl.exists(), list(d.iterdir())
    rc, out = _dec(tmp_path, d, "--mode", "0", "--delete-source", "--delete-confirm-off")
    assert hdr.exists(), out
    assert anim.exists(), out
    assert "KEPT" in out, out
    assert not ctl.exists(), out        # control: a normal master is still deleted


@needs_jxlinfo
def test_622_transcoder_png_decode_keeps_float_but_not_animation(tmp_path):
    d = _folder(tmp_path, "tra622")
    ctl = _master16(tmp_path, d)
    hdr = _float_jxl(d / "hdr (2).jxl")
    anim = _anim_jxl(d / "anim (2).jxl")
    assert ctl.exists(), list(d.iterdir())
    rc, out = _tra(tmp_path, d, "--force-convert", "--decode", "--format", "png",
                   "--mode", "0", "--delete-source", "--delete-confirm-off")
    assert hdr.exists(), out
    assert not anim.exists(), out
    with Image.open(d / "anim (2).png") as im:
        assert getattr(im, "n_frames", 1) == 3
    assert not ctl.exists(), out


# ------------------------------------------------------- G4: the wrapper
import jxl_jpeg_transcoder as tra  # noqa: E402
import jxl_recompressor as rec  # noqa: E402
import jxl_tiff_encoder as enc  # noqa: E402

_STATUS = {k: True for k in
           ("cjxl", "djxl", "exiftool", "magick", "tifffile", "pillow", "imagecodecs")}


def _load_wrapper():
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(
            "bs261110_jxl_photo", str(SCRIPTS / "jxl_photo.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(SCRIPTS))
    return mod


@pytest.fixture
def wp():
    return _load_wrapper()


@pytest.fixture
def menu(wp, tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


@pytest.fixture
def launched(wp, monkeypatch):
    calls = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600:
                        (calls.append(list(map(str, cmd))), 0)[1])
    return calls


def _flag(cmd, name):
    return cmd[cmd.index(name) + 1] if name in cmd else None


def _recompress_command(menu):
    wf = dict(menu._new_workflow(), origin_format="jxl", dest_format="jxl",
              conversion_type="jxl_recompress", mode=1, staging="", input_dir="x")
    menu._review_init(wf, _STATUS)
    cmd = menu._review_command(wf)
    assert cmd, wf
    return list(map(str, cmd))


# ------------------------------------------------------------------- #625
def test_625_wizard_follows_the_recompressors_own_policies(menu, monkeypatch):
    monkeypatch.setattr(rec, "ON_DOWNGRADE", "skip")
    monkeypatch.setattr(rec, "ON_REGENERATION", "skip")
    monkeypatch.setattr(rec, "ON_SCAN", "convert")
    cmd = _recompress_command(menu)
    assert _flag(cmd, "--on-downgrade") == "skip", cmd
    assert _flag(cmd, "--on-regeneration") == "skip", cmd
    assert _flag(cmd, "--on-scan") == "convert", cmd


def test_625_control_factory_settings_keep_the_wizard_defaults(menu, monkeypatch):
    """'ask' would be a silent skip on the wizard's pipe (#347): only then does
    the wizard's own default count."""
    monkeypatch.setattr(rec, "ON_DOWNGRADE", "ask")
    monkeypatch.setattr(rec, "ON_REGENERATION", "ask")
    monkeypatch.setattr(rec, "ON_SCAN", "copy")
    cmd = _recompress_command(menu)
    assert _flag(cmd, "--on-downgrade") == "copy", cmd
    assert _flag(cmd, "--on-regeneration") == "convert", cmd
    assert _flag(cmd, "--on-scan") == "copy", cmd


# ------------------------------------------------------------------- #626
def _bare_session(wp, src, origin, dest, conv):
    s = {n: None for n in wp.ToolConfig.__dataclass_fields__ if n.startswith("last_")}
    s.update({"last_input_dir": str(src), "last_origin_format": origin,
              "last_dest_format": dest, "last_conversion_type": conv,
              "last_output_mode": "1"})
    return s


def test_626_preset_without_effort_and_distance_reads_the_encoder(wp, menu, launched,
                                                                  tmp_path, monkeypatch):
    monkeypatch.setattr(enc, "CJXL_EFFORT", 3)
    monkeypatch.setattr(enc, "CJXL_DISTANCE", 0.0)
    src = tmp_path / HOSTILE / "photos"
    src.mkdir(parents=True)
    session = _bare_session(wp, src, "tiff", "jxl", "jxl_tiff_encoder")
    assert menu._run_saved_session(session, _STATUS,
                                   answers={"overwrite": False, "dry_run": True})
    cmd = launched[-1]
    assert _flag(cmd, "--effort") == "3", cmd
    assert float(_flag(cmd, "--distance")) == 0.0, cmd
    assert _flag(cmd, "--workers") == str(menu.config.config.default_workers), cmd


def test_626_preset_without_quality_reads_the_transcoder(wp, menu, launched, tmp_path,
                                                         monkeypatch):
    monkeypatch.setattr(tra, "JPEG_DEFAULT_QUALITY", 80)
    monkeypatch.setattr(tra, "CJXL_EFFORT", 4)
    src = tmp_path / HOSTILE / "photos"
    src.mkdir(parents=True)
    session = _bare_session(wp, src, "jxl", "jpeg", "jxl_to_jpeg_force")
    assert menu._run_saved_session(session, _STATUS,
                                   answers={"overwrite": False, "dry_run": True})
    cmd = launched[-1]
    assert _flag(cmd, "--quality") == "80", cmd
    assert _flag(cmd, "--effort") == "4", cmd
    assert _flag(cmd, "--workers") == str(menu.config.config.default_workers), cmd


def test_626_control_a_saved_value_still_wins(wp, menu, launched, tmp_path, monkeypatch):
    monkeypatch.setattr(enc, "CJXL_EFFORT", 3)
    src = tmp_path / HOSTILE / "photos"
    src.mkdir(parents=True)
    session = _bare_session(wp, src, "tiff", "jxl", "jxl_tiff_encoder")
    session.update(last_effort=9, last_distance=0.5, last_workers=2)
    assert menu._run_saved_session(session, _STATUS,
                                   answers={"overwrite": False, "dry_run": True})
    cmd = launched[-1]
    assert _flag(cmd, "--effort") == "9", cmd
    assert float(_flag(cmd, "--distance")) == 0.5, cmd
    assert _flag(cmd, "--workers") == "2", cmd
