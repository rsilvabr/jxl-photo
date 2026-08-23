#!/usr/bin/env python3
"""M3 — _read_multipage_markers_batch must match exiftool's SourceFile back to
the requested path case-insensitively.

exiftool can hand back a differently-cased drive letter or flipped separators.
An exact-case lookup miss wrote the marker info under a key no caller ever
looks up, so the file kept the standalone defaults: a marked multi-page group
decoded as loose single-page TIFFs, and with --delete-source each page passed
the single-page integrity check and was deleted. _read_source_markers_batch
was fixed for exactly this hazard (tests/test_round29_lows.py); this is the
same pattern applied to the multipage reader.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_tiff_decoder as dec


class _FakeRun:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.mark.parametrize("mangle", ["upper", "slashes"],
                         ids=["recased", "flipped-separators"])
def test_multipage_marker_lookup_survives_a_recased_path(monkeypatch, tmp_path, mangle):
    jxl = tmp_path / "Scan" / "Photo_page2.JXL"
    jxl.parent.mkdir()
    jxl.write_bytes(b"\x00")
    dec.setup_logger()

    returned = str(jxl).upper() if mangle == "upper" else str(jxl).replace("\\", "/")
    payload = json.dumps([{"SourceFile": returned,
                           "Relation": [dec.MULTIPAGE_MARKER_PREFIX + "GRP1",
                                        dec.PAGES_PREFIX + "3",
                                        dec.PAGE_PREFIX + "1"]}])
    monkeypatch.setattr(dec.subprocess, "run",
                        lambda *a, **k: _FakeRun(stdout=payload, returncode=0))

    markers = dec._read_multipage_markers_batch([jxl])
    # The caller looks up marker_map.get(str(j)); the marker must land there,
    # not under the differently-cased key exiftool handed back.
    assert markers[str(jxl)]["group"] == "GRP1"
    assert markers[str(jxl)]["pages"] == 3
    assert markers[str(jxl)]["page"] == 1
    assert len(markers) == 1
