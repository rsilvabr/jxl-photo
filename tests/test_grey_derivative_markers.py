"""#672 — a grey derivative JPEG carried the master's provenance and no
`jxlphoto-derived`.

The transcoder's ImageMagick route gives a grey image no profile step (grey is
never colour-converted), so ImageMagick kept what it read from djxl's PNG
besides an ICC: the master's EXIF and XMP. A master's XMP is often larger than
a JPEG's 64 KB APP1 segment (film scans: SilverFast's mask list, 109 KB on the
IR page of a real scan); ImageMagick wrote it broken, every later exiftool
write to the JPEG was ignored, and the derivative kept `jxlphoto-src` /
`jxlphoto-srcsum` with no `jxlphoto-derived` — the next run refused it as "not
a derivative of this run". Real codecs, in a folder named with Japanese,
brackets, a space and `100%off %d`.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest
import tifffile

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "吾妻山 [グレー] 100%off %d"
_TOOLS = ("cjxl", "djxl", "exiftool", "magick")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")


def _big_xmp() -> bytes:
    """An XMP packet over 64 KB, like a SilverFast scan's."""
    filler = "mask " * 18000          # ~90 KB of text in a description
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:description><rdf:Alt><rdf:li xml:lang="x-default">{filler}</rdf:li>'
        '</rdf:Alt></dc:description></rdf:Description></rdf:RDF></x:xmpmeta>'
        '<?xpacket end="w"?>').encode("utf-8")


def _run(tmp_path, script, *args):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"),
           "JXLPHOTO_SETTINGS": "", "JXLPHOTO_JXL_THUMB": "0"}
    r = subprocess.run([sys.executable, str(REPO / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, env=env)
    return r.returncode, r.stdout + r.stderr


def _relation(path) -> str:
    fd, af = tempfile.mkstemp(suffix=".args")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"-charset\nfilename=utf8\n-s\n-s\n-s\n-XMP-dc:Relation\n{path}\n")
    try:
        return subprocess.run(["exiftool", "-@", af], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120).stdout
    finally:
        os.unlink(af)


@needs_tools
@pytest.mark.parametrize("fmt,ext", [("jpeg", "jpg"), ("png", "png")])
def test_a_grey_derivative_is_marked_as_one(tmp_path, fmt, ext):
    src = tmp_path / HOSTILE / "scan"
    src.mkdir(parents=True)
    rng = np.random.default_rng(4)
    y, x = np.mgrid[0:900, 0:1200]
    grey = np.clip(x * 50 + y * 20 + rng.integers(-800, 800, x.shape), 0, 65535).astype(np.uint16)
    tifffile.imwrite(src / "赤外 (2).tif", grey, photometric="minisblack",
                     extratags=[(700, "B", len(_big_xmp()), _big_xmp(), True)])
    rc, out = _run(tmp_path, "jxl_tiff_encoder.py", src, "--mode", "1", "--distance", "1",
                   "--effort", "1", "--no-preflight")
    assert rc == 0, out[-3000:]
    master = src / "converted_jxl" / "赤外 (2).jxl"
    assert "jxlphoto-src:" in _relation(master)

    dst = tmp_path / HOSTILE / "copies"
    args = [src / "converted_jxl", dst, "--mode", "2", "--force-convert", "--decode",
            "--format", fmt, "--icc-profile", "sRGB", "--resize-long", "600",
            "--sharpen", "screen"]
    rc, out = _run(tmp_path, "jxl_jpeg_transcoder.py", *args)
    assert rc == 0, out[-3000:]
    rel = _relation(dst / f"赤外 (2).{ext}")
    assert "jxlphoto-derived:" in rel, rel
    assert "jxlphoto-src:" not in rel and "jxlphoto-srcsum:" not in rel, rel

    # The next run over its own output re-derives it, never refuses it.
    rc, out = _run(tmp_path, "jxl_jpeg_transcoder.py", *args, "--overwrite")
    assert rc == 0 and "REFUSED" not in out, out[-3000:]
