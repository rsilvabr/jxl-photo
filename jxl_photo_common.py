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


# === Paths and folder names ===

_COLLAPSING_MODES = frozenset({2, 4, 5, 6, 7})

_LONG_PATH_THRESHOLD = 240


def _long_codec_paths(cmd):
    """`cmd` with every long absolute path argument of cjxl/djxl/jxlinfo given the
    Windows extended-length prefix (Windows only; otherwise unchanged)."""
    if os.name != "nt" or not cmd:
        return cmd
    exe = os.path.basename(str(cmd[0])).lower()
    if not exe.startswith(("cjxl", "djxl", "jxlinfo")):
        return cmd
    out = [cmd[0]]
    for a in cmd[1:]:
        s = str(a) if isinstance(a, (str, os.PathLike)) else None
        if s is None or s.startswith("-") or s.startswith("\\\\?\\"):
            out.append(a)
            continue
        if not os.path.isabs(s):
            # A relative path is long by its ABSOLUTE form (#575: an input
            # given relative to a deep working folder). Only a token that
            # looks like a path counts: a separator or an extension, no '='
            # and not a number — option values (-d 0.1, --effort 7,
            # -x strip=xmp) are never paths (261104 review, M1).
            looks_like_path = (
                "=" not in s
                and (os.sep in s or (os.altsep and os.altsep in s)
                     or bool(os.path.splitext(s)[1])))
            try:
                float(s)
                looks_like_path = False
            except ValueError:
                pass
            if not looks_like_path:
                out.append(a)
                continue
        s = os.path.abspath(s)
        if len(s) < _LONG_PATH_THRESHOLD:
            out.append(a)
            continue
        out.append("\\\\?\\UNC\\" + s[2:] if s.startswith("\\\\") else "\\\\?\\" + s)
    return out


def _magick_out_path(path) -> str:
    """`path` as an ImageMagick OUTPUT operand. ImageMagick formats the name
    it writes like printf does (%d, %o, %x: the scene number), so a folder
    named "100%off" became "1000ff" and the write failed with "No such file
    or directory" (#647). A doubled percent sign is a literal one. Inputs are
    not formatted and must not be escaped. Parity-pinned (four backends)."""
    return str(path).replace("%", "%%")


def _ansi_encodable(text) -> bool:
    """Whether a program that reads its command line in the ANSI code page
    gets `text` intact. Always True off Windows and with the UTF-8 code page."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        acp = ctypes.windll.kernel32.GetACP()
    except Exception:
        return True
    if acp == 65001:
        return True
    try:
        str(text).encode(f"cp{acp}")
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def _path_excluded_below_root(file_path: Path, root, names) -> bool:
    """True when any folder segment of file_path BELOW root matches one
    of `names` (lower-cased). Relative to root, segment match,
    fail-closed."""
    if not names or root is None:
        return False
    try:
        rel = file_path.parent.relative_to(root)
    except ValueError:
        try:
            rel = file_path.parent.resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            rel = file_path.parent  # fail closed, like _is_tool_output_path
    return any(seg.lower() in names for seg in rel.parts)


def _work_dir_problem(path, label, dry_run, dry_run_uses_it=False):
    """Why a work folder (TEMP_DIR, staging) cannot be used, or None. A real
    run creates it. A DRY RUN creates nothing (a simulation that leaves new
    folders on disk is not a simulation): a path that exists but is not a
    folder is refused, and a folder the dry run itself writes into
    (dry_run_uses_it: TEMP_DIR holds the exiftool argfiles and the captured
    output of every planning read) must already exist, or every marker read
    fails and a good archive is refused as unreadable."""
    if path is None:
        return None
    p = Path(path)
    try:
        if dry_run:
            if p.exists() and not p.is_dir():
                return f"{label} is not a directory: {path}"
            if dry_run_uses_it and not p.is_dir():
                return (f"{label} does not exist: {path}. A dry run creates no "
                        f"folders: create it, or run without --dry-run (a real "
                        f"run creates it).")
            return None
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"{label} is not usable: {path} ({e})"
    return None


def _source_path_id(src_path) -> str:
    """Stable id for a source's LOCATION. Free to compute."""
    norm = os.path.normcase(os.path.abspath(str(src_path)))
    return hashlib.sha256(norm.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _run_collapses_structure(mode, output_arg, source_root) -> bool:
    """Does THIS run drop the source's folder from the output path?

    _COLLAPSING_MODES holds the modes that always do. Mode 0 also does, but only
    when an output folder was given: every file then lands in that one folder,
    flat, exactly like mode 2 — so a second run over a DIFFERENT source folder
    writes the same names into it, and with --delete-source it overwrites the
    first archive after that archive's own source is already gone. Mode 0 is
    flat, so _abort_on_duplicate_outputs never sees this: the recorded marker is
    the only defence there is.

    Mode 0 IN PLACE (no output folder) is NOT collapsing and must not be treated
    as one: demanding a marker there would refuse existing archives that can
    never collide, which is the dead end #271 exists to avoid.
    """
    if mode in _COLLAPSING_MODES:
        return True
    if mode != 0 or not output_arg:
        return False
    try:
        return (os.path.normcase(os.path.abspath(str(output_arg)))
                != os.path.normcase(os.path.abspath(str(source_root))))
    except (OSError, ValueError):
        return True     # cannot tell -> assume it collapses (fail closed)


# Backport of Path.is_relative_to for Python < 3.9
def _is_relative_to(path: Path, anchor: Path) -> bool:
    """Backport of Path.is_relative_to for Python < 3.9."""
    try:
        path.relative_to(anchor)
        return True
    except ValueError:
        return False


def _marker_matches(part_lower: str, marker_lower: str) -> bool:
    """Folder-name part matches the export marker.

    Matches start/end with the marker (e.g. _EXPORT, _Export_2024, My_EXPORT)
    and, for underscore-wrapped markers, also the bare word — so the default
    '_EXPORT' also detects 'Export_Lightroom' and 'Lightroom_Export', as the
    documentation promises.
    """
    # startswith needs a token boundary after the marker, otherwise '_EXPORTS'
    # (a backup folder, different thing) would match the '_EXPORT' marker.
    # endswith is inherently safe: the marker's own leading underscore anchors it.
    if not marker_lower:
        # An empty marker matches nothing: without this, endswith("") is True
        # and marker_lower[0] raises IndexError. Fail closed — a run without a
        # marker must not silently treat every folder as an anchor.
        return False
    if part_lower.startswith(marker_lower):
        rest = part_lower[len(marker_lower):]
        if not rest or rest[0] in '_- ':
            return True
    if part_lower.endswith(marker_lower):
        s = len(part_lower) - len(marker_lower)
        # endswith also needs a left anchor: either the marker brings its own
        # (a leading underscore, as in the default '_EXPORT') or the name
        # must boundary it — otherwise marker 'EXPORT' would match 'ReExport'.
        if s == 0 or marker_lower[0] in '_- ' or part_lower[s - 1] in '_- ':
            return True
    bare = marker_lower.strip('_')
    if not bare or bare == marker_lower:
        return False
    # The bare word must be a complete TOKEN at the START or END of the name
    # (bounded by _, -, space, or the string edges). This keeps the documented
    # cases (Export_Lightroom, Lightroom_Export, My_EXPORT) while rejecting
    # 'exports', 'EXPORTED_RAWS', 'reexport' — and mid-name tokens like
    # 'backup_export_old'.
    import re as _re
    if part_lower.startswith(bare):
        e = len(bare)
        if e == len(part_lower) or part_lower[e] in '_- ':
            return True
    if part_lower.endswith(bare):
        s = len(part_lower) - len(bare)
        if s == 0 or part_lower[s - 1] in '_- ':
            return True
    return False


def _replace_suffix_token(name: str, suffix_from: str, suffix_to: str) -> str:
    """Replace the FIRST occurrence of suffix_from in a folder name, but only
    when it is a complete token (bounded by _, -, space, or string edges) —
    otherwise 'MyJXLArchive' would become 'MyTIFFArchive'. No token match
    returns the name unchanged (caller applies the append fallback).
    """
    import re as _re
    pat = _re.compile(
        _re.escape(suffix_from) + r'(?=$|[_\- ])', _re.IGNORECASE)
    # A non-token match must NOT stop the search: in 'MyTIFF_TIFF' the
    # embedded 'TIFF' fails the left-boundary test, but the trailing '_TIFF'
    # is a valid token and gets replaced.
    for m in pat.finditer(name):
        if m.start() == 0 or name[m.start() - 1] in '_- ':
            return name[:m.start()] + suffix_to + name[m.end():]
    return name


def _argfile_safe(value) -> str:
    """Sanitize a value for inclusion in an exiftool argfile (-@).

    Argfiles are parsed one argument per line, so embedded newlines in XMP
    text (multi-line captions, etc.) would split one argument into several
    bogus ones. Collapse all CR/LF runs into a single space.
    """
    return re.sub(r"[\r\n]+", " ", str(value))


def _stderr_tail(stderr, limit=200):
    """The LAST `limit` characters of a tool's stderr, CRLF folded, for an
    error message. The head is the wrong end: cjxl prints its version banner
    and an "Encoding [...]" line first, which filled the old [:200] slice — 97
    of the 314 errors of the 2026-10-04 run were logged without the line that
    said what failed."""
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    return (stderr or "").replace("\r\n", "\n").strip()[-limit:]


# === Encode record and lineage ===

ORIGIN_PREFIX = "jxlphoto-origin:"

_ENCODE_TAG_RE = re.compile(r"cjxl\s+d=([0-9.]+)\s+e=(\d+)")

_GEN_TAG_RE = re.compile(r"(?:^|\|)\s*gen=(\d+)\s*(?=\||$)")

_MACHINE_BLOCK_RE = re.compile(
    r"(?:^|\|)\s*"
    r"(?:gen=\d+\s*\|\s*)?"
    r"cjxl\s+d=[0-9.]+\s+e=\d+"
    r"(?:\s*\|\s*cjxl\s+d=[0-9.]+\s+e=\d+)*"
)

_NO_RECORD_SEGMENT = "no encode record"

_TOOLKIT_MARKER_NAMESPACE = "jxlphoto-"


def _append_encode_entry(text: str, new_d, new_e):
    """(new_text, stored, counted_before, orphans): append "cjxl d=new_d
    e=new_e" to the field's chain and rewrite the gen= token at the head of
    the machine block.

    Append-only is the design: the chain is the lineage history, and
    replacing it would erase the generation count that --on-regeneration
    guards. The user's own text (a caption) stays FIRST — dc:Description is
    visible in Windows Properties and machine output must not push a caption
    behind it. gen is reconciled over the final chain (never incremented):
    a wrong value self-corrects on the next pass. `stored`/`counted_before`
    are returned so the caller can log a divergence (once per run); `orphans`
    comes from _strip_encode_params.

    Chain entries are read back from the MACHINE BLOCKS only: a caption that
    merely contains "cjxl d=1 e=7" is user text and is not absorbed into the
    chain (and not counted as a generation).
    """
    s = str(text)
    _gen_old, stored, counted = _reconcile_gen(s)
    user, orphans = _strip_encode_params(s)
    entries = []
    for block in _MACHINE_BLOCK_RE.findall(s):
        entries.extend(f"cjxl d={d} e={e}"
                       for d, e in _ENCODE_TAG_RE.findall(block))
    entries.append(f"cjxl d={new_d} e={new_e}")
    counted_new = counted
    try:
        if float(new_d) > 0:
            counted_new += 1
    except (TypeError, ValueError):
        pass
    gen = max(stored, counted_new)
    block = " | ".join([f"gen={gen}"] + entries)
    return (f"{user} | {block}" if user else block), stored, counted, orphans


def _reconcile_gen(text: str):
    """(gen, stored, counted) for the field — gen is DERIVED, never incremented.

    counted = number of "cjxl d=X" chain entries with X > 0 (a d=0 lossless
    entry is appended to the chain but costs no quality, so it is not a
    generation). stored = the LARGEST gen= token, 0 when absent or unparseable
    (a corrupt field carrying several self-corrects upward — undercounting is
    the damaging direction).
    gen = max(stored, counted): if chain entries were removed the stored gen
    is the better number, if entries were added without updating gen the
    count is better — undercounting generations is the error that causes
    damage, so the larger value wins in both directions. A malformed chain
    entry never stops the rest from being counted. Legacy fields (chain, no
    gen=) need no special case: max(0, counted) == counted.

    Only MACHINE-BLOCK segments count: a caption merely containing
    "cjxl d=1 e=7" is user text, not an encode record, and must not raise the
    generation count.
    """
    s = str(text)
    stored = max((int(v) for v in _GEN_TAG_RE.findall(s)), default=0)
    counted = 0
    for block in _MACHINE_BLOCK_RE.findall(s):
        for d, _e in _ENCODE_TAG_RE.findall(block):
            try:
                if float(d) > 0:
                    counted += 1
            except ValueError:
                continue  # malformed entry (e.g. d=1.2.3): skip, keep counting
    return max(stored, counted), stored, counted


def _strip_encode_params(text: str):
    """(cleaned_text, orphans) with the machine block removed.

    Removes the gen= token and the WHOLE cjxl d=/e= chain (one unit) from a
    " | "-joined metadata string, keeping any unrelated text (an original
    caption, a real CreatorTool). Orphaned gen= segments — left behind by a
    corrupted or hand-edited field — are removed ONLY as whole segments and
    ONLY at the tail (the machine-block region): "Project gen=3 phase 2" and
    "gen=2 | My caption" are user text and stay. `orphans` counts how many
    were stripped so the caller can log it: an orphan means something wrote
    the field badly earlier.
    """
    cleaned = _MACHINE_BLOCK_RE.sub("", str(text))
    parts = [p.strip() for p in cleaned.split("|")]
    parts = [p for p in parts if p]
    orphans = 0
    while parts and re.fullmatch(r"gen=\d+", parts[-1]):
        parts.pop()
        orphans += 1
    return " | ".join(parts), orphans


def _history_record(values) -> str:
    """The encode record carried by xmpMM:History, or "" — pure.

    `values` is exiftool's HistoryParameters: a str (one entry), a list, or
    None. Since v2.24.0 every write appends one History entry whose
    parameters END with the whole record ("jxl_tiff_encoder: from image/tiff
    to image/jxl | gen=1 | cjxl d=0.05 e=9"). The newest entry that carries
    one wins; an entry with a "no encode record" segment (--encode-tag off)
    stops the walk first; entries with neither (a decode, another program's
    entry, a version before v2.24.0) are skipped.
    """
    if values is None:
        return ""
    items = values if isinstance(values, list) else [values]
    for text in reversed([str(v) for v in items]):
        if any(seg.strip() == _NO_RECORD_SEGMENT for seg in text.split("|")):
            return ""
        blocks = _MACHINE_BLOCK_RE.findall(text)
        if blocks:
            return " | ".join(b.strip().lstrip("|").strip() for b in blocks)
    return ""


def _origin_for_output(tokens, has_record, fresh):
    """(value, inherited) for the jxlphoto-origin token of a new output.

    tokens: the INPUT's dc:Relation items, or None when unreadable.
    has_record: the input carries a gen=/cjxl lineage record.
    fresh: the origin this script assigns to a clean input.
    An inherited origin wins; an input this toolkit already touched without
    one (an older version) stays unknown; only a clean input gets `fresh`."""
    if tokens is None:
        return None, False
    for t in tokens:
        if t.startswith(ORIGIN_PREFIX) and len(t) > len(ORIGIN_PREFIX):
            return t[len(ORIGIN_PREFIX):], True
    if has_record or any(t.startswith(_TOOLKIT_MARKER_NAMESPACE) for t in tokens):
        return None, False
    return fresh, False


def _merge_lineage_blocks(desc: str, software: str, history: str = ""):
    """Merge the fields' machine blocks: (entries, stored_gen).

    `history` (v2.24.0) is the record xmpMM:History carries
    (_history_record): the newest home of the record. The three are folded
    in age order, desc -> software -> history, with the rule below applied
    pairwise; with history="" the result is the two-field merge's.

    The record can be SPLIT across dc:Description and Software (the user
    switched --encode-tag, a file came through a tool that keeps only one
    field, or an older version left a shadow copy behind). Whichever side
    holds it, the chain must survive the restamp.

    Entries are NEVER deduplicated inside one field: "cjxl d=0.1 e=7 | cjxl
    d=0.1 e=7" is two real generations (decode, then re-encode at the same
    settings — exactly what the append-only chain exists to record), and
    collapsing them undercounts gen in the damaging direction. Across the two
    fields:
      * the same chain in both (a mirror), or one a prefix of the other (a
        copy that was later extended), is ONE history -> the longer one;
      * anything else is split history -> both, dc:Description first (the
        older side in every case this toolkit produces: a decoder-made TIFF
        carries the JXL's chain there, and the new encode lands in Software).
    stored gen = max of ALL gen= tokens in both fields (_GEN_TAG_RE.findall,
    like _reconcile_gen — a repeated gen= token is a real generation, never
    deduped, and reading only the first one made a restamp rewrite a gen=5
    file back down to gen=1). Entries inside a caption's running
    text never match: the block regex only matches whole " | "-delimited
    segments.
    """
    def _chain(text):
        chain = []
        for block in _MACHINE_BLOCK_RE.findall(str(text)):
            chain.extend(_ENCODE_TAG_RE.findall(block))
        return chain

    def _union(a, b):
        if not a or b[:len(a)] == a:
            return list(b) if len(b) >= len(a) else list(a)
        if not b or a[:len(b)] == b:
            return list(a)
        return a + b

    entries = _union(_union(_chain(desc), _chain(software)), _chain(history))
    stored = 0
    for text in (desc, software, history):
        # findall + max per field: a field can carry SEVERAL gen= tokens (a
        # badly merged/written field) and search() reads only the FIRST —
        # "gen=1 | cjxl d=0.1 e=7 | gen=5 | cjxl d=1.0 e=7" would store 1 and
        # the restamp would erase gen=5, undercounting gen in the damaging
        # direction. Same fix _reconcile_gen got (round 38).
        gen_tokens = _GEN_TAG_RE.findall(str(text))
        if gen_tokens:
            stored = max(stored, max(int(v) for v in gen_tokens))
    return entries, stored


def _collapse_list_reset(lines, tag="-XMP-dc:Relation"):
    """exiftool lines with `tag=` + `tag+=v...` turned into `tag=v...`.

    In ONE exiftool call, any `+=` on a list tag puts every assignment to it in
    add mode: `-X=` then `-X+=v` keeps the values the FILE already holds and
    appends v. Only all-`=` assignments replace the list. While the XMP came
    in through -tagsfromfile in that same call, the reset worked (a later
    assignment overrides a copied tag); now that the source's XMP is written
    into the file as a block first, the old list would survive — stale
    jxlphoto-src/srcsum on a derivative, doubled markers. Lines without the
    bare reset are returned unchanged (add mode is what they ask for).
    Parity-pinned (encoder + recompressor).
    """
    reset, add = tag + "=", tag + "+="
    if reset not in lines:
        return list(lines)
    out, values, slot = [], [], None
    for ln in lines:
        if ln == reset or ln.startswith(add):
            if slot is None:
                slot = len(out)
                out.append(None)
            if ln.startswith(add):
                values.append(ln[len(add):])
            continue
        out.append(ln)
    return out[:slot] + ([reset + v for v in values] or [reset]) + out[slot + 1:]


# === Settings overlay reader ===


def _overlay_value(spec, value):
    """`value` (from JSON) checked against an _OVERLAY_SETTINGS spec:
    (True, the value to use) or (False, what it should have been)."""
    kind = spec[0]
    if kind == "choice":
        for c in spec[1:]:
            if value == c and type(value) is type(c):
                return True, c
        return False, "one of " + ", ".join(json.dumps(c) for c in spec[1:])
    if kind == "bool":
        return (True, value) if isinstance(value, bool) else (False, "true or false")
    if kind in ("int?", "float?"):
        if value is None:
            return True, None
        spec = (kind[:-1],) + tuple(spec[1:])
        kind = spec[0]
    if kind in ("int", "float"):
        lo, hi = spec[1], spec[2]
        number = (isinstance(value, int) if kind == "int"
                  else isinstance(value, (int, float))) and not isinstance(value, bool)
        if number and (lo is None or value >= lo) and (hi is None or value <= hi):
            return True, (float(value) if kind == "float" else value)
        what = "a whole number" if kind == "int" else "a number"
        if lo is not None and hi is not None:
            what += f" from {lo} to {hi}"
        elif lo is not None:
            what += f" of at least {lo}"
        return False, what
    if kind in ("name", "name?"):
        if isinstance(value, str):
            v = value.strip()
            if not v and kind == "name?":
                return True, v
            if v and v not in (".", "..") and not any(ch in v for ch in '/\\:*?"<>|'):
                return True, v
        return False, "a folder or file name (no path separators)"
    if kind == "path?":
        if value is None:
            return True, None
        if isinstance(value, str) and value.strip():
            return True, value.strip()
        return False, "a folder path, or null"
    if kind == "strs":
        if isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value):
            return True, tuple(v.strip() for v in value)
        return False, "a list of names"
    return False, "a setting the overlay cannot change"


def _settings_overlay_section(path, module: str):
    """{name: value} of `module`'s section of the overlay file at `path`, or
    None (no path, no file, no section). Raises ValueError/OSError on a file
    that cannot be read or is not a JSON object of objects."""
    if path is None or not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    section = data.get(module)
    if section is not None and not isinstance(section, dict):
        raise ValueError(f"the '{module}' entry is not an object")
    return section


# === Staging, locks and small helpers ===

PAGE_PREFIX = "jxlphoto-page:"

_CAMERA_THUMB_MAX_SIDE = 160

_STAGING_PREFIX_RE = re.compile(r"^[0-9a-f]{32}_")


def _staging_leftovers(staging_dir):
    """(paths, total_bytes) for files this tool left behind in staging."""
    found, total = [], 0
    try:
        entries = list(Path(staging_dir).iterdir())
    except OSError:
        return [], 0
    for f in entries:
        if not _STAGING_PREFIX_RE.match(f.name):
            continue
        try:
            if not f.is_file():
                continue
            total += f.stat().st_size
        except OSError:
            continue
        found.append(f)
    return found, total


def _try_lock_nb(fh) -> bool:
    """Lock an open file without waiting. False when another holder has it."""
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock_nb(fh) -> None:
    """Release a lock _try_lock_nb took (closing the file releases it too)."""
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _changed_since(path, st) -> bool:
    """Is `path` no longer the file `st` (its os.stat before an in-place edit
    began) describes? A file that cannot be read counts as changed. Checked
    right before the edited copy replaces it (#559): one rewritten meanwhile
    by another run would otherwise be put back to its old version."""
    try:
        now = os.stat(path)
    except OSError:
        return True
    return (now.st_size, now.st_mtime_ns) != (st.st_size, st.st_mtime_ns)


def _guard_refusal_hint(g, restamp: bool) -> list:
    """The lines every non-delete provenance refusal ends with: the two likely
    causes, what to do about each, and where the details are. `restamp` names
    --restamp-moved, which only the encoder has."""
    lines = [
        "  Why: every output records the full PATH of the file that made it. This "
        "output names a different path, so it is either (a) the same photo after "
        "its folder was renamed or moved, or (b) a different photo with the same "
        "name.",
        "  (a) Renamed/moved folder:"
        + (" if the source was not edited since, run once with --restamp-moved "
           "(rewrites the record, no re-encode), or" if restamp else "")
        + " re-run with --overwrite --provenance content (accepts an unchanged "
        "source). If you never delete sources and rename folders often, turn the "
        "guard off: --no-overwrite-guard, or OVERWRITE_GUARD = False at the top "
        "of the script, or 'Overwrite guard: off' in the wizard.",
        "  (b) Different photo: rename one of them, or write into another folder.",
        f"  Details: {g.GUARD_DOC_URL}",
    ]
    return lines


def _fmt_size(n):
    """Human size that stays informative below a gigabyte.

    A fixed GB format printed "0.0 GB" for everything under ~50 MB, which is
    exactly the reading someone with a nearly-full staging drive needs to see.
    """
    for unit, step in (("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2)):
        if n >= step:
            return f"{n / step:.1f} {unit}"
    return f"{n / 1024:.0f} KB"


def _is_camera_thumbnail_page(tokens, width, height) -> bool:
    """True for a page other than the first of a split TIFF that is at most
    160 px on its long side: the thumbnail a camera or its software writes
    into its own TIFF WITHOUT the reduced-image flag (NX Studio: 160x107 or
    107x160; Fuji Hyper Utility: 160x120). The encoder archives it as a page
    like any other, so the TIFF comes back whole; a derivative run makes no
    copy of it, like the flagged thumbnail pages. Pure: `tokens` are the
    JXL's dc:Relation values, width/height its pixel size (0 or None when
    unknown: then it is not one)."""
    try:
        side = max(int(width or 0), int(height or 0))
    except (TypeError, ValueError):
        return False
    if not 0 < side <= _CAMERA_THUMB_MAX_SIDE:
        return False
    for t in tokens or ():
        t = str(t).strip()
        if t.startswith(PAGE_PREFIX):
            try:
                return int(t[len(PAGE_PREFIX):]) > 0
            except ValueError:
                return False
    return False


def _resize_geometry(w: int, h: int, mode, value, allow_upscale: bool = False):
    """(new_w, new_h) for --resize-long/--resize-short/--resize-percent, or None
    when the image keeps its size (no resize requested, same size, or a
    would-be upscale without --allow-upscale). Aspect ratio always kept."""
    if not mode:
        return None
    if mode == "long":
        scale = float(value) / max(w, h)
    elif mode == "short":
        scale = float(value) / min(w, h)
    elif mode == "percent":
        scale = float(value) / 100.0
    else:
        raise ValueError(f"unknown resize mode: {mode}")
    if scale <= 0:
        raise ValueError("resize value must be positive")
    if scale > 1.0 and not allow_upscale:
        return None
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    if (nw, nh) == (w, h):
        return None
    return nw, nh


def _batch_read_state(g, label: str, total: int) -> dict:
    """Bookkeeping for a planning-time exiftool batch reader. See
    _batch_read_tick."""
    now = time.monotonic()
    return {"label": label, "total": total, "t0": now,
            "next": now + g._SCAN_QUIET_SECONDS, "gap": g._SCAN_REPORT_EVERY}


def _scan_state(g, root):
    """Bookkeeping for a directory walk. See _scan_tick."""
    now = time.monotonic()
    return {"root": str(root) if root is not None else "", "scanned": 0,
            "t0": now, "next": now + g._SCAN_QUIET_SECONDS,
            "gap": g._SCAN_REPORT_EVERY, "announced": False}


# libjxl version detection - used to gate flags that only exist in newer
# cjxl/djxl builds. Unknown versions are treated as "old" (safe fallback:
# no new flags are ever appended).
@functools.lru_cache(maxsize=None)
def _tool_version(exe: str):
    """Return (major, minor, patch) of a cjxl/djxl-like tool, or None if unknown."""
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
        out = (r.stdout or "") + " " + (r.stderr or "")
    except Exception:
        return None
    m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(x) for x in m.groups()) if m else None


def _reduced_by_integer_decode(info_text, frames_kept: bool):
    """Why a 16-bit integer decode of this JXL is not a full copy of it (#622),
    or None. Positive evidence only: no jxlinfo answer means None."""
    m = re.search(r"^JPEG XL (image|animation),[^\n]*", info_text or "", re.M)
    if not m:
        return None
    line = m.group(0)
    if m.group(1) == "animation" and not frames_kept:
        return "an animation (only its first frame is decoded)"
    if "float" in line:
        return "floating-point samples (16-bit integers clip and round them)"
    b = re.search(r"\b(\d+)-bit\b", line)
    if b and int(b.group(1)) > 16:
        return f"{b.group(1)}-bit samples"
    return None


def _capture_output_identity(write_path: Path, final_path: Path):
    """Capture (mtime_ns, size) of a pre-existing output (non-staging only).
    Returns None for staging paths or nonexistent outputs."""
    if write_path != final_path or not final_path.exists():
        return None
    try:
        st = final_path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _linux_mem_available():
    """/proc/meminfo MemAvailable in bytes, or None."""
    with open("/proc/meminfo", encoding="ascii") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    return None


def _resize_label(mode, value, allow_upscale=False):
    if not mode:
        return ""
    tag = {"long": "long", "short": "short", "percent": "pct"}[mode]
    v = int(value) if float(value).is_integer() else value
    return f"@{tag}{v}" + ("+up" if allow_upscale else "")


def _derived_label(icc_label, resize_mode=None, resize_value=None,
                   allow_upscale=False, sharpen=None):
    """jxlphoto-derived:<label>. Changing any part re-derives on the next sync."""
    return ((icc_label or "keep")
            + _resize_label(resize_mode, resize_value, allow_upscale)
            + (f"+{sharpen}" if sharpen and sharpen != "none" else ""))


def md5_of_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _delete_partial_if_written(write_path: Path, final_path: Path, pre_identity) -> None:
    """Delete write_path ONLY if this run actually wrote to it.

    - Staging (write != final): always this run's file -> delete.
    - No pre-existing identity: anything there is this run's partial -> delete.
    - Identity changed: this run truncated/rewrote it -> delete.
    - Identity UNCHANGED: this run never touched it (e.g. the codec failed at
      startup with rc!=0) -> the good pre-existing file is KEPT.
    """
    try:
        if write_path != final_path or pre_identity is None:
            if write_path.exists():
                write_path.unlink()
            return
        st = write_path.stat()
        if (st.st_mtime_ns, st.st_size) != pre_identity:
            write_path.unlink()
    except OSError:
        pass


# === Marker anchor, argfiles and export names ===


def _marker_anchor_index(dir_parts, root, marker_lower):
    """Index in `dir_parts` (the folder parts of a file's path) of the export
    marker folder that anchors modes 6/7: the FIRST matching folder at or
    below the input `root` (the root folder itself included), else the
    CLOSEST matching one above it (a run pointed inside an export folder).
    A folder ABOVE the root used to win whenever it came first — a library
    under 'Lightroom_Export' or 'C1_Export' (both match '_EXPORT') anchored
    there instead of on the real '_EXPORT' below (E-H of the 2026-11-03
    audit). A path that is not under `root` keeps the old rule (the first
    match from the top). None when no folder matches."""
    dir_parts = list(dir_parts)
    start = 0
    if root is not None:
        try:
            rp = [os.path.normcase(p) for p in Path(root).parts]
        except TypeError:
            rp = []
        dp = [os.path.normcase(p) for p in dir_parts]
        if rp and dp[:len(rp)] == rp:
            start = len(rp) - 1         # the root folder itself counts
        elif rp and dp == rp[:-1]:
            start = len(dp)             # the root is this very file
    for i in range(start, len(dir_parts)):
        if _marker_matches(dir_parts[i].lower(), marker_lower):
            return i
    for i in range(min(start, len(dir_parts)) - 1, -1, -1):
        if _marker_matches(dir_parts[i].lower(), marker_lower):
            return i
    return None


def _argfile_via_stdin(g, cmd):
    """(cmd, None) unchanged — or, when `cmd` names an exiftool argfile
    (`-@ <file>`) whose PATH the ANSI code page cannot carry, (the same
    command with `-@ -`, the file's bytes for its stdin). exiftool reads that
    path from its command line in the ANSI code page: with %TEMP% outside it
    (a Windows user name in another script) every call failed with "Error
    opening arg file". The file's CONTENT is UTF-8 with `-charset
    filename=utf8` and is read the same way from stdin. Parity-pinned (four
    backends)."""
    try:
        i = list(cmd).index("-@")
    except ValueError:
        return cmd, None
    if i + 1 >= len(cmd):
        return cmd, None
    path = str(cmd[i + 1])
    if path == "-" or g._ansi_encodable(path):
        return cmd, None
    with open(path, "rb") as f:
        data = f.read()
    return list(cmd[:i + 1]) + ["-"] + list(cmd[i + 2:]), data


def _validate_export_folder_name(name: str, marker: str, subfolder: str):
    """None when `name` is usable as the modes 6/7 output folder, else the reason.

    The output folder is created directly under the export marker, so it must be
    ONE plain path component; a name that itself matches the marker would be
    read as a second anchor by every later mode 6/7 scan; and a name equal to
    the requested input subfolder would write the outputs among the sources.
    """
    n = (name or "").strip()
    if not n:
        return "the folder name is empty"
    if n in (".", "..") or any(c in n for c in '/\\:*?"<>|'):
        return f"'{n}' is not a single plain folder name"
    if marker and _marker_matches(n.lower(), marker.lower()):
        return f"'{n}' matches the export marker '{marker}' — it would become a new anchor"
    if subfolder and n.lower() == subfolder.lower():
        return f"'{n}' is the input subfolder itself — outputs would land among the sources"
    return None


def _struct_escape(value) -> str:
    """A value inside an exiftool serialized structure ({Field=value,...}):
    , [ ] { } and | are escaped with a leading |, and newlines are flattened
    like every other argfile value."""
    return re.sub(r"([,\[\]{}|])", r"|\1", _argfile_safe(value))


# === Progress, staging and disk ===


def _scan_tick(g, st, found):
    """Report that a slow walk is still moving; stay quiet while it is fast.

    The clock is read once every _SCAN_CHECK_INTERVAL entries rather than on
    every one: this runs for every file on the volume, not just the matches.
    """
    st["scanned"] += 1
    if st["scanned"] % g._SCAN_CHECK_INTERVAL:
        return
    now = time.monotonic()
    if now < st["next"]:
        return
    if not st["announced"]:
        g.logger.info(f"Searching for files under {st['root']} -- a large or "
                    f"network drive can take a while...")
        st["announced"] = True
    g.logger.info(f"  Scanned {st['scanned']} entries, {found} match(es) so far "
                f"({now - st['t0']:.0f}s)")
    st["gap"] = min(st["gap"] * g._SCAN_REPORT_FACTOR, g._SCAN_REPORT_MAX)
    st["next"] = now + st["gap"]


def _scan_done(g, st, found):
    """Close the report, but only if one was ever opened."""
    if st["announced"]:
        g.logger.info(f"  Scan finished: {found} match(es) from {st['scanned']} "
                    f"entries in {time.monotonic() - st['t0']:.0f}s")


def _batch_read_tick(g, st, done: int) -> None:
    """Between two batches of a planning-time read: "<label>: N/M read" once
    the read is slow, quiet while it is fast (W-A of the 2026-11-03 audit).
    A busy disk made one 400-file batch take minutes (#537), and these reads
    printed nothing until they finished: the wrapper, which stops a child
    that prints nothing for its idle timeout, took a long planning phase for
    a hung child. Same pacing as _scan_tick."""
    if not 0 < done < st["total"]:
        return
    now = time.monotonic()
    if now < st["next"]:
        return
    elapsed = now - st["t0"]
    left = max(0.0, elapsed / done * (st["total"] - done))
    eta = f"~{left / 60:.0f} min left" if left >= 60 else f"~{left:.0f}s left"
    g.logger.info(f"  {st['label']}: {done}/{st['total']} read ({elapsed:.0f}s, {eta})")
    st["gap"] = min(st["gap"] * g._SCAN_REPORT_FACTOR, g._SCAN_REPORT_MAX)
    st["next"] = now + st["gap"]


def _promote_from_staging(g, write_path, final_path) -> bool:
    """Move one finished output out of staging. True when it landed.

    The move goes to a temp file IN THE DESTINATION FOLDER first and only then
    over the final name with an atomic os.replace (E6/X3 of the 2026-10-08
    audit). A plain shutil.move onto an existing output is copy2 + unlink on
    Windows — even on one volume, since os.rename refuses an existing target —
    and copy2 TRUNCATES the destination first: a kill or power cut mid-copy
    left the old archive destroyed and a partial file with a fresh mtime that
    smart sync then treats as up to date forever. Now the final name holds
    either the old file or the complete new one, never a mix.

    When the move raises, what reached the temp decides: a COMPLETE copy (the
    copy finished, only the staging unlink or the final rename failed) is put
    in place — after a successful copy+unlink it may be the only copy left —
    and a partial one is removed (the good copy is still in staging; the
    destination was never touched). A destination volume that is simply FULL
    also has to stop the run rather than produce one MOVE FAILED line per
    remaining file, which is what the disk-full abort exists for.
    """
    try:
        size = write_path.stat().st_size
    except OSError:
        size = 0
    tmp = final_path.parent / f"{uuid.uuid4().hex}_{final_path.name}.tmp"
    try:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(write_path), str(tmp))
        os.replace(str(tmp), str(final_path))
        return True
    except OSError as e:
        g.logger.error(f"  MOVE FAILED, kept in staging | {write_path.name} -> "
                     f"{final_path} | {e}")
        try:
            landed = tmp.stat().st_size if tmp.exists() else None
        except OSError:
            landed = None
        if landed is not None:
            if size > 0 and landed == size:
                try:
                    os.replace(str(tmp), str(final_path))
                    g.logger.error("    The copy itself finished — the destination now "
                                 "holds the COMPLETE output.")
                except OSError as e2:
                    g.logger.error(f"    The COMPLETE output is at {tmp}: rename it to "
                                 f"{final_path.name} once the problem is fixed ({e2}).")
            else:
                try:
                    tmp.unlink()
                    g.logger.error("    Removed the partial copy; the destination was "
                                 "never touched and the complete copy is still in "
                                 "staging.")
                except OSError as e2:
                    g.logger.error(f"    Could NOT remove the partial copy {tmp} ({e2}); "
                                 f"the destination itself was never touched.")
        g._abort_if_disk_full(final_path.parent, size)
        return False


def _clean_staging(g, staging_dir):
    """Delete leftovers that have been idle long enough to be nobody's.

    Deliberately runs BEFORE the batch, not after: sweeping at the end would
    delete this run's own failures, which are the evidence the KEEP path exists
    to preserve. Sweeping first clears the previous runs' orphans instead.
    """
    if not staging_dir:
        return 0, 0
    if g._staging_used_by_another_run(staging_dir):
        g.logger.warning(f"Staging: another run is using {staging_dir} — nothing swept "
                       f"(its outputs may be waiting there for their folder to finish)")
        return 0, 0
    found, _ = g._staging_leftovers(staging_dir)
    if not found:
        return 0, 0
    cutoff = time.time() - g._STAGING_MIN_AGE_SECONDS
    removed, freed = 0, 0
    for f in found:
        try:
            st = f.stat()
            if st.st_mtime > cutoff:
                continue        # young enough that a live run may own it
            size = st.st_size
            f.unlink()
        except OSError as e:
            g.logger.warning(f"  Could not remove staging leftover {f.name}: {e}")
            continue
        removed += 1
        freed += size
    if removed:
        g.logger.info(f"Staging: removed {removed} leftover file(s), freed {g._fmt_size(freed)}")
    return removed, freed


def _sweep_orphan_temps(g, finals):
    """Remove the leftover temps of these output paths (name or stem form:
    '<uuid>_photo.jxl.tmp', '<uuid>_photo.tmp', a temp of a temp). Returns how
    many were removed; a folder or file that cannot be read is skipped."""
    names = {}
    for f in finals:
        if f is None:
            continue
        f = Path(f)
        names.setdefault(f.parent, set()).update((f.name, f.stem))
    removed = 0
    for folder, wanted in names.items():
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for e in entries:
            m = g._ORPHAN_TEMP_RE.match(e.name)
            if not m or m.group(1) not in wanted:
                continue
            try:
                if not e.is_file(follow_symlinks=False):
                    continue
                os.unlink(e.path)
            except OSError as exc:
                g.logger.warning(f"Could not remove a leftover temp: {e.path} ({exc})")
                continue
            removed += 1
            g.logger.info(f"Removed a leftover temp from an interrupted run: {e.path}")
    return removed


def _report_staging_leftovers(g, staging_dir):
    """Say what is still sitting in staging, so the leak cannot stay invisible."""
    if not staging_dir:
        return
    found, total = g._staging_leftovers(staging_dir)
    if not found:
        return
    g.logger.warning(f"Staging holds {len(found)} leftover file(s) ({g._fmt_size(total)}) "
                   f"in {staging_dir}")
    for f in sorted(found)[:5]:
        g.logger.warning(f"    {f.name}")
    if len(found) > 5:
        g.logger.warning(f"    ... and {len(found) - 5} more")
    g.logger.warning("  These are outputs whose conversion failed, kept for inspection. "
                   "Pass --clean-staging on a later run to sweep the older ones.")


def _holds_staging(g, fn):
    """Run `fn` holding TEMP2_DIR (read at call time) as in use (#578): its
    outputs wait in staging until their destination folder is complete."""
    def wrapper(*args, **kwargs):
        with g._staging_hold(g.TEMP2_DIR):
            return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    wrapper.__wrapped__ = fn
    return wrapper


def _flush_to_disk(g, path) -> bool:
    """Force `path`'s data to the disk (#617). False when it cannot be done:
    the delete gates then keep the source — a file that only exists in the
    OS cache does not justify an irreversible delete. The flush needs write
    access: a read-only output (an archive the owner protected) keeps its
    source too, and the reason is logged."""
    try:
        fd = os.open(str(path), os.O_RDWR | getattr(os, "O_BINARY", 0))
    except OSError as e:
        g.logger.warning(f"  could not open {Path(path).name} to flush it to disk "
                       f"(read-only or in use?): {e}")
        return False
    try:
        os.fsync(fd)
        return True
    except OSError as e:
        g.logger.warning(f"  could not flush {Path(path).name} to disk: {e}")
        return False
    finally:
        os.close(fd)


def _abort_if_disk_full(g, write_dir, needed):
    """Latch an abort when `write_dir` can no longer take a `needed`-byte file.

    Only ever called from a failure path, so a healthy run never pays for the
    stat. A volume that cannot be queried returns False: "cannot tell" must
    never be reported to the user as "disk full".
    """
    try:
        free = shutil.disk_usage(write_dir).free
    except OSError:
        return False
    required = max(int(needed or 0), g._MIN_FREE_BYTES)
    if free >= required:
        return False
    g._signal_abort(f"no space left on {write_dir} "
                  f"({free // (1024 * 1024)} MB free, "
                  f"needs at least {required // (1024 * 1024)} MB)")
    return True
