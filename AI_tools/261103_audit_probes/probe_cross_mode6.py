"""Probe X-B: the encoder (TIFF16 -> 16B_JXL) and the transcoder (JPEG ->
JXL_jpeg) both write JXLs under _EXPORT in modes 6/7. Which of the later
mode-6 scans (decoder, recompressor) pick up both, and do they collide?"""
import importlib.util
import sys
import tempfile
from pathlib import Path

repo = Path(sys.argv[1])


def load(name):
    spec = importlib.util.spec_from_file_location(name, repo / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sys.argv = ["probe"]
dec = load("jxl_tiff_decoder")
rec = load("jxl_recompressor")
tr = load("jxl_jpeg_transcoder")
enc = load("jxl_tiff_encoder")
with tempfile.TemporaryDirectory() as d:
    ev = Path(d) / "250118_吾妻山公園 [FINAL]" / "01_Z7"
    tif = ev / "_EXPORT" / "TIFF16" / "DSC_0001 (2).tif"
    jpg = ev / "_EXPORT" / "JPEG" / "DSC_0001 (2).jpg"
    for p in (tif, jpg):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    enc_out = enc.resolve_output(tif, 6, ev)
    tr_out = tr.resolve_output_transcode(jpg, 6, ev, False)
    print("encoder mode 6 ->", enc_out.relative_to(ev))
    print("transcoder JPEG->JXL mode 6 ->", tr_out.relative_to(ev))
    for p in (enc_out, tr_out):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n")
    for name, mod, finder in (("decoder", dec, dec.find_jxls_mode6),
                              ("recompressor", rec, rec.find_jxls_mode6)):
        found = finder(ev)
        outs = [mod.resolve_output(f, 6, ev) for f in found]
        print(f"{name} mode 6: {len(found)} inputs ->",
              sorted({str(o.relative_to(ev)) for o in outs if o is not None}))
