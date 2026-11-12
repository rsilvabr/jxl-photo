#!/usr/bin/env python3
"""jxl_photo_common - the helpers the jxl-photo scripts share, one copy each.

This file must sit next to the scripts. Each script imports the copy in ITS
OWN folder (`_import_common` at the top of every script), never another one
found on sys.path, and refuses to start when the versions differ.

Two kinds of functions live here (AGENTS.md, "The common module"):

- Pure helpers read nothing from the calling script. A script binds them by
  name: `reorder_jxl_boxes = _common.reorder_jxl_boxes`.
- The others take the calling script's namespace as their first parameter,
  `g` (a `Script`), and read through it at CALL time: every setting, the
  logger, `__file__`, the run state and every other toolkit helper they call
  (`g.TEMP_DIR`, `g.logger`, `g._run_captured(...)`). So the settings overlay,
  the per-run reset of the run-scoped globals and a test's monkeypatch on the
  script module all reach the code here, and the state stays per script. The
  script keeps a one-line shim with the same name:
  `def _run_captured(*a, **k): return _common._run_captured(_G, *a, **k)`.

Never define a setting here: the settings live at the top of each script.
Only the standard library is imported at the top: numpy, Pillow and tifffile
are the scripts' (a function here reaches them as g.np, g.Image, ...).
"""

import base64
import contextlib
import functools
import hashlib
import io
import json
import math
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

TOOLKIT_VERSION = "2.28.0"


class Script:
    """A script's globals, read and written at call time (never copied).

    `g.NAME` reads the script's global NAME as it is NOW; `g.NAME = v` sets
    it (what a `global NAME` statement did in the script's own copy)."""

    __slots__ = ("_g",)

    def __init__(self, namespace: dict):
        object.__setattr__(self, "_g", namespace)

    def __getattr__(self, name):
        try:
            return self._g[name]
        except KeyError:
            raise AttributeError(f"the calling script defines no {name!r}") from None

    def __setattr__(self, name, value):
        self._g[name] = value


def _script_get(g, name, default=None):
    """The script's global `name`, or `default` when it defines none."""
    return g._g.get(name, default)


# === Boxes and codestream ===

_MODULAR_LOSSY_BYTES_PER_PIXEL = 110

_STREAMING_BYTES_PER_PIXEL = 40

_THUMB_HEAD = 65536

_WHOLE_IMAGE_E7_BYTES_PER_PIXEL = 90

_WHOLE_IMAGE_E8_BYTES_PER_PIXEL = 320


def reorder_jxl_boxes(jxl_path):
    """Reorders ISOBMFF boxes so Exif comes BEFORE the codestream.
    IrfanView reads JXL boxes linearly and stops at the codestream — Exif must come first.
    Supports both lossless (single jxlc) and lossy (multiple jxlp) JXL."""
    data = jxl_path.read_bytes()
    # Payloads are views into `data`, never copies (#583): the file is held
    # in memory once, and the output is written box by box.
    # (A stand-in that only slices -- the tests' 4 GiB file -- is used as is.)
    view = memoryview(data) if isinstance(data, (bytes, bytearray)) else data
    file_size = len(data)
    
    # Sanity check: reasonable file size (prevent OOM on malformed files)
    MAX_JXL_SIZE = 4 * 1024 * 1024 * 1024  # 4GB max
    if file_size > MAX_JXL_SIZE:
        raise RuntimeError(f"JXL file too large ({file_size} bytes), skipping box reorder")
    if file_size < 12:  # Minimum valid JXL: 12-byte signature
        return  # Too small to have boxes, leave as-is

    # Bare codestream has no boxes to reorder; leave as-is.
    if data[:2] == b'\xff\x0a':
        return

    boxes = []

    i = 0
    MAX_BOX_SIZE = min(file_size, MAX_JXL_SIZE)
    
    while i < file_size:
        if i + 8 > file_size:
            # Do NOT rewrite the file with only the parsed boxes — that would
            # silently drop the trailing bytes and turn a file that fails the
            # integrity gate into one that passes it (this runs BEFORE
            # _verify_jxl_integrity, and mode 8 deletes the source on pass).
            raise RuntimeError(f"Truncated box header at offset {i}: {file_size - i} trailing byte(s)")

        size = int.from_bytes(data[i:i+4], "big")
        name = data[i+4:i+8]
        
        # Validate size to prevent integer overflow / OOM
        if size > MAX_BOX_SIZE:
            raise RuntimeError(f"Invalid JXL box size {size} at offset {i}, possible corrupted file")
        if 1 < size < 8:
            raise RuntimeError(f"Invalid JXL box size {size} at offset {i}, minimum is 8")
        
        if size == 1:
            # Extended size (64-bit)
            if i + 16 > file_size:
                raise RuntimeError(f"Truncated extended box at offset {i}: file too short for 16-byte header")
            ext_size = int.from_bytes(data[i+8:i+16], "big")
            if ext_size > MAX_JXL_SIZE:
                raise RuntimeError(f"Invalid JXL extended box size {ext_size}, possible corrupted file")
            if ext_size < 16:
                raise RuntimeError(f"Invalid JXL extended box size {ext_size}, minimum is 16")
            if i + ext_size > file_size:
                raise RuntimeError(f"Truncated extended box at offset {i}: declared {ext_size} but only {file_size - i} bytes remain")
            header, payload = data[i:i+16], view[i+16:i+ext_size]
            size = ext_size
            boxes.append((name, header, payload))
        elif size == 0:
            # Box extends to end of file
            header, payload = data[i:i+8], view[i+8:]
            boxes.append((name, header, payload))
            break
        else:
            if i + size > file_size:
                raise RuntimeError(f"Truncated box at offset {i}: declared {size} but only {file_size - i} bytes remain")
            header, payload = data[i:i+8], view[i+8:i+size]
            boxes.append((name, header, payload))
        i += size if size != 0 else file_size

    # Split boxes into groups: metadata (before codestream) and codestream
    CODESTREAM = {b"jxlc", b"jxlp"}
    META_ORDER = {b"JXL ", b"ftyp", b"jxll"}       # required structure first
    META_EXTRA = {b"Exif", b"xml ", b"jbrd", b"brob"}  # metadata before codestream
    # brob is the Brotli-compressed metadata box libjxl/exiftool may write;
    # leaving it after the codestream is exactly the bug this function exists
    # to fix (IrfanView stops reading at the codestream).

    # Group by type preserving appearance order (important for multiple jxlp)
    meta_order_boxes  = []
    meta_extra_boxes  = []
    codestream_boxes  = []
    other_boxes       = []
    pre_codestream_boxes = []

    for name, h, p in boxes:
        if name in META_ORDER:
            meta_order_boxes.append((name, h, p))
        elif name in META_EXTRA:
            meta_extra_boxes.append((name, h, p))
        elif name in CODESTREAM:
            codestream_boxes.append((name, h, p))
        elif codestream_boxes:
            other_boxes.append((name, h, p))
        else:
            # E-G (2026-11-03 audit): an unknown box that sat BEFORE the
            # codestream stays before it — the Explorer thumbnail (uuid) box is
            # put there so a reader finds it without crossing the image data.
            # (jxl-thumb reads every box, so Explorer showed it either way; a
            # reader that stops at the codestream would not.)
            pre_codestream_boxes.append((name, h, p))

    # Exif before the XMP: IrfanView shows no EXIF when the xml box comes
    # first. Since v2.17.0 the encoder writes the source's XMP block in its
    # own exiftool call BEFORE the EXIF (_preload_xmp_block), so the Exif box
    # arrived second (#561). Stable sort: every other box keeps its order
    # (jbrd reconstruction pairs boxes by type, never across types).
    meta_extra_boxes.sort(key=_exif_box_rank)

    # Final order: structure -> metadata -> codestream -> others
    ordered = (meta_order_boxes + meta_extra_boxes + pre_codestream_boxes
               + codestream_boxes + other_boxes)

    # A box that declared size 0 ("extends to EOF") is only valid as the LAST
    # box in the file. If regrouping moved it earlier, rewrite its header with
    # the real computed size, otherwise everything after it becomes payload
    # and the file is corrupt.
    parts = []
    for idx, (name, h, p) in enumerate(ordered):
        declared = int.from_bytes(h[0:4], "big")
        if declared == 0 and idx < len(ordered) - 1:
            real_size = 8 + len(p)
            try:
                h = real_size.to_bytes(4, "big") + h[4:8]
            except OverflowError:
                # A file just under 4 GiB whose trailing size-0 box spans
                # most of it computes a real_size that no longer fits the
                # 32-bit field — report it like the truncation guards above,
                # not as an uncaught traceback.
                raise RuntimeError(
                    f"Cannot re-header size-0 box {name!r}: real size {real_size} "
                    f"exceeds the 32-bit box size field") from None
        parts.append((h, p))

    # Every header is settled before the file is opened: a box that cannot
    # be re-headered above raises with the file untouched.
    with open(jxl_path, "wb") as f:
        for h, p in parts:
            f.write(h)
            f.write(p)


def _exif_box_rank(box) -> int:
    """Sort key of reorder_jxl_boxes: 0 for an Exif box (plain, or brob
    holding one), 1 for the rest."""
    name, _header, payload = box
    return 0 if name == b"Exif" or (name == b"brob" and payload[:4] == b"Exif") else 1


def _jxl_boxes(f, end):
    """(type, box start, header size, box size) of each top-level box, read
    with seeks from an open file. Stops at the first malformed header."""
    out, pos = [], 0
    while pos + 8 <= end:
        f.seek(pos)
        size, typ = struct.unpack(">I4s", f.read(8))
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr or pos + size > end:
            break
        out.append((typ, pos, hdr, size))
        pos += size
    return out


def has_jbrd_box(jxl_path: Path) -> bool:
    """Check if JXL has jbrd (JPEG Bitstream Reconstruction Data) box.
    Returns True if this JXL can be losslessly transcoded back to JPEG.
    Parses ISOBMFF boxes sequentially until jbrd is found or EOF, so files
    with large metadata headers before jbrd are detected correctly.
    """
    try:
        with open(jxl_path, 'rb') as f:
            header = f.read(12)

            if header[:2] == b'\xff\x0a':  # Bare codestream
                return False
            if header[:12] != b'\x00\x00\x00\x0cJXL \x0d\x0a\x87\x0a':
                return False

            while True:
                box_header = f.read(8)
                if len(box_header) < 8:
                    return False

                size = int.from_bytes(box_header[:4], 'big')
                box_type = box_header[4:8]

                if box_type == b'jbrd':
                    return True

                if size == 0:  # Box extends to end of file
                    return False
                elif size == 1:  # Extended 64-bit size
                    ext_size = f.read(8)
                    if len(ext_size) < 8:
                        return False
                    size = int.from_bytes(ext_size, 'big')
                    if size < 16:
                        return False
                    payload = size - 16
                else:
                    if size < 8:
                        return False
                    payload = size - 8

                if payload > 0:
                    f.seek(payload, 1)
        return False
    except Exception:
        return False


def _codestream_head(jxl_path, n=64):
    """The first `n` bytes of a JXL's codestream - a bare codestream, or the
    jxlc / jxlp boxes of a container (a jxlp payload without its 4-byte
    index) - or None when the file is not a JXL or cannot be read."""
    try:
        with open(jxl_path, "rb") as f:
            first = f.read(12)
            if first[:2] == b"\xff\x0a":
                return (first + f.read(max(0, n - len(first))))[:n]
            if first != b"\x00\x00\x00\x0cJXL \r\n\x87\n":
                return None
            out = b""
            while len(out) < n:
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                size, kind = struct.unpack(">I", hdr[:4])[0], hdr[4:8]
                head_len = 8
                if size == 1:
                    ext = f.read(8)
                    if len(ext) < 8:
                        break
                    size, head_len = struct.unpack(">Q", ext)[0], 16
                left = None if size == 0 else size - head_len
                if left is not None and left < 0:
                    return None
                if kind in (b"jxlc", b"jxlp"):
                    if kind == b"jxlp":
                        if left is not None and left < 4:
                            return None
                        f.seek(4, 1)
                        left = None if left is None else left - 4
                    want = n - len(out) if left is None else min(left, n - len(out))
                    data = f.read(want)
                    out += data
                    if kind == b"jxlc" or left is None:
                        break
                    f.seek(left - len(data), 1)
                    continue
                if left is None:
                    break
                f.seek(left, 1)
            return out or None
    except OSError:
        return None


def _header_orientation(jxl_path):
    """The orientation (1-8) in a JXL codestream's ImageMetadata, read in
    Python from its first bytes (ISO/IEC 18181-1: the signature, SizeHeader,
    then all_default, extra_fields and the 3-bit orientation), or None when
    the file cannot be parsed. Needs no jxlinfo (#636)."""
    data = _codestream_head(jxl_path)
    if not data or data[:2] != b"\xff\x0a":
        return None
    bits, total, pos = int.from_bytes(data, "little"), len(data) * 8, 16

    def take(k):
        nonlocal pos
        if pos + k > total:
            raise ValueError("JXL header cut short")
        v = (bits >> pos) & ((1 << k) - 1)
        pos += k
        return v

    try:
        small = take(1)
        if small:
            take(5)                                  # height / 8 - 1
        else:
            take((9, 13, 18, 30)[take(2)])           # height - 1 (U32)
        if take(3) == 0:                             # no fixed aspect ratio
            if small:
                take(5)
            else:
                take((9, 13, 18, 30)[take(2)])
        if take(1) or not take(1):                   # all_default / no extra_fields
            return 1
        return take(3) + 1
    except ValueError:
        return None


def _codestream_orientation(info_text):
    """The orientation (1-8) the JXL codestream carries, or None."""
    m = re.search(r"^Orientation:\s*([1-8])\b", info_text or "", re.M)
    return int(m.group(1)) if m else None


def _codestream_fingerprint(jxl_path):
    """'<codestream bytes>:<FNV-1a 64 of its first 64 KiB, 16 hex digits>', or
    None when the file is not a JPEG XL. The codestream is the whole file for
    a bare one, else the jxlc box or the jxlp boxes joined without their
    4-byte index — never the metadata boxes around it."""
    try:
        with open(jxl_path, "rb") as f:
            end = f.seek(0, 2)
            f.seek(0)
            if f.read(2) == b"\xff\x0a":
                f.seek(0)
                length, head = end, f.read(_THUMB_HEAD)
            else:
                length, head, found = 0, b"", False
                for typ, pos, hdr, size in _jxl_boxes(f, end):
                    skip = {b"jxlc": 0, b"jxlp": 4}.get(typ)
                    if skip is None or size - hdr < skip:
                        continue
                    found = True
                    payload = size - hdr - skip
                    length += payload
                    if len(head) < _THUMB_HEAD:
                        f.seek(pos + hdr + skip)
                        head += f.read(min(payload, _THUMB_HEAD - len(head)))
                if not found:
                    return None
    except (OSError, struct.error):
        return None
    h = 0xcbf29ce484222325
    for b in head:
        h = ((h ^ b) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return f"{length}:{h:016x}"


def _cjxl_whole_image(distance, effort, buffering=None):
    """True when cjxl encodes the whole image at once instead of streaming
    (libjxl 0.12 with buffering left to the encoder: effort 10+, effort 8-9
    above distance 0.5, effort 7 from distance 3; --buffering 0 always,
    1-3 never)."""
    if buffering is not None:
        return buffering == 0
    return (effort >= 10 or (effort in (8, 9) and distance > 0.5)
            or (effort == 7 and distance >= 3))


def _cjxl_bytes_per_pixel(distance, effort, buffering=None, modular=False):
    """Estimated peak bytes per pixel of one cjxl process."""
    if modular and distance > 0:
        return _MODULAR_LOSSY_BYTES_PER_PIXEL
    if _cjxl_whole_image(distance, effort, buffering):
        return (_WHOLE_IMAGE_E8_BYTES_PER_PIXEL if effort >= 8
                else _WHOLE_IMAGE_E7_BYTES_PER_PIXEL)
    return _STREAMING_BYTES_PER_PIXEL


# === ICC and PNG ===

ICC_PROFILE_PREFIX = "jxlphoto-iccprofile:"


def _carried_icc(creator, tokens):
    """The original ICC profile a toolkit JXL carries in XMP, or None: the
    jxlphoto-iccprofile token in dc:Relation first, else the ICC:<base64>
    segment of CreatorTool (where JXLs written up to v2.18.x keep it). Same
    validation for both: strict base64, >= 128 bytes, 'acsp' at offset 36.
    Parity-pinned (decoder + transcoder + recompressor)."""
    for token in tokens or ():
        token = str(token).strip()
        if not token.startswith(ICC_PROFILE_PREFIX):
            continue
        try:
            data = base64.b64decode(token[len(ICC_PROFILE_PREFIX):], validate=True)
        except Exception:
            continue
        if len(data) >= 128 and data[36:40] == b"acsp":
            return data
    return _xmp_icc_from_creator_tool(creator)


def _xmp_icc_from_creator_tool(text: str):
    """The encoder's ICC:<base64> segment of CreatorTool, validated — the same
    rules as jxl_tiff_decoder.extract_icc_from_xmp (split on '|', 'ICC:'
    prefix, strict base64, >= 128 bytes, 'acsp' at 36)."""
    for segment in (text or "").split("|"):
        segment = segment.strip()
        if not segment.startswith("ICC:"):
            continue
        try:
            data = base64.b64decode(segment[4:].strip(), validate=True)
        except Exception:
            continue
        if len(data) >= 128 and data[36:40] == b"acsp":
            return data
    return None


def _icc_a2b_only(icc) -> bool:
    """Can this ICC profile NOT be a faithful conversion TARGET?

    True for a profile with A2B tables and no B2A — an input (scanner)
    profile such as the film scanner's SFprofT. It is a fine SOURCE (A2B0
    takes its pixels to the PCS), but converting INTO it makes LittleCMS fall
    back to the matrix/TRC, which is not the inverse of A2B0: a real scan came
    back at 27 dB instead of ~49. A profile with B2A tables, or a plain
    matrix/TRC one, is invertible. Unparsable -> False (no claim made).
    """
    try:
        if not icc or len(icc) < 132:
            return False
        n = struct.unpack(">I", bytes(icc[128:132]))[0]
        if n > 1000 or len(icc) < 132 + 12 * n:
            return False
        sigs = {bytes(icc[132 + 12 * i:136 + 12 * i]) for i in range(n)}
    except Exception:
        return False
    has_a2b = bool(sigs & {b"A2B0", b"A2B1", b"A2B2"})
    has_b2a = bool(sigs & {b"B2A0", b"B2A1", b"B2A2"})
    return has_a2b and not has_b2a


def _djxl_icc_args(tmp_dir: Path) -> tuple:
    """(extra djxl args, out_icc_path, orig_icc_path) that make ONE djxl call
    also report which colour space it decoded to and what the file's own is."""
    out_icc = Path(tmp_dir) / "djxl_out.icc"
    orig_icc = Path(tmp_dir) / "djxl_orig.icc"
    for p in (out_icc, orig_icc):
        try:
            p.unlink()
        except OSError:
            pass
    return ([f"--icc_out={out_icc}", f"--orig_icc_out={orig_icc}"], out_icc, orig_icc)


def _icc_profile_token(icc_bytes: bytes) -> str:
    """The dc:Relation token that carries an ICC profile byte for byte.
    Parity-pinned (encoder + recompressor)."""
    return ICC_PROFILE_PREFIX + base64.b64encode(icc_bytes).decode("ascii")


def _png_iccp(png_path: Path):
    """The ICC profile in a PNG's iCCP chunk (decompressed), or None. Reads
    chunk headers only up to the first IDAT."""
    import zlib
    try:
        with open(png_path, "rb") as f:
            if f.read(8) != b"\x89PNG\r\n\x1a\n":
                return None
            while True:
                head = f.read(8)
                if len(head) < 8:
                    return None
                n = struct.unpack(">I", head[:4])[0]
                t = head[4:8]
                if t == b"IDAT":
                    return None
                if t == b"iCCP":
                    data = f.read(n)
                    sep = data.index(b"\x00")
                    return zlib.decompress(data[sep + 2:])
                f.seek(n + 4, 1)
    except Exception:
        return None


def _png_has_alpha(png_path: Path) -> bool:
    """True when the PNG djxl just wrote has an alpha channel (colour type 4
    or 6). Only the 26-byte signature + IHDR is read."""
    try:
        with open(png_path, "rb") as f:
            head = f.read(26)
    except OSError:
        return False
    if len(head) < 26 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return False
    return head[25] in (4, 6)          # 4 = grey + alpha, 6 = RGBA


def _png_is_grayscale(png_path: Path) -> bool:
    """True when the PNG djxl just wrote is single-channel (colour type 0 or 4).

    Only the 26-byte signature + IHDR is read: this runs per file, and the
    decoded intermediate of a 93 MP scan is hundreds of MB.
    """
    try:
        with open(png_path, "rb") as f:
            head = f.read(26)
    except OSError:
        return False
    if len(head) < 26 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return False
    return head[25] in (0, 4)          # 0 = grey, 4 = grey + alpha


def _png_chunk_types(png_path: Path) -> list:
    """Chunk types before the first IDAT (iCCP/sRGB must precede it per the PNG
    spec) — reads a few KB, not the whole 16-bit intermediate."""
    types = []
    with open(png_path, "rb") as f:
        if f.read(8) != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError(f"not a PNG: {png_path.name}")
        while True:
            head = f.read(8)
            if len(head) < 8:
                break
            n = struct.unpack(">I", head[:4])[0]
            t = head[4:8].decode("latin-1")
            if t == "IDAT":
                break
            types.append(t)
            f.seek(n + 4, 1)                         # data + CRC
    return types


def _png_size(png_path: Path):
    """(width, height) from the IHDR of a PNG djxl just wrote."""
    with open(png_path, "rb") as f:
        head = f.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise RuntimeError(f"not a PNG: {png_path}")
    return struct.unpack(">II", head[16:24])


def _decoded_in_original_space(out_icc: Path, orig_icc: Path):
    """True when djxl returned the pixels in the file's own colour space
    (native colour, lossless, or an encoder 'skip' file): pasting/assigning
    that profile is correct. False for a LOSSY file carrying an ICC blob:
    djxl then returns LINEAR sRGB and the pixels must be CONVERTED, from a
    float decode. None when djxl did not write the profiles: nothing then
    proves the pixels kept the file's own colour space, and pasting the
    original ICC on a lossy ICC-blob decode is exactly the linear-sRGB trap
    (round 43, D-5) — the decoder fails CLOSED on it."""
    try:
        a, b = out_icc.read_bytes(), orig_icc.read_bytes()
    except OSError:
        return None
    return a == b
