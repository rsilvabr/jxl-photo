"""The bugs Fable's full sweep found (2026-11-22, AI_tools/261122_Fable_review_full-sweep.md),
one test each, made from its reproduction scripts. Real codecs where the bug is in what they
read or write; every folder is hostile (Japanese, [ ], a space).
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_photo_common as common  # noqa: E402
import jxl_recompressor as rec  # noqa: E402
import jxl_tiff_encoder as enc  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "250118_吾妻山公園 [FINAL] x"
needs_codecs = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool")),
    reason="needs cjxl, djxl and exiftool on PATH")


def _script(name, *args, cwd=None):
    """A backend as a subprocess, its log in the test's folder."""
    return subprocess.run([sys.executable, str(REPO / name), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL, timeout=600,
                          cwd=cwd, env=dict(os.environ, JXLPHOTO_SETTINGS=""))


def _scan_master(root: Path) -> Path:
    """A lossless film-scan master (SilverFast's Software tag marks the scan)."""
    tif = root / "TIFF" / "scan (2).tif"
    tif.parent.mkdir(parents=True)
    rng = np.random.default_rng(1)
    tifffile.imwrite(str(tif), rng.integers(0, 65535, (120, 160, 3), dtype=np.uint16),
                     photometric="rgb", software="SilverFast 8.8.0 r24 (Jan  9 2023)")
    r = _script("jxl_tiff_encoder.py", tif.parent, root / "JXL", "--mode", "0",
                "--effort", "3", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    return next((root / "JXL").glob("*.jxl"))


# --- B1 (#690): an encode record that could not be read is not "no record" ------

@needs_codecs
def test_690_an_unread_record_leaves_the_file_as_it_is(tmp_path, monkeypatch):
    """The planning batch that reads the encode records stalls: before #690 the
    file read as one with no record ('unknown', converted by default) and a
    lossless scan was re-encoded lossy IN PLACE with exit 0, past the film-scan
    and regeneration guards."""
    master = _scan_master(tmp_path / HOSTILE)
    before = master.read_bytes()
    real = rec._run_captured

    def stalled_batch(cmd, timeout, *a, **k):
        if k.get("stall") is not None and "-@" in cmd:
            argfile = Path(cmd[cmd.index("-@") + 1])
            if argfile.is_file() and "HistoryParameters" in argfile.read_text(
                    encoding="utf-8", errors="replace"):
                raise subprocess.TimeoutExpired(cmd, timeout)
        return real(cmd, timeout, *a, **k)

    monkeypatch.setattr(rec, "_run_captured", stalled_batch)
    monkeypatch.setattr(sys, "argv", ["jxl_recompressor.py", str(master.parent),
                                      "--mode", "0", "--effort", "3", "--workers", "1",
                                      "--delete-confirm-off"])
    with pytest.raises(SystemExit) as e:
        rec.main()
    assert e.value.code not in (0, None)
    assert master.read_bytes() == before


# --- B2 (#691) and B3 (#692): decodes that must not delete their JXL --------------

def _relation(path: Path) -> str:
    # Through an argfile (the scripts' own helper): on a command line exiftool
    # reads the '[ ]' of a hostile folder as a wildcard and answers nothing.
    r = rec._run_exiftool_argfile(["-s3", "-XMP-dc:Relation", str(path)], timeout=60)
    return r.stdout or ""


def _set_relation(path: Path, tokens) -> None:
    """dc:Relation = exactly `tokens`: the clear and the adds in two commands
    (in one, exiftool ignores the clear and only adds)."""
    for args in (["-overwrite_original", "-XMP-dc:Relation=", str(path)],
                 ["-overwrite_original"] + [f"-XMP-dc:Relation+={t}" for t in tokens]
                 + [str(path)]):
        r = rec._run_exiftool_argfile(args, timeout=60)
        assert r.returncode == 0, r.stderr


def _prophoto_jpeg(path: Path):
    from PIL import Image
    icc = rec._build_matrix_trc_icc(
        "ProPhoto-like g2.2 (test)",
        [(0.7347, 0.2653), (0.1596, 0.8404), (0.0366, 0.0001)], (0.3457, 0.3585), 2.2)
    rng = np.random.default_rng(3)
    img = Image.fromarray(rng.integers(0, 255, (96, 128, 3), dtype=np.uint8))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, quality=92, icc_profile=icc)


@needs_codecs
def test_691_a_colour_converted_png_is_a_derivative_and_never_proves_the_master(tmp_path):
    """An sRGB 16-bit PNG of a ProPhoto master used to carry the master's
    jxlphoto-src/srcsum: a later faithful decode with --delete-skipped took it
    for the master's copy and DELETED the master (Fable's p1)."""
    d = tmp_path / HOSTILE
    jpg = d / "photo (2).jpg"
    _prophoto_jpeg(jpg)
    r = _script("jxl_jpeg_transcoder.py", jpg, "--force-convert", "--distance", "0.5",
                "--mode", "0")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    jpg.unlink()
    master = d / "photo (2).jxl"
    r = _script("jxl_jpeg_transcoder.py", master, "--force-convert", "--decode",
                "--format", "png", "--to-srgb", "--mode", "0")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    rel = _relation(d / "photo (2).png")
    assert "jxlphoto-derived:" in rel and "jxlphoto-src:" not in rel, rel
    r = _script("jxl_jpeg_transcoder.py", master, "--force-convert", "--decode",
                "--format", "png", "--mode", "0", "--delete-source", "--delete-skipped",
                "--delete-confirm-off")
    assert master.exists(), (r.stdout + r.stderr)[-3000:]


@needs_codecs
def test_691_a_colour_converted_png_an_older_version_made_never_proves_the_master(tmp_path):
    """The PNGs written before #691: an sRGB PNG carrying the master's
    jxlphoto-src/srcsum and no derived mark, as rc4 wrote it. A faithful
    decode with --delete-skipped finds it (SKIP) and must keep the master:
    the PNG's profile is not the one the master carries (an encoder master:
    its jxlphoto-iccprofile token; a JXL without one cannot be judged so)."""
    d = tmp_path / HOSTILE
    icc = _icc()
    tif = tmp_path / "tif [src]" / "photo (2).tif"
    tif.parent.mkdir(parents=True)
    rng = np.random.default_rng(8)
    tifffile.imwrite(str(tif), rng.integers(0, 65535, (96, 128, 3), dtype=np.uint16),
                     photometric="rgb", extratags=[(34675, "B", len(icc), icc, True)])
    r = _script("jxl_tiff_encoder.py", tif.parent, d, "--mode", "0", "--distance", "0.5",
                "--effort", "3", "--no-preflight")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    master = d / "photo (2).jxl"
    faithful = tmp_path / HOSTILE / "faithful"
    r = _script("jxl_jpeg_transcoder.py", master, faithful, "--force-convert", "--decode",
                "--format", "png", "--mode", "0")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    marks = [t.strip() for t in _relation(faithful / "photo (2).png").split(",")
             if t.strip().startswith(("jxlphoto-src:", "jxlphoto-srcsum:"))]
    assert len(marks) == 2, marks
    r = _script("jxl_jpeg_transcoder.py", master, "--force-convert", "--decode",
                "--format", "png", "--to-srgb", "--mode", "0")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    png = d / "photo (2).png"
    _set_relation(png, marks)
    assert "jxlphoto-derived:" not in _relation(png)
    r = _script("jxl_jpeg_transcoder.py", master, "--force-convert", "--decode",
                "--format", "png", "--mode", "0", "--delete-source", "--delete-skipped",
                "--delete-confirm-off")
    assert master.exists(), (r.stdout + r.stderr)[-3000:]
    assert "colour profile is not the JXL's own" in r.stdout, r.stdout[-3000:]


@needs_codecs
def test_691_to_srgb_alone_is_a_derivative_when_the_run_is_planned(tmp_path):
    """Review S1: --to-srgb becomes icc_profile only later in main(), so the
    planning took its copy for a plain conversion: a --sync re-run (the master
    newer) refused its own derivative, exit 1."""
    d = tmp_path / HOSTILE
    jpg = d / "photo (2).jpg"
    _prophoto_jpeg(jpg)
    r = _script("jxl_jpeg_transcoder.py", jpg, "--force-convert", "--distance", "0.5",
                "--mode", "0")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    jpg.unlink()
    master = d / "photo (2).jxl"
    out = tmp_path / HOSTILE / "sRGB out"
    r = _script("jxl_jpeg_transcoder.py", master, out, "--force-convert", "--decode",
                "--format", "jpeg", "--to-srgb", "--mode", "0")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    rel = _relation(out / "photo (2).jpg")
    assert "jxlphoto-derived:" in rel and "jxlphoto-src:" not in rel, rel
    later = (out / "photo (2).jpg").stat().st_mtime + 30
    os.utime(master, (later, later))
    r = _script("jxl_jpeg_transcoder.py", master, out, "--force-convert", "--decode",
                "--format", "jpeg", "--to-srgb", "--mode", "0", "--sync")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    assert "REFUS" not in r.stdout, r.stdout[-3000:]


@needs_codecs
def test_691_an_earlier_unlabelled_export_is_skipped_not_refused(tmp_path):
    """Review S7: what an older version wrote with --icc-profile sRGB (or a
    faithful export of the same master, or the user's retouch of it: nothing
    tells them apart) carries the master's own marks and no derived label.
    Since #691 every later run of the same command refused it, exit 1. It is
    left to the skip logic, as before #691: SKIP, the file byte for byte (the
    second review's M1: forced, it was overwritten); --overwrite remakes it as
    a derivative. A file whose marks name another source stays refused."""
    d = tmp_path / HOSTILE
    jpg = d / "photo (2).jpg"
    _prophoto_jpeg(jpg)
    r = _script("jxl_jpeg_transcoder.py", jpg, "--force-convert", "--distance", "0.5",
                "--mode", "0")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    jpg.unlink()
    master = d / "photo (2).jxl"
    out = tmp_path / HOSTILE / "sRGB"
    # A faithful export in the folder, retouched by the user (XMP kept).
    r = _script("jxl_jpeg_transcoder.py", master, out, "--force-convert", "--decode",
                "--format", "jpeg", "--mode", "0")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    export = out / "photo (2).jpg"
    assert "jxlphoto-src:" in _relation(export)
    edit = rec._run_exiftool_argfile(["-overwrite_original", "-XMP-dc:Title=EDITED BY USER",
                                      str(export)], timeout=60)
    assert edit.returncode == 0, edit.stderr
    before = export.read_bytes()
    cmd = [master, out, "--force-convert", "--decode", "--format", "jpeg",
           "--icc-profile", "sRGB", "--mode", "0"]
    r = _script("jxl_jpeg_transcoder.py", *cmd)
    assert r.returncode == 0 and "REFUSED" not in r.stdout, (r.stdout + r.stderr)[-3000:]
    assert export.read_bytes() == before, "an export of this master was overwritten"
    r = _script("jxl_jpeg_transcoder.py", *cmd, "--overwrite")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    assert "jxlphoto-derived:" in _relation(export)
    # Another source's marks, no label: still refused.
    marks = [m for m in ("jxlphoto-src:0000000000000000", "jxlphoto-srcsum:0000000000000000")]
    _set_relation(export, marks)
    r = _script("jxl_jpeg_transcoder.py", *cmd)
    assert r.returncode != 0 and "REFUSED" in r.stdout, (r.stdout + r.stderr)[-3000:]


@needs_codecs
def test_692_a_jpeg_archive_decoded_to_pixels_is_never_deleted(tmp_path):
    """A lossless JPEG archive (jbrd) holds the JPEG's own bytes (and an HDR
    gain map after them): a 16-bit PNG of its pixels deleted it (Fable's p5)."""
    d = tmp_path / HOSTILE
    jpg = d / "hdr (2).jpg"
    _prophoto_jpeg(jpg)
    r = _script("jxl_jpeg_transcoder.py", jpg, "--force-transcode", "--mode", "0",
                "--delete-source", "--delete-confirm-off")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    archive = d / "hdr (2).jxl"
    assert archive.exists() and not jpg.exists()
    r = _script("jxl_jpeg_transcoder.py", archive, "--force-convert", "--decode",
                "--format", "png", "--mode", "0", "--delete-source", "--delete-confirm-off")
    assert archive.exists(), (r.stdout + r.stderr)[-3000:]
    assert "KEEP (a JPEG archive" in r.stdout


# --- B4 (#693): an ICC probe that could not run is not a verdict -----------------

def _icc():
    return rec._build_matrix_trc_icc(
        "ProPhoto-like g2.2 (test)",
        [(0.7347, 0.2653), (0.1596, 0.8404), (0.0366, 0.0001)], (0.3457, 0.3585), 2.2)


@needs_codecs
@pytest.mark.parametrize("failure", ["timeout", "killed"])
def test_693_an_icc_probe_that_could_not_run_is_not_cached(tmp_path, monkeypatch, failure):
    """cjxl failing for a passing reason during the cautious probe used to be
    cached as "skip" for good: every lossy master of that profile came out
    without its ICC in the codestream (Fable's t05). A probe that could not
    run decides nothing: no cache, and this file keeps its profile in XMP
    (the way that always encodes, review M2)."""
    store = {}
    monkeypatch.setattr(enc, "_load_icc_cache", lambda: dict(store))
    monkeypatch.setattr(enc, "_save_icc_cache", lambda cache: store.update(cache))

    class Killed:
        returncode, stdout, stderr = 3221225786, b"", b""      # 0xC000013A

    def run(cmd, timeout, *a, **k):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, timeout)
        return Killed()

    monkeypatch.setattr(enc, "_run_captured", run)
    verdict = enc._cautious_should_embed_icc(_icc(), tmp_path / "x.tif", distance=0.5)
    assert store == {}, store
    assert verdict is False


@needs_codecs
def test_693_a_profile_cjxl_refuses_is_still_archived(tmp_path, monkeypatch):
    """Review M2: a profile cjxl itself refuses (an sRGB profile whose colour
    space field says CMYK) — "cannot tell" would let the heuristic embed it
    and the real encode failed on every run. It is the verdict "skip" (cached,
    as in rc4) and the master is written, its profile in XMP."""
    from PIL import ImageCms
    store = {}
    monkeypatch.setattr(enc, "_load_icc_cache", lambda: dict(store))
    monkeypatch.setattr(enc, "_save_icc_cache", lambda cache: store.update(cache))
    bad = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    bad[16:20] = b"CMYK"
    tif = tmp_path / HOSTILE / "TIFF16" / "DSC_0001 (2).tif"
    tif.parent.mkdir(parents=True)
    rng = np.random.default_rng(2)
    tifffile.imwrite(str(tif), rng.integers(0, 65535, (64, 96, 3), dtype=np.uint16),
                     photometric="rgb", extratags=[(34675, "B", len(bad), bytes(bad), True)])
    out = tmp_path / HOSTILE / "JXL"
    _encoder_main(monkeypatch, tif.parent, out, "--mode", "0", "--distance", "1",
                  "--effort", "1", "--workers", "1", "--no-preflight")
    assert (out / "DSC_0001 (2).jxl").exists()
    assert [v.get("embed") for v in store.values()] == [False], store


# --- B5 (#694): an unknown cjxl version is tried again, then said ----------------

def test_694_a_version_probe_that_times_out_once_is_tried_again(monkeypatch):
    calls = []

    def flaky(cmd, **kw):
        calls.append(kw.get("timeout"))
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        return subprocess.CompletedProcess(cmd, 0, "JPEG XL encoder v0.13.0 5a7da83", "")

    common._tool_version.cache_clear()
    try:
        monkeypatch.setattr(common.subprocess, "run", flaky)
        assert common._tool_version("cjxl-test-probe") == (0, 13, 0)
        assert len(calls) == 2 and calls[1] > calls[0]
    finally:
        common._tool_version.cache_clear()


def test_694_an_unknown_version_is_said_at_the_start_of_a_run(monkeypatch):
    said = []

    class Log:
        def warning(self, msg):
            said.append(msg)

    monkeypatch.setattr(enc, "logger", Log())
    monkeypatch.setattr(enc, "_tool_version", lambda exe: None)
    enc._warn_if_version_unknown("cjxl")
    assert len(said) == 1 and "#669" in said[0] and "#670" in said[0]
    monkeypatch.setattr(enc, "_tool_version", lambda exe: (0, 13, 0))
    enc._warn_if_version_unknown("cjxl")
    assert len(said) == 1


# --- B12 (#701): a JXL that fails its pixel check in a deleting run --------------

def _rgb16_tiff(path: Path, seed=4):
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    tifffile.imwrite(str(path), rng.integers(0, 65535, (64, 96, 3), dtype=np.uint16),
                     photometric="rgb")


def _encoder_main(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["jxl_tiff_encoder.py", *map(str, args)])
    try:
        enc.main()
    except SystemExit as e:
        return e.code or 0
    return 0


@needs_codecs
def test_701_a_failed_check_in_a_deleting_run_is_an_error_and_renames_the_jxl(
        tmp_path, monkeypatch):
    """The gate's round-trip check failed (forced: an impossible PSNR floor):
    before #701 the TIFF was kept but the refused JXL kept its final name, the
    run said OK and exited 0, and the next --sync called it up to date."""
    import jxl_tiff_decoder as dec
    src = tmp_path / HOSTILE / "TIFF"
    out = tmp_path / HOSTILE / "JXL"
    tif = src / "DSC_0001 (2).tif"
    _rgb16_tiff(tif)
    monkeypatch.setattr(enc, "VERIFY_LOSSY_MIN_PSNR", 500.0)
    code = _encoder_main(monkeypatch, src, out, "--mode", "0", "--distance", "1",
                         "--workers", "1", "--no-preflight", "--delete-source",
                         "--delete-confirm-off")
    assert code == 1
    assert tif.exists()
    assert not (out / "DSC_0001 (2).jxl").exists()
    assert (out / "DSC_0001 (2)_jxlphoto-error.jxl").exists()
    assert dec.find_jxls_recursive(out) == []       # never a source
    monkeypatch.setattr(enc, "VERIFY_LOSSY_MIN_PSNR", 40.0)
    code = _encoder_main(monkeypatch, src, out, "--mode", "0", "--distance", "1",
                         "--workers", "1", "--no-preflight")
    assert code == 0 and (out / "DSC_0001 (2).jxl").exists()    # encoded again


# --- B10 (#699): a TIFF rewritten while it is encoded -----------------------------

@needs_codecs
def test_699_a_tiff_re_exported_before_its_encode_starts_is_archived(tmp_path, monkeypatch):
    """Review S8: the comparison used the TIFF as the PLANNING saw it, so a TIFF
    re-exported while it waited in the queue (read whole, in its new version)
    was thrown away as "the old version". Compared with the TIFF as its page
    read it, it is archived."""
    src = tmp_path / HOSTILE / "TIFF"
    tifs = [src / f"DSC_000{i} (2).tif" for i in range(2)]
    for i, t in enumerate(tifs):
        _rgb16_tiff(t, seed=i + 1)
    real = enc.convert_one

    def hooked(tiff_path, *a, **k):
        if Path(tiff_path).name == tifs[1].name:
            _rgb16_tiff(Path(tiff_path), seed=9)            # re-exported, not yet read
            later = Path(tiff_path).stat().st_mtime + 5
            os.utime(tiff_path, (later, later))
        return real(tiff_path, *a, **k)

    monkeypatch.setattr(enc, "convert_one", hooked)
    out = tmp_path / HOSTILE / "JXL"
    code = _encoder_main(monkeypatch, src, out, "--mode", "0", "--distance", "0",
                         "--effort", "1", "--workers", "1", "--no-preflight")
    assert code == 0
    assert sorted(p.name for p in out.glob("*.jxl")) == ["DSC_0000 (2).jxl", "DSC_0001 (2).jxl"]


@needs_codecs
def test_699_a_tiff_rewritten_during_its_encode_gets_no_jxl(tmp_path, monkeypatch):
    """The re-export lands while cjxl works on the OLD pixels: the JXL took its
    final name with the time the encode ENDED, newer than the new TIFF, and
    every --sync after it said "up to date" (Fable's t02)."""
    src = tmp_path / HOSTILE / "TIFF"
    out = tmp_path / HOSTILE / "JXL"
    tif = src / "DSC_0001 (2).tif"
    _rgb16_tiff(tif, seed=1)
    real = enc._run_captured
    done = {}

    def reexport_during_cjxl(cmd, *a, **k):
        if "cjxl" in os.path.basename(str(cmd[0])).lower() and not done:
            done["x"] = True
            _rgb16_tiff(tif, seed=9)
        return real(cmd, *a, **k)

    monkeypatch.setattr(enc, "_run_captured", reexport_during_cjxl)
    code = _encoder_main(monkeypatch, src, out, "--mode", "0", "--distance", "0",
                         "--workers", "1", "--no-preflight")
    assert done and code == 1
    assert not (out / "DSC_0001 (2).jxl").exists()
    monkeypatch.setattr(enc, "_run_captured", real)
    code = _encoder_main(monkeypatch, src, out, "--mode", "0", "--distance", "0",
                         "--workers", "1", "--no-preflight")
    assert code == 0 and (out / "DSC_0001 (2).jxl").exists()


# --- B14 (#703): a TIFF decoded with a page of its split missing ------------------

def _full_pages(tif: Path) -> int:
    with tifffile.TiffFile(str(tif)) as t:
        return sum(1 for p in t.pages if not (int(p.subfiletype or 0) & 1))


@needs_codecs
def test_703_a_tiff_decoded_with_a_page_missing_is_decoded_again(tmp_path):
    """A split decoded while page 1 was missing gives a valid one-page TIFF;
    when the page came back with its old date, the TIFF was still the newest
    file of the group and the sync called it up to date for good (Fable's exp6)."""
    root = tmp_path / HOSTILE
    tif = root / "TIFF" / "two page (2).tif"
    tif.parent.mkdir(parents=True)
    rng = np.random.default_rng(5)
    with tifffile.TiffWriter(str(tif)) as w:
        for _ in range(2):
            w.write(rng.integers(0, 65535, (48, 64, 3), dtype=np.uint16), photometric="rgb")
    r = _script("jxl_tiff_encoder.py", tif.parent, root / "JXL", "--mode", "0",
                "--distance", "0", "--effort", "3", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    page1 = [p for p in (root / "JXL").glob("*.jxl") if p.stem.endswith("_page1")][0]
    away = root / "away"
    away.mkdir()
    os.replace(page1, away / page1.name)
    out = root / "OUT"
    _script("jxl_tiff_decoder.py", root / "JXL", out, "--mode", "0", "--workers", "1",
            "--no-preview")
    decoded = out / "two page (2).tif"
    assert _full_pages(decoded) == 1
    os.replace(away / page1.name, page1)           # back, with its old date
    r = _script("jxl_tiff_decoder.py", root / "JXL", out, "--mode", "0", "--workers", "1",
                "--no-preview")
    assert _full_pages(decoded) == 2, (r.stdout + r.stderr)[-3000:]


# --- B21 (#710): the recompressor's own output folders follow its settings -------

def test_710_own_output_folders_follow_the_settings(tmp_path, monkeypatch):
    """With JXL_FOLDER_NAME = "JXL_web" (the overlay takes it) a second run
    took the first run's outputs as new sources: only the shipped names were
    known (Fable's t1)."""
    monkeypatch.setattr(rec, "JXL_FOLDER_NAME", "JXL_web")
    monkeypatch.setattr(rec, "CONVERTED_JXL_FOLDER", "converted_web")
    assert rec._is_own_output_path(["jxl_web"])
    assert rec._is_own_output_path(["converted_web"])
    assert rec._is_own_output_path(["jxl_recompressed"])      # the shipped one too
    lib = tmp_path / HOSTILE
    (lib / "JXL_web").mkdir(parents=True)
    (lib / "JXL_web" / "a (2).jxl").write_bytes(b"\xff\x0a")
    (lib / "b (2).jxl").write_bytes(b"\xff\x0a")
    assert [p.name for p in rec.find_jxls_recursive(lib)] == ["b (2).jxl"]


# --- B8 (#697): a caption with a '|' is the user's text, byte for byte -----------

@pytest.mark.parametrize("field,user", [
    ("Tokyo|Kyoto", "Tokyo|Kyoto"),
    ("A || B", "A || B"),
    ("Tokyo|Kyoto | gen=1 | cjxl d=0.1 e=7", "Tokyo|Kyoto"),
    ("gen=1 | cjxl d=0.1 e=7 | A || B", "A || B"),
])
def test_697_a_caption_with_pipes_comes_back_as_it_was(field, user):
    """The field was split on every '|' and joined with ' | ': "Tokyo|Kyoto"
    became "Tokyo | Kyoto" in the master (Fable's t04)."""
    assert enc._strip_encode_params(field) == (user, 0)


# --- B7 (#696): the first page's tags win in the master of a multi-page TIFF -----

@needs_codecs
@pytest.mark.parametrize("folder", [HOSTILE, "100%off %d [x] y"])
def test_696_the_first_page_s_tags_win(tmp_path, folder):
    """`-exif:all` reads every IFD: the second page's Artist and PageNumber
    overwrote the first page's in the master (Fable's exp4). Both routes: a
    '%' path copies through _exiftool_source's container."""
    src = tmp_path / folder / "TIFF"
    src.mkdir(parents=True)
    tif = src / "two (2).tif"
    img = np.full((24, 32, 3), 1000, np.uint16)
    with tifffile.TiffWriter(str(tif)) as w:
        w.write(img, photometric="rgb", extratags=[(315, "s", 0, "Artist Zero", True),
                                                   (297, "H", 2, (0, 2), True)])
        w.write(img, photometric="rgb", extratags=[(315, "s", 0, "Artist One", True),
                                                   (297, "H", 2, (1, 2), True)])
    out = tmp_path / folder / "JXL"
    r = _script("jxl_tiff_encoder.py", src, out, "--mode", "0", "--distance", "0",
                "--effort", "3", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    tags = rec._run_exiftool_argfile(["-s3", "-IFD0:Artist", "-IFD0:PageNumber",
                                      str(out / "two (2).jxl")], timeout=60).stdout
    assert tags.split() == ["Artist", "Zero", "0", "2"], tags
    # Review N1: and the second page's master its own (it took page 0's).
    tags1 = rec._run_exiftool_argfile(["-s3", "-IFD0:Artist", "-IFD0:PageNumber",
                                       str(out / "two (2)_page1.jxl")], timeout=60).stdout
    assert tags1.split() == ["Artist", "One", "1", "2"], tags1


# --- B11 (#700): the Explorer thumbnail of an RGB+IR scan -------------------------

def _thumbnail_of(jxl: Path):
    import io
    from PIL import Image
    data = jxl.read_bytes()
    i = data.find(b"jxlphoto-thumbnail:v1:")
    assert i >= 0, "no thumbnail box"
    return Image.open(io.BytesIO(data[data.index(b"\xff\xd8", i):])).convert("RGB")


@needs_codecs
@pytest.mark.skipif(not shutil.which("magick"), reason="needs ImageMagick")
def test_700_an_ir_channel_is_not_composited_into_the_thumbnail(tmp_path):
    """The IR channel of a 4-sample scan is the JXL's alpha: the thumbnail
    (`-background white -flatten`) came out halfway to white for an IR of 50 %
    (Fable's t4)."""
    src = tmp_path / HOSTILE / "TIFF"
    src.mkdir(parents=True)
    # Larger than the thumbnail (else no box: "small"), with a little noise
    # so the lossless file is big enough for one.
    rng = np.random.default_rng(7)
    noise = rng.integers(-300, 300, (800, 1000, 4))
    base = np.array([48 * 257, 118 * 257, 32 * 257, 32768])      # IR at 50 %
    img = np.clip(base + noise, 0, 65535).astype(np.uint16)
    tifffile.imwrite(str(src / "scan ir (2).tif"), img, photometric="rgb",
                     extrasamples=[0])
    out = tmp_path / HOSTILE / "JXL"
    r = _script("jxl_tiff_encoder.py", src, out, "--mode", "0", "--distance", "0",
                "--effort", "3", "--workers", "1", "--explorer-thumbnail", "on")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    thumb = np.asarray(_thumbnail_of(out / "scan ir (2).jxl"), dtype=np.float64)
    h, w = thumb.shape[:2]
    mean = thumb[h // 3: 2 * h // 3, w // 3: 2 * w // 3].reshape(-1, 3).mean(axis=0)
    assert all(abs(a - b) <= 6 for a, b in zip(mean, (48, 118, 32))), mean


# --- B6 (#695): a resized derivative of an RGB+IR scan ----------------------------

@needs_codecs
@pytest.mark.skipif(not shutil.which("magick"), reason="needs ImageMagick")
def test_695_an_ir_channel_takes_no_part_in_a_derivative(tmp_path):
    """The IR channel reached ImageMagick as alpha: Lanczos weighted the colour
    by it (red columns with full IR, blue ones with none, halved: red, not
    purple) and the derivative came out RGBA (Fable's t3)."""
    src = tmp_path / HOSTILE / "TIFF"
    src.mkdir(parents=True)
    img = np.zeros((64, 64, 4), np.uint16)
    img[:, 0::2, 0] = 65535
    img[:, 0::2, 3] = 65535                     # red columns, IR full
    img[:, 1::2, 2] = 65535                     # blue columns, IR zero
    tifffile.imwrite(str(src / "scan ir (2).tif"), img, photometric="rgb",
                     extrasamples=[0])
    masters = tmp_path / HOSTILE / "JXL"
    r = _script("jxl_tiff_encoder.py", src, masters, "--mode", "0", "--distance", "0",
                "--effort", "3", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    out = tmp_path / HOSTILE / "SMALL"
    r = _script("jxl_recompressor.py", masters / "scan ir (2).jxl", out, "--mode", "0",
                "--resize-percent", "50", "--distance", "1", "--effort", "3",
                "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    deriv = next(out.rglob("*.jxl"))
    pam = subprocess.run(["djxl", str(deriv), "-", "--output_format=pam"],
                         capture_output=True).stdout
    arr = common._pam_array(pam).astype(np.float64)
    assert arr.ndim == 3 and arr.shape[2] == 3, arr.shape        # no IR as alpha
    red, blue = arr[..., 0].mean(), arr[..., 2].mean()
    assert abs(red - blue) < 0.15 * max(red, blue), (red, blue)   # purple, not red
    assert "jxlphoto-extrasamples" not in _relation(deriv)


# --- B9 (#698): a private tag exiftool knows by a name comes back raw ------------

@needs_codecs
def test_698_private_tags_come_back_byte_for_byte(tmp_path):
    """Tag 65000 is Photoshop Camera Raw's OwnerName to exiftool: its EXIF copy
    into the decoded TIFF moved it to the EXIF IFD and deleted the bytes the
    decoder had put back in IFD0, while 65003 (unknown to exiftool) came back
    (Fable's exp10)."""
    src = tmp_path / HOSTILE / "TIFF"
    src.mkdir(parents=True)
    codes = (35243, 50000, 65000, 65003)
    tifffile.imwrite(str(src / "tags (2).tif"), np.full((16, 16, 3), 1000, np.uint16),
                     photometric="rgb",
                     extratags=[(c, 7, 3, b"abc", True) for c in codes])
    masters = tmp_path / HOSTILE / "JXL"
    r = _script("jxl_tiff_encoder.py", src, masters, "--mode", "0", "--distance", "0",
                "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    out = tmp_path / HOSTILE / "OUT"
    r = _script("jxl_tiff_decoder.py", masters, out, "--mode", "0", "--workers", "1",
                "--no-preview")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    with tifffile.TiffFile(str(out / "tags (2).tif")) as t:
        tags = t.pages[0].tags
        got = {c: (int(tags[c].dtype), bytes(tags[c].value)) for c in codes if c in tags}
    assert got == {c: (7, b"abc") for c in codes}, got


@needs_codecs
@pytest.mark.parametrize("mode", ["roundtrip", "none"])
def test_698_the_cameras_tags_of_the_same_name_stay(tmp_path, mode):
    """Review S4: Camera Raw's 65102/65108 are WhiteBalance/Contrast to
    exiftool, the names of the camera's own ExifIFD tags too. The decoded TIFF
    keeps both: the raw IFD0 tags and the camera's ExifIFD ones (excluded by
    name, the copy lost the camera's; by group, it deleted the raw ones)."""
    src = tmp_path / HOSTILE / "TIFF"
    src.mkdir(parents=True)
    tifffile.imwrite(str(src / "acr (2).tif"), np.full((16, 16, 3), 1000, np.uint16),
                     photometric="rgb",
                     extratags=[(65102, 2, 0, "As Shot", True), (65108, 2, 0, "+25", True)])
    masters = tmp_path / HOSTILE / "JXL"
    r = _script("jxl_tiff_encoder.py", src, masters, "--mode", "0", "--distance", "0",
                "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    master = masters / "acr (2).jxl"
    w = rec._run_exiftool_argfile(["-overwrite_original", "-ExifIFD:WhiteBalance#=0",
                                   "-ExifIFD:Contrast#=0", str(master)], timeout=60)
    assert w.returncode == 0, w.stderr
    out = tmp_path / HOSTILE / "OUT"
    args = ["--mode", "0", "--workers", "1", "--no-preview"] + (
        ["--none"] if mode == "none" else [])
    r = _script("jxl_tiff_decoder.py", masters, out, *args)
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    tiff = out / "acr (2).tif"
    with tifffile.TiffFile(str(tiff)) as t:
        tags = t.pages[0].tags
        raw = {c: tags[c].value for c in (65102, 65108) if c in tags}
    assert raw == {65102: "As Shot", 65108: "+25"}, raw
    e = rec._run_exiftool_argfile(["-s", "-s", "-s", "-ExifIFD:WhiteBalance",
                                   "-ExifIFD:Contrast", str(tiff)], timeout=60)
    assert e.stdout.split() == ["Auto", "Normal"], e.stdout


# --- G4: a decode carries ONE provenance pair (Fable's mutation M17) -------------

@needs_codecs
def test_a_decode_carries_the_jxl_s_provenance_pair_only(tmp_path):
    """_inherited_marker_removal_lines drops the src/srcsum pair the JXL
    inherited from its JPEG in the same call that writes the JXL's own: no
    test failed when it was removed (Fable's mutation M17, 2026-11-22)."""
    d = tmp_path / HOSTILE
    jpg = d / "photo (2).jpg"
    _prophoto_jpeg(jpg)
    r = _script("jxl_jpeg_transcoder.py", jpg, "--force-convert", "--distance", "0.5",
                "--mode", "0")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    jxl = d / "photo (2).jxl"
    inherited = [t for t in _relation(jxl).strip().split(", ") if t.startswith("jxlphoto-src:")]
    out = tmp_path / HOSTILE / "PNG"
    r = _script("jxl_jpeg_transcoder.py", jxl, out, "--force-convert", "--decode",
                "--format", "png", "--mode", "0")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    srcs = [t for t in _relation(next(out.rglob("*.png"))).strip().split(", ")
            if t.startswith("jxlphoto-src:")]
    assert len(srcs) == 1 and srcs != inherited, (srcs, inherited)


# --- B13 (#702): Ctrl+C stops the queue, finishes the files in progress ----------

@needs_codecs
def test_702_ctrl_c_stops_the_queue_and_ends_with_the_summary(tmp_path, monkeypatch):
    """Ctrl+C after the 2nd file (simulated: _thread.interrupt_main, what SIGINT
    does): the pool's `with` waited for EVERY queued file, all 12 were
    converted and the run ended without a summary (Fable's t03)."""
    import _thread
    src = tmp_path / HOSTILE / "TIFF"
    for i in range(12):
        _rgb16_tiff(src / f"DSC_{i:04d} (2).tif", seed=i + 1)
    out = tmp_path / HOSTILE / "JXL"
    real = enc.convert_one
    done = []

    def counting(*a, **k):
        r = real(*a, **k)
        done.append(r[1])
        if len(done) == 2:
            _thread.interrupt_main()            # the user's Ctrl+C
        return r

    monkeypatch.setattr(enc, "convert_one", counting)
    monkeypatch.setattr(sys, "argv", ["jxl_tiff_encoder.py", str(src), str(out), "--mode", "0",
                                      "--distance", "1", "--effort", "1", "--workers", "1",
                                      "--no-preflight"])
    with pytest.raises(KeyboardInterrupt):      # __main__ turns it into exit 130
        enc.main()
    written = list(out.glob("*.jxl"))
    assert 2 <= len(written) <= 3, [p.name for p in written]
    assert done.count("aborted") >= 8            # the queued files: not attempted


# --- B19 (#708): the manifest's scan decision and in-place test ask the run -----

def _wizard(tmp_path, monkeypatch):
    import jxl_photo as wp
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    launched = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600: (launched.append(cmd), 0)[1])
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    menu._ui = wp.ScriptedUI({})
    return wp, menu, launched


def test_708_a_marker_in_the_expert_flags_makes_the_manifest_look(tmp_path, monkeypatch):
    """Two mode-6 rows, the run's marker _EXPORT nowhere in the paths, and
    `--export-marker OUT` in the expert flags: both rows write
    OUT/16B_JXL/foto.jxl. The scan was skipped as impossible."""
    wp, menu, launched = _wizard(tmp_path, monkeypatch)
    root = tmp_path / HOSTILE / "OUT"
    for sub in ("x", "y"):
        (root / sub).mkdir(parents=True)
        tifffile.imwrite(str(root / sub / "foto.tif"), np.zeros((16, 16, 3), np.uint16),
                         photometric="rgb")
    entries = [(str(root / s), str(root / s), 6) for s in ("x", "y")]
    wf = {"origin_format": "tiff", "dest_format": "jxl", "mode": 99, "workers": 1,
          "conversion_type": "jxl_tiff_encoder", "manifest_entries": entries,
          "manifest_row_options": [{}, {}], "dry_run": True, "staging": None,
          "advanced_options": {"sync": True}, "expert_flags": "--export-marker OUT",
          "mode_config": {}, "unattended": True, "effort": 7, "distance": 0.1,
          "use_ram": True}
    assert not menu._execute_manifest_workflow(wf, {k: True for k in (
        "cjxl", "djxl", "exiftool", "magick", "tifffile", "pillow", "imagecodecs")})
    assert not launched
    assert any("same name in" in ln for ln in menu._ui.output), menu._ui.output[-15:]


def test_708_a_file_row_into_its_own_folder_is_in_place(tmp_path, monkeypatch):
    wp, _menu, _launched = _wizard(tmp_path, monkeypatch)
    d = tmp_path / HOSTILE
    d.mkdir()
    f = d / "a (2).jxl"
    f.write_bytes(b"\xff\x0a")
    assert wp._recompress_entry_in_place(str(f), str(d), 2)
    assert not wp._recompress_entry_in_place(str(f), str(tmp_path / "elsewhere"), 2)
    assert not wp._recompress_entry_in_place(str(d), str(tmp_path / "elsewhere"), 2)


# --- V1, V3: the same files, faster ------------------------------------------------

def test_v1_cjxl_threads_share_the_cores(monkeypatch):
    monkeypatch.setattr(common.os, "cpu_count", lambda: 32)
    assert common._cjxl_threads_for(1) is None          # one encode: every core
    assert common._cjxl_threads_for(0) is None
    assert common._cjxl_threads_for(8) == 8
    assert common._cjxl_threads_for(16) == 4
    assert common._cjxl_threads_for(24) == 4            # never fewer than 4
    assert common._cjxl_threads_for(2) is None          # 32: every core anyway
    monkeypatch.setattr(common.os, "cpu_count", lambda: 8)
    assert common._cjxl_threads_for(3) == 5             # max(4, 16 // 3)
    assert common._cjxl_threads_for(4) == 4
    assert common._cjxl_threads_for(2) is None          # 8: every core anyway


@needs_codecs
def test_v1_a_run_of_several_workers_passes_the_threads_and_then_forgets_them(
        tmp_path, monkeypatch):
    src = tmp_path / HOSTILE / "TIFF"
    for i in range(3):
        _rgb16_tiff(src / f"DSC_{i:04d} (2).tif", seed=i + 1)
    seen = []
    real = enc._run_captured

    def spy(cmd, *a, **k):
        if Path(str(cmd[0])).stem.lower() == "cjxl":
            seen.append([str(c) for c in cmd])
        return real(cmd, *a, **k)

    monkeypatch.setattr(enc, "_run_captured", spy)
    monkeypatch.setattr(common.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(sys, "argv", ["jxl_tiff_encoder.py", str(src),
                                      str(tmp_path / HOSTILE / "JXL"), "--mode", "0",
                                      "--distance", "1", "--effort", "1", "--workers", "3",
                                      "--no-preflight"])
    try:
        enc.main()
    except SystemExit as e:
        assert not e.code
    encodes = [c for c in seen if "-d" in c]
    assert encodes and all("--num_threads=21" in c for c in encodes), encodes
    assert enc._CJXL_THREADS is None


def test_v3_the_preview_table_is_the_rounding():
    import jxl_tiff_decoder as dec
    v = np.arange(65536, dtype=np.uint16)
    assert np.array_equal(dec._U16_TO_U8[v], np.rint(v / 257).astype(np.uint8))


# --- V2: the Explorer thumbnail from the image in memory -----------------------

def _spy_thumbnail_routes(module, monkeypatch):
    routes = []
    for name in ("_thumbnail_from_array", "_make_explorer_thumbnail"):
        def spy(*a, _real=getattr(module, name), _n=name, **k):
            routes.append((_n, _real(*a, **k)))
            return routes[-1][1]
        monkeypatch.setattr(module, name, spy)
    return routes


def _diff(a, b):
    x = np.asarray(a, dtype=np.int16)
    y = np.asarray(b, dtype=np.int16)
    assert x.shape == y.shape, (x.shape, y.shape)
    d = np.abs(x - y)
    return float(d.mean()), int(d.max())


@needs_codecs
def test_v2_a_masters_thumbnail_comes_from_its_page(tmp_path, monkeypatch):
    """The encoder makes the thumbnail from the TIFF page it holds, in the
    page's own profile, before cjxl: the master is not decoded again. For a
    lossless master it is the thumbnail the decode made (measured on 73 real
    outputs: identical wherever the TIFF has a profile)."""
    import io
    from PIL import Image
    icc = _icc()
    tif = tmp_path / HOSTILE / "TIFF16" / "DSC_0001 (2).tif"
    tif.parent.mkdir(parents=True)
    rng = np.random.default_rng(5)
    y, x = np.mgrid[0:800, 0:1000]
    img = np.stack([x * 65, y * 81, np.full_like(x, 20000)], -1) + rng.integers(0, 3000, (800, 1000, 3))
    tifffile.imwrite(str(tif), np.clip(img, 0, 65535).astype(np.uint16), photometric="rgb",
                     extratags=[(34675, "B", len(icc), icc, True)])
    routes = _spy_thumbnail_routes(enc, monkeypatch)
    out = tmp_path / HOSTILE / "16B_JXL"
    _encoder_main(monkeypatch, tif.parent, out, "--mode", "0", "--distance", "0",
                  "--effort", "1", "--workers", "1", "--no-preflight",
                  "--explorer-thumbnail", "on")
    jxl = out / "DSC_0001 (2).jxl"
    assert [n for n, _j in routes] == ["_thumbnail_from_array"], routes
    assert enc._thumbnail_box_fingerprint(jxl), "no thumbnail box"
    stored = _thumbnail_of(jxl)
    work = tmp_path / "decode"
    work.mkdir()
    ref = common._make_explorer_thumbnail(enc._G, jxl, work)
    mean, worst = _diff(stored, Image.open(io.BytesIO(ref)).convert("RGB"))
    assert mean < 0.1 and worst <= 2, (mean, worst)


def test_703_a_thumbnail_entry_recorded_as_a_full_page_is_not_counted(tmp_path):
    """Review N2: a thumbnail entry recorded with SubfileType 0 is written as
    SubfileType 1; counted as a full page, its TIFF looked short on every sync."""
    import jxl_tiff_decoder as dec
    tif = tmp_path / HOSTILE / "two (2).tif"
    tif.parent.mkdir(parents=True)
    with tifffile.TiffWriter(str(tif)) as w:
        w.write(np.zeros((32, 32, 3), np.uint16), photometric="rgb")
        w.write(np.zeros((8, 8, 3), np.uint16), photometric="rgb", subfiletype=1)
    entries = [(tmp_path / "a.jxl", 0, False, False, 0, False, 16),
               (tmp_path / "a_thumbnail.jxl", 1, True, False, 0, False, 16)]
    assert not dec._fewer_pages_than_group(tif, entries)


# --- The review of rc5 (AI_tools/261122_Opus_review_sweep-fixes.md) --------------

def _jpegs(folder: Path, n: int):
    from PIL import Image
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(1)
    for i in range(n):
        Image.fromarray(rng.integers(0, 255, (64, 96, 3), dtype=np.uint8)).save(
            folder / f"p{i} (2).jpg", quality=90)


@needs_codecs
def test_m1_a_ctrl_c_during_the_transcoders_planning_stops_the_run(tmp_path, monkeypatch):
    """The handler is in place from main(); cmd_*'s own _reset_abort after its
    planning cleared the press, and the run converted every file, exit 0."""
    import _thread
    import jxl_jpeg_transcoder as tr
    src = tmp_path / HOSTILE / "src"
    _jpegs(src, 4)
    real = tr._record_planned_outputs

    def hooked(pairs):
        _thread.interrupt_main()                 # Ctrl+C during the planning
        return real(pairs)

    monkeypatch.setattr(tr, "_record_planned_outputs", hooked)
    out = tmp_path / HOSTILE / "out"
    monkeypatch.setattr(sys, "argv", ["jxl_jpeg_transcoder.py", str(src), str(out),
                                      "--force-transcode", "--mode", "0", "--workers", "1"])
    with pytest.raises(KeyboardInterrupt):
        tr.main()
    assert not (out.exists() and list(out.glob("*.jxl")))


@needs_codecs
def test_s2_no_source_is_deleted_after_a_ctrl_c(tmp_path, monkeypatch):
    """A Ctrl+C that lands in the delete gate (74 % of a deleting run) said
    "no other file starts", then the gate deleted every TIFF."""
    import _thread
    src = tmp_path / HOSTILE / "TIFF"
    tifs = [src / f"DSC_000{i} (2).tif" for i in range(4)]
    for i, t in enumerate(tifs):
        _rgb16_tiff(t, seed=i + 1)
    real = enc._verify_roundtrip_page
    fired = []

    def check(*a, **k):
        if not fired:
            fired.append(1)
            _thread.interrupt_main()             # Ctrl+C while the gate checks
        return real(*a, **k)

    monkeypatch.setattr(enc, "_verify_roundtrip_page", check)
    out = tmp_path / HOSTILE / "JXL"
    # Lossless: the gate's check passes for every page (noise at d=1 fails its
    # PSNR floor, and then nothing is deleted in any version).
    monkeypatch.setattr(sys, "argv", ["jxl_tiff_encoder.py", str(src), str(out), "--mode", "0",
                                      "--distance", "0", "--effort", "1", "--workers", "1",
                                      "--no-preflight", "--delete-source", "--delete-confirm-off"])
    with pytest.raises(KeyboardInterrupt):
        enc.main()
    kept = [t for t in tifs if t.exists()]
    assert kept == tifs, kept    # also the one judged at the press (re-read before its delete)


@needs_codecs
def test_s2_a_check_that_could_not_run_does_not_rename_the_master(tmp_path, monkeypatch):
    """#701 renames a JXL that FAILED its check; one whose check could not run
    (djxl killed by the same Ctrl+C, a locked file) is no verdict: the TIFF is
    kept, the JXL keeps its name."""
    src = tmp_path / HOSTILE / "TIFF"
    _rgb16_tiff(src / "DSC_0001 (2).tif")

    def killed(*a, **k):
        raise RuntimeError("djxl exited 3221225786")

    monkeypatch.setattr(enc, "_decode_jxl_for_verify", killed)
    out = tmp_path / HOSTILE / "JXL"
    _encoder_main(monkeypatch, src, out, "--mode", "0", "--distance", "1", "--effort", "1",
                  "--workers", "1", "--no-preflight", "--delete-source", "--delete-confirm-off")
    assert (src / "DSC_0001 (2).tif").exists()
    assert (out / "DSC_0001 (2).jxl").exists()
    assert not list(out.glob(f"*{enc.ERROR_SUFFIX}*"))


@needs_codecs
def test_s3_a_keep_check_that_raises_keeps_its_source_and_the_gate_goes_on(tmp_path,
                                                                          monkeypatch):
    """The #691 profile read raising (a timeout) ended the lossy gate in a
    traceback: no summary, the sources after it never judged."""
    import jxl_jpeg_transcoder as tr
    icc = _icc()
    src = tmp_path / "tif [src]"
    src.mkdir()
    rng = np.random.default_rng(4)
    for i in range(3):
        tifffile.imwrite(str(src / f"DSC_000{i} (2).tif"),
                         rng.integers(0, 65535, (48, 64, 3), dtype=np.uint16), photometric="rgb",
                         extratags=[(34675, "B", len(icc), icc, True)])
    masters = tmp_path / HOSTILE / "JXL"
    r = _script("jxl_tiff_encoder.py", src, masters, "--mode", "0", "--distance", "0",
                "--effort", "1", "--no-preflight")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    real = tr._read_creator_and_relation
    real_gate = tr._delete_converted_sources
    in_gate = []

    def flaky(path, *a, **k):
        if in_gate and Path(path).name == "DSC_0001 (2).jxl":
            raise subprocess.TimeoutExpired(["exiftool"], 1)
        return real(path, *a, **k)

    def gate(*a, **k):
        in_gate.append(1)                       # the conversions are done
        return real_gate(*a, **k)

    monkeypatch.setattr(tr, "_read_creator_and_relation", flaky)
    monkeypatch.setattr(tr, "_delete_converted_sources", gate)
    monkeypatch.setattr(sys, "argv", ["jxl_jpeg_transcoder.py", str(masters),
                                      str(tmp_path / HOSTILE / "PNG"), "--force-convert",
                                      "--decode", "--format", "png", "--mode", "0",
                                      "--workers", "1", "--delete-source",
                                      "--delete-confirm-off"])
    try:
        tr.main()
    except SystemExit as e:
        assert e.code in (0, None)
    assert (masters / "DSC_0001 (2).jxl").exists()
    assert not (masters / "DSC_0000 (2).jxl").exists()
    assert not (masters / "DSC_0002 (2).jxl").exists()


def test_s5_a_second_ctrl_c_stops_after_another_abort():
    """With another reason latched first (a full disk), every Ctrl+C only
    printed "Ctrl+C again stops at once"."""
    import signal
    import time
    enc._reset_abort()
    try:
        enc._abort_reason = "disk full on the output volume"
        raised = 0
        with enc._ctrl_c_finishes_the_run():
            for _ in range(3):
                try:
                    signal.raise_signal(signal.SIGINT)
                    time.sleep(0.05)
                except KeyboardInterrupt:
                    raised += 1
        assert raised == 2
    finally:
        enc._reset_abort()


def test_n4_a_jxl_the_gate_cannot_read_is_kept(tmp_path):
    import jxl_jpeg_transcoder as tr
    gone = tmp_path / HOSTILE / "gone (2).jxl"
    why = tr._lossy_decode_keep_reason(gone, tmp_path / "out.png")
    assert why and why.startswith("KEEP (the JXL could not be read"), why


@needs_codecs
def test_n3_the_dry_run_counts_an_unread_record_as_an_error(tmp_path, monkeypatch, capsys):
    import json
    master = _scan_master(tmp_path / HOSTILE)
    real = rec._run_captured

    def stalled_batch(cmd, timeout, *a, **k):
        if k.get("stall") is not None and "-@" in cmd:
            argfile = Path(cmd[cmd.index("-@") + 1])
            if argfile.is_file() and "HistoryParameters" in argfile.read_text(
                    encoding="utf-8", errors="replace"):
                raise subprocess.TimeoutExpired(cmd, timeout)
        return real(cmd, timeout, *a, **k)

    monkeypatch.setattr(rec, "_run_captured", stalled_batch)
    monkeypatch.setattr(sys, "argv", ["jxl_recompressor.py", str(master.parent),
                                      "--mode", "0", "--effort", "3", "--workers", "1",
                                      "--dry-run", "--summary-json"])
    with pytest.raises(SystemExit):
        rec.main()
    line = next(ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("##JXLSUM##"))
    assert json.loads(line[len("##JXLSUM##"):])["errors"] == 1, line


@needs_codecs
def test_n5_a_tiff_gone_during_its_encode_keeps_its_jxl(tmp_path, monkeypatch):
    """A TIFF that vanished while its page was encoded (moved, deleted by
    another program) counted as "changed": the JXL, maybe its only copy, was
    thrown away. It is kept, with a warning."""
    src = tmp_path / HOSTILE / "TIFF"
    _rgb16_tiff(src / "DSC_0001 (2).tif")
    seen = []
    real = enc._stat_identity

    def identity(path):
        seen.append(path)
        return real(path) if len(seen) == 1 else None    # gone after the read

    monkeypatch.setattr(enc, "_stat_identity", identity)
    out = tmp_path / HOSTILE / "JXL"
    code = _encoder_main(monkeypatch, src, out, "--mode", "0", "--distance", "0",
                         "--effort", "1", "--workers", "1", "--no-preflight")
    assert code == 0
    assert (out / "DSC_0001 (2).jxl").exists()


@needs_codecs
def test_701_every_failing_page_of_a_multi_page_tiff_is_renamed(tmp_path, monkeypatch):
    """The second review of rc5, S3: the gate stopped at the first page that
    failed, so a second bad page kept its final name and the next sync took it
    for up to date. Both pages fail here (an impossible PSNR floor)."""
    src = tmp_path / HOSTILE / "TIFF"
    src.mkdir(parents=True)
    rng = np.random.default_rng(3)
    with tifffile.TiffWriter(str(src / "two (2).tif")) as w:
        for _ in range(2):
            w.write(rng.integers(0, 65535, (64, 96, 3), dtype=np.uint16), photometric="rgb")
    monkeypatch.setattr(enc, "VERIFY_LOSSY_MIN_PSNR", 500.0)
    out = tmp_path / HOSTILE / "JXL"
    code = _encoder_main(monkeypatch, src, out, "--mode", "0", "--distance", "1",
                         "--effort", "1", "--workers", "1", "--no-preflight",
                         "--delete-source", "--delete-confirm-off")
    assert code == 1
    assert (src / "two (2).tif").exists()
    names = sorted(p.name for p in out.glob("*.jxl"))
    assert names == [f"two (2){enc.ERROR_SUFFIX}.jxl", f"two (2)_page1{enc.ERROR_SUFFIX}.jxl"], names


# --- The second review of rc5 (AI_tools/261122_Opus_review2_sweep-fixes.md) -----

def _press_ctrl_c_on_entry(monkeypatch, module, gate_name):
    """Ctrl+C pressed as the delete gate starts: the flag the handler sets."""
    real = getattr(module, gate_name)

    def gate(*a, **k):
        module._ctrl_c_seen = True
        return real(*a, **k)

    monkeypatch.setattr(module, gate_name, gate)


def _masters(tmp_path, n=2):
    src = tmp_path / "tif [src]"
    for i in range(n):
        _rgb16_tiff(src / f"DSC_000{i} (2).tif", seed=i + 1)
    masters = tmp_path / HOSTILE / "JXL"
    r = _script("jxl_tiff_encoder.py", src, masters, "--mode", "0", "--distance", "0",
                "--effort", "1", "--no-preflight")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    return masters, sorted(masters.glob("*.jxl"))


@needs_codecs
def test_s2_the_decoders_gate_deletes_nothing_after_a_ctrl_c(tmp_path, monkeypatch):
    import jxl_tiff_decoder as dec
    masters, jxls = _masters(tmp_path)
    _press_ctrl_c_on_entry(monkeypatch, dec, "_delete_decoded_sources")
    monkeypatch.setattr(sys, "argv", ["jxl_tiff_decoder.py", str(masters),
                                      str(tmp_path / HOSTILE / "TIFF"), "--mode", "0",
                                      "--workers", "1", "--delete-source", "--delete-confirm-off"])
    with pytest.raises(KeyboardInterrupt):
        dec.main()
    assert all(j.exists() for j in jxls)


@needs_codecs
def test_s2_the_transcoders_lossless_gate_deletes_nothing_after_a_ctrl_c(tmp_path,
                                                                        monkeypatch):
    import jxl_jpeg_transcoder as tr
    src = tmp_path / HOSTILE / "JPEG"
    _jpegs(src, 2)
    jpgs = sorted(src.glob("*.jpg"))
    _press_ctrl_c_on_entry(monkeypatch, tr, "_delete_transcoded_sources")
    monkeypatch.setattr(sys, "argv", ["jxl_jpeg_transcoder.py", str(src),
                                      str(tmp_path / HOSTILE / "JXL"), "--force-transcode",
                                      "--mode", "0", "--workers", "1", "--delete-source",
                                      "--delete-confirm-off"])
    with pytest.raises(KeyboardInterrupt):
        tr.main()
    assert all(j.exists() for j in jpgs)


@needs_codecs
def test_s2_the_transcoders_lossy_gate_deletes_nothing_after_a_ctrl_c(tmp_path, monkeypatch):
    import jxl_jpeg_transcoder as tr
    masters, jxls = _masters(tmp_path)
    _press_ctrl_c_on_entry(monkeypatch, tr, "_delete_converted_sources")
    monkeypatch.setattr(sys, "argv", ["jxl_jpeg_transcoder.py", str(masters),
                                      str(tmp_path / HOSTILE / "PNG"), "--force-convert",
                                      "--decode", "--format", "png", "--mode", "0",
                                      "--workers", "1", "--delete-source", "--delete-confirm-off"])
    with pytest.raises(KeyboardInterrupt):
        tr.main()
    assert all(j.exists() for j in jxls)


@needs_codecs
def test_s2_the_recompressors_gate_deletes_nothing_after_a_ctrl_c(tmp_path, monkeypatch):
    """rc4's recompressor deleted the converted sources after a Ctrl+C."""
    masters, jxls = _masters(tmp_path)
    _press_ctrl_c_on_entry(monkeypatch, rec, "_delete_gate")
    monkeypatch.setattr(sys, "argv", ["jxl_recompressor.py", str(masters),
                                      str(tmp_path / HOSTILE / "small"), "--mode", "2",
                                      "--distance", "1", "--effort", "1", "--workers", "1",
                                      "--on-unknown", "convert", "--on-regeneration", "convert",
                                      "--on-downgrade", "convert", "--no-preflight",
                                      "--delete-source", "--delete-confirm-off"])
    with pytest.raises(SystemExit) as e:
        rec.main()
    assert e.value.code == 130
    assert all(j.exists() for j in jxls)


@needs_codecs
@pytest.mark.parametrize("encodes", [True, False])
def test_694_only_a_transcoder_run_that_encodes_says_the_version_is_unknown(
        tmp_path, monkeypatch, caplog, encodes):
    """The warning comes from the run itself (not only the helper), and not
    from a decode-only run, which writes no archive and no alpha (review N5)."""
    import logging
    import jxl_jpeg_transcoder as tr
    monkeypatch.setattr(tr, "_tool_version", lambda exe, *a, **k: None)
    if encodes:
        src = tmp_path / HOSTILE / "JPEG"
        _jpegs(src, 1)
        argv = [str(src), str(tmp_path / HOSTILE / "JXL"), "--force-transcode", "--mode", "0"]
    else:
        masters, _j = _masters(tmp_path, 1)
        argv = [str(masters), str(tmp_path / HOSTILE / "OUT"), "--force-convert", "--decode",
                "--format", "png", "--mode", "0"]
    monkeypatch.setattr(sys, "argv", ["jxl_jpeg_transcoder.py", *argv, "--workers", "1"])
    with caplog.at_level(logging.WARNING):
        try:
            tr.main()
        except SystemExit:
            pass
    said = any("gave no version" in r.getMessage() for r in caplog.records)
    assert said is encodes


@needs_codecs
def test_694_an_encoder_run_says_the_version_is_unknown(tmp_path, monkeypatch, caplog):
    import logging
    src = tmp_path / HOSTILE / "TIFF"
    _rgb16_tiff(src / "DSC_0001 (2).tif")
    monkeypatch.setattr(enc, "_tool_version", lambda exe, *a, **k: None)
    with caplog.at_level(logging.WARNING):
        _encoder_main(monkeypatch, src, tmp_path / HOSTILE / "JXL", "--mode", "0",
                      "--distance", "0", "--effort", "1", "--workers", "1", "--no-preflight")
    assert any("gave no version" in r.getMessage() for r in caplog.records)


def test_711_the_jxlinfo_notes_are_emptied_for_each_serve_job():
    import jxl_jpeg_transcoder as tr
    import jxl_tiff_decoder as dec
    for module in (dec, tr):
        assert "_JXLINFO_NOTES" in module._SERVE_FILE_CACHES
