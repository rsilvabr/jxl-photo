"""Probe X-A: an exiftool batch that fails (rc!=0, no stdout) leaves the
output's markers at None — the same value as "no markers at all" — and the
non-strict overwrite guards then let the overwrite through. No codecs."""
import importlib.util
import subprocess
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
enc = load("jxl_tiff_encoder")
tr = load("jxl_jpeg_transcoder")


def failing_batch(cmd, timeout, text=False, input=None, stall=None):
    return subprocess.CompletedProcess(cmd, 1, "", "exiftool: stalled (simulated)")


with tempfile.TemporaryDirectory() as d:
    out = Path(d) / "foto [a].jxl"
    out.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n")   # not jbrd
    src = Path(d) / "foto [a].tif"
    src.write_bytes(b"II*\x00")
    for mod in (enc, tr):
        mod._run_captured = failing_batch
        marks = mod._read_source_markers_batch([out])
        info = marks[str(out)]
        print(mod.__name__, "unreadable batch ->", info)
    print("encoder _foreign_overwrite_reason ->",
          enc._foreign_overwrite_reason(info, src, out), "(None = overwrite allowed)")
    kept, refused = tr._foreign_overwrite_filter([(src, out)], False,
                                                  lambda s, o: True)
    print("transcoder _foreign_overwrite_filter refused ->", refused,
          "(empty = overwrite allowed)")
