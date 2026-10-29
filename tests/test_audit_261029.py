"""Audit of 2026-10-29 (#551-#560).

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
#556 A JPEG -> JXL run with staging filed the destination checksums only at
     the very end: stopped in between, every moved JXL lacked its line and
     later deleting runs refused it for good.
#557 A lossless JPEG archive without its checksum line could be overwritten
     by another JPEG of the same name (or a lossless decode could overwrite a
     JPEG its JXL does not hold): no line was "no evidence".
#558 The recompressor's verbatim copy of a lossless JPEG archive — the only
     copy once --delete-source removed its source — was overwritten by
     another same-named file: jbrd files carry no marker to compare.
#559 Three in-place edits (the encoder's restamp, the recompressor's
     thumbnail refresh, the transcoder's jbrd repair) copied a file, worked on
     the copy and swapped it in without checking the original had not been
     rewritten meanwhile: another run's new version was put back to the old.
#560 The decoder judged an existing TIFF safe to replace when a group's
     decode started and swapped the fresh decode in when it ended: an edit
     saved in between was overwritten.

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


def _run_rc(tmp_path, script, *args):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"), "JXLPHOTO_JXL_THUMB": "0"}
    r = subprocess.run([sys.executable, str(REPO / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, env=env, stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout + r.stderr


def _run(tmp_path, script, *args):
    rc, out = _run_rc(tmp_path, script, *args)
    assert rc == 0, out[-6000:]
    return out


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


# ------------------------------------------------------------------- #556
def _jpeg_at(path, seed):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    Image.fromarray((rng.random((120, 160, 3)) * 255).astype(np.uint8)).save(path, quality=90)
    return path


def _outtakes(tmp_path, name="DSC_0001 [x].JPG", seed=1):
    """A camera-like JPEG in <event>\\<cam>\\_OUTTAKES, as the owner's preset sees it."""
    d = tmp_path / HOSTILE / "2026" / "260101_伊豆沼 [FINAL] 5%" / "1_Z8" / "_OUTTAKES"
    return _jpeg_at(d / name, seed)


_OUTTAKES_ARGS = ("--mode", "6", "--export-marker", "_OUTTAKES", "--sync", "--workers", "1",
                  "--force-transcode")


def _strip_checksums(folder):
    """What a run stopped between moving its JXLs and recording them leaves."""
    for db in folder.rglob("checksums.md5"):
        db.unlink()


@needs_tools
@pytest.mark.parametrize("mode", ["6", "8"])
def test_556_a_jxl_without_its_checksum_is_adopted_when_it_reconstructs_the_jpeg(tmp_path, mode):
    """Mode 6 collapses folders: the strict provenance check adopts it. Mode 8
    keeps them: the --delete-skipped gate does."""
    jpg = _outtakes(tmp_path)
    original = jpg.read_bytes()
    root = tmp_path / HOSTILE / "2026"
    args = _OUTTAKES_ARGS if mode == "6" else ("--mode", "8", "--sync", "--workers", "1",
                                               "--force-transcode")
    _run(tmp_path, "jxl_jpeg_transcoder.py", root, *args)
    jxl = (jpg.parent / "JXL_jpeg" / (jpg.stem + ".jxl") if mode == "6"
           else jpg.with_suffix(".jxl"))
    assert jxl.exists() and jpg.exists()
    _strip_checksums(root)
    log = _run(tmp_path, "jxl_jpeg_transcoder.py", root, *args,
               "--delete-source", "--delete-skipped", "--delete-confirm-off")
    assert not jpg.exists(), log[-2500:]
    assert "ADOPTED" in log
    out = tmp_path / "recon.jpg"
    subprocess.run(["djxl", "--reconstruct_jpeg", str(jxl), str(out)], check=True,
                   capture_output=True)
    assert out.read_bytes() == original


@needs_tools
def test_556_another_jpeg_of_the_same_name_is_never_adopted(tmp_path):
    jpg = _outtakes(tmp_path)
    root = tmp_path / HOSTILE / "2026"
    _run(tmp_path, "jxl_jpeg_transcoder.py", root, *_OUTTAKES_ARGS)
    _strip_checksums(root)
    _outtakes(tmp_path, seed=2)                 # a different photo, same name
    _newer(jpg)
    _rc, log = _run_rc(tmp_path, "jxl_jpeg_transcoder.py", root, *_OUTTAKES_ARGS,
                       "--delete-source", "--delete-skipped", "--delete-confirm-off")
    assert jpg.exists(), log[-2500:]
    assert "ADOPTED" not in log


@needs_tools
def test_556_a_no_md5_run_says_why_it_cannot_adopt(tmp_path):
    """--no-md5 records no line, so nothing is adopted — and the refusal says
    that, not that the reconstruction failed."""
    jpg = _outtakes(tmp_path)
    root = tmp_path / HOSTILE / "2026"
    _run(tmp_path, "jxl_jpeg_transcoder.py", root, *_OUTTAKES_ARGS)
    _strip_checksums(root)
    _rc, log = _run_rc(tmp_path, "jxl_jpeg_transcoder.py", root, *_OUTTAKES_ARGS, "--no-md5",
                       "--delete-source", "--delete-skipped", "--delete-confirm-off")
    assert jpg.exists(), log[-2500:]
    assert "--no-md5 records none" in " ".join(log.split())


@needs_tools
def test_556_a_stopped_staging_run_files_the_checksums_of_what_it_moved(tmp_path, monkeypatch):
    """Two destination folders, the run stopped (Ctrl+C) while the second one
    is converted: the first folder's JXL, already moved, has its line."""
    first = _outtakes(tmp_path, "A.JPG")
    other = tmp_path / HOSTILE / "2026" / "260202_別 [x]" / "_OUTTAKES"
    other.mkdir(parents=True)
    shutil.copy2(first, other / "B.JPG")
    finals = [first.parent / "JXL_jpeg" / "A.jxl", other / "JXL_jpeg" / "B.jxl"]
    staging = tmp_path / "staging [一時]"
    staging.mkdir()
    monkeypatch.setattr(tra, "TEMP2_DIR", str(staging))
    monkeypatch.setattr(tra, "STORE_MD5", True)
    monkeypatch.setattr(tra, "DELETE_SOURCE", False)
    tra.setup_logger()
    tra._reset_abort()
    real = tra.encode_one_transcode

    def stopped_at_b(src, *a, **k):
        if src.name == "B.JPG":
            raise KeyboardInterrupt
        return real(src, *a, **k)

    monkeypatch.setattr(tra, "encode_one_transcode", stopped_at_b)
    with pytest.raises(KeyboardInterrupt):
        tra.process_group_transcode([(first, finals[0]), (other / "B.JPG", finals[1])], 1,
                                    decode=False, verify=False, mode=6,
                                    reconvert_val=False, smart=True)
    assert finals[0].exists()
    assert tra.read_md5_db(finals[0]) == tra.md5_of_file(first)


@needs_tools
@pytest.mark.parametrize("worker", ["transcode", "convert"])
def test_556_a_reconversion_stopped_before_its_swap_leaves_the_old_line(tmp_path, monkeypatch,
                                                                      worker):
    """Without staging the lines used to be filed BEFORE the os.replace: a run
    stopped between the two left the OLD archive under the NEW source's
    checksum."""
    jpg = _outtakes(tmp_path)
    final = jpg.with_suffix(".jxl")
    monkeypatch.setattr(tra, "TEMP2_DIR", None)
    monkeypatch.setattr(tra, "STORE_MD5", True)
    tra.setup_logger()
    tra._reset_abort()

    def encode(reconvert):
        if worker == "transcode":
            return tra.encode_one_transcode(jpg, final, final, reconvert, 7, False)
        return tra.encode_to_jxl(jpg, final, final, effort=7, distance=0.0,
                                 reconvert_val=reconvert, smart=False)

    assert encode(False)[1] == "ok"
    old = tra.md5_of_file(jpg)
    assert tra.read_md5_db(final) == old
    _outtakes(tmp_path, seed=2)                 # re-exported in place

    def stopped(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(tra.os, "replace", stopped)
    with pytest.raises(KeyboardInterrupt):
        encode(True)
    monkeypatch.undo()
    assert tra.read_md5_db(final) == old        # still names the file on disk
    assert tra._jxl_reconstructs_to(final, jpg) is False


# ------------------------------------------------------------------- #557
@needs_tools
@pytest.mark.parametrize("guard", ["on", "off"])
def test_557_a_lineless_archive_is_never_overwritten_by_another_jpeg(tmp_path, guard):
    """The archive's checksums.md5 is gone (deleted as clutter, the .jxl files
    copied alone, written with --no-md5) and so is photo A's JPEG: a later
    sync with another foto.jpg must not overwrite the only copy of A — with
    the overwrite guard off too (it never relaxes the jbrd refusals)."""
    src = tmp_path / HOSTILE / "src [元]"
    out = tmp_path / HOSTILE / "out 5%"
    a = _jpeg_at(src / "A" / "foto (1).jpg", 1)
    _run(tmp_path, "jxl_jpeg_transcoder.py", src, out, "--mode", "2", "--workers", "1")
    archive = out / "foto (1).jxl"
    assert archive.exists() and tra.has_jbrd_box(archive)
    _strip_checksums(out)
    a.unlink()
    before = tra.md5_of_file(archive)
    _newer(_jpeg_at(src / "B" / "foto (1).jpg", 2))
    rc, log = _run_rc(tmp_path, "jxl_jpeg_transcoder.py", src, out, "--mode", "2", "--sync",
                      "--workers", "1", *(["--no-overwrite-guard"] if guard == "off" else []))
    assert tra.md5_of_file(archive) == before, log[-2500:]
    assert rc == 1, log[-2500:]
    assert "lossless archive of a DIFFERENT JPEG" in log


@needs_tools
def test_557_a_lineless_archive_of_the_same_jpeg_still_syncs(tmp_path):
    src = tmp_path / HOSTILE / "src [元]"
    out = tmp_path / HOSTILE / "out 5%"
    a = _jpeg_at(src / "A" / "foto (1).jpg", 1)
    _run(tmp_path, "jxl_jpeg_transcoder.py", src, out, "--mode", "2", "--workers", "1")
    _strip_checksums(out)
    _newer(a)                                   # same bytes, newer date
    log = _run(tmp_path, "jxl_jpeg_transcoder.py", src, out, "--mode", "2", "--sync",
               "--workers", "1")
    assert "REFUSING" not in log


@needs_tools
def test_557_a_lossless_decode_never_overwrites_a_jpeg_its_jxl_does_not_hold(tmp_path):
    a = _jpeg_at(tmp_path / HOSTILE / "a [元].jpg", 1)
    jxl_dir = tmp_path / HOSTILE / "jxl 5%"
    jxl_dir.mkdir()
    subprocess.run(["cjxl", str(a), str(jxl_dir / "foto (1).jxl"), "--lossless_jpeg=1"],
                   check=True, capture_output=True)
    out = tmp_path / HOSTILE / "out [出]"
    other = _jpeg_at(out / "foto (1).jpg", 2)  # another photo, its only copy
    before = tra.md5_of_file(other)
    rc, log = _run_rc(tmp_path, "jxl_jpeg_transcoder.py", jxl_dir, out, "--decode",
                      "--force-transcode", "--mode", "2", "--overwrite", "--workers", "1")
    assert tra.md5_of_file(other) == before, log[-2500:]
    assert rc == 1, log[-2500:]
    assert "not the one this JXL holds" in log


# ------------------------------------------------------------------- #558
def _jbrd_jxl(path, seed, effort=7):
    """A lossless JPEG archive (jbrd), as the transcoder writes it."""
    jpg = _jpeg_at(path.with_name(f"src_{seed}.jpg"), seed)
    subprocess.run(["cjxl", str(jpg), str(path), "--lossless_jpeg=1", "-e", str(effort)],
                   check=True, capture_output=True)
    jpg.unlink()
    return path


def _archived_by_the_recompressor(tmp_path):
    """Photo A's lossless archive copied verbatim into a flat folder (mode 2),
    its source then deleted: the copy is the only one left."""
    src = tmp_path / HOSTILE / "src [元]"
    out = tmp_path / HOSTILE / "out 5%"
    a = _jbrd_jxl(src / "A" / "foto (1).jxl", 1)
    _run(tmp_path, "jxl_recompressor.py", src, out, *_REC_COPY,
         "--delete-source", "--delete-confirm-off")
    copy = out / "foto (1).jxl"
    assert copy.exists() and not a.exists()
    return src, out, copy


_REC_COPY = ("--mode", "2", "--workers", "1", "--jbrd-policy", "copy")


@needs_tools
@pytest.mark.parametrize("guard", ["on", "off"])
def test_558_another_jpeg_archive_never_overwrites_the_only_copy(tmp_path, guard):
    src, out, copy = _archived_by_the_recompressor(tmp_path)
    before = tra.md5_of_file(copy)
    _newer(_jbrd_jxl(src / "B" / "foto (1).jxl", 2))
    rc, log = _run_rc(tmp_path, "jxl_recompressor.py", src, out, *_REC_COPY, "--sync",
                      *(["--no-overwrite-guard"] if guard == "off" else []))
    assert tra.md5_of_file(copy) == before, log[-2500:]
    assert rc == 1, log[-2500:]
    assert "lossless archive of a DIFFERENT JPEG" in log


@needs_tools
def test_558_a_master_never_overwrites_a_jpeg_archive_of_the_same_name(tmp_path):
    src, out, copy = _archived_by_the_recompressor(tmp_path)
    before = tra.md5_of_file(copy)
    png = tmp_path / "b.png"
    from PIL import Image
    Image.fromarray((np.random.default_rng(3).random((120, 160, 3)) * 255)
                    .astype(np.uint8)).save(png)
    b = src / "B" / "foto (1).jxl"
    b.parent.mkdir(parents=True)
    subprocess.run(["cjxl", str(png), str(b), "-d", "1", "--container=1"], check=True,
                   capture_output=True)
    _newer(b)
    rc, log = _run_rc(tmp_path, "jxl_recompressor.py", src, out, "--mode", "2", "--sync",
                      "--workers", "1", "--on-unknown", "convert", "--distance", "1")
    assert tra.md5_of_file(copy) == before, log[-2500:]
    assert rc == 1, log[-2500:]
    assert "is a lossless JPEG archive (jbrd)" in log


@needs_tools
def test_558_the_same_jpeg_rearchived_still_syncs(tmp_path):
    """Same JPEG, archived again at another effort: other bytes, same JPEG
    back from djxl --reconstruct_jpeg — a legitimate update."""
    src = tmp_path / HOSTILE / "src [元]"
    out = tmp_path / HOSTILE / "out 5%"
    a = _jbrd_jxl(src / "A" / "foto (1).jxl", 1, effort=1)
    _run(tmp_path, "jxl_recompressor.py", src, out, *_REC_COPY)
    _jbrd_jxl(a, 1, effort=9)
    assert tra.md5_of_file(a) != tra.md5_of_file(out / "foto (1).jxl")
    _newer(a)
    log = _run(tmp_path, "jxl_recompressor.py", src, out, *_REC_COPY, "--sync")
    assert "REFUSED" not in log
    assert tra.md5_of_file(out / "foto (1).jxl") == tra.md5_of_file(a)


# ------------------------------------------------------------------- #559
def _lossless_jxl(path, seed):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    png = path.with_name(f"src_{seed}.png")
    Image.fromarray((np.random.default_rng(seed).random((120, 160, 3)) * 255)
                    .astype(np.uint8)).save(png)
    subprocess.run(["cjxl", str(png), str(path), "-d", "0", "--container=1"], check=True,
                   capture_output=True)
    png.unlink()
    return path


def _rewritten_meanwhile(path, new_bytes):
    """What another run re-encoding `path` in the middle of an edit leaves."""
    path.write_bytes(new_bytes)
    t = time.time() + 5
    os.utime(path, (t, t))


@needs_tools
def test_559_the_thumbnail_refresh_never_puts_back_an_old_version(tmp_path, monkeypatch):
    jxl = _lossless_jxl(tmp_path / HOSTILE / "16B_JXL [x]" / "DSC_0001 (2).jxl", 1)
    new = _lossless_jxl(tmp_path / "newer.jxl", 2).read_bytes()
    monkeypatch.setattr(rec, "EXPLORER_THUMBNAIL", "on")

    def thumbnail_while_rewritten(path, tmp_dir):
        _rewritten_meanwhile(jxl, new)
        return "written"

    monkeypatch.setattr(rec, "_add_explorer_thumbnail", thumbnail_while_rewritten)
    status, detail = rec._refresh_one_thumbnail(jxl, dry_run=False)
    assert jxl.read_bytes() == new, (status, detail)
    assert status == "error" and "changed" in detail


@needs_tools
def test_559_a_restamp_never_puts_back_an_old_version(tmp_path, monkeypatch):
    _ev, tif_dir, jxl_dir = _event(tmp_path)
    _tiff(tif_dir / "DSC_0001 (2).tif", "NX Studio 1.6", 1, pages=("main",))
    _run(tmp_path, "jxl_tiff_encoder.py", tmp_path / HOSTILE / "2026", "--mode", "6",
         "--workers", "1")
    master = jxl_dir / "DSC_0001 (2).jxl"
    old_src = enc._read_source_markers_batch([master])[str(master)]["src"]
    assert old_src
    new = _lossless_jxl(tmp_path / "newer.jxl", 2).read_bytes()
    real = enc.reorder_jxl_boxes

    def reorder_while_rewritten(path):
        real(path)
        _rewritten_meanwhile(master, new)

    monkeypatch.setattr(enc, "reorder_jxl_boxes", reorder_while_rewritten)
    why = enc._rewrite_master_relation(
        master, [f"-XMP-dc:Relation-={enc.SRC_PREFIX}{old_src}",
                 f"-XMP-dc:Relation+={enc.SRC_PREFIX}0123456789abcdef"], "0123456789abcdef")
    assert master.read_bytes() == new, why
    assert why and "changed" in why


def test_559_a_jbrd_repair_never_puts_back_an_old_version(tmp_path, monkeypatch):
    jxl = tmp_path / HOSTILE / "JXL_jpeg [x] 5%" / "DSC_0001 (2).jxl"
    jxl.parent.mkdir(parents=True)
    jxl.write_bytes(b"an archive whose reconstruction fails")
    tra.setup_logger()

    def repaired_while_rewritten(path, tmp):
        tmp.write_bytes(b"the repaired copy")
        _rewritten_meanwhile(path, b"archived again by another run")
        return "0" * 32, "markers stripped"

    monkeypatch.setattr(tra, "_strip_markers_proving_repair", repaired_while_rewritten)
    state, detail = tra._repair_one_jbrd(jxl, False)
    assert jxl.read_bytes() == b"archived again by another run", (state, detail)
    assert state == "broken" and "changed" in detail


def test_559_a_leftover_page_rewritten_while_checked_is_not_removed(tmp_path, monkeypatch):
    """#551's clean-up reads the markers, then removes: a thumbnail page another
    run wrote in between (a new layout) must not be the one removed."""
    tif = tmp_path / HOSTILE / "TIFF16 [x]" / "DSC_0001 (2).tif"
    tif.parent.mkdir(parents=True)
    tif.write_bytes(b"the TIFF, still here")
    folder = tmp_path / HOSTILE / "16B_JXL 5%"
    folder.mkdir()
    new_main = folder / "DSC_0001 (2).jxl"
    new_main.write_bytes(b"this run's archive")
    leftover = folder / f"DSC_0001 (2)_page1{enc.THUMBNAIL_SUFFIX}.jxl"
    leftover.write_bytes(b"a thumbnail page of the earlier encode")
    src_id = enc._source_path_id(tif)
    newer = b"a NEW thumbnail page, written by another run meanwhile"

    def markers_while_rewritten(paths):
        _rewritten_meanwhile(leftover, newer)
        return {p: ({"src": src_id, "group": "old", "thumb": True}
                    if os.path.normcase(str(p)) == os.path.normcase(str(leftover))
                    else {"src": src_id, "group": "new", "thumb": False}) for p in paths}

    monkeypatch.setattr(enc, "_read_page_markers_batch", markers_while_rewritten)
    enc.setup_logger()
    enc._clean_stale_split_pages([(tif, new_main)], [((tif,), "ok", str(new_main))])
    assert leftover.exists() and leftover.read_bytes() == newer


# ------------------------------------------------------------------- #560
def _private_module(script, tag):
    """A private copy of a script (its globals cannot leak into the suite's)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"a261029_{tag}", str(REPO / script))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@needs_tools
@pytest.mark.parametrize("staging", [False, True])
def test_560_the_decoder_never_writes_over_a_tiff_saved_during_the_decode(tmp_path, monkeypatch,
                                                                          staging):
    _ev, tif_dir, jxl_dir = _event(tmp_path)
    _tiff(tif_dir / "DSC_0001 (2).tif", "NX Studio 1.6", 1, pages=("main",))
    _run(tmp_path, "jxl_tiff_encoder.py", tmp_path / HOSTILE / "2026", "--mode", "6",
         "--workers", "1")
    master = jxl_dir / "DSC_0001 (2).jxl"
    out = tmp_path / HOSTILE / "decoded [出]"
    _run(tmp_path, "jxl_tiff_decoder.py", jxl_dir, out, "--mode", "0", "--workers", "1")
    tiff = out / "DSC_0001 (2).tif"
    assert tiff.exists()
    _newer(master)                              # re-archived: the sync decodes it again
    edit = tmp_path / "edit.tif"
    _tiff(edit, "Adobe Photoshop 26.0", 9, pages=("main",))
    saved = edit.read_bytes()

    d = _private_module("jxl_tiff_decoder.py", f"dec{staging}")
    real = d._verify_tiff_integrity

    def saved_in_photoshop_meanwhile(path):
        if Path(path) != tiff:                  # the fresh decode, right before its swap
            tiff.write_bytes(saved)
            t = time.time() + 30
            os.utime(tiff, (t, t))
        return real(path)

    monkeypatch.setattr(d, "_verify_tiff_integrity", saved_in_photoshop_meanwhile)
    argv = ["jxl_tiff_decoder.py", str(jxl_dir), str(out), "--mode", "0", "--sync",
            "--workers", "1", "--delete-source", "--delete-confirm-off"]
    if staging:
        argv += ["--staging", str(tmp_path / "staging [一時]")]
    monkeypatch.setattr(sys, "argv", argv)
    try:
        d.main()
    except SystemExit:
        pass
    assert tiff.read_bytes() == saved, "the edit saved during the decode was overwritten"
    assert master.exists(), "the JXL was deleted although its decode was not delivered"


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
