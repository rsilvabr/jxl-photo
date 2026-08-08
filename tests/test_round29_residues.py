#!/usr/bin/env python3
"""Bugs #291-#296 — residues left by the round-29 fixes themselves.

Every one was introduced by a round-29 commit and found by re-reading it:

  * #291 — the new "--mode 1 ignores the output folder" warning picked the
    subfolder from `auto_decode`, which determine_command returns False for the
    CONVERT command even when converting a JXL. A lossy JXL->JPEG mode-1 run was
    told converted_jxl/ while it writes recovered_jpeg/. It also ignored
    --output-name, which overrides the folder for convert modes 1 and 3.
  * #292 — the new inert-flag warnings hand-wrote `mode == 0 and output` instead
    of asking _run_collapses_structure, the predicate added two commits earlier.
    Mode 0 with an output folder EQUAL to the source collapses nothing, so the
    ad-hoc test warned about --strip/--none where there was nothing to warn
    about and stayed silent about an inert --provenance.
  * #293 — the rewritten staging-checksum block deleted the staging
    checksums.md5 even when a move had failed, taking with it the only copy of
    the hash for the file still sitting in staging.
  * #294 — the --allow-incomplete-groups banner said the affected groups are
    "named above" when they are named below, and the shared tail told the
    disagreeing-markers case to go find a page that may not be missing.
  * #295 — _confirm_lossy_delete_skipped was annotated `-> bool` and documented
    as returning False on cancel, which no path did, so `if not ...: return
    False` at both call sites was dead. Its input() had no EOFError guard, so a
    closed stdin raised instead of declining.
  * #296 — _DEFAULT_INFO lost the 'pages' key added to the marker dicts. Benign
    today only because that one key is read with .get().
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_jpeg_transcoder as tr
import jxl_photo as wp
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent
ENCODER = str(REPO / "jxl_tiff_encoder.py")
DECODER = str(REPO / "jxl_tiff_decoder.py")
TRANSCODER = str(REPO / "jxl_jpeg_transcoder.py")


def _run(script, *args, cwd):
    return subprocess.run([sys.executable, script, *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=600, cwd=str(cwd), stdin=subprocess.DEVNULL)


def _tiff(path: Path, value=1000):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.full((32, 32, 3), value, np.uint16),
                     photometric="rgb")


def _jpeg(path: Path, seed=0):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    y, x = np.mgrid[0:48, 0:48]
    a = ((np.sin((x + seed) / 7.0) * .25 + np.cos((y + seed) / 9.0) * .25 + .5) * 255)
    Image.fromarray(np.stack([a.astype(np.uint8)] * 3, axis=2)).save(str(path), quality=92)


# ── #291: the mode-1 warning must name the folder the run writes to ─────────

def _a_jxl(tmp_path) -> Path:
    _jpeg(tmp_path / "src" / "a.jpg")
    assert _run(TRANSCODER, "src", "--force-transcode", "--mode", "3",
                cwd=tmp_path).returncode == 0
    return next((tmp_path / "src").rglob("*.jxl"))


def test_a_single_jxl_names_recovered_jpeg(tmp_path):
    """determine_command returns auto_decode=False for the convert command even
    when the input is a JXL, so the warning named the encode-side folder."""
    jxl = _a_jxl(tmp_path)
    r = _run(TRANSCODER, str(jxl), "elsewhere", "--force-convert", "--mode", "1",
             cwd=tmp_path)
    # The warning LINE only: the input path itself contains "converted_jxl".
    line = next(l for l in r.stdout.splitlines() if "ignores the output folder" in l)
    assert tr.RECOVERED_JPEG_FOLDER in line, line
    assert tr.CONVERTED_JXL_FOLDER not in line, line


def test_a_folder_convert_admits_the_direction_is_not_known_yet(tmp_path):
    """cmd_convert scans FIRST and falls back to from_jxl only when the folder
    holds no JPEG/PNG, so main() cannot know which folder the outputs land in.
    Naming one of them would be a guess — and it guessed wrong."""
    jxl = _a_jxl(tmp_path)
    r = _run(TRANSCODER, str(jxl.parent), "elsewhere", "--force-convert",
             "--mode", "1", cwd=tmp_path)
    assert "ignores the output folder" in r.stdout, r.stdout
    assert tr.RECOVERED_JPEG_FOLDER in r.stdout
    assert tr.CONVERTED_JXL_FOLDER in r.stdout
    assert "content selects" in r.stdout
    # ...and the run really does pick the one the old warning denied.
    assert (jxl.parent / tr.RECOVERED_JPEG_FOLDER).exists(), r.stdout


def test_jpeg_to_jxl_mode1_still_names_converted_jxl(tmp_path):
    _jpeg(tmp_path / "src" / "a.jpg")
    r = _run(TRANSCODER, "src", "elsewhere", "--force-transcode", "--mode", "1",
             cwd=tmp_path)
    assert tr.CONVERTED_JXL_FOLDER in r.stdout, r.stdout


def test_output_name_override_is_reflected(tmp_path):
    """--output-name wins for convert modes 1 and 3; the warning said otherwise."""
    _jpeg(tmp_path / "src" / "a.jpg")
    r = _run(TRANSCODER, "src", "elsewhere", "--force-convert", "--mode", "1",
             "--output-name", "my_folder", cwd=tmp_path)
    assert "my_folder" in r.stdout, r.stdout


# ── #292: the warnings must use the shared predicate ────────────────────────

def test_mode0_writing_back_into_the_source_is_not_a_collapse(tmp_path):
    """--strip warned about a layout where nothing can collide."""
    _tiff(tmp_path / "src" / "a.tif")
    r = _run(ENCODER, "src", "src", "--mode", "0", "--distance", "0", "--strip",
             cwd=tmp_path)
    assert "--strip writes NO provenance marker" not in r.stdout, r.stdout


def test_mode0_with_a_real_output_folder_still_warns(tmp_path):
    _tiff(tmp_path / "src" / "a.tif")
    r = _run(ENCODER, "src", "out", "--mode", "0", "--distance", "0", "--strip",
             cwd=tmp_path)
    assert "--strip writes NO provenance marker" in r.stdout, r.stdout


def test_provenance_is_reported_inert_for_mode0_in_place(tmp_path):
    """The mirror image: the ad-hoc test suppressed this warning."""
    _tiff(tmp_path / "src" / "a.tif")
    r = _run(ENCODER, "src", "src", "--mode", "0", "--distance", "0",
             "--delete-source", "--delete-confirm-off", "--provenance", "content",
             cwd=tmp_path)
    assert "has no effect in mode 0" in r.stdout, r.stdout


def test_provenance_is_not_reported_inert_when_mode0_collapses(tmp_path):
    _tiff(tmp_path / "src" / "a.tif")
    r = _run(ENCODER, "src", "out", "--mode", "0", "--distance", "0",
             "--delete-source", "--delete-confirm-off", "--provenance", "content",
             cwd=tmp_path)
    assert "has no effect in mode 0" not in r.stdout, r.stdout


def test_the_decoder_none_warning_uses_the_predicate_too(tmp_path):
    _tiff(tmp_path / "src" / "a.tif")
    assert _run(ENCODER, "src", "jxl", "--mode", "2", "--distance", "0",
                cwd=tmp_path).returncode == 0
    r = _run(DECODER, "jxl", "jxl", "--mode", "0", "--none", cwd=tmp_path)
    assert "--none writes NO provenance marker" not in r.stdout, r.stdout


# ── #293: a stranded output keeps its checksum somewhere ────────────────────

def test_the_staging_db_survives_a_failed_move(tmp_path, monkeypatch):
    """The hash of a file still in staging lived only in the staging db, and
    the db was deleted anyway."""
    staging = tmp_path / "stg"
    staging.mkdir()
    _jpeg(tmp_path / "root" / "a.jpg")

    real_move = tr.shutil.move

    def _fail_move(a, b):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(tr, "TEMP2_DIR", str(staging))
    monkeypatch.setattr(tr.shutil, "move", _fail_move)
    monkeypatch.setattr(tr, "DELETE_SOURCE", False)
    tr._reset_abort()
    src = tmp_path / "root" / "a.jpg"
    out = tmp_path / "root" / "out" / "a.jxl"
    tr.process_group_transcode([(src, out)], 1, decode=False, verify=False, mode=3,
                               reconvert_val=True, smart=False, effort=1)
    monkeypatch.setattr(tr.shutil, "move", real_move)

    assert (staging / tr.CHECKSUMS_FILENAME).exists(), (
        "the only copy of the stranded file's checksum was deleted")


def test_the_staging_db_is_still_cleaned_up_on_success(tmp_path):
    staging = tmp_path / "stg"
    staging.mkdir()
    _jpeg(tmp_path / "root" / "a.jpg")
    assert _run(TRANSCODER, "root", "--force-transcode", "--mode", "3",
                "--staging", str(staging), cwd=tmp_path).returncode == 0
    assert not (staging / tr.CHECKSUMS_FILENAME).exists()


# ── #294: the wording matches where the information actually is ─────────────

def test_the_banner_does_not_point_the_wrong_way():
    src = (REPO / "jxl_tiff_decoder.py").read_text(encoding="utf-8")
    assert "named above" not in src
    assert "INCOMPLETE warnings below" in src


def _three_page_split(tmp_path):
    p = tmp_path / "src" / "scan.tif"
    p.parent.mkdir(parents=True, exist_ok=True)
    with tifffile.TiffWriter(str(p)) as tif:
        for v in (1000, 20000, 40000):
            tif.write(np.full((32, 32, 3), v, np.uint16), photometric="rgb")
    assert _run(ENCODER, "src", "jxl", "--mode", "2", "--distance", "0", "--effort", "1",
                "--multipage-mode", "split", cwd=tmp_path).returncode == 0
    return sorted((tmp_path / "jxl").glob("*.jxl"))


def test_disagreeing_markers_do_not_advise_hunting_for_a_page(tmp_path):
    """That branch may have every page present — the markers are what disagree.
    Telling the user to go find the missing one sends them after nothing."""
    jxls = _three_page_split(tmp_path)
    subprocess.run(["exiftool", "-overwrite_original",
                    "-XMP-dc:Relation-=jxlphoto-pages:3",
                    "-XMP-dc:Relation+=jxlphoto-pages:5", str(jxls[0])],
                   capture_output=True, timeout=60)
    r = _run(DECODER, "jxl", "out", "--mode", "2", "--delete-source",
             "--delete-confirm-off", cwd=tmp_path)
    assert "disagree about the size" in r.stdout, r.stdout
    assert "folder holding every page" not in r.stdout, r.stdout
    assert "markers are inconsistent" in r.stdout
    assert "--allow-incomplete-groups" in r.stdout


def test_a_truncated_split_still_gets_the_find_the_page_advice(tmp_path):
    jxls = _three_page_split(tmp_path)
    jxls[1].unlink()
    r = _run(DECODER, "jxl", "out", "--mode", "2", "--delete-source",
             "--delete-confirm-off", cwd=tmp_path)
    assert "folder holding every page" in r.stdout, r.stdout
    assert "missing page for good" in r.stdout


# ── #295: the gate's contract, and a closed stdin ───────────────────────────

def test_the_gate_declares_what_it_actually_returns():
    import inspect
    sig = inspect.signature(wp.InteractiveMenu._confirm_lossy_delete_skipped)
    assert sig.return_annotation is None, sig.return_annotation
    src = (REPO / "jxl_photo.py").read_text(encoding="utf-8")
    assert "if not self._confirm_lossy_delete_skipped" not in src, (
        "the dead call-site guard is back")


def test_a_closed_stdin_declines_instead_of_raising(monkeypatch, capsys):
    """Every other gate here fails closed on EOF; this one raised EOFError out
    of input() and took the run down with a traceback."""
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)

    def _eof(*a):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    adv = {"delete_source": True, "delete_skipped": True}
    menu._confirm_lossy_delete_skipped(
        {"advanced_options": adv, "conversion_type": "convert_lossy"})
    assert adv["delete_skipped"] is False, "EOF must count as declining"
    assert "will be KEPT" in capsys.readouterr().out


def test_ctrl_c_at_that_prompt_declines_too(monkeypatch):
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)

    def _int(*a):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", _int)
    adv = {"delete_source": True, "delete_skipped": True}
    menu._confirm_lossy_delete_skipped(
        {"advanced_options": adv, "conversion_type": "convert_lossy"})
    assert adv["delete_skipped"] is False


# ── #296: the default marker dict mirrors the real one ──────────────────────

def test_default_info_carries_every_marker_key(tmp_path):
    f = tmp_path / "a.jxl"
    f.write_bytes(b"x")
    real = dec._read_multipage_markers_batch([f])[str(f)]
    src = (REPO / "jxl_tiff_decoder.py").read_text(encoding="utf-8")
    i = src.index("_DEFAULT_INFO = {")
    default = eval(src[i + len("_DEFAULT_INFO = "):src.index("}", i) + 1])
    assert set(default) == set(real), (
        f"_DEFAULT_INFO is missing {set(real) - set(default)}")
