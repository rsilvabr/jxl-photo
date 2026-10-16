#!/usr/bin/env python3
"""v2.15.0: the space preflight counts only what the run will WRITE.

The encoder and the recompressor projected the space of every planned file,
up-to-date ones included: a nightly sync over a whole library sampled three
crops and estimated 193 GB for a run that re-encoded 164 of 1 344 photos
(log of 2026-10-13). An up-to-date output is a SKIP and needs no space.

Real codecs, each test in its own tmp_path. The scripts run as subprocesses
through a tiny wrapper that replaces `_preflight_space` with a probe printing
how many files it was handed (`PFCOUNT n`), then calls main().

Pre-fix proof (must fail on the 7969b1c scripts):

    mkdir <tmp>\\pfpre
    git show 7969b1c:jxl_tiff_encoder.py > <tmp>\\pfpre\\jxl_tiff_encoder.py
    git show 7969b1c:jxl_recompressor.py > <tmp>\\pfpre\\jxl_recompressor.py
    $env:PF_SCRIPTS = "<tmp>\\pfpre"
    python -m pytest tests/test_preflight_counts_writes.py -q
"""

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import tifffile

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("PF_SCRIPTS") or REPO).resolve()

_HAVE_TOOLS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
real = pytest.mark.skipif(not _HAVE_TOOLS,
                          reason="needs cjxl, djxl and exiftool on PATH")

_ENC_PROBE = """\
import sys
sys.path.insert(0, {d!r})
import jxl_tiff_encoder as m
m._preflight_space = lambda groups, *a, **k: print(
    "PFCOUNT", sum(len(v) for v in groups.values()), flush=True)
sys.argv = [{s!r}] + sys.argv[1:]
m.main()
"""

_REC_PROBE = """\
import sys
sys.path.insert(0, {d!r})
import jxl_recompressor as m
m._preflight_space = lambda items: print("PFCOUNT", len(items), flush=True)
sys.argv = [{s!r}] + sys.argv[1:]
m.main()
"""


def _probe(tmp_path, name, template):
    w = tmp_path / f"probe_{name}"
    w.write_text(template.format(d=str(SCRIPTS), s=str(SCRIPTS / name)),
                 encoding="utf-8")
    return w


def _run(script, *args):
    return subprocess.run([sys.executable, str(script)] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=600,
                          stdin=subprocess.DEVNULL)


def _count(r):
    hits = re.findall(r"^PFCOUNT (\d+)$", r.stdout, re.MULTILINE)
    assert hits, r.stdout + r.stderr
    return int(hits[-1])


def _tiff(path: Path, value: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.full((48, 64, 3), value, np.uint16),
                     photometric="rgb")


def _touch_future(p: Path):
    t = time.time() + 60
    os.utime(p, (t, t))


ENC_FAST = ["--mode", "8", "--distance", "1", "--effort", "3"]


@real
def test_p1_encoder_sync_counts_only_what_it_reencodes(tmp_path):
    probe = _probe(tmp_path, "jxl_tiff_encoder.py", _ENC_PROBE)
    lib = tmp_path / "lib"
    _tiff(lib / "a.tif", 1000)
    _tiff(lib / "b.tif", 2000)

    r = _run(probe, lib, *ENC_FAST)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _count(r) == 2                      # first run: everything is new

    r = _run(probe, lib, *ENC_FAST, "--sync")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 reconverted" in r.stdout, r.stdout
    assert _count(r) == 0, "an up-to-date master was counted"

    _touch_future(lib / "a.tif")
    r = _run(probe, lib, *ENC_FAST, "--sync")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _count(r) == 1


@real
def test_p2_recompressor_sync_counts_only_what_it_writes(tmp_path):
    lib = tmp_path / "t"
    _tiff(lib / "a.tif", 1000)
    _tiff(lib / "b.tif", 2000)
    r = _run(SCRIPTS / "jxl_tiff_encoder.py", lib, tmp_path / "m", "--mode", "2",
             "--distance", "0.1", "--effort", "3", "--no-preflight")
    assert r.returncode == 0, r.stdout + r.stderr

    probe = _probe(tmp_path, "jxl_recompressor.py", _REC_PROBE)
    rec_args = [tmp_path / "m", "--mode", "1", "--distance", "2", "--effort", "3",
                "--on-unknown", "convert", "--on-downgrade", "convert"]
    r = _run(probe, *rec_args)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _count(r) == 2

    r = _run(probe, *rec_args, "--sync")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _count(r) == 0, "an up-to-date output was counted"

    _touch_future(tmp_path / "m" / "a.jxl")
    r = _run(probe, *rec_args, "--sync")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _count(r) == 1
