"""#550: a '%' in a source file name lost all of its metadata.

exiftool format-expands the file name given to -tagsFromFile and to '<='
('%d', '%f', '%e', with modifiers), so 'photo 5%.tif' made it open a
different, missing file: a warning, exit 0, and the output silently carried
none of the source's EXIF (and, through '<=', none of its XMP). Doubling the
'%' does not escape it. The real-photo battery found it: its hostile file
name ends in '5%'. Every script now copies such a source's metadata through a
container with a plain name. Real codecs and exiftool, inside a folder named
with Japanese, brackets, a space and %.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "吾妻山 [tmp] 100%"
NAME = "DSC_0001 (2) 菜の花 [v2] 5%"
# '5%.jxl' happens to open fine ('%.j' is not expanded, '%.t' is); '%o', '%d',
# '%f', '%e' and their modifiers always are.
NAME2 = "DSC_0002 50%off 菜の花 [v2]"
_TOOLS = ("cjxl", "djxl", "exiftool", "magick")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")


def _run(tmp_path, script, *args):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs")}
    r = subprocess.run([sys.executable, str(REPO / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, env=env)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return r.stdout + r.stderr


def _exif(*args):
    af = Path(os.environ.get("TEMP", ".")) / f"pct_{os.getpid()}.args"
    af.write_text("-charset\nfilename=utf8\n" + "\n".join(map(str, args)) + "\n",
                  encoding="utf-8")
    try:
        return subprocess.run(["exiftool", "-@", str(af)], capture_output=True).stdout
    finally:
        af.unlink()


def _tags(path):
    d = json.loads(_exif("-j", "-EXIF:Software", "-EXIF:ExposureTime", "-EXIF:FNumber",
                         "-XMP-dc:Subject", path))[0]
    return d.get("Software"), d.get("ExposureTime"), d.get("Subject")


def _camera_tiff(path):
    rng = np.random.default_rng(1)
    a = (rng.integers(0, 60000, (120, 160, 3))).astype(np.uint16)
    tifffile.imwrite(path, a, photometric="rgb", software="Capture One 23 Windows",
                     metadata=None)
    _exif("-overwrite_original", "-ExposureTime=1/125", "-FNumber=8",
          "-XMP-dc:Subject=kept", path)


@needs_tools
def test_a_percent_name_keeps_its_metadata_through_every_script(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    src = d / f"{NAME}.tif"
    _camera_tiff(src)
    want = ("Capture One 23 Windows", "1/125", "kept")
    assert _tags(src) == want

    _run(tmp_path, "jxl_tiff_encoder.py", src, d / "jxl", "--distance", "1", "--effort", "3",
         "--no-preflight")
    jxl = d / "jxl" / f"{NAME}.jxl"
    assert _tags(jxl) == want, _tags(jxl)

    _run(tmp_path, "jxl_tiff_decoder.py", d / "jxl", d / "tif")
    assert _tags(d / "tif" / f"{NAME}.tif") == want

    _run(tmp_path, "jxl_recompressor.py", d / "jxl", "--mode", "8", "--distance", "2",
         "--effort", "3", "--no-preflight", "--delete-confirm-off",
         "--on-regeneration", "convert")
    assert _tags(jxl) == want, _tags(jxl)
    assert b"cjxl d=2.0" in _exif("-s3", "-XMP-dc:Description", jxl)


@needs_tools
def test_a_percent_jxl_keeps_its_metadata_when_the_transcoder_decodes_it(tmp_path):
    """The transcoder's JXL -> PNG path: djxl carries the EXIF itself and the
    exiftool copy is a safety net, so this passed before the fix too — it
    guards that the fixed copy does not break the path."""
    d = tmp_path / HOSTILE
    d.mkdir()
    ascii_dir = tmp_path / "ascii"
    ascii_dir.mkdir()
    tif = ascii_dir / "a.tif"
    _camera_tiff(tif)
    _run(tmp_path, "jxl_tiff_encoder.py", tif, ascii_dir, "--distance", "1", "--effort", "3",
         "--no-preflight")
    src = d / f"{NAME}.jxl"
    shutil.copy(ascii_dir / "a.jxl", src)
    assert _tags(src)[1] == "1/125"
    _run(tmp_path, "jxl_jpeg_transcoder.py", src, d / "out", "--decode", "--format", "png")
    out = next((d / "out").glob("*.png"))
    sw, et, subj = _tags(out)
    assert et == "1/125" and subj == "kept", (sw, et, subj)


@needs_tools
def test_a_percent_jxl_keeps_its_metadata_in_the_decoder_and_the_recompressor(tmp_path):
    """A master whose own name has a '%' (written correctly): the decoded TIFF
    and the in-place recompression must keep its metadata."""
    d = tmp_path / HOSTILE
    d.mkdir()
    ascii_dir = tmp_path / "ascii"
    ascii_dir.mkdir()
    _camera_tiff(ascii_dir / "a.tif")
    _run(tmp_path, "jxl_tiff_encoder.py", ascii_dir / "a.tif", ascii_dir, "--distance", "1",
         "--effort", "3", "--no-preflight")
    want = ("Capture One 23 Windows", "1/125", "kept")
    for sub in ("dec", "rec"):
        (d / sub).mkdir()
        shutil.copy(ascii_dir / "a.jxl", d / sub / f"{NAME2}.jxl")
    assert _tags(d / "dec" / f"{NAME2}.jxl") == want

    _run(tmp_path, "jxl_tiff_decoder.py", d / "dec", d / "dec_out")
    assert _tags(d / "dec_out" / f"{NAME2}.tif") == want

    _run(tmp_path, "jxl_recompressor.py", d / "rec", "--mode", "8", "--distance", "2",
         "--effort", "3", "--no-preflight", "--delete-confirm-off",
         "--on-regeneration", "convert")
    jxl = d / "rec" / f"{NAME2}.jxl"
    assert b"cjxl d=2.0" in _exif("-s3", "-XMP-dc:Description", jxl)
    assert _tags(jxl) == want, _tags(jxl)
