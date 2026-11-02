#!/usr/bin/env python3
"""#562: a JPEG -> JXL file shows IrfanView no EXIF.

cjxl Brotli-compresses the Exif and XMP boxes it copies from a JPEG (brob
boxes) unless given --compress_boxes=0, and IrfanView reads the EXIF only
from a PLAIN Exif box placed before the image data. The owner checked three
copies of one transcoded JPEG on 2026-11-03: the transcoder's (brob) showed
only the file name, cjxl's own with plain boxes but the Exif after the first
codestream box showed nothing, and the transcoder's with its boxes
decompressed in place showed everything.

The transcoder now writes plain boxes, and --repair-jbrd rewrites the
metadata of existing archives: the plain boxes come from cjxl run on the
rebuilt JPEG, the codestream and the jbrd box keep their bytes, and the copy
must rebuild the very same JPEG before it replaces the file.

Real codecs and exiftool; every file under a folder named with Japanese,
brackets, a space and %. Pre-fix proof: every test fails against v2.19.1
(git show v2.19.1:jxl_jpeg_transcoder.py).
"""
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

import jxl_jpeg_transcoder as tra

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "吾妻山 [jpeg exif] 100%"
_TOOLS = ("cjxl", "djxl", "exiftool")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")


# --------------------------------------------------------------------- helpers
def _run(tmp_path, *args, ok=True):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"),
           "JXLPHOTO_JXL_THUMB": "0"}
    r = subprocess.run([sys.executable, str(REPO / "jxl_jpeg_transcoder.py"),
                        *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=900, env=env,
                       stdin=subprocess.DEVNULL)
    if ok:
        assert r.returncode == 0, (r.stdout + r.stderr)[-6000:]
    return " ".join((r.stdout + r.stderr).split())


def _boxes(path):
    """[(type, inner type of a brob or None, whole box bytes)] — an
    independent reader."""
    data, out, pos = Path(path).read_bytes(), [], 0
    while pos + 8 <= len(data):
        size, typ = struct.unpack(">I4s", data[pos:pos + 8])
        hdr = 8
        if size == 1:
            size, hdr = struct.unpack(">Q", data[pos + 8:pos + 16])[0], 16
        elif size == 0:
            size = len(data) - pos
        box = data[pos:pos + size]
        out.append((typ, box[hdr:hdr + 4] if typ == b"brob" else None, box))
        pos += size
    return out


def _irfanview_ok(path):
    """Plain Exif before the XMP and before the first codestream box, no
    compressed metadata at all."""
    boxes = _boxes(path)
    assert not [i for t, i, _ in boxes if t == b"brob" and i in (b"Exif", b"xml ")], \
        [(t, i) for t, i, _ in boxes]
    order = [t for t, _i, _b in boxes if t in (b"Exif", b"xml ", b"jxlc", b"jxlp")]
    assert b"Exif" in order, order
    first = order.index(b"Exif")
    return not set(order[:first]) & {b"xml ", b"jxlc", b"jxlp"}


def _image_and_jbrd(path):
    boxes = _boxes(path)
    return ([b for t, _i, b in boxes if t in (b"jxlc", b"jxlp")],
            [b for t, _i, b in boxes if t == b"jbrd"])


def _model(path):
    r = tra._run_exiftool_argfile(["-j", "-Model", str(path)])
    return json.loads(r.stdout)[0].get("Model")


def _jpeg(folder, name="DSC_0002 (3).jpg"):
    """A JPEG with camera EXIF and an XMP packet, like a Capture One export."""
    from PIL import Image
    folder.mkdir(parents=True, exist_ok=True)
    jpg = folder / name
    Image.fromarray((np.random.default_rng(5).random((64, 48, 3)) * 255)
                    .astype("uint8")).save(jpg, quality=90)
    r = tra._run_exiftool_argfile(["-overwrite_original", "-Make=NIKON CORPORATION",
                                   "-Model=NIKON Z f", "-ISO=200",
                                   "-XMP-xmp:CreatorTool=Capture One Windows", str(jpg)])
    assert r.returncode == 0, r.stderr
    return jpg


def _old_archive(jpg, jxl):
    """What the transcoder wrote up to v2.19.1: cjxl's default (Exif/XMP in
    brob boxes), then reorder_jxl_boxes."""
    jxl.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["cjxl", str(jpg), str(jxl), "--lossless_jpeg=1", "--effort", "7"],
                       capture_output=True)
    assert r.returncode == 0, r.stderr
    tra.reorder_jxl_boxes(jxl)
    assert [i for t, i, _ in _boxes(jxl) if t == b"brob"], "cjxl compresses by default"
    return jxl


# ------------------------------------------------------------------ real tests
@needs_tools
def test_transcoder_writes_plain_exif_where_irfanview_reads_it(tmp_path):
    jpg = _jpeg(tmp_path / HOSTILE)
    original = tra.md5_of_file(jpg)
    _run(tmp_path, jpg.parent, "--mode", "1")
    jxl = jpg.parent / "converted_jxl" / "DSC_0002 (3).jxl"
    assert _irfanview_ok(jxl), [(t, i) for t, i, _ in _boxes(jxl)]
    assert tra._irfanview_layout_problem(jxl) is None
    assert tra._jxl_reconstruct_md5(jxl) == original, "JPEG still rebuilt bit for bit"
    assert _model(jxl) == "NIKON Z f"


@needs_tools
def test_compress_metadata_keeps_brob_and_the_repair_leaves_it(tmp_path):
    """--compress-metadata (COMPRESS_METADATA_BOXES = True): the user picks
    the smaller file over IrfanView; --repair-jbrd honours that choice."""
    jpg = _jpeg(tmp_path / HOSTILE)
    original = tra.md5_of_file(jpg)
    _run(tmp_path, jpg.parent, "--mode", "1", "--compress-metadata")
    jxl = jpg.parent / "converted_jxl" / "DSC_0002 (3).jxl"
    assert {i for t, i, _ in _boxes(jxl) if t == b"brob"} == {b"Exif", b"xml "}
    assert tra._jxl_reconstruct_md5(jxl) == original
    data = jxl.read_bytes()
    log = _run(tmp_path, jxl.parent, "--repair-jbrd", "--compress-metadata")
    assert "EXIF MADE READABLE" not in log and "] OK |" in log, log[-3000:]
    assert jxl.read_bytes() == data


@needs_tools
def test_repair_jbrd_makes_an_old_archive_readable_and_proves_it(tmp_path):
    folder = tmp_path / HOSTILE
    jpg = _jpeg(folder / "src")
    original = tra.md5_of_file(jpg)
    jxl = _old_archive(jpg, folder / "250118_菜の花 [FINAL]" / "DSC_0002 (3).jxl")
    old_md5 = tra.md5_of_file(jxl)
    tra.store_md5_db(jxl, original)
    tra.store_jxl_self_hash_db(jxl, old_md5)
    image, jbrd = _image_and_jbrd(jxl)
    old = 1_600_000_000
    os.utime(jxl, (old, old))
    assert tra._irfanview_layout_problem(jxl) == "compressed EXIF/XMP"

    # Dry run: reported, nothing written.
    data = jxl.read_bytes()
    log = _run(tmp_path, jxl.parent, "--repair-jbrd", "--dry-run")
    assert "WOULD MAKE EXIF READABLE (compressed EXIF/XMP)" in log, log[-3000:]
    assert jxl.read_bytes() == data
    assert not list(jxl.parent.glob("*.tmp"))

    log = _run(tmp_path, jxl.parent, "--repair-jbrd")
    assert "EXIF MADE READABLE (compressed EXIF/XMP; checksums.md5 updated)" in log, log[-3000:]
    assert _irfanview_ok(jxl), [(t, i) for t, i, _ in _boxes(jxl)]
    assert _image_and_jbrd(jxl) == (image, jbrd), "codestream and jbrd keep their bytes"
    assert tra._jxl_reconstruct_md5(jxl) == original
    assert int(jxl.stat().st_mtime) == old, "the modified date is kept"
    assert tra.read_md5_db(jxl) == original, "the JPEG's line still holds"
    assert tra.read_jxl_self_hash_db(jxl) == tra.md5_of_file(jxl) != old_md5
    assert tra._jxl_binds_to_archived_jpeg(jxl, jpg, original), \
        "the decode-side gates still recognise the archive"
    assert _model(jxl) == "NIKON Z f"
    assert not list(jxl.parent.glob("*.tmp"))

    # Again over its own output: nothing to do, the file is untouched.
    data = jxl.read_bytes()
    log = _run(tmp_path, jxl.parent, "--repair-jbrd")
    assert "EXIF MADE READABLE" not in log and "] OK |" in log, log[-3000:]
    assert jxl.read_bytes() == data


@needs_tools
def test_repair_jbrd_moves_a_plain_exif_out_of_the_image_data(tmp_path):
    """Plain boxes, but the Exif after the first codestream box (what cjxl
    writes on its own): IrfanView shows nothing at all."""
    folder = tmp_path / HOSTILE
    jpg = _jpeg(folder / "src")
    original = tra.md5_of_file(jpg)
    jxl = folder / "out" / "DSC_0002 (3).jxl"
    jxl.parent.mkdir(parents=True)
    r = subprocess.run(["cjxl", str(jpg), str(jxl), "--lossless_jpeg=1",
                        "--compress_boxes=0"], capture_output=True)
    assert r.returncode == 0, r.stderr
    assert tra._irfanview_layout_problem(jxl) == "EXIF after the image data"
    image, jbrd = _image_and_jbrd(jxl)

    log = _run(tmp_path, jxl.parent, "--repair-jbrd")
    assert "EXIF MADE READABLE (EXIF after the image data)" in log, log[-3000:]
    assert _irfanview_ok(jxl)
    assert _image_and_jbrd(jxl) == (image, jbrd)
    assert tra._jxl_reconstruct_md5(jxl) == original
    assert not (jxl.parent / tra.CHECKSUMS_FILENAME).exists(), \
        "no checksums.md5 lines are invented for a file that had none"


@needs_tools
def test_relayout_that_cannot_be_proven_leaves_the_file_untouched(tmp_path, monkeypatch):
    folder = tmp_path / HOSTILE
    jpg = _jpeg(folder / "src")
    jxl = _old_archive(jpg, folder / "out" / "DSC_0002 (3).jxl")
    data = jxl.read_bytes()
    real = tra._jxl_reconstruct_md5
    # The rewritten copy rebuilds "another" JPEG: the swap must not happen.
    monkeypatch.setattr(tra, "_jxl_reconstruct_md5",
                        lambda p: "0" * 32 if Path(p).suffix == ".tmp" else real(p))
    state, detail = tra._relayout_one_jbrd(jxl, False, "compressed EXIF/XMP")
    assert state == "failed" and "does not rebuild the same JPEG" in detail, detail
    assert jxl.read_bytes() == data
    assert not list(jxl.parent.glob("*.tmp"))


# ------------------------------------------------------------------ unit tests
def _container(tmp_path, *boxes):
    p = tmp_path / "伊豆沼 [u] 5%.jxl"
    sig = struct.pack(">I4s", 12, b"JXL ") + b"\r\n\x87\n"
    body = b"".join(struct.pack(">I4s", 8 + len(pl), t) + pl for t, pl in boxes)
    p.write_bytes(sig + body)
    return p


FTYP = (b"ftyp", b"jxl \0\0\0\0jxl ")
CS = (b"jxlp", b"\0\0\0\0\xff\x0a" + b"c" * 30)


@pytest.mark.parametrize("boxes, problem", [
    ([FTYP, (b"Exif", b"\0" * 8), (b"xml ", b"<x/>"), (b"jbrd", b"j"), CS], None),
    ([FTYP, (b"brob", b"Exif" + b"z" * 9), (b"jbrd", b"j"), (b"xml ", b"<x/>"), CS],
     "compressed EXIF/XMP"),
    ([FTYP, (b"Exif", b"\0" * 8), (b"brob", b"xml " + b"z" * 9), CS],
     "compressed EXIF/XMP"),
    ([FTYP, CS, (b"jbrd", b"j"), (b"Exif", b"\0" * 8), (b"xml ", b"<x/>")],
     "EXIF after the image data"),
    ([FTYP, (b"xml ", b"<x/>"), (b"Exif", b"\0" * 8), CS], "EXIF after the XMP"),
    ([FTYP, (b"jbrd", b"j"), CS], None),
], ids=["good", "brob-exif", "brob-xml", "after-image", "after-xmp", "no-exif"])
def test_irfanview_layout_problem(tmp_path, boxes, problem):
    assert tra._irfanview_layout_problem(_container(tmp_path, *boxes)) == problem


def test_irfanview_layout_problem_on_what_is_not_a_container(tmp_path):
    bare = tmp_path / "bare [x].jxl"
    bare.write_bytes(b"\xff\x0a" + b"c" * 40)
    assert tra._irfanview_layout_problem(bare) is None
    trunc = _container(tmp_path, FTYP, (b"brob", b"Exif" + b"z" * 9))
    trunc.write_bytes(trunc.read_bytes()[:-3])
    assert tra._irfanview_layout_problem(trunc) is None
    assert tra._parse_jxl_boxes(trunc.read_bytes()) is None
