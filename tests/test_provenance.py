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


def test_content_id_follows_the_image_not_the_name():
    img = np.full((8, 8, 3), 1234, np.uint16)
    assert enc._page_content_id(img) == enc._page_content_id(img.copy())
    assert enc._page_content_id(img) != enc._page_content_id(
        np.full((8, 8, 3), 1235, np.uint16))


def test_content_id_agrees_across_the_2d_3d_normalisation():
    """convert_one hashes before adding the trailing axis; the check hashes
    what tifffile returns. Both must land on the same id."""
    flat = np.full((8, 8), 999, np.uint16)
    assert enc._page_content_id(flat) == enc._page_content_id(flat[:, :, np.newaxis])


def test_8bit_sources_hash_as_their_promoted_form():
    """The encoder promotes 8-bit to 16-bit before encoding, so the check must
    promote too or every 8-bit source would look like a different image."""
    eight = np.full((8, 8, 3), 200, np.uint8)
    promoted = eight.astype(np.uint16) * 257
    assert enc._page_content_id(promoted) == enc._page_content_id(
        enc._canon_for_compare(eight))
