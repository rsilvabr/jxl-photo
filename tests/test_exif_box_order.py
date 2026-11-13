#!/usr/bin/env python3
"""#561: the Exif box comes before the XMP box, or IrfanView shows no EXIF.

reorder_jxl_boxes moves the metadata boxes before the codestream (IrfanView
stops reading there) but kept their relative order. Since v2.17.0 the
encoder writes the source's XMP block in its own exiftool call BEFORE the
EXIF (_xmp_block_preload_args, for the scanners' XMP), so every master from
v2.17.0 to v2.19.0 came out xml -> Exif, and IrfanView showed no EXIF for
them (the owner checked three copies of one master: only the one with the
Exif box moved first showed it). The reorder now puts Exif first in all
three backends, and --refresh-explorer-thumbnails moves it in files already
written, without re-encoding.

Real codecs and exiftool; every file under a folder named with Japanese,
brackets, a space and %. Pre-fix proof: the encoder, recompressor and refresh
tests fail against v2.19.0 (git show v2.19.0:<script>).
"""
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

import jxl_jpeg_transcoder as tra
import jxl_recompressor as rec
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "吾妻山 [exif] 100%"
_TOOLS = ("cjxl", "djxl", "exiftool", "magick", "jxlinfo")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")
_XMP = ("<?xpacket begin='﻿' id='W5M0MpCehiHzreSzNTczkc9d'?>"
        "<x:xmpmeta xmlns:x='adobe:ns:meta/'>"
        "<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>"
        "<rdf:Description rdf:about='' xmlns:xmp='http://ns.adobe.com/xap/1.0/'>"
        "<xmp:CreatorTool>Capture One Windows</xmp:CreatorTool>"
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
        "<?xpacket end='w'?>").encode("utf-8")


# --------------------------------------------------------------------- helpers
def _run(tmp_path, script, *args):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"),
           "JXLPHOTO_JXL_THUMB": "0"}
    r = subprocess.run([sys.executable, str(REPO / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=900, env=env,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, (r.stdout + r.stderr)[-6000:]
    return r.stdout + r.stderr


def _boxes(path):
    """[(type, whole box bytes)] — an independent reader."""
    data, out, pos = Path(path).read_bytes(), [], 0
    while pos + 8 <= len(data):
        size, typ = struct.unpack(">I4s", data[pos:pos + 8])
        if size == 1:
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
        elif size == 0:
            size = len(data) - pos
        out.append((typ, data[pos:pos + size]))
        pos += size
    return out


def _order(path):
    """Box types, a brob box named by the type it compresses."""
    out = []
    for t, b in _boxes(path):
        if t == b"brob":
            t = b[8:12]
        if t in (b"Exif", b"xml ", b"jxlc", b"jxlp"):
            out.append(t)
    return out


def _exif_first(path):
    order = _order(path)
    assert b"Exif" in order and b"xml " in order, order
    return order.index(b"Exif") < order.index(b"xml ")


def _make_xml_first(path):
    """Rewrite `path` the way v2.17.0-v2.19.0 wrote it: the Exif box after
    the XMP box, everything else unchanged."""
    boxes = _boxes(path)
    exif = [b for b in boxes if b[0] == b"Exif"]
    rest = [b for b in boxes if b[0] != b"Exif"]
    k = next(i for i, b in enumerate(rest) if b[0] == b"xml ")
    Path(path).write_bytes(b"".join(b for _, b in rest[:k + 1] + exif + rest[k + 1:]))
    assert not _exif_first(path)


def _make(path, xmp=True):
    """16-bit RGB TIFF with camera EXIF (written by exiftool) and, when
    asked, a Capture One XMP packet."""
    rng = np.random.default_rng(7)
    arr = (rng.random((96, 64, 3)) * 65535).astype("uint16")
    path.parent.mkdir(parents=True, exist_ok=True)
    extra = [(700, "B", len(_XMP), _XMP, False)] if xmp else []
    tifffile.imwrite(str(path), arr, photometric="rgb", metadata=None, extratags=extra)
    r = enc._run_exiftool_argfile(["-overwrite_original", "-Make=NIKON CORPORATION",
                                   "-Model=NIKON Z f", "-ISO=200", str(path)])
    assert r.returncode == 0, r.stderr


def _model(path):
    import json
    r = enc._run_exiftool_argfile(["-j", "-Model", str(path)])
    return json.loads(r.stdout)[0].get("Model")


def _master(tmp_path):
    src = tmp_path / HOSTILE / "260101_写真 [FINAL]"
    _make(src / "DSC_0001 (2).tif")
    _run(tmp_path, "jxl_tiff_encoder.py", src, "--mode", "1", "--distance", "0.5",
         "--no-preflight")
    jxl = src / "converted_jxl" / "DSC_0001 (2).jxl"
    assert jxl.exists()
    return jxl


# ------------------------------------------------------------------ real tests
@needs_tools
def test_encoder_writes_exif_before_the_xmp(tmp_path):
    jxl = _master(tmp_path)
    assert _exif_first(jxl), _order(jxl)
    order = _order(jxl)
    assert order.index(b"xml ") < order.index(b"jxlc" if b"jxlc" in order else b"jxlp")
    assert _model(jxl) == "NIKON Z f"


@needs_tools
def test_recompressor_output_puts_exif_first_from_an_xml_first_source(tmp_path):
    jxl = _master(tmp_path)
    _make_xml_first(jxl)
    folder = jxl.parent
    _run(tmp_path, "jxl_recompressor.py", folder, "--mode", "1", "--distance", "2",
         "--no-keep-smaller", "--no-preflight", "--workers", "1")
    out = folder / "recompressed_jxl" / jxl.name
    assert out.exists()
    assert _exif_first(out), _order(out)
    assert _model(out) == "NIKON Z f"


@needs_tools
def test_recompressor_derivative_puts_exif_first(tmp_path):
    jxl = _master(tmp_path)
    _make_xml_first(jxl)
    folder = jxl.parent
    _run(tmp_path, "jxl_recompressor.py", folder, "--mode", "1", "--distance", "1",
         "--output-icc", "sRGB", "--resize-long", "48", "--no-preflight",
         "--workers", "1")
    out = folder / "recompressed_jxl" / jxl.name
    assert out.exists()
    assert _exif_first(out), _order(out)


@needs_tools
def test_refresh_moves_the_exif_first_in_place_and_keeps_everything_else(tmp_path):
    jxl = _master(tmp_path)
    _make_xml_first(jxl)
    old = 1_600_000_000
    os.utime(jxl, (old, old))
    before = {t: b for t, b in _boxes(jxl)}
    cs = rec._codestream_sha256(jxl)

    # Lossy and no jxl-thumb (JXLPHOTO_JXL_THUMB=0): the thumbnail is skipped,
    # the order is still repaired.
    log = _run(tmp_path, "jxl_recompressor.py", jxl.parent, "--mode", "0",
               "--refresh-explorer-thumbnails")
    assert "REORDERED" in log, log[-3000:]
    assert _exif_first(jxl), _order(jxl)
    after = {t: b for t, b in _boxes(jxl)}
    assert after == before, "every box keeps its bytes"
    assert rec._codestream_sha256(jxl) == cs
    assert int(jxl.stat().st_mtime) == old, "the modified date must be kept (sync)"
    assert _model(jxl) == "NIKON Z f"
    assert not list(jxl.parent.glob("*.tmp"))

    # Run it again over its own output: nothing to do, the file is untouched.
    data = jxl.read_bytes()
    log = _run(tmp_path, "jxl_recompressor.py", jxl.parent, "--mode", "0",
               "--refresh-explorer-thumbnails")
    assert "REORDERED" not in log, log[-3000:]
    assert jxl.read_bytes() == data


@needs_tools
def test_refresh_dry_run_reports_and_writes_nothing(tmp_path):
    jxl = _master(tmp_path)
    _make_xml_first(jxl)
    data = jxl.read_bytes()
    log = _run(tmp_path, "jxl_recompressor.py", jxl.parent, "--mode", "0",
               "--refresh-explorer-thumbnails", "--dry-run")
    assert "EXIF box moved before the XMP" in log, log[-3000:]
    assert jxl.read_bytes() == data


@needs_tools
def test_transcoder_lossless_jpeg_exif_first_and_still_bit_exact(tmp_path):
    from PIL import Image
    folder = tmp_path / HOSTILE
    folder.mkdir()
    jpg = folder / "DSC_0002 (3).jpg"
    Image.fromarray((np.random.default_rng(3).random((64, 48, 3)) * 255)
                    .astype("uint8")).save(jpg, quality=90)
    r = enc._run_exiftool_argfile(["-overwrite_original", "-Model=NIKON Z f",
                                   "-XMP-xmp:CreatorTool=Capture One Windows", str(jpg)])
    assert r.returncode == 0, r.stderr
    original = tra.md5_of_file(jpg)
    _run(tmp_path, "jxl_jpeg_transcoder.py", folder, "--mode", "1")
    jxl = folder / "converted_jxl" / "DSC_0002 (3).jxl"
    assert _exif_first(jxl), _order(jxl)
    assert tra._jxl_reconstruct_md5(jxl) == original


# ------------------------------------------------------------------ unit tests
def _box(typ, payload=b""):
    return typ, struct.pack(">I4s", 8 + len(payload), typ), payload


@pytest.mark.parametrize("mod", [enc, rec, tra], ids=["enc", "rec", "tra"])
def test_exif_box_rank(mod):
    assert mod._exif_box_rank(_box(b"Exif", b"\0\0\0\0II*\0")) == 0
    assert mod._exif_box_rank(_box(b"brob", b"Exif...")) == 0
    assert mod._exif_box_rank(_box(b"brob", b"xml ...")) == 1
    assert mod._exif_box_rank(_box(b"xml ", b"<x/>")) == 1
    assert mod._exif_box_rank(_box(b"jbrd", b"\0")) == 1
    boxes = [_box(b"xml ", b"a"), _box(b"jbrd", b"b"), _box(b"Exif", b"c")]
    assert [b[0] for b in sorted(boxes, key=mod._exif_box_rank)] == [
        b"Exif", b"xml ", b"jbrd"]


def _container(tmp_path, *boxes):
    p = tmp_path / "伊豆沼 [u] 5%.jxl"
    sig = struct.pack(">I4s", 12, b"JXL ") + b"\r\n\x87\n"
    body = b"".join(struct.pack(">I4s", 8 + len(pl), t) + pl for t, pl in boxes)
    p.write_bytes(sig + body)
    return p


def test_exif_after_xml_and_move(tmp_path):
    p = _container(tmp_path, (b"ftyp", b"jxl \0\0\0\0jxl "), (b"xml ", b"<x/>"),
                   (b"brob", b"Exif" + b"z" * 9), (b"uuid", b"u" * 20),
                   (b"jxlc", b"\xff\x0a" + b"c" * 30))
    assert rec._exif_after_xml(p) is True
    rec._move_exif_first(p)
    assert [t for t, _ in _boxes(p)] == [b"JXL ", b"ftyp", b"brob", b"xml ",
                                         b"uuid", b"jxlc"]
    assert rec._exif_after_xml(p) is False
    q = _container(tmp_path, (b"ftyp", b"jxl \0\0\0\0jxl "),
                   (b"jxlc", b"\xff\x0a" + b"c" * 30))
    assert rec._exif_after_xml(q) is False
