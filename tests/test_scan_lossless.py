"""v2.17.0: film scans stay lossless.

A raw negative recompressed to d=0.3 showed clearly visible loss after the
owner's usual processing (inversion and curves magnify an error that lossy
JPEG XL keeps just below visibility in the file as stored), and many "final"
scans are the only copy left. So:

- the encoder gives a film scan (SCANNER_SOFTWARE, or an IR channel) its own
  distance, SCAN_DISTANCE = 0 (lossless) by default, in the same run as the
  other TIFFs; --scan-scope raw narrows it to raw scans;
- every page of a scan carries jxlphoto-scan:raw|final;
- the recompressor never re-encodes a scan lossy unless --on-scan says so
  (default copy), recognising unmarked masters by their Software tag or IR
  page; derivatives and lossless requests are not affected.

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

import jxl_recompressor as rec
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "吾妻山 [tmp] 100%"
_TOOLS = ("cjxl", "djxl", "exiftool")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")


def _run(tmp_path, script, *args):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs")}
    r = subprocess.run([sys.executable, str(REPO / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, env=env)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return r.stdout + r.stderr


def _photo(h=200, w=280, seed=1, dtype=np.uint16):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    base = np.stack([x * 200 // w, y * 200 // h, (x + y) * 100 // (w + h)], -1)
    a = base * 257 + rng.integers(0, 4000, (h, w, 3))
    return (a if dtype == np.uint16 else a // 257).astype(dtype)


def _tiff(path, software, dtype=np.uint16, seed=1):
    a = _photo(seed=seed, dtype=dtype)
    tifffile.imwrite(path, a, photometric="rgb", software=software, metadata=None)
    return a


def _exif(*args):
    """exiftool through a UTF-8 argfile: the paths are Japanese."""
    af = Path(os.environ.get("TEMP", ".")) / f"scanll_{os.getpid()}.args"
    af.write_text("-charset\nfilename=utf8\n" + "\n".join(map(str, args)) + "\n",
                  encoding="utf-8")
    try:
        return subprocess.run(["exiftool", "-@", str(af)], capture_output=True).stdout
    finally:
        af.unlink()


def _meta(path):
    d = json.loads(_exif("-j", "-XMP-dc:Relation", "-XMP-dc:Description", path))[0]
    rel = d.get("Relation") or []
    return ([rel] if isinstance(rel, str) else rel), str(d.get("Description") or "")


@needs_tools
def test_one_run_encodes_scans_lossless_and_the_rest_at_its_distance(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    scan = _tiff(d / "neg [1] 100%.tif", "SilverFast 8.8.0 r16", seed=1)
    _tiff(d / "photo (2).tif", "Capture One 23 Windows", seed=2)
    log = _run(tmp_path, "jxl_tiff_encoder.py", d, "--distance", "1", "--effort", "3",
               "--no-preflight")
    flat = " ".join(log.split())
    assert ("Film scans: 1 TIFF(s) to encode at distance 0 (lossless) instead of 1 "
            "(SCAN_DISTANCE) — 1 by scanner software (SCANNER_SOFTWARE)") in flat, flat[-3000:]
    assert "Film scans: 1 TIFF(s) encoded at distance 0 (lossless)" in flat
    assert "only a guess" not in flat
    rel, desc = _meta(d / "neg [1] 100%.jxl")
    assert "jxlphoto-scan:raw" in rel and desc.endswith("cjxl d=0.0 e=3"), (rel, desc)
    rel, desc = _meta(d / "photo (2).jxl")
    assert not any(t.startswith("jxlphoto-scan:") for t in rel)
    assert desc.endswith("cjxl d=1.0 e=3"), desc
    # The scan really is lossless, and the decoded TIFF does not carry the marker.
    _run(tmp_path, "jxl_tiff_decoder.py", d / "neg [1] 100%.jxl", tmp_path / "out")
    out = tmp_path / "out" / "neg [1] 100%.tif"
    with tifffile.TiffFile(out) as t:
        assert np.array_equal(t.pages[0].asarray(), scan)
    rel, _d = _meta(out)
    assert not any(t.startswith("jxlphoto-scan:") for t in rel), rel


@needs_tools
def test_a_tiff_without_software_or_camera_exif_is_a_scan(tmp_path):
    """Lab scans and Epson Scan write no Software tag; a camera always records
    an exposure. Measured on a 15 000-TIFF library."""
    d = tmp_path / HOSTILE
    d.mkdir()
    _tiff(d / "lab scan 001.tif", "", seed=11)
    _tiff(d / "stripped camera.tif", "", seed=12)
    _exif("-overwrite_original", "-ExposureTime=1/125", "-FNumber=8", d / "stripped camera.tif")
    _run(tmp_path, "jxl_tiff_encoder.py", d, "--distance", "1", "--effort", "3",
         "--no-preflight")
    rel, desc = _meta(d / "lab scan 001.jxl")
    assert "jxlphoto-scan:raw" in rel and desc.endswith("cjxl d=0.0 e=3"), (rel, desc)
    rel, desc = _meta(d / "stripped camera.jxl")
    assert not any(t.startswith("jxlphoto-scan:") for t in rel)
    assert desc.endswith("cjxl d=1.0 e=3"), desc


def _camera_tiff_without_tags(path, seed):
    """A camera TIFF rewritten without a single tag (the owner's Fuji S5 Pro
    and Nikon E990 TIFFs): the image plus the 160x120 thumbnail page."""
    a = _photo(seed=seed)
    with tifffile.TiffWriter(path) as w:
        w.write(a, photometric="rgb", software="", metadata=None)
        w.write(np.zeros((120, 160, 3), np.uint8), photometric="rgb", software="",
                metadata=None)
    with tifffile.TiffFile(path) as t:
        assert 305 not in t.pages[0].tags


@needs_tools
def test_a_guessed_scan_is_announced_and_a_camera_thumbnail_is_not_a_scan(tmp_path):
    """No Software tag, no camera EXIF: a lab scan — unless a 160x120
    thumbnail page says a camera wrote the file. The run names the guessed
    scans' folders before the first encode and again in the summary; a sync
    re-run with nothing to write says nothing."""
    d = tmp_path / HOSTILE
    (d / "lab [scans]").mkdir(parents=True)
    (d / "S5Pro [CULL]").mkdir()
    _tiff(d / "lab [scans]" / "0001 50%.tif", "", seed=13)
    _camera_tiff_without_tags(d / "S5Pro [CULL]" / "_DSF0311.tif", seed=14)
    log = _run(tmp_path, "jxl_tiff_encoder.py", d, "--mode", "8", "--distance", "1",
               "--effort", "3", "--no-preflight")
    flat = " ".join(log.split())
    assert "1 GUESSED (no Software tag, no camera EXIF)" in flat, flat[-3000:]
    assert flat.count("of them are only a guess") == 2       # planning + summary
    warned = [ln for ln in log.splitlines() if "WARNING" in ln]
    assert any(str(d / "lab [scans]") in ln for ln in warned), warned
    assert not any("S5Pro [CULL]" in ln for ln in warned), warned
    assert "Stop now (Ctrl+C)" in flat and "--scan-distance same --overwrite" in flat

    rel, desc = _meta(d / "lab [scans]" / "0001 50%.jxl")
    assert "jxlphoto-scan:raw" in rel and desc.endswith("cjxl d=0.0 e=3"), (rel, desc)
    rel, desc = _meta(d / "S5Pro [CULL]" / "_DSF0311.jxl")
    assert "jxlphoto-scan:no" in rel and desc.endswith("cjxl d=1.0 e=3"), (rel, desc)

    log = _run(tmp_path, "jxl_tiff_encoder.py", d, "--mode", "8", "--distance", "1",
               "--effort", "3", "--no-preflight")
    assert "only a guess" not in log and "Film scans: " not in log

    # The recompressor takes the camera master for what the encoder said.
    for t in d.rglob("*.tif"):
        t.unlink()
    before = (d / "lab [scans]" / "0001 50%.jxl").read_bytes()
    # (--no-keep-smaller: the blank 160x120 page would not shrink, and its
    # group is replaced all-or-nothing.)
    _run(tmp_path, "jxl_recompressor.py", d, "--mode", "8", "--distance", "2",
         "--effort", "3", "--no-preflight", "--delete-confirm-off",
         "--on-regeneration", "convert", "--no-keep-smaller")
    assert (d / "lab [scans]" / "0001 50%.jxl").read_bytes() == before
    assert _meta(d / "S5Pro [CULL]" / "_DSF0311.jxl")[1].endswith("cjxl d=2.0 e=3")


@needs_tools
def test_scan_distance_same_and_scope_raw(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    _tiff(d / "raw scan.tif", "VueScan 9 x64", seed=3)                       # 16-bit, no ICC: raw
    _tiff(d / "final scan.tif", "SilverFast 8.8.0", dtype=np.uint8, seed=4)  # 8-bit: final
    _run(tmp_path, "jxl_tiff_encoder.py", d, "--distance", "1", "--effort", "3",
         "--no-preflight", "--scan-scope", "raw")
    rel, desc = _meta(d / "raw scan.jxl")
    assert "jxlphoto-scan:raw" in rel and desc.endswith("cjxl d=0.0 e=3")
    rel, desc = _meta(d / "final scan.jxl")
    assert "jxlphoto-scan:final" in rel and desc.endswith("cjxl d=1.0 e=3"), desc

    _run(tmp_path, "jxl_tiff_encoder.py", d, "--distance", "1", "--effort", "3",
         "--no-preflight", "--overwrite", "--scan-distance", "same")
    _rel, desc = _meta(d / "raw scan.jxl")
    assert desc.endswith("cjxl d=1.0 e=3"), desc


@needs_tools
def test_mode8_deletes_both_sources_each_verified_at_its_own_distance(tmp_path):
    """The delete gate must check each output against the distance it was
    really encoded at: a lossless scan bit-exact, the lossy photo by its
    lossy check — never the run's distance for both."""
    d = tmp_path / HOSTILE / "sub [x]"
    d.mkdir(parents=True)
    _tiff(d / "neg.tif", "SilverFast 8.8.0", seed=5)
    _tiff(d / "photo.tif", "Capture One 23", seed=6)
    _run(tmp_path, "jxl_tiff_encoder.py", tmp_path / HOSTILE, "--mode", "8",
         "--distance", "1", "--effort", "3", "--no-preflight", "--delete-source",
         "--delete-confirm-off", "--verify-roundtrip")
    assert sorted(p.name for p in d.iterdir()) == ["neg.jxl", "photo.jxl"]


@needs_tools
def test_the_recompressor_never_reencodes_a_scan_lossy_unless_asked(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    _tiff(d / "neg.tif", "SilverFast 8.8.0", seed=7)
    _tiff(d / "photo.tif", "Capture One 23", seed=8)
    _run(tmp_path, "jxl_tiff_encoder.py", d, "--distance", "0", "--effort", "3",
         "--no-preflight")
    for t in d.glob("*.tif"):
        t.unlink()
    # An unmarked scan from before the marker: recognised by its Software tag.
    shutil.copy(d / "neg.jxl", d / "old neg.jxl")
    _exif("-overwrite_original", "-XMP-dc:Relation-=jxlphoto-scan:raw", d / "old neg.jxl")
    assert "jxlphoto-scan:raw" not in _meta(d / "old neg.jxl")[0]
    before = {p.name: p.read_bytes() for p in d.glob("*.jxl")}

    log = _run(tmp_path, "jxl_recompressor.py", d, "--mode", "8", "--distance", "1",
               "--effort", "3", "--no-preflight", "--delete-confirm-off")
    assert "scan=copy" in log
    assert (d / "neg.jxl").read_bytes() == before["neg.jxl"]
    assert (d / "old neg.jxl").read_bytes() == before["old neg.jxl"]
    assert (d / "photo.jxl").read_bytes() != before["photo.jxl"]
    assert _meta(d / "photo.jxl")[1].endswith("cjxl d=1.0 e=3")

    _run(tmp_path, "jxl_recompressor.py", d, "--mode", "8", "--distance", "1",
         "--effort", "3", "--no-preflight", "--delete-confirm-off", "--on-scan", "convert")
    assert _meta(d / "neg.jxl")[1].endswith("cjxl d=1.0 e=3")


@needs_tools
def test_a_derivative_of_a_scan_is_not_blocked(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    _tiff(d / "neg.tif", "SilverFast 8.8.0", seed=9)
    _run(tmp_path, "jxl_tiff_encoder.py", d, "--distance", "0", "--effort", "3",
         "--no-preflight")
    (d / "neg.tif").unlink()
    _run(tmp_path, "jxl_recompressor.py", d, tmp_path / "small", "--mode", "2",
         "--distance", "2", "--effort", "3", "--resize-long", "100", "--no-preflight")
    assert (tmp_path / "small" / "neg.jxl").exists()


def _info(software="", icc=None, bits=16, pages=((0, 3, ()),), camera=False, sizes=()):
    info = {}
    for i, (st, spp, es) in enumerate(pages):
        info[i] = {"subfiletype": st, "samples": spp, "extrasamples": es,
                   "size": sizes[i] if i < len(sizes) else (4256, 2848)}
    info[0].update({"software": software, "icc_class": icc, "bits": bits,
                    "camera": camera})
    return info


_TWO = ((0, 3, ()), (0, 3, ()))


@pytest.mark.parametrize("info,kind", [
    (_info("SilverFast 8.8.0", "scnr", 16, ((0, 3, ()), (1, 3, ()), (4, 1, ()))), "raw"),
    (_info("SilverFast 8.8.0", "mntr", 16), "final"),
    (_info("SilverFast 8.8.0", "mntr", 8), "final"),
    (_info("SilverFast 8.8.0", None, 8), "final"),
    (_info("SilverFast 8.8.0", None, 16), "raw"),
    (_info("VueScan 9 x64", None, 16, ((0, 4, ()),)), "raw"),       # RGBI
    (_info("VueScan 9 x64", "mntr", 16, ((0, 4, ()),)), "raw"),     # IR kept
    (_info("Negmaster BR", "mntr", 16, ((0, 4, (0,)),)), "raw"),    # IR, no scanner name
    (_info("Capture One 23", "mntr", 16, ((0, 3, ()), (1, 3, ()))), None),
    (_info("Adobe Photoshop", "mntr", 16, ((0, 4, (2,)),)), None),  # alpha, not IR
    (_info("", None, 16), "raw"),                                   # lab / Epson Scan
    (_info("", None, 8), "final"),
    (_info("", "mntr", 16), "final"),                               # EPSON Standard RGB
    (_info("", None, 16, camera=True), None),                       # stripped camera TIFF
    (_info("Capture One 23", None, 16), None),
    # A camera TIFF rewritten without tags keeps its 160x120 thumbnail page.
    (_info("", "mntr", 16, _TWO, sizes=((4256, 2848), (160, 120))), None),
    (_info("", None, 8, _TWO, sizes=((1536, 2048), (120, 160))), None),
    (_info("", "mntr", 16, _TWO, sizes=((4256, 2848), (320, 240))), "final"),
    (_info("SilverFast 8.8.0", "mntr", 16, _TWO, sizes=((4000, 6000), (160, 120))), "final"),
    (_info("", None, 16, ((0, 4, ()), (0, 3, ())), sizes=((900, 600), (160, 120))), "raw"),
])
def test_scan_kind(info, kind):
    assert enc._scan_kind(info) == kind


@pytest.mark.parametrize("info,reason", [
    (_info("SilverFast 8.8.0", "scnr"), "software"),
    (_info("Negmaster BR", "mntr", 16, ((0, 4, (0,)),)), "ir"),
    (_info("", None, 16), "guessed"),
    (_info("", "mntr", 16, _TWO, sizes=((4256, 2848), (160, 120))), "camera"),
    (_info("", None, 16, camera=True), None),
    (_info("Capture One 23", "mntr"), None),
])
def test_scan_reason(info, reason):
    assert enc._scan_reason(info) == reason


@pytest.mark.parametrize("rel,sw,kind", [
    (["jxlphoto-scan:final"], "", "final"),
    (["jxlphoto-scan:raw"], "SilverFast", "raw"),
    (["jxlphoto-subfiletype:4"], "", "unknown"),
    ([], "SilverFast 8.8.0 r16", "unknown"),
    ([], "Capture One 23 | gen=1 | cjxl d=0.1 e=7", None),
    (["jxlphoto-origin:tiff16"], "", "unknown"),                   # an old lab-scan master
    (["jxlphoto-origin:tiff8"], "gen=1 | cjxl d=0.1 e=7", "unknown"),
    (["jxlphoto-origin:jpeg"], "", None),
    (["jxlphoto-origin:tiff16", "jxlphoto-scan:no"], "", None),     # the encoder's verdict
])
def test_recompressor_scan_kind(rel, sw, kind):
    assert rec._scan_kind_of(rel, sw, camera=False) == kind


def test_recompressor_camera_exif_is_not_a_scan():
    assert rec._scan_kind_of(["jxlphoto-origin:tiff16"], "", camera=True) is None


@pytest.mark.parametrize("kind,new_d,scope,derivative,expected", [
    ("raw", 1.0, "all", False, "copy"),
    ("final", 1.0, "all", False, "copy"),
    ("final", 1.0, "raw", False, None),
    ("unknown", 1.0, "raw", False, "copy"),
    ("raw", 0.0, "all", False, None),
    ("raw", 1.0, "all", True, None),
    (None, 1.0, "all", False, None),
])
def test_recompressor_scan_action(monkeypatch, kind, new_d, scope, derivative, expected):
    monkeypatch.setattr(rec, "ON_SCAN", "copy")
    monkeypatch.setattr(rec, "SCAN_SCOPE", scope)
    monkeypatch.setattr(rec, "DERIVATIVE", derivative)
    assert rec._scan_action(kind, new_d) == expected


def test_both_scripts_ship_the_same_scanner_list():
    assert enc.SCANNER_SOFTWARE == rec.SCANNER_SOFTWARE


@pytest.mark.parametrize("text,value", [("0", "0"), ("0.05", "0.05"), ("same", "same"),
                                        ("SAME", "same"), ("16", None), ("x", None)])
def test_wizard_scan_distance_values(text, value):
    import jxl_photo as wp
    assert wp._valid_scan_distance(text) == value
