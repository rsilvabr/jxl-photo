#!/usr/bin/env python3
"""Regressions for two problems found on a real 615-file mode-6 run:

1. The encoder fed its thread pool one OUTPUT FOLDER at a time, so a library
   spread over many folders ran at a fraction of --workers (CPU sat at ~4%).
2. Manifest CSVs were written as UTF-8 without a BOM, which Excel opens using
   the system ANSI codepage — Japanese folder names came out as mojibake.
"""

import csv
import sys
import threading
import time
import unittest.mock as mock
from pathlib import Path

import numpy as np
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_tiff_encoder as enc
import jxl_photo as wrapper

enc.setup_logger()

JAPANESE_PATH = r"G:\2024\240419_山羊公園_長瀞岩畳_Shibazakura [FINAL]\02_Z7\_EXPORT"


# ─────────────────────────────────────────────
# 1. Thread pool spans every output folder
# ─────────────────────────────────────────────

def test_pool_spans_output_folders(tmp_path, monkeypatch):
    """Mode 3 gives every source subfolder its own output folder. The run used
    to process those folders one after another, each with its own pool, so a
    folder holding a single file used exactly one worker and the pool drained at
    every folder boundary. With 6 folders of 1 file and 4 workers, the old code
    could never exceed a concurrency of 1."""
    for i in range(6):
        d = tmp_path / f"shoot_{i}"
        d.mkdir()
        tifffile.imwrite(str(d / f"photo_{i}.tif"),
                         np.random.randint(0, 4096, (16, 16, 3), dtype=np.uint16))

    live = 0
    peak = 0
    lock = threading.Lock()

    def fake_convert_one(tiff_path, write_path, final_path, page_idx=0, *a, **k):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.15)          # long enough for the pool to fill
        with lock:
            live -= 1
        write_path.parent.mkdir(parents=True, exist_ok=True)
        write_path.write_bytes(b"\x00" * 16)
        return ((str(tiff_path), page_idx), "ok", str(final_path), "done")

    monkeypatch.setattr(enc, "convert_one", fake_convert_one)
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_encoder.py", str(tmp_path), "--mode", "3",
                         "--workers", "4", "--overwrite"])
    enc.main()

    assert peak >= 2, (
        f"peak concurrency was {peak} across 6 output folders with 4 workers — "
        f"the pool is still being fed one folder at a time")


def test_every_planned_output_is_converted(tmp_path, monkeypatch):
    """Companion to the above: widening the pool must not drop or duplicate work."""
    for i in range(5):
        d = tmp_path / f"shoot_{i}"
        d.mkdir()
        for j in range(3):
            tifffile.imwrite(str(d / f"photo_{i}_{j}.tif"),
                             np.random.randint(0, 4096, (16, 16, 3), dtype=np.uint16))

    seen = []
    seen_lock = threading.Lock()

    def fake_convert_one(tiff_path, write_path, final_path, page_idx=0, *a, **k):
        with seen_lock:
            seen.append(str(final_path))
        write_path.parent.mkdir(parents=True, exist_ok=True)
        write_path.write_bytes(b"\x00" * 16)
        return ((str(tiff_path), page_idx), "ok", str(final_path), "done")

    monkeypatch.setattr(enc, "convert_one", fake_convert_one)
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_encoder.py", str(tmp_path), "--mode", "3",
                         "--workers", "4", "--overwrite"])
    enc.main()

    assert len(seen) == 15, f"expected 15 conversions, got {len(seen)}"
    assert len(set(seen)) == 15, "an output was converted twice"


def test_staging_is_flushed_per_folder(tmp_path, monkeypatch):
    """Staging must still empty out: files are moved when their destination
    folder finishes, so nothing is left behind at the end of the run."""
    staging = tmp_path / "staging"
    src = tmp_path / "src"
    for i in range(4):
        d = src / f"shoot_{i}"
        d.mkdir(parents=True)
        tifffile.imwrite(str(d / f"photo_{i}.tif"),
                         np.random.randint(0, 4096, (16, 16, 3), dtype=np.uint16))

    def fake_convert_one(tiff_path, write_path, final_path, page_idx=0, *a, **k):
        write_path.parent.mkdir(parents=True, exist_ok=True)
        write_path.write_bytes(b"\x00" * 16)
        return ((str(tiff_path), page_idx), "ok", str(final_path), "done")

    monkeypatch.setattr(enc, "convert_one", fake_convert_one)
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_encoder.py", str(src), "--mode", "3",
                         "--workers", "3", "--overwrite", "--staging", str(staging)])
    enc.main()

    assert len(list(src.rglob("*.jxl"))) == 4, "every output reached its destination"
    assert not list(staging.glob("*.jxl")), "staging must be empty when the run ends"


# ─────────────────────────────────────────────
# 2. Manifest CSV encoding
# ─────────────────────────────────────────────

def _menu():
    cm = wrapper.ConfigManager()
    return wrapper.InteractiveMenu(cm, wrapper.DependencyChecker(cm))


def _run_manifest(menu, path, direction="tiff2jxl"):
    wf = {"origin_format": direction.split("2")[0], "dest_format": direction.split("2")[1]}
    with mock.patch.object(menu, "_pick_manifest", return_value=str(path)), \
         mock.patch.object(wrapper, "RICH_AVAILABLE", False), \
         mock.patch("builtins.input", return_value="y"):
        return menu._wizard_run_from_manifest(wf), wf


def test_manifest_is_written_with_bom_for_excel(tmp_path, monkeypatch):
    """Without the BOM Excel decodes the CSV with the system ANSI codepage and
    shows 文字化け for Japanese folder names — then saves those broken bytes back."""
    monkeypatch.setattr(wrapper, "SCRIPT_DIR", tmp_path)

    menu = _menu()
    analyzer = mock.MagicMock()
    analyzer.origin, analyzer.dest = "tiff", "jxl"
    analyzer.generate_manifest.return_value = [(JAPANESE_PATH, "", 3, 6)]

    path = Path(menu._generate_manifest(analyzer, {}, 6))
    raw = path.read_bytes()
    assert raw[:3] == b"\xef\xbb\xbf", "manifest must start with a UTF-8 BOM"
    assert JAPANESE_PATH in raw.decode("utf-8-sig"), "the path must survive intact"


def test_manifest_reader_strips_bom_from_header(tmp_path):
    """A BOM left in the first cell makes the header read as '\\ufeffSource', so
    the "is this the header?" check fails and the header becomes a data row."""
    path = tmp_path / "manifest_bom.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Source", "Destination", "Mode", "Direction"])
        w.writerow([JAPANESE_PATH, "", 6, "tiff2jxl"])

    ok, wf = _run_manifest(_menu(), path)
    assert ok, "a manifest we wrote ourselves must be accepted"
    entries = wf["manifest_entries"]
    assert len(entries) == 1, "the header row must not be parsed as an entry"
    assert entries[0][0] == JAPANESE_PATH, "Japanese path must round-trip exactly"


def test_manifest_in_ansi_codepage_is_refused_not_guessed(tmp_path):
    """Excel can save the CSV back in the system ANSI codepage. Guessing an
    encoding yields a plausible-looking path pointing somewhere else — and these
    paths drive a converter that deletes sources in mode 8. Refuse instead."""
    path = tmp_path / "manifest_ansi.csv"
    path.write_bytes(
        ("Source,Destination,Mode,Direction\r\n" + JAPANESE_PATH + ",,6,tiff2jxl\r\n")
        .encode("cp932"))

    ok, wf = _run_manifest(_menu(), path)
    assert ok is False, "an undecodable manifest must not run"
    assert "manifest_entries" not in wf, "no entry may be built from guessed bytes"


def test_ascii_manifest_still_works_without_bom(tmp_path):
    """Pure-ASCII manifests are valid UTF-8, so hand-written files (no BOM) are
    unaffected by the stricter decoding."""
    path = tmp_path / "manifest_plain.csv"
    path.write_text("Source,Destination,Mode,Direction\n"
                    r"G:\2024" + ",,3,tiff2jxl\n"
                    r"G:\2025" + ",,3,tiff2jxl\n", encoding="utf-8")

    ok, wf = _run_manifest(_menu(), path)
    assert ok, "a hand-written ASCII manifest must still be accepted"
    assert len(wf["manifest_entries"]) == 2
