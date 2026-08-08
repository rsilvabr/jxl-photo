#!/usr/bin/env python3
"""Bug #282 — a multi-page split arriving with pages MISSING.

The decode of a partial group produces a perfectly VALID TIFF, just a shorter
one, so no integrity check, round-trip or checksum downstream can tell it is
short — and --delete-source then destroys the JXLs that DID arrive, leaving the
missing page nowhere at all.

Before this, only a group of exactly ONE member that was not page 0 was
detected. A three-page split arriving as pages {0,1} decoded to a two-page TIFF
and had both JXLs deleted.

The fix is a recorded page count (jxlphoto-pages:N), because nothing else can
tell a complete split from a truncated one. In particular a GAP in the page
numbers is not evidence: --thumbnail-mode exclude drops the thumbnail page and
leaves the real pages on their ORIGINAL indices, so the ordinary film-scan
shape [real, thumb, real] archives completely and correctly as pages {0, 2}.
test_thumbnail_gap_is_not_incomplete is the guard on that.

--allow-incomplete-groups is the way out for someone whose page really is gone.
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_tiff_decoder as dec

REPO = Path(__file__).resolve().parent.parent
ENCODER = str(REPO / "jxl_tiff_encoder.py")
DECODER = str(REPO / "jxl_tiff_decoder.py")


def _run(script, *args, cwd):
    return subprocess.run([sys.executable, script, *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=600, cwd=str(cwd), stdin=subprocess.DEVNULL)


def _three_real_pages(path: Path):
    """A scan-shaped TIFF: three real pages, no thumbnail."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tifffile.TiffWriter(str(path)) as tif:
        for v in (1000, 20000, 40000):
            tif.write(np.full((40, 40, 3), v, np.uint16), photometric="rgb")


def _real_thumb_real(path: Path):
    """The ordinary film-scan shape: page 1 is an embedded preview."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tifffile.TiffWriter(str(path)) as tif:
        tif.write(np.full((80, 80, 3), 1000, np.uint16), photometric="rgb")
        tif.write(np.full((20, 20, 3), 200, np.uint8), photometric="rgb",
                  subfiletype=tifffile.FILETYPE.REDUCEDIMAGE)
        tif.write(np.full((60, 60, 3), 40000, np.uint16), photometric="rgb")


def _encode_split(tmp_path, tiff_maker, *extra):
    tiff_maker(tmp_path / "src" / "scan.tif")
    r = _run(ENCODER, "src", "jxl", "--mode", "2", "--distance", "0", "--effort", "1",
             "--multipage-mode", "split", *extra, cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    return sorted((tmp_path / "jxl").glob("*.jxl"))


def _markers(jxl: Path):
    return dec._read_multipage_markers_batch([jxl])[str(jxl)]


def _remaining(tmp_path):
    return sorted((tmp_path / "jxl").glob("*.jxl"))


DECODE = ["jxl", "out", "--mode", "2", "--depth", "16", "--compression", "zip",
          "--workers", "1"]
DELETE = ["--delete-source", "--delete-confirm-off"]


# ── the marker itself ───────────────────────────────────────────────────────

def test_encoder_records_the_page_count_on_every_member(tmp_path):
    jxls = _encode_split(tmp_path, _three_real_pages)
    assert len(jxls) == 3
    for j in jxls:
        assert _markers(j)["pages"] == 3, j.name


def test_a_single_page_tiff_gets_no_group_and_no_count(tmp_path):
    (tmp_path / "src").mkdir()
    tifffile.imwrite(str(tmp_path / "src" / "one.tif"),
                     np.full((40, 40, 3), 500, np.uint16), photometric="rgb")
    r = _run(ENCODER, "src", "jxl", "--mode", "2", "--distance", "0", "--effort", "1",
             cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    m = _markers(tmp_path / "jxl" / "one.jxl")
    assert m["group"] is None and m["pages"] is None


def test_the_count_never_reaches_the_reconstructed_tiff(tmp_path):
    """It describes the JXLs, not the picture — like every other jxlphoto-*."""
    _encode_split(tmp_path, _three_real_pages)
    assert _run(DECODER, *DECODE, cwd=tmp_path).returncode == 0
    r = subprocess.run(["exiftool", "-XMP-dc:Relation", "-s", "-s", "-s",
                        str(tmp_path / "out" / "scan.tif")],
                       capture_output=True, text=True, timeout=60)
    assert "jxlphoto-pages" not in r.stdout


# ── the false positive this must NOT produce ────────────────────────────────

def test_thumbnail_gap_is_not_incomplete(tmp_path):
    """--thumbnail-mode exclude leaves the real pages on their ORIGINAL indices,
    so a complete archive of [real, thumb, real] is pages {0, 2}. Reading that
    hole as a missing page would refuse the commonest film-scan shape there is."""
    jxls = _encode_split(tmp_path, _real_thumb_real, "--thumbnail-mode", "exclude")
    assert len(jxls) == 2
    assert sorted(_markers(j)["page"] for j in jxls) == [0, 2], \
        "fixture no longer reproduces the gap"
    for j in jxls:
        assert _markers(j)["pages"] == 2

    r = _run(DECODER, *DECODE, *DELETE, cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    assert "INCOMPLETE" not in r.stdout, r.stdout
    assert not _remaining(tmp_path), "a complete group was not deleted"
    with tifffile.TiffFile(str(tmp_path / "out" / "scan.tif")) as tif:
        assert len(tif.pages) == 2


# ── the hole it must catch ──────────────────────────────────────────────────

def _lose_page(tmp_path, page):
    jxls = _encode_split(tmp_path, _three_real_pages)
    assert len(jxls) == 3
    next(j for j in jxls if _markers(j)["page"] == page).unlink()


def test_a_truncated_split_is_detected_and_the_sources_kept(tmp_path):
    _lose_page(tmp_path, 1)
    r = _run(DECODER, *DECODE, *DELETE, cwd=tmp_path)
    assert "INCOMPLETE" in r.stdout, r.stdout
    assert "recorded 3 page(s)" in r.stdout
    assert len(_remaining(tmp_path)) == 2, "the sources were deleted"
    # The TIFF is still written: the refusal is about DELETING, not decoding.
    assert (tmp_path / "out" / "scan.tif").exists()


def test_a_missing_tail_page_is_caught_too(tmp_path):
    """Pages {0,1} of a three-page split look exactly like a two-page split.
    This is the case no count-free check can see."""
    _lose_page(tmp_path, 2)
    r = _run(DECODER, *DECODE, *DELETE, cwd=tmp_path)
    assert "INCOMPLETE" in r.stdout, r.stdout
    assert len(_remaining(tmp_path)) == 2


def test_delete_skipped_also_refuses_a_truncated_split(tmp_path):
    """The SKIP path has no "this run wrote it" to fall back on, so it needs
    the same gate."""
    _lose_page(tmp_path, 1)
    assert _run(DECODER, *DECODE, cwd=tmp_path).returncode == 0   # make it a SKIP
    r = _run(DECODER, *DECODE, *DELETE, "--delete-skipped", cwd=tmp_path)
    assert len(_remaining(tmp_path)) == 2, r.stdout


# ── the way out ─────────────────────────────────────────────────────────────

def test_allow_incomplete_groups_lets_the_delete_through(tmp_path):
    """For someone whose page really is gone and who wants the archive
    finished anyway."""
    _lose_page(tmp_path, 1)
    r = _run(DECODER, *DECODE, *DELETE, "--allow-incomplete-groups", cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    # Still names what is missing: the override buys a delete, not silence.
    assert "INCOMPLETE" in r.stdout
    assert not _remaining(tmp_path), "the override did not delete"
    with tifffile.TiffFile(str(tmp_path / "out" / "scan.tif")) as tif:
        assert len(tif.pages) == 2


def test_the_override_warns_about_what_it_is_doing(tmp_path):
    _lose_page(tmp_path, 1)
    r = _run(DECODER, *DECODE, *DELETE, "--allow-incomplete-groups", cwd=tmp_path)
    assert "cannot be recovered" in r.stdout


def test_the_override_is_inert_without_delete_source(tmp_path):
    _lose_page(tmp_path, 1)
    r = _run(DECODER, *DECODE, "--allow-incomplete-groups", cwd=tmp_path)
    assert "no effect without --delete-source" in r.stdout
    assert len(_remaining(tmp_path)) == 2


# ── archives written before the marker existed ──────────────────────────────

def _strip_count(jxl: Path, n: int):
    subprocess.run(["exiftool", "-overwrite_original",
                    "-XMP-dc:Relation-=jxlphoto-pages:" + str(n), str(jxl)],
                   capture_output=True, timeout=60)


def test_a_legacy_split_with_a_gap_is_not_refused(tmp_path):
    """No count means "cannot tell", not "incomplete". Refusing every
    multi-page archive anyone already owns is bug #271's dead end."""
    jxls = _encode_split(tmp_path, _three_real_pages)
    for j in jxls:
        _strip_count(j, 3)
    assert all(_markers(j)["pages"] is None for j in jxls)
    next(j for j in jxls if _markers(j)["page"] == 1).unlink()

    r = _run(DECODER, *DECODE, *DELETE, cwd=tmp_path)
    assert "INCOMPLETE" not in r.stdout, r.stdout
    assert not _remaining(tmp_path)


def test_a_legacy_lone_fragment_is_still_caught(tmp_path):
    """The one thing provable without a count: a group of one that is not
    page 0 cannot be whole."""
    jxls = _encode_split(tmp_path, _three_real_pages)
    for j in jxls:
        _strip_count(j, 3)
    for j in jxls:
        if _markers(j)["page"] != 1:
            j.unlink()

    r = _run(DECODER, *DECODE, *DELETE, cwd=tmp_path)
    assert "INCOMPLETE" in r.stdout, r.stdout
    assert "records no page count" in r.stdout
    assert len(_remaining(tmp_path)) == 1


def test_members_disagreeing_about_the_size_fail_closed(tmp_path):
    jxls = _encode_split(tmp_path, _three_real_pages)
    _strip_count(jxls[0], 3)
    subprocess.run(["exiftool", "-overwrite_original",
                    "-XMP-dc:Relation+=jxlphoto-pages:5", str(jxls[0])],
                   capture_output=True, timeout=60)
    r = _run(DECODER, *DECODE, *DELETE, cwd=tmp_path)
    assert "disagree about the size" in r.stdout, r.stdout
    assert len(_remaining(tmp_path)) == 3
