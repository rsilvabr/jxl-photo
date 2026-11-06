"""The encode record of a file, wherever the toolkit keeps it (tests only).

Since v2.24.0 the record (`gen=N | cjxl d=X e=Y | …`) lives in the newest
xmpMM:History entry that carries one; files written before keep it in
dc:Description or Software. Tests that only need its VALUE read it here, so
they do not care which visible copy (--encode-tag) a run made. Reads through
a UTF-8 argfile: the test folders have Japanese names.
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

import jxl_recompressor as _rec


def record_fields(path) -> dict:
    """{'history', 'desc', 'software', 'comment'} of `path` (History = the
    record _history_record finds; the others are the raw field values)."""
    fd, af = tempfile.mkstemp(suffix=".args")
    os.close(fd)
    try:
        Path(af).write_text(
            "-charset\nfilename=utf8\n-j\n-XMP-xmpMM:HistoryParameters\n"
            f"-XMP-dc:Description\n-Software\n-XMP-exif:UserComment\n{path}\n",
            encoding="utf-8")
        out = subprocess.run(["exiftool", "-@", af], capture_output=True).stdout
    finally:
        os.unlink(af)
    data = json.loads(out.decode("utf-8")) if out.strip() else [{}]
    e = data[0] if data else {}
    return {"history": _rec._history_record(e.get("HistoryParameters")),
            "desc": str(e.get("Description") or ""),
            "software": str(e.get("Software") or ""),
            "comment": str(e.get("UserComment") or "")}


def encode_record(path) -> str:
    """The record a reader trusts: the History's, else the chain found in
    dc:Description / Software (a file written before v2.24.0)."""
    f = record_fields(path)
    if f["history"]:
        return f["history"]
    blocks = []
    for text in (f["desc"], f["software"]):
        blocks += [b.strip().lstrip("|").strip() for b in _rec._MACHINE_BLOCK_RE.findall(text)]
    return " | ".join(blocks)
