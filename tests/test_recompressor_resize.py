#!/usr/bin/env python3
"""Resize/sharpening derivatives in the recompressor (JXL -> JXL).

`--resize-*` and `--sharpen` generalize the `--output-icc` derivative mode:
without `--output-icc` the source colour space is kept, with it the target is
applied first. The real-codec tests check pixels, primaries, CreatorTool and
the `jxlphoto-derived` recipe marker.
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


def _jxl_stub(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)


def _saturated_png(path: Path, w: int = 640, h: int = 480):
    import numpy as np
    import imagecodecs
    x = np.linspace(0, 1, w)
    y = np.linspace(0, 1, h)
    yy, xx = np.meshgrid(x, y, indexing="xy")
    arr = np.stack([(xx * 65535).astype("uint16"),
                    (yy * 65535).astype("uint16"),
                    ((1 - xx) * (1 - yy) * 65535).astype("uint16")], axis=2)
    path.write_bytes(imagecodecs.png_encode(arr))


def _make_master(tmp_path: Path, name: str = "master.jxl") -> Path:
    """A 640x480 synthetic ProPhoto-like master with the encoder's markers."""
    pp = tmp_path / "pp.icc"
    pp.write_bytes(rec._build_matrix_trc_icc(
        "test-prophoto", [(0.7347, 0.2653), (0.1596, 0.8404), (0.0366, 0.0001)],
        (0.3457, 0.3585), 1.8))
    raw = tmp_path / "raw.png"
    src = tmp_path / "src.png"
    _saturated_png(raw)
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


def _run(tmp_path: Path, *extra, expect=0):
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(tmp_path), "--mode", "1", "--distance", "1.0",
                        "--workers", "1", "--no-preflight", *extra],
                       capture_output=True, text=True, timeout=600,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == expect, r.stdout + r.stderr
    return r


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


def _jxlinfo(path: Path) -> str:
    return subprocess.run(["jxlinfo", str(path)], capture_output=True, text=True,
                          timeout=120).stdout


def _size_of(path: Path):
    from PIL import Image
    out = path.parent / "_dec.png"
    subprocess.run(["djxl", str(path), str(out), "--bits_per_sample=8"],
                   check=True, capture_output=True)
    with Image.open(out) as im:
        size = im.size
    out.unlink()
    return size


# ---------------------------------------------------------------------------
# CLI refusals
# ---------------------------------------------------------------------------

def test_resize_mode8_is_refused(tmp_path):
    _jxl_stub(tmp_path / "a.jxl")
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(tmp_path), "--mode", "8", "--resize-long", "2048",
                        "--dry-run"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "--resize" in r.stderr, r.stdout + r.stderr


def test_sharpen_with_delete_source_is_refused(tmp_path):
    _jxl_stub(tmp_path / "a.jxl")
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(tmp_path), "--mode", "1", "--sharpen", "screen",
                        "--delete-source", "--dry-run"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "derivative" in r.stderr, r.stdout + r.stderr


def test_resize_percent_zero_is_refused(tmp_path):
    _jxl_stub(tmp_path / "a.jxl")
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(tmp_path), "--mode", "1", "--resize-percent", "0",
                        "--dry-run"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr


# ---------------------------------------------------------------------------
# Real codecs
# ---------------------------------------------------------------------------

@real
def test_resize_without_output_icc_keeps_the_source_colour_space(tmp_path):
    master = _make_master(tmp_path)
    _run(tmp_path, "--resize-long", "320")
    out = tmp_path / "recompressed_jxl" / "master.jxl"
    assert out.exists()

    info = _jxlinfo(out)
    assert re.search(r"red\(x=0\.73", info), info          # original primaries

    tokens = _relation_tokens(out)
    assert "jxlphoto-derived:keep@long320" in tokens, tokens
    assert not any(t.startswith("jxlphoto-src:") for t in tokens), tokens
    assert not any(t.startswith("jxlphoto-srcsum:") for t in tokens), tokens

    ct = _exif_value(out, "-XMP-xmp:CreatorTool")
    assert rec._xmp_icc_from_creator_tool(ct), ct          # source ICC kept
    assert _size_of(out) == (320, 240)


@real
def test_resize_and_sharpen_with_srgb_target(tmp_path):
    _make_master(tmp_path)
    _run(tmp_path, "--output-icc", "sRGB", "--resize-long", "320",
         "--sharpen", "screen")
    out = tmp_path / "recompressed_jxl" / "master.jxl"
    info = _jxlinfo(out)
    assert "Primaries: sRGB" in info, info
    assert "jxlphoto-derived:sRGB@long320+screen" in _relation_tokens(out)
    assert _size_of(out) == (320, 240)


@real
def test_changing_only_the_resize_re_derives_on_sync(tmp_path):
    _make_master(tmp_path)
    _run(tmp_path, "--resize-long", "320", "--sync")
    out = tmp_path / "recompressed_jxl" / "master.jxl"
    assert "jxlphoto-derived:keep@long320" in _relation_tokens(out)

    r = _run(tmp_path, "--resize-long", "320", "--sync")
    assert "SKIP" in r.stdout, r.stdout

    r = _run(tmp_path, "--resize-long", "200", "--sync")
    assert "recipe changed" in r.stdout + r.stderr, r.stdout + r.stderr
    assert "jxlphoto-derived:keep@long200" in _relation_tokens(out)
    assert _size_of(out) == (200, 150)


@real
def test_existing_non_derivative_output_is_refused(tmp_path):
    _make_master(tmp_path)
    out = tmp_path / "recompressed_jxl" / "master.jxl"
    _jxl_stub(out)
    before = out.read_bytes()
    r = _run(tmp_path, "--resize-long", "320", "--overwrite", expect=1)
    assert "REFUSED" in r.stdout + r.stderr, r.stdout + r.stderr
    assert out.read_bytes() == before
