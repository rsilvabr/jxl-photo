"""8-bit TIFFs are archived lossless at low distances (EIGHT_BIT_DISTANCE).

Measured on 40 photos (2026-11-06, docs/file_sizes.md): an 8-bit TIFF's
lossless JXL is a quarter of a 16-bit one's (43 MB at 45 MP) and d=0.05 comes
out LARGER than it; d=0.1 saves only ~24%. So the encoder gives an 8-bit TIFF
that is not a film scan its own distance, like SCAN_DISTANCE for scans:

- "auto" (default): lossless when the run's distance is
  EIGHT_BIT_LOSSLESS_UP_TO (0.1) or less, the run's distance above it;
- "same": the run's distance; a number: that distance.

Real codecs and exiftool; every file inside a folder named with Japanese,
brackets, a space and %.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "菜の花 [8bit] 50%"
_TOOLS = ("cjxl", "djxl", "exiftool")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")


# ------------------------------------------------------------------ the rule
@pytest.mark.parametrize("setting, run_d, expected", [
    ("auto", 0.0, 0.0), ("auto", 0.05, 0.0), ("auto", 0.1, 0.0),
    ("auto", 0.2, 0.2), ("auto", 1.0, 1.0),
    ("same", 0.05, 0.05), (None, 0.05, 0.05),
    ("0.5", 0.05, 0.5), (0.3, 1.0, 0.3), ("0", 1.0, 0.0),
])
def test_eight_bit_distance(monkeypatch, setting, run_d, expected):
    monkeypatch.setattr(enc, "EIGHT_BIT_DISTANCE", setting)
    monkeypatch.setattr(enc, "EIGHT_BIT_LOSSLESS_UP_TO", 0.1)
    monkeypatch.setattr(enc, "CJXL_DISTANCE", run_d)
    assert enc._eight_bit_distance() == expected


def test_the_threshold_is_read_from_its_setting(monkeypatch):
    monkeypatch.setattr(enc, "EIGHT_BIT_DISTANCE", "auto")
    monkeypatch.setattr(enc, "CJXL_DISTANCE", 0.15)
    monkeypatch.setattr(enc, "EIGHT_BIT_LOSSLESS_UP_TO", 0.15)
    assert enc._eight_bit_distance() == 0.0
    monkeypatch.setattr(enc, "EIGHT_BIT_LOSSLESS_UP_TO", 0.1)
    assert enc._eight_bit_distance() == 0.15


@pytest.mark.parametrize("value", ["", "lossy", "-1", "26", "nan?"])
def test_a_bad_setting_is_refused(value):
    with pytest.raises(ValueError):
        enc._parse_eight_bit_distance(value)


@pytest.mark.parametrize("kind, bits, expected", [
    (None, 8, 0.0),        # an 8-bit photo: lossless
    (None, 16, 0.1),       # a 16-bit photo: the run's distance
    (None, 0, 0.1),        # bits unknown: never guessed 8-bit
    ("final", 8, 0.1),     # an 8-bit scan outside --scan-scope raw: scans are not touched
    ("raw", 8, 0.0),       # an 8-bit scan: SCAN_DISTANCE (0), not this rule
])
def test_record_scan_applies_it_to_8bit_non_scans_only(monkeypatch, kind, bits, expected):
    monkeypatch.setattr(enc, "CJXL_DISTANCE", 0.1)
    monkeypatch.setattr(enc, "EIGHT_BIT_DISTANCE", "auto")
    monkeypatch.setattr(enc, "SCAN_SCOPE", "raw")
    monkeypatch.setattr(enc, "SCAN_DISTANCE", 0.0)
    monkeypatch.setattr(enc, "_SCAN_PLAN", {})
    monkeypatch.setattr(enc, "_SCAN_REASON", {})
    monkeypatch.setattr(enc, "_EIGHT_BIT_PLAN", set())
    enc._record_scan("p.tif", kind, None, bits)
    assert enc._distance_for("p.tif") == expected
    assert (os.path.normcase("p.tif") in enc._EIGHT_BIT_PLAN) == (kind is None and bits == 8)


def test_a_refusal_names_eight_bit_distance(monkeypatch):
    monkeypatch.setattr(enc, "CJXL_DISTANCE", 0.1)
    monkeypatch.setattr(enc, "EIGHT_BIT_DISTANCE", "auto")
    monkeypatch.setattr(enc, "_SCAN_PLAN", {})
    monkeypatch.setattr(enc, "_SCAN_REASON", {})
    monkeypatch.setattr(enc, "_EIGHT_BIT_PLAN", set())
    enc._record_scan("photo8.tif", None, None, 8)
    hint = enc._scan_distance_hint("photo8.tif")
    assert "an 8-bit TIFF" in hint and "--eight-bit-distance same" in hint
    assert "film scan" not in hint


# ------------------------------------------------------------- real codecs
def _run(tmp_path, *args, ok=True):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs")}
    r = subprocess.run([sys.executable, str(REPO / "jxl_tiff_encoder.py"), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, env=env)
    if ok:
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return r.returncode, " ".join((r.stdout + r.stderr).split())


def _tiff(path, dtype, seed):
    """A photo with a Software tag: neither a guessed nor a real film scan."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:180, 0:240]
    base = np.stack([x * 200 // 240, y * 200 // 180, (x + y) * 100 // 420], -1)
    a = base * 257 + rng.integers(0, 4000, (180, 240, 3))
    a = (a if dtype == np.uint16 else a // 257).astype(dtype)
    tifffile.imwrite(path, a, photometric="rgb", software="Capture One 23 Windows",
                     metadata=None)
    return a


def _desc(path):
    af = Path(os.environ.get("TEMP", ".")) / f"eightbit_{os.getpid()}.args"
    af.write_text(f"-charset\nfilename=utf8\n-j\n-XMP-dc:Description\n{path}\n",
                  encoding="utf-8")
    try:
        out = subprocess.run(["exiftool", "-@", str(af)], capture_output=True).stdout
    finally:
        af.unlink()
    return str(json.loads(out)[0].get("Description") or "")


@needs_tools
def test_one_run_archives_the_8bit_tiff_lossless_and_the_16bit_one_at_its_distance(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    src8 = _tiff(d / "DSC_0001 (2) [8].tif", np.uint8, 1)
    _tiff(d / "DSC_0002 16.tif", np.uint16, 2)
    _rc, log = _run(tmp_path, d, "--distance", "0.1", "--effort", "3", "--no-preflight")
    assert ("8-bit TIFFs: 1 TIFF(s) to encode at distance 0 (lossless) instead of 0.1 "
            "(EIGHT_BIT_DISTANCE auto)") in log, log[-3000:]
    assert "8-bit TIFFs: 1 TIFF(s) encoded at distance 0 (lossless)" in log
    assert _desc(d / "DSC_0001 (2) [8].jxl").endswith("cjxl d=0.0 e=3")
    assert _desc(d / "DSC_0002 16.jxl").endswith("cjxl d=0.1 e=3")
    # lossless for real: the pixels come back exactly
    out = tmp_path / "out"
    subprocess.run([sys.executable, str(REPO / "jxl_tiff_decoder.py"),
                    str(d / "DSC_0001 (2) [8].jxl"), str(out)],
                   capture_output=True, check=True,
                   env={**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs")})
    with tifffile.TiffFile(out / "DSC_0001 (2) [8].tif") as t:
        back = t.pages[0].asarray()
    # the decoder writes 16-bit TIFFs: an exact 8-bit value v comes back as v * 257
    if back.dtype == np.uint16:
        back = back // 257 if np.array_equal(back % 257, np.zeros_like(back)) else back
    assert np.array_equal(back, src8)


@needs_tools
def test_above_the_threshold_and_with_same_the_8bit_tiff_follows_the_run(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    _tiff(d / "a [1].tif", np.uint8, 3)
    _rc, log = _run(tmp_path, d, "--distance", "0.5", "--effort", "3", "--no-preflight")
    assert "8-bit TIFFs:" not in log
    assert _desc(d / "a [1].jxl").endswith("cjxl d=0.5 e=3")
    _run(tmp_path, d, "--distance", "0.05", "--effort", "3", "--no-preflight",
         "--overwrite", "--eight-bit-distance", "same")
    assert _desc(d / "a [1].jxl").endswith("cjxl d=0.05 e=3")
    _run(tmp_path, d, "--distance", "0.05", "--effort", "3", "--no-preflight",
         "--overwrite", "--eight-bit-distance", "0.3")
    assert _desc(d / "a [1].jxl").endswith("cjxl d=0.3 e=3")


@needs_tools
def test_mode8_deletes_the_8bit_source_after_a_lossless_check(tmp_path):
    """The delete gate judges each output at the distance it was encoded at:
    the 8-bit TIFF bit-exact (lossless), the 16-bit one by its lossy check."""
    d = tmp_path / HOSTILE / "sub [x]"
    d.mkdir(parents=True)
    _tiff(d / "eight.tif", np.uint8, 5)
    _tiff(d / "sixteen.tif", np.uint16, 6)
    _run(tmp_path, tmp_path / HOSTILE, "--mode", "8", "--distance", "0.1", "--effort", "3",
         "--no-preflight", "--delete-source", "--delete-confirm-off", "--verify-roundtrip")
    assert sorted(p.name for p in d.iterdir()) == ["eight.jxl", "sixteen.jxl"]


@needs_tools
def test_a_bad_value_stops_the_run_before_any_encode(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    _tiff(d / "a.tif", np.uint8, 7)
    rc, log = _run(tmp_path, d, "--distance", "0.1", "--eight-bit-distance", "lossy", ok=False)
    assert rc == 2 and "--eight-bit-distance" in log
    assert not (d / "a.jxl").exists()
