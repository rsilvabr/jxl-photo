#!/usr/bin/env python3
"""Cross-RUN output collisions in the folder-collapsing modes (bug #268).

_abort_on_duplicate_outputs only sees collisions WITHIN one run. In modes
2/4/5/6/7 the output path drops folder structure, so a file added later in a
different folder resolves to the SAME output. With --delete-source that
destroyed the earlier photo outright: its source was deleted by the first run
and its archive overwritten by the second, and the log said "1 overwrites".

The fix records WHICH source made each output (`jxlphoto-src:` = location,
`jxlphoto-srcsum:` = image) and refuses to overwrite-and-delete when neither
matches. --provenance picks which proof is required:

  path    (default) the recorded location must match. Free. Survives
          re-exporting a file in place, not moving its folder.
  content also accepts a matching image, so it survives moved folders, at the
          cost of reading each source. A SUPERSET of path on purpose: content
          alone would refuse a legitimately re-edited file.
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent
ENCODER = str(REPO / "jxl_tiff_encoder.py")


def _tiff(path: Path, value: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.full((32, 32, 3), value, np.uint16),
                     photometric="rgb")


def _run(*args, cwd):
    return subprocess.run([sys.executable, ENCODER, *args],
                          capture_output=True, text=True, timeout=600,
                          cwd=str(cwd), stdin=subprocess.DEVNULL)


ARCHIVE = ["--mode", "5", "--distance", "0", "--delete-source",
           "--delete-confirm-off"]


def test_a_second_folder_cannot_overwrite_the_first_archive(tmp_path):
    """The reproduction: A archived and deleted, then B (different photo, same
    stem) added. Pre-fix B overwrote A's archive and was deleted too, so A's
    photo existed nowhere."""
    _tiff(tmp_path / "root" / "A" / "foto.tif", 1000)
    r = _run("root", *ARCHIVE, cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    assert not (tmp_path / "root" / "A" / "foto.tif").exists()

    _tiff(tmp_path / "root" / "B" / "foto.tif", 60000)
    r = _run("root", *ARCHIVE, "--delete-skipped", cwd=tmp_path)

    assert "REFUSING" in r.stdout
    assert (tmp_path / "root" / "B" / "foto.tif").exists(), "B was deleted"
    # and A's archive is untouched
    out = tmp_path / "root" / "JXL_16bits" / "foto.jxl"
    png = tmp_path / "chk.png"
    subprocess.run(["djxl", str(out), str(png)], capture_output=True, timeout=300)
    import imagecodecs
    assert imagecodecs.png_decode(png.read_bytes()).flat[0] == 1000, (
        "A's archive was overwritten by B")


def test_re_exporting_in_place_is_still_allowed(tmp_path):
    """The normal sync workflow: same path, new pixels. Must NOT be refused —
    content-only matching would have broken exactly this."""
    src = tmp_path / "root" / "A" / "foto.tif"
    _tiff(src, 1000)
    assert _run("root", *ARCHIVE, cwd=tmp_path).returncode == 0

    _tiff(src, 1500)                      # re-edited and re-exported in place
    r = _run("root", *ARCHIVE, cwd=tmp_path)
    assert "REFUSING" not in r.stdout, r.stdout
    assert not src.exists(), "a legitimate re-export was not processed"


def test_moved_folder_is_refused_under_path_and_accepted_under_content(tmp_path):
    src = tmp_path / "root" / "A" / "foto.tif"
    _tiff(src, 1000)
    assert _run("root", *ARCHIVE, cwd=tmp_path).returncode == 0

    # same image, different folder
    moved = tmp_path / "root" / "C" / "foto.tif"
    _tiff(moved, 1000)

    r = _run("root", *ARCHIVE, "--delete-skipped", cwd=tmp_path)
    assert "REFUSING" in r.stdout
    assert "--provenance content" in r.stdout, "the way out must be named"
    assert moved.exists()

    r = _run("root", *ARCHIVE, "--delete-skipped", "--provenance", "content",
             cwd=tmp_path)
    assert "REFUSING" not in r.stdout, r.stdout
    assert not moved.exists(), "content matching did not recognise the moved file"


def test_non_collapsing_modes_are_untouched(tmp_path):
    """Modes 0/1/3/8 derive the output from the source's own folder, so no
    check runs and nothing changes."""
    src = tmp_path / "root" / "A" / "foto.tif"
    _tiff(src, 1000)
    r = _run("root", "--mode", "8", "--distance", "0", "--delete-source",
             "--delete-confirm-off", cwd=tmp_path)
    assert "Provenance:" not in r.stdout
    assert not src.exists()


def test_no_check_without_delete_source(tmp_path):
    """Without deletion an overwrite is recoverable — the source is still
    there — so the check must not slow down or block an ordinary run."""
    _tiff(tmp_path / "root" / "A" / "foto.tif", 1000)
    assert _run("root", "--mode", "5", "--distance", "0", cwd=tmp_path).returncode == 0
    _tiff(tmp_path / "root" / "B" / "foto.tif", 60000)
    r = _run("root", "--mode", "5", "--distance", "0", cwd=tmp_path)
    assert "Provenance:" not in r.stdout
    assert "REFUSING" not in r.stdout


# --- the id helpers --------------------------------------------------------

def test_path_id_is_stable_and_case_normalised(tmp_path):
    a = enc._source_path_id(tmp_path / "A" / "foto.tif")
    assert a == enc._source_path_id(str(tmp_path / "A" / "foto.tif"))
    assert a != enc._source_path_id(tmp_path / "B" / "foto.tif")


def test_content_id_follows_the_bytes(tmp_path):
    a, b, c = tmp_path / "a.bin", tmp_path / "b.bin", tmp_path / "c.bin"
    a.write_bytes(b"same"); b.write_bytes(b"same"); c.write_bytes(b"other")
    assert enc._file_content_id(a) == enc._file_content_id(b)
    assert enc._file_content_id(a) != enc._file_content_id(c)


def test_content_id_of_a_group_depends_on_order(tmp_path):
    """A decoder group is several JXLs in page order; two groups holding the
    same files in a different order are not the same source."""
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"one"); b.write_bytes(b"two")
    assert enc._file_content_id([a, b]) != enc._file_content_id([b, a])
    assert enc._file_content_id([a]) == enc._file_content_id(a)


def test_content_id_reads_in_blocks(tmp_path):
    """A 700 MB source must never be held in memory to be hashed."""
    import hashlib
    big = tmp_path / "big.bin"
    payload = bytes(range(256)) * 8192          # 2 MB, crosses the 1 MB block
    big.write_bytes(payload)
    expected = hashlib.sha256()
    expected.update(hashlib.sha256(payload).digest())
    assert enc._file_content_id(big) == expected.hexdigest()[:16]


# ---------------------------------------------------------------------------
# The decoder carries the same hazard: JXL -> TIFF in a collapsing mode, with
# --delete-source, could overwrite an earlier run's TIFF and delete the JXLs
# that made it. It records the same markers (source LOCATION and source BYTES;
# bytes rather than decoded pixels so re-checking never costs a full decode).
# ---------------------------------------------------------------------------

import jxl_tiff_decoder as dec

DECODER = str(REPO / "jxl_tiff_decoder.py")


def _rund(*args, cwd):
    return subprocess.run([sys.executable, DECODER, *args],
                          capture_output=True, text=True, timeout=600,
                          cwd=str(cwd), stdin=subprocess.DEVNULL)


def _make_jxl(tmp_path: Path, dest: Path, value: int):
    """A real JXL, produced by the encoder."""
    t = tmp_path / f"tmp_{value}.tif"
    _tiff(t, value)
    r = _run(str(t), "--mode", "0", "--distance", "0", cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    dest.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / f"tmp_{value}.jxl").replace(dest)
    t.unlink()


DEC_ARCHIVE = ["--mode", "5", "--delete-source", "--delete-confirm-off"]


def test_decoder_refuses_an_output_made_by_another_source(tmp_path):
    _make_jxl(tmp_path, tmp_path / "root" / "A" / "foto.jxl", 1000)
    assert _rund("root", *DEC_ARCHIVE, cwd=tmp_path).returncode == 0
    assert not (tmp_path / "root" / "A" / "foto.jxl").exists()

    _make_jxl(tmp_path, tmp_path / "root" / "B" / "foto.jxl", 60000)
    r = _rund("root", *DEC_ARCHIVE, "--delete-skipped", cwd=tmp_path)

    assert "REFUSING" in r.stdout, r.stdout
    assert (tmp_path / "root" / "B" / "foto.jxl").exists(), "B was deleted"
    kept = tifffile.imread(str(tmp_path / "root" / "TIFF_16bits" / "foto.tif"))
    assert kept.flat[0] == 1000, "A's TIFF was overwritten by B"


def test_decoder_allows_redecoding_the_same_source(tmp_path):
    src = tmp_path / "root" / "A" / "foto.jxl"
    _make_jxl(tmp_path, src, 1000)
    assert _rund("root", *DEC_ARCHIVE, cwd=tmp_path).returncode == 0

    _make_jxl(tmp_path, src, 1000)          # same location again
    r = _rund("root", *DEC_ARCHIVE, "--overwrite", cwd=tmp_path)
    assert "REFUSING" not in r.stdout, r.stdout
    assert not src.exists()


def test_decoder_content_matching_survives_a_moved_source(tmp_path):
    _make_jxl(tmp_path, tmp_path / "root" / "A" / "foto.jxl", 1000)
    assert _rund("root", *DEC_ARCHIVE, cwd=tmp_path).returncode == 0

    # the SAME jxl bytes, at a different location
    moved = tmp_path / "root" / "C" / "foto.jxl"
    _make_jxl(tmp_path, moved, 1000)

    r = _rund("root", *DEC_ARCHIVE, "--delete-skipped", cwd=tmp_path)
    assert "REFUSING" in r.stdout
    assert moved.exists()

    r = _rund("root", *DEC_ARCHIVE, "--delete-skipped",
              "--provenance", "content", cwd=tmp_path)
    assert "REFUSING" not in r.stdout, r.stdout
    assert not moved.exists(), "content matching did not recognise the moved source"


def test_decoder_non_collapsing_modes_are_untouched(tmp_path):
    src = tmp_path / "root" / "A" / "foto.jxl"
    _make_jxl(tmp_path, src, 1000)
    r = _rund("root", "--mode", "8", "--delete-source", "--delete-confirm-off",
              cwd=tmp_path)
    assert "Provenance:" not in r.stdout
    assert not src.exists()


def test_decoder_markers_do_not_leak_into_the_tiff(tmp_path):
    """They describe the JXL, not the picture — the reconstructed TIFF must not
    carry them, like every other internal marker."""
    _make_jxl(tmp_path, tmp_path / "root" / "A" / "foto.jxl", 1000)
    assert _rund("root", "--mode", "5", cwd=tmp_path).returncode == 0
    out = tmp_path / "root" / "TIFF_16bits" / "foto.tif"
    marks = dec._read_source_markers_batch([out])[str(out)]
    assert marks["src"], "the decoder recorded no provenance"
    # ...but the human-visible Relation bag must not show internal markers
    r = subprocess.run(["exiftool", "-s", "-s", "-s", "-XMP-dc:Relation", str(out)],
                       capture_output=True, text=True, timeout=60)
    assert "jxlphoto-mpg" not in r.stdout
    assert "jxlphoto-depth" not in r.stdout


def test_decoder_helpers_match_the_encoder(tmp_path):
    """The three scripts must compute identical ids, or an archive written by
    one and checked by another would never match."""
    import jxl_jpeg_transcoder as tr
    p = tmp_path / "x" / "y.tif"
    assert (enc._source_path_id(p) == dec._source_path_id(p)
            == tr._source_path_id(p))
    assert enc._COLLAPSING_MODES == dec._COLLAPSING_MODES == tr._COLLAPSING_MODES
    f = tmp_path / "blob.bin"
    f.write_bytes(b"hello provenance")
    assert dec._file_content_id(f) == tr._file_content_id([f])


# ---------------------------------------------------------------------------
# Transcoder. Same guard, with one constraint the other two do not have: the
# LOSSLESS JXL -> JPEG output must stay byte-identical to the original, so it
# cannot carry a marker. checksums.md5 already holds the original JPEG's hash
# keyed by the JXL, which is a stronger proof anyway.
# ---------------------------------------------------------------------------

import jxl_jpeg_transcoder as tr

TRANSCODER = str(REPO / "jxl_jpeg_transcoder.py")


def _runt(*args, cwd):
    return subprocess.run([sys.executable, TRANSCODER, *args],
                          capture_output=True, text=True, timeout=600,
                          cwd=str(cwd), stdin=subprocess.DEVNULL)


def _jpeg(path: Path, seed: int):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    y, x = np.mgrid[0:96, 0:128]
    a = ((np.sin((x + seed) / 9.0) * .25 + np.cos((y + seed) / 11.0) * .25 + .5) * 255)
    a = a.astype(np.uint8)
    Image.fromarray(np.stack([a, a, a], axis=2)).save(str(path), quality=92)


TR_ARCHIVE = ["--force-transcode", "--mode", "5", "--delete-source",
              "--delete-confirm-off"]


def test_transcoder_refuses_an_output_made_by_another_source(tmp_path):
    _jpeg(tmp_path / "root" / "A" / "foto.jpg", 0)
    assert _runt("root", *TR_ARCHIVE, cwd=tmp_path).returncode == 0
    assert not (tmp_path / "root" / "A" / "foto.jpg").exists()

    _jpeg(tmp_path / "root" / "B" / "foto.jpg", 50)
    r = _runt("root", *TR_ARCHIVE, "--delete-skipped", cwd=tmp_path)
    assert "REFUSING" in r.stdout, r.stdout
    assert (tmp_path / "root" / "B" / "foto.jpg").exists(), "B was deleted"


def test_transcoder_allows_reconverting_the_same_source(tmp_path):
    src = tmp_path / "root" / "A" / "foto.jpg"
    _jpeg(src, 0)
    assert _runt("root", *TR_ARCHIVE, cwd=tmp_path).returncode == 0
    _jpeg(src, 0)                                   # same location again
    r = _runt("root", *TR_ARCHIVE, "--overwrite", cwd=tmp_path)
    assert "REFUSING" not in r.stdout, r.stdout
    assert not src.exists()


def test_transcoder_content_matching_survives_a_moved_source(tmp_path):
    a = tmp_path / "root" / "A" / "foto.jpg"
    _jpeg(a, 0)
    keep = tmp_path / "keep.jpg"
    keep.write_bytes(a.read_bytes())
    assert _runt("root", *TR_ARCHIVE, cwd=tmp_path).returncode == 0

    moved = tmp_path / "root" / "C" / "foto.jpg"
    moved.parent.mkdir(parents=True)
    moved.write_bytes(keep.read_bytes())            # identical bytes, new place

    r = _runt("root", *TR_ARCHIVE, "--delete-skipped", cwd=tmp_path)
    assert "REFUSING" in r.stdout
    assert moved.exists()

    r = _runt("root", *TR_ARCHIVE, "--delete-skipped", "--provenance", "content",
              cwd=tmp_path)
    assert "REFUSING" not in r.stdout, r.stdout
    assert not moved.exists()


def test_lossless_jpeg_recovery_stays_byte_identical(tmp_path):
    """The markers must not touch the one output that has to match the original
    byte for byte — the whole promise of the lossless path."""
    src = tmp_path / "orig.jpg"
    _jpeg(src, 3)
    original = src.read_bytes()

    assert _runt("orig.jpg", "--force-transcode", cwd=tmp_path).returncode == 0
    jxl = tmp_path / "orig.jxl"
    assert tr.has_jbrd_box(jxl), "jbrd lost"
    marks = tr._read_source_markers_batch([jxl])[str(jxl)]
    assert marks["src"], "the JXL carries no provenance marker"

    src.unlink()
    r = _runt("orig.jxl", "--force-transcode", "--decode", cwd=tmp_path)
    assert "MD5 PASS" in r.stdout, r.stdout
    assert src.read_bytes() == original, "recovered JPEG is not byte-identical"


def test_transcoder_non_collapsing_modes_are_untouched(tmp_path):
    src = tmp_path / "root" / "A" / "foto.jpg"
    _jpeg(src, 0)
    r = _runt("root", "--force-transcode", "--mode", "8", "--delete-source",
              "--delete-confirm-off", cwd=tmp_path)
    assert "Provenance:" not in r.stdout
    assert not src.exists()
