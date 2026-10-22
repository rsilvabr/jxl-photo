"""#546: a run killed between writing an output's temp and renaming it into
place leaves '<uuid>_<name>.tmp' beside the output. Nothing adopts it (the
.tmp suffix), but nothing removed it either: the battery's kill test found
one, a complete 16 MB master, after the re-run had written the real file.

Each backend now removes, right after planning, the leftover temps of the
outputs it is about to write. Only those: a temp of another output, a file
without the uuid prefix and anything in a dry run stay. Real codecs and
exiftool; every file inside a folder named with Japanese, brackets, a space
and %.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

import jxl_jpeg_transcoder as tra
import jxl_recompressor as rec
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "吾妻山 [tmp] 100%"
HEX = "66d01bd17938470a8671a6c03539a7cb"
HEX2 = "0123456789abcdef0123456789abcdef"
_TOOLS = ("cjxl", "djxl", "exiftool", "magick")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")


def _run(tmp_path, script, *args):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs")}
    r = subprocess.run([sys.executable, str(REPO / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, env=env)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return r.stdout + r.stderr


def _photo(h=240, w=320, seed=1):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    base = np.stack([x * 200 // w, y * 200 // h, (x + y) * 100 // (w + h)], -1)
    return (base * 257 + rng.integers(0, 2000, (h, w, 3))).astype(np.uint16)


def _jxl(tmp_path, dest, distance):
    """cjxl reads no TIFF and cannot open a Japanese path itself (ANSI fopen):
    a 16-bit PPM beside, encoded there, then copied in."""
    a = _photo()
    ppm = tmp_path / "src.ppm"
    ppm.write_bytes(b"P6\n%d %d\n65535\n" % (a.shape[1], a.shape[0]) + a.astype(">u2").tobytes())
    subprocess.run(["cjxl", str(ppm), str(tmp_path / "p.jxl"), "-d", str(distance), "-e", "3"],
                   check=True, capture_output=True)
    shutil.copy(tmp_path / "p.jxl", dest)


def _orphan(folder, name, data=b"partial"):
    p = folder / name
    p.write_bytes(data)
    return p


@pytest.mark.parametrize("mod", [enc, dec, tra, rec], ids=lambda m: m.__name__)
def test_sweep_removes_only_the_planned_outputs_temps(tmp_path, mod):
    d = tmp_path / HOSTILE
    (d / "sub").mkdir(parents=True)
    final = d / "p (1).jxl"
    final.write_bytes(b"final")
    gone = [
        _orphan(d, f"{HEX}_p (1).jxl.tmp"),             # a write or promotion temp
        _orphan(d, f"{HEX}_p (1).tmp"),                 # the recompressor's in-place temp
        _orphan(d, f"{HEX2}_{HEX}_p (1).jxl.tmp.tmp"),  # a box rewrite of a temp
        _orphan(d, f"{HEX}_repair_p (1).tmp"),          # the transcoder's --repair-jbrd temp
    ]
    kept = [
        final,
        _orphan(d, f"{HEX}_q (1).jxl.tmp"),             # another output's temp
        _orphan(d, "backup_p (1).jxl.tmp"),             # no uuid prefix: not ours
        _orphan(d, f"{HEX[:31]}_p (1).jxl.tmp"),        # 31 hex digits: not ours
        _orphan(d, f"{HEX}_p (1).jxl"),                 # no .tmp suffix
        _orphan(d / "sub", f"{HEX}_p (1).jxl.tmp"),     # another folder
    ]
    assert mod._sweep_orphan_temps([final]) == len(gone)
    assert not any(p.exists() for p in gone)
    assert all(p.exists() for p in kept)
    assert mod._sweep_orphan_temps([final]) == 0


def test_the_four_copies_match_the_same_names():
    """_sweep_orphan_temps is parity-pinned; the pattern it reads is a module
    constant, so pin that too."""
    assert len({m._ORPHAN_TEMP_RE.pattern for m in (enc, dec, tra, rec)}) == 1


@pytest.mark.parametrize("mod", [enc, dec, tra, rec], ids=lambda m: m.__name__)
def test_sweep_survives_a_missing_folder(tmp_path, mod):
    assert mod._sweep_orphan_temps([tmp_path / "nope" / "x.jxl"]) == 0


@needs_tools
def test_encoder_rerun_removes_the_killed_runs_temp(tmp_path):
    """The battery's case: mode 8 writes the master beside the TIFF; a kill
    left the complete temp of one of them. The next run writes the master
    and removes the temp; another output's temp stays."""
    d = tmp_path / HOSTILE
    d.mkdir()
    tifffile.imwrite(d / "p (1).tif", _photo(), photometric="rgb")
    orphan = _orphan(d, f"{HEX}_p (1).jxl.tmp")
    other = _orphan(d, f"{HEX}_other (2).jxl.tmp")
    args = [d, "--mode", "8", "--distance", "1", "--effort", "3", "--no-preflight"]
    _run(tmp_path, "jxl_tiff_encoder.py", *args, "--dry-run")
    assert orphan.exists(), "a dry run must not delete anything"
    out = _run(tmp_path, "jxl_tiff_encoder.py", *args)
    assert (d / "p (1).jxl").exists()
    assert not orphan.exists()
    assert other.exists()
    assert "interrupted run" in " ".join(out.split())
    # Skipped by the sync on a second run: its temp still goes.
    orphan = _orphan(d, f"{HEX2}_p (1).jxl.tmp")
    _run(tmp_path, "jxl_tiff_encoder.py", *args)
    assert not orphan.exists()


@needs_tools
def test_decoder_removes_the_temp_of_its_tiff(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    _jxl(tmp_path, d / "p (1).jxl", 1)
    orphan = _orphan(d, f"{HEX}_p (1).tif.tmp")
    _run(tmp_path, "jxl_tiff_decoder.py", d, "--mode", "8")
    assert (d / "p (1).tif").exists()
    assert not orphan.exists()


@needs_tools
def test_transcoder_removes_the_temp_of_its_jxl(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    subprocess.run(["magick", "-size", "320x240", "gradient:red-blue", "-quality", "90",
                    str(tmp_path / "p.jpg")], check=True)
    shutil.copy(tmp_path / "p.jpg", d / "p (1).jpg")
    orphan = _orphan(d, f"{HEX}_p (1).jxl.tmp")
    _run(tmp_path, "jxl_jpeg_transcoder.py", d, "--mode", "8")
    assert (d / "p (1).jxl").exists()
    assert not orphan.exists()


@needs_tools
def test_recompressor_in_place_removes_its_stem_temp(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    _jxl(tmp_path, d / "p (1).jxl", 0.5)
    orphan = _orphan(d, f"{HEX}_p (1).tmp")
    _run(tmp_path, "jxl_recompressor.py", d, "--mode", "8", "--distance", "2", "--effort", "3",
         "--on-unknown", "convert", "--on-regeneration", "convert",
         "--on-downgrade", "convert", "--no-preflight", "--delete-confirm-off")
    assert not orphan.exists()
    assert not list(d.glob("*.tmp"))
