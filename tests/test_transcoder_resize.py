#!/usr/bin/env python3
"""Resize/sharpening derivatives in the transcoder (JXL -> JPEG/PNG).

Unit-level refusals run without codecs; the pixel/profile/marker assertions
run the real djxl -> magick pipeline against a synthetic ProPhoto master.
"""

import base64
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_recompressor as rec

REPO = Path(__file__).resolve().parent.parent

_HAVE_TOOLS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool", "magick"))
real = pytest.mark.skipif(
    not _HAVE_TOOLS, reason="needs cjxl, djxl, exiftool and magick on PATH")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _saturated_png(path: Path, w: int, h: int):
    import numpy as np
    import imagecodecs
    x = np.linspace(0, 1, w)
    y = np.linspace(0, 1, h)
    yy, xx = np.meshgrid(x, y, indexing="xy")
    arr = np.stack([(xx * 65535).astype("uint16"),
                    (yy * 65535).astype("uint16"),
                    ((1 - xx) * (1 - yy) * 65535).astype("uint16")], axis=2)
    path.write_bytes(imagecodecs.png_encode(arr))


def _make_master(tmp_path: Path, w: int = 640, h: int = 480,
                 name: str = "master.jxl") -> Path:
    """A synthetic ProPhoto-like master with the encoder's markers."""
    pp = tmp_path / "pp.icc"
    pp.write_bytes(rec._build_matrix_trc_icc(
        "test-prophoto", [(0.7347, 0.2653), (0.1596, 0.8404), (0.0366, 0.0001)],
        (0.3457, 0.3585), 1.8))
    raw = tmp_path / "raw.png"
    src = tmp_path / "src.png"
    _saturated_png(raw, w, h)
    subprocess.run(["magick", str(raw), "-profile", str(pp), str(src)],
                   check=True, capture_output=True)
    master = tmp_path / name
    subprocess.run(["cjxl", str(src), str(master), "-d", "0.05",
                    "--container=1", "-x", "strip=exif", "-x", "strip=xmp"],
                   check=True, capture_output=True)
    b64 = base64.b64encode(pp.read_bytes()).decode("ascii")
    subprocess.run(["exiftool", "-q", "-overwrite_original",
                    f"-XMP-xmp:CreatorTool=Test | ICC:{b64}",
                    "-XMP-dc:Description=gen=1 | cjxl d=0.05 e=7",
                    "-XMP-dc:Relation+=jxlphoto-src:1111",
                    "-XMP-dc:Relation+=jxlphoto-srcsum:2222",
                    "-XMP-dc:Relation+=jxlphoto-depth:16", str(master)],
                   check=True, capture_output=True)
    return master


def _adobe_icc(tmp_path: Path) -> Path:
    p = tmp_path / "adobe.icc"
    p.write_bytes(rec._adobe_rgb_icc_bytes())
    return p


def _exif_value(path: Path, tag: str):
    r = subprocess.run(["exiftool", "-j", "-s", "-s", tag, str(path)],
                       capture_output=True, text=True, timeout=60)
    entry = json.loads(r.stdout)[0]
    return entry.get(tag.split(":")[-1].lstrip("-"))


def _relation_tokens(path: Path) -> list:
    rel = _exif_value(path, "-XMP-dc:Relation")
    if rel is None:
        return []
    return [str(t).strip() for t in (rel if isinstance(rel, list) else [rel])]


def _run_transcoder(input_path: Path, *extra, timeout=600):
    return subprocess.run([sys.executable, str(REPO / "jxl_jpeg_transcoder.py"),
                           str(input_path), "--workers", "1", *extra],
                          capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL)


# ---------------------------------------------------------------------------
# Refusals (no codecs needed)
# ---------------------------------------------------------------------------

def test_resize_with_delete_source_is_refused(tmp_path):
    (tmp_path / "a.jxl").write_bytes(b"\x00" * 16)
    r = _run_transcoder(tmp_path / "a.jxl", "--decode", "--force-convert",
                        "--resize-long", "2048", "--delete-source")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "DERIVATIVE" in r.stderr, r.stdout + r.stderr


def test_resize_with_force_transcode_is_refused(tmp_path):
    (tmp_path / "a.jxl").write_bytes(b"\x00" * 16)
    r = _run_transcoder(tmp_path / "a.jxl", "--force-transcode", "--decode",
                        "--resize-long", "2048")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "bit-exact" in r.stderr, r.stdout + r.stderr


def test_resize_percent_above_100_without_allow_upscale_is_refused(tmp_path):
    (tmp_path / "a.jxl").write_bytes(b"\x00" * 16)
    r = _run_transcoder(tmp_path / "a.jxl", "--decode", "--force-convert",
                        "--resize-percent", "150")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "upscale" in r.stderr, r.stdout + r.stderr


def test_resize_non_positive_is_refused(tmp_path):
    (tmp_path / "a.jxl").write_bytes(b"\x00" * 16)
    r = _run_transcoder(tmp_path / "a.jxl", "--decode", "--force-convert",
                        "--resize-long", "0")
    assert r.returncode == 2, r.stdout + r.stderr


# ---------------------------------------------------------------------------
# Real codecs
# ---------------------------------------------------------------------------

@real
def test_decode_resize_long_to_jpeg(tmp_path):
    master = _make_master(tmp_path)
    r = _run_transcoder(master, "--decode", "--force-convert", "--mode", "1",
                        "--to-srgb", "--resize-long", "320", "--format", "jpeg")
    assert r.returncode == 0, r.stdout + r.stderr
    out = tmp_path / "recovered_jpeg" / "master.jpg"
    assert out.exists(), r.stdout + r.stderr

    from PIL import Image
    with Image.open(out) as im:
        assert im.size == (320, 240), im.size

    assert _exif_value(out, "-ExifImageWidth") == 320
    assert _exif_value(out, "-ExifImageHeight") == 240
    prof_desc = _exif_value(out, "-ICC_Profile:ProfileDescription")
    assert prof_desc and "srgb" in prof_desc.lower(), prof_desc

    tokens = _relation_tokens(out)
    assert "jxlphoto-derived:sRGB@long320" in tokens, tokens
    assert not any(t.startswith("jxlphoto-src:") for t in tokens), tokens
    assert not any(t.startswith("jxlphoto-srcsum:") for t in tokens), tokens


@real
def test_decode_resize_never_upscales_without_the_flag(tmp_path):
    master = _make_master(tmp_path, w=640, h=480)
    r = _run_transcoder(master, "--decode", "--force-convert", "--mode", "1",
                        "--to-srgb", "--resize-long", "2000", "--format", "jpeg")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "already smaller" in (r.stdout + r.stderr).lower(), r.stdout + r.stderr
    out = tmp_path / "recovered_jpeg" / "master.jpg"
    from PIL import Image
    with Image.open(out) as im:
        assert im.size == (640, 480), im.size


@real
def test_sharpen_keeps_the_converted_profile(tmp_path):
    """Trap B2: `-colorspace Lab ... -colorspace sRGB` drops the ICC profile;
    the converted profile must be re-assigned or the JPEG loses its tag."""
    pytest.importorskip("PIL.ImageCms")
    master = _make_master(tmp_path)
    adobe = _adobe_icc(tmp_path)
    r = _run_transcoder(master, "--decode", "--force-convert", "--mode", "1",
                        "--icc-profile", str(adobe), "--sharpen", "screen",
                        "--format", "jpeg")
    assert r.returncode == 0, r.stdout + r.stderr
    out = tmp_path / "recovered_jpeg" / "master.jpg"
    assert "jxlphoto-derived:icc-" in "\n".join(_relation_tokens(out))

    from PIL import ImageCms
    r2 = subprocess.run(["exiftool", "-b", "-ICC_Profile", str(out)],
                        capture_output=True, timeout=60)
    assert r2.returncode == 0 and r2.stdout, "the sharpened JPEG has no ICC profile"
    got = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(r2.stdout)))
    want = ImageCms.getProfileDescription(
        ImageCms.ImageCmsProfile(io.BytesIO(adobe.read_bytes())))
    assert got == want, (got, want)


@real
def test_auto_routes_a_jbrd_jxl_through_the_resize(tmp_path):
    """With resize/sharpening, a jbrd JXL cannot take the lossless recovery
    (it needs pixels): auto mode must decode and re-encode it."""
    from PIL import Image
    jpeg = tmp_path / "photo.jpg"
    Image.new("RGB", (640, 480), (200, 30, 40)).save(jpeg, quality=90)
    r = subprocess.run(["cjxl", str(jpeg), str(tmp_path / "photo.jxl"), "-d", "0"],
                       capture_output=True)
    assert r.returncode == 0, r.stderr
    import jxl_jpeg_transcoder as tr
    assert tr.has_jbrd_box(tmp_path / "photo.jxl")

    r = _run_transcoder(tmp_path, "--mode", "1", "--resize-long", "320",
                        "--format", "jpeg")
    assert r.returncode == 0, r.stdout + r.stderr
    out = tmp_path / "recovered_jpeg" / "photo.jpg"
    assert out.exists(), r.stdout + r.stderr
    with Image.open(out) as im:
        assert im.size == (320, 240), im.size
    assert "decoded and re-encoded instead" in r.stdout + r.stderr


@real
def test_existing_non_derivative_output_is_refused_and_kept(tmp_path):
    master = _make_master(tmp_path)
    out = tmp_path / "recovered_jpeg" / "master.jpg"
    out.parent.mkdir(parents=True)
    out.write_bytes(b"a user export, not ours")
    before = out.read_bytes()
    r = _run_transcoder(master, "--decode", "--force-convert", "--mode", "1",
                        "--to-srgb", "--resize-long", "320", "--format", "jpeg",
                        "--overwrite")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "REFUSED" in r.stdout + r.stderr, r.stdout + r.stderr
    assert out.read_bytes() == before


@real
def test_recipe_change_re_derives_without_overwrite(tmp_path):
    master = _make_master(tmp_path)
    r = _run_transcoder(master, "--decode", "--force-convert", "--mode", "1",
                        "--to-srgb", "--resize-long", "320", "--format", "jpeg")
    assert r.returncode == 0, r.stdout + r.stderr
    out = tmp_path / "recovered_jpeg" / "master.jpg"
    assert "jxlphoto-derived:sRGB@long320" in _relation_tokens(out)

    # Same command again: SKIP (the recipe is unchanged).
    r = _run_transcoder(master, "--decode", "--force-convert", "--mode", "1",
                        "--to-srgb", "--resize-long", "320", "--format", "jpeg")
    assert "SKIP" in r.stdout, r.stdout

    # A different recipe re-derives even without --overwrite.
    r = _run_transcoder(master, "--decode", "--force-convert", "--mode", "1",
                        "--to-srgb", "--resize-long", "200", "--format", "jpeg")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "re-deriving" in r.stdout + r.stderr
    assert "jxlphoto-derived:sRGB@long200" in _relation_tokens(out)
    from PIL import Image
    with Image.open(out) as im:
        assert im.size == (200, 150), im.size
