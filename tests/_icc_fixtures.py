#!/usr/bin/env python3
"""Shared fixtures for the ICC-blob tests: a table-curve ICC profile built
from scratch (no third-party file needed)."""

import struct

import numpy as np

import jxl_recompressor as rec


def romm_toe_icc() -> bytes:
    """ProPhoto primaries, D50, ROMM TRC with the linear toe (E < 1/512 -> 16E)
    as a 4096-entry curv table: a profile with NO native JPEG XL form."""
    base = bytearray(rec._build_matrix_trc_icc(
        "ProPhoto ROMM toe (test)", [(0.7347, 0.2653), (0.1596, 0.8404), (0.0366, 0.0001)],
        (0.3457, 0.3585), 1.8))
    x = np.linspace(0, 1, 4096)
    y = np.where(x < 16 / 512, x / 16, x ** 1.8)
    curv = b"curv" + b"\0" * 4 + struct.pack(">I", 4096) + np.round(y * 65535).astype(">u2").tobytes()
    n = struct.unpack(">I", base[128:132])[0]
    body = bytes(base)
    while len(body) % 4:
        body += b"\0"
    new_off = len(body)
    body += curv
    while len(body) % 4:
        body += b"\0"
    b = bytearray(body)
    for i in range(n):
        sig, off, ln = struct.unpack(">4sII", b[132 + 12 * i:144 + 12 * i])
        if sig in (b"rTRC", b"gTRC", b"bTRC"):
            struct.pack_into(">4sII", b, 132 + 12 * i, sig, new_off, len(curv))
    struct.pack_into(">I", b, 0, len(b))
    return bytes(b)


def grey_toe_icc() -> bytes:
    """A GREY display profile (D50 white, kTRC) whose tone curve is the same
    ROMM-with-linear-toe 4096-entry table: like Photoshop's "Dot Gain" grey
    profiles, it has NO native JPEG XL form, so a lossy cjxl stores the blob
    and djxl returns linear grey."""
    def _xyz(x, y, z):
        return b"XYZ " + b"\0" * 4 + b"".join(
            struct.pack(">i", round(v * 65536)) for v in (x, y, z))
    desc_txt = b"Grey ROMM toe (test)\0"
    desc = (b"desc" + b"\0" * 4 + struct.pack(">I", len(desc_txt)) + desc_txt
            + b"\0" * (4 + 4 + 2 + 1 + 67))
    x = np.linspace(0, 1, 4096)
    y = np.where(x < 16 / 512, x / 16, x ** 1.8)
    curv = (b"curv" + b"\0" * 4 + struct.pack(">I", 4096)
            + np.round(y * 65535).astype(">u2").tobytes())
    tags = [(b"desc", desc), (b"wtpt", _xyz(0.9642, 1.0, 0.8249)),
            (b"kTRC", curv), (b"cprt", b"text" + b"\0" * 4 + b"PD\0\0")]
    off = 128 + 4 + 12 * len(tags)
    table, data = b"", b""
    for sig, d in tags:
        while len(d) % 4:
            d += b"\0"
        table += struct.pack(">4sII", sig, off + len(data), len(d))
        data += d
    body = struct.pack(">I", len(tags)) + table + data
    hdr = bytearray(128)
    struct.pack_into(">I", hdr, 0, 128 + len(body))
    hdr[8:12] = bytes([2, 0x10, 0, 0])
    hdr[12:16], hdr[16:20], hdr[20:24], hdr[36:40] = b"mntr", b"GRAY", b"XYZ ", b"acsp"
    hdr[68:80] = b"".join(struct.pack(">i", round(v * 65536)) for v in (0.9642, 1.0, 0.8249))
    return bytes(hdr) + body
