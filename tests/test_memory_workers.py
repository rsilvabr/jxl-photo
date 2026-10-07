#!/usr/bin/env python3
"""Worker memory cap + thread-free subprocess capture (round 44).

The 2026-10-04 scheduled MOBILE run started 30 recompressor workers at d=3
effort 7; each cjxl peaked at ~3.6 GiB (the whole image at once instead of the
usual streaming encode), 30 x 3.6 GiB did not fit in the commit limit, 314
files failed and one worker hung forever in a subprocess reader thread whose
bootstrap died with MemoryError.

These tests pin the two fixes:

  * `_memory_capped_workers` / `_cjxl_whole_image` / `_cjxl_bytes_per_pixel` /
    `_available_commit_bytes` — the encoder and the recompressor estimate the
    same memory for the same settings and lower `--workers` so the batch fits.
  * `_run_captured` — stdout/stderr go through temp FILES, so no reader thread
    is ever created (a failed thread bootstrap can no longer hang the run).

`tests/test_helper_parity.py` already pins that the two copies of the memory
helpers and the four copies of `_run_captured` stay byte-identical.

The real-codec test (12) fixes libjxl 0.12.0's whole-image threshold. If a new
libjxl changes it, that test fails and the B/px table plus the rule in
`_cjxl_whole_image` (plan AI_tools/261004_Claude_plan_memoria-workers.md, §2.2)
have to be re-measured.
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_jpeg_transcoder as tr
import jxl_recompressor as rec
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent

GIB = 2 ** 30
AVAIL_40 = 40 * GIB
# 45.4 MP — the Z7 photo of the MOBILE run.
MP45 = 45_400_000


def _jxl_stub(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)


@pytest.fixture(autouse=True)
def _ample_physical_ram(monkeypatch):
    """The cap budgets from min(commit, physical RAM) by default. The tests
    below pin the COMMIT arithmetic, so the machine's real RAM must never be
    the smaller one; the WORKER_MEMORY_LIMIT tests override this."""
    for mod in (enc, rec):
        monkeypatch.setattr(mod, "_available_physical_bytes", lambda: 10 ** 15)
        monkeypatch.setattr(mod, "WORKER_MEMORY_LIMIT", "both")


# ---------------------------------------------------------------------------
# 1-7: the estimator and the cap itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mod", [enc, rec], ids=["encoder", "recompressor"])
@pytest.mark.parametrize(
    "distance,effort,buffering,modular,expected",
    [
        (3.0, 7, None, False, 90),
        (2.9, 7, None, False, 40),
        (8, 6, None, False, 40),
        (0.5, 8, None, False, 40),
        (0.51, 8, None, False, 320),
        (1, 9, None, False, 320),
        (0.05, 9, None, False, 40),
        (0, 7, None, False, 40),
        (0, 9, None, False, 40),
        (1, 10, None, False, 320),
        (3, 7, 1, False, 40),
        (1, 7, 0, False, 90),
        (1, 9, 0, False, 320),
        (0.05, 9, None, True, 110),
        (0, 9, None, True, 40),
    ],
)
def test_bytes_per_pixel_table(mod, distance, effort, buffering, modular, expected):
    assert mod._cjxl_bytes_per_pixel(distance, effort, buffering, modular) == expected


@pytest.mark.parametrize("mod", [enc, rec], ids=["encoder", "recompressor"])
def test_cap_whole_image_mobile(mod, monkeypatch, caplog):
    """d=3 e=7 is the MOBILE preset: 40 GiB free / 3.9 GiB per job -> 8."""
    monkeypatch.setattr(mod, "WORKER_MEMORY_FRACTION", 0.8)
    monkeypatch.setattr(mod, "_available_commit_bytes", lambda: AVAIL_40)
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        workers = mod._memory_capped_workers(30, MP45, 3.0, 7)
    assert workers == int(AVAIL_40 * 0.8 // (MP45 * 90)) == 8
    assert "--workers 30 reduced to 8" in caplog.text
    assert "--buffering 1" in caplog.text


@pytest.mark.parametrize("mod", [enc, rec], ids=["encoder", "recompressor"])
@pytest.mark.parametrize("effort,says,never", [
    # effort 7: streaming is the same image, ~1.5 % larger -> suggest it.
    (7, "files ~1.5% larger, same quality", "use effort 7 instead"),
    # effort 9: a streamed encode is effort 7's file (measured, cjxl 0.12) —
    # suggesting --buffering 1 as a cheap fix was misleading.
    (9, "use effort 7 instead", "same quality"),
])
def test_cap_hint_depends_on_effort(mod, monkeypatch, caplog, effort, says, never):
    monkeypatch.setattr(mod, "WORKER_MEMORY_FRACTION", 0.8)
    monkeypatch.setattr(mod, "_available_commit_bytes", lambda: AVAIL_40)
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        mod._memory_capped_workers(30, MP45, 3.0, effort)
    assert "reduced to" in caplog.text
    assert says in caplog.text, caplog.text
    assert never not in caplog.text, caplog.text
    assert "~2% larger" not in caplog.text


@pytest.mark.parametrize("mod", [enc, rec], ids=["encoder", "recompressor"])
def test_cap_streaming_no_buffering_hint(mod, monkeypatch, caplog):
    """d=1 e=7 streams: 40 GiB / 1.8 GiB per job -> 18, no buffering hint."""
    monkeypatch.setattr(mod, "WORKER_MEMORY_FRACTION", 0.8)
    monkeypatch.setattr(mod, "_available_commit_bytes", lambda: AVAIL_40)
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        workers = mod._memory_capped_workers(30, MP45, 1.0, 7)
    assert workers == int(AVAIL_40 * 0.8 // (MP45 * 40)) == 18
    assert "--workers 30 reduced to 18" in caplog.text
    assert "--buffering" not in caplog.text


@pytest.mark.parametrize("mod", [enc, rec], ids=["encoder", "recompressor"])
def test_cap_unknown_memory(mod, monkeypatch, caplog):
    """Neither commit nor RAM readable: do not cap, log why."""
    monkeypatch.setattr(mod, "WORKER_MEMORY_FRACTION", 0.8)
    monkeypatch.setattr(mod, "_available_commit_bytes", lambda: None)
    monkeypatch.setattr(mod, "_available_physical_bytes", lambda: None)
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        assert mod._memory_capped_workers(30, MP45, 3.0, 7) == 30
    assert "could not read" in caplog.text


@pytest.mark.parametrize("mod", [enc, rec], ids=["encoder", "recompressor"])
def test_cap_disabled(mod, monkeypatch):
    """Fraction 0 disables the cap WITHOUT ever reading the committed memory."""
    monkeypatch.setattr(mod, "WORKER_MEMORY_FRACTION", 0)

    def _boom():
        raise AssertionError("the cap read the machine's memory while disabled")

    monkeypatch.setattr(mod, "_available_commit_bytes", _boom)
    monkeypatch.setattr(mod, "_available_physical_bytes", _boom)
    assert mod._memory_capped_workers(30, MP45, 3.0, 7) == 30


@pytest.mark.parametrize("mod", [enc, rec], ids=["encoder", "recompressor"])
def test_cap_unknown_pixels_assumes_60mp(mod, monkeypatch):
    """An unknown page size is assumed 60 MP (conservative, not zero)."""
    monkeypatch.setattr(mod, "WORKER_MEMORY_FRACTION", 0.8)
    monkeypatch.setattr(mod, "_available_commit_bytes", lambda: AVAIL_40)
    expected = int(AVAIL_40 * 0.8 // (60_000_000 * 40))
    assert expected == 14
    assert mod._memory_capped_workers(30, 0, 1.0, 7) == expected


@pytest.mark.skipif(sys.platform != "win32", reason="GlobalMemoryStatusEx")
@pytest.mark.parametrize("mod", [enc, rec], ids=["encoder", "recompressor"])
def test_available_commit_bytes_real(mod):
    v = mod._available_commit_bytes()
    assert isinstance(v, int) and v > 0


# ---------------------------------------------------------------------------
# 7b: WORKER_MEMORY_LIMIT — the budget is min(commit, physical RAM) by default
# ---------------------------------------------------------------------------

def _boom():
    raise AssertionError("read a memory figure this WORKER_MEMORY_LIMIT excludes")


@pytest.mark.parametrize("mod", [enc, rec], ids=["encoder", "recompressor"])
def test_limit_both_takes_the_smaller_physical(mod, monkeypatch, caplog):
    """The 2026-10-07 case inverted: a large pagefile leaves 40 GiB to commit
    but only 20 GiB of RAM — the RAM decides, or the run pages to disk."""
    monkeypatch.setattr(mod, "WORKER_MEMORY_FRACTION", 0.9)
    monkeypatch.setattr(mod, "_available_commit_bytes", lambda: AVAIL_40)
    monkeypatch.setattr(mod, "_available_physical_bytes", lambda: 20 * GIB)
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        workers = mod._memory_capped_workers(30, MP45, 3.0, 9)
    assert workers == int(20 * GIB * 0.9 // (MP45 * 320)) == 1
    assert "20.0 GB available (physical)" in caplog.text, caplog.text
    assert "of physical RAM still available" in caplog.text, caplog.text


@pytest.mark.parametrize("mod", [enc, rec], ids=["encoder", "recompressor"])
def test_limit_both_takes_the_smaller_commit(mod, monkeypatch, caplog):
    """The measured 2026-10-07 machine: 32 GiB to commit, 39 GiB of RAM free
    (a 13.7 GB pagefile and browsers that reserve more than they use)."""
    monkeypatch.setattr(mod, "WORKER_MEMORY_FRACTION", 0.9)
    monkeypatch.setattr(mod, "_available_commit_bytes", lambda: 32 * GIB)
    monkeypatch.setattr(mod, "_available_physical_bytes", lambda: 39 * GIB)
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        workers = mod._memory_capped_workers(30, MP45, 3.0, 9)
    assert workers == int(32 * GIB * 0.9 // (MP45 * 320)) == 2
    assert "32.0 GB available (commit)" in caplog.text, caplog.text
    assert "the system can still commit" in caplog.text, caplog.text


@pytest.mark.parametrize("mod", [enc, rec], ids=["encoder", "recompressor"])
@pytest.mark.parametrize("limit,commit,physical,expected", [
    ("commit", lambda: AVAIL_40, _boom, int(AVAIL_40 * 0.8 // (MP45 * 90))),
    ("physical", _boom, lambda: 20 * GIB, int(20 * GIB * 0.8 // (MP45 * 90))),
])
def test_limit_single_reading(mod, monkeypatch, limit, commit, physical, expected):
    """"commit"/"physical" read ONLY their own figure."""
    monkeypatch.setattr(mod, "WORKER_MEMORY_FRACTION", 0.8)
    monkeypatch.setattr(mod, "WORKER_MEMORY_LIMIT", limit)
    monkeypatch.setattr(mod, "_available_commit_bytes", commit)
    monkeypatch.setattr(mod, "_available_physical_bytes", physical)
    assert mod._memory_capped_workers(30, MP45, 3.0, 7) == expected


@pytest.mark.parametrize("mod", [enc, rec], ids=["encoder", "recompressor"])
def test_limit_both_with_one_reading_unknown(mod, monkeypatch):
    monkeypatch.setattr(mod, "WORKER_MEMORY_FRACTION", 0.8)
    monkeypatch.setattr(mod, "_available_commit_bytes", lambda: None)
    monkeypatch.setattr(mod, "_available_physical_bytes", lambda: 20 * GIB)
    assert mod._available_memory_bytes() == (20 * GIB, "physical")
    monkeypatch.setattr(mod, "_available_physical_bytes", lambda: None)
    assert mod._available_memory_bytes() == (None, None)


@pytest.mark.parametrize("mod", [enc, rec], ids=["encoder", "recompressor"])
def test_limit_unknown_value_counts_as_both(mod, monkeypatch, caplog):
    monkeypatch.setattr(mod, "WORKER_MEMORY_FRACTION", 0.8)
    monkeypatch.setattr(mod, "WORKER_MEMORY_LIMIT", "ram")
    monkeypatch.setattr(mod, "_available_commit_bytes", lambda: AVAIL_40)
    monkeypatch.setattr(mod, "_available_physical_bytes", lambda: 20 * GIB)
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        workers = mod._memory_capped_workers(30, MP45, 3.0, 7)
    assert workers == int(20 * GIB * 0.8 // (MP45 * 90))
    assert "WORKER_MEMORY_LIMIT = 'ram' is not one of" in caplog.text, caplog.text


@pytest.mark.skipif(sys.platform != "win32", reason="GlobalMemoryStatusEx")
@pytest.mark.parametrize("mod", [enc, rec], ids=["encoder", "recompressor"])
def test_available_physical_bytes_real(mod, monkeypatch):
    # Undo the autouse double: this one reads the real machine.
    monkeypatch.undo()
    phys = mod._available_physical_bytes()
    commit = mod._available_commit_bytes()
    assert isinstance(phys, int) and phys > 0
    # Physical RAM available can never exceed the total commit headroom by
    # more than the RAM itself; sanity: both readings are plausible numbers.
    assert phys < 2 ** 50 and commit < 2 ** 50


# ---------------------------------------------------------------------------
# 8: main() actually caps what it hands to process_group
# ---------------------------------------------------------------------------

def _run_rec_main(monkeypatch, tmp_path, extra_argv, captured, avail=AVAIL_40):
    """Drive rec.main() with the process_group / exiftool doubles.

    The doubles follow tests/test_audit_round37.py's skeleton: a fake
    process_group records the `workers` argument, a fake params reader returns
    the metadata for each source (here with the MOBILE photo's pixel count).
    """
    src_dir = tmp_path / "in"
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "a.jxl"
    _jxl_stub(src)
    out_dir = tmp_path / "out"

    def fake_pg(items, workers):
        captured["workers"] = workers
        captured["items"] = [dict(it) for it in items]
        return ({str(it["src"]): ("ok", str(it["final"])) for it in items},
                {str(it["src"]) for it in items})

    monkeypatch.setattr(rec, "process_group", fake_pg)
    monkeypatch.setattr(
        rec, "_read_encode_params_batch",
        lambda paths: {str(p): {"desc": "", "software": "", "params": None,
                                "gen": 0, "pixels": MP45} for p in paths})
    monkeypatch.setattr(rec, "emit_summary_json", lambda *a, **k: None)
    monkeypatch.setattr(rec, "_get_cjxl_cmd", lambda: "cjxl")
    monkeypatch.setattr(shutil, "which", lambda c: "C:/tools/x")
    monkeypatch.setattr(rec, "_available_commit_bytes", lambda: avail)
    monkeypatch.setattr(rec, "WORKER_MEMORY_FRACTION", 0.8)
    monkeypatch.setattr(
        sys, "argv",
        ["jxl_recompressor.py", str(src_dir), str(out_dir), "--mode", "2",
         "--workers", "30", "--on-unknown", "convert", "--no-preflight"]
        + list(extra_argv))
    try:
        rec.main()
    except SystemExit as e:
        assert e.code == 0, f"unexpected exit {e.code}"
    return src


def test_recompressor_main_caps_workers(tmp_path, monkeypatch):
    captured = {}
    _run_rec_main(monkeypatch, tmp_path, ["--distance", "3", "--effort", "7"],
                  captured)
    assert captured["workers"] == 8
    assert captured["items"][0]["action"] == "convert"

    captured2 = {}
    _run_rec_main(monkeypatch, tmp_path / "second",
                  ["--distance", "1", "--effort", "7"], captured2)
    assert captured2["workers"] == 18


# ---------------------------------------------------------------------------
# 9: the encoder records each page's pixel count for the cap
# ---------------------------------------------------------------------------

def test_encoder_records_page_pixels(tmp_path):
    p = tmp_path / "a.tif"
    tifffile.imwrite(p, np.zeros((48, 64, 3), dtype=np.uint16))
    enc._PAGE_PIXELS.clear()
    enc._analyze_tiff_pages(p)
    assert enc._PAGE_PIXELS[(os.path.normcase(str(p)), 0)] == 64 * 48 == 3072


def test_encoder_ignore_mode_records_page_pixels(tmp_path, monkeypatch):
    """--multipage-mode ignore still records page 0's size for the cap.

    Ignore encodes page 0 only; without this the cap fell back to the 60 MP
    `_UNKNOWN_IMAGE_PIXELS` default and lowered `--workers` for no reason.
    """
    p = tmp_path / "a.tif"
    tifffile.imwrite(p, np.zeros((48, 64, 3), dtype=np.uint16), photometric="rgb")
    monkeypatch.setattr(enc, "MULTIPAGE_TIFF_MODE", "ignore")
    enc._PAGE_PIXELS.clear()
    enc.convert_multipage(p, tmp_path / "out", 0)
    assert enc._PAGE_PIXELS[(os.path.normcase(str(p)), 0)] == 64 * 48 == 3072


# ---------------------------------------------------------------------------
# 10-11: _run_captured never starts a reader thread; text/timeout behavior
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="Windows reader threads")
def test_run_captured_starts_no_thread(monkeypatch):
    """THE mechanism proof: if any call started a Thread, this fails.

    `capture_output=True` starts reader threads on Windows; a MemoryError in a
    thread's bootstrap makes Thread.start() wait forever before the timeout is
    armed. With temp files there is no thread at all.
    """
    if shutil.which("exiftool") is None or shutil.which("cjxl") is None:
        pytest.skip("needs exiftool and cjxl")

    def _no_thread(*_a, **_k):
        raise AssertionError("a reader thread was started")

    monkeypatch.setattr(threading.Thread, "start", _no_thread)

    for mod in (enc, dec, tr, rec):
        r = mod._run_exiftool_argfile(["-ver"])
        assert r.returncode == 0, mod.__name__
        assert r.stdout.strip(), mod.__name__

    r = rec._run_captured(["cjxl", "--version"], 30)
    assert r.returncode == 0
    assert b"JPEG XL" in (r.stdout + r.stderr)


def test_run_captured_text_and_timeout():
    # buffer.write bypasses Python's Windows \n -> \r\n translation, so the
    # child emits a literal CRLF — exactly what a Windows exiftool does.
    cmd = [sys.executable, "-c",
           "import sys; sys.stdout.buffer.write(b'a\\r\\nb'); "
           "sys.stderr.buffer.write(b'e')"]
    r = rec._run_captured(cmd, 30, text=True)
    assert r.returncode == 0
    assert r.stdout == "a\nb"
    assert r.stderr == "e"

    with pytest.raises(subprocess.TimeoutExpired):
        rec._run_captured([sys.executable, "-c", "import time; time.sleep(30)"], 1)


# ---------------------------------------------------------------------------
# 12: the whole-image threshold against the real codec
# ---------------------------------------------------------------------------

def _peak_private(cmd, psutil):
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    proc = psutil.Process(p.pid)
    peak = 0
    while p.poll() is None:
        try:
            mi = proc.memory_info()
        except psutil.Error:
            break
        peak = max(peak, mi.private if sys.platform == "win32" else mi.rss)
        time.sleep(0.02)
    p.wait()
    return peak


def test_cjxl_whole_image_threshold_real(tmp_path):
    """libjxl 0.12.0: d=3 e=7 encodes the whole image, d=2.9 e=7 streams.

    The threshold moves with effort 7 at distance 3. If a new libjxl changes
    it, this test fails and the B/px table plus `_cjxl_whole_image`'s rule must
    be re-measured (see the module docstring).
    """
    psutil = pytest.importorskip("psutil")
    cjxl = shutil.which("cjxl")
    if cjxl is None:
        pytest.skip("needs cjxl")

    rng = np.random.default_rng(0)
    y, x = np.mgrid[0:4096, 0:4096].astype(np.float32)
    base = np.stack([x / 4096, y / 4096, (x + y) / 8192], -1) * 50000 + 5000
    img = (base + rng.normal(0, 800, base.shape)).clip(0, 65535).astype(">u2")
    ppm = tmp_path / "in.ppm"
    ppm.write_bytes(b"P6\n4096 4096\n65535\n" + img.tobytes())

    peak_29 = _peak_private([cjxl, str(ppm), str(tmp_path / "d29.jxl"),
                             "-d", "2.9", "--effort", "7"], psutil)
    peak_3 = _peak_private([cjxl, str(ppm), str(tmp_path / "d3.jxl"),
                            "-d", "3", "--effort", "7"], psutil)

    assert peak_3 > 1.4 * peak_29, f"d=3 {peak_3} vs d=2.9 {peak_29}"
    assert rec._cjxl_whole_image(3, 7)
    assert not rec._cjxl_whole_image(2.9, 7)


# ---------------------------------------------------------------------------
# 13: the end-of-run memory hint
# ---------------------------------------------------------------------------

def _run_rec_with_failures(tmp_path, monkeypatch, detail):
    src_dir = tmp_path / "in"
    src_dir.mkdir(parents=True, exist_ok=True)
    _jxl_stub(src_dir / "a.jxl")
    out_dir = tmp_path / "out"

    def fake_pg(items, workers):
        for it in items:
            rec._error_details[str(it["src"])] = detail
        return ({str(it["src"]): ("error", str(it["final"])) for it in items},
                set())

    monkeypatch.setattr(rec, "process_group", fake_pg)
    monkeypatch.setattr(
        rec, "_read_encode_params_batch",
        lambda paths: {str(p): {"desc": "", "software": "", "params": None,
                                "gen": 0, "pixels": MP45} for p in paths})
    monkeypatch.setattr(rec, "emit_summary_json", lambda *a, **k: None)
    monkeypatch.setattr(rec, "_get_cjxl_cmd", lambda: "cjxl")
    monkeypatch.setattr(shutil, "which", lambda c: "C:/tools/x")
    monkeypatch.setattr(
        sys, "argv",
        ["jxl_recompressor.py", str(src_dir), str(out_dir), "--mode", "2",
         "--on-unknown", "convert", "--no-preflight"])
    try:
        rec.main()
    except SystemExit:
        pass


def test_memory_hint_in_summary(tmp_path, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        _run_rec_with_failures(
            tmp_path, monkeypatch,
            "cjxl: JPEG XL encoder v0.12.0\nJxlEncoderProcessOutput failed.")
    assert "look like the system ran out of memory" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        _run_rec_with_failures(tmp_path / "exiftool", monkeypatch,
                               "exiftool metadata copy: Out of memory!\n")
    assert "look like the system ran out of memory" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        _run_rec_with_failures(tmp_path / "other", monkeypatch,
                               "cjxl: bad header")
    assert "look like the system ran out of memory" not in caplog.text


# A cjxl stderr whose failure line starts past character 200 — the shape of 97
# of the 314 errors of the 2026-10-04 run, whose logged detail (the old
# stderr[:200]) held only the version banner and the "Encoding [...]" line.
_CJXL_OOM_STDERR = (
    b"JPEG XL encoder v0.12.0 4128790 [_AVX2_,SSE4,SSE2] {Clang 22.1.3}\r\n"
    b"Encoding [Container | VarDCT, d3.000, effort: 7 | 890-byte Exif | "
    b"7199-byte XMP]\r\n"
    b"Encoding [Container | VarDCT, d3.000, effort: 7 | 890-byte Exif | "
    b"7199-byte XMP]\r\n"
    b"JxlEncoderProcessOutput failed.\r\nEncodeImageJXL() failed.\r\n")


def _convert_one_failing(tmp_path, monkeypatch, fake_run_captured):
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    out = tmp_path / "out" / "a.jxl"
    monkeypatch.setattr(rec, "_run_captured", fake_run_captured)
    monkeypatch.setattr(rec, "_get_cjxl_cmd", lambda: "cjxl")
    monkeypatch.setattr(rec, "_cjxl_buffering_flag", lambda: [])
    status = rec.convert_one(src, out, out, "convert", False, "", "", None)[1]
    assert status == "error"
    return rec._error_details.pop(str(src))


@pytest.mark.parametrize("failure", ["cjxl_stderr", "memoryerror"])
def test_memory_failures_are_recognised(tmp_path, monkeypatch, failure):
    """The REAL convert_one error path must record a detail the end-of-run
    hint recognises: the tail of cjxl's stderr (its failure line comes last),
    and a bare MemoryError (empty message) by its type name."""
    assert _CJXL_OOM_STDERR.index(b"JxlEncoderProcessOutput") > 200

    def fake(cmd, timeout, text=False):
        if failure == "memoryerror":
            raise MemoryError()
        return subprocess.CompletedProcess(cmd, 1, b"", _CJXL_OOM_STDERR)

    detail = _convert_one_failing(tmp_path, monkeypatch, fake)
    assert any(s in detail.lower() for s in rec._MEMORY_ERROR_SIGNATURES), detail
    if failure == "cjxl_stderr":
        assert detail.endswith("EncodeImageJXL() failed."), detail
        assert "\r" not in detail


# ---------------------------------------------------------------------------
# 14: the two copies agree, constant for constant
# ---------------------------------------------------------------------------

def test_constants_agree():
    for name in ("_STREAMING_BYTES_PER_PIXEL",
                 "_WHOLE_IMAGE_E7_BYTES_PER_PIXEL",
                 "_WHOLE_IMAGE_E8_BYTES_PER_PIXEL",
                 "_MODULAR_LOSSY_BYTES_PER_PIXEL",
                 "_UNKNOWN_IMAGE_PIXELS"):
        assert getattr(enc, name) == getattr(rec, name), name

    # WORKER_MEMORY_FRACTION is zeroed in memory by the conftest fixture, so
    # compare the SOURCE line of each script.
    pat = re.compile(r"^WORKER_MEMORY_FRACTION = (.+)$", re.M)
    src_enc = (REPO / "jxl_tiff_encoder.py").read_text(encoding="utf-8")
    src_rec = (REPO / "jxl_recompressor.py").read_text(encoding="utf-8")
    val_enc = pat.search(src_enc).group(1)
    val_rec = pat.search(src_rec).group(1)
    # Equal, but NOT pinned to a value: it is a user setting, and editing it at
    # the top of both scripts must not break the suite.
    assert val_enc == val_rec
    pat = re.compile(r"^WORKER_MEMORY_LIMIT = (.+)$", re.M)
    assert pat.search(src_enc).group(1) == pat.search(src_rec).group(1)
