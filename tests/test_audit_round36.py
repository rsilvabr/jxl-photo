#!/usr/bin/env python3
"""Regression tests for round 36: the regressions the round-35 fixes introduced.

  R1  decoder: refusing to overwrite a master TIFF is status "refused", never
      "skipped" — a skip admitted the JXL to --delete-skipped, which deleted
      it on the strength of an unrelated TIFF (the log said KEEP, then DELETED)
  R2  _merge_lineage_blocks deduplicated entries INSIDE one field, so a real
      "cjxl d=0.1 e=7 | cjxl d=0.1 e=7" chain (two generations) read as one
  R3  the recompressor's multi-page veto never saw the pages that FAILED (or
      were policy-skipped / refused): they were left out of the decisions, so
      page 0 was deleted alone
  R4  "output == input" was refused in mode 0 too — every mode-0 manifest
      row sends Destination == Source, so they all exited 2; the wrapper's
      in-place gate only matched an EMPTY Destination, which never happens
  R5  --repair-jbrd ignored exiftool's exit code (reported "markers stripped"
      for an edit that never happened), edited the archive in place even when
      the repair failed, and left checksums.md5 stale after a repair
  +   recompressor: in-place detection compared paths without abspath
  +   recompressor: metadata kept as Brotli "brob" boxes (IrfanView)

The last class of tests runs the REAL codecs: every bug of the audit that
found these (round 34/35) was invisible to the mocked suite.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_jpeg_transcoder as tr
import jxl_recompressor as rec
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc
import jxl_photo as wp

REPO = Path(__file__).resolve().parent.parent


def _jxl_stub(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)


# ---------------------------------------------------------------------------
# R1 — a refused master TIFF never lets --delete-skipped delete the JXL
# ---------------------------------------------------------------------------

def test_refused_group_is_never_deleted_by_delete_skipped(tmp_path, monkeypatch):
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    final = tmp_path / "a.tif"
    final.write_bytes(b"an unrelated master")

    dec.setup_logger()
    monkeypatch.setattr(dec, "DELETE_SOURCE", True)
    monkeypatch.setattr(dec, "DELETE_SKIPPED", True)
    monkeypatch.setattr(dec, "TEMP2_DIR", None)
    monkeypatch.setattr(dec, "_verify_tiff_integrity", lambda p: True)
    monkeypatch.setattr(dec, "convert_multipage_jxl_group",
                        lambda m, e, w, f, *a: (str(m), "refused", str(f)))
    task = {"main_jxl": src, "entries": [(src, 0, False, False, 0, False, None)],
            "final_tiff": final}
    dec.process_group([task], 1)
    assert src.exists(), "the JXL was deleted on the strength of an unrelated TIFF"


def test_would_skip_group_is_false_for_a_refused_master(tmp_path, monkeypatch):
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    final = tmp_path / "a.tif"
    final.write_bytes(b"master")
    os.utime(final, (final.stat().st_mtime - 100,) * 2)
    monkeypatch.setattr(dec, "OVERWRITE", "smart")
    monkeypatch.setattr(dec, "_decode_output_is_ours", lambda p: False)
    assert dec._would_skip_group([(src, 0, False, False, 0, False, None)], final) is False


# ---------------------------------------------------------------------------
# R2 — the lineage merge never collapses real generations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mod", [rec, enc], ids=["recompressor", "encoder"])
def test_merge_keeps_repeated_entries_inside_one_field(mod):
    entries, stored = mod._merge_lineage_blocks("cjxl d=0.1 e=7 | cjxl d=0.1 e=7", "")
    assert entries == [("0.1", "7"), ("0.1", "7")]


@pytest.mark.parametrize("mod", [rec, enc], ids=["recompressor", "encoder"])
def test_merge_mirror_is_one_history_split_is_two(mod):
    same = "gen=1 | cjxl d=0.1 e=7"
    assert mod._merge_lineage_blocks(same, same) == ([("0.1", "7")], 1)
    # desc = the decoded TIFF's inherited chain, software = the new encode
    entries, _ = mod._merge_lineage_blocks("gen=1 | cjxl d=0.1 e=7",
                                           "Capture One | gen=1 | cjxl d=0.5 e=7")
    assert entries == [("0.1", "7"), ("0.5", "7")]


def test_restamp_of_a_two_generation_legacy_chain_counts_three(monkeypatch):
    monkeypatch.setattr(rec, "ENCODE_TAG_MODE", "xmp")
    monkeypatch.setattr(rec, "CJXL_DISTANCE", 1.0)
    monkeypatch.setattr(rec, "CJXL_EFFORT", 7)
    lines = rec._restamp_args("cjxl d=0.1 e=7 | cjxl d=0.1 e=7", "")
    desc = next(l for l in lines if l.startswith("-XMP-dc:Description="))
    assert desc == ("-XMP-dc:Description=gen=3 | cjxl d=0.1 e=7 | "
                    "cjxl d=0.1 e=7 | cjxl d=1.0 e=7")


# ---------------------------------------------------------------------------
# R3 — a page that did not make it vetoes the deletion of its siblings
# ---------------------------------------------------------------------------

def _group(tmp_path, monkeypatch):
    items = []
    for name in ("scan.jxl", "scan_page2.jxl"):
        s = tmp_path / name
        _jxl_stub(s)
        f = tmp_path / "out" / name
        _jxl_stub(f)
        items.append({"src": s, "final": f, "in_place": False,
                      "action": "convert", "src_d": 0.1})
    rec.setup_logger()
    monkeypatch.setattr(rec, "DELETE_SOURCE", True)
    monkeypatch.setattr(rec, "DELETE_SKIPPED", False)
    monkeypatch.setattr(rec, "_verify_jxl_integrity", lambda p: True)
    monkeypatch.setattr(rec, "_read_mpg_markers",
                        lambda paths: ({str(p): "group-1" for p in paths}, True))
    return items


def test_failed_sibling_vetoes_the_group(tmp_path, monkeypatch):
    a, b = _group(tmp_path, monkeypatch)
    results = {str(a["src"]): ("ok", str(a["final"])),
               str(b["src"]): ("error", str(b["final"]))}
    rec._delete_gate([a, b], results, {str(a["src"])})
    assert a["src"].exists(), "page 0 deleted while its IR page failed"
    assert b["src"].exists()


def test_policy_skipped_sibling_vetoes_the_group(tmp_path, monkeypatch):
    a, b = _group(tmp_path, monkeypatch)
    b["action"] = "skip"                  # never ran: no entry in results
    results = {str(a["src"]): ("ok", str(a["final"]))}
    rec._delete_gate([a, b], results, {str(a["src"])})
    assert a["src"].exists()


def test_same_group_id_in_another_folder_is_another_document(tmp_path, monkeypatch):
    a, b = _group(tmp_path, monkeypatch)
    other = tmp_path / "copy" / "scan_page2.jxl"
    _jxl_stub(other)
    b = dict(b, src=other)                # same id, different folder, failed
    results = {str(a["src"]): ("ok", str(a["final"])),
               str(other): ("error", str(b["final"]))}
    rec._delete_gate([a, b], results, {str(a["src"])})
    assert not a["src"].exists(), "a copy elsewhere must not veto this document"


# ---------------------------------------------------------------------------
# R4 — mode 0 with output == input is in place; the wrapper sees it too
# ---------------------------------------------------------------------------

def test_mode0_output_equal_to_input_is_in_place(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    _jxl_stub(root / "a.jxl")
    r = subprocess.run(
        [sys.executable, str(REPO / "jxl_recompressor.py"), str(root), str(root),
         "--mode", "0", "--dry-run", "--on-unknown", "convert"],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "(in place)" in r.stdout


def test_relative_and_absolute_paths_are_the_same_file(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    _jxl_stub(root / "a.jxl")
    r = subprocess.run(
        [sys.executable, str(REPO / "jxl_recompressor.py"), "photos",
         str(root.resolve()), "--mode", "0", "--dry-run", "--on-unknown", "convert"],
        capture_output=True, text=True, timeout=120, cwd=str(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "(in place)" in r.stdout, "the re-encode would be written over its own input"


def test_wrapper_detects_in_place_manifest_rows(tmp_path):
    src = str(tmp_path)
    assert wp._recompress_entry_in_place(src, src, 0)          # Destination == Source
    assert wp._recompress_entry_in_place(src, "", 0)
    assert wp._recompress_entry_in_place(src, str(tmp_path / "x"), 8)
    assert not wp._recompress_entry_in_place(src, str(tmp_path / "x"), 0)
    assert not wp._recompress_entry_in_place(src, src, 1)


# ---------------------------------------------------------------------------
# R5 — --repair-jbrd works on a copy and says only what really happened
# ---------------------------------------------------------------------------

def test_strip_reports_a_failed_exiftool_edit(tmp_path, monkeypatch):
    jxl = tmp_path / "p.jxl"
    _jxl_stub(jxl)
    monkeypatch.setattr(tr, "_run_exiftool_argfile",
                        lambda lines, **kw: subprocess.CompletedProcess(lines, 1, "", "minor"))
    why = tr._strip_provenance_markers(jxl, {"src": "a", "srcsum": "b"})
    assert why and "could not edit" in why


def test_failed_repair_leaves_the_file_untouched(tmp_path, monkeypatch):
    jxl = tmp_path / "p.jxl"
    _jxl_stub(jxl)
    before = jxl.read_bytes()
    tr.setup_logger()
    monkeypatch.setattr(tr, "_read_source_markers_batch",
                        lambda paths: {str(p): {"src": "a", "srcsum": "b"} for p in paths})

    def _strip(path, info):
        path.write_bytes(b"edited")
        return None
    monkeypatch.setattr(tr, "_strip_provenance_markers", _strip)
    monkeypatch.setattr(tr, "_jxl_reconstruct_md5", lambda p: None)
    state, _ = tr._repair_one_jbrd(jxl, dry_run=False)
    assert state == "broken"
    assert jxl.read_bytes() == before
    assert list(tmp_path.iterdir()) == [jxl], "the repair copy was left behind"


def test_successful_repair_refreshes_checksums(tmp_path, monkeypatch):
    jxl = tmp_path / "p.jxl"
    _jxl_stub(jxl)
    tr.store_md5_db(jxl, "0" * 32)                 # the ORIGINAL JPEG's md5
    tr.setup_logger()
    monkeypatch.setattr(tr, "_read_source_markers_batch",
                        lambda paths: {str(p): {"src": "a", "srcsum": "b"} for p in paths})

    def _strip(path, info):
        path.write_bytes(path.read_bytes() + b"x")
        return None
    monkeypatch.setattr(tr, "_strip_provenance_markers", _strip)
    monkeypatch.setattr(tr, "_jxl_reconstruct_md5", lambda p: "f" * 32)
    state, _ = tr._repair_one_jbrd(jxl, dry_run=False)
    assert state == "repaired"
    assert tr.read_md5_db(jxl) == "f" * 32
    assert tr.read_jxl_self_hash_db(jxl) == tr.md5_of_file(jxl)


def test_dry_run_repair_writes_nothing(tmp_path, monkeypatch):
    jxl = tmp_path / "p.jxl"
    _jxl_stub(jxl)
    before = jxl.read_bytes()
    tr.setup_logger()
    monkeypatch.setattr(tr, "_read_source_markers_batch",
                        lambda paths: {str(p): {"src": "a", "srcsum": "b"} for p in paths})
    monkeypatch.setattr(tr, "_strip_provenance_markers", lambda p, i: None)
    monkeypatch.setattr(tr, "_jxl_reconstruct_md5", lambda p: "f" * 32)
    state, _ = tr._repair_one_jbrd(jxl, dry_run=True)
    assert state == "repairable"
    assert jxl.read_bytes() == before
    assert list(tmp_path.iterdir()) == [jxl]


# ---------------------------------------------------------------------------
# Real codecs
# ---------------------------------------------------------------------------

_HAVE_TOOLS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
real = pytest.mark.skipif(not _HAVE_TOOLS, reason="needs cjxl, djxl and exiftool on PATH")


def _jpeg_with_xmp(path: Path):
    from PIL import Image
    import numpy as np
    rng = np.random.default_rng(1)
    arr = (rng.random((96, 128, 3)) * 255).astype("uint8")
    Image.fromarray(arr).save(path, quality=90)
    subprocess.run(["exiftool", "-q", "-overwrite_original",
                    "-XMP-dc:Description=caption", "-XMP-xmp:Rating=4",
                    "-Make=NIKON", str(path)], check=True, capture_output=True)


def _boxes(path: Path):
    d = path.read_bytes()
    i, out = 0, []
    while i < len(d):
        s = int.from_bytes(d[i:i + 4], "big")
        t = d[i + 4:i + 8].decode("latin-1")
        if s == 1:
            s = int.from_bytes(d[i + 8:i + 16], "big")
        if s == 0:
            s = len(d) - i
        out.append(t)
        i += s
    return out


@real
def test_real_jpeg_with_xmp_still_reconstructs_bit_exact(tmp_path):
    jpg = tmp_path / "photo.jpg"
    _jpeg_with_xmp(jpg)
    original = tr.md5_of_file(jpg)
    r = subprocess.run([sys.executable, str(REPO / "jxl_jpeg_transcoder.py"),
                        str(tmp_path), "--mode", "1"],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    jxl = tmp_path / "converted_jxl" / "photo.jxl"
    assert tr._jxl_reconstruct_md5(jxl) == original


@real
def test_real_repair_recovers_a_marker_damaged_jxl(tmp_path):
    jpg = tmp_path / "photo.jpg"
    _jpeg_with_xmp(jpg)
    jxl = tmp_path / "photo.jxl"
    subprocess.run(["cjxl", str(jpg), str(jxl), "--lossless_jpeg=1"],
                   check=True, capture_output=True)
    # What v2.0.0-v2.0.3 did to every JPEG -> JXL transcode:
    subprocess.run(["exiftool", "-q", "-overwrite_original",
                    "-XMP-dc:Relation+=jxlphoto-src:0123456789abcdef",
                    "-XMP-dc:Relation+=jxlphoto-srcsum:fedcba9876543210",
                    str(jxl)], check=True, capture_output=True)
    assert tr._jxl_reconstruct_md5(jxl) is None, "fixture no longer reproduces the damage"
    tr.setup_logger()
    state, detail = tr._repair_one_jbrd(jxl, dry_run=False)
    assert state == "repaired", detail
    assert tr._jxl_reconstruct_md5(jxl) is not None


@real
def test_real_recompress_writes_plain_metadata_before_the_codestream(tmp_path):
    import numpy as np
    src_dir = tmp_path / "in"
    src_dir.mkdir()
    png = tmp_path / "a.png"
    from PIL import Image
    Image.fromarray((np.random.default_rng(2).random((64, 64, 3)) * 255)
                    .astype("uint8")).save(png)
    jxl = src_dir / "a.jxl"
    subprocess.run(["cjxl", str(png), str(jxl), "-d", "0.1", "--container=1"],
                   check=True, capture_output=True)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-Make=NIKON",
                    "-XMP-dc:Description=gen=1 | cjxl d=0.1 e=7", str(jxl)],
                   check=True, capture_output=True)
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(src_dir), "--mode", "1", "--distance", "1.0",
                        "--no-preflight"],
                       capture_output=True, text=True, timeout=300,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    out = src_dir / "recompressed_jxl" / "a.jxl"
    boxes = _boxes(out)
    assert "brob" not in boxes, boxes
    first_code = min(i for i, b in enumerate(boxes) if b in ("jxlc", "jxlp"))
    assert boxes.index("Exif") < first_code and boxes.index("xml ") < first_code, boxes


# ---------------------------------------------------------------------------
# Round 40 real-codec tests (#1, #2, #3)
# ---------------------------------------------------------------------------

def _random_png(path: Path, seed: int, size=(64, 64)):
    from PIL import Image
    import numpy as np
    Image.fromarray((np.random.default_rng(seed).random((*size, 3)) * 255)
                    .astype("uint8")).save(path)


@real
def test_real_thumbnail_named_standalone_decodes_as_a_photo(tmp_path):
    """#1: a normal single-page TIFF named `photo_thumbnail.tif` must encode
    and then decode with --no-reconstruct-multipage + --thumbnail-handling
    ignore as an ORDINARY TIFF, not be tagged SubFileType=1 or skipped."""
    import numpy as np
    import tifffile

    tif = tmp_path / "photo_thumbnail.tif"
    tifffile.imwrite(str(tif),
                     (np.random.default_rng(7).random((48, 64, 3)) * 65535)
                     .astype("uint16"), photometric="rgb")

    r = subprocess.run([sys.executable, str(REPO / "jxl_tiff_encoder.py"),
                        str(tif), "--mode", "0", "--multipage-mode", "split",
                        "--delete-confirm-off"],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    jxl = tmp_path / "photo_thumbnail.jxl"
    assert jxl.exists(), r.stdout + r.stderr

    r = subprocess.run([sys.executable, str(REPO / "jxl_tiff_decoder.py"),
                        str(tmp_path), "--mode", "1",
                        "--no-reconstruct-multipage",
                        "--thumbnail-handling", "ignore",
                        "--overwrite", "--delete-confirm-off"],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    out = tmp_path / "converted_tiff" / "photo_thumbnail.tif"
    assert out.exists(), ("a standalone *_thumbnail photo produced no TIFF:\n"
                          + r.stdout + r.stderr)
    with tifffile.TiffFile(str(out)) as tf:
        assert int(tf.pages[0].subfiletype or 0) == 0, \
            "the standalone photo was written as a reduced-resolution thumbnail"


@real
def test_real_recompressor_delete_skipped_keeps_source_next_to_unrelated_output(tmp_path):
    """#2: mode 1, an unrelated same-named output must NOT certify the deletion
    of the source under --delete-skipped. Pre-fix the source was unlinked."""
    src_dir = tmp_path / "in"
    src_dir.mkdir()
    src = src_dir / "photo.jxl"
    _random_png(tmp_path / "own.png", seed=3)
    subprocess.run(["cjxl", str(tmp_path / "own.png"), str(src),
                    "-d", "0.1", "--container=1"], check=True, capture_output=True)
    subprocess.run(["exiftool", "-q", "-overwrite_original",
                    "-XMP-dc:Relation+=jxlphoto-src:AAAAAAAAAAAAAAAA",
                    "-XMP-dc:Relation+=jxlphoto-srcsum:1111111111111111",
                    str(src)], check=True, capture_output=True)

    out_dir = src_dir / "recompressed_jxl"
    out_dir.mkdir()
    out = out_dir / "photo.jxl"
    _random_png(tmp_path / "other.png", seed=4)
    subprocess.run(["cjxl", str(tmp_path / "other.png"), str(out),
                    "-d", "1.0", "--container=1"], check=True, capture_output=True)
    subprocess.run(["exiftool", "-q", "-overwrite_original",
                    "-XMP-dc:Relation+=jxlphoto-src:CCCCCCCCCCCCCCCC",
                    "-XMP-dc:Relation+=jxlphoto-srcsum:3333333333333333",
                    str(out)], check=True, capture_output=True)
    # The output is NEWER, so the run reports SKIP (exists) and the gate acts.
    import os
    import time
    future = time.time() + 100
    os.utime(out, (future, future))

    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(src_dir), "--mode", "1", "--on-unknown", "convert",
                        "--delete-source", "--delete-skipped",
                        "--delete-confirm-off", "--no-preflight"],
                       capture_output=True, text=True, timeout=300,
                       stdin=subprocess.DEVNULL)
    assert src.exists(), ("the source was deleted on the strength of an unrelated "
                          "same-named output:\n" + r.stdout + r.stderr)


@real
def test_real_repair_strips_multiple_marker_pairs(tmp_path):
    """#3: a jbrd JXL with TWO marker pairs of DIFFERENT ids must be fully
    stripped by --repair-jbrd (pre-fix only the last pair was removed, and the
    file was reported STILL BROKEN though it was repairable)."""
    jpg = tmp_path / "photo.jpg"
    _jpeg_with_xmp(jpg)
    jxl = tmp_path / "photo.jxl"
    subprocess.run(["cjxl", str(jpg), str(jxl), "--lossless_jpeg=1"],
                   check=True, capture_output=True)
    subprocess.run(["exiftool", "-q", "-overwrite_original",
                    "-XMP-dc:Relation+=jxlphoto-src:AAAAAAAAAAAAAAAA",
                    "-XMP-dc:Relation+=jxlphoto-srcsum:1111111111111111",
                    "-XMP-dc:Relation+=jxlphoto-src:BBBBBBBBBBBBBBBB",
                    "-XMP-dc:Relation+=jxlphoto-srcsum:2222222222222222",
                    str(jxl)], check=True, capture_output=True)
    assert tr._jxl_reconstruct_md5(jxl) is None, \
        "fixture no longer reproduces the multi-pair damage"
    tr.setup_logger()
    state, detail = tr._repair_one_jbrd(jxl, dry_run=False)
    assert state == "repaired", detail
    assert tr._jxl_reconstruct_md5(jxl) is not None
    srcs, srcsums = tr._read_all_source_marker_values(jxl)
    assert srcs == [] and srcsums == [], (srcs, srcsums)


# ---------------------------------------------------------------------------
# Round 40b real-codec test (#6): single-page MASK TIFF keeps SubFileType=4
# ---------------------------------------------------------------------------

@real
def test_real_single_page_mask_tiff_keeps_subfiletype_4(tmp_path):
    """#6: a ONE-page TIFF whose only page is SubFileType=4 (MASK — a film
    scanner's IR page exported alone) encodes through ALL three paths (normal
    split planning, --multipage-mode ignore, --multipage-mode skip) and the
    decoder must write SubfileType=4 back. Pre-fix the JXL carried no
    jxlphoto-subfiletype marker and the round trip wrote 0."""
    import numpy as np
    import tifffile

    for mp_mode in ("split", "ignore", "skip"):
        d = tmp_path / mp_mode
        d.mkdir()
        tif = d / "photo.tif"
        tifffile.imwrite(str(tif),
                         (np.random.default_rng(5).random((48, 64, 3)) * 65535)
                         .astype("uint16"), photometric="rgb",
                         extratags=[(254, 4, 1, 4, True)])  # SubfileType=4 as a raw tag

        r = subprocess.run([sys.executable, str(REPO / "jxl_tiff_encoder.py"),
                            str(tif), "--mode", "0", "--multipage-mode", mp_mode,
                            "--delete-confirm-off"],
                           capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stdout + r.stderr
        jxl = d / "photo.jxl"
        assert jxl.exists(), r.stdout + r.stderr
        # The JXL must carry the marker the docs promise.
        probe = subprocess.run([enc._get_exiftool_cmd() or "exiftool", "-j",
                                "-XMP-dc:Relation", str(jxl)],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=120)
        marker = "jxlphoto-subfiletype:4"
        assert f"{marker}" in probe.stdout.replace('"', ''), (
            f"{mp_mode}: no subfiletype marker on the single-page MASK encode "
            f"({probe.stdout} {probe.stderr})")

        r2 = subprocess.run([sys.executable, str(REPO / "jxl_tiff_decoder.py"),
                             str(jxl), "--mode", "1", "--thumbnail-handling",
                             "ignore"],
                            capture_output=True, text=True, timeout=300)
        assert r2.returncode == 0, r2.stdout + r2.stderr
        tif_out = d / "converted_tiff" / "photo.tif"
        assert tif_out.exists(), r2.stdout + r2.stderr
        with tifffile.TiffFile(str(tif_out)) as got:
            assert len(got.pages) >= 1
            sft = int(got.pages[0].subfiletype or 0)
        assert sft == 4, (
            f"{mp_mode}: a single-page MASK TIFF decoded back as SubfileType="
            f"{sft} (docs promise 'restored exactly, including SubfileType=4')")

        # Clean the outputs so the second/third iteration re-encodes fresh.
        for p in (jxl, tif_out):
            try:
                p.unlink()
            except OSError:
                pass
