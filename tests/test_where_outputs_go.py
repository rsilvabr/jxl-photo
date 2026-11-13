"""Where each output goes: the wrapper asks the scripts (refactor C1, the
"where" half) — #655–#660.

The wrapper's file count (_count_origin_files) and its manifest collision
scan (_manifest_output_collisions) used to compute the destinations
themselves, copying the scripts' rules. They now ask each script which files a
run takes (_planned_sources) and where each output goes (_planned_output).
These are the cases where the copy disagreed with the script (#655–#659), and
the script bug the comparison found (#660: the decoder and the transcoder
anchored the export marker of modes 6/7 on the OUTPUT folder). Every test
works in a folder with Japanese, [ ], a space and `100%off %d`, and each one
failed on v2.30.0_beta1.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import jxl_photo as wp  # noqa: E402

HOSTILE = "250118_吾妻山公園 [FINAL] 100%off %d"
TIF = {".tif", ".tiff"}
JXL = {".jxl"}


@pytest.fixture
def menu(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp, "console", None)
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


@pytest.fixture
def root(tmp_path):
    r = tmp_path / HOSTILE
    r.mkdir()
    return r


def _touch(*paths, content=b"x"):
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


def _norm(p):
    return os.path.normcase(os.path.abspath(str(p)))


# ── #655 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("conversion", ["transcode_lossless", "convert_lossy"])
def test_jpeg_count_leaves_out_the_transcoders_own_output_folders(menu, root, conversion):
    """A recursive JPEG -> JXL run skips the JPEGs the transcoder itself
    recovered (recovered_jpeg, jpeg_recovered, converted): the delete
    preview counted them (4 for a run that touches 1)."""
    shoot = root / "shoot [a]"
    _touch(shoot / "DSC 0001 (2).jpg", shoot / "recovered_jpeg" / "r.jpg",
           shoot / "jpeg_recovered" / "j.jpg", shoot / "converted" / "c.jpg")
    wf = {"origin_format": "jpeg", "dest_format": "jxl", "input_dir": str(shoot),
          "conversion_type": conversion}
    assert menu._count_origin_files(wf, 2) == 1
    assert menu._count_origin_files(wf, 0) == 1        # flat: the root file only


# ── #656 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("origin,dest,conversion,name", [
    ("tiff", "jxl", None, "DSC 0001 (2).tif"),
    ("jpeg", "jxl", "transcode_lossless", "DSC 0001 (2).jpg"),
    ("jxl", "jpeg", "jxl_to_jpeg_auto", "DSC 0001 (2).jxl"),
])
def test_count_of_one_file_a_marker_mode_takes_nothing_from(menu, root, origin, dest,
                                                            conversion, name):
    """A single file outside any export marker, in modes 6/7: the encoder's
    finders find nothing in a file and the transcoder leaves it out (and
    refuses a file there anyway) — the preview announced 1."""
    f = root / "loose [b]" / name
    _touch(f)
    wf = {"origin_format": origin, "dest_format": dest, "input_dir": str(f),
          "conversion_type": conversion}
    assert menu._count_origin_files(wf, 6) == 0
    assert menu._count_origin_files(wf, 7) == 0
    assert menu._count_origin_files(wf, 0) == 1        # the run takes it there


def test_count_of_one_file_of_another_format_is_zero(menu, root):
    """A TIFF given to a JXL -> JXL count: none of the origin format (as
    before), not "unknown" because the recompressor refuses the file."""
    f = root / "loose [b]" / "DSC 0001 (2).tif"
    _touch(f)
    wf = {"origin_format": "jxl", "dest_format": "jxl", "input_dir": str(f)}
    assert menu._count_origin_files(wf, 0) == 0


# ── #657 ──────────────────────────────────────────────────────────────────────

def test_a_file_row_writes_into_its_own_folder(menu, root):
    """A row whose Source is a file, mode 0, empty Destination (the loader
    puts the file itself there): the scan recorded the FILE as the output
    folder, so a JXL -> JXL row on that file next to a row on its folder —
    the file recompressed twice, by two child processes — went unseen."""
    folder = root / "shoot [a]"
    one = folder / "DSC 0001 (2).jxl"
    _touch(one, folder / "DSC 0002.jxl")
    entries = [(str(one), str(one), 0), (str(folder), str(folder), 0)]
    sink = {}
    menu._manifest_output_collisions(entries, JXL, origin="jxl", dest="jxl",
                                     export_marker="_EXPORT", cross_sink=sink,
                                     conversion_type="jxl_recompress")
    assert sink[0] == {_norm(folder)}
    chained = menu._manifest_output_source_collisions(entries, sink)
    assert any(_norm(src_b) == _norm(folder) for _a, src_b, _o in chained), chained


def test_a_file_row_collides_with_a_row_writing_into_its_folder(menu, root):
    """The same file row (TIFF -> JXL in place) next to a row that writes a
    same-named JXL into that folder: two photos, one output name."""
    here, there = root / "shoot [a]", root / "other shoot"
    _touch(here / "DSC 0001 (2).tif", there / "DSC 0001 (2).tif")
    one = here / "DSC 0001 (2).tif"
    entries = [(str(one), str(one), 0), (str(there), str(here), 0)]
    cols = menu._manifest_output_collisions(entries, TIF, origin="tiff", dest="jxl",
                                            export_marker="_EXPORT",
                                            conversion_type="jxl_tiff_encoder")
    assert len(cols) == 1 and _norm(cols[0][2]) == _norm(here), cols


# ── #658 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("origin,dest,ext,conversion", [
    ("tiff", "jxl", ".tif", "jxl_tiff_encoder"),
    ("jxl", "tiff", ".jxl", "jxl_tiff_decoder"),
    ("jpeg", "jxl", ".jpg", "transcode_lossless"),
    ("jpeg", "jxl", ".jpg", "convert_lossy"),
])
def test_no_phantom_rename_where_the_child_never_renames(menu, root, origin, dest, ext,
                                                         conversion):
    """A rename left in an old preset reaches only the recompressor and the
    transcoder's lossy decode (build_child_cmd): the scan renamed every
    direction's outputs and refused a manifest that collides with nothing."""
    out = root / "out [x]"
    _touch(root / "sub A" / f"IMG_ProPhoto{ext}", root / "sub B" / f"IMG_sRGB{ext}")
    entries = [(str(root / "sub A"), str(out), 2), (str(root / "sub B"), str(out), 2)]
    exts = wp.FolderAnalyzer(root, origin, dest)._get_extensions(origin)
    sink = {}
    cols = menu._manifest_output_collisions(entries, exts, origin=origin, dest=dest,
                                            export_marker="_EXPORT", cross_sink=sink,
                                            rename_from="ProPhoto", rename_to="sRGB",
                                            conversion_type=conversion)
    assert cols == []
    # Both rows were scanned (a row the child would refuse is skipped, which
    # would make "no collision" true for the wrong reason).
    assert sink == {0: {_norm(out)}, 1: {_norm(out)}}


def test_the_recompressors_rename_still_collides(menu, root):
    """The control: the recompressor does get the rename, so the same rows
    land on one name."""
    out = root / "out [x]"
    _touch(root / "sub A" / "IMG_ProPhoto.jxl", root / "sub B" / "IMG_sRGB.jxl")
    entries = [(str(root / "sub A"), str(out), 2), (str(root / "sub B"), str(out), 2)]
    cols = menu._manifest_output_collisions(entries, JXL, origin="jxl", dest="jxl",
                                            export_marker="_EXPORT",
                                            rename_from="ProPhoto", rename_to="sRGB",
                                            conversion_type="jxl_recompress")
    assert len(cols) == 1


# ── #659 ──────────────────────────────────────────────────────────────────────

def test_a_file_row_of_another_format_writes_nothing(menu, root):
    """A TIFF given as the Source of a JXL -> JXL row: the recompressor
    refuses it (not a JXL), so it writes nothing — the scan recorded an
    output for it and refused the manifest over a collision with a real
    JXL of the same name."""
    out = root / "out [x]"
    tif = root / "a [tif]" / "DSC 0001 (2).tif"
    _touch(tif, root / "b" / "DSC 0001 (2).jxl")
    entries = [(str(tif), str(out), 0), (str(root / "b"), str(out), 0)]
    sink = {}
    cols = menu._manifest_output_collisions(entries, JXL, origin="jxl", dest="jxl",
                                            export_marker="_EXPORT", cross_sink=sink,
                                            conversion_type="jxl_recompress")
    assert cols == []
    assert not sink.get(0)


# ── #660 ──────────────────────────────────────────────────────────────────────

def _dry_run_lines(script, args, env_dir):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", JXLPHOTO_SETTINGS="",
               JXLPHOTO_LOG_DIR=str(env_dir))
    r = subprocess.run([sys.executable, str(REPO / script)] + [str(a) for a in args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env, stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return [ln for ln in (r.stdout + r.stderr).splitlines() if " DRY | " in ln]


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
def test_decoder_anchors_the_marker_on_the_input_not_the_output(root, tmp_path):
    """Mode 6 with an output folder given (a manifest row with an explicit
    Destination): the decoder anchored _EXPORT on the OUTPUT folder, so a
    'Lightroom_Export' above the library won and the TIFF went to
    Lightroom_Export/16B_TIFF/lib/2025_EXPORT/16B_JXL/."""
    lib = root / "Lightroom_Export" / "lib [2025]"
    _touch(lib / "2025_EXPORT" / "16B_JXL" / "DSC 0001 (2).jxl",
           content=b"\x00\x00\x00\x0cJXL \r\n\x87\n")
    lines = _dry_run_lines("jxl_tiff_decoder.py",
                           [lib, root / "out [x]", "--mode", "6", "--dry-run"],
                           tmp_path / "logs")
    want = str(lib / "2025_EXPORT" / "16B_TIFF" / "DSC 0001 (2).tif")
    assert any(want in ln for ln in lines), lines


@pytest.mark.parametrize("flags,src_name,out_folder", [
    (["--force-transcode"], "DSC 0001 (2).jpg", "JXL_jpeg"),
    (["--force-convert", "--from-jpeg"], "DSC 0001 (2).jpg", "JXL_jpeg"),
])
def test_transcoder_anchors_the_marker_on_the_input_not_the_output(root, tmp_path, flags,
                                                                   src_name, out_folder):
    """The same for the transcoder (its lossless and convert routes)."""
    lib = root / "Lightroom_Export" / "lib [2025]"
    _touch(lib / "2025_EXPORT" / "JPG" / src_name,
           content=b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9")
    lines = _dry_run_lines("jxl_jpeg_transcoder.py",
                           [lib, root / "out [x]", "--mode", "6", "--dry-run"] + flags,
                           tmp_path / "logs")
    want = str(lib / "2025_EXPORT" / out_folder / "DSC 0001 (2).jxl")
    assert any(want in ln for ln in lines), lines


_SIG = b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00\x00\x00\x14ftypjxl \x00\x00\x00\x00jxl "
JBRD_JXL = _SIG + b"\x00\x00\x00\x08jbrd" + b"\x00\x00\x00\x0ajxlc\xff\x0a"
PLAIN_JXL = _SIG + b"\x00\x00\x00\x0ajxlc\xff\x0a"


@pytest.mark.parametrize("extra", [
    ["--mode", "1"],
    ["--mode", "1", "--rename-from", "IMG_A", "--rename-to", "IMG_Z"],
    ["--mode", "3", "--format", "png", "--rename-from", "IMG_A", "--rename-to", "IMG_Z"],
    ["--mode", "2", "OUT", "--format", "jpeg", "--rename-from", "IMG_A", "--rename-to", "IMG_Z"],
])
def test_the_transcoders_answer_is_its_runs_plan(root, tmp_path, extra):
    """The wrapper asks the transcoder's _planned_output without knowing a
    JXL's route (an auto run decides by its jbrd box): the answer must be
    the path the run itself plans, jbrd recovery (original name, always a
    JPEG) and lossy convert (renamed, the requested format) alike."""
    sys.path.insert(0, str(REPO))
    import jxl_jpeg_transcoder as tr
    src = root / "in [a]"
    _touch(src / "IMG_A one [jbrd].jxl", content=JBRD_JXL)
    _touch(src / "IMG_A two plain.jxl", content=PLAIN_JXL)
    out = root / "out [x]"
    argv = [str(src)] + [str(out) if a == "OUT" else a for a in extra] + ["--from-jxl"]
    lines = _dry_run_lines("jxl_jpeg_transcoder.py", argv + ["--dry-run"], tmp_path / "logs")
    planned = {}
    for ln in lines:
        name, _sep, dest = ln.split(" DRY | ", 1)[1].partition(" -> ")
        planned[name.strip()] = dest.split(" [DERIVE")[0].strip()
    assert len(planned) == 2, lines
    ns = tr.build_parser().parse_args(argv)
    for f in sorted(src.iterdir()):
        assert str(tr._planned_output(f, ns)) == planned[f.name], (f.name, planned)


def test_the_scan_sees_the_decoders_place(menu, root):
    """The wrapper's scan answers what the decoder does: an explicit
    Destination in mode 6 keeps the outputs below the library's own marker."""
    lib = root / "Lightroom_Export" / "lib [2025]"
    _touch(lib / "2025_EXPORT" / "16B_JXL" / "DSC 0001 (2).jxl")
    sink = {}
    menu._manifest_output_collisions([(str(lib), str(root / "out [x]"), 6)], JXL,
                                     origin="jxl", dest="tiff", export_marker="_EXPORT",
                                     cross_sink=sink, conversion_type="jxl_tiff_decoder")
    assert sink[0] == {_norm(lib / "2025_EXPORT" / "16B_TIFF")}
