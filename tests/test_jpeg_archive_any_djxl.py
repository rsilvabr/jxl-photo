"""A lossless JPEG archive is rebuilt bit for bit by every djxl, not only the
one that wrote it (#669).

libjxl 0.13 marks the archive (jbrd) of a 4:4:4 or gray JPEG for LF smoothing
by default: a viewer smooths the 8x8 block averages a little when it shows the
JXL. djxl 0.12 and 0.11 apply that smoothing when they REBUILD the JPEG too
(libjxl #4931, fixed in 0.13) and give back other bytes — 15 of the 76 JPEG
fixtures, one not at all. With --jpeg_reconstruction_lfs=0 every djxl from
0.11 on rebuilds the archive bit for bit, at the same size; the transcoder
passes it with cjxl 0.13 or newer (the flag does not exist before).

The real-codec tests need cjxl 0.13+. They tell the two kinds of archive apart
by how djxl SHOWS them (an image with smooth areas, where the smoothing
changes pixels), so no other flag of the toolkit's command line matters. Set
JXLPHOTO_OLD_DJXL to an older djxl (0.11/0.12) to also rebuild the archives
with it. Every file sits in a folder with Japanese, [ ], a space and `%`.
"""
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import jxl_jpeg_transcoder as tr  # noqa: E402

HOSTILE = "250118_吾妻山公園 [FINAL] 100%off %d"
_TOOLS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))


@pytest.mark.parametrize("version,flags", [
    ((0, 13, 0), ["--jpeg_reconstruction_lfs=0"]),
    ((0, 14, 2), ["--jpeg_reconstruction_lfs=0"]),
    ((0, 12, 0), []),
    ((0, 11, 2), []),
    (None, []),                       # an unreadable version: no unknown flag
])
def test_the_flag_follows_the_cjxl_version(monkeypatch, version, flags):
    monkeypatch.setattr(tr, "_tool_version", lambda exe: version)
    assert tr._jpeg_archive_flags() == flags


def _cjxl_013():
    v = tr._common._tool_version("cjxl") if shutil.which("cjxl") else None
    return v is not None and v[:2] >= (0, 13)


def _md5(p):
    return hashlib.md5(Path(p).read_bytes()).hexdigest()


def _smooth_jpeg(path, kind):
    """A JPEG with smooth gradients (where LF smoothing shows) and a little
    noise, saved 4:4:4 (or gray) at a quality that leaves block steps."""
    h, w = 160, 224
    y, x = np.mgrid[0:h, 0:w]
    base = (40 + 150 * x / w + 30 * np.sin(y / 23.0)).astype(np.float64)
    noise = np.random.default_rng(5).normal(0, 1.5, (h, w))
    g = np.clip(base + noise, 0, 255).astype(np.uint8)
    if kind == "gray":
        Image.fromarray(g, "L").save(path, quality=70)
    else:
        rgb = np.stack([g, np.clip(g * 0.8 + 30, 0, 255), 255 - g], axis=-1).astype(np.uint8)
        Image.fromarray(rgb).save(path, quality=70, subsampling=0)


def _shown(jxl, png):
    subprocess.run(["djxl", str(jxl), str(png)], check=True, capture_output=True)
    with Image.open(png) as im:
        return np.asarray(im).copy()


@pytest.mark.skipif(not _TOOLS, reason="needs cjxl, djxl and exiftool")
@pytest.mark.parametrize("kind", ["444", "gray"])
@pytest.mark.parametrize("route", [["--force-transcode"], ["--force-convert", "--distance", "0"]],
                         ids=["transcode", "convert_d0"])
def test_the_archive_is_the_one_every_djxl_rebuilds(tmp_path, kind, route):
    if not _cjxl_013():
        pytest.skip("cjxl older than 0.13 has no LF smoothing to turn off")
    d = tmp_path / HOSTILE
    d.mkdir()
    jpg = d / f"DSC 0001 ({kind}).jpg"
    _smooth_jpeg(jpg, kind)
    # cjxl's own two archives: how djxl shows each.
    shown = {}
    for name, extra in (("lfs0", ["--jpeg_reconstruction_lfs=0"]), ("default", [])):
        ref = tmp_path / f"ref_{name}.jxl"
        subprocess.run(["cjxl", str(jpg), str(ref), "--lossless_jpeg=1"] + extra,
                       check=True, capture_output=True)
        shown[name] = _shown(ref, tmp_path / f"ref_{name}.png")
    assert not np.array_equal(shown["lfs0"], shown["default"]), (
        "the fixture does not tell the two archives apart")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", JXLPHOTO_SETTINGS="",
               JXLPHOTO_LOG_DIR=str(tmp_path / "logs"), JXLPHOTO_JXL_THUMB="0")
    r = subprocess.run([sys.executable, str(REPO / "jxl_jpeg_transcoder.py"), str(jpg)] + route,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env, stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]
    archive = jpg.with_suffix(".jxl")
    assert tr.has_jbrd_box(archive)
    assert np.array_equal(_shown(archive, tmp_path / "archive.png"), shown["lfs0"]), (
        "the toolkit's archive is shown with LF smoothing: djxl 0.12 would rebuild "
        "another JPEG")
    for djxl in filter(None, ["djxl", os.environ.get("JXLPHOTO_OLD_DJXL")]):
        rec = tmp_path / "rebuilt.jpg"
        subprocess.run([djxl, str(archive), str(rec)], check=True, capture_output=True)
        assert _md5(rec) == _md5(jpg), djxl
        rec.unlink()
