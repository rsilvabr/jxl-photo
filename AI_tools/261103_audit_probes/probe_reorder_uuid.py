"""Probe E-G: does reorder_jxl_boxes keep the Explorer-thumbnail uuid box
before the codestream? Builds a synthetic container: signature, ftyp,
uuid (thumbnail), jxlc, then an Exif box appended after the codestream (what
exiftool does). Pure Python: no codecs needed.

Usage: python probe_reorder_uuid.py <repo root> [script.py ...]
(default: the encoder, the recompressor and the transcoder)
"""
import importlib.util
import struct
import sys
import tempfile
from pathlib import Path

repo = Path(sys.argv[1])
names = sys.argv[2:] or ["jxl_tiff_encoder.py", "jxl_recompressor.py",
                         "jxl_jpeg_transcoder.py"]


def box(typ, payload):
    return struct.pack(">I4s", 8 + len(payload), typ) + payload


sig = b"\x00\x00\x00\x0cJXL \r\n\x87\n"
ftyp = box(b"ftyp", b"jxl \x00\x00\x00\x00jxl ")
thumb_uuid = bytes.fromhex("4e0b4016f51c473d9669bc35aebe3c16")
uuidbox = box(b"uuid", thumb_uuid + b"jxlphoto-thumbnail:v1:10:0000000000000000\n" + b"JPEGDATA")
jxlc = box(b"jxlc", b"\xff\x0a" + b"\x00" * 8)
exif = box(b"Exif", b"\x00\x00\x00\x00MM\x00*")
data = sig + ftyp + uuidbox + jxlc + exif

sys.argv = ["probe"]
for name in names:
    spec = importlib.util.spec_from_file_location(Path(name).stem, repo / name)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "DSC_0001 [a] 写真.jxl"
        p.write_bytes(data)
        m.reorder_jxl_boxes(p)
        out = p.read_bytes()
        i, order = 12, []
        while i < len(out):
            size, typ = struct.unpack(">I4s", out[i:i + 8])
            order.append(typ.decode())
            i += size
        print(f"{name}: before ['ftyp','uuid','jxlc','Exif'] -> after {order}")
