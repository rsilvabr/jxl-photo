#!/usr/bin/env python3
"""Regressions for the decoder low-severity batch:

1. copy_metadata never checked exiftool's exit code — a failed metadata copy
   (corrupt tag, write failure) was silently dropped, the TIFF passed the
   pixel gate, and --delete-source removed the JXL holding the only copy of
   that metadata.
2. A group containing ONLY a thumbnail page kept strategy="unknown", and
   "unknown" != 'none': under --none it got the full XMP/IPTC copy, provenance
   markers and a JPEG preview that None mode explicitly forbids.
3. A duplicate (page, thumb) entry demoted to standalone kept is_thumb=True,
   so its single-page output TIFF's primary image was tagged subfiletype=1
   (reduced-resolution), which some readers hide.
4. _counter["done"] was never reset between runs in the same process, so the
   second run's progress started at [N+1/total].
5. process_group's `mode` parameter was dead (the delete gate is
   mode-independent) but every caller still passed it.
6. The script-set TEMP2_DIR staging path was never validated; an invalid one
   crashed mid-run at staging_dir.mkdir with a raw traceback.
"""

import inspect
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_tiff_decoder as dec


class _FakeRun:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture(autouse=True)
def _reset_globals():
    dec._reset_abort()
    yield
    dec._reset_abort()
    dec.TEMP2_DIR = None
    dec.DELETE_SOURCE = False
    dec.OVERWRITE = "smart"
    dec.ADD_JPEG_PREVIEW = True


def _jxl_stub(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)


def _no_markers(jxls):
    return {str(j): {"group": None, "inherited": False, "subfiletype": 0,
                     "grayscale": False, "depth": None, "page": None,
                     "pages": None, "thumb": False, "srcsum": None} for j in jxls}


# ---------------------------------------------------------------------------
# 1. A failed metadata copy must block deletion, not ride through silently
# ---------------------------------------------------------------------------

def test_copy_metadata_reports_a_nonzero_exiftool_exit(monkeypatch, tmp_path):
    """rc != 0 is how exiftool reports a corrupt tag / failed write; it does
    not raise, so an unchecked call dropped the metadata silently."""
    tif = tmp_path / "a.tif"
    tif.write_bytes(b"\x00")

    def fake_argfile(args_lines, timeout=60):
        if "-tagsfromfile" in [str(a) for a in args_lines] and \
                "-exif:all" in [str(a) for a in args_lines]:
            return _FakeRun(stderr="Error: corrupt tag", returncode=1)
        return _FakeRun()

    monkeypatch.setattr(dec, "_run_exiftool_argfile", fake_argfile)
    assert dec.copy_metadata(tmp_path / "a.jxl", tif, tmp_path) is False


def test_copy_metadata_success_still_returns_true(monkeypatch, tmp_path):
    tif = tmp_path / "a.tif"
    tif.write_bytes(b"\x00")
    monkeypatch.setattr(dec, "_run_exiftool_argfile",
                        lambda *a, **k: _FakeRun())
    assert dec.copy_metadata(tmp_path / "a.jxl", tif, tmp_path) is True


def test_metadata_copy_failure_is_an_error_and_keeps_the_tiff(monkeypatch, tmp_path):
    """The pixels are fine — the TIFF must be kept — but the group reports a
    real error, which is what the delete gate fails closed on."""
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    out = tmp_path / "photo.tif"

    dec.setup_logger()
    monkeypatch.setattr(dec, "OVERWRITE", True)
    monkeypatch.setattr(dec, "ADD_JPEG_PREVIEW", False)
    monkeypatch.setattr(dec, "decode_jxl_to_numpy",
                        lambda *a, **k: (np.zeros((8, 8, 3), dtype=np.uint16),
                                         None, "x", "roundtrip"))
    monkeypatch.setattr(dec, "copy_metadata", lambda *a, **k: False)
    monkeypatch.setattr(dec, "cleanup_xmp_icc", lambda *a, **k: None)

    main, status, reason = dec.convert_multipage_jxl_group(
        src, [(src, 0, False, False, 0, False, None)], out, out)

    assert status == "error", "a metadata-copy failure must surface as an error"
    assert out.exists(), "the pixel-valid TIFF must be kept"
    with tifffile.TiffFile(str(out)) as t:
        assert len(t.pages) == 1


def test_metadata_copy_failure_blocks_delete(monkeypatch, tmp_path):
    """End to end through process_group: the source JXL holds the only copy of
    the lost metadata, so --delete-source must refuse it."""
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    final = tmp_path / "out" / "photo.tif"

    dec.setup_logger()
    monkeypatch.setattr(dec, "DELETE_SOURCE", True)
    monkeypatch.setattr(dec, "DELETE_CONFIRM", False)
    monkeypatch.setattr(dec, "TEMP2_DIR", None)
    monkeypatch.setattr(dec, "OVERWRITE", True)
    monkeypatch.setattr(dec, "ADD_JPEG_PREVIEW", False)
    monkeypatch.setattr(dec, "decode_jxl_to_numpy",
                        lambda *a, **k: (np.zeros((8, 8, 3), dtype=np.uint16),
                                         None, "x", "roundtrip"))
    monkeypatch.setattr(dec, "copy_metadata", lambda *a, **k: False)
    monkeypatch.setattr(dec, "cleanup_xmp_icc", lambda *a, **k: None)

    task = {"type": "multi", "main_jxl": src,
            "entries": [(src, 0, False, False, 0, False, None)],
            "ignored_thumbs": [], "final_tiff": final}
    # The pre-fix signature still carries the dead `mode` parameter; pass it
    # when present so THIS test fails on the delete, not on a TypeError.
    if "mode" in inspect.signature(dec.process_group).parameters:
        results = dec.process_group([task], 1, 0)
    else:
        results = dec.process_group([task], 1)

    assert results[0][1] == "error", "a metadata-copy failure must surface as an error"
    assert src.exists(), "metadata copy failed but the only copy was deleted"
    assert final.exists(), "the pixel-valid TIFF must be kept"


# ---------------------------------------------------------------------------
# 2. A thumb-only group must honour the selected mode (None = EXIF only)
# ---------------------------------------------------------------------------

def test_thumb_only_group_respects_none_mode(monkeypatch, tmp_path):
    """strategy was only adopted from pages with `not is_thumb`; a thumb-only
    group kept "unknown", which is != 'none' — full XMP/IPTC copy, provenance
    markers and a JPEG preview where None mode promises none of those."""
    src = tmp_path / "t.jxl"
    _jxl_stub(src)
    out = tmp_path / "t.tif"

    dec.setup_logger()
    monkeypatch.setattr(dec, "OVERWRITE", True)
    monkeypatch.setattr(dec, "ADD_JPEG_PREVIEW", True)
    previews, full_copies = [], []
    monkeypatch.setattr(dec, "add_jpeg_preview", lambda *a, **k: previews.append(1))
    monkeypatch.setattr(dec, "copy_metadata", lambda *a, **k: full_copies.append(1))
    monkeypatch.setattr(dec, "cleanup_xmp_icc", lambda *a, **k: None)
    monkeypatch.setattr(dec, "_run_exiftool_argfile", lambda *a, **k: _FakeRun())
    monkeypatch.setattr(dec, "decode_jxl_to_numpy",
                        lambda *a, **k: (np.zeros((8, 8, 3), dtype=np.uint16),
                                         None, "x", "none"))

    main, status, _ = dec.convert_multipage_jxl_group(
        src, [(src, 0, True, False, 0, False, None)], out, out)

    assert status == "ok"
    assert previews == [], "a None-mode thumb-only TIFF got a JPEG preview"
    assert full_copies == [], "a None-mode thumb-only TIFF got the full XMP/IPTC copy"


# ---------------------------------------------------------------------------
# 3. A duplicate thumbnail demoted to standalone is a full page, not a thumb
# ---------------------------------------------------------------------------

def test_demoted_duplicate_thumbnail_loses_its_thumb_role(monkeypatch, tmp_path):
    """Construct the shape directly: a group of (page0, thumb page1) plus a
    SECOND marker-carrying copy of the thumb. The copy is demoted to
    standalone; keeping is_thumb would tag its primary image subfiletype=1."""
    a = tmp_path / "scan.jxl"
    t1 = tmp_path / "scan_page1.jxl"
    t2 = tmp_path / "scan_page1_copy.jxl"
    for f in (a, t1, t2):
        f.write_bytes(b"\x00")

    def _info(page, thumb, subfiletype):
        return {"group": "g1", "inherited": False, "subfiletype": subfiletype,
                "grayscale": False, "depth": None, "page": page,
                "pages": None, "thumb": thumb, "srcsum": None}

    markers = {str(a): _info(0, False, 0),
               str(t1): _info(1, True, 1),
               str(t2): _info(1, True, 1)}
    monkeypatch.setattr(dec, "_read_multipage_markers_batch", lambda jxls: markers)

    groups = dec.collect_multipage_groups([a, t1, t2])
    sizes = sorted(len(v) for v in groups.values())
    assert sizes == [1, 2], f"expected [1 standalone, 2 group], got {sizes}"

    standalone_entry = next(v[0] for v in groups.values() if len(v) == 1)
    assert standalone_entry[0] == t2, "the later-sorted duplicate must be the demoted one"
    assert standalone_entry[2] is False, \
        "demoted to standalone but still flagged as a thumbnail (subfiletype=1)"
    assert standalone_entry[4] == 0, \
        "the thumbnail's subfiletype=1 rode along onto the primary page"


# ---------------------------------------------------------------------------
# 4. _counter["done"] resets with _counter["total"] between in-process runs
# ---------------------------------------------------------------------------

def _run_main_once(monkeypatch, src_dir):
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_decoder.py", str(src_dir), "--mode", "3"])
    dec.main()


def test_progress_counter_resets_between_runs(monkeypatch, tmp_path):
    """Two main() runs in one process: the second must count from 1, not
    continue the first run's `done`."""
    for d in ("run1", "run2"):
        _jxl_stub(tmp_path / d / "a.jxl")

    monkeypatch.setattr(dec, "_check_external_tools", lambda dry_run=False: None)
    monkeypatch.setattr(dec, "_warn_if_libjxl_too_old", lambda *a, **k: None)
    monkeypatch.setattr(dec, "_read_multipage_markers_batch", _no_markers)

    def fake_process_group(tasks, workers, *a, **k):
        for t in tasks:
            dec.next_count()
        return [(str(t["main_jxl"]), "ok", str(t["final_tiff"])) for t in tasks]

    monkeypatch.setattr(dec, "process_group", fake_process_group)

    _run_main_once(monkeypatch, tmp_path / "run1")
    assert dec._counter["done"] == 1, "fixture broken: run 1 did not count its file"
    _run_main_once(monkeypatch, tmp_path / "run2")
    assert dec._counter["done"] == 1, \
        f"run 2 inherited run 1's progress (done={dec._counter['done']})"


# ---------------------------------------------------------------------------
# 5. process_group's dead `mode` parameter is gone
# ---------------------------------------------------------------------------

def test_process_group_has_no_dead_mode_parameter():
    """The delete gate is mode-independent and nothing else referenced it."""
    assert "mode" not in inspect.signature(dec.process_group).parameters


# ---------------------------------------------------------------------------
# 6. A script-set TEMP2_DIR is validated up front, like every other path
# ---------------------------------------------------------------------------

def test_script_set_temp2_dir_is_validated_up_front(monkeypatch, tmp_path, capsys):
    """Only --staging was checked; a bad TEMP2_DIR in the script settings
    crashed mid-run at staging_dir.mkdir with a raw traceback."""
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"x")   # a FILE: mkdir below it must fail
    src_dir = tmp_path / "in"
    _jxl_stub(src_dir / "a.jxl")

    monkeypatch.setattr(dec, "TEMP2_DIR", str(blocker / "stg"))
    monkeypatch.setattr(dec, "_check_external_tools", lambda dry_run=False: None)
    monkeypatch.setattr(dec, "_warn_if_libjxl_too_old", lambda *a, **k: None)
    monkeypatch.setattr(dec, "_read_multipage_markers_batch", _no_markers)
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_decoder.py", str(src_dir), "--mode", "3"])

    with pytest.raises(SystemExit) as exit_info:
        dec.main()
    assert exit_info.value.code == 2
    assert "TEMP2_DIR" in capsys.readouterr().err
