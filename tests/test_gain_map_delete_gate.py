"""#641: a lossy JPEG -> JXL (--force-convert --from-jpeg, distance above 0) of
a JPEG that carries an HDR gain map.

An Ultra HDR JPEG (Android), an ISO 21496-1 one and Apple's are a standard
picture plus a second, smaller image after it — the gain map an HDR screen
uses. cjxl encodes the FIRST image of the file, so the converted JXL holds the
standard picture only, and `--delete-source` then deleted the only copy of the
gain map without a word (measured on copies of the owner's Xiaomi 17T Pro
JPEGs on 2026-11-11: 459 KB of gain map gone). Now: one warning per file, and
the delete gates keep such a JPEG. The lossless transcode keeps the whole file
(bit-exact) and still deletes; a camera's MPO (preview images, no gain map) is
not held back.

Synthetic JPEGs in a hostile folder (Japanese, `[ ]`, a space, `%`,
parentheses), REAL cjxl/djxl/exiftool; the owner's real files when present.
"""
import hashlib
import importlib.util
import io
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)

HOSTILE = "写真 [HDR] 100%off (2)"
_TOOLS = ("cjxl", "djxl", "exiftool")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")

OWNER_FILES = Path(r"E:\TESTE\JPEG_varied")

XMP_HEAD = b"http://ns.adobe.com/xap/1.0/\x00"
ULTRA_HDR_XMP = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
    b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
    b'rdf:about="" xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/" '
    b'hdrgm:Version="1.0"/></rdf:RDF></x:xmpmeta>')
APPLE_XMP = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
    b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
    b'rdf:about="" xmlns:apdi="http://ns.apple.com/pixeldatainfo/1.0/" '
    b'apdi:AuxiliaryImageType="urn:com:apple:photo:2020:aux:hdrgainmap"/>'
    b'</rdf:RDF></x:xmpmeta>')
ISO_APP2 = b"urn:iso:std:iso:ts:21496:-1\x00\x00\x00\x00\x00"


# ----------------------------------------------------------------- helpers
def _jpeg_bytes(seed, w=160, h=120):
    rng = np.random.default_rng(seed)
    buf = io.BytesIO()
    Image.fromarray((rng.random((h, w, 3)) * 255).astype(np.uint8)).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _seg(marker, payload):
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload


def _mpf(first_len, second_len):
    """A valid two-image MPF index (CIPA DC-007): offsets count from the
    byte-order mark of this segment; the caller places the second image."""
    entries = (struct.pack("<IIIHH", 0x030000, first_len, 0, 0, 0)
               + struct.pack("<IIIHH", 0x000000, second_len, 0, 0, 0))
    ifd = (struct.pack("<H", 3)
           + struct.pack("<HHI4s", 0xB000, 7, 4, b"0100")
           + struct.pack("<HHII", 0xB001, 4, 1, 2)
           + struct.pack("<HHII", 0xB002, 7, len(entries), 8 + 2 + 3 * 12 + 4)
           + struct.pack("<I", 0))
    return b"MPF\x00" + b"II*\x00" + struct.pack("<I", 8) + ifd + entries


def _two_image_jpeg(path, first_segments=(), second_segments=(), mpf=True):
    """A JPEG followed by a second, smaller JPEG — the layout of an MPO and of
    every gain-map JPEG. `first_segments`/`second_segments` are (marker,
    payload) pairs inserted right after each image's SOI."""
    path.parent.mkdir(parents=True, exist_ok=True)
    first, second = _jpeg_bytes(1), _jpeg_bytes(2, 40, 30)
    second = second[:2] + b"".join(_seg(m, p) for m, p in second_segments) + second[2:]
    segs = b"".join(_seg(m, p) for m, p in first_segments)
    if mpf:
        segs += _seg(0xE2, _mpf(len(first) + len(segs) + 2 + 2 + len(_mpf(0, 0)), len(second)))
    path.write_bytes(first[:2] + segs + first[2:] + second)
    return path


def _ultra_hdr(path):
    return _two_image_jpeg(path, [(0xE1, XMP_HEAD + ULTRA_HDR_XMP)])


def _iso_gain_map(path):
    return _two_image_jpeg(path, [(0xE2, ISO_APP2)], mpf=False)


def _apple_gain_map(path):
    return _two_image_jpeg(path, second_segments=[(0xE1, XMP_HEAD + APPLE_XMP)])


def _camera_mpo(path):
    return _two_image_jpeg(path)


def _plain(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_jpeg_bytes(1))
    return path


def _folder(tmp_path, name="shoot"):
    d = tmp_path / HOSTILE / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run(tmp_path, *args, timeout=600):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"),
           "JXLPHOTO_SETTINGS": "", "JXLPHOTO_JXL_THUMB": "0",
           "JXLPHOTO_NO_UPDATE_CHECK": "1"}
    r = subprocess.run([sys.executable, str(SCRIPTS / "jxl_jpeg_transcoder.py"),
                        *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, env=env,
                       stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout + r.stderr


def _lossy(tmp_path, folder, *extra):
    return _run(tmp_path, folder, "--mode", "1", "--force-convert", "--from-jpeg",
                "--distance", "1", *extra)


DELETE = ("--delete-source", "--delete-confirm-off")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _jxls(folder):
    return sorted(Path(folder).rglob("*.jxl"))


_MODS = {}


def _tr():
    if "tr" not in _MODS:
        sys.path.insert(0, str(SCRIPTS))
        try:
            spec = importlib.util.spec_from_file_location(
                "gain_map_transcoder", str(SCRIPTS / "jxl_jpeg_transcoder.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        finally:
            sys.path.remove(str(SCRIPTS))
        _MODS["tr"] = mod
    return _MODS["tr"]


# --------------------------------------------------------------- detection
@pytest.mark.parametrize("make, kind", [
    (_ultra_hdr, "Ultra HDR"), (_iso_gain_map, "ISO 21496-1"), (_apple_gain_map, "Apple"),
    (_camera_mpo, None), (_plain, None)])
def test_641_the_kind_of_gain_map_a_jpeg_carries(tmp_path, make, kind):
    jpg = make(_folder(tmp_path) / "DSC_0001 (2).jpg")
    assert _tr()._jpeg_gain_map(jpg) == kind


def test_641_a_mark_split_between_two_reads_is_still_found(tmp_path):
    """Apple's mark sits in the SECOND image: the file is read in 1 MiB pieces
    and the mark may straddle two of them."""
    m = _tr()
    mark = b"urn:com:apple:photo:2020:aux:hdrgainmap"
    jpg = _camera_mpo(_folder(tmp_path) / "DSC_0001 (2).jpg")
    data = jpg.read_bytes()
    pad = (1 << 20) - len(data) - 10            # the mark starts 10 bytes before 1 MiB
    jpg.write_bytes(data + bytes(pad) + mark + bytes(100))
    assert m._jpeg_gain_map(jpg) == "Apple"


def test_641_not_a_jpeg_and_a_cut_file_have_no_gain_map(tmp_path):
    m = _tr()
    d = _folder(tmp_path)
    (d / "a.jpg").write_bytes(b"")
    (d / "b.jpg").write_bytes(b"RIFF\x00\x00\x00\x00WEBP")
    (d / "c.jpg").write_bytes(_ultra_hdr(d / "x.jpg").read_bytes()[:6])
    assert [m._jpeg_gain_map(d / n) for n in ("a.jpg", "b.jpg", "c.jpg")] == [None, None, None]


def test_641_a_jpeg_that_cannot_be_read_is_kept(tmp_path, monkeypatch):
    """"Cannot tell" is a reason to keep the file, never "no gain map"."""
    m = _tr()
    jpg = _plain(_folder(tmp_path) / "DSC_0001 (2).jpg")
    out = jpg.with_suffix(".jxl")
    out.write_bytes(b"\xff\x0a")

    def boom(_p):
        raise PermissionError("locked")
    monkeypatch.setattr(m, "_jpeg_gain_map", boom)
    why = m._gain_map_keep_reason(jpg, out)
    assert why and why.startswith("KEEP (could not check"), why


def test_641_only_a_jpeg_source_and_a_jxl_output_are_judged(tmp_path):
    m = _tr()
    jpg = _ultra_hdr(_folder(tmp_path) / "DSC_0001 (2).jpg")
    assert m._gain_map_keep_reason(jpg.with_suffix(".png"), jpg.with_suffix(".jxl")) is None
    assert m._gain_map_keep_reason(jpg.with_suffix(".jxl"), jpg) is None
    assert m._gain_map_keep_reason(jpg, None) is None


def test_641_the_owners_real_files():
    xiaomi = OWNER_FILES / "xiaomi17t_ultrahdr_mpf_app4__IMG_20260719_211148.jpg"
    pixel = OWNER_FILES / "mpf_pixel_srgb__PXL_20250210_050603096.jpg"
    nikon = OWNER_FILES / "nikonzf_mpo3_422_o8___DSC0133.JPG"
    casio = OWNER_FILES / "mpf_camera__CIMG1033.JPG"
    if not all(p.exists() for p in (xiaomi, pixel, nikon, casio)):
        pytest.skip("the owner's fixtures are not on this machine")
    m = _tr()
    assert m._jpeg_gain_map(xiaomi) in ("Ultra HDR", "ISO 21496-1")
    assert m._jpeg_gain_map(pixel) == "Ultra HDR"
    assert m._jpeg_gain_map(nikon) is None       # an MPO with two preview images
    assert m._jpeg_gain_map(casio) is None


# ------------------------------------------------------------- real codecs
@needs_tools
@pytest.mark.parametrize("make", [_ultra_hdr, _iso_gain_map, _apple_gain_map])
def test_641_a_lossy_convert_never_deletes_a_jpeg_with_a_gain_map(tmp_path, make):
    jpg = make(_folder(tmp_path, "hdr") / "DSC_0001 (2).jpg")
    before = _sha(jpg)
    rc, out = _lossy(tmp_path, jpg.parent, *DELETE)
    flat = " ".join(out.split())
    assert "Traceback" not in out, out
    assert len(_jxls(jpg.parent)) == 1, out       # the picture is converted
    assert jpg.exists() and _sha(jpg) == before, out
    assert "HDR gain map not carried | DSC_0001 (2).jpg" in flat, out
    assert "KEEP (the JPEG carries an HDR gain map" in flat and "--force-transcode" in flat, out
    assert "kept by a gate: 1" in flat, out


@needs_tools
def test_641_run_again_with_delete_skipped_the_jpeg_is_still_kept(tmp_path):
    """The second run over the toolkit's own output: the JXL exists, the JPEG
    is 'already archived' for --delete-skipped — and still the only copy of
    its gain map."""
    jpg = _ultra_hdr(_folder(tmp_path, "twice") / "DSC_0001 (2).jpg")
    rc, out = _lossy(tmp_path, jpg.parent)
    assert rc == 0 and len(_jxls(jpg.parent)) == 1, out
    assert "HDR gain map not carried" in " ".join(out.split()), out
    rc, out = _lossy(tmp_path, jpg.parent, *DELETE, "--delete-skipped")
    flat = " ".join(out.split())
    assert jpg.exists(), out
    assert "KEEP (the JPEG carries an HDR gain map" in flat, out
    assert "Sources DELETED: 0" in flat, out


@needs_tools
def test_641_the_lossless_transcode_keeps_the_gain_map_and_deletes(tmp_path):
    """The way out the message names: the whole JPEG is in the JXL, bit for
    bit, so its delete gate is not held back."""
    jpg = _ultra_hdr(_folder(tmp_path, "lossless") / "DSC_0001 (2).jpg")
    before = jpg.read_bytes()
    rc, out = _run(tmp_path, jpg.parent, "--mode", "1", "--force-transcode", *DELETE)
    assert rc == 0, out
    jxl = _jxls(jpg.parent)
    assert len(jxl) == 1 and not jpg.exists(), out
    assert "HDR gain map" not in out, out
    back = tmp_path / "back.jpg"
    r = subprocess.run(["djxl", jxl[0].name, str(back)], cwd=str(jxl[0].parent),
                       capture_output=True)
    assert r.returncode == 0 and back.read_bytes() == before


@needs_tools
@pytest.mark.parametrize("make", [_camera_mpo, _plain])
def test_641_a_camera_mpo_and_a_plain_jpeg_are_deleted_as_before(tmp_path, make):
    jpg = make(_folder(tmp_path, "mpo") / "DSC_0001 (2).jpg")
    rc, out = _lossy(tmp_path, jpg.parent, *DELETE)
    assert rc == 0, out
    assert len(_jxls(jpg.parent)) == 1 and not jpg.exists(), out
    assert "HDR gain map" not in out, out
