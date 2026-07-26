#!/usr/bin/env python3
"""
Tests for the 19th audit round fixes:

- read_png_to_numpy hard-fails on 16-bit RGB/RGBA when imagecodecs DECODE
  fails, not only when the import fails (no silent 16->8 bit degradation)
- reorder_jxl_boxes raises on a truncated box header instead of rewriting the
  file without the trailing bytes (which let a corrupt file pass the gate)
- the mode-8 delete gate blocks deletion when the final-path lookup misses
- _read_multipage_markers_batch keeps the markers exiftool did return when it
  exits non-zero because of one bad file in the batch
- cmd_auto honors --from-jpeg (PNGs left untouched)
"""

import argparse
import struct
import sys
import zlib
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_jpeg_transcoder as tr
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc


class _FakeRun:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _png_chunk(tag, payload):
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def _write_png_16bit_rgb(path, w=4, h=4, value=64000):
    """Minimal valid 16-bit RGB PNG (color type 2, bit depth 16)."""
    img = np.full((h, w, 3), value, dtype=">u2")
    raw = b"".join(b"\x00" + row.tobytes() for row in img)
    data = (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, 2, 0, 0, 0))
            + _png_chunk(b"IDAT", zlib.compress(raw))
            + _png_chunk(b"IEND", b""))
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------------------
# #1 imagecodecs decode failure must not degrade 16-bit to 8-bit
# ---------------------------------------------------------------------------

def test_png_16bit_hard_fails_when_imagecodecs_decode_raises(monkeypatch, tmp_path):
    """A png_decode() that raises is as unusable as a missing import: the
    IHDR guard must fire instead of falling through to PIL's 8-bit read."""
    png = _write_png_16bit_rgb(tmp_path / "16bit.png")
    dec.setup_logger()

    import imagecodecs  # noqa: F401  (skip if genuinely absent)
    monkeypatch.setattr(
        "imagecodecs.png_decode",
        lambda *a, **k: (_ for _ in ()).throw(MemoryError("boom")))

    with pytest.raises(RuntimeError, match="imagecodecs is required"):
        dec.read_png_to_numpy(png, target_depth=16)


def test_png_8bit_target_still_falls_back_when_decode_raises(monkeypatch, tmp_path):
    """The guard is 16-bit only: an 8-bit target may still use PIL."""
    png = _write_png_16bit_rgb(tmp_path / "16bit.png", value=64000)
    dec.setup_logger()

    import imagecodecs  # noqa: F401
    monkeypatch.setattr(
        "imagecodecs.png_decode",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("unsupported")))

    rgb, _alpha = dec.read_png_to_numpy(png, target_depth=8)
    assert rgb.dtype == np.uint8


# ---------------------------------------------------------------------------
# #2 truncated box header must raise, not silently drop trailing bytes
# ---------------------------------------------------------------------------

def _jxl_container(extra=b""):
    """Valid minimal JXL container: signature box + ftyp + jxlc."""
    sig = struct.pack(">I", 12) + b"JXL " + b"\r\n\x87\n"
    ftyp = struct.pack(">I", 20) + b"ftyp" + b"jxl " + b"\x00\x00\x00\x00" + b"jxl "
    payload = b"\xff\x0a\x00\x01\x02\x03"
    jxlc = struct.pack(">I", 8 + len(payload)) + b"jxlc" + payload
    return sig + ftyp + jxlc + extra


@pytest.mark.parametrize("mod", [enc, tr], ids=["encoder", "transcoder"])
def test_reorder_raises_on_truncated_box_header(mod, tmp_path):
    """1-7 trailing bytes cannot form a box header. Rewriting the file without
    them would turn a file that fails the integrity gate into one that passes,
    and mode 8 deletes the source on a pass."""
    p = tmp_path / "trailing.jxl"
    p.write_bytes(_jxl_container(extra=b"\x00\x01\x02"))
    before = p.read_bytes()

    with pytest.raises(RuntimeError, match="Truncated box header"):
        mod.reorder_jxl_boxes(p)

    # The file must be left untouched, not rewritten shorter.
    assert p.read_bytes() == before


@pytest.mark.parametrize("mod", [enc, tr], ids=["encoder", "transcoder"])
def test_reorder_still_works_on_clean_container(mod, tmp_path):
    p = tmp_path / "clean.jxl"
    p.write_bytes(_jxl_container())
    mod.reorder_jxl_boxes(p)
    out = p.read_bytes()
    assert out[4:8] == b"JXL "
    assert b"jxlc" in out
    assert len(out) == len(_jxl_container())


# ---------------------------------------------------------------------------
# #3 a bad file in the batch must not strip markers from the good ones
# ---------------------------------------------------------------------------

def test_marker_batch_keeps_results_when_exiftool_exits_nonzero(monkeypatch, tmp_path):
    good = tmp_path / "scan.jxl"
    bad = tmp_path / "locked.jxl"
    for f in (good, bad):
        f.write_bytes(b"\x00")
    dec.setup_logger()

    payload = ('[{"SourceFile": %s, "Relation": "%sGRP1"}]'
               % (repr(str(good)).replace("'", '"').replace("\\", "\\\\"),
                  dec.MULTIPAGE_MARKER_PREFIX))

    # exiftool exits 1 because of `bad`, but still prints JSON for `good`.
    monkeypatch.setattr(dec.subprocess, "run",
                        lambda *a, **k: _FakeRun(stdout=payload, returncode=1))

    markers = dec._read_multipage_markers_batch([good, bad])
    assert markers[str(good)]["group"] == "GRP1"      # not lost
    assert markers[str(bad)]["group"] is None         # falls back to standalone


def test_marker_batch_gives_up_only_when_stdout_is_empty(monkeypatch, tmp_path):
    j = tmp_path / "a.jxl"
    j.write_bytes(b"\x00")
    dec.setup_logger()
    monkeypatch.setattr(dec.subprocess, "run",
                        lambda *a, **k: _FakeRun(stdout="", returncode=1))
    markers = dec._read_multipage_markers_batch([j])
    assert markers[str(j)]["group"] is None


# ---------------------------------------------------------------------------
# #5 --from-jpeg must be honored in auto mode
# ---------------------------------------------------------------------------

def _auto_args(tmp_path, **kw):
    base = dict(
        input=tmp_path, output=None, mode=0, workers=2, effort=7,
        overwrite=False, sync=False, staging=None, dry_run=True,
        delete_source=False, no_md5=False, no_verify=False, decode=False,
        force_transcode=False, force_convert=False, format="jpeg", quality=95,
        distance=1.0, bit_depth=8, icc_profile=None, ram=True,
        output_name="converted", output_suffix="_converted",
        rename_from="", rename_to="", from_jxl=False, from_jpeg=False,
        export_subfolder=None, delete_confirm_off=False, strip=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _auto_seen_suffixes(monkeypatch, tmp_path, **kw):
    (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    seen = set()

    def fake_group(files, args, **kwargs):
        for f in files:
            seen.add(Path(f).suffix.lower())
        return []

    tr.setup_logger()
    monkeypatch.setattr(tr, "_process_file_group", fake_group)
    monkeypatch.setattr(tr, "process_group_transcode",
                        lambda files, *a, **kwargs: fake_group(files, None))

    tr.cmd_auto(_auto_args(tmp_path, **kw))
    return seen


def test_cmd_auto_without_from_jpeg_sees_both(monkeypatch, tmp_path):
    """Control: without the flag both formats are processed, so the test below
    is not passing just because nothing ran."""
    seen = _auto_seen_suffixes(monkeypatch, tmp_path)
    assert ".jpg" in seen and ".png" in seen


def test_cmd_auto_from_jpeg_ignores_pngs(monkeypatch, tmp_path):
    seen = _auto_seen_suffixes(monkeypatch, tmp_path, from_jpeg=True)
    assert ".jpg" in seen, "JPEGs must still be processed"
    assert ".png" not in seen, "--from-jpeg must leave PNGs untouched in auto mode"
