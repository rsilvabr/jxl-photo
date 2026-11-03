"""Probe D-C: does the decoder's mode 6/7 discovery pick up the recompressor's
derivative folder (_EXPORT/16B_JXL_small) next to the masters (_EXPORT/16B_JXL),
and do both resolve to the same TIFF? Pure Python: dummy files, no codecs."""
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
with tempfile.TemporaryDirectory() as d:
    root = Path(d) / "250118_吾妻山公園 [FINAL]" / "01_Z7"
    master = root / "_EXPORT" / "16B_JXL" / "DSC_0001 (2).jxl"
    small = root / "_EXPORT" / rec.EXPORT_JXL_FOLDER / "DSC_0001 (2).jxl"
    for p in (master, small):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n")
    for mode, finder in ((6, dec.find_jxls_mode6), (7, dec.find_jxls_mode7)):
        found = finder(root.parent)
        outs = [dec.resolve_output(f, mode, root.parent) for f in found]
        print(f"decoder mode {mode}: found {len(found)} ->",
              sorted({str(o.relative_to(root)) for o in outs}))
