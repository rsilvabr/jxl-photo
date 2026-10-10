#!/usr/bin/env python3
"""The jxlphoto-origin marker (plan 261010, topic `origin-marker`).

`jxlphoto-origin:<value>` records what the FIRST master of a photo was made
from. U1-U5 are unit tests; U4 and the 16-bit half of U5 need real tools.
R1-R8 run the real cjxl/djxl/exiftool/magick: the marker is provenance that
exiftool has to read and write, so a mocked exiftool would prove nothing.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_jpeg_transcoder as tr
import jxl_recompressor as rec
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent

_HAVE_TOOLS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool", "magick"))
real = pytest.mark.skipif(not _HAVE_TOOLS,
                          reason="needs cjxl, djxl, exiftool and magick on PATH")


def _relation(path):
    r = subprocess.run(["exiftool", "-j", "-XMP-dc:Relation", str(path)],
                       capture_output=True, text=True, check=True)
    rel = json.loads(r.stdout)[0].get("Relation")
    return [] if rel is None else [str(v) for v in (rel if isinstance(rel, list) else [rel])]


def _origins(path):
    return [t for t in _relation(path) if t.startswith("jxlphoto-origin:")]


def _run(script, *args):
    r = subprocess.run([sys.executable, str(REPO / script)] + [str(a) for a in args],
                       capture_output=True, text=True, timeout=300,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, f"{script} failed:\n{r.stdout}{r.stderr}"
    return r


def _rgb_tiff(path: Path, dtype):
    import tifffile
    top = 255 if dtype == np.uint8 else 65535
    data = (np.random.default_rng(4).random((40, 60, 3)) * top).astype(dtype)
    tifffile.imwrite(str(path), data, photometric="rgb")


def _jpeg_with_xmp(path: Path):
    from PIL import Image
    arr = (np.random.default_rng(1).random((96, 128, 3)) * 255).astype("uint8")
    Image.fromarray(arr).save(path, quality=90)
    subprocess.run(["exiftool", "-q", "-overwrite_original",
                    "-XMP-dc:Description=caption", "-XMP-xmp:Rating=4",
                    "-Make=NIKON", str(path)], check=True, capture_output=True)


def _random_png(path: Path, seed: int):
    from PIL import Image
    Image.fromarray((np.random.default_rng(seed).random((64, 64, 3)) * 255)
                    .astype("uint8")).save(path)


def _set_meta(path: Path, *args):
    subprocess.run(["exiftool", "-q", "-overwrite_original", *map(str, args), str(path)],
                   check=True, capture_output=True)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

_ORIGIN_CASES = [
    (["x", "jxlphoto-origin:jpeg"], False, "tiff16", ("jpeg", True)),
    (["jxlphoto-origin:jpeg"], True, "jxl", ("jpeg", True)),
    (["jxlphoto-src:abc"], False, "tiff16", (None, False)),
    ([], True, "tiff16", (None, False)),
    ([], False, "tiff16", ("tiff16", False)),
    (["user tag"], False, "jxl", ("jxl", False)),
    (None, False, "tiff16", (None, False)),
    # The plan's U1 table expected ("jpeg", False) here, but the §2 helper (and
    # rule 3 of the plan) treat ANY jxlphoto-* token as "touched by an older
    # toolkit version", so the conservative never-guess answer is unknown.
    # See the report, "Decisions and deviations".
    (["jxlphoto-origin:"], False, "jpeg", (None, False)),
]


@pytest.mark.parametrize("mod", [enc, tr, rec],
                         ids=["encoder", "transcoder", "recompressor"])
@pytest.mark.parametrize("tokens,has_record,fresh,expected", _ORIGIN_CASES)
def test_u1_origin_for_output(mod, tokens, has_record, fresh, expected):
    assert mod._origin_for_output(tokens, has_record, fresh) == expected


def test_u2_constants_agree_across_the_three_scripts():
    mods = (enc, tr, rec)
    assert len({m.ORIGIN_PREFIX for m in mods}) == 1
    assert len({m._TOOLKIT_MARKER_NAMESPACE for m in mods}) == 1
    assert len({m._ORIGIN_RECORD_RE.pattern for m in mods}) == 1


@pytest.mark.parametrize("text", [
    "gen=1 | cjxl d=0.1 e=7",
    "cjxl d=1 e=7",
    "Capture One | gen=2 | cjxl d=3 e=9",
])
def test_u3_origin_record_regex_matches_an_encode_record(text):
    assert enc._ORIGIN_RECORD_RE.search(text)


@pytest.mark.parametrize("text", [
    "my caption",
    "Project gen=3 phase 2",
])
def test_u3_origin_record_regex_ignores_plain_text(text):
    assert not enc._ORIGIN_RECORD_RE.search(text)


@real
def test_u4_read_existing_relation_drops_the_origin(tmp_path):
    xmp = tmp_path / "side.xmp"
    _set_meta(xmp, "-XMP-dc:Relation=user",
              "-XMP-dc:Relation+=jxlphoto-origin:tiff16")
    assert enc.read_existing_relation(xmp) == ["user"]


def test_u5_png_bit_depth_8_and_non_png(tmp_path):
    from PIL import Image
    p8 = tmp_path / "a.png"
    Image.fromarray((np.random.default_rng(2).random((16, 16, 3)) * 255)
                    .astype("uint8")).save(p8)
    assert tr._png_bit_depth(p8) == 8
    other = tmp_path / "not.png"
    other.write_bytes(b"not a png at all, definitely not 25 bytes")
    assert tr._png_bit_depth(other) is None


@real
def test_u5_png_bit_depth_16(tmp_path):
    import tifffile
    tif = tmp_path / "a16.tif"
    tifffile.imwrite(str(tif),
                     (np.random.default_rng(3).random((16, 16, 3)) * 65535)
                     .astype("uint16"), photometric="rgb")
    p16 = tmp_path / "a16.png"
    subprocess.run(["magick", str(tif), "-depth", "16", str(p16)],
                   check=True, capture_output=True)
    assert tr._png_bit_depth(p16) == 16


# ---------------------------------------------------------------------------
# R1/R2 - the encoder writes or inherits the origin
# ---------------------------------------------------------------------------

@real
def test_r1_encoder_clean_tiff_gets_its_depth(tmp_path):
    u16 = tmp_path / "u16.tif"
    _rgb_tiff(u16, np.uint16)
    _run("jxl_tiff_encoder.py", u16, "--mode", "0", "--delete-confirm-off")
    assert _origins(tmp_path / "u16.jxl") == ["jxlphoto-origin:tiff16"]

    d8 = tmp_path / "eight"
    d8.mkdir()
    u8 = d8 / "u8.tif"
    _rgb_tiff(u8, np.uint8)
    _run("jxl_tiff_encoder.py", u8, "--mode", "0", "--delete-confirm-off")
    assert _origins(d8 / "u8.jxl") == ["jxlphoto-origin:tiff8"]


@real
def test_r2a_encoder_inherits_an_existing_origin(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    tif = d / "p.tif"
    _rgb_tiff(tif, np.uint16)
    _set_meta(tif, "-XMP-dc:Relation+=jxlphoto-origin:jpeg")
    _run("jxl_tiff_encoder.py", tif, "--mode", "0", "--delete-confirm-off")
    assert _origins(d / "p.jxl") == ["jxlphoto-origin:jpeg"]


@real
def test_r2b_encoder_does_not_guess_for_a_toolkit_touched_tiff(tmp_path):
    d = tmp_path / "b"
    d.mkdir()
    tif = d / "p.tif"
    _rgb_tiff(tif, np.uint16)
    _set_meta(tif, "-XMP-dc:Relation+=jxlphoto-src:0123456789abcdef")
    _run("jxl_tiff_encoder.py", tif, "--mode", "0", "--delete-confirm-off")
    assert _origins(d / "p.jxl") == []


@real
def test_r2c_encoder_does_not_guess_for_a_lineage_record(tmp_path):
    d = tmp_path / "c"
    d.mkdir()
    tif = d / "p.tif"
    _rgb_tiff(tif, np.uint16)
    _set_meta(tif, "-XMP-dc:Description=gen=1 | cjxl d=0.1 e=7")
    _run("jxl_tiff_encoder.py", tif, "--mode", "0", "--delete-confirm-off")
    assert _origins(d / "p.jxl") == []


@real
def test_r2d_encoder_keeps_user_relation_values(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    tif = d / "p.tif"
    _rgb_tiff(tif, np.uint16)
    _set_meta(tif, "-XMP-dc:Relation+=my-tag")
    _run("jxl_tiff_encoder.py", tif, "--mode", "0", "--delete-confirm-off")
    jxl = d / "p.jxl"
    assert _origins(jxl) == ["jxlphoto-origin:tiff16"]
    assert "my-tag" in _relation(jxl)


# ---------------------------------------------------------------------------
# R3 - the decoder carries the origin into the TIFF and back
# ---------------------------------------------------------------------------

@real
def test_r3_decoder_carries_the_origin_forward(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _rgb_tiff(src / "p.tif", np.uint16)
    _run("jxl_tiff_encoder.py", src / "p.tif", "--mode", "0", "--delete-confirm-off")
    _run("jxl_tiff_decoder.py", src / "p.jxl", "--mode", "1",
         "--overwrite", "--delete-confirm-off")
    tif = src / "converted_tiff" / "p.tif"
    assert _origins(tif) == ["jxlphoto-origin:tiff16"]

    other = tmp_path / "other"
    other.mkdir()
    shutil.copy(str(tif), str(other / "p.tif"))
    _run("jxl_tiff_encoder.py", other / "p.tif", "--mode", "0", "--delete-confirm-off")
    assert _origins(other / "p.jxl") == ["jxlphoto-origin:tiff16"]


# ---------------------------------------------------------------------------
# R4/R5/R6 - the recompressor
# ---------------------------------------------------------------------------

@real
def test_r4a_foreign_jxl_gets_the_jxl_origin(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    png = tmp_path / "rand.png"
    _random_png(png, seed=6)
    subprocess.run(["cjxl", str(png), str(in_dir / "foreign.jxl"),
                    "-d", "0.1", "--container=1"], check=True, capture_output=True)
    _run("jxl_recompressor.py", in_dir, "--mode", "1", "--distance", "1.0",
         "--no-preflight")
    out = in_dir / "recompressed_jxl" / "foreign.jxl"
    assert _origins(out) == ["jxlphoto-origin:jxl"]


@real
def test_r4b_recompressing_our_master_keeps_tiff16(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    _rgb_tiff(in_dir / "p.tif", np.uint16)
    _run("jxl_tiff_encoder.py", in_dir / "p.tif", "--mode", "0", "--delete-confirm-off")
    _run("jxl_recompressor.py", in_dir, "--mode", "1", "--distance", "1.0",
         "--no-preflight")
    out = in_dir / "recompressed_jxl" / "p.jxl"
    assert _origins(out) == ["jxlphoto-origin:tiff16"]


@real
def test_r4c_a_lineage_record_without_markers_stays_unknown(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    png = tmp_path / "rand.png"
    _random_png(png, seed=7)
    src = in_dir / "a.jxl"
    subprocess.run(["cjxl", str(png), str(src), "-d", "0.1", "--container=1"],
                   check=True, capture_output=True)
    _set_meta(src, "-XMP-dc:Description=gen=1 | cjxl d=0.1 e=7")
    _run("jxl_recompressor.py", in_dir, "--mode", "1", "--distance", "1.0",
         "--no-preflight")
    out = in_dir / "recompressed_jxl" / "a.jxl"
    assert _origins(out) == []


@real
def test_r5_derivative_keeps_the_origin(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    _rgb_tiff(in_dir / "p.tif", np.uint16)
    _run("jxl_tiff_encoder.py", in_dir / "p.tif", "--mode", "0", "--delete-confirm-off")
    _run("jxl_recompressor.py", in_dir, "--mode", "1", "--distance", "1.0",
         "--output-icc", "sRGB", "--no-preflight")
    out = in_dir / "recompressed_jxl" / "p.jxl"
    assert _origins(out) == ["jxlphoto-origin:tiff16"]
    tokens = _relation(out)
    assert any(t.startswith("jxlphoto-derived:") for t in tokens), tokens
    assert not any(t.startswith("jxlphoto-src:") for t in tokens), tokens


def _jbrd_jxl(tmp_path: Path) -> Path:
    d = tmp_path / "in"
    d.mkdir()
    jpg = tmp_path / "photo.jpg"
    _jpeg_with_xmp(jpg)
    subprocess.run(["cjxl", str(jpg), str(d / "photo.jxl"), "--lossless_jpeg=1"],
                   check=True, capture_output=True)
    return d


@real
def test_r6a_converted_jbrd_gets_the_jpeg_origin(tmp_path):
    in_dir = _jbrd_jxl(tmp_path)
    # --no-keep-smaller: the plan's §4b says a KEEP_SMALLER verbatim copy never
    # gains an origin, and on this small synthetic JPEG the d=1.0 re-encode is
    # not smaller, so without the flag the run would test the copy path.
    _run("jxl_recompressor.py", in_dir, "--mode", "1", "--distance", "1.0",
         "--jbrd-policy", "convert", "--no-keep-smaller", "--no-preflight")
    out = in_dir / "recompressed_jxl" / "photo.jxl"
    assert _origins(out) == ["jxlphoto-origin:jpeg"]


@real
def test_r6b_verbatim_copy_gets_no_origin(tmp_path):
    in_dir = _jbrd_jxl(tmp_path)
    _run("jxl_recompressor.py", in_dir, "--mode", "1", "--distance", "1.0",
         "--no-preflight")
    out = in_dir / "recompressed_jxl" / "photo.jxl"
    assert _origins(out) == []


# ---------------------------------------------------------------------------
# R7/R8 - the transcoder
# ---------------------------------------------------------------------------

@real
def test_r7a_jpeg_pixel_reencode_gets_the_jpeg_origin(tmp_path):
    jpg = tmp_path / "photo.jpg"
    _jpeg_with_xmp(jpg)
    _run("jxl_jpeg_transcoder.py", jpg, "--force-convert", "--distance", "1")
    assert _origins(tmp_path / "photo.jxl") == ["jxlphoto-origin:jpeg"]


@real
def test_r7b_png8_pixel_reencode_gets_png8(tmp_path):
    png = tmp_path / "a.png"
    _random_png(png, seed=8)
    _run("jxl_jpeg_transcoder.py", png, "--force-convert", "--distance", "1")
    assert _origins(tmp_path / "a.jxl") == ["jxlphoto-origin:png8"]


@real
def test_r7c_png16_pixel_reencode_gets_png16(tmp_path):
    import tifffile
    tif = tmp_path / "a16.tif"
    tifffile.imwrite(str(tif),
                     (np.random.default_rng(9).random((32, 32, 3)) * 65535)
                     .astype("uint16"), photometric="rgb")
    png = tmp_path / "a16.png"
    subprocess.run(["magick", str(tif), "-depth", "16", str(png)],
                   check=True, capture_output=True)
    assert tr._png_bit_depth(png) == 16
    _run("jxl_jpeg_transcoder.py", png, "--force-convert", "--distance", "1")
    assert _origins(tmp_path / "a16.jxl") == ["jxlphoto-origin:png16"]


@real
def test_r7d_lossless_jbrd_gets_no_origin_and_still_reconstructs(tmp_path):
    jpg = tmp_path / "photo.jpg"
    _jpeg_with_xmp(jpg)
    original = tr.md5_of_file(jpg)
    _run("jxl_jpeg_transcoder.py", tmp_path, "--mode", "1")
    jxl = tmp_path / "converted_jxl" / "photo.jxl"
    assert _origins(jxl) == []
    assert tr._jxl_reconstruct_md5(jxl) == original


@real
def test_r8_decode_to_jpeg_and_back_keeps_tiff16(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _rgb_tiff(src / "p.tif", np.uint16)
    _run("jxl_tiff_encoder.py", src / "p.tif", "--mode", "0", "--delete-confirm-off")
    _run("jxl_jpeg_transcoder.py", src / "p.jxl", "--force-convert", "--format", "jpeg")
    jpg = src / "p.jpg"
    assert _origins(jpg) == ["jxlphoto-origin:tiff16"]

    back = tmp_path / "back"
    back.mkdir()
    shutil.copy(str(jpg), str(back / "p.jpg"))
    _run("jxl_jpeg_transcoder.py", back / "p.jpg", "--force-convert", "--distance", "1")
    assert _origins(back / "p.jxl") == ["jxlphoto-origin:tiff16"]
