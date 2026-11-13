#!/usr/bin/env python3
"""#654: the lossless JPEG -> JXL transcode accepts only a JXL with a jbrd box.

cjxl fails on a JPEG it cannot rebuild bit for bit (CMYK, arithmetic coding,
4:1:1 sampling with cjxl 0.12.0), so a JXL WITHOUT the box means cjxl read the
file as another format — a PNG named .jpg — and encoded its pixels at its own
default distance. Before the fix that lossy JXL was logged as OK by the path
that promises a reversible archive (a bare codestream failed the integrity
check by chance; a PNG with metadata, which cjxl puts in a container, passed),
and the provenance/History write into it swallowed its failure at DEBUG.

Real cjxl, djxl and exiftool, in a folder with Japanese, brackets, a space
and a `%`.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

HOSTILE = "250118_吾妻山公園 [FINAL] 100%off %d"

real = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool")),
    reason="needs cjxl, djxl and exiftool on PATH")


def _png_with_metadata(path: Path):
    """A PNG with EXIF and XMP: cjxl writes it as a CONTAINER (Exif + xml
    boxes), which is what passed the integrity check before the fix. Made
    beside the hostile folder (exiftool reads its command line in the ANSI
    code page) and copied in."""
    from PIL import Image
    arr = (np.random.default_rng(7).random((96, 128, 3)) * 255).astype("uint8")
    plain = path.parent.parent / f"plain_{path.parent.parent.name[:8]}.png"
    Image.fromarray(arr).save(plain)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-XMP-dc:Title=caption",
                    "-EXIF:Artist=someone", str(plain)], check=True, capture_output=True)
    shutil.copyfile(plain, path)


def _jpeg(path: Path):
    from PIL import Image
    arr = (np.random.default_rng(3).random((96, 128, 3)) * 255).astype("uint8")
    Image.fromarray(arr).save(path, quality=90)


def _run(*args):
    return subprocess.run([sys.executable, str(REPO / "jxl_jpeg_transcoder.py")]
                          + [str(a) for a in args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300,
                          stdin=subprocess.DEVNULL)


@real
def test_a_png_named_jpg_is_an_error_not_a_lossy_archive(tmp_path):
    folder = tmp_path / HOSTILE
    folder.mkdir()
    fake = folder / "DSC_0001 (2).jpg"
    _png_with_metadata(fake)
    real_jpeg = folder / "DSC_0002 (2).jpg"
    _jpeg(real_jpeg)
    before = fake.read_bytes()

    r = _run(folder, "--mode", "1", "--delete-source", "--delete-confirm-off")
    out = r.stdout + r.stderr
    made = folder / "converted_jxl"
    # The real JPEG is archived losslessly as before (and, proven, deleted).
    assert (made / "DSC_0002 (2).jxl").exists(), out
    # The PNG named .jpg: an error for the file, no JXL left behind, the
    # source untouched.
    assert not (made / "DSC_0001 (2).jxl").exists(), out
    assert not list(made.glob("*DSC_0001*")), sorted(p.name for p in made.iterdir())
    assert fake.exists() and fake.read_bytes() == before
    assert "ERROR | DSC_0001 (2).jpg | cjxl did not transcode it losslessly" in out, out
    assert "1 errors" in out, out
    # Its checksums.md5 line was never filed.
    md5 = (made / "checksums.md5").read_text(encoding="utf-8")
    assert "DSC_0001" not in md5, md5
    assert "Traceback" not in out, out


@real
def test_a_rerun_after_the_error_still_refuses(tmp_path):
    """Fed again (the source is still there), the run refuses again: nothing
    was left that a skip-existing run could take for a finished archive."""
    folder = tmp_path / HOSTILE
    folder.mkdir()
    fake = folder / "a b [c].jpg"
    _png_with_metadata(fake)
    for _ in range(2):
        r = _run(folder, "--mode", "1")
        out = r.stdout + r.stderr
        assert "cjxl did not transcode it losslessly" in out, out
        assert "SKIP" not in out, out
        assert not list((folder / "converted_jxl").glob("*.jxl")), out
