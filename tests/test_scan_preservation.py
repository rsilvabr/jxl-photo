"""v2.17.0: a film scan survives TIFF -> JXL -> TIFF the way its scanner
software needs it.

SilverFast (tested by the owner on two real scans, a Provia and a Fuji Pro 160
negative) recognised a decoded scan's IR channel only with its own XMP, a
namespace exiftool can read but not write, which the field-by-field copy
(-xmp:all) dropped; and showed "Loading..." instead of the picture without the
1:8 thumbnail page, which --thumbnail-mode exclude (the old default) dropped.
Its two private TIFF tags (35243, 35244) were lost too.

Now the XMP travels as one block, private tags ride in a jxlphoto-tifftag
marker, and --thumbnail-mode auto keeps thumbnail pages except Capture One's.
Real codecs and exiftool; every file inside a folder named with Japanese,
brackets, a space and %.
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

import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "吾妻山 [tmp] 100%"
_TOOLS = ("cjxl", "djxl", "exiftool")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")

# A SilverFast-shaped XMP: its own namespace ("LSI/"), which exiftool reads as
# XMP-Silverfast but cannot write — the part a field copy loses.
SF_XMP = (
    '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '  <rdf:Description rdf:about="" xmlns:Silverfast="LSI/"\n'
    '    Silverfast:Company="LaserSoft Imaging">\n'
    '   <Silverfast:ScanFrames>&lt;isWithIR&gt;TRUE &lt;/isWithIR&gt;'
    '&lt;iSRD_AutoISRD&gt;TRUE&lt;/iSRD_AutoISRD&gt;</Silverfast:ScanFrames>\n'
    '  </rdf:Description>\n'
    ' </rdf:RDF>\n'
    '</x:xmpmeta>\n'
    '<?xpacket end="w"?>').encode("utf-8")
TAG_ID = bytes.fromhex("0b79934200000000")
TAG_FILM = b"Fuji" + bytes(284)


def _run(tmp_path, script, *args):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs")}
    r = subprocess.run([sys.executable, str(REPO / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, env=env)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return r.stdout + r.stderr


def _photo(h, w, seed, channels=3):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    base = np.stack([x * 200 // w, y * 200 // h, (x + y) * 100 // (w + h)], -1)
    a = (base * 257 + rng.integers(0, 2000, (h, w, 3))).astype(np.uint16)
    return a if channels == 3 else a[..., 0].copy()


def _scan(path, software="SilverFast 8.8.0 r16 (May  9 2019)", private=None,
          ir=True, xmp=SF_XMP):
    """[RGB, 1:8 thumbnail, IR mask] like a SilverFast HDRi scan."""
    private = [(35243, TAG_ID), (35244, TAG_FILM)] if private is None else private
    main, thumb, irp = _photo(240, 320, 1), _photo(30, 40, 2), _photo(240, 320, 3, 1)
    extratags = [(code, 1, len(raw), raw, True) for code, raw in private]
    if xmp:
        extratags.append((700, 1, len(xmp), xmp, True))
    with tifffile.TiffWriter(path) as w:
        w.write(main, photometric="rgb", software=software, metadata=None,
                extratags=extratags)
        w.write(thumb, photometric="rgb", subfiletype=1, metadata=None)
        if ir:
            w.write(irp, photometric="minisblack", metadata=None,
                    extratags=[(254, 4, 1, 4, True)])
    return main, thumb, irp


def _exif(*args):
    """exiftool through a UTF-8 argfile: the paths are Japanese."""
    af = Path(os.environ.get("TEMP", ".")) / f"scanpres_{os.getpid()}.args"
    af.write_text("-charset\nfilename=utf8\n" + "\n".join(map(str, args)) + "\n",
                  encoding="utf-8")
    try:
        r = subprocess.run(["exiftool", "-@", str(af)], capture_output=True)
    finally:
        af.unlink()
    return r.stdout


def _relation(path):
    data = json.loads(_exif("-j", "-XMP-dc:Relation", "-XMP-xmpMM:HistoryAction", path))[0]
    rel = data.get("Relation") or []
    hist = data.get("HistoryAction") or []
    return ([rel] if isinstance(rel, str) else rel), ([hist] if isinstance(hist, str) else hist)


@needs_tools
def test_scan_round_trip_keeps_xmp_private_tags_and_thumbnail(tmp_path):
    d = tmp_path / HOSTILE
    (d / "jxl").mkdir(parents=True)
    src = d / "scan 0002 [IR].tif"
    main, thumb, irp = _scan(src)

    _run(tmp_path, "jxl_tiff_encoder.py", src, d / "jxl", "--distance", "0",
         "--effort", "3", "--no-preflight")
    names = sorted(p.name for p in (d / "jxl").glob("*.jxl"))
    assert names == ["scan 0002 [IR].jxl", "scan 0002 [IR]_page1_thumbnail.jxl",
                     "scan 0002 [IR]_page2.jxl"], names
    master = d / "jxl" / "scan 0002 [IR].jxl"
    assert b"isWithIR" in _exif("-b", "-XMP", master)
    rel, hist = _relation(master)
    assert any(t.startswith("jxlphoto-tifftag:35243:1:" + TAG_ID.hex()) for t in rel)
    assert any(t.startswith("jxlphoto-tifftag:35244:1:") for t in rel)
    # The block preload must not make the edit call double a list (#531).
    assert hist == ["converted"], hist

    _run(tmp_path, "jxl_tiff_decoder.py", d / "jxl", d / "tif")
    out = d / "tif" / "scan 0002 [IR].tif"
    with tifffile.TiffFile(out) as t:
        assert [int(p.subfiletype) for p in t.pages] == [0, 1, 4]
        assert np.array_equal(t.pages[0].asarray(), main)
        assert np.array_equal(t.pages[1].asarray(), thumb)
        assert np.array_equal(t.pages[2].asarray(), irp)
        p0 = t.pages[0]
        assert b"isWithIR" in bytes(p0.tags[700].value)
        assert bytes(p0.tags[35243].value) == TAG_ID
        assert bytes(p0.tags[35244].value) == TAG_FILM
    rel, _h = _relation(out)
    assert not any(t.startswith("jxlphoto-tifftag:") for t in rel), rel


@needs_tools
def test_recompressed_master_still_carries_them(tmp_path):
    d = tmp_path / HOSTILE
    (d / "jxl").mkdir(parents=True)
    src = d / "neg [1] 100%.tif"
    _scan(src)
    _run(tmp_path, "jxl_tiff_encoder.py", src, d / "jxl", "--distance", "1",
         "--effort", "3", "--no-preflight")
    _run(tmp_path, "jxl_recompressor.py", d / "jxl", "--mode", "8", "--distance", "0.5",
         "--effort", "3", "--no-keep-smaller", "--on-downgrade", "convert",
         "--on-regeneration", "convert", "--on-unknown", "convert", "--no-preflight",
         "--delete-confirm-off")
    master = d / "jxl" / "neg [1] 100%.jxl"
    rel, hist = _relation(master)
    assert any("cjxl d=0.5" in t for t in [_exif("-s3", "-XMP-dc:Description", master).decode()])
    assert b"isWithIR" in _exif("-b", "-XMP", master)
    assert any(t.startswith("jxlphoto-tifftag:35243:") for t in rel)
    assert hist == ["converted", "converted"], hist
    _run(tmp_path, "jxl_tiff_decoder.py", d / "jxl", d / "tif")
    with tifffile.TiffFile(d / "tif" / "neg [1] 100%.tif") as t:
        assert [int(p.subfiletype) for p in t.pages] == [0, 1, 4]
        assert b"isWithIR" in bytes(t.pages[0].tags[700].value)
        assert bytes(t.pages[0].tags[35243].value) == TAG_ID


@needs_tools
def test_an_oversized_private_tag_keeps_none_of_the_page(tmp_path):
    """All or nothing: a header tag without its big partner is worse than
    neither (Affinity's 51168 + a 230 MB 51169)."""
    d = tmp_path / HOSTILE
    (d / "jxl").mkdir(parents=True)
    src = d / "affinity (1).tif"
    _scan(src, software="Affinity Photo", ir=False,
          private=[(51168, b"\x00\xffKS" + bytes(106)), (51169, bytes(5000))])
    log = _run(tmp_path, "jxl_tiff_encoder.py", src, d / "jxl", "--distance", "0",
               "--effort", "3", "--no-preflight")
    assert "private TIFF tags are not kept" in log and "51169" in log
    rel, _h = _relation(d / "jxl" / "affinity (1).jxl")
    assert not any(t.startswith("jxlphoto-tifftag:") for t in rel), rel


@needs_tools
def test_sync_never_adds_a_thumbnail_beside_an_older_group(tmp_path):
    """An archive written without the thumbnail keeps that layout on a sync
    run (a lone new page would form a second, truncated group); --overwrite
    rewrites the whole scan with it."""
    d = tmp_path / HOSTILE
    (d / "jxl").mkdir(parents=True)
    src = d / "scan (2).tif"
    _scan(src)
    _run(tmp_path, "jxl_tiff_encoder.py", src, d / "jxl", "--distance", "0",
         "--effort", "3", "--no-preflight", "--thumbnail-mode", "exclude")
    before = sorted(p.name for p in (d / "jxl").glob("*.jxl"))
    assert before == ["scan (2).jxl", "scan (2)_page2.jxl"]

    log = _run(tmp_path, "jxl_tiff_encoder.py", src, d / "jxl", "--distance", "0",
               "--effort", "3", "--no-preflight")
    assert sorted(p.name for p in (d / "jxl").glob("*.jxl")) == before
    assert "--overwrite to add them" in " ".join(log.split())

    _run(tmp_path, "jxl_tiff_encoder.py", src, d / "jxl", "--distance", "0",
         "--effort", "3", "--no-preflight", "--overwrite")
    assert sorted(p.name for p in (d / "jxl").glob("*.jxl")) == [
        "scan (2).jxl", "scan (2)_page1_thumbnail.jxl", "scan (2)_page2.jxl"]
    # And a later sync of that archive leaves it alone too (no stale page).
    log = _run(tmp_path, "jxl_tiff_encoder.py", src, d / "jxl", "--distance", "0",
               "--effort", "3", "--no-preflight")
    assert "stale" not in log.lower(), log[-2000:]
    _run(tmp_path, "jxl_tiff_decoder.py", d / "jxl", d / "tif")
    with tifffile.TiffFile(d / "tif" / "scan (2).tif") as t:
        assert [int(p.subfiletype) for p in t.pages] == [0, 1, 4]


@pytest.mark.parametrize("mode,software,mask,kept", [
    ("auto", "SilverFast 8.8.0", False, True),
    ("auto", "Capture One 23 Windows", False, False),
    ("auto", "capture one", False, False),
    ("auto", "", False, True),
    ("scans", "VueScan 9 x64 (9.7.50)", False, True),
    ("scans", "Adobe Photoshop Lightroom", False, False),
    ("scans", "", True, True),
    ("include", "Capture One 23", False, True),
    ("exclude", "SilverFast", True, False),
])
def test_thumbnail_modes(mode, software, mask, kept):
    info = {0: {"subfiletype": 0, "samples": 3, "software": software},
            1: {"subfiletype": 1, "samples": 3}}
    if mask:
        info[2] = {"subfiletype": 4, "samples": 1}
    assert enc._thumbnails_kept(mode, info) is kept


@pytest.mark.parametrize("mode,kept", [("auto", [1]), ("scans", [1]), ("include", [1, 2])])
def test_a_thumbnail_the_encoder_cannot_encode_is_left_out(mode, kept):
    """The decoder appends a YCbCr JPEG preview (photometric 6) to every
    single-page TIFF it writes: re-archiving that TIFF under auto/scans must
    not fail on it nor turn it into a _thumbnail.jxl. include keeps every
    page, as before."""
    info = {0: {"subfiletype": 0, "samples": 3, "software": "SilverFast"},
            1: {"subfiletype": 1, "samples": 3, "photometric": 2},
            2: {"subfiletype": 1, "samples": 3, "photometric": 6}}
    assert enc._kept_thumbnail_pages([1, 2], mode, info) == kept


@needs_tools
def test_a_single_page_tiff_keeps_its_private_tags_through_the_preview_rewrite(tmp_path):
    """A single-page decode gets a JPEG preview page, written by rebuilding
    the file from its pixels (add_jpeg_preview): the private tags must be
    carried through that rebuild too."""
    d = tmp_path / HOSTILE
    (d / "jxl").mkdir(parents=True)
    src = d / "slide [3] 100%.tif"
    main = _photo(240, 320, 5)
    xmp = SF_XMP
    with tifffile.TiffWriter(src) as w:
        w.write(main, photometric="rgb", software="SilverFast 9", metadata=None,
                extratags=[(35243, 1, 8, TAG_ID, True), (700, 1, len(xmp), xmp, True)])
    _run(tmp_path, "jxl_tiff_encoder.py", src, d / "jxl", "--distance", "0",
         "--effort", "3", "--no-preflight")
    _run(tmp_path, "jxl_tiff_decoder.py", d / "jxl", d / "tif")
    with tifffile.TiffFile(d / "tif" / "slide [3] 100%.tif") as t:
        assert len(t.pages) == 2 and int(t.pages[1].subfiletype) == 1   # the preview
        assert np.array_equal(t.pages[0].asarray(), main)
        assert bytes(t.pages[0].tags[35243].value) == TAG_ID
        assert b"isWithIR" in bytes(t.pages[0].tags[700].value)
