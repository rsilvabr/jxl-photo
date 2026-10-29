"""Audit of 2026-10-29 (#551-#555).

#551 A TIFF re-encoded with FEWER pages (a thumbnail page dropped by
     --thumbnail-mode exclude, or by "auto" for a re-export from Capture One)
     left its old page beside the new archive, under the old group id: the
     decoder warned about an incomplete group and wrote an extra TIFF.
#552 A derivative run (--output-icc / --resize-* / --sharpen) made a
     derivative of every thumbnail page too.
#555 The transcoder's resized/sharpened/colour-converted JXL -> JPEG/PNG did
     the same.
#553 With %TEMP% outside the ANSI code page, exiftool could not open its
     argfile — every call of all four backends failed.
#554 The delete-skipped and adopt-scan refusals of a film scan named
     --distance, not the --scan-distance that decides it.

Real codecs and exiftool; every file inside a folder named with Japanese,
brackets, a space and %.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import tifffile

import jxl_jpeg_transcoder as tra
import jxl_recompressor as rec
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "伊豆沼 [tmp] 100%"
_TOOLS = ("cjxl", "djxl", "exiftool")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")
BACKENDS = (enc, dec, tra, rec)


def _run(tmp_path, script, *args):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"), "JXLPHOTO_JXL_THUMB": "0"}
    r = subprocess.run([sys.executable, str(REPO / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, env=env)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return r.stdout + r.stderr


def _tiff(path, software, seed, pages=("main", "thumb")):
    """A 16-bit RGB photo plus, per `pages`, a 160x120 thumbnail page or a
    second full-size page."""
    rng = np.random.default_rng(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tifffile.TiffWriter(str(path)) as w:
        for kind in pages:
            if kind == "thumb":
                w.write((rng.random((120, 160, 3)) * 255).astype(np.uint8),
                        photometric="rgb", subfiletype=1, metadata=None)
            else:
                w.write((rng.random((150, 200, 3)) * 65535).astype(np.uint16),
                        photometric="rgb", software=software, metadata=None)


def _event(tmp_path):
    ev = tmp_path / HOSTILE / "2026" / "260101_伊豆沼 [FINAL] 5%"
    return ev, ev / "_EXPORT" / "TIFF16", ev / "_EXPORT" / "16B_JXL"


def _newer(path):
    t = time.time() + 5
    os.utime(path, (t, t))


# ------------------------------------------------------------------- #551
@needs_tools
def test_551_exclude_overwrite_removes_the_old_thumbnail_page(tmp_path):
    ev, tif_dir, jxl_dir = _event(tmp_path)
    _tiff(tif_dir / "DSC_0001 [a].tif", "NX Studio 1.6", 1)
    root = tmp_path / HOSTILE / "2026"
    _run(tmp_path, "jxl_tiff_encoder.py", root, "--mode", "6", "--sync", "--workers", "1")
    assert sorted(p.name for p in jxl_dir.iterdir()) == [
        "DSC_0001 [a].jxl", "DSC_0001 [a]_page1_thumbnail.jxl"]
    # Another photo whose name says it is a page of DSC_0002, but whose marker
    # names DSC_0001: never touched by DSC_0002's clean-up.
    _tiff(tif_dir / "DSC_0002.tif", "NX Studio 1.6", 2, pages=("main",))
    shutil.copy2(jxl_dir / "DSC_0001 [a]_page1_thumbnail.jxl",
                 jxl_dir / "DSC_0002_page1_thumbnail.jxl")

    log = _run(tmp_path, "jxl_tiff_encoder.py", root, "--mode", "6", "--overwrite",
               "--thumbnail-mode", "exclude", "--workers", "1")
    names = sorted(p.name for p in jxl_dir.iterdir())
    assert "DSC_0001 [a]_page1_thumbnail.jxl" not in names, log[-2000:]
    assert "DSC_0001 [a].jxl" in names and "DSC_0002.jxl" in names
    assert "DSC_0002_page1_thumbnail.jxl" in names        # foreign marker: kept
    assert "Removed a thumbnail page left by an earlier encode" in log


@needs_tools
def test_551_capture_one_reexport_leaves_no_incomplete_group(tmp_path):
    ev, tif_dir, jxl_dir = _event(tmp_path)
    tif = tif_dir / "DSC_0003.tif"
    root = tmp_path / HOSTILE / "2026"
    _tiff(tif, "NX Studio 1.6", 3)
    _run(tmp_path, "jxl_tiff_encoder.py", root, "--mode", "6", "--sync", "--workers", "1")
    assert (jxl_dir / "DSC_0003_page1_thumbnail.jxl").exists()
    _tiff(tif, "Capture One 23 Windows", 4)                 # the re-export
    _newer(tif)
    _run(tmp_path, "jxl_tiff_encoder.py", root, "--mode", "6", "--sync", "--workers", "1")
    assert sorted(p.name for p in jxl_dir.iterdir()) == ["DSC_0003.jxl"]
    out = tmp_path / HOSTILE / "decoded"
    log = _run(tmp_path, "jxl_tiff_decoder.py", jxl_dir, out, "--mode", "0", "--workers", "1")
    assert "INCOMPLETE" not in log
    assert sorted(p.name for p in out.iterdir() if p.suffix == ".tif") == ["DSC_0003.tif"]


@needs_tools
def test_551_an_old_real_page_is_kept_and_reported(tmp_path):
    ev, tif_dir, jxl_dir = _event(tmp_path)
    tif = tif_dir / "scan [IR].tif"
    root = tmp_path / HOSTILE / "2026"
    _tiff(tif, "NX Studio 1.6", 5, pages=("main", "main"))
    _run(tmp_path, "jxl_tiff_encoder.py", root, "--mode", "6", "--sync", "--workers", "1")
    assert (jxl_dir / "scan [IR]_page1.jxl").exists()
    _tiff(tif, "NX Studio 1.6", 6, pages=("main",))         # saved again with one page
    _newer(tif)
    log = _run(tmp_path, "jxl_tiff_encoder.py", root, "--mode", "6", "--sync", "--workers", "1")
    assert (jxl_dir / "scan [IR]_page1.jxl").exists()       # real data: never deleted
    assert "KEPT" in log and "scan [IR]_page1.jxl" in log


# ------------------------------------------------------------------- #552
@needs_tools
def test_552_a_derivative_run_skips_thumbnail_pages(tmp_path):
    ev, tif_dir, jxl_dir = _event(tmp_path)
    _tiff(tif_dir / "DSC_0004 [b].tif", "NX Studio 1.6", 7)
    root = tmp_path / HOSTILE / "2026"
    _run(tmp_path, "jxl_tiff_encoder.py", root, "--mode", "6", "--sync", "--workers", "1")
    assert (jxl_dir / "DSC_0004 [b]_page1_thumbnail.jxl").exists()
    log = _run(tmp_path, "jxl_recompressor.py", root, "--mode", "7", "--output-icc", "sRGB",
               "--export-subfolder", "16B_JXL", "--export-jxl-folder", "sRGB",
               "--distance", "0.5", "--workers", "1")
    names = sorted(p.name for p in (ev / "_EXPORT" / "sRGB").iterdir())
    assert names == ["DSC_0004 [b].jxl"], log[-2000:]
    assert "thumbnail page(s) of split TIFFs skipped" in log
    assert "got NO derivative" not in log           # not a policy skip (#543)


@needs_tools
@pytest.mark.skipif(not shutil.which("magick"), reason="needs ImageMagick")
@pytest.mark.parametrize("route", ["convert", "auto"])
def test_555_transcoder_derivatives_skip_thumbnail_pages(tmp_path, route):
    """The transcoder's side of #552: a resized JPEG is made of the photo,
    not of the thumbnail page its TIFF carried."""
    ev, tif_dir, jxl_dir = _event(tmp_path)
    _tiff(tif_dir / "DSC_0005 [c].tif", "NX Studio 1.6", 8)
    root = tmp_path / HOSTILE / "2026"
    _run(tmp_path, "jxl_tiff_encoder.py", root, "--mode", "6", "--sync", "--workers", "1")
    assert (jxl_dir / "DSC_0005 [c]_page1_thumbnail.jxl").exists()
    out = tmp_path / HOSTILE / f"jpeg {route} 5%"
    args = [jxl_dir, out, "--mode", "2", "--resize-long", "100", "--format", "jpeg",
            "--workers", "1"] + (["--force-convert"] if route == "convert" else [])
    log = _run(tmp_path, "jxl_jpeg_transcoder.py", *args)
    names = sorted(p.name for p in out.rglob("*.jpg"))
    assert names == ["DSC_0005 [c].jpg"], log[-2000:]
    assert "thumbnail page(s) of split TIFFs skipped" in log


# ------------------------------------------------------------------- #553
def _acp():
    try:
        import ctypes
        return ctypes.windll.kernel32.GetACP()
    except Exception:
        return None


# Characters no single ANSI code page holds together (Japanese, Greek, Polish).
_NON_ANSI = "一時 Ωμ ąę [x] 100%"
needs_ansi_cp = pytest.mark.skipif(os.name != "nt" or _acp() in (None, 65001),
                                   reason="needs Windows with a non-UTF-8 ANSI code page")


@pytest.mark.parametrize("mod", BACKENDS, ids=lambda m: m.__name__)
def test_553_argfile_via_stdin_only_when_the_path_cannot_go_on_the_command_line(
        tmp_path, monkeypatch, mod):
    af = tmp_path / "args.txt"
    af.write_bytes(b"-ver\n")
    monkeypatch.setattr(mod, "_ansi_encodable", lambda s: True)
    assert mod._argfile_via_stdin(["exiftool", "-@", str(af)]) == (["exiftool", "-@", str(af)], None)
    monkeypatch.setattr(mod, "_ansi_encodable", lambda s: False)
    assert mod._argfile_via_stdin(["exiftool", "-@", str(af), "-j"]) == (
        ["exiftool", "-@", "-", "-j"], b"-ver\n")
    assert mod._argfile_via_stdin(["exiftool", "-@", "-"]) == (["exiftool", "-@", "-"], None)
    assert mod._argfile_via_stdin(["cjxl", "a.png", "b.jxl"]) == (["cjxl", "a.png", "b.jxl"], None)


@needs_ansi_cp
@pytest.mark.skipif(not shutil.which("exiftool"), reason="needs exiftool")
@pytest.mark.parametrize("mod", BACKENDS, ids=lambda m: m.__name__)
def test_553_exiftool_works_with_a_temp_folder_outside_the_ansi_code_page(
        tmp_path, monkeypatch, mod):
    tmp = tmp_path / _NON_ANSI
    tmp.mkdir()
    monkeypatch.setattr(mod, "TEMP_DIR", str(tmp))
    r = mod._run_exiftool_argfile(["-ver"])
    assert r.returncode == 0 and r.stdout.strip()[:2].isdigit(), r.stderr
    # The planning-time batch path (stall): its argfile goes in on stdin too.
    af = tmp / "batch.txt"
    af.write_text("-ver\n", encoding="utf-8")
    r = mod._run_captured([mod._get_exiftool_cmd(), "-@", str(af)], 60, text=True, stall=30)
    assert r.returncode == 0 and r.stdout.strip()[:2].isdigit(), r.stderr


# ------------------------------------------------------------------- #554
def test_554_a_film_scan_refusal_names_scan_distance(monkeypatch):
    monkeypatch.setattr(enc, "CJXL_DISTANCE", 0.05)
    monkeypatch.setattr(enc, "_distance_for", lambda t: 0.0)
    hint = enc._scan_distance_hint("scan.tif")
    assert "--scan-distance same" in hint and "SCAN_DISTANCE 0" in hint
    monkeypatch.setattr(enc, "_distance_for", lambda t: 0.05)
    assert enc._scan_distance_hint("photo.tif") == ""
