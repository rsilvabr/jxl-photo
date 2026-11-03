#!/usr/bin/env python3
"""E-G of the 2026-11-03 audit: the Explorer thumbnail box stays before the
codestream.

`reorder_jxl_boxes` (encoder, recompressor, transcoder) grouped every box it
did not know — the thumbnail's `uuid` box among them — AFTER the codestream.
`_rewrite_master_relation` runs it on masters that already carry a thumbnail
(--restamp-moved, --restamp-then-sync, the --provenance adopt stamp), so each
of those moved the thumbnail to the end of the file, where jxl-thumb does not
look for it. And `--refresh-explorer-thumbnails` read the fingerprint wherever
the box was, called it "current", and never moved it back.

Now an unknown box that sat before the codestream stays before it (in its
order, after the metadata boxes), and the refresh moves a misplaced thumbnail
box back, bytes unchanged (status "reordered").

B1-B4 run the three reorder copies on synthetic containers; F1-F3 the refresh
(no codec needed); R1 runs the real exiftool/cjxl/djxl through
_rewrite_master_relation (skipped when absent). Fixture paths are hostile.

Pre-fix proof (must fail on the base commit's scripts):

    mkdir <tmp>/egpre
    git show HEAD:jxl_tiff_encoder.py > <tmp>/egpre/jxl_tiff_encoder.py
    (same for jxl_tiff_decoder, jxl_jpeg_transcoder, jxl_recompressor)
    JXL_SCRIPTS_UNDER_TEST=<tmp>/egpre python -m pytest tests/test_thumbnail_box_order.py -q
"""

import importlib
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

_REPO = Path(__file__).resolve().parent.parent
_OVERRIDE = os.environ.get("JXL_SCRIPTS_UNDER_TEST")
sys.path.insert(0, str(Path(_OVERRIDE).resolve()) if _OVERRIDE else str(_REPO))

import jxl_recompressor as rec  # noqa: E402
import jxl_tiff_encoder as enc  # noqa: E402

REORDER_SCRIPTS = ["jxl_tiff_encoder", "jxl_recompressor", "jxl_jpeg_transcoder"]
HOSTILE = "250118_吾妻山公園_菜の花 [FINAL] 5%"
THUMB_UUID = bytes.fromhex("4e0b4016f51c473d9669bc35aebe3c16")
THUMB_MARKER = b"jxlphoto-thumbnail:v1:"
FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"thumbnail bytes" * 40 + b"\xff\xd9"

_HAVE_CODECS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
real_codecs = pytest.mark.skipif(not _HAVE_CODECS,
                                 reason="needs cjxl, djxl and exiftool on PATH")


@pytest.fixture(params=REORDER_SCRIPTS)
def mod(request):
    return importlib.import_module(request.param)


def _box(typ: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), typ) + payload


SIG = _box(b"JXL ", b"\r\n\x87\n")
FTYP = _box(b"ftyp", b"jxl \x00\x00\x00\x00jxl ")
EXIF = _box(b"Exif", b"\x00\x00\x00\x00MM\x00*" + b"\x00" * 16)
XMP = _box(b"xml ", b"<x:xmpmeta/>")
JXLC = _box(b"jxlc", b"\xff\x0a" + bytes(range(256)) * 8)
JXLP0 = _box(b"jxlp", b"\x00\x00\x00\x00" + b"\xff\x0a" + b"a" * 300)
JXLP1 = _box(b"jxlp", b"\x80\x00\x00\x01" + b"b" * 300)
OTHER = _box(b"zzzz", b"an unknown box written after the image")


def _thumb_box(fingerprint: str, jpeg: bytes = FAKE_JPEG) -> bytes:
    return _box(b"uuid", THUMB_UUID + THUMB_MARKER + fingerprint.encode("ascii")
                + b"\n" + jpeg)


def _boxes(path: Path):
    d = path.read_bytes()
    i, out = 0, []
    while i < len(d):
        size = int.from_bytes(d[i:i + 4], "big")
        out.append((d[i + 4:i + 8], d[i:i + size]))
        i += size
    return out


def _types(path: Path):
    return [t.decode("latin-1") for t, _raw in _boxes(path)]


def _hostile(tmp_path) -> Path:
    d = tmp_path / HOSTILE / "16B_JXL (2)"
    d.mkdir(parents=True)
    return d


# ===========================================================================
# B — reorder_jxl_boxes, all three copies
# ===========================================================================

def test_b1_thumbnail_before_the_codestream_stays_there(mod, tmp_path):
    """The restamp case: exiftool left its Exif after the codestream; the
    reorder moves the Exif up and must keep the uuid where it was."""
    f = _hostile(tmp_path) / "DSC_0001 (2).jxl"
    thumb = _thumb_box("1234:00ff00ff00ff00ff")
    f.write_bytes(SIG + FTYP + XMP + thumb + JXLC + EXIF)
    mod.reorder_jxl_boxes(f)
    assert _types(f) == ["JXL ", "ftyp", "Exif", "xml ", "uuid", "jxlc"], _types(f)
    assert dict((t, raw) for t, raw in _boxes(f))[b"uuid"] == thumb, "box bytes changed"


def test_b2_lossy_jxlp_file_keeps_the_thumbnail_ahead(mod, tmp_path):
    f = _hostile(tmp_path) / "50%off [b].jxl"
    thumb = _thumb_box("600:0123456789abcdef")
    f.write_bytes(SIG + FTYP + thumb + JXLP0 + JXLP1 + XMP + EXIF)
    mod.reorder_jxl_boxes(f)
    assert _types(f) == ["JXL ", "ftyp", "Exif", "xml ", "uuid", "jxlp", "jxlp"], _types(f)


def test_b3_unknown_box_after_the_codestream_stays_after(mod, tmp_path):
    """Control: what sat after the image keeps going to the end, as before."""
    f = _hostile(tmp_path) / "DSC_0002.jxl"
    f.write_bytes(SIG + FTYP + JXLC + OTHER + EXIF)
    mod.reorder_jxl_boxes(f)
    assert _types(f) == ["JXL ", "ftyp", "Exif", "jxlc", "zzzz"], _types(f)


def test_b4_every_byte_survives(mod, tmp_path):
    f = _hostile(tmp_path) / "DSC_0003.jxl"
    parts = [SIG, FTYP, XMP, _thumb_box("1:0"), JXLC, OTHER, EXIF]
    f.write_bytes(b"".join(parts))
    mod.reorder_jxl_boxes(f)
    assert sorted(raw for _t, raw in _boxes(f)) == sorted(parts)


# ===========================================================================
# F — the refresh moves a misplaced thumbnail back
# ===========================================================================

def _misplaced_master(tmp_path) -> Path:
    """A master whose thumbnail box an old restamp moved to the end: its
    fingerprint still names the codestream, so it is otherwise current."""
    f = _hostile(tmp_path) / "DSC_0001 (2).jxl"
    f.write_bytes(SIG + FTYP + EXIF + XMP + JXLC)
    fp = rec._codestream_fingerprint(f)
    assert fp
    f.write_bytes(SIG + FTYP + EXIF + XMP + JXLC + _thumb_box(fp))
    os.utime(f, (1_600_000_000, 1_600_000_000))
    return f


def _refresh_env(monkeypatch):
    monkeypatch.setattr(rec, "EXPLORER_THUMBNAIL", "on")
    monkeypatch.setattr(rec, "_verify_jxl_integrity", lambda p: True)


def test_f1_refresh_moves_a_misplaced_thumbnail_back(tmp_path, monkeypatch):
    f = _misplaced_master(tmp_path)
    _refresh_env(monkeypatch)
    thumb_before = [raw for t, raw in _boxes(f) if t == b"uuid"]
    cs_before = rec._codestream_sha256(f)
    status, detail = rec._refresh_one_thumbnail(f, dry_run=False)
    assert status == "reordered", (status, detail)
    assert "thumbnail box moved before the codestream" in detail, detail
    assert _types(f) == ["JXL ", "ftyp", "Exif", "xml ", "uuid", "jxlc"], _types(f)
    assert [raw for t, raw in _boxes(f) if t == b"uuid"] == thumb_before
    assert rec._codestream_sha256(f) == cs_before
    assert f.stat().st_mtime == 1_600_000_000, "the master's date was not kept"


def test_f2_refresh_dry_run_reports_and_touches_nothing(tmp_path, monkeypatch):
    f = _misplaced_master(tmp_path)
    _refresh_env(monkeypatch)
    before = f.read_bytes()
    status, detail = rec._refresh_one_thumbnail(f, dry_run=True)
    assert status == "reordered" and "thumbnail box moved" in detail, (status, detail)
    assert f.read_bytes() == before


def test_f3_well_placed_thumbnail_is_still_current(tmp_path, monkeypatch):
    """Control: a thumbnail already before the codestream is left alone."""
    f = _hostile(tmp_path) / "DSC_0004.jxl"
    f.write_bytes(SIG + FTYP + EXIF + XMP + JXLC)
    fp = rec._codestream_fingerprint(f)
    f.write_bytes(SIG + FTYP + EXIF + XMP + _thumb_box(fp) + JXLC)
    _refresh_env(monkeypatch)
    assert rec._refresh_one_thumbnail(f, dry_run=False) == ("current", "")


# ===========================================================================
# R — real exiftool + codecs: a restamp keeps the thumbnail in place
# ===========================================================================

@real_codecs
def test_r1_restamp_rewrite_keeps_the_thumbnail_before_the_codestream(tmp_path):
    d = _hostile(tmp_path)
    t = d / "DSC_0001 (2).tif"
    tifffile.imwrite(str(t), np.full((48, 64, 3), 1000, np.uint16), photometric="rgb")
    r = subprocess.run([sys.executable, str(Path(enc.__file__)), str(t), "--mode", "0",
                        "--distance", "0", "--effort", "3", "--no-preflight",
                        "--explorer-thumbnail", "off"],
                       capture_output=True, text=True, timeout=600,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    j = d / "DSC_0001 (2).jxl"
    enc._write_thumbnail_box(j, FAKE_JPEG, enc._codestream_fingerprint(j))
    assert _types(j).index("uuid") < _types(j).index("jxlc"), _types(j)
    old_id = enc._read_source_markers_batch([j])[str(j)]["src"]
    assert old_id, "the encoder wrote no jxlphoto-src"
    new_id = "0123456789abcdef"
    # What --restamp-moved does: drop the old record, add the new one.
    why = enc._rewrite_master_relation(
        j, [f"-XMP-dc:Relation-={enc.SRC_PREFIX}{old_id}",
            f"-XMP-dc:Relation+={enc.SRC_PREFIX}{new_id}"], new_id)
    assert why is None, why
    types = _types(j)
    assert "uuid" in types, types
    assert types.index("uuid") < types.index("jxlc"), types
