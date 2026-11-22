#!/usr/bin/env python3
"""The speed round of 2026-11-21 (v2.31.0, plan 261121), what each change
must keep and what it must stop doing:

- the decoder writes a TIFF's metadata in ONE rewrite, not four (ExifTool
  rewrites the whole TIFF for every command that writes), and still decodes
  with a '%' in its temp folder;
- the encoder decodes a page ONCE for the Explorer thumbnail and the page
  check of --verify-roundtrip;
- the content sync hashes its TIFFs in parallel;
- the delete gate's pixel checks run in parallel, with the same verdicts;
- the recompressor's ImageMagick PNG stays in memory (no conv.png);
- the generated sRGB profile carries its fixed date in the transcoder's
  --to-srgb and the decoder's --target-icc srgb too (#685).

Real codecs, in a folder with Japanese, [ ], a space and '%'."""

import hashlib
import json
import os
import shutil
import stat
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_photo_common as common
import jxl_jpeg_transcoder as tr
import jxl_recompressor as rec
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc

needs_tools = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool", "magick")),
    reason="needs cjxl, djxl, exiftool and magick")

HOSTILE = "吾妻山 [x] 100%off %d"


def _run(mod, argv):
    old = sys.argv
    sys.argv = [mod.__name__ + ".py"] + [str(a) for a in argv]
    try:
        mod.main()
    except SystemExit as e:
        assert e.code in (0, None), f"{mod.__name__} exited {e.code}"
    finally:
        sys.argv = old


def _tiff(path, seed=0, size=(48, 64), xmp=None, smooth=False):
    """A 16-bit RGB TIFF as a camera program writes it: no tifffile Software
    and no shaped-JSON ImageDescription (those two are the decoder's rare
    clean-ups). `smooth`: a gradient, for a lossy encode the page check can
    pass (noise at d=1 is below its 20 dB floor)."""
    rng = np.random.default_rng(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    tags = [(700, 1, len(xmp), xmp, True)] if xmp else []
    if smooth:
        h, w = size
        y, x = np.mgrid[0:h, 0:w]
        img = np.stack([x * 65535 // max(1, w - 1), y * 65535 // max(1, h - 1),
                        np.full((h, w), 30000 + seed * 1000)], axis=2).astype(np.uint16)
    else:
        img = rng.integers(0, 65535, size + (3,), dtype=np.uint16)
    tifffile.imwrite(str(path), img, photometric="rgb", extratags=tags,
                     software="", metadata=None)
    return path


def _tag(path, name):
    r = enc._run_exiftool_argfile(["-j", "-" + name, str(path)], timeout=60)
    entry = json.loads(r.stdout)[0]
    return entry.get(name.split(":")[-1])


_XMP = ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title><rdf:Alt><rdf:li xml:lang="x-default">写真 [1]</rdf:li></rdf:Alt></dc:title>'
        '<dc:relation><rdf:Bag><rdf:li>user value, kept</rdf:li></rdf:Bag></dc:relation>'
        '</rdf:Description></rdf:RDF></x:xmpmeta>').encode("utf-8")


# ---------------------------------------------------------------------------
# The decoder: one rewrite of the TIFF
# ---------------------------------------------------------------------------

@needs_tools
def test_the_decoder_writes_a_tiff_s_metadata_in_one_rewrite(tmp_path, monkeypatch):
    """The EXIF, the XMP block (edited in a file of its own first) and the
    IPTC in ONE command on the TIFF; until v2.30.x four commands rewrote it."""
    src = _tiff(tmp_path / HOSTILE / "TIFF16" / "DSC_0001 (2).tif", xmp=_XMP)
    jxl_dir = tmp_path / HOSTILE / "16B_JXL"
    _run(enc, [src.parent, jxl_dir, "--mode", "0", "--distance", "0", "--effort", "3",
               "--workers", "1", "--no-preflight"])
    jxl = jxl_dir / "DSC_0001 (2).jxl"
    assert "jxlphoto-depth:16" in str(_tag(jxl, "XMP-dc:Relation"))
    writes = []
    real = dec._exiftool_commands

    def spy(commands, *a, **k):
        for c in commands:
            c = [str(x) for x in (c if not isinstance(c, Path) else [c])]
            # a command that writes the TIFF (not the block's small .xmp file)
            if "-overwrite_original" in c and not c[-1].lower().endswith(".xmp"):
                writes.append(c)
        return real(commands, *a, **k)

    monkeypatch.setattr(dec, "_exiftool_commands", spy)
    out = tmp_path / HOSTILE / "back [tif]"
    _run(dec, [jxl_dir, out, "--mode", "0", "--workers", "1"])
    tif = out / "DSC_0001 (2).tif"
    assert tif.exists()
    assert len(writes) == 1, writes
    rel = _tag(tif, "XMP-dc:Relation")
    rel = rel if isinstance(rel, list) else [rel]
    assert "user value, kept" in rel, rel
    assert not [v for v in rel if str(v).startswith(("jxlphoto-depth", "jxlphoto-page"))], rel
    assert any(str(v).startswith("jxlphoto-src:") for v in rel), rel
    assert _tag(tif, "XMP-dc:Title") == "写真 [1]"


@pytest.mark.parametrize("script", ["encoder", "decoder", "transcoder", "recompressor"])
@pytest.mark.parametrize("how", ["TEMP_DIR", "the system's TEMP"])
def test_a_percent_in_the_temp_folder_is_refused_at_the_start(tmp_path, monkeypatch, capsys,
                                                               script, how):
    """ExifTool reads a '%' in the name of a file it writes (-o) or reads a tag
    from (<=) as a format code, and the toolkit keeps such files in the temp
    folder: some steps failed file by file there, and the encoder kept only
    the XMP fields ExifTool can write (it dropped SilverFast's LSI settings).
    Every script refuses such a folder before its first file (the owner's
    call, 2026-11-21) — TEMP_DIR, or the system's TEMP when it is unset."""
    mod = {"encoder": enc, "decoder": dec, "transcoder": tr, "recompressor": rec}[script]
    temp = tmp_path / "tmp 100%off %d"
    temp.mkdir()
    if how == "TEMP_DIR":
        monkeypatch.setattr(mod, "TEMP_DIR", str(temp))
    else:
        import tempfile
        monkeypatch.setattr(mod, "TEMP_DIR", None)
        monkeypatch.setattr(tempfile, "tempdir", str(temp))
    src = tmp_path / HOSTILE / "in"
    src.mkdir(parents=True)
    out = tmp_path / HOSTILE / "out"
    old = sys.argv
    sys.argv = [mod.__name__ + ".py", str(src), str(out), "--mode", "0", "--workers", "1"]
    try:
        with pytest.raises(SystemExit) as e:
            mod.main()
    finally:
        sys.argv = old
    assert e.value.code == 2
    err = " ".join(capsys.readouterr().err.split())
    assert "the temp folder has a '%' in its path" in err and "TEMP_DIR" in err, err[-600:]


@needs_tools
def test_copy_metadata_keeps_the_old_route_for_a_percent_temp_folder(tmp_path):
    """Review S1: a run never gets there any more (refused at the start), but
    a direct call with such a temp folder still writes every field: the block
    goes straight from a plain-named JXL into the TIFF and the edits take a
    second command, as up to v2.30.x."""
    src = _tiff(tmp_path / HOSTILE / "TIFF16" / "DSC_0001 (2).tif", xmp=_XMP)
    jxl_dir = tmp_path / "吾妻山 [x] masters"           # a JXL path without '%'
    _run(enc, [src.parent, jxl_dir, "--mode", "0", "--distance", "0", "--effort", "3",
               "--workers", "1", "--no-preflight"])
    jxl = jxl_dir / "DSC_0001 (2).jxl"
    out = tmp_path / HOSTILE / "back [tif]"
    _run(dec, [jxl_dir, out, "--mode", "0", "--workers", "1"])
    tif = out / "DSC_0001 (2).tif"
    assert dec._run_exiftool_argfile(["-overwrite_original", "-XMP:all=", str(tif)],
                                     timeout=60).returncode == 0
    temp = tmp_path / "tmp 100%off %d"
    temp.mkdir()
    assert dec.copy_metadata(jxl, tif, temp, provenance_sources=[jxl]) is True
    rel = _tag(tif, "XMP-dc:Relation")
    rel = rel if isinstance(rel, list) else [rel]
    assert "user value, kept" in rel, rel
    assert not [v for v in rel if str(v).startswith(("jxlphoto-depth", "jxlphoto-page"))], rel
    assert any(str(v).startswith("jxlphoto-src:") for v in rel), rel
    assert _tag(tif, "XMP-dc:Title") == "写真 [1]"
    assert _tag(tif, "XMP-xmpMM:HistorySoftwareAgent")


def _hold_without_sharing(path):
    """Open `path` with no sharing at all, as an antivirus, an indexer or a
    backup agent does for an instant to a file just written."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k32.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)   # GENERIC_READ, OPEN_EXISTING
    assert h not in (None, 0, -1, 2 ** 64 - 1), ctypes.get_last_error()
    return lambda: k32.CloseHandle(h)


@needs_tools
@pytest.mark.skipif(sys.platform != "win32", reason="a lock without sharing is Windows")
def test_a_block_the_write_cannot_open_fails_the_copy(tmp_path, monkeypatch):
    """Fable's review, M1: when ExifTool cannot open the block's own file at the
    TIFF's write it says "Error opening file" and exits 0 — the TIFF had the
    EXIF only, no XMP and no jxlphoto-src, and copy_metadata said True (with
    --delete-source the JXL holding the only copy went)."""
    src = _tiff(tmp_path / HOSTILE / "TIFF16" / "DSC_0001 (2).tif", xmp=_XMP)
    jxl_dir = tmp_path / "吾妻山 [x] masters"           # no '%': the block's own file
    _run(enc, [src.parent, jxl_dir, "--mode", "0", "--distance", "0", "--effort", "3",
               "--workers", "1", "--no-preflight"])
    jxl = jxl_dir / "DSC_0001 (2).jxl"
    out = tmp_path / HOSTILE / "back [tif]"
    _run(dec, [jxl_dir, out, "--mode", "0", "--workers", "1"])
    tif = out / "DSC_0001 (2).tif"
    # the TIFF as the decoder has it before its metadata: no XMP at all
    assert dec._run_exiftool_argfile(["-overwrite_original", "-XMP:all=", str(tif)],
                                     timeout=60).returncode == 0
    work = tmp_path / "work [w]"
    work.mkdir()
    real = dec._exiftool_commands

    def held_at_the_write(commands, *a, **k):
        results = []
        for c in commands:
            lines = [str(x) for x in (c if not isinstance(c, Path) else [c])]
            block = next((x[len("-XMP<="):] for x in lines if x.startswith("-XMP<=")), None)
            release = _hold_without_sharing(block) if block else None
            try:
                results += real([c], *a, **k)
            finally:
                if release:
                    release()
        return results

    monkeypatch.setattr(dec, "_exiftool_commands", held_at_the_write)
    assert dec.copy_metadata(jxl, tif, work, provenance_sources=[jxl]) is False
    monkeypatch.undo()
    assert dec.copy_metadata(jxl, tif, work, provenance_sources=[jxl]) is True
    rel = _tag(tif, "XMP-dc:Relation")
    assert any(str(v).startswith("jxlphoto-src:") for v in (rel if isinstance(rel, list) else [rel]))


@needs_tools
def test_a_check_that_raises_keeps_its_source_and_the_gate_goes_on(tmp_path, monkeypatch,
                                                                   caplog):
    """Fable's review, S3: one pixel check that raised (a MemoryError in its
    comparison) ended the whole delete gate — now with `workers` checks at
    once. It keeps its own source; the others are judged."""
    names = ("DSC_0001 (2).tif", "DSC_0002 [b].tif", "DSC_0003 %.tif")
    src, out, args = _recorded_gate_run(tmp_path, monkeypatch, names)
    real = enc._verify_roundtrip_page

    def check(tiff, page, jxl, distance):
        if Path(tiff).name == "DSC_0002 [b].tif":
            raise MemoryError("planted")
        return real(tiff, page, jxl, distance)

    monkeypatch.setattr(enc, "_verify_roundtrip_page", check)
    enc._delete_sources(*args, workers=3)
    assert (src / names[1]).exists()
    assert not (src / names[0]).exists() and not (src / names[2]).exists()
    assert "the check itself failed (MemoryError: planted)" in caplog.text


def test_the_maker_notes_probe_never_reads_a_failed_copy_as_rewritable(tmp_path, monkeypatch):
    """A stub that did not get the EXIF proves nothing about it: "cannot
    tell" keeps the file an error (review nit)."""
    answers = [subprocess.CompletedProcess([], 1, "", "Error: could not copy"),
               subprocess.CompletedProcess([], 0, "    1 image files updated\n", "")]
    monkeypatch.setattr(dec, "_exiftool_commands", lambda commands, *a, **k: answers)
    assert dec._tiff_rewritable_after_copy(tmp_path / "x.jxl", tmp_path) is False


# ---------------------------------------------------------------------------
# The encoder: one decode for the thumbnail and the page check
# ---------------------------------------------------------------------------

@needs_tools
@pytest.mark.parametrize("distance", ["0", "1"])
def test_one_decode_per_page_for_the_thumbnail_and_the_check(tmp_path, monkeypatch, distance):
    src = _tiff(tmp_path / HOSTILE / "TIFF16" / "DSC_0001 (2).tif", size=(96, 128),
                smooth=True)
    decodes = []
    real = enc._run_to_memory

    def spy(cmd, *a, **k):
        if cmd and str(cmd[0]).lower().startswith("djxl"):
            decodes.append([str(c) for c in cmd])
        return real(cmd, *a, **k)

    monkeypatch.setattr(enc, "_run_to_memory", spy)
    checked = []
    real_check = enc._verify_roundtrip_page

    def check(*a, **k):
        checked.append(real_check(*a, **k))
        return checked[-1]

    monkeypatch.setattr(enc, "_verify_roundtrip_page", check)
    routes = []
    for _name in ("_thumbnail_from_array", "_make_explorer_thumbnail"):
        def _spy(*a, _real=getattr(enc, _name), _n=_name, **k):
            routes.append((_n, _real(*a, **k)))
            return routes[-1][1]
        monkeypatch.setattr(enc, _name, _spy)
    out = tmp_path / HOSTILE / "16B_JXL"
    _run(enc, [src.parent, out, "--mode", "0", "--distance", distance, "--effort", "3",
               "--workers", "1", "--no-preflight", "--verify-roundtrip",
               "--explorer-thumbnail", "on"])
    assert (out / "DSC_0001 (2).jxl").exists()
    assert checked and all(ok for ok, _d in checked), checked
    # One decode per page: the check's. Since V2 (rc5) the thumbnail is made
    # from the page in memory, so the decode it kept for the check is gone too.
    assert len(decodes) == 1, decodes
    assert [n for n, _j in routes] == ["_thumbnail_from_array"], routes
    assert routes[0][1][:2] == bytes([0xFF, 0xD8]), "the thumbnail from memory is not a JPEG"


@needs_tools
def test_a_kept_decode_of_another_codestream_is_never_used(tmp_path, monkeypatch):
    """The page check decodes again when the file's codestream is not the one
    the thumbnail decoded (here: replaced after the thumbnail)."""
    a = _tiff(tmp_path / HOSTILE / "a" / "x.tif", seed=1)
    b = _tiff(tmp_path / HOSTILE / "b" / "x.tif", seed=2)
    out_a, out_b = tmp_path / HOSTILE / "ja", tmp_path / HOSTILE / "jb"
    for s, o in ((a, out_a), (b, out_b)):
        _run(enc, [s.parent, o, "--mode", "0", "--distance", "0", "--effort", "3",
                   "--workers", "1", "--no-preflight"])
    ja, jb = out_a / "x.jxl", out_b / "x.jxl"
    pam = enc._run_to_memory(["djxl", str(ja), "-", "--output_format=pam"], 60).stdout
    enc._KEPT_DECODE.kept = (common._codestream_digest(ja), pam, tmp_path / "no1.icc",
                             tmp_path / "no2.icc")
    arr = enc._decode_jxl_for_verify(jb, tmp_path)
    assert enc._KEPT_DECODE.kept is None
    with tifffile.TiffFile(str(b)) as t:
        assert np.array_equal(arr, t.pages[0].asarray())


# ---------------------------------------------------------------------------
# The content sync: hashes in parallel, same verdicts
# ---------------------------------------------------------------------------

def test_the_sync_byte_check_hashes_in_parallel(tmp_path, monkeypatch):
    pairs = []
    for i in range(4):
        t = tmp_path / HOSTILE / f"t{i}.tif"
        j = tmp_path / HOSTILE / f"t{i}.jxl"
        t.parent.mkdir(parents=True, exist_ok=True)
        j.write_bytes(b"j")
        t.write_bytes(b"t%d" % i)
        old = time.time() - 1000
        os.utime(j, (old, old))
        pairs.append((t, j))
    sums = {str(t): f"sum{i}" for i, (t, _j) in enumerate(pairs)}
    marks = {str(j): {"srcsum": sums[str(t)] if i != 2 else "other"}
             for i, (t, j) in enumerate(pairs)}
    marks[str(pairs[3][1])] = {"srcsum": None, "unread": True}
    monkeypatch.setattr(enc, "OVERWRITE", "smart")
    monkeypatch.setattr(enc, "_read_source_markers_batch", lambda paths: marks)
    barrier = threading.Barrier(3, timeout=20)

    def content_id(path):
        barrier.wait()                  # three hashes in flight at once, or this raises
        return sums[str(path)]

    monkeypatch.setattr(enc, "_file_content_id", content_id)
    monkeypatch.setattr(enc, "_SYNC_SAME_BYTES", set())
    proven = enc._mark_sync_same_bytes([(t, j) for t, j in pairs], dry_run=False)
    assert proven == 2
    keys = {(os.path.normcase(str(t)), os.path.normcase(str(j))) for t, j in pairs[:2]}
    assert enc._SYNC_SAME_BYTES == keys


# ---------------------------------------------------------------------------
# The delete gate: the pixel checks in parallel, the same verdicts
# ---------------------------------------------------------------------------

def _recorded_gate_run(tmp_path, monkeypatch, names):
    src = tmp_path / HOSTILE / "TIFF16"
    for i, name in enumerate(names):
        _tiff(src / name, seed=i)
    out = tmp_path / HOSTILE / "16B_JXL"
    seen = {}
    monkeypatch.setattr(enc, "_delete_sources", lambda *a, **k: seen.setdefault("args", a))
    _run(enc, [src, out, "--mode", "0", "--distance", "0", "--effort", "3", "--workers", "2",
               "--no-preflight", "--delete-source", "--delete-confirm-off"])
    monkeypatch.undo()
    assert "args" in seen
    for key in enc._delete_stats:
        enc._delete_stats[key] = 0
    return src, out, seen["args"]


@needs_tools
def test_the_delete_gate_checks_pixels_in_parallel(tmp_path, monkeypatch, caplog):
    names = ("DSC_0001 (2).tif", "DSC_0002 [b].tif", "DSC_0003 %.tif")
    src, out, args = _recorded_gate_run(tmp_path, monkeypatch, names)
    barrier = threading.Barrier(3, timeout=20)
    real = enc._verify_roundtrip_page

    def check(tiff, page, jxl, distance):
        barrier.wait()                  # the three checks run at once, or this raises
        ok, detail = real(tiff, page, jxl, distance)
        if Path(tiff).name == "DSC_0002 [b].tif":
            return False, "planted failure"
        return ok, detail

    monkeypatch.setattr(enc, "_verify_roundtrip_page", check)
    enc._delete_sources(*args, workers=3)
    assert not (src / names[0]).exists() and not (src / names[2]).exists()
    assert (src / names[1]).exists()
    assert enc._delete_stats["deleted"] == 2 and enc._delete_stats["kept"] == 1
    assert "round-trip verification FAILED on page 0: planted failure" in caplog.text


def test_the_delete_gate_starts_no_pixel_check_for_a_source_it_keeps_anyway(
        tmp_path, monkeypatch, caplog):
    """Review S5: a --delete-skipped re-run over old masters whose provenance
    fails decoded every one of them before the loop kept them all."""
    tif = _tiff(tmp_path / HOSTILE / "TIFF16" / "DSC_0001 (2).tif")
    final = tmp_path / HOSTILE / "16B_JXL" / "DSC_0001 (2).jxl"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"\xff\x0a")
    results = [((str(tif), 0), "skipped", str(final), None)]
    tasks = [(tif, None, final, 0, None, None, None, None, None)]
    monkeypatch.setattr(enc, "DELETE_SKIPPED", True)
    monkeypatch.setattr(enc, "VERIFY_ROUNDTRIP", True)
    monkeypatch.setattr(enc, "_discarded_real_page_sources", set())
    monkeypatch.setattr(enc, "_unarchived_content_sources", {})
    monkeypatch.setattr(enc, "_delete_stats", dict.fromkeys(enc._delete_stats, 0))
    monkeypatch.setattr(enc, "_skipped_archive_proof", lambda pairs: {})   # no proof
    monkeypatch.setattr(enc, "_verify_jxl_integrity", lambda p: True)

    def check(*a):
        return True, "planted"

    monkeypatch.setattr(enc, "_verify_roundtrip_page", check)
    submitted = []
    real_pool = enc.ThreadPoolExecutor

    class RecordingPool(real_pool):
        def submit(self, fn, *a, **k):
            if fn is check:
                submitted.append(a)
            return super().submit(fn, *a, **k)

    monkeypatch.setattr(enc, "ThreadPoolExecutor", RecordingPool)
    enc._delete_sources(results, tasks, set(), False, workers=2)
    assert tif.exists()
    assert submitted == []
    assert "KEEP source (provenance check failed)" in caplog.text


# ---------------------------------------------------------------------------
# The recompressor: ImageMagick's PNG in memory
# ---------------------------------------------------------------------------

@needs_tools
@pytest.mark.parametrize("extra", [["--resize-long", "40"], ["--sharpen", "screen"]])
def test_the_imagemagick_png_stays_in_memory(tmp_path, monkeypatch, extra):
    src = _tiff(tmp_path / HOSTILE / "TIFF16" / "DSC_0001 (2).tif", size=(60, 80))
    masters = tmp_path / HOSTILE / "16B_JXL"
    _run(enc, [src.parent, masters, "--mode", "0", "--distance", "0", "--effort", "3",
               "--workers", "1", "--no-preflight"])
    magick = []
    real_c, real_m = rec._run_captured, rec._run_to_memory

    def spy_c(cmd, *a, **k):
        if cmd and str(cmd[0]).lower().startswith("magick"):
            magick.append(("file", [str(c) for c in cmd]))
        return real_c(cmd, *a, **k)

    def spy_m(cmd, *a, **k):
        if cmd and str(cmd[0]).lower().startswith("magick"):
            magick.append(("memory", [str(c) for c in cmd]))
        return real_m(cmd, *a, **k)

    monkeypatch.setattr(rec, "_run_captured", spy_c)
    monkeypatch.setattr(rec, "_run_to_memory", spy_m)
    out = tmp_path / HOSTILE / "small 50%"
    _run(rec, [masters, out, "--mode", "0", "--distance", "1", "--effort", "3",
               "--workers", "1", "--no-preflight", "--on-unknown", "convert",
               "--on-regeneration", "convert", "--on-downgrade", "convert"] + extra)
    made = list(out.glob("*.jxl"))
    assert len(made) == 1, made
    derive = [m for m in magick if "-depth" in m[1]]
    assert derive and all(kind == "memory" and m[-1] == "png:-" for kind, m in derive), derive
    r = rec._run_to_memory(["djxl", str(made[0]), "-", "--output_format=pam"], 60)
    assert r.returncode == 0 and common._pam_array(r.stdout) is not None


def test_a_child_that_writes_in_small_pieces_is_read_at_full_speed():
    """ImageMagick writes its PNG in small pieces: through the system's
    default pipe each piece waited for the reading thread, which sleeps while
    the pipe is empty — the 13 MB PNG of a 2048 px derivative took 2.0 s
    against 1.0 s for a file, and the derivative run was slower than with
    conv.png (found after the review, 2026-11-21). Measured here: 8 MB in
    4 KB pieces, 1.04 s through the default pipe, 0.03 s through 1 MB."""
    child = [sys.executable, "-c",
             "import sys\nw = sys.stdout.buffer\nfor _ in range(2048):\n"
             "    w.write(b'x' * 4096)\n    w.flush()\n"]
    t0 = time.monotonic()
    r = rec._run_to_memory(child, 60)
    took = time.monotonic() - t0
    assert r.returncode == 0 and len(r.stdout) == 8 * 2**20
    assert took < 0.5, f"{took:.2f} s for 8 MB written in 4 KB pieces"


# ---------------------------------------------------------------------------
# #685: the generated sRGB with its fixed date, everywhere
# ---------------------------------------------------------------------------

def _icc_date(icc: bytes):
    return struct.unpack(">6H", icc[24:36])


@needs_tools
def test_to_srgb_writes_the_profile_with_the_fixed_date(tmp_path, monkeypatch):
    from PIL import Image
    src = _tiff(tmp_path / HOSTILE / "TIFF16" / "DSC_0001 (2).tif")
    masters = tmp_path / HOSTILE / "16B_JXL"
    _run(enc, [src.parent, masters, "--mode", "0", "--distance", "0", "--effort", "3",
               "--workers", "1", "--no-preflight"])
    own_temp = tmp_path / "temp [t]"
    own_temp.mkdir()
    stale = own_temp / "jxl_photo_sRGB.icc"
    from PIL import ImageCms
    stale.write_bytes(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    monkeypatch.setattr(tr, "TEMP_DIR", str(own_temp))
    monkeypatch.setattr(tr, "_srgb_icc_cache", None)
    out = tmp_path / HOSTILE / "jpeg 100%"
    _run(tr, [masters, out, "--mode", "0", "--format", "jpeg", "--to-srgb", "--workers", "1"])
    jpgs = list(out.glob("*.jpg"))
    assert len(jpgs) == 1
    with Image.open(jpgs[0]) as im:
        icc = im.info.get("icc_profile")
    assert icc and _icc_date(icc) == common._GENERATED_ICC_DATE
    # The old file is left alone: the profile lives under its content's name.
    assert stale.read_bytes() != common._generated_srgb_icc()
    assert Path(tr._srgb_icc_cache).read_bytes() == common._generated_srgb_icc()
    assert Path(tr._srgb_icc_cache).parent == own_temp


def _stale_profile_folder(tmp_path, monkeypatch):
    from PIL import ImageCms
    own_temp = tmp_path / HOSTILE / "temp [t]"
    own_temp.mkdir(parents=True)
    stale = own_temp / "jxl_photo_sRGB.icc"
    stale.write_bytes(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    monkeypatch.setattr(tr, "TEMP_DIR", str(own_temp))
    monkeypatch.setattr(tr, "_srgb_icc_cache", None)
    return own_temp, stale


@pytest.mark.parametrize("how", ["read-only", "open in another program"])
def test_an_old_profile_that_cannot_be_replaced_never_costs_the_profile(
        tmp_path, monkeypatch, how):
    """Review M1: an older version's jxl_photo_sRGB.icc that cannot be
    replaced (read-only, open without FILE_SHARE_DELETE, another account's)
    made _get_srgb_icc_path give None for the whole run, and --to-srgb fell
    back to -colorspace sRGB: wrong colours from a ProPhoto master."""
    own_temp, stale = _stale_profile_folder(tmp_path, monkeypatch)
    old = stale.read_bytes()
    holder = None
    if how == "read-only":
        os.chmod(stale, stat.S_IREAD)
    else:
        holder = open(stale, "rb")          # Python opens without FILE_SHARE_DELETE
    try:
        path = tr._get_srgb_icc_path()
        assert path, "no sRGB profile: --to-srgb would fall back to -colorspace sRGB"
        assert Path(path).read_bytes() == common._generated_srgb_icc()
        assert stale.read_bytes() == old
    finally:
        if holder is not None:
            holder.close()
        os.chmod(stale, stat.S_IREAD | stat.S_IWRITE)


def test_a_damaged_profile_that_cannot_be_replaced_gets_a_copy_of_this_process(
        tmp_path, monkeypatch):
    own_temp, _stale = _stale_profile_folder(tmp_path, monkeypatch)
    data = common._generated_srgb_icc()
    stem = "jxl_photo_sRGB_" + hashlib.sha256(data).hexdigest()[:16]
    damaged = own_temp / f"{stem}.icc"
    damaged.write_bytes(data[:100])          # a crash mid-write, then made read-only
    os.chmod(damaged, stat.S_IREAD)
    try:
        path = tr._get_srgb_icc_path()
        assert path and Path(path).name == f"{stem}.{os.getpid()}.icc"
        assert Path(path).read_bytes() == data
    finally:
        os.chmod(damaged, stat.S_IREAD | stat.S_IWRITE)


def test_the_decoder_s_srgb_target_has_the_fixed_date():
    icc = dec.load_target_icc("srgb")
    assert icc == common._generated_srgb_icc()
    assert _icc_date(icc) == common._GENERATED_ICC_DATE
