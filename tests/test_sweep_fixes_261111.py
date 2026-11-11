"""Round 80 (#642-#646): what a read of the owner's whole disk left to decide.

#642  a camera TIFF's own 160 px thumbnail page, written WITHOUT the
      reduced-image flag (NX Studio, Fuji Hyper Utility), is archived as a
      page like any other — and a derivative run made a resized/sharpened
      copy and a JPEG of it, beside the photo's. No copy is made of it now.
#643  the Photoshop image resources of a TIFF (tag 34377) were never carried;
      a small block now is, byte for byte, and an IPTC block that lives only
      inside it reaches the XMP like the IPTC tag's does (#640).
#644  the padding Windows Photos reserves in a TIFF (tag 59932, 2 KB of
      zeros) became a 4 KB marker in the master.
#645  a lossy JXL and a JPEG/PNG made from an Ultra HDR JPEG kept the XMP
      that announces a gain map they do not hold.
#646  the jxlphoto-tifftag markers (only a JXL -> TIFF decode reads them)
      rode along into every JPEG and PNG.

Synthetic files in a hostile folder (Japanese, `[ ]`, a space, parentheses,
with and without `%`), REAL cjxl/djxl/exiftool.
"""
import hashlib
import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)

HOSTILE = "写真 [R80] 100%off (2)"
HOSTILE_NO_PERCENT = "写真 [R80] off (2)"
_TOOLS = ("cjxl", "djxl", "exiftool")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")
needs_magick = pytest.mark.skipif(not shutil.which("magick"), reason="needs ImageMagick")


# ----------------------------------------------------------------- helpers
def _run(tmp_path, script, *args, timeout=900):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"),
           "JXLPHOTO_SETTINGS": "", "JXLPHOTO_JXL_THUMB": "0",
           "JXLPHOTO_NO_UPDATE_CHECK": "1"}
    r = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, env=env,
                       stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout + r.stderr


def _encode(tmp_path, folder, *extra):
    return _run(tmp_path, "jxl_tiff_encoder.py", folder, "--mode", "1", "--distance", "0",
                "--effort", "2", "--no-preflight", *extra)


def _folder(tmp_path, name="shoot", hostile=HOSTILE):
    d = tmp_path / hostile / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tags(path, *names):
    path = Path(path)
    r = subprocess.run(["exiftool", "-j", "-n", "-G1", *[f"-{n}" for n in names], path.name],
                       cwd=str(path.parent), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return json.loads(r.stdout)[0] if r.stdout.strip() else {}


def _relation(path):
    rel = _tags(path, "XMP-dc:Relation").get("XMP-dc:Relation", [])
    return [str(rel)] if not isinstance(rel, list) else [str(t) for t in rel]


def _tifftags(path):
    return sorted(t.split(":")[1] for t in _relation(path) if t.startswith("jxlphoto-tifftag:"))


def _raw_tag(tiff, code):
    with tifffile.TiffFile(str(tiff)) as t:
        tag = t.pages[0].tags.get(code)
        if tag is None:
            return None
        t.filehandle.seek(tag.valueoffset)
        return t.filehandle.read(int(tag.valuebytecount))


def _pixels(seed, w=320, h=240, dtype=np.uint16):
    rng = np.random.default_rng(seed)
    top = 65535 if dtype == np.uint16 else 255
    return (rng.random((h, w, 3)) * top).astype(dtype)


def _write_tiff(path, extratags=(), software="Test Editor 1.0"):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), _pixels(1), photometric="rgb", software=software,
                     metadata=None, extratags=list(extratags))
    return path


def _irb(*records):
    """A Photoshop image resource block: (id, payload) records."""
    out = b""
    for rid, payload in records:
        out += b"8BIM" + struct.pack(">H", rid) + b"\x00\x00" + struct.pack(">I", len(payload))
        out += payload + (b"\x00" if len(payload) % 2 else b"")
    return out


def _iim(*datasets):
    """An IPTC IIM stream: (record, dataset, bytes)."""
    return b"".join(b"\x1c" + bytes([rec, ds]) + struct.pack(">H", len(v)) + v
                    for rec, ds, v in datasets)


IPTC = _iim((1, 90, b"\x1b%G"), (2, 55, b"19580614"), (2, 25, "彼岸花".encode("utf-8")))
SMALL_IRB = _irb((0x0425, bytes(range(16))), (0x0404, IPTC))
PREVIEW_IRB = _irb((0x040C, bytes(6000)), (0x0425, bytes(range(16))))
PATHS_IRB = _irb((0x040C, bytes(6000)), (2000, bytes(260)))


_MODS = {}


def _mod(script):
    if script not in _MODS:
        sys.path.insert(0, str(SCRIPTS))
        try:
            spec = importlib.util.spec_from_file_location(
                "r80_" + script.replace(".py", ""), str(SCRIPTS / script))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        finally:
            sys.path.remove(str(SCRIPTS))
        _MODS[script] = mod
    return _MODS[script]


# ------------------------------------------------------ #642 camera thumbnail
def _camera_tiff(path):
    """A camera program's TIFF: the photo, then its 160x107 thumbnail as a
    second page with no SubfileType at all (what NX Studio writes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tifffile.TiffWriter(str(path)) as tw:
        tw.write(_pixels(1, 640, 427), photometric="rgb", software="NX Studio 1.5.0",
                 metadata=None)
        tw.write(_pixels(2, 160, 107, np.uint8), photometric="rgb", metadata=None)
    return path


@pytest.mark.parametrize("script", ["jxl_recompressor.py", "jxl_jpeg_transcoder.py"])
@pytest.mark.parametrize("tokens, w, h, expected", [
    (["jxlphoto-mpg:abc", "jxlphoto-page:1"], 160, 107, True),
    (["jxlphoto-page:1"], 107, 160, True),
    (["jxlphoto-page:2"], 160, 120, True),
    (["jxlphoto-page:0"], 160, 120, False),          # the first page is the photo
    (["jxlphoto-src:x"], 160, 120, False),           # a small photo of its own
    (["jxlphoto-page:1"], 161, 107, False),
    (["jxlphoto-page:1"], 4000, 3000, False),        # an IR page, a second exposure
    (["jxlphoto-page:1"], 0, 0, False),              # size unknown: not one
    (["jxlphoto-page:1"], None, None, False),
    (["jxlphoto-page:x"], 160, 120, False),
])
def test_642_what_counts_as_a_camera_thumbnail_page(script, tokens, w, h, expected):
    assert _mod(script)._is_camera_thumbnail_page(tokens, w, h) is expected


@needs_tools
@needs_magick
def test_642_no_copy_is_made_of_a_camera_thumbnail_page(tmp_path):
    src = _folder(tmp_path, "nx")
    _camera_tiff(src / "DSC_0001 (2).tif")
    rc, out = _encode(tmp_path, src)
    masters = sorted(p.name for p in (src / "converted_jxl").glob("*.jxl"))
    assert rc == 0 and masters == ["DSC_0001 (2).jxl", "DSC_0001 (2)_page1.jxl"], out

    # the recompressor's resized copies: of the photo only
    rc, out = _run(tmp_path, "jxl_recompressor.py", src / "converted_jxl", "--mode", "1",
                   "--distance", "1", "--resize-long", "200")
    copies = sorted(p.name for p in (src / "converted_jxl").rglob("*.jxl")
                    if p.parent != src / "converted_jxl")
    assert rc == 0, out
    assert copies == ["DSC_0001 (2).jxl"], (copies, out)
    assert "thumbnail page" in " ".join(out.split()), out

    # the transcoder's resized JPEGs: of the photo only (a copy of the two
    # masters alone, without the recompressor's output folder)
    alone = _folder(tmp_path, "nx_jpeg")
    for p in (src / "converted_jxl").glob("*.jxl"):
        shutil.copyfile(p, alone / p.name)
    rc, out = _run(tmp_path, "jxl_jpeg_transcoder.py", alone, "--mode", "8",
                   "--force-convert", "--decode", "--format", "jpeg", "--resize-long", "200")
    jpegs = sorted(p.name for p in alone.glob("*.jp*g"))
    assert rc == 0, out
    assert jpegs == ["DSC_0001 (2).jpg"], (jpegs, out)
    assert "1 thumbnail page(s) of split TIFFs skipped" in " ".join(out.split()), out


@needs_tools
def test_642_the_master_still_holds_the_whole_tiff(tmp_path):
    """Control: nothing changes for the archive — both pages are there and the
    decoder writes the TIFF back with both, pixel for pixel."""
    src = _folder(tmp_path, "nx_back")
    tif = _camera_tiff(src / "DSC_0001 (2).tif")
    rc, out = _encode(tmp_path, src)
    assert rc == 0, out
    rc, out = _run(tmp_path, "jxl_tiff_decoder.py", src / "converted_jxl", "--mode", "1",
                   "--depth-policy", "preserve_original")
    back = src / "converted_jxl" / "converted_tiff" / "DSC_0001 (2).tif"
    assert rc == 0 and back.exists(), out
    with tifffile.TiffFile(str(tif)) as a, tifffile.TiffFile(str(back)) as b:
        assert len(a.pages) == len(b.pages) == 2
        for pa, pb in zip(a.pages, b.pages):
            assert np.array_equal(pa.asarray(), pb.asarray())


# ---------------------------------------------------- #643 Photoshop block
@needs_tools
@pytest.mark.parametrize("hostile", [HOSTILE, HOSTILE_NO_PERCENT])
def test_643_a_small_photoshop_block_is_carried_and_comes_back(tmp_path, hostile):
    src = _folder(tmp_path, "ps", hostile)
    tif = _write_tiff(src / "DSC_0001 (2).tif", [(34377, 1, len(SMALL_IRB), SMALL_IRB, True)])
    rc, out = _encode(tmp_path, src)
    jxl = src / "converted_jxl" / "DSC_0001 (2).jxl"
    assert rc == 0 and jxl.exists(), out
    assert "34377" in _tifftags(jxl), _relation(jxl)
    rc, out = _run(tmp_path, "jxl_tiff_decoder.py", src / "converted_jxl", "--mode", "1")
    back = src / "converted_jxl" / "converted_tiff" / "DSC_0001 (2).tif"
    assert rc == 0 and back.exists(), out
    assert _raw_tag(back, 34377) == _raw_tag(tif, 34377) == SMALL_IRB


@needs_tools
@pytest.mark.parametrize("hostile", [HOSTILE, HOSTILE_NO_PERCENT])
def test_643_an_iptc_block_inside_the_photoshop_block_reaches_the_xmp(tmp_path, hostile):
    """The capture date and the keyword exist only in the IPTC stream inside
    tag 34377 (no tag 33723, no XMP): they are in the master's XMP. Both
    routes: a '%' path goes through a small TIFF holding only the stream."""
    src = _folder(tmp_path, "ps_iptc", hostile)
    _write_tiff(src / "DSC_0001 (2).tif", [(34377, 1, len(SMALL_IRB), SMALL_IRB, True)])
    rc, out = _encode(tmp_path, src)
    jxl = src / "converted_jxl" / "DSC_0001 (2).jxl"
    assert rc == 0 and jxl.exists(), out
    t = _tags(jxl, "XMP-photoshop:DateCreated", "XMP-dc:Subject")
    assert str(t.get("XMP-photoshop:DateCreated", "")).startswith("1958:06:14"), t
    subj = t.get("XMP-dc:Subject")
    assert (subj if isinstance(subj, list) else [subj]) == ["彼岸花"], t


@needs_tools
def test_643_a_large_block_is_left_out_said_once_and_never_a_veto(tmp_path):
    """A Lightroom/Photoshop TIFF: most of the block is a preview. Not
    carried, one summary line (not one warning per file), and the TIFF is
    deleted as before (#620 decided tag 34377 never keeps the source)."""
    src = _folder(tmp_path, "ps_big")
    tif = _write_tiff(src / "DSC_0001 (2).tif",
                      [(34377, 1, len(PREVIEW_IRB), PREVIEW_IRB, True)])
    rc, out = _encode(tmp_path, src, "--delete-source", "--delete-confirm-off")
    flat = " ".join(out.split())
    jxl = src / "converted_jxl" / "DSC_0001 (2).jxl"
    assert rc == 0 and jxl.exists(), out
    assert "34377" not in _tifftags(jxl)
    assert "Photoshop resources: 1 page(s)" in flat, out
    assert "WARNING" not in out or "Photoshop resource block" not in out, out
    assert not tif.exists(), out


@needs_tools
def test_643_saved_paths_in_a_block_left_out_are_named(tmp_path):
    src = _folder(tmp_path, "ps_paths")
    _write_tiff(src / "DSC_0001 (2).tif", [(34377, 1, len(PATHS_IRB), PATHS_IRB, True)])
    rc, out = _encode(tmp_path, src)
    flat = " ".join(out.split())
    assert rc == 0, out
    assert "WARNING" in out and "DSC_0001 (2).tif" in flat and "saved paths" in flat, out


def test_643_the_resource_reader_stops_at_what_it_cannot_parse():
    enc = _mod("jxl_tiff_encoder.py")
    assert enc._irb_resources(SMALL_IRB) == [(0x0425, bytes(range(16))), (0x0404, IPTC)]
    assert enc._irb_resources(SMALL_IRB[:-3]) == [(0x0425, bytes(range(16)))]
    assert enc._irb_resources(b"not a block") == []
    assert enc._irb_resources(b"") == []


# ------------------------------------------------------- #644 padding tag
@needs_tools
def test_644_the_padding_tag_is_not_carried_and_a_scanner_tag_still_is(tmp_path):
    src = _folder(tmp_path, "pad")
    _write_tiff(src / "DSC_0001 (2).tif",
                [(59932, 7, 2060, bytes(2060), True), (35243, 2, 6, b"film1\x00", True)])
    rc, out = _encode(tmp_path, src)
    jxl = src / "converted_jxl" / "DSC_0001 (2).jxl"
    assert rc == 0 and jxl.exists(), out
    assert _tifftags(jxl) == ["35243"], _relation(jxl)
    assert "private TIFF tags are not kept" not in " ".join(out.split()), out


# ------------------------------------------ #645 the XMP that announces HDR
XMP_HEAD = b"http://ns.adobe.com/xap/1.0/\x00"
ULTRA_HDR_XMP = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
    b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
    b'rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" '
    b'xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/" '
    b'xmlns:Container="http://ns.google.com/photos/1.0/container/" '
    b'xmlns:Item="http://ns.google.com/photos/1.0/container/item/" '
    b'hdrgm:Version="1.0"><dc:title><rdf:Alt><rdf:li xml:lang="x-default">'
    b'kept</rdf:li></rdf:Alt></dc:title><Container:Directory><rdf:Seq>'
    b'<rdf:li rdf:parseType="Resource"><Container:Item Item:Semantic="Primary" '
    b'Item:Mime="image/jpeg"/></rdf:li><rdf:li rdf:parseType="Resource">'
    b'<Container:Item Item:Semantic="GainMap" Item:Mime="image/jpeg" '
    b'Item:Length="1234"/></rdf:li></rdf:Seq></Container:Directory>'
    b'</rdf:Description></rdf:RDF></x:xmpmeta>')


def _jpeg_bytes(seed, w=160, h=120):
    rng = np.random.default_rng(seed)
    buf = io.BytesIO()
    Image.fromarray((rng.random((h, w, 3)) * 255).astype(np.uint8)).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _ultra_hdr(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    first, second = _jpeg_bytes(1), _jpeg_bytes(2, 40, 30)
    payload = XMP_HEAD + ULTRA_HDR_XMP
    seg = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    path.write_bytes(first[:2] + seg + first[2:] + second)
    return path


_HDR_TAGS = ("XMP-hdrgm:all", "XMP-GContainer:all")


@needs_tools
def test_645_outputs_without_the_gain_map_do_not_announce_one(tmp_path):
    src = _folder(tmp_path, "hdr")
    jpg = _ultra_hdr(src / "DSC_0001 (2).jpg")
    had = _tags(jpg, *_HDR_TAGS)
    assert any(k.startswith("XMP-hdrgm") for k in had) and any(
        k.startswith("XMP-GContainer") for k in had), had       # the fixture is what it says
    rc, out = _run(tmp_path, "jxl_jpeg_transcoder.py", src, "--mode", "1", "--force-convert",
                   "--from-jpeg", "--distance", "1")
    jxl = src / "converted_jxl" / "DSC_0001 (2).jxl"
    assert rc == 0 and jxl.exists(), out
    left = {k: v for k, v in _tags(jxl, *_HDR_TAGS, "XMP-dc:Title").items() if k != "SourceFile"}
    assert left == {"XMP-dc:Title": "kept"}, left
    rc, out = _run(tmp_path, "jxl_jpeg_transcoder.py", src / "converted_jxl", "--mode", "8",
                   "--force-convert", "--decode", "--format", "jpeg")
    back = src / "converted_jxl" / "DSC_0001 (2).jpg"
    assert rc == 0 and back.exists(), out
    left = {k: v for k, v in _tags(back, *_HDR_TAGS, "XMP-dc:Title").items() if k != "SourceFile"}
    assert left == {"XMP-dc:Title": "kept"}, left


@needs_tools
def test_645_the_lossless_archive_keeps_the_jpeg_as_it_is(tmp_path):
    """Control: the lossless transcode holds the whole JPEG, gain map and its
    XMP included — nothing is edited there."""
    src = _folder(tmp_path, "hdr_ll")
    jpg = _ultra_hdr(src / "DSC_0001 (2).jpg")
    before = jpg.read_bytes()
    rc, out = _run(tmp_path, "jxl_jpeg_transcoder.py", src, "--mode", "1", "--force-transcode")
    jxl = src / "converted_jxl" / "DSC_0001 (2).jxl"
    assert rc == 0 and jxl.exists(), out
    back = tmp_path / "back.jpg"
    r = subprocess.run(["djxl", jxl.name, str(back)], cwd=str(jxl.parent), capture_output=True)
    assert r.returncode == 0 and back.read_bytes() == before


# ------------------------------------- #647 a '%d', '%o', '%x' in the folder
@pytest.mark.parametrize("script", ["jxl_tiff_encoder.py", "jxl_tiff_decoder.py",
                                    "jxl_jpeg_transcoder.py", "jxl_recompressor.py"])
def test_647_a_percent_sign_is_doubled_in_what_imagemagick_writes(script):
    m = _mod(script)
    assert m._magick_out_path(Path(r"E:\50%off\a%d.jpg")) == r"E:\50%%off\a%%d.jpg"
    assert m._magick_out_path(r"E:\plain\a.jpg") == r"E:\plain\a.jpg"


@needs_tools
@needs_magick
@pytest.mark.parametrize("fmt", ["jpeg", "png"])
def test_647_a_resized_copy_in_a_folder_named_like_a_printf_format(tmp_path, fmt):
    """ImageMagick formats the name it WRITES like printf: '100%off' became
    '1000ff' and every resized, sharpened or colour-converted JPEG/PNG of such
    a folder ended in "No such file or directory"."""
    src = _folder(tmp_path, "pct", "写真 [R80] 100%off %d 5%x (2)")
    _write_tiff(src / "DSC_0001 (2).tif")
    rc, out = _encode(tmp_path, src)
    jxl = src / "converted_jxl" / "DSC_0001 (2).jxl"
    assert rc == 0 and jxl.exists(), out
    rc, out = _run(tmp_path, "jxl_jpeg_transcoder.py", jxl.parent, "--mode", "8",
                   "--force-convert", "--decode", "--format", fmt, "--resize-long", "200")
    made = [p for p in jxl.parent.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
    assert rc == 0 and len(made) == 1, out
    with Image.open(made[0]) as im:
        assert max(im.size) == 200
    assert not list(tmp_path.rglob("*.tmp")), list(tmp_path.rglob("*.tmp"))


# --------------------------------------------- #646 tifftag in JPEG and PNG
def _master_with_tifftags(tmp_path, name):
    src = _folder(tmp_path, name)
    tif = _write_tiff(src / "DSC_0001 (2).tif",
                      [(33723, 7, len(IPTC), IPTC, True), (35243, 2, 6, b"film1\x00", True)],
                      software="SilverFast 8.8.0")
    rc, out = _encode(tmp_path, src)
    jxl = src / "converted_jxl" / "DSC_0001 (2).jxl"
    assert rc == 0 and jxl.exists(), out
    assert _tifftags(jxl) == ["33723", "35243"], _relation(jxl)
    return tif, jxl


@needs_tools
@pytest.mark.parametrize("fmt", ["jpeg", "png"])
def test_646_a_jpeg_or_png_made_from_a_master_carries_no_tifftag(tmp_path, fmt):
    _tif, jxl = _master_with_tifftags(tmp_path, "tt_" + fmt)
    rc, out = _run(tmp_path, "jxl_jpeg_transcoder.py", jxl.parent, "--mode", "8",
                   "--force-convert", "--decode", "--format", fmt)
    made = [p for p in jxl.parent.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
    assert rc == 0 and len(made) == 1, out
    rel = _relation(made[0])
    assert not [t for t in rel if t.startswith("jxlphoto-tifftag:")], rel
    assert any(t.startswith("jxlphoto-src:") for t in rel), rel      # the other markers stay
    assert any(t.startswith("jxlphoto-scan:") for t in rel), rel


@needs_tools
@needs_magick
def test_646_a_resized_jpeg_carries_none_either(tmp_path):
    _tif, jxl = _master_with_tifftags(tmp_path, "tt_resized")
    rc, out = _run(tmp_path, "jxl_jpeg_transcoder.py", jxl.parent, "--mode", "8",
                   "--force-convert", "--decode", "--format", "jpeg", "--resize-long", "200")
    made = [p for p in jxl.parent.iterdir() if p.suffix.lower() in (".jpg", ".jpeg")]
    assert rc == 0 and len(made) == 1, out
    rel = _relation(made[0])
    assert not [t for t in rel if t.startswith("jxlphoto-tifftag:")], rel
    assert any(t.startswith("jxlphoto-derived:") for t in rel), rel


@needs_tools
def test_646_a_recompressed_jxl_keeps_them_and_the_tiff_gets_its_tags_back(tmp_path):
    """Control: a JXL copy can still be decoded to a TIFF, so it keeps the
    markers; and the decoder writes the tags back, byte for byte."""
    tif, jxl = _master_with_tifftags(tmp_path, "tt_keep")
    rc, out = _run(tmp_path, "jxl_recompressor.py", jxl.parent, "--mode", "1", "--distance", "1")
    copies = [p for p in jxl.parent.rglob("*.jxl") if p.parent != jxl.parent]
    assert rc == 0 and len(copies) == 1, out
    assert _tifftags(copies[0]) == ["33723", "35243"], _relation(copies[0])
    rc, out = _run(tmp_path, "jxl_tiff_decoder.py", jxl.parent, "--mode", "1")
    back = jxl.parent / "converted_tiff" / "DSC_0001 (2).tif"
    assert rc == 0 and back.exists(), out
    assert _raw_tag(back, 33723) == IPTC
    assert _raw_tag(back, 35243) == _raw_tag(tif, 35243)
