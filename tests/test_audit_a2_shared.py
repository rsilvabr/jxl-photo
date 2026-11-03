#!/usr/bin/env python3
"""A2 of the 2026-11-03 audit — the helpers duplicated across the backends.

  E-C (#575) _long_codec_paths (4 copies) skipped RELATIVE paths, so an input
             given relative to a deep working folder reached cjxl/djxl past
             260 characters without the \\\\?\\ prefix.
  E-F (#578) --clean-staging of one run swept the outputs another run had
             waiting in a shared staging folder (4 copies of _clean_staging).
  E-I (#583) reorder_jxl_boxes (3 copies) held the file twice in memory and
             built the output with a quadratic `out +=`.

Every fixture path is hostile: Japanese, `[ ]`, a space, `%`, parentheses.

Pre-fix proof (must fail on the base commit's scripts):

    git show 488e8b6:<script>.py > <tmp>/a2pre/<script>.py   (the 4 backends)
    JXL_SCRIPTS_UNDER_TEST=<tmp>/a2pre python -m pytest tests/test_audit_a2_shared.py -q
"""

import importlib
import os
import shutil
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

import numpy as np
import pytest
import tifffile

_REPO = Path(__file__).resolve().parent.parent
_OVERRIDE = os.environ.get("JXL_SCRIPTS_UNDER_TEST")
_SCRIPTS = Path(_OVERRIDE).resolve() if _OVERRIDE else _REPO
sys.path.insert(0, str(_SCRIPTS))

HOSTILE = "250118_吾妻山公園_菜の花 [FINAL] 5%"
BACKENDS = ["jxl_tiff_encoder", "jxl_tiff_decoder", "jxl_jpeg_transcoder",
            "jxl_recompressor"]
REORDERERS = ["jxl_tiff_encoder", "jxl_jpeg_transcoder", "jxl_recompressor"]
_HAVE = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
real = pytest.mark.skipif(not _HAVE, reason="needs cjxl, djxl and exiftool on PATH")
windows = pytest.mark.skipif(os.name != "nt", reason="the 260-character limit is Windows'")


def _deep_dir(tmp_path, total=215) -> Path:
    """A real folder whose absolute path is about `total` characters."""
    d = tmp_path / HOSTILE
    i = 0
    while len(str(d)) < total - 20:
        i += 1
        d = d / f"深い [d{i}] 5%"
    d.mkdir(parents=True)
    return d


# ===========================================================================
# E-C (#575)
# ===========================================================================

@windows
@pytest.mark.parametrize("name", BACKENDS)
def test_ec_relative_long_path_gets_the_prefix(name, tmp_path, monkeypatch):
    mod = importlib.import_module(name)
    cwd = _deep_dir(tmp_path)
    monkeypatch.chdir(cwd)
    rel = os.path.join("sub 吾妻 [x] (1)", "another long folder name 5%", "DSC_0001 (2).png")
    assert len(os.path.join(str(cwd), rel)) >= mod._LONG_PATH_THRESHOLD
    out = mod._long_codec_paths(["cjxl", rel, "out.jxl", "-d", "0.1", "--effort", "7"])
    assert out[1] == "\\\\?\\" + os.path.abspath(rel), out[1]
    assert out[2] == "out.jxl", "a short relative path must stay as it is"
    assert out[3:] == ["-d", "0.1", "--effort", "7"], "option values were rewritten"


@real
@windows
def test_ec_real_cjxl_relative_long_path(tmp_path):
    cwd = _deep_dir(tmp_path, total=205)
    src = Path("TIFF16 [吾妻] (1)") / "a much longer export folder name 5%"
    (cwd / src).mkdir(parents=True)
    tifffile.imwrite(str(cwd / src / "DSC_0001 (2).tif"),
                     np.full((32, 48, 3), 30000, np.uint16), photometric="rgb")
    out_rel = Path("16B_JXL [吾妻] (1)") / "a much longer output folder name 5% (export 2025)"
    final = cwd / out_rel / "DSC_0001 (2).jxl"
    assert len(str(final)) >= 260, len(str(final))
    r = subprocess.run([sys.executable, str(_SCRIPTS / "jxl_tiff_encoder.py"),
                        str(src), str(out_rel), "--mode", "0", "--distance", "0",
                        "--effort", "3", "--no-preflight"], cwd=str(cwd),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, stdin=subprocess.DEVNULL)
    assert final.exists(), (r.stdout + r.stderr)[-3000:]


# ===========================================================================
# E-F (#578)
# ===========================================================================

_HOLDER = r'''
import os, sys, time
stg, ready, release = sys.argv[1], sys.argv[2], sys.argv[3]
if sys.argv[4] == "helper":
    sys.path.insert(0, sys.argv[5])
    import importlib
    m = importlib.import_module(sys.argv[6])
    cm = m._staging_hold(stg)
    cm.__enter__()
else:   # the lock exactly as a live run holds it, without the helper
    import msvcrt
    fh = open(os.path.join(stg, ".jxlphoto-live-" + "ab" * 16), "w+b")
    fh.write(b"1"); fh.flush(); fh.seek(0)
    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
open(ready, "w").close()
for _ in range(600):
    if os.path.exists(release):
        break
    time.sleep(0.05)
'''


def _leftover(stg: Path) -> Path:
    f = stg / ("0123456789abcdef" * 2 + "_DSC_0001 (2).jxl")
    f.write_bytes(b"x" * 64)
    old = time.time() - 7200
    os.utime(f, (old, old))
    return f


def _start_holder(tmp_path, stg, how, name):
    ready, release = tmp_path / "ready", tmp_path / "release"
    p = subprocess.Popen([sys.executable, "-c", _HOLDER, str(stg), str(ready),
                          str(release), how, str(_SCRIPTS), name],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(400):
        if ready.exists() or p.poll() is not None:
            break
        time.sleep(0.05)
    return p, ready, release


@windows
@pytest.mark.parametrize("how", ["by-hand", "helper"])
@pytest.mark.parametrize("name", BACKENDS)
def test_ef_live_run_blocks_the_sweep(name, how, tmp_path):
    mod = importlib.import_module(name)
    mod.setup_logger()
    stg = tmp_path / "ステージング [s] 5%"
    stg.mkdir()
    waiting = _leftover(stg)
    p, ready, release = _start_holder(tmp_path, stg, how, name)
    try:
        assert ready.exists(), p.communicate(timeout=30)
        mod._clean_staging(stg)
        assert waiting.exists(), "another live run's waiting output was swept"
    finally:
        release.write_text("")
        p.wait(timeout=60)
    # The holder is gone: its lock file is stale and the sweep runs.
    mod._clean_staging(stg)
    assert not waiting.exists(), "the leftover of a finished run was not swept"
    assert not list(stg.glob(".jxlphoto-live-*")) or how == "by-hand", (
        "the dead run's lock file was not removed")


@pytest.mark.parametrize("name", BACKENDS)
def test_ef_hold_leaves_staging_clean(name, tmp_path):
    mod = importlib.import_module(name)
    stg = tmp_path / "ステージング [t] 5%"
    with mod._staging_hold(stg):
        assert len(list(stg.glob(".jxlphoto-live-*"))) == 1
        with mod._staging_hold(stg):                     # reentrant
            assert len(list(stg.glob(".jxlphoto-live-*"))) == 1
        assert len(list(stg.glob(".jxlphoto-live-*"))) == 1
    assert list(stg.iterdir()) == []
    with mod._staging_hold(None):
        pass


# ===========================================================================
# E-I (#583)
# ===========================================================================

def _box(name: bytes, payload: bytes, size0=False) -> bytes:
    size = 0 if size0 else 8 + len(payload)
    return size.to_bytes(4, "big") + name + payload


def _old_reorder(data: bytes, rank) -> bytes:
    """reorder_jxl_boxes as it was at 488e8b6, on valid input (reference)."""
    boxes, i, n = [], 0, len(data)
    while i < n:
        size = int.from_bytes(data[i:i + 4], "big")
        name = data[i + 4:i + 8]
        if size == 0:
            boxes.append((name, data[i:i + 8], data[i + 8:]))
            break
        boxes.append((name, data[i:i + 8], data[i + 8:i + size]))
        i += size
    order, extra, cs, other, pre = [], [], [], [], []
    for b in boxes:
        if b[0] in {b"JXL ", b"ftyp", b"jxll"}:
            order.append(b)
        elif b[0] in {b"Exif", b"xml ", b"jbrd", b"brob"}:
            extra.append(b)
        elif b[0] in {b"jxlc", b"jxlp"}:
            cs.append(b)
        elif cs:
            other.append(b)
        else:
            pre.append(b)
    extra.sort(key=rank)
    ordered = order + extra + pre + cs + other
    out = b""
    for idx, (name, h, p) in enumerate(ordered):
        if int.from_bytes(h[0:4], "big") == 0 and idx < len(ordered) - 1:
            h = (8 + len(p)).to_bytes(4, "big") + h[4:8]
        out += h + p
    return out


_SIG = _box(b"JXL ", b"\r\n\x87\n")
_FTYP = _box(b"ftyp", b"jxl \x00\x00\x00\x00jxl ")
_LAYOUTS = {
    "lossless": _SIG + _FTYP + _box(b"jxll", b"\x0a") + _box(b"jxlc", b"\xff\x0a" + b"c" * 300)
                + _box(b"xml ", b"<x/>") + _box(b"Exif", b"\x00" * 4 + b"II*\x00"),
    "lossy-jxlp": _SIG + _FTYP + b"".join(_box(b"jxlp", bytes([0, 0, 0, k]) + b"p" * 99)
                                        for k in range(3))
                  + _box(b"Exif", b"\x00" * 8) + _box(b"brob", b"xml " + b"z" * 20),
    "size0-last": _SIG + _FTYP + _box(b"Exif", b"e" * 10) + _box(b"jxlc", b"c" * 500, size0=True),
    "size0-moved": _SIG + _FTYP + _box(b"jxlc", b"c" * 400)
                   + _box(b"Exif", b"e" * 30, size0=True),
    "brob-exif": _SIG + _FTYP + _box(b"xml ", b"<x/>") + _box(b"jxlc", b"c" * 64)
                 + _box(b"brob", b"Exif" + b"b" * 40),
    "uuid-thumb": _SIG + _FTYP + _box(b"uuid", b"u" * 50) + _box(b"jxlc", b"c" * 64)
                  + _box(b"Exif", b"e" * 12),
}


@pytest.mark.parametrize("layout", sorted(_LAYOUTS))
@pytest.mark.parametrize("name", REORDERERS)
def test_ei_reorder_bytes_unchanged(name, layout, tmp_path):
    """Equivalence pin (no pre-fix proof)."""
    mod = importlib.import_module(name)
    data = _LAYOUTS[layout]
    f = tmp_path / HOSTILE / f"{layout} (1).jxl"
    f.parent.mkdir(parents=True)
    f.write_bytes(data)
    mod.reorder_jxl_boxes(f)
    assert f.read_bytes() == _old_reorder(data, mod._exif_box_rank)


@pytest.mark.parametrize("name", REORDERERS)
def test_ei_no_quadratic_concat(name, tmp_path):
    mod = importlib.import_module(name)
    payload = b"p" * 4096
    data = (_SIG + _FTYP + b"".join(_box(b"jxlp", k.to_bytes(4, "big") + payload)
                                   for k in range(2000))
            + _box(b"Exif", b"\x00" * 8))
    f = tmp_path / HOSTILE / "many boxes (1).jxl"
    f.parent.mkdir(parents=True)
    f.write_bytes(data)
    tracemalloc.start()
    try:
        mod.reorder_jxl_boxes(f)
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1.5 * len(data), (
        f"peak {peak / 2**20:.1f} MiB for a {len(data) / 2**20:.1f} MiB file")
