#!/usr/bin/env python3
"""The xmpMM:History entry (plan 261013, topic `xmp-history`).

Every output the toolkit writes gains ONE entry in xmpMM:History naming the
toolkit version, the time and the conversion. U1-U4 are unit tests; H1-H10 run
the real cjxl/djxl/exiftool/magick, because the entry is written through
exiftool structures and read back by it — a mocked exiftool would prove
nothing about the escaping or the serialization.
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
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent
# getattr, not enc.TOOLKIT_VERSION: against the pre-fix code the proof run must
# show every test FAILING, not the whole module erroring at collection.
VERSION = getattr(enc, "TOOLKIT_VERSION", None)

_HAVE_TOOLS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool", "magick"))
real = pytest.mark.skipif(not _HAVE_TOOLS,
                          reason="needs cjxl, djxl, exiftool and magick on PATH")

BACKENDS = [(enc, "jxl_tiff_encoder"), (dec, "jxl_tiff_decoder"),
            (tr, "jxl_jpeg_transcoder"), (rec, "jxl_recompressor")]


def _history(path):
    r = subprocess.run(["exiftool", "-struct", "-j", "-XMP-xmpMM:History", str(path)],
                       capture_output=True, text=True, check=True)
    h = json.loads(r.stdout)[0].get("History")
    return [] if h is None else (h if isinstance(h, list) else [h])


def _ours(entry, action, params):
    assert entry["Action"] == action
    assert entry["SoftwareAgent"] == f"jxl-photo {VERSION} (https://github.com/rsilvabr/jxl-photo)"
    assert entry["Parameters"] == params
    assert entry["When"][:4].isdigit()


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


def _jbrd_jxl(tmp_path: Path) -> Path:
    d = tmp_path / "in"
    d.mkdir()
    jpg = tmp_path / "photo.jpg"
    _jpeg_with_xmp(jpg)
    subprocess.run(["cjxl", str(jpg), str(d / "photo.jxl"), "--lossless_jpeg=1"],
                   check=True, capture_output=True)
    return d


def _encode_master(tmp_path: Path) -> Path:
    """A clean uint16 TIFF encoded once (encoder History = 1 entry)."""
    src = tmp_path / "src"
    src.mkdir()
    _rgb_tiff(src / "p.tif", np.uint16)
    _run("jxl_tiff_encoder.py", src / "p.tif", "--mode", "0", "--delete-confirm-off")
    return src / "p.jxl"


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mod,stem", BACKENDS, ids=[b[1] for b in BACKENDS])
def test_u1_converted_names_both_types(mod, stem):
    lines = mod._history_args("converted", "a.tif", "x_b.jxl.tmp")
    assert isinstance(lines, list) and len(lines) == 1, lines
    line = lines[0]
    assert line.startswith("-XMP-xmpMM:History+={Action=converted,When="), line
    assert (f"SoftwareAgent=jxl-photo {mod.TOOLKIT_VERSION} "
            f"(https://github.com/rsilvabr/jxl-photo)") in line, line
    assert line.endswith(f"Parameters={stem}: from image/tiff to image/jxl}}"), line


@pytest.mark.parametrize("mod,stem", BACKENDS, ids=[b[1] for b in BACKENDS])
def test_u2_unknown_mime_drops_the_types(mod, stem):
    line = mod._history_args("converted", "a.raw", "b.jxl")[0]
    assert ": from" not in line, line
    assert line.endswith(f"Parameters={stem}}}"), line


@pytest.mark.parametrize("mod,stem", BACKENDS, ids=[b[1] for b in BACKENDS])
def test_u3_a_structure_value_is_escaped(mod, stem, monkeypatch):
    monkeypatch.setattr(mod, "TOOLKIT_URL", "a,b{c}|d")
    line = mod._history_args("converted", "a.tif", "b.jxl")[0]
    assert "(a|,b|{c|}||d)" in line, line


@pytest.mark.parametrize("mod,stem", BACKENDS, ids=[b[1] for b in BACKENDS])
def test_u4_history_mime(mod, stem):
    assert mod._history_mime("x.TIF") == "image/tiff"
    assert mod._history_mime("u_x.jpeg.tmp") == "image/jpeg"
    assert mod._history_mime("x.tmp") is None
    assert mod._history_mime("x") is None


# ---------------------------------------------------------------------------
# Real codecs
# ---------------------------------------------------------------------------

@real
def test_h1_encoder_clean_tiff_gets_one_entry(tmp_path):
    u16 = tmp_path / "u16.tif"
    _rgb_tiff(u16, np.uint16)
    _run("jxl_tiff_encoder.py", u16, "--mode", "0", "--delete-confirm-off")
    h = _history(tmp_path / "u16.jxl")
    assert len(h) == 1, h
    _ours(h[0], "converted", "jxl_tiff_encoder: from image/tiff to image/jxl")


@real
def test_h2_encoder_keeps_the_existing_history(tmp_path):
    tif = tmp_path / "cap.tif"
    _rgb_tiff(tif, np.uint16)
    _set_meta(tif, "-XMP-xmpMM:History={Action=saved,"
                   "When=2026:10:01 10:00:00,SoftwareAgent=Capture One 16}")
    _run("jxl_tiff_encoder.py", tif, "--mode", "0", "--delete-confirm-off")
    h = _history(tmp_path / "cap.jxl")
    assert len(h) == 2, h
    assert h[0]["Action"] == "saved" and h[0]["SoftwareAgent"] == "Capture One 16"
    _ours(h[1], "converted", "jxl_tiff_encoder: from image/tiff to image/jxl")


@real
def test_h3_strip_writes_no_history(tmp_path):
    tif = tmp_path / "s.tif"
    _rgb_tiff(tif, np.uint16)
    _run("jxl_tiff_encoder.py", tif, "--mode", "0", "--strip", "--delete-confirm-off")
    assert _history(tmp_path / "s.jxl") == []


@real
def test_h4_decoder_appends_and_the_encoder_appends_again(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _rgb_tiff(src / "p.tif", np.uint16)
    _run("jxl_tiff_encoder.py", src / "p.tif", "--mode", "0", "--delete-confirm-off")
    _run("jxl_tiff_decoder.py", src / "p.jxl", "--mode", "1",
         "--overwrite", "--delete-confirm-off")
    tif = src / "converted_tiff" / "p.tif"
    h = _history(tif)
    assert len(h) == 2, h
    _ours(h[0], "converted", "jxl_tiff_encoder: from image/tiff to image/jxl")
    _ours(h[1], "converted", "jxl_tiff_decoder: from image/jxl to image/tiff")

    other = tmp_path / "other"
    other.mkdir()
    shutil.copy(str(tif), str(other / "p.tif"))
    _run("jxl_tiff_encoder.py", other / "p.tif", "--mode", "0", "--delete-confirm-off")
    h = _history(other / "p.jxl")
    assert len(h) == 3, h
    _ours(h[2], "converted", "jxl_tiff_encoder: from image/tiff to image/jxl")


@real
def test_h5_recompressor_reencode_appends_converted(tmp_path):
    jxl = _encode_master(tmp_path)
    in_dir = jxl.parent
    _run("jxl_recompressor.py", in_dir, "--mode", "1", "--distance", "1.0",
         "--no-preflight")
    out = in_dir / "recompressed_jxl" / "p.jxl"
    h = _history(out)
    assert len(h) == 2, h
    _ours(h[0], "converted", "jxl_tiff_encoder: from image/tiff to image/jxl")
    _ours(h[1], "converted", "jxl_recompressor: from image/jxl to image/jxl")


@real
def test_h6_recompressor_derivative_appends_derived(tmp_path):
    jxl = _encode_master(tmp_path)
    in_dir = jxl.parent
    _run("jxl_recompressor.py", in_dir, "--mode", "1", "--distance", "1.0",
         "--output-icc", "sRGB", "--no-preflight")
    out = in_dir / "recompressed_jxl" / "p.jxl"
    h = _history(out)
    assert len(h) == 2, h
    _ours(h[0], "converted", "jxl_tiff_encoder: from image/tiff to image/jxl")
    assert h[1]["Action"] == "derived", h[1]
    assert h[1]["SoftwareAgent"] == (
        f"jxl-photo {VERSION} (https://github.com/rsilvabr/jxl-photo)")


@real
def test_h7_jbrd_copy_keeps_the_bytes_and_gains_nothing(tmp_path):
    in_dir = _jbrd_jxl(tmp_path)
    before = tr.md5_of_file(in_dir / "photo.jxl")
    _run("jxl_recompressor.py", in_dir, "--mode", "1", "--distance", "1.0",
         "--no-preflight")
    out = in_dir / "recompressed_jxl" / "photo.jxl"
    assert tr.md5_of_file(out) == before
    assert _history(out) == []


@real
def test_h8_transcoder_encode_appends_converted(tmp_path):
    jpg = tmp_path / "photo.jpg"
    _jpeg_with_xmp(jpg)
    _run("jxl_jpeg_transcoder.py", jpg, "--force-convert", "--distance", "1")
    h = _history(tmp_path / "photo.jxl")
    assert len(h) == 1, h
    _ours(h[0], "converted", "jxl_jpeg_transcoder: from image/jpeg to image/jxl")

    png = tmp_path / "a.png"
    _random_png(png, seed=8)
    _run("jxl_jpeg_transcoder.py", png, "--force-convert", "--distance", "1")
    h = _history(tmp_path / "a.jxl")
    assert len(h) == 1, h
    _ours(h[0], "converted", "jxl_jpeg_transcoder: from image/png to image/jxl")


@real
def test_h9_transcoder_jbrd_gains_nothing_and_still_reconstructs(tmp_path):
    jpg = tmp_path / "photo.jpg"
    _jpeg_with_xmp(jpg)
    original = tr.md5_of_file(jpg)
    _run("jxl_jpeg_transcoder.py", tmp_path, "--mode", "1")
    jxl = tmp_path / "converted_jxl" / "photo.jxl"
    assert _history(jxl) == []
    assert tr._jxl_reconstruct_md5(jxl) == original


@real
def test_h10_transcoder_decode_converted_and_derived(tmp_path):
    jxl = _encode_master(tmp_path)
    _run("jxl_jpeg_transcoder.py", jxl, "--force-convert", "--format", "jpeg")
    h = _history(jxl.with_suffix(".jpg"))
    assert len(h) == 2, h
    _ours(h[0], "converted", "jxl_tiff_encoder: from image/tiff to image/jxl")
    _ours(h[1], "converted", "jxl_jpeg_transcoder: from image/jxl to image/jpeg")

    _run("jxl_jpeg_transcoder.py", jxl, "--force-convert", "--format", "jpeg",
         "--resize-long", "32", "--mode", "1")
    h = _history(jxl.parent / "recovered_jpeg" / "p.jpg")
    assert len(h) == 2, h
    assert h[1]["Action"] == "derived", h[1]
    assert h[1]["Parameters"] == (
        "jxl_jpeg_transcoder: from image/jxl to image/jpeg")


_REC_IN_PLACE = ["--distance", "2", "--no-preflight", "--no-keep-smaller",
                 "--on-unknown", "convert", "--on-regeneration", "convert",
                 "--on-downgrade", "convert", "--delete-confirm-off"]


@real
def test_h5b_recompressor_in_place_names_both_types(tmp_path):
    """In place the temp is '<uuid>_<stem>.tmp' — no extension left — so the
    entry must take its type from the FINAL name (review 261013, should-fix 3)."""
    jxl = _encode_master(tmp_path)
    _run("jxl_recompressor.py", jxl.parent, "--mode", "8", *_REC_IN_PLACE)
    h = _history(jxl)
    assert len(h) == 2, h
    _ours(h[1], "converted", "jxl_recompressor: from image/jxl to image/jxl")


@real
def test_h11_recompressor_never_doubles_a_list_it_appends_to(tmp_path):
    """#531: cjxl carried the source's XMP into its output and the restamp then
    ADDED the copied lists to the carried ones wherever the same exiftool call
    appended (`+=`): a third-party JXL's dc:Relation came back doubled next to
    the new jxlphoto-origin (v2.10.0-v2.12.0), and the History entry would
    double the History. The recompressor now encodes with -x strip=xmp."""
    d = tmp_path / "in"
    d.mkdir()
    png = tmp_path / "t.png"
    _random_png(png, seed=11)
    subprocess.run(["cjxl", str(png), str(d / "t.jxl"), "-d", "1", "--container=1"],
                   check=True, capture_output=True)
    _set_meta(d / "t.jxl", "-XMP-dc:Relation=urn:example:album-7",
              "-XMP-xmpMM:History={Action=saved,SoftwareAgent=Capture One 16}")
    _run("jxl_recompressor.py", d, "--mode", "1", *_REC_IN_PLACE)
    out = d / "recompressed_jxl" / "t.jxl"
    r = subprocess.run(["exiftool", "-struct", "-j", "-XMP-dc:Relation", str(out)],
                       capture_output=True, text=True, check=True)
    rel = json.loads(r.stdout)[0].get("Relation")
    rel = rel if isinstance(rel, list) else [rel]
    assert rel.count("urn:example:album-7") == 1, rel
    assert "jxlphoto-origin:jxl-lossy" in rel, rel
    h = _history(out)
    assert len(h) == 2, h
    assert h[0]["SoftwareAgent"] == "Capture One 16", h
    _ours(h[1], "converted", "jxl_recompressor: from image/jxl to image/jxl")
