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
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

TOOLKIT_VERSION = "2.30.2-rc1"


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

_THUMB_HEAD = 65536

# Peak private memory of ONE cjxl, in bytes per pixel of the image, measured on
# libjxl 0.12.0 with 45 MP 16-bit photos (+~10% margin). Plan
# 261004_Claude_plan_memoria-workers.md has the full table.
_STREAMING_BYTES_PER_PIXEL = 40
_WHOLE_IMAGE_E7_BYTES_PER_PIXEL = 90
_WHOLE_IMAGE_E8_BYTES_PER_PIXEL = 320
_MODULAR_LOSSY_BYTES_PER_PIXEL = 110


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
    # own exiftool command BEFORE the EXIF (_xmp_block_preload_args), so the Exif box
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
    """
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
    """
    return ICC_PROFILE_PREFIX + base64.b64encode(icc_bytes).decode("ascii")


# The creation date (year, month, day, hour, minute, second) written into the
# sRGB profile the toolkit generates (_generated_srgb_icc).
_GENERATED_ICC_DATE = (2026, 11, 13, 0, 0, 0)


def _generated_srgb_icc() -> bytes:
    """Pillow's (littleCMS's) built-in sRGB profile with a FIXED creation
    date. Generated as it is, it carries the moment it was made in its header
    (bytes 24-35), so two identical derivatives made on different days
    differed in their jxlphoto-iccprofile token. The profile ID (bytes 84-99)
    covers the date: littleCMS leaves it zero (not computed); should it ever
    be set, it is computed again for the new date (ICC.1 7.2.18: MD5 of the
    profile with flags, rendering intent and ID zeroed). ImportError when
    Pillow is missing: the caller says what needs it."""
    from PIL import ImageCms
    raw = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    raw[24:36] = struct.pack(">6H", *_GENERATED_ICC_DATE)
    if any(raw[84:100]):
        h = bytearray(raw)
        h[44:48] = bytes(4)
        h[64:68] = bytes(4)
        h[84:100] = bytes(16)
        raw[84:100] = hashlib.md5(bytes(h)).digest()
    return bytes(raw)


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

# The modes that COLLAPSE folder structure (2/4/5/6/7) drop the source's folder
# from the output path, so two sources in different folders resolve to the same
# output. _abort_on_duplicate_outputs catches that inside one run; only a
# recorded marker catches it ACROSS runs — and with --delete-source the second
# run would otherwise overwrite the first archive and delete both originals.
_COLLAPSING_MODES = frozenset({2, 4, 5, 6, 7})

# cjxl and djxl fail on a path of 260 characters or more ("Could not write
# jxl file", "couldn't load") even with Windows long paths enabled; given the
# extended-length prefix \\?\ they read and write it, Japanese included
# (measured with libjxl 0.12 on a 320-character path, 2026-10-22). A temp
# written beside the output adds ~50 characters to the source path, and 1459
# files of the owner's library reach the limit that way. exiftool handles long
# paths itself, and ImageMagick only ever sees short temp paths (and would
# read the '?' of the prefix as a wildcard), so only the two codecs get it.
_LONG_PATH_THRESHOLD = 240


def _abort_on_duplicate_outputs(g, pairs):
    """Abort the run if two outputs map to the same destination file.

    Modes 6/7 drop the first subfolder level under EXPORT_MARKER, so same-named
    files in different recipe subfolders would silently overwrite each other
    (and with mode 8 + delete, a single validated output could justify deleting
    several distinct sources). Better to stop loudly than to lose data.

    pairs: list of (source_path, dest_path) tuples. Each child sees only its own
    run: a clash between two manifest entries is the wrapper's to catch.
    """
    from collections import Counter, defaultdict
    norm = {}
    by_dest = defaultdict(list)
    for src, dst in pairs:
        norm.setdefault(os.path.normcase(str(dst)), str(dst))
        by_dest[os.path.normcase(str(dst))].append(str(src))
    counts = Counter(os.path.normcase(str(d)) for _, d in pairs)
    dupes = sorted(norm[d] for d, c in counts.items() if c > 1)
    if dupes:
        for d in dupes[:10]:
            srcs = by_dest[os.path.normcase(d)]
            g.logger.error(f"Duplicate output destination: {d}")
            for s in srcs[:4]:
                g.logger.error(f"    <- from: {s}")
            if len(srcs) > 4:
                g.logger.error(f"    <- ... and {len(srcs) - 4} more source(s)")
        if len(dupes) > 10:
            g.logger.error(f"... and {len(dupes) - 10} more")
        g.logger.error("Aborting: multiple inputs map to the same output file. "
                       "Rename inputs, pick another mode/folder, or split the run to avoid silent overwrites.")
        g.logger.error("  Hint: marker-anchored modes (6/7) drop ONE folder level under the marker, "
                       "so nested marker folders (X/X/photo.tif and X/photo.tif) and same-named files "
                       "in sibling recipe folders both collapse onto the same .jxl name.")
        sys.exit(2)


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
    not formatted and must not be escaped."""
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

# dc:Relation token naming what the FIRST master of this photo was made from:
# tiff8 / tiff16 (encoder), jpeg / png8 / png16 / png (transcoder, pixel
# re-encode), jpeg (recompressor re-encoding a jbrd JXL), jxl-lossy /
# jxl-lossless (recompressor, a JXL no toolkit script wrote, as jxlinfo reads
# its header; plain `jxl` when jxlinfo could not tell, and in files written by
# v2.10.0). Written once by the script that first touches a clean input, then
# carried forward unchanged — absent means unknown, never guessed. The only
# reader is the recompressor's --on-regeneration guard (_hidden_generations).
ORIGIN_PREFIX = "jxlphoto-origin:"

_ENCODE_TAG_RE = re.compile(r"cjxl\s+d=([0-9.]+)\s+e=(\d+)")

# The stored generation token, read ONLY as a complete " | "-delimited segment
# (or bounded by the field's start/end): "Project gen=3 phase 2" is running
# text, not a token, and must never be read as one.
_GEN_TAG_RE = re.compile(r"(?:^|\|)\s*gen=(\d+)\s*(?=\||$)")

# The whole machine block as ONE unit: an optional gen= lead segment followed
# by the contiguous cjxl d=/e= chain. The block only starts at a segment
# boundary ((?:^|\|)), so it never bites into a caption's running text; and
# matching only the first cjxl entry would leave an orphaned gen= and a
# partial chain behind. Legacy fields (a chain with no gen=) are the normal
# case and match in full.
_MACHINE_BLOCK_RE = re.compile(
    r"(?:^|\|)\s*"
    r"(?:gen=\d+\s*\|\s*)?"
    r"cjxl\s+d=[0-9.]+\s+e=\d+"
    r"(?:\s*\|\s*cjxl\s+d=[0-9.]+\s+e=\d+)*"
)

# The History entry a deliberate "--encode-tag off" writes: a reader that meets
# it before any record stops there (the lineage was discarded on purpose, an
# older entry's record must not come back).
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

# Must match the encoder's PAGE_XMP_PREFIX: which page of its TIFF this JXL
# holds. It carries the page index of a split page, so reconstruction does
# not depend on the file name (which breaks for sources named *_page<N> or
# *_thumbnail).
PAGE_PREFIX = "jxlphoto-page:"

_CAMERA_THUMB_MAX_SIDE = 160

# Staging leftovers. A file whose conversion failed is deliberately KEPT in
# staging for manual recovery ("KEEP in staging"), and nothing ever swept it.
# Over weeks of scheduled runs that is a slow leak on precisely the small
# scratch SSD the disk-full abort exists to protect -- the leftovers eventually
# cause the condition they were evidence of.
#
# Every staging name the toolkit writes starts with a uuid4 hex prefix, and
# that is what makes a sweep safe: a staging directory is frequently a shared
# scratch folder, so nothing that did not come from here may be touched. The
# scan is non-recursive for the same reason.
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
    filename=utf8` and is read the same way from stdin."""
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


# === The log ===


def _utf8_console():
    """stdout and stderr in UTF-8 (an unencodable character becomes '?'),
    from the first line of a script's main(): redirected to a file or read
    through a pipe (the wizard's), they are the Windows code page, and
    argparse's --help or error, or a message printed before setup_logger,
    that held a character outside it — a Japanese folder name in a setting
    or a path — ended in a UnicodeEncodeError traceback (#661). setup_logger
    does the same for stdout."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def setup_logger(g):
    """The script's logger (named g._LOGGER_NAME): a new log file in g.LOG_DIR
    and the console. Sets g.logger and returns the log file's path; the last
    line names the file (g._LOG_SAVED_LABEL, "Log saved to" by default).

    The logger and the file are at DEBUG, the console at INFO (#651: at INFO
    the logger dropped every debug line before the file could see it). A
    second call in the same process (tests, a script driving another)
    replaces the handlers instead of doubling every line. No delete summary
    here: this runs before anything is deleted, and a line of zeros reads
    like a result."""
    g.LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # PID suffix: two children of the same script started in the same second
    # (two manifest entries) would otherwise open the SAME file in append;
    # the counter covers a same-process repeat inside that second.
    n = 0
    while True:
        log_file = g.LOG_DIR / f"{timestamp}_{os.getpid()}{('_' + str(n)) if n else ''}.log"
        if not log_file.exists():
            break
        n += 1

    log = logging.getLogger(g._LOGGER_NAME)
    g.logger = log
    log.setLevel(logging.DEBUG)

    for handler in log.handlers[:]:
        log.removeHandler(handler)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    # stdout in UTF-8, or a Japanese folder name turns into mojibake on the
    # Windows console.
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)

    log.addHandler(fh)
    log.addHandler(ch)
    log.info(f"{g.TOOLKIT_NAME} {g.TOOLKIT_VERSION} | {Path(g.__file__).name}")
    g._log_settings_overlay()
    log.info(f"{_script_get(g, '_LOG_SAVED_LABEL', 'Log saved to')}: {log_file}")
    return log_file


# === JXL finders of the decoder and the recompressor ===


def _iter_jxls(g, paths, root=None):
    """The .jxl files among `paths` (each real file once, sorted), with the scan
    progress. A script that has EXCLUDE_FOLDERS (the decoder) leaves out the
    files below those folders and says how many; one without it (the
    recompressor) filters nothing."""
    exclude = _script_get(g, "EXCLUDE_FOLDERS", None)
    seen = set()
    files = []
    excluded = 0
    _scan = g._scan_state(root)
    for f in paths:
        g._scan_tick(_scan, len(files))
        if f.suffix.lower() not in g.JXL_EXTS:
            continue
        try:
            if not f.is_file():
                continue
            key = f.resolve()
        except OSError:
            continue
        if exclude is not None and _path_excluded_below_root(f, root, exclude):
            excluded += 1
            continue
        if key not in seen:
            seen.add(key)
            files.append(f)
    g._scan_done(_scan, len(files))
    if excluded > 0:
        g.logger.info(f"Excluded {excluded} file(s) under: {', '.join(exclude)}")
    return sorted(files)


def find_jxls_mode6(g, input_path):
    """Mode 6: only JXLs inside folders containing EXPORT_MARKER (any subfolder).
    The recursive walk is the script's own (the recompressor skips its own
    output folders there)."""
    all_jxls = g.find_jxls_recursive(input_path)
    filtered = []
    marker_lower = g.EXPORT_MARKER.lower()
    skipped_other = 0
    for j in all_jxls:
        # Match only directory parts; the filename itself is not an anchor
        parts_str = list(j.parts[:-1])
        export_idx = _marker_anchor_index(parts_str, input_path, marker_lower)
        if export_idx is not None:
            below = [p.lower() for p in parts_str[export_idx + 1:]]
            if g._skip_other_tool_output(below, honor_requested_subfolder=False):
                skipped_other += 1
                continue
            filtered.append(j)
    g._log_other_tool_skips(skipped_other)
    return sorted(filtered)


def find_jxls_mode7(g, input_path):
    """Mode 7: only JXLs inside EXPORT_MARKER/EXPORT_JXL_SUBFOLDER."""
    all_jxls = g.find_jxls_recursive(input_path)
    filtered = []
    marker_lower = g.EXPORT_MARKER.lower()
    subfolder_lower = g.EXPORT_JXL_SUBFOLDER.lower()
    skipped_other = 0
    for j in all_jxls:
        # Match only directory parts; the filename itself is not an anchor
        parts_str = list(j.parts[:-1])
        export_idx = _marker_anchor_index(parts_str, input_path, marker_lower)
        if export_idx is None:
            continue
        if g._skip_other_tool_output([p.lower() for p in parts_str[export_idx + 1:]]):
            skipped_other += 1
            continue
        if g.EXPORT_JXL_SUBFOLDER:
            if export_idx + 1 < len(parts_str) and parts_str[export_idx + 1].lower() == subfolder_lower:
                filtered.append(j)
        else:
            filtered.append(j)
    g._log_other_tool_skips(skipped_other)
    return sorted(filtered)


# === Several exiftool commands in one process ===


def _split_by_marker(text, tag, n):
    """[(status, text)] of `n` commands from one stream of
    _exiftool_one_process: what a command printed is what came before its
    marker and after the previous one. A command without its marker keeps
    (-1, "")."""
    parts = [(-1, "") for _ in range(n)]
    rx = re.compile(re.escape(tag) + r" (\d+) (-?\d+)\}")
    pending = []
    for line in (text or "").split("\n"):
        m = rx.fullmatch(line.strip())
        if m and int(m.group(1)) < n:
            parts[int(m.group(1))] = (int(m.group(2)), "".join(pending))
            pending = []
        else:
            pending.append(line + "\n")
    return parts


def _argfile_command(g, path):
    """The argument lines of an argfile written for one exiftool call (the
    charset lines it starts with are added again per command)."""
    text = Path(path).read_text(encoding="utf-8")
    if text.startswith(g._ARGFILE_CHARSET):
        text = text[len(g._ARGFILE_CHARSET):]
    return [ln for ln in text.split("\n") if ln != ""]


def _exiftool_one_process(g, commands, timeout):
    """Run the commands in ONE exiftool process (`-execute` between them):
    [CompletedProcess] in command order, each with its own exit status
    (`-echo3`/`-echo4` markers carrying `${status}`, exiftool 12.15 or later),
    stdout and stderr. FAIL-CLOSED: a command whose marker is missing or
    carries no number has status -1. Each command starts with the charset
    lines (and -m where the script has EXIFTOOL_IGNORE_MINOR on), like a
    _run_exiftool_argfile call of its own."""
    tag = "{jxlphoto" + uuid.uuid4().hex[:12]
    head = g._ARGFILE_CHARSET.rstrip("\n").split("\n")
    if _script_get(g, "EXIFTOOL_IGNORE_MINOR"):
        head = head + ["-m"]
    lines = []
    for i, cmd in enumerate(commands):
        if i:
            lines.append("-execute")
        marker = f"{tag} {i} " + "${status}}"
        lines += head + [str(a) for a in cmd] + ["-echo3", marker, "-echo4", marker]
    argfile = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         dir=g.TEMP_DIR, encoding="utf-8", newline="\n") as af:
            argfile = af.name
            af.write("\n".join(lines) + "\n")
        r = g._run_captured([g._get_exiftool_cmd(), "-@", argfile], timeout, text=True)
    finally:
        if argfile:
            try:
                os.unlink(argfile)
            except OSError:
                pass
    outs = _split_by_marker(r.stdout, tag, len(commands))
    errs = _split_by_marker(r.stderr, tag, len(commands))
    results = []
    for cmd, (s_out, t_out), (s_err, t_err) in zip(commands, outs, errs):
        status = s_out if s_out == s_err else -1
        results.append(subprocess.CompletedProcess(cmd, status, t_out, t_err))
    return results


def _exiftool_batch_ok(g) -> bool:
    """True when several exiftool commands may share one process: the
    script's own runners are in place (a test that replaces
    _run_exiftool_argfile or _run_captured sees every command on its own,
    as before) and this exiftool reports the status of each command
    (`${status}`, 12.15 or later). Asked once per run and per exiftool:
    one start, in g._EXIFTOOL_BATCH."""
    own = _script_get(g, "__name__")
    for name in ("_run_exiftool_argfile", "_run_captured"):
        if getattr(_script_get(g, name), "__module__", None) != own:
            return False
    state = _script_get(g, "_EXIFTOOL_BATCH")
    if state is None:
        return False
    exe = g._get_exiftool_cmd()
    if exe not in state:
        version = ""
        try:
            r = _exiftool_one_process(g, [["-ver"]], g.EXIFTOOL_TIMEOUT)[0]
            version = (r.stdout or "").strip()
            state[exe] = r.returncode == 0 and bool(version)
        except Exception:
            state[exe] = False
        # One line in the log file: a run that falls back to one process per
        # command is only slower, and nothing else would tell (review V4).
        g.logger.debug(f"exiftool {version or '(version unknown)'}: "
                       + ("a file's commands share one process" if state[exe]
                          else "one process per command (no status per command)"))
    return state[exe]


def _exiftool_commands(g, commands, timeout=60, stop_at_failure=True):
    """Run exiftool commands in order: [CompletedProcess], each with its own
    exit status, stdout and stderr. A command is a list of argument lines
    (run like _run_exiftool_argfile) or the Path of an argfile written for
    one call (run like `exiftool -@ <file>`).

    ONE process when _exiftool_batch_ok: every command runs, even after one
    that failed (a caller that raises at the first failure drops the file,
    so what the later ones did is never kept). Otherwise one process per
    command, exactly as before: stopping at the first that fails, or
    running them all with stop_at_failure=False (a caller whose old calls
    never stopped, the decoder's copy_metadata)."""
    if len(commands) > 1 and _exiftool_batch_ok(g):
        lines = [_argfile_command(g, c) if isinstance(c, Path) else list(c) for c in commands]
        return _exiftool_one_process(g, lines, timeout * len(commands))
    results = []
    for cmd in commands:
        if isinstance(cmd, Path):
            r = g._run_captured([g._get_exiftool_cmd(), "-@", str(cmd)], timeout, text=True)
        else:
            r = g._run_exiftool_argfile(cmd, timeout=timeout)
        results.append(r)
        if r.returncode != 0 and stop_at_failure:
            break
    return results


def _prefetched_result(g, args_lines):
    """The kept result of exactly this command for this thread (used once),
    or None."""
    local = _script_get(g, "_EXIFTOOL_PREFETCH")
    kept = getattr(local, "results", None)
    if not kept:
        return None
    return kept.pop(tuple(str(a) for a in args_lines), None)


def _prefetch_exiftool_reads(g, commands, timeout):
    """Run READ-ONLY exiftool commands now, in one process (when
    _exiftool_batch_ok), and keep their results for this thread: each
    reader's own _run_exiftool_argfile call with the same arguments is then
    answered without starting exiftool. A reader whose arguments differ runs
    as before, so this can only change the speed. Never a command that
    writes (an `-o` extraction run twice fails); the files read must not
    change before the readers run. _clear_exiftool_prefetch drops what is
    left."""
    local = _script_get(g, "_EXIFTOOL_PREFETCH")
    if local is None:
        return
    local.results = {}
    if len(commands) < 2 or not _exiftool_batch_ok(g):
        return
    results = _exiftool_one_process(g, [list(c) for c in commands],
                                    timeout * len(commands))
    local.results = {tuple(str(a) for a in c): r for c, r in zip(commands, results)}


def _clear_exiftool_prefetch(g):
    """Drop this thread's kept results (_prefetch_exiftool_reads)."""
    local = _script_get(g, "_EXIFTOOL_PREFETCH")
    if local is not None:
        local.results = {}


def _run_exiftool_writes(g, writes, timeout):
    """Run the writes, (command, what its failure says, how many characters
    of the output it quotes), in order (_exiftool_commands), and raise
    RuntimeError at the first that failed with the message its own call
    always raised."""
    results = _exiftool_commands(g, [w[0] for w in writes], timeout)
    for (_cmd, prefix, n), r in zip(writes, results):
        if r.returncode != 0:
            raise RuntimeError(f"{prefix}{(r.stderr or r.stdout or 'no output')[:n].strip()}")


# === Settings overlay ===


def _apply_settings_overlay(g, namespace: dict, module: str, specs: dict,
                            derived: dict) -> dict:
    """Apply `module`'s section of the overlay file to `namespace` (this
    script's globals(), right after the settings block): only the names in
    `specs`, each checked by _overlay_value. A `derived` setting
    ({"EXIFTOOL_TIMEOUT": "CJXL_TIMEOUT"}) follows its source when the
    overlay changes the source and the script had it equal. Returns
    {"path", "applied", "warnings"} for _log_settings_overlay. Never raises:
    an overlay that cannot be read changes nothing."""
    result = {"path": None, "applied": [], "warnings": []}
    try:
        result["path"] = g._settings_overlay_path()
        if ((os.environ.get(g._SETTINGS_OVERLAY_ENV) or "").strip()
                and not result["path"].is_file()):
            # #632: a mistyped JXLPHOTO_SETTINGS ran with the script's own
            # settings and not one line in the log.
            result["warnings"].append(
                f"{g._SETTINGS_OVERLAY_ENV} names a file that does not exist "
                f"({result['path']}); no setting changed")
            return result
        section = g._settings_overlay_section(result["path"], module)
    except Exception as e:
        result["warnings"].append(f"{result['path']}: not read ({e}); no setting changed")
        return result
    if not section:
        return result
    before = {name: namespace.get(name) for name in specs}
    for name, value in section.items():
        if name in g._OVERLAY_SAFETY_SETTINGS:
            result["warnings"].append(
                f"{name} is a safety setting: it is chosen per run (or in the "
                f"script itself), never by the overlay; ignored")
            continue
        spec = specs.get(name)
        if spec is None:
            result["warnings"].append(f"{name} is not a setting the overlay can "
                                      f"change in {module}; ignored")
            continue
        ok, checked = g._overlay_value(spec, value)
        if not ok:
            result["warnings"].append(f"{name} = {json.dumps(value)} is not "
                                      f"{checked}; the script's value is kept")
            continue
        namespace[name] = checked
        result["applied"].append(name)
    for name, source in derived.items():
        if (source in result["applied"] and name not in section
                and before.get(name) == before.get(source)):
            namespace[name] = namespace[source]
    return result


def _settings_overlay_path(g):
    """The overlay file to read (it may not exist), or None when
    JXLPHOTO_SETTINGS is set but empty. Same places as the wrapper's own
    config (ConfigManager._get_config_path): next to the scripts first."""
    env = os.environ.get(g._SETTINGS_OVERLAY_ENV)
    if env is not None:
        return Path(env) if env.strip() else None
    portable = Path(g.__file__).resolve().parent / g._SETTINGS_OVERLAY_FILE
    if portable.exists():
        return portable
    if os.name == "nt":
        home = Path(os.environ.get("USERPROFILE") or Path.home())
    else:
        home = Path.home()
    return home / g._SETTINGS_OVERLAY_FILE


# === Memory ===


def _available_memory_bytes(g):
    """(bytes, "commit"|"physical") the worker cap budgets from, per
    WORKER_MEMORY_LIMIT: the smaller of the two ("both"), or just one. A reading
    that fails is left out; (None, None) when nothing could be read. An unknown
    setting counts as "both", the stricter budget."""
    limit = g.WORKER_MEMORY_LIMIT if g.WORKER_MEMORY_LIMIT in g._MEMORY_LIMITS else "both"
    readings = []
    if limit in ("both", "commit"):
        readings.append((g._available_commit_bytes(), "commit"))
    if limit in ("both", "physical"):
        readings.append((g._available_physical_bytes(), "physical"))
    readings = [r for r in readings if r[0] is not None]
    if not readings:
        return None, None
    return min(readings, key=lambda r: r[0])


def _available_commit_bytes():
    """Memory the system can still commit, in bytes, or None if unknown.
    Windows: ullAvailPageFile (RAM + pagefile not yet committed — the limit
    WinError 1455 reports). Linux: MemAvailable."""
    try:
        if sys.platform == "win32":
            st = _windows_memory_status()
            return st[1] if st else None
        return _linux_mem_available()
    except Exception:
        return None


def _available_physical_bytes():
    """Physical RAM still available (free + standby cache), in bytes, or None
    if unknown. Windows: ullAvailPhys. Linux: MemAvailable (no commit limit by
    default there, so both readings are the same)."""
    try:
        if sys.platform == "win32":
            st = _windows_memory_status()
            return st[0] if st else None
        return _linux_mem_available()
    except Exception:
        return None


def _windows_memory_status():
    """GlobalMemoryStatusEx as (ullAvailPhys, ullAvailPageFile), or None."""
    import ctypes

    class _MemoryStatusEx(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    st = _MemoryStatusEx()
    st.dwLength = ctypes.sizeof(_MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return None
    return int(st.ullAvailPhys), int(st.ullAvailPageFile)


# === Explorer thumbnail box ===

# A small sRGB JPEG of the image in a box of the JXL container, for jxl-thumb
# (0.2.0+), which shows it in Windows Explorer instead of decoding the photo.
# The box is an ISO BMFF `uuid` box — the format's own way to carry a
# program's private data: decoders and editors that do not know it skip it
# (djxl, exiftool and this toolkit's box reorder keep it in place; exiftool's
# -tagsfromfile does not copy it, so a re-encode never inherits the source's).
# Not EXIF: a 768 px JPEG does not fit a JPEG's 64 KB EXIF segment, and
# djxl, which copies a JXL's EXIF into the JPEG it writes, then fails.
# Payload: the UUID, then b"jxlphoto-thumbnail:v1:<codestream bytes>:<FNV-1a
# 64 of its first 64 KiB>\n", then the JPEG. jxl-thumb shows it only while
# that fingerprint still names the file's codestream: a program that
# re-encodes the pixels and keeps the box would leave a stale one, ignored.
THUMB_BOX_MARKER = b"jxlphoto-thumbnail:v1:"

THUMB_BOX_UUID = bytes.fromhex("4e0b4016f51c473d9669bc35aebe3c16")


def _write_thumbnail_box(jxl_path, jpeg, fingerprint) -> None:
    """Rewrite `jxl_path` with its thumbnail box replaced: `jpeg` (bytes)
    goes in a fresh box right before the codestream, where a reader finds it
    without crossing the image data; None removes it. Every other box keeps
    its bytes and order. Through a temp file beside it and os.replace."""
    jxl_path = Path(jxl_path)
    tmp = jxl_path.parent / f"{uuid.uuid4().hex}_{jxl_path.name}.tmp"
    try:
        with open(jxl_path, "rb") as f:
            end = f.seek(0, 2)
            if end >= 2:
                f.seek(0)
                if f.read(2) == b"\xff\x0a":
                    raise ValueError("a bare codestream has no container for the box")
            boxes = _jxl_boxes(f, end)
            if not boxes or boxes[-1][1] + boxes[-1][3] != end:
                raise ValueError("unreadable container")
            new_box = b""
            if jpeg is not None:
                payload = (THUMB_BOX_UUID + THUMB_BOX_MARKER
                           + fingerprint.encode("ascii") + b"\n" + jpeg)
                new_box = struct.pack(">I4s", 8 + len(payload), b"uuid") + payload
            with open(tmp, "wb") as out:
                placed = False
                for typ, pos, hdr, size in boxes:
                    if typ == b"uuid":
                        f.seek(pos + hdr)
                        if f.read(16) == THUMB_BOX_UUID:
                            continue              # the old thumbnail
                    if not placed and typ in (b"jxlc", b"jxlp"):
                        out.write(new_box)
                        placed = True
                    f.seek(pos)
                    left = size
                    while left:
                        chunk = f.read(min(left, 1 << 22))
                        if not chunk:
                            raise OSError("short read")
                        out.write(chunk)
                        left -= len(chunk)
                if not placed:
                    out.write(new_box)
        os.replace(str(tmp), str(jxl_path))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _thumbnail_box_fingerprint(jxl_path):
    """The fingerprint recorded in the file's thumbnail box, '' when it has
    none, None when the file cannot be read."""
    try:
        with open(jxl_path, "rb") as f:
            end = f.seek(0, 2)
            for typ, pos, hdr, size in _jxl_boxes(f, end):
                if typ == b"uuid" and size - hdr >= 16 + len(THUMB_BOX_MARKER):
                    f.seek(pos + hdr)
                    head = f.read(min(size - hdr, 16 + len(THUMB_BOX_MARKER) + 64))
                    if (head[:16] == THUMB_BOX_UUID
                            and head[16:16 + len(THUMB_BOX_MARKER)] == THUMB_BOX_MARKER):
                        rest = head[16 + len(THUMB_BOX_MARKER):]
                        return rest.split(b"\n", 1)[0].decode("ascii", "replace")
    except (OSError, struct.error):
        return None
    return ""


# The raw ImageMagick format for each PAM tuple type (and its DEPTH). magick
# copies an image it reads from stdin to a temp file before decoding it when
# the format has a header — PAM, PNG and MIFF alike, whatever -limit says
# (measured: 260 MB written for a 45 MP page, 2026-11-14) — and reads raw
# samples as they come.
_PAM_RAW_FORMATS = {(b"GRAYSCALE", 1): "gray", (b"GRAYSCALE_ALPHA", 2): "graya",
                    (b"RGB", 3): "rgb", (b"RGB_ALPHA", 4): "rgba"}


def _pam_header(data):
    """(width, height, depth, maxval, tupltype, payload start) of the one
    8- or 16-bit PAM image in `data` (djxl's output), or None when it is not
    exactly that: not a P7 header followed by exactly its payload (a second
    frame, a short read), a MAXVAL other than 255 or 65535 (a 12-bit 4095
    read as the full range comes out sixteen times too dark), more than one
    TUPLTYPE line. The callers then take the route through a file, as
    before #671."""
    view = memoryview(data)
    head = bytes(view[:1024])
    end = head.find(b"\nENDHDR\n")
    if not head.startswith(b"P7\n") or end < 0:
        return None
    fields, tupltypes = {}, []
    for line in head[3:end].split(b"\n"):
        line = line.strip()
        if not line or line.startswith(b"#"):
            continue
        key, _, value = line.partition(b" ")
        if key == b"TUPLTYPE":
            tupltypes.append(value.strip())
        else:
            fields[key] = value.strip()
    try:
        w, h = int(fields[b"WIDTH"]), int(fields[b"HEIGHT"])
        depth, maxval = int(fields[b"DEPTH"]), int(fields[b"MAXVAL"])
    except (KeyError, ValueError):
        return None
    if len(tupltypes) != 1 or maxval not in (255, 65535) or w <= 0 or h <= 0 or depth <= 0:
        return None
    start = end + len(b"\nENDHDR\n")
    if len(view) - start != w * h * depth * (1 if maxval == 255 else 2):
        return None
    return w, h, depth, maxval, tupltypes[0], start


def _pam_raw_input(data):
    """(magick input arguments, pixel bytes) for the one PAM image in `data`
    (djxl's output), or None when raw samples cannot carry it exactly
    (_pam_header's cases, or a tuple type with no raw format). The caller
    then hands magick the PAM itself, as before #671.

    The settings of the raw read are reset right after it (+size +depth
    +endian): -depth 16 stays in force otherwise and the JPEG came out with
    other bytes. With the reset, the JPEG is byte for byte the one made from
    the PAM file."""
    head = _pam_header(data)
    if head is None:
        return None
    w, h, depth, maxval, tupltype, start = head
    fmt = _PAM_RAW_FORMATS.get((tupltype, depth))
    if fmt is None:
        return None
    return (["-size", f"{w}x{h}", "-depth", "8" if maxval == 255 else "16", "-endian", "MSB",
             f"{fmt}:-", "+size", "+depth", "+endian"], memoryview(data)[start:])


def _pam_array(data):
    """The one PAM image in `data` as the array imagecodecs.png_decode gives
    for the PNG djxl writes of the same file — (H, W) for one channel, else
    (H, W, C); uint8 for MAXVAL 255, native uint16 for 65535; a writable copy
    — or None for what _pam_header refuses (the caller decodes to a PNG, as
    before #673)."""
    head = _pam_header(data)
    if head is None:
        return None
    w, h, depth, maxval, _tupltype, start = head
    import numpy as np
    a = np.frombuffer(data, dtype=np.uint8 if maxval == 255 else ">u2",
                      count=w * h * depth, offset=start)
    a = a.astype(np.uint8 if maxval == 255 else np.uint16)   # native, writable
    return a.reshape(h, w) if depth == 1 else a.reshape(h, w, depth)


def _make_explorer_thumbnail(g, jxl_path, tmp_dir):
    """An EXPLORER_THUMBNAIL_SIZE px sRGB JPEG of the file (bytes), or None
    with a warning: a thumbnail is never worth failing an archive file.
    djxl decodes WITHOUT a target colour space — asked for one it converts a
    lossless file too while --icc_out still names the original profile (a
    double conversion). Left alone, --icc_out names exactly what the pixels
    are in (the file's own profile, or linear sRGB for a lossy ICC blob) and
    ImageMagick converts from it to sRGB, after the resize.

    The full-size decode never touches the disk (#671): djxl writes the PAM
    to stdout, read into memory (_run_to_memory), and magick reads its
    samples raw from stdin (_pam_raw_input). magick starts only once djxl has
    exited: djxl writes the --icc_out profile AFTER the image, and the
    command names the profile only when it exists. A PAM the raw read cannot
    carry still goes through thumb.pam, the route before #671."""
    tmp_dir = Path(tmp_dir)
    pam, icc, jpg = tmp_dir / "thumb.pam", tmp_dir / "thumb_src.icc", tmp_dir / "thumb.jpg"
    srgb = tmp_dir / "thumb_srgb.icc"
    try:
        if shutil.which("djxl") is None or shutil.which("magick") is None:
            raise RuntimeError("needs djxl and ImageMagick (magick) on PATH")
        from PIL import ImageCms
        srgb.write_bytes(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
        r = g._run_to_memory(["djxl", str(jxl_path), "-", "--output_format=pam",
                              f"--icc_out={icc}"], g.CJXL_TIMEOUT)
        if r.returncode != 0 or not r.stdout:
            raise RuntimeError(f"djxl: {g._stderr_tail(r.stderr)}")
        raw = g._pam_raw_input(r.stdout)
        if raw is None:
            pam.write_bytes(r.stdout)
            source, pixels = [str(pam)], None
        else:
            source, pixels = raw
        size = int(g.EXPLORER_THUMBNAIL_SIZE)
        cmd = ["magick", *source, "-resize", f"{size}x{size}>"]
        if icc.exists() and icc.stat().st_size:
            cmd += ["-profile", str(icc)]
        cmd += ["-profile", str(srgb), "-background", "white", "-flatten", "-strip",
                "-quality", "85", "-sampling-factor", "2x2", "-interlace", "none",
                "jpeg:" + g._magick_out_path(jpg)]
        r = g._run_captured(cmd, g.CJXL_TIMEOUT, input=pixels)
        if r.returncode != 0 or not jpg.exists():
            raise RuntimeError(f"magick: {g._stderr_tail(r.stderr)}")
        return jpg.read_bytes()
    except Exception as e:
        g.logger.warning(f"  >Explorer thumbnail skipped ({e}) | {Path(jxl_path).name}")
        return None
    finally:
        for p in (pam, jpg):
            try:
                p.unlink()
            except OSError:
                pass


def _add_explorer_thumbnail(g, jxl_path, tmp_dir) -> str:
    """Make the thumbnail of a finished output and put it in its box.
    Returns "written", "small" (not worth it: the image is no larger than the
    thumbnail, or the thumbnail would be more than EXPLORER_THUMBNAIL_MAX_SHARE
    of the file — such a file decodes fast anyway) or "failed" (warned).
    The fingerprint is re-read afterwards: the box rewrite copies the
    codestream byte for byte, and a thumbnail must never name the wrong
    pixels."""
    fp = g._codestream_fingerprint(jxl_path)
    if fp is None:
        g.logger.warning(f"  >Explorer thumbnail skipped (not a JPEG XL container) "
                       f"| {Path(jxl_path).name}")
        return "failed"
    jpeg = g._make_explorer_thumbnail(jxl_path, tmp_dir)
    if jpeg is None:
        return "failed"
    import io
    from PIL import Image
    with Image.open(io.BytesIO(jpeg)) as im:
        long_side = max(im.size)
    if (long_side < int(g.EXPLORER_THUMBNAIL_SIZE)
            or len(jpeg) > float(g.EXPLORER_THUMBNAIL_MAX_SHARE) * Path(jxl_path).stat().st_size):
        return "small"
    g._write_thumbnail_box(jxl_path, jpeg, fp)
    if g._codestream_fingerprint(jxl_path) != fp or g._thumbnail_box_fingerprint(jxl_path) != fp:
        raise RuntimeError("the codestream changed while the Explorer thumbnail was written")
    return "written"


def _explorer_thumbnail_wanted(g, lossless: bool) -> bool:
    """auto: every output when jxl-thumb is installed (it shows them), else
    lossless outputs only (they have no preview of their own)."""
    if g.EXPLORER_THUMBNAIL == "on":
        return True
    return g.EXPLORER_THUMBNAIL == "auto" and (lossless or g._jxl_thumb_installed())


# === Provenance ===


def _file_content_id(g, paths) -> str:
    """Stable id for a source's BYTES (one path, or a group of them in order).

    Hashing the file rather than the decoded image on purpose: recomputing this
    at check time must not cost a full decode. Read in 1 MB blocks so a 700 MB
    source is never held in memory.
    """
    if isinstance(paths, (str, Path)):
        paths = [paths]
    outer = hashlib.sha256()
    for p in paths:
        outer.update(g._file_digest_cached(p))
    return outer.hexdigest()[:16]


def _provenance_ok(g, info: dict, src_paths, mode_check: str) -> bool:
    """Did `src_paths` make the output these markers came from?

    `path` compares the recorded LOCATION: free, and it survives re-converting a
    file in place. `content` also accepts a matching set of source BYTES, so it
    survives a MOVED folder — deliberately a superset, because content alone
    would refuse a legitimately re-exported file and break the sync workflow.
    """
    first = src_paths[0] if isinstance(src_paths, (list, tuple)) else src_paths
    if info.get("src") and info["src"] == g._source_path_id(first):
        return True
    if mode_check == "content" and info.get("srcsum"):
        try:
            return info["srcsum"] == g._file_content_id(src_paths)
        except OSError:
            return False
    return False


def _provenance_proves_archive(g, info: dict, src_paths, mode_check: str) -> bool:
    """The DELETE-side proof for a source whose conversion was SKIPPED: the
    existing output must be an archive of the source's CURRENT bytes.

    `_provenance_ok` answers "may this run overwrite that output?", where the
    recorded location is enough. Deleting a source on the strength of an output
    this run never wrote needs more: a source replaced in place by a different
    file with an older date (a restored backup, a copy that kept its
    timestamps) still matches the recorded path, smart sync skips it, and the
    gate deleted the only copy of the new version. So the recorded `srcsum`
    must also match the source as it is now; an output without one proves
    nothing. Fail closed: an unreadable source keeps it.
    """
    if not g._provenance_ok(info, src_paths, mode_check):
        return False
    if not info.get("srcsum"):
        return False
    try:
        return info["srcsum"] == g._file_content_id(src_paths)
    except OSError:
        return False


def _provenance_marker_args(g, src_paths):
    """exiftool argfile lines recording WHICH source made an output."""
    first = src_paths[0] if isinstance(src_paths, (list, tuple)) else src_paths
    lines = ["-XMP-dc:Relation+=" + g.SRC_PREFIX + g._source_path_id(first)]
    try:
        lines.append("-XMP-dc:Relation+=" + g.SRCSUM_PREFIX + g._file_content_id(src_paths))
    except OSError:
        pass        # never fail a conversion over a marker
    return lines


# === xmpMM:History and the visible record ===


def _history_args(g, action: str, src_path, dst_path, record: str = "",
                  codec: str = "") -> list:
    """The one exiftool line appending this run's entry to xmpMM:History:
    which toolkit version wrote the file, when, and from what. Write-only:
    nothing in the toolkit reads it back. Callers never use it for a jbrd
    container or a verbatim copy.

    `codec` names the cjxl or djxl that made the file (the caller's own
    command name). Since v2.30.0 its libjxl version ends the SoftwareAgent —
    "jxl-photo 2.30.0 (URL) with libjxl 0.12.0" — because the same encode
    record means different files in two libjxl versions (0.12 clamps every
    distance below 0.05 to 0.05; 0.11 did not). The version comes from
    _tool_version's cache: one `--version` per codec and run, never one per
    file. Left out when the version cannot be read."""
    when = datetime.now().astimezone().isoformat(timespec="seconds")
    agent = f"{g.TOOLKIT_NAME} {g.TOOLKIT_VERSION} ({g.TOOLKIT_URL})"
    libjxl = _tool_version(codec) if codec else None
    if libjxl:
        agent += f" with libjxl {'.'.join(map(str, libjxl))}"
    params = Path(g.__file__).stem
    src_mime, dst_mime = g._history_mime(src_path), g._history_mime(dst_path)
    if src_mime and dst_mime:
        params += f": from {src_mime} to {dst_mime}"
    # Since v2.24.0 the encode record ("gen=1 | cjxl d=0.05 e=9", or
    # _NO_RECORD_SEGMENT for --encode-tag off) ends the entry's parameters:
    # the History is where the toolkit keeps it (_history_record reads it).
    if record:
        params += f" | {record}"
    fields = [f"Action={g._struct_escape(action)}", f"When={when}",
              f"SoftwareAgent={g._struct_escape(agent)}",
              f"Parameters={g._struct_escape(params)}"]
    return ["-XMP-xmpMM:History+={" + ",".join(fields) + "}"]


def _visible_record_args(g, mode, record, desc, software, comment, label="a file") -> list:
    """exiftool lines for the encode record's VISIBLE copies (v2.24.0).

    The record itself lives in the History entry; this only writes it where
    `mode` shows it — "title" in dc:Description, "software" in Software,
    "comment" in XMP exif:UserComment (Explorer's Comments) — after any user
    text, and removes the machine block from every OTHER field that holds one
    (user text kept). A field is written only when its value changes.
    "history" and "off" show it nowhere. Pure apart from the orphan-token
    warnings."""
    mode = "history" if mode == "xmp" else mode
    lines = []
    for tag, name, value, shown_in in (
            ("-XMP-dc:Description=", "dc:Description", desc, "title"),
            ("-Software=", "Software", software, "software"),
            ("-XMP-exif:UserComment=", "exif:UserComment", comment, "comment")):
        value = str(value or "")
        user, orphans = g._strip_encode_params(value)
        if orphans:
            g.logger.warning(f"Stripped {orphans} orphaned gen= token(s) from {name} "
                           f"of {label} — the field was written badly earlier")
        if mode == shown_in and record:
            new = f"{user} | {record}" if user else record
        else:
            new = user
        if new != value:
            lines.append(tag + g._argfile_safe(new))
    return lines


def _encode_tag_mode(g) -> str:
    """ENCODE_TAG_MODE with its old name resolved: "xmp" (the default before
    v2.24.0) means "history" — the record IS in the XMP; the Title copy is
    "title". Read at use time, so a value set without main() (the wrapper,
    a test) means the same."""
    mode = str(g.ENCODE_TAG_MODE or "history").strip().lower()
    return "history" if mode == "xmp" else mode


# === Other tools' folders, orientation and distance ===

# cjxl >= 0.12 clamps every lossy distance below this to the same value:
# --distance 0.005 ... 0.05 were measured producing BYTE-IDENTICAL output
# (VarDCT and lossy modular alike). libjxl PR #4238 set the floor so the DC
# coefficients stay inside int16 — below it the bitstream leaves Level 5 of
# the JPEG XL spec.
_MIN_EFFECTIVE_DISTANCE = 0.05

# cjxl 0.11.2 (measured, 2026-09-24): only 0.005 and 0.01 were identical;
# 0.02/0.03/0.04 were real steps above 0.05 (d=0.01: +8.7 dB PSNR at 1.75x
# the size of d=0.05). Older builds share the pre-#4238 code.
_MIN_EFFECTIVE_DISTANCE_PRE_012 = 0.01


def _sibling_str_settings(g, script_name: str, names) -> dict:
    """{name: value} of the plain string settings `names` at the top of the
    sibling script `script_name` (next to this one), read with `ast` —
    nothing is imported or executed. Missing or unreadable -> {}."""
    out = {}
    try:
        import ast
        src = (Path(g.__file__).resolve().parent / script_name).read_text(
            encoding="utf-8")
        for node in ast.parse(src).body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id in names
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                out[node.targets[0].id] = node.value.value.strip()
    except Exception:
        pass
    # The script settings overlay changes the sibling's value too: what it
    # sets for that script is the setting its run uses.
    try:
        section = g._settings_overlay_section(g._settings_overlay_path(),
                                            Path(script_name).stem) or {}
        for name in names:
            ok, value = g._overlay_value(("name",), section.get(name))
            if ok:
                out[name] = value
    except Exception:
        pass
    return out


def _skip_other_tool_output(g, parts_below_marker, honor_requested_subfolder: bool = True) -> bool:
    """True if a folder below the export marker is another tool's JXL output
    folder (_other_tool_output_folders) — never a folder the encoder writes
    masters into, and, in mode 7, never the EXPORT_JXL_SUBFOLDER explicitly requested (an
    explicit request wins over this heuristic; mode 6 has none and passes
    honor_requested_subfolder=False). Without it a derivative or a JPEG
    archive next to the master resolved to the master's output: the run
    aborted on the duplicate, or decoded/recompressed the derivative as a
    master."""
    names, suffixes, masters = g._other_tool_output_folders()
    requested = g.EXPORT_JXL_SUBFOLDER.lower() if honor_requested_subfolder else ""
    return any(p != requested and p not in masters
               and (p in names or any(p.endswith(s) for s in suffixes))
               for p in parts_below_marker)


def _log_other_tool_skips(g, count: int) -> None:
    if count:
        names, suffixes, _masters = g._other_tool_output_folders()
        g.logger.info(f"Ignored {count} JXL(s) in other tools' output folders "
                    f"({', '.join(sorted(names))}"
                    + (f", or any folder ending in {', '.join(sorted(suffixes))}"
                       if suffixes else "")
                    + ") — derivatives and JPEG archives, not masters; use another "
                      "mode to process them")


def _orientation_reset_args(g, jxl_path) -> list:
    """exiftool arguments that set the copied EXIF Orientation to 1 when the
    JXL's CODESTREAM carries one (#623): djxl, and cjxl reading a JXL, have
    already turned the pixels, so the tag copied from the Exif box would turn
    them a second time. A JXL made from a TIFF has codestream orientation 1
    and keeps its tag."""
    o = g._jxl_orientation(jxl_path)
    # The XMP mirror of the tag goes too (#634): a reader that prefers the
    # XMP would turn the upright pixels again.
    return (["-IFD0:Orientation#=1", "-XMP-tiff:Orientation="]
            if o not in (None, 1) else [])


def _jxl_orientation(g, jxl_path):
    """The orientation (1-8) a JXL's codestream carries: jxlinfo's answer,
    else the header read in Python (#636: without jxlinfo the copied EXIF
    tag turned the picture a second time). None when neither can tell."""
    o = g._codestream_orientation(g._jxlinfo_text(jxl_path))
    return o if o is not None else g._header_orientation(jxl_path)


def _warn_distance_clamp(g, distance, floor: float = _MIN_EFFECTIVE_DISTANCE) -> None:
    """Say so when a requested distance buys nothing.

    `floor` is the installed cjxl's lossy floor (_min_effective_distance):
    0.05 from libjxl 0.12 on, 0.01 before it.

    Call AFTER setup_logger(): on the module-level logger a warning falls
    through to logging.lastResort — unformatted on stderr, never in the log
    file (bug #238).
    """
    try:
        d = float(distance)
    except (TypeError, ValueError):
        return
    if 0 < d < floor:
        g.logger.warning(
            f"--distance {d} behaves exactly like {floor}: this cjxl "
            f"clamps every lossy distance below that to the same output. "
            f"Use --distance 0 for true lossless.")


def _min_effective_distance(exe: str) -> float:
    """The lossy distance floor of the cjxl that `exe` names.

    0.05 from libjxl 0.12 on, 0.01 before it. An unknown version reads as the
    current behaviour (0.05): the only consequence is a warning that may be
    one version too cautious, never a skipped encode.
    """
    v = _tool_version(exe)
    if v is not None and v[:2] < (0, 12):
        return _MIN_EFFECTIVE_DISTANCE_PRE_012
    return _MIN_EFFECTIVE_DISTANCE


def _tool_at_least(exe: str, major: int, minor: int) -> bool:
    v = _tool_version(exe)
    return v is not None and v[:2] >= (major, minor)


def _keep_alpha_flags(exe: str) -> list:
    """--strip_alpha=0 for the cjxl that `exe` names when it is libjxl 0.13 or
    newer (the flag does not exist before), else nothing.

    cjxl 0.13 drops, by default, an alpha channel with nothing in it (every
    sample opaque) from a LOSSY encode (libjxl #4981): the lossy master of a
    16-bit RGBA TIFF (a Luminar export) came back as an RGB TIFF, one channel
    short (#670). The toolkit keeps every channel its source has, as cjxl 0.12
    did; a 4th channel of an RGB+IR scan also reaches cjxl as alpha. Lossless
    encodes keep alpha by default; passing the flag there changes nothing."""
    return ["--strip_alpha=0"] if _tool_at_least(exe, 0, 13) else []


# === Subprocess and exiftool ===


def _run_captured(g, cmd, timeout, text=False, input=None, stall=None):
    """subprocess.run with stdout/stderr captured through temp FILES.

    capture_output=True makes communicate() start two reader threads per call
    on Windows. Under memory exhaustion a thread can fail in its bootstrap
    (MemoryError) before it signals "started", and Thread.start() then waits
    forever — before the timeout is even armed. The 2026-10-04 scheduled run
    lost a manifest that way (one worker hung 60 min until the wrapper killed
    the child). With files there is no thread: a failed spawn raises OSError,
    which the callers already handle.

    `input` (bytes) goes to the child's stdin, written from THIS thread:
    communicate() starts reader threads only for PIPE stdout/stderr, never
    for stdin.

    `stall` (seconds) is for the planning-time exiftool BATCH reads (#537):
    the child is stopped only when its stdout has not grown for `stall`
    seconds, or after `timeout` seconds in all — a slow disk keeps printing
    and is left to finish, a hung exiftool prints nothing. Polled with
    Popen.wait(timeout=1), which starts no thread. Not combinable with
    `input`.
    """
    cmd = g._long_codec_paths(cmd)
    if stall is not None and input is not None:
        raise ValueError("_run_captured: stall cannot be combined with input")
    # An exiftool argfile whose path the ANSI code page cannot carry goes in
    # on stdin (_argfile_via_stdin) — in a stall run too: exiftool reads its
    # whole -@ list before it opens the first file.
    cmd, _argfile_bytes = g._argfile_via_stdin(cmd)
    if _argfile_bytes is not None:
        input = _argfile_bytes
    stdin_kw = {"stdin": g._DEVNULL} if input is None else {"input": input}
    with tempfile.TemporaryFile(dir=g.TEMP_DIR) as out, \
            tempfile.TemporaryFile(dir=g.TEMP_DIR) as err:
        if stall is None:
            r = subprocess.run(cmd, stdout=out, stderr=err, timeout=timeout,
                               **stdin_kw)
            rc, o, e = r.returncode, r.stdout, r.stderr
        else:
            p = subprocess.Popen(cmd, stdout=out, stderr=err,
                                 stdin=g._DEVNULL if input is None else subprocess.PIPE)
            try:
                if input is not None:
                    p.stdin.write(input)
                    p.stdin.close()
                started = grew = time.monotonic()
                size = 0
                while True:
                    try:
                        rc = p.wait(timeout=1)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                    now = time.monotonic()
                    cur = os.fstat(out.fileno()).st_size
                    if cur != size:
                        size, grew = cur, now
                    if now - grew >= stall or now - started >= timeout:
                        raise subprocess.TimeoutExpired(
                            cmd, stall if now - grew >= stall else timeout)
            except BaseException:
                # Like subprocess.run: a timeout, a Ctrl+C or an fstat error
                # never leaves the child running.
                p.kill()
                p.wait()
                raise
            o = e = None
        out.seek(0)
        err.seek(0)
        o = o if o is not None else out.read()
        e = e if e is not None else err.read()
    if text:
        if isinstance(o, bytes):
            o = o.decode("utf-8", "replace").replace("\r\n", "\n")
        if isinstance(e, bytes):
            e = e.decode("utf-8", "replace").replace("\r\n", "\n")
    return g._CompletedProcess(cmd, rc, o, e)


_PIPE_CHUNK = 1 << 23


def _read_pipe_until_eof(fd, deadline):
    """Everything readable from the pipe `fd` until its writer closes it, as
    bytes, or None when `deadline` (a time.monotonic() value) passes first.

    Only what is ready is read, so a child that is still computing never
    holds this thread past the deadline: on Windows PeekNamedPipe says how
    much is waiting (an empty pipe whose writer is gone raises
    BrokenPipeError: the end), elsewhere select() does. No thread is started
    (see _run_captured). The pieces are joined once at the end: a bytearray
    grown piece by piece copied a 272 MB image some forty times over."""
    chunks = []
    if os.name == "nt":
        import _winapi
        import msvcrt
        handle = msvcrt.get_osfhandle(fd)
        pause = 0.0005
        while True:
            try:
                ready = _winapi.PeekNamedPipe(handle, 0)[0]
            except OSError as exc:
                if isinstance(exc, BrokenPipeError) or getattr(exc, "winerror", None) in (109, 232):
                    break
                raise
            if ready:
                chunk = os.read(fd, min(ready, _PIPE_CHUNK))
                if not chunk:
                    break
                chunks.append(chunk)
                pause = 0.0005
                continue
            if time.monotonic() >= deadline:
                return None
            time.sleep(pause)
            pause = min(pause * 2, 0.05)
        return b"".join(chunks)
    import select
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return None
        if not select.select([fd], [], [], left)[0]:
            return None
        chunk = os.read(fd, _PIPE_CHUNK)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _run_to_memory(g, cmd, timeout):
    """`cmd` run like _run_captured, but its stdout is read into MEMORY in this
    thread and never touches the disk (#671). Returns a CompletedProcess with
    stdout and stderr as bytes.

    For a decode that is only an intermediate: the Explorer thumbnail's
    full-size PAM (272 MB for a 45 MP 16-bit page) used to go to TEMP_DIR once
    per output, and on a slow staging disk those writes set the pace of the
    whole run (7 files a minute instead of 34, 2026-11-14). _run_captured
    cannot carry it: its stdout is a temp FILE, by design.

    The rules of _run_captured hold: no thread (stdout read here,
    _read_pipe_until_eof; stderr to a temp file), long codec paths get their
    prefix, `timeout` holds while the child computes, and a timeout or a
    Ctrl+C never leaves the child running."""
    cmd = g._long_codec_paths(cmd)
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryFile(dir=g.TEMP_DIR) as err:
        p = subprocess.Popen(cmd, stdin=g._DEVNULL, stdout=subprocess.PIPE,
                             stderr=err, bufsize=0)
        try:
            out = _read_pipe_until_eof(p.stdout.fileno(), deadline)
            if out is None:
                raise subprocess.TimeoutExpired(cmd, timeout)
            # The child closed its stdout: it is exiting. A few seconds of
            # grace even when the deadline came at that very moment.
            rc = p.wait(timeout=max(5.0, deadline - time.monotonic()))
        except BaseException:
            p.kill()
            p.wait()
            raise
        finally:
            p.stdout.close()
        err.seek(0)
        return g._CompletedProcess(cmd, rc, out, err.read())


def _exiftool_source(g, path, tmp_dir) -> str:
    """The path to give exiftool as a -tagsFromFile source.

    exiftool format-expands that argument ('%d', '%f', '%e', with modifiers):
    'photo 5%.tif' opened a different, missing file — a warning, exit 0 — and
    the output silently got none of the source's metadata (#550). Doubling the
    '%' does not escape it. A path with a '%' is dumped to a MIE container in
    tmp_dir (the SOURCE operand is not expanded) and that is copied from
    instead; any other path is returned unchanged, at no cost. Raises when the
    dump fails: an output must not go out without the metadata it should
    carry.
    """
    p = str(path)
    if "%" not in p:
        return p
    mie = os.path.join(str(tmp_dir), "metadata_source.mie")
    try:
        os.unlink(mie)
    except OSError:
        pass
    # -unsafe: exiftool leaves its "unsafe" tags (InteropIndex, ...) out of
    # -all:all, and a caller that names one (#637) would find it missing.
    r = g._run_exiftool_argfile(["-o", mie, "-all:all", "-unsafe", p], timeout=g.EXIFTOOL_TIMEOUT)
    if os.path.exists(mie):
        return mie
    if "Nothing to write" in (r.stderr or "") + (r.stdout or ""):
        return p        # no metadata at all: there is nothing to lose
    raise RuntimeError(f"exiftool could not read the metadata of {os.path.basename(p)} "
                       f"(a '%' in its path): {(r.stderr or r.stdout or '')[:200].strip()}")


def _read_creator_and_relation(g, jxl_path: Path):
    """(creator_tool str, [relation tokens]) of one file. Raises on failure:
    a derivative whose source profile cannot be read must not be produced."""
    r = g._run_exiftool_argfile(["-j", "-s", "-s", "-XMP-xmp:CreatorTool",
                               "-XMP-dc:Relation", str(jxl_path)], timeout=g.EXIFTOOL_TIMEOUT)
    if not r.stdout:
        raise RuntimeError(f"exiftool could not read {jxl_path.name} (rc={r.returncode})")
    entry = json.loads(r.stdout)[0]
    rel = entry.get("Relation")
    tokens = [] if rel is None else [str(t).strip() for t in (rel if isinstance(rel, list) else [rel])]
    return str(entry.get("CreatorTool") or ""), tokens


def _xmp_block_args(g, path, tmp_dir) -> list:
    """exiftool lines copying `path`'s XMP into the target as one BLOCK.

    ["-tagsfromfile", path, "-XMP"] — unless the path has a '%', which
    exiftool would expand (#550, _exiftool_source). A MIE container does not
    carry the XMP as a block, so the raw packet is read (the source operand is
    not expanded) into tmp_dir and written back with -XMP<=. [] when the file
    has no XMP.
    """
    p = str(path)
    if "%" not in p:
        return ["-tagsfromfile", p, "-XMP"]
    r = g._run_exiftool_argfile(["-b", "-XMP", p], timeout=g.EXIFTOOL_TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError(f"exiftool could not read the XMP of {os.path.basename(p)} "
                           f"(a '%' in its path): {(r.stderr or '')[:200].strip()}")
    if not (r.stdout or "").strip():
        return []
    xmp = os.path.join(str(tmp_dir), "metadata_source.xmp")
    with open(xmp, "w", encoding="utf-8", newline="") as f:
        f.write(r.stdout)
    return [f"-XMP<={xmp}"]


def _origin_read_args(path):
    """The exiftool command _read_origin_inputs runs (and a prefetch may run
    for it)."""
    return ["-j", "-XMP-dc:Relation", "-XMP-dc:Description", "-EXIF:Software",
            "-XMP-xmpMM:HistoryParameters", str(path)]


def _read_origin_inputs(g, path):
    """(dc:Relation tokens, has_record) of an INPUT file, for
    _origin_for_output. tokens is None when exiftool could not read the file:
    the caller then writes no origin (unknown is never guessed)."""
    try:
        r = g._run_exiftool_argfile(_origin_read_args(path), timeout=g.EXIFTOOL_TIMEOUT)
        if r.returncode != 0 or not r.stdout:
            return None, False
        data = json.loads(r.stdout)
        entry = data[0] if data else {}
    except Exception:
        return None, False
    rel = entry.get("Relation")
    values = [] if rel is None else (rel if isinstance(rel, list) else [rel])
    tokens = [str(v).strip() for v in values if str(v).strip()]
    hist = entry.get("HistoryParameters")
    hist = "\n".join(str(v) for v in (hist if isinstance(hist, list) else [hist or ""]))
    text = f"{entry.get('Description') or ''}\n{entry.get('Software') or ''}\n{hist}"
    return tokens, bool(g._ORIGIN_RECORD_RE.search(text))


# === The planning-time batch reads ===


def _exiftool_batch(g, paths, head, label, on_data, on_no_output=None, on_error=None,
                    on_done=None, strict=False, batch=400, progress=True):
    """The engine of the planning-time batch readers (refactor F7): `paths` in
    chunks of `batch`, ONE exiftool call per chunk (an argfile: the `head`
    lines, then the paths), EXIFTOOL_BATCH_TIMEOUT in all and stopped after
    EXIFTOOL_BATCH_STALL without new output (#537), progress under `label`
    (_batch_read_state / _batch_read_tick, #566). Nothing runs for no paths.

    Each reader keeps its own rules in callbacks, run where its code ran:
    on_data(chunk, r, entries) with the parsed JSON, inside the same try, so
    a surprise in it is handled like a failed batch; on_no_output(chunk, r)
    when exiftool printed nothing (or, with `strict`, also exited non-zero:
    a reader that reads a partial batch as unread); on_error(chunk, e) on any
    exception (a stall, a timeout, JSON cut short); on_done(chunk) after each
    chunk, whatever happened. progress=False: the reader reports its own
    progress (in on_done), and the engine's lines stay out."""
    if not paths:
        return
    _prog = g._batch_read_state(label, len(paths)) if progress else None
    for i in range(0, len(paths), batch):
        chunk = paths[i:i + batch]
        if progress:
            g._batch_read_tick(_prog, i)
        argfile = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                             dir=g.TEMP_DIR, encoding="utf-8",
                                             newline=chr(10)) as af:
                af.write(chr(10).join(list(head) + [str(p) for p in chunk]))
                af.write(chr(10))
                argfile = af.name
            r = g._run_captured([g._get_exiftool_cmd(), "-@", argfile],
                                g.EXIFTOOL_BATCH_TIMEOUT, text=True,
                                stall=g.EXIFTOOL_BATCH_STALL)
            if not r.stdout or (strict and r.returncode != 0):
                if on_no_output is not None:
                    on_no_output(chunk, r)
                continue
            on_data(chunk, r, json.loads(r.stdout))
        except Exception as e:
            if on_error is not None:
                on_error(chunk, e)
        finally:
            if on_done is not None:
                on_done(chunk)
            if argfile:
                try:
                    os.unlink(argfile)
                except OSError:
                    pass


# The head of the batches that read dc:Relation (and the charset lines the
# readers always put after it).
_RELATION_BATCH_HEAD = ("-j", "-s", "-s", "-XMP-dc:Relation",
                        "-charset", "FileName=UTF8", "-charset", "UTF8")


def _read_source_markers_batch(g, outputs: list) -> dict:
    """{output path: {'src': id|None, 'srcsum': id|None}} in as few exiftool
    calls as possible — one per file would be minutes on a large library.

    A file whose markers cannot be read comes back with both None, which the
    caller treats as "cannot prove anything": fail closed. It ALSO carries
    'unread': True — "cannot tell" is not "no marker" (X-A of the 2026-11-03
    audit): an overwrite guard that took a failed batch for a legacy,
    markerless archive let the overwrite through. Unread = the batch failed
    or stalled, or exiftool returned no entry (or an Error) for that file.
    """
    markers = {str(o): {"src": None, "srcsum": None} for o in outputs}
    # normcase -> the exact key the caller will look up by.
    index = {os.path.normcase(str(o)): str(o) for o in outputs}
    answered = set()

    def on_data(chunk, r, data):
        # exiftool exits non-zero when it fails on ANY file of the batch but
        # still prints valid JSON for the rest — use what came back.
        for entry in data:
            src = entry.get("SourceFile")
            if src is not None and not entry.get("Error"):
                # Answered, with or without a Relation: a file with no
                # Relation at all is a real "no marker", not an unread one.
                answered.add(os.path.normcase(str(Path(src))))
            rel = entry.get("Relation")
            if src is None or rel is None:
                continue
            values = rel if isinstance(rel, list) else [str(rel)]
            info = {"src": None, "srcsum": None}
            for token in values:
                token = str(token).strip()
                if token.startswith(g.SRC_PREFIX):
                    info["src"] = token[len(g.SRC_PREFIX):]
                elif token.startswith(g.SRCSUM_PREFIX):
                    info["srcsum"] = token[len(g.SRCSUM_PREFIX):]
            # normcase, like every other path comparison in the provenance
            # layer: exiftool can hand back a differently-cased drive letter
            # or flipped separators, and a lookup miss left the file with
            # both markers None. That reads downstream as "no marker at
            # all", so a file whose provenance is perfectly recorded was
            # refused with "written by an older version" — a reason that
            # sends the user looking for the wrong problem.
            key = os.path.normcase(str(Path(src)))
            if key in index:
                markers[index[key]] = info
            else:
                g.logger.warning(f"Provenance: marker read came back for a path "
                                 f"this run did not ask about, so it cannot be "
                                 f"matched | {src}")

    def on_no_output(chunk, r):
        g.logger.warning(f"Provenance: could not read markers for a batch of "
                         f"{len(chunk)} file(s) (rc={r.returncode})")

    def on_error(chunk, e):
        g.logger.warning(f"Provenance: marker batch failed ({e}); "
                         f"{len(chunk)} file(s) cannot be verified")

    def on_done(chunk):
        for o in chunk:
            if os.path.normcase(str(o)) not in answered:
                markers[str(o)]["unread"] = True
        answered.clear()

    _exiftool_batch(g, outputs, _RELATION_BATCH_HEAD, "Provenance markers", on_data,
                    on_no_output=on_no_output, on_error=on_error, on_done=on_done)
    return markers


def _read_derived_markers_batch(g, paths: list) -> dict:
    """{path str: label | None | False}: the jxlphoto-derived label, None when
    the file has no such token (NOT a derivative), False when it could not be
    read at all (unknown -> the caller must fail closed).

    Same batch engine as _read_source_markers_batch (_exiftool_batch): one
    exiftool call per 400 files instead of one per file.
    """
    markers = {str(o): False for o in paths}
    # normcase -> the exact key the caller will look up by.
    index = {os.path.normcase(str(o)): str(o) for o in paths}

    def on_data(chunk, r, data):
        for entry in data:
            src = entry.get("SourceFile")
            if src is None:
                continue
            key = os.path.normcase(str(Path(src)))
            if key not in index:
                continue
            rel = entry.get("Relation")
            label = None
            if rel is not None:
                values = rel if isinstance(rel, list) else [str(rel)]
                for token in values:
                    token = str(token).strip()
                    if token.startswith(g.DERIVED_XMP_PREFIX):
                        label = token[len(g.DERIVED_XMP_PREFIX):]
                        break
            markers[index[key]] = label

    def on_no_output(chunk, r):
        g.logger.warning(f"Derivative check: could not read markers for a batch "
                         f"of {len(chunk)} file(s) (rc={r.returncode})")

    def on_error(chunk, e):
        g.logger.warning(f"Derivative check: marker batch failed ({e}); "
                         f"{len(chunk)} file(s) cannot be verified")

    _exiftool_batch(g, paths, _RELATION_BATCH_HEAD, "Derivative markers", on_data,
                    on_no_output=on_no_output, on_error=on_error)
    return markers


def _verify_jxl_integrity(g, jxl_path: Path) -> bool:
    """Verify JXL file integrity before deleting source.

    Checks:
    1. File exists and size > 0
    2. File has valid JXL signature (0xFF 0x0A for bare JXL or ISOBMFF box)
    3. For container files: the box chain is walked and must be well-formed
       and end exactly at EOF — catches truncated/short-written files, which
       a signature-only check would accept.
    """
    if not jxl_path.exists():
        return False

    try:
        stat = jxl_path.stat()
        if stat.st_size == 0:
            return False

        # Check JXL signature (first 2 bytes for bare JXL, or first 12 for container)
        with open(jxl_path, 'rb') as f:
            header = f.read(12)

        if len(header) < 2:
            return False

        # Bare JXL starts with 0xFF 0x0A. Every output this toolkit produces
        # is a CONTAINER (exiftool always injects metadata boxes), so a bare
        # codestream at the delete gate means something went wrong mid-write —
        # and a bare file gets no structural validation at all, so a 2-byte
        # stub would pass. Refuse deletion: the source stays.
        if header[0:2] == b'\xff\x0a':
            g.logger.warning(f"Integrity check: bare codestream (no container boxes) — refusing | {jxl_path.name}")
            return False

        # Container format starts with 0x00 0x00 0x00 0x0C 0x4A 0x58 0x4C 0x20 0x0D 0x0A 0x87 0x0A
        if header != b'\x00\x00\x00\x0cJXL \r\n\x87\n':
            return False

        # Walk the ISOBMFF box chain; every box must be well-formed and the
        # chain must end exactly at EOF. A codestream box (jxlc/jxlp) must be
        # present — a metadata-only file must never pass the delete gate.
        #
        # A codestream split into jxlp boxes must also be WHOLE: each jxlp
        # starts with a 4-byte index counting up from 0, and the LAST one has
        # the high bit set (ISO/IEC 18181-2). A file cut exactly at a box
        # boundary is a well-formed chain that ends at EOF — only the missing
        # "last" bit tells it from a complete one. jxlc and jxlp never mix.
        file_size = stat.st_size
        i = 12
        has_codestream = False
        has_jxlc = False
        jxlp_next = 0
        jxlp_ended = False
        with open(jxl_path, 'rb') as f:
            while i < file_size:
                if i + 8 > file_size:
                    return False
                f.seek(i)
                box_header = f.read(8)
                size = int.from_bytes(box_header[0:4], "big")
                box_type = box_header[4:8]
                if box_type in (b"jxlc", b"jxlp"):
                    has_codestream = True
                head = 8
                if size == 1:
                    # Extended 64-bit size
                    if i + 16 > file_size:
                        return False
                    ext = f.read(8)
                    size = int.from_bytes(ext, "big")
                    if size < 16:
                        return False
                    head = 16
                elif size != 0 and size < 8:
                    return False
                if size != 0 and i + size > file_size:
                    return False
                if box_type == b"jxlc":
                    has_jxlc = True
                elif box_type == b"jxlp":
                    end = file_size if size == 0 else i + size
                    if jxlp_ended or end - (i + head) < 4:
                        return False
                    f.seek(i + head)
                    index = int.from_bytes(f.read(4), "big")
                    if (index & 0x7FFFFFFF) != jxlp_next:
                        return False
                    jxlp_next += 1
                    jxlp_ended = bool(index & 0x80000000)
                if size == 0:
                    # Box extends to end of file; must be the last one
                    i = file_size
                    break
                i += size
        if jxlp_next and (has_jxlc or not jxlp_ended):
            return False
        return has_codestream and i == file_size
    except (OSError, IOError):
        return False


# === Disk-full abort ===


def _signal_abort(g, reason):
    """Latch the FIRST reason and announce it once.

    Racing workers all fail within milliseconds of each other, so the latch has
    to be first-wins: the earliest failure is the one that explains the run.
    """
    with g._abort_lock:
        if g._abort_reason is not None:
            return
        g._abort_reason = reason
    g.logger.error(f"ABORTING RUN: {reason}")
    # NOT "nothing was deleted": the delete gate runs after the pool drains,
    # so sources whose output was already written and verified BEFORE this
    # latched are still removed. That is safe — each one passed every gate —
    # but the old wording contradicted the very next line of the log.
    g.logger.error("  Queued files were NOT attempted. Sources already converted and "
                 "verified in this run may still be deleted below; nothing that was "
                 "not attempted is touched.")
    g.logger.error("  Free space, then re-run: sync mode resumes where this stopped.")


def _reset_abort(g):
    """Clear the latch. Called when a run starts (and by the tests).

    Also clears the per-run delete counters: they are run state exactly like the
    latch, and a second run in the SAME process (the test suite, or anything
    importing this module) inherited the first one's totals — so the summary
    reported deletions that this run never made.
    """
    with g._abort_lock:
        g._abort_reason = None
    for _k in g._delete_stats:
        g._delete_stats[_k] = 0


def _aborted(g):
    """The reason the run gave up, or None while it is healthy."""
    return g._abort_reason


# === Staging holds ===


@contextlib.contextmanager
def _staging_hold(g, staging_dir):
    """Mark `staging_dir` as in use by this run while the block runs (#578).
    Reentrant; a no-op without a staging folder."""
    if not staging_dir:
        yield
        return
    key = os.path.normcase(os.path.abspath(str(staging_dir)))
    entry = g._STAGING_HOLDS.get(key)
    if entry is None:
        path = Path(staging_dir) / f"{g._STAGING_LIVE_PREFIX}{uuid.uuid4().hex}"
        fh = None
        try:
            Path(staging_dir).mkdir(parents=True, exist_ok=True)
            fh = open(path, "w+b")
            fh.write(str(os.getpid()).encode("ascii"))
            fh.flush()
            if not g._try_lock_nb(fh):
                raise OSError("the lock could not be taken")
            entry = [path, fh, 0]
        except OSError as e:
            g.logger.warning(f"Staging: could not mark {staging_dir} as in use ({e}) "
                           f"— a --clean-staging run started meanwhile may sweep "
                           f"this run's waiting outputs")
            if fh is not None:
                fh.close()
                try:
                    path.unlink()
                except OSError:
                    pass
            entry = [None, None, 0]
        g._STAGING_HOLDS[key] = entry
    entry[2] += 1
    try:
        yield
    finally:
        entry[2] -= 1
        if entry[2] <= 0:
            g._STAGING_HOLDS.pop(key, None)
            if entry[1] is not None:
                g._unlock_nb(entry[1])
                entry[1].close()
                try:
                    entry[0].unlink()
                except OSError:
                    pass


def _staging_used_by_another_run(g, staging_dir) -> bool:
    """True while another live process holds a `.jxlphoto-live-*` file in
    `staging_dir`. One nobody holds is from a run that died: it is removed."""
    own = {os.path.normcase(str(e[0])) for e in g._STAGING_HOLDS.values()
           if e[0] is not None}
    try:
        marks = [f for f in Path(staging_dir).iterdir()
                 if f.name.startswith(g._STAGING_LIVE_PREFIX)]
    except OSError:
        return False
    busy = False
    for f in marks:
        if os.path.normcase(str(f)) in own:
            continue
        try:
            fresh = time.time() - f.stat().st_mtime < g._STAGING_LIVE_GRACE_SECONDS
        except FileNotFoundError:
            continue
        except OSError:
            busy = True
            continue
        if fresh:
            busy = True             # just created: maybe not locked yet
            continue
        try:
            fh = open(f, "r+b")
        except FileNotFoundError:
            continue
        except OSError:
            busy = True             # cannot even open it: a live holder (fail closed)
            continue
        try:
            if not g._try_lock_nb(fh):
                busy = True
                continue
            g._unlock_nb(fh)
        finally:
            fh.close()
        try:
            f.unlink()
        except OSError:
            pass
    return busy


# === Source identity and planned outputs ===


def _record_source_identity(g, path) -> None:
    """Remember a source's (size, mtime_ns) before this run reads it. The
    first record wins: a multi-page file is read page by page."""
    key = os.path.normcase(os.path.abspath(str(path)))
    if key in g._source_identity:
        return
    try:
        st = os.stat(path)
    except OSError:
        return
    g._source_identity[key] = (st.st_size, st.st_mtime_ns)


def _source_changed_since_read(g, path) -> bool:
    """Is the file at `path` no longer the one this run recorded before
    reading it (rewritten, re-exported or replaced since)? A vanished file
    counts as changed. A source this run never recorded (an already-archived
    one admitted by --delete-skipped, which was never read) returns False:
    its own proof lives elsewhere in the gate."""
    rec = g._source_identity.get(os.path.normcase(os.path.abspath(str(path))))
    if rec is None:
        return False
    try:
        st = os.stat(path)
    except OSError:
        return True
    return (st.st_size, st.st_mtime_ns) != rec


def _record_planned_outputs(g, pairs) -> None:
    """Remember the state of every output of `pairs` ((source, output)).
    The first record of a path wins."""
    for _src, out in pairs:
        key = os.path.normcase(os.path.abspath(str(out)))
        if key in g._planned_outputs:
            continue
        try:
            st = os.stat(out)
        except FileNotFoundError:
            g._planned_outputs[key] = None
        except OSError:
            g._planned_outputs[key] = False
        else:
            g._planned_outputs[key] = (st.st_size, st.st_mtime_ns)


def _output_changed_since_planned(g, final_path):
    """Why `final_path` must not be written now, or None. Not None when a
    file is there that is not the one this run planned over: one that
    appeared, one that changed, or one whose state could not be read at
    planning or cannot be read now (fail closed)."""
    key = os.path.normcase(os.path.abspath(str(final_path)))
    if key not in g._planned_outputs:
        return None
    rec = g._planned_outputs[key]
    try:
        st = os.stat(final_path)
    except FileNotFoundError:
        return None
    except OSError as e:
        return f"the output path could not be checked before the swap ({e})"
    if rec is None:
        return ("a file appeared at the output path after this run planned it "
                "(another run writing the same folder?)")
    if rec is False:
        return "the output path could not be read when this run planned it"
    if (st.st_size, st.st_mtime_ns) != rec:
        return ("the existing output changed after this run judged it "
                "(another run rewrote it?)")
    return None


def _swap_into_place(g, tmp_path, final_path) -> None:
    """Rename a verified temp over its final name — unless the final path is
    no longer what this run planned (#615). Raises RuntimeError on a refusal;
    the caller's handler removes the temp and reports the file as an error.
    An output planned as ABSENT is renamed without replacing on Windows
    (os.rename refuses an existing target there), so a file that appears
    between the check and the rename is not replaced either."""
    why = g._output_changed_since_planned(final_path)
    if why:
        raise RuntimeError(f"REFUSED to write {Path(final_path).name}: {why} — "
                           f"nothing was overwritten and the source is kept")
    key = os.path.normcase(os.path.abspath(str(final_path)))
    if os.name == "nt" and key in g._planned_outputs and g._planned_outputs[key] is None:
        try:
            os.rename(str(tmp_path), str(final_path))
        except FileExistsError:
            raise RuntimeError(
                f"REFUSED to write {Path(final_path).name}: a file appeared at the "
                f"output path after this run planned it (another run writing the "
                f"same folder?) — nothing was overwritten and the source is kept"
            ) from None
        return
    os.replace(str(tmp_path), str(final_path))


def _file_digest_cached(g, path) -> bytes:
    """sha256 of one file's bytes, remembered for this run."""
    try:
        st = os.stat(path)
        key = (os.path.normcase(str(path)), st.st_size, st.st_mtime_ns)
    except OSError:
        key = None
    if key is not None:
        hit = g._content_id_cache.get(key)
        if hit is not None:
            return hit
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    digest = h.digest()
    if key is not None:
        g._content_id_cache[key] = digest
    return digest


# === Tool commands and exiftool argfiles ===


def _get_exiftool_cmd(g):
    if g._exiftool_cmd is None:
        candidates = ["exiftool", "exiftool-k", "exiftool(-k)"]
        for cmd in candidates:
            if shutil.which(cmd) is not None:
                g._exiftool_cmd = cmd
                break
        else:
            g._exiftool_cmd = "exiftool"  # defer and let subprocess fail naturally
    return g._exiftool_cmd


def _get_cjxl_cmd(g):
    if g._cjxl_cmd is None:
        candidates = ["cjxl", "cjxl.exe"]
        for cmd in candidates:
            if shutil.which(cmd) is not None:
                g._cjxl_cmd = cmd
                break
    return g._cjxl_cmd


def _run_exiftool_argfile(g, args_lines, timeout=60):
    """Run exiftool with an argfile (UTF-8 + FileName charset). A read this
    thread prefetched with exactly these arguments (_prefetch_exiftool_reads)
    is answered from there, without starting exiftool.

    Using an argfile instead of raw argv avoids two Windows pitfalls:
    paths containing [ ] being treated as wildcards, and non-ASCII paths
    being decoded with the wrong codepage.
    Returns the CompletedProcess (stdout/stderr decoded as UTF-8, captured
    through temp files — see _run_captured).
    """
    kept = _prefetched_result(g, args_lines)
    if kept is not None:
        return kept
    argfile = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         dir=g.TEMP_DIR, encoding="utf-8", newline="\n") as af:
            argfile = af.name
            af.write(g._ARGFILE_CHARSET)
            if _script_get(g, "EXIFTOOL_IGNORE_MINOR"):
                # Only the decoder has this setting: -m makes exiftool write
                # past an error it calls minor (maker notes it cannot parse).
                af.write("-m\n")
            af.write("\n".join(str(a) for a in args_lines))
            af.write("\n")
        return g._run_captured([g._get_exiftool_cmd(), "-@", argfile],
                             timeout, text=True)
    finally:
        if argfile:
            try:
                os.unlink(argfile)
            except OSError:
                pass


def _jxlinfo_text(g, jxl_path):
    """`jxlinfo -v` output of a JXL (a header read, no decode), or None when
    jxlinfo is not installed, fails or times out. An answer is remembered
    while the file keeps its size and mtime."""
    exe = shutil.which("jxlinfo")
    if not exe:
        if "missing" not in g._JXLINFO_NOTES:
            g._JXLINFO_NOTES.add("missing")
            g.logger.warning("jxlinfo not found (it ships with libjxl, next to djxl): "
                           "float or animated JXLs are not recognised before a "
                           "delete")
        return None
    try:
        _st = os.stat(jxl_path)
        key = (os.path.normcase(str(jxl_path)), _st.st_size, _st.st_mtime_ns)
    except OSError:
        key = None
    if key is not None:
        with g._JXLINFO_CACHE_LOCK:
            if key in g._JXLINFO_CACHE:
                return g._JXLINFO_CACHE[key]
    try:
        r = g._run_captured([exe, "-v", str(jxl_path)], g.EXIFTOOL_TIMEOUT, text=True)
    except Exception:
        return None
    text = (r.stdout or "") if r.returncode == 0 else None
    if key is not None and text is not None:
        with g._JXLINFO_CACHE_LOCK:
            if len(g._JXLINFO_CACHE) >= g._JXLINFO_CACHE_MAX:
                g._JXLINFO_CACHE.clear()
            g._JXLINFO_CACHE[key] = text
    return text


# === Run state and logs ===

# The run summary the jxl_photo.py wrapper reads (--summary-json): ONE line,
# this prefix + JSON, printed by each script's emit_summary_json. The wrapper
# totals a manifest's entries from it and pops these `extras` keys BY NAME for
# its "SOURCES DELETED" block, so each spelling lives here, once: a label
# changed on one side only dropped the deletions from that block silently
# (the recompressor's "deleted sources" once did).
SUMMARY_PREFIX = "##JXLSUM## "
SUMMARY_DELETED = "Sources deleted"
SUMMARY_DELETED_ARCHIVED = "Sources deleted (already archived)"
SUMMARY_KEPT = "Sources KEPT by a delete gate"
SUMMARY_REFUSED = "Refused (output belongs to another source)"

# MIME types named in the xmpMM:History entry (_history_args).
_HISTORY_MIME = {".tif": "image/tiff", ".tiff": "image/tiff",
                 ".jxl": "image/jxl", ".jpg": "image/jpeg",
                 ".jpeg": "image/jpeg", ".png": "image/png"}


def _memory_capped_workers(g, requested, max_pixels, distance, effort,
                           buffering=None, modular=False,
                           extra_bytes_per_pixel=0):
    """--workers lowered so `requested` parallel cjxl processes fit in memory.
    Logs the estimate; warns when it had to lower the count.

    extra_bytes_per_pixel: what each worker holds on the CALLER's side on top
    of its cjxl (the encoder's page array and PNG, #569)."""
    if g.WORKER_MEMORY_FRACTION <= 0:
        return requested
    if g.WORKER_MEMORY_LIMIT not in g._MEMORY_LIMITS:
        g.logger.warning(f"WORKER_MEMORY_LIMIT = {g.WORKER_MEMORY_LIMIT!r} is not one of "
                       f"{', '.join(g._MEMORY_LIMITS)} — using 'both'")
    pixels = max_pixels if max_pixels > 0 else g._UNKNOWN_IMAGE_PIXELS
    per_job = pixels * (g._cjxl_bytes_per_pixel(distance, effort, buffering, modular)
                        + extra_bytes_per_pixel)
    avail, budget = g._available_memory_bytes()
    if avail is None:
        g.logger.info("Memory: could not read the available memory — "
                    "--workers not capped")
        return requested
    cap = max(1, int(avail * g.WORKER_MEMORY_FRACTION // per_job))
    workers = min(requested, cap)
    if modular and distance > 0:
        kind = "modular"
    elif g._cjxl_whole_image(distance, effort, buffering):
        kind = "whole-image"
    else:
        kind = "streaming"
    _extra = (f", incl. ~{pixels * extra_bytes_per_pixel / 2**30:.1f} GB in Python"
              if extra_bytes_per_pixel > 0 else "")
    g.logger.info(f"Memory: ~{per_job / 2**30:.1f} GB per worker ({kind} encode, "
                f"{pixels / 1e6:.0f} MP, d={distance} e={effort}{_extra}) | "
                f"{avail / 2**30:.1f} GB available ({budget}) | workers {workers}")
    if cap < requested:
        hint = ""
        if kind == "whole-image" and effort >= 8:
            # Measured (cjxl 0.12, docs/README_jxl_recompressor.md, "Streaming
            # vs whole-image"): streaming skips the extra effort-9 work.
            hint = (" This distance/effort makes cjxl encode the whole image at "
                    "once. --buffering 1 would stream it, but a streamed effort "
                    "8+ encode is effort 7's file (~8% larger): for a light run "
                    "use effort 7 instead.")
        elif kind == "whole-image":
            hint = (" This distance/effort makes cjxl encode the whole image at "
                    "once; --buffering 1 keeps it streaming (~3x less memory, "
                    "files ~1.5% larger, same quality).")
        g.logger.warning(f"--workers {requested} reduced to {cap}: {requested} cjxl "
                       f"processes at ~{per_job / 2**30:.1f} GB each would not "
                       f"fit in the {avail / 2**30:.1f} GB "
                       f"{g._MEMORY_BUDGET_TEXT[budget]} (WORKER_MEMORY_FRACTION="
                       f"{g.WORKER_MEMORY_FRACTION}, WORKER_MEMORY_LIMIT="
                       f"{g.WORKER_MEMORY_LIMIT}).{hint}")
    return workers


def _jxl_thumb_installed(g) -> bool:
    """True when jxl-thumb is Explorer's thumbnail handler for .jxl for this
    Windows user (one registry read per run). JXLPHOTO_JXL_THUMB=1/0
    overrides the check — for the test suite, and for a run whose thumbnails
    are meant for another user or PC."""
    env = os.environ.get("JXLPHOTO_JXL_THUMB")
    if env in ("0", "1"):
        return env == "1"
    if not g._jxl_thumb_seen:
        found = False
        if os.name == "nt":
            try:
                import winreg
                for sub in g._JXL_THUMB_HANDLER_KEYS:
                    try:
                        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, sub) as key:
                            value = str(winreg.QueryValue(key, None) or "").strip()
                    except OSError:
                        continue
                    if value:
                        found = value.upper() == g._JXL_THUMB_CLSID
                        break
            except Exception:
                found = False
        g._jxl_thumb_seen.append(found)
    return g._jxl_thumb_seen[0]


def _history_mime(path):
    """MIME type of a path for the History entry, or None. A staging temp
    '<uuid>_<name>.tmp' is named by the extension before '.tmp'."""
    p = Path(str(path))
    if p.suffix.lower() == ".tmp":
        p = Path(p.stem)
    return _HISTORY_MIME.get(p.suffix.lower())


def _sharpen_args(g, preset, sigma=None, gain=None, threshold=None, grey=False):
    """ImageMagick args for output sharpening, or [] for none. Colour images are
    sharpened on the Lab L channel only (no colour fringes); a grey image has a
    single channel and is sharpened directly. `-colorspace Lab ... -colorspace
    sRGB` DROPS the ICC profile — the caller must re-assign the output profile
    right after these args (verified: pixels stay identical)."""
    if not preset or preset == "none":
        return []
    p = dict(g.SHARPEN_PRESETS[preset])
    if sigma is not None:
        p["sigma"] = float(sigma)
    if gain is not None:
        p["gain"] = float(gain)
    if threshold is not None:
        p["threshold"] = float(threshold)
    op = f"0x{p['sigma']}+{p['gain']}+{p['threshold']}"
    if grey:
        return ["-unsharp", op]
    return ["-colorspace", "Lab", "-channel", "R", "-unsharp", op, "+channel",
            "-colorspace", "sRGB"]


def next_count(g):
    with g.counter_lock:
        g._counter["done"] += 1
        return g._counter["done"], g._counter["total"]


def _log_gen_notes_once(g, stored: int, counted: int) -> None:
    """Divergence between the stored gen= and the chain length is information,
    not an error (max() already kept the safer value) — log it once per run,
    not per file. Legacy files (no gen= token, stored == 0) are the normal
    case and never count as divergence.
    """
    if stored and stored != counted and not g._gen_divergence_logged:
        g._gen_divergence_logged = True
        g.logger.info("At least one file's stored gen= disagrees with its cjxl "
                    "chain length — kept the larger (safer) value; the field "
                    "was corrected on the restamp.")


def _log_settings_overlay(g) -> None:
    """The overlay's audit lines (setup_logger calls it): which file and which
    settings it changed, and every entry it refused."""
    result = _script_get(g, "_SETTINGS_OVERLAY") or {}
    if result.get("applied"):
        g.logger.info(f"Script settings overlay: {result['path']}: "
                    f"{len(result['applied'])} value(s): {', '.join(result['applied'])}")
    for warning in result.get("warnings", ()):
        g.logger.warning(f"Script settings overlay: {warning}")


def _other_tool_output_folders(g) -> tuple:
    """(names, suffixes, masters), lowercased: the JXL OUTPUT folders of the
    OTHER tools (_OTHER_TOOL_FOLDER_SOURCES, _OTHER_TOOL_FIXED_FOLDERS,
    _OTHER_TOOL_SUFFIX_SOURCES) — each configured value AND its shipped
    default — and the folders the encoder writes masters into
    (_MASTER_FOLDER_SOURCES, same rule), which are never among them: masters
    are what these modes are for, and a name both tools use (converted_jxl)
    must keep its masters. Read from the siblings' settings once (X-B of the
    2026-11-03 audit; the encoder's _decoder_output_folders is the mirror),
    then cached."""
    if g._other_tool_folders_cache is None:
        wanted = {}
        for script, setting, _default in (g._OTHER_TOOL_FOLDER_SOURCES
                                          + g._OTHER_TOOL_SUFFIX_SOURCES
                                          + g._MASTER_FOLDER_SOURCES):
            wanted.setdefault(script, set()).add(setting)
        read = {s: g._sibling_str_settings(s, n) for s, n in wanted.items()}

        def _values(script, setting, default):
            got = (read.get(script) or {}).get(setting) or ""
            return {v.strip().lower() for v in (got, default) if v.strip()}

        masters = set()
        for src in g._MASTER_FOLDER_SOURCES:
            masters |= _values(*src)
        names = {n.lower() for n in g._OTHER_TOOL_FIXED_FOLDERS}
        for src in g._OTHER_TOOL_FOLDER_SOURCES:
            names |= _values(*src)
        suffixes = set()
        for src in g._OTHER_TOOL_SUFFIX_SOURCES:
            suffixes |= {"_" + v for v in _values(*src)}
        names -= masters
        g._other_tool_folders_cache = (frozenset(names), frozenset(suffixes),
                                     frozenset(masters))
    return g._other_tool_folders_cache


def _log_rejected_file(g, file_path, reason):
    """Log rejected files to Logs/<script>/rejected_files.log for easy review."""
    try:
        rej_dir = g.LOG_DIR
        rej_dir.mkdir(parents=True, exist_ok=True)
        rej_file = rej_dir / "rejected_files.log"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Locked: called from worker threads; concurrent appends would interleave.
        with g._rejected_log_lock:
            with open(rej_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} | {reason} | {file_path}\n")
    except Exception:
        pass


# === Pixel verification ===


def _decode_jxl_for_verify(g, jxl_path: Path, tmp_dir: Path,
                           require_original_space=False):
    """Decode a JXL back to pixels. Raises on any failure — a verification that
    cannot run must never read as a verification that passed.

    require_original_space: also raise when djxl did not return the pixels in
    the file's own colour space (a lossy ICC blob decodes to linear sRGB), so
    a comparison against the SOURCE pixels says why it cannot run (#584).

    The pixels come from a PAM djxl writes to stdout, read into memory
    (#673): the same arrays as the PNG it used to write to tmp_dir (117 of
    117 real masters), without the PNG — 236 MB written and 8.7 s of libpng
    deflate inside djxl for a 45 MP page, most of a deleting run's time. A
    PAM _pam_array cannot carry (a 12-bit file: the PNG holds it scaled to 16
    bits) is decoded to the PNG, as before."""
    icc_args, out_icc, orig_icc = (g._djxl_icc_args(tmp_dir) if require_original_space
                                   else ([], None, None))
    r = g._run_to_memory(["djxl", str(jxl_path), "-", "--output_format=pam"] + icc_args,
                         g.CJXL_TIMEOUT)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"djxl: {g._stderr_tail(r.stderr)}")
    if require_original_space and g._decoded_in_original_space(out_icc, orig_icc) is False:
        raise RuntimeError(
            "lossy ICC blob: djxl decodes it to linear sRGB, so the pixel check "
            "cannot compare it with the source — source kept "
            "(ICC_PNG_STRATEGY 'cautious' never writes such files)")
    arr = g._pam_array(r.stdout)
    if arr is not None:
        return arr
    png_path = tmp_dir / "verify.png"
    r = g._run_captured(["djxl", str(jxl_path), str(png_path)] + icc_args, g.CJXL_TIMEOUT)
    if r.returncode != 0 or not png_path.exists():
        raise RuntimeError(f"djxl: {g._stderr_tail(r.stderr)}")
    # imagecodecs, never PIL: PIL silently quantises 16-bit RGB/RGBA PNGs to
    # 8-bit, which would make every lossless comparison fail for a reason that
    # has nothing to do with the encode. Checked up front in main().
    import imagecodecs
    return imagecodecs.png_decode(png_path.read_bytes())


def _canon_for_compare(g, a):
    """Both sides of the comparison in one shape/dtype convention.

    The encoder feeds cjxl a 3D array (2D pages get a trailing axis) and
    promotes 8-bit to 16-bit with *257; djxl gives back 2D for a grayscale JXL.
    Squeeze the single-channel axis away on both so a grayscale page compares
    as (H, W) either way.
    """
    if a.ndim == 3 and a.shape[2] == 1:
        a = a[:, :, 0]
    if a.dtype == g.np.uint8:
        return a.astype(g.np.uint16) * 257
    if a.dtype != g.np.uint16:
        return a.astype(g.np.uint16)
    return a


def _compare_stats(g, src, dec):
    """(source mean, decoded mean, MSE), accumulated over row blocks."""
    total = src.size
    s_sum = d_sum = sq = 0.0
    for y in range(0, src.shape[0], g._VERIFY_CHUNK_ROWS):
        a = src[y:y + g._VERIFY_CHUNK_ROWS].astype(g.np.float64)
        b = dec[y:y + g._VERIFY_CHUNK_ROWS].astype(g.np.float64)
        s_sum += float(a.sum())
        d_sum += float(b.sum())
        diff = a - b
        sq += float((diff * diff).sum())
    return s_sum / total, d_sum / total, sq / total
