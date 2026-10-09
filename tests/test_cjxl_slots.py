#!/usr/bin/env python3
"""cjxl slots: full-size encodes capped separately from the workers (261009).

The 2026-10-09 scheduled MOBILE run (recompressor, mode 7, --output-icc sRGB,
d=3 e=9, --workers 30) logged "workers 3" from the memory cap, yet only ~2.2
of the 3 cjxl ran on average: each worker spends about a third of its life in
the light steps (djxl, the magick recipe, exiftool, the checks) with its cjxl
slot idle. Plan AI_tools/261009_Claude_plan_cjxl-slots.md separates the two: a
semaphore caps the full-size cjxl (_run_cjxl_full) and the leftover memory
pays for extra workers that prepare the next files (_memory_capped_pipeline).

Tests 1-8 are mocked arithmetic/state; 9-10 are real-codec tests.
"""

import logging
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_recompressor as rec

GIB = 2 ** 30
MP45 = 45_400_000


def _jxl_stub(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)


@pytest.fixture(autouse=True)
def _fresh_slots(monkeypatch):
    """Physical RAM never the smaller reading unless a test says so, and no
    semaphore leaks from one test to the next."""
    monkeypatch.setattr(rec, "_available_physical_bytes", lambda: 10 ** 15)
    monkeypatch.setattr(rec, "WORKER_MEMORY_LIMIT", "both")
    monkeypatch.setattr(rec, "VERIFY_ROUNDTRIP", False)
    yield
    if hasattr(rec, "_set_cjxl_slots"):
        rec._set_cjxl_slots(None)


# main() resets the --output-icc/--resize-*/--sharpen run state at its TOP
# (OUTPUT_ICC through _RUN_DEFAULTS since #529), but leaves it set when it
# returns: a later test that calls convert_one/_derive_pixels directly would
# still see DERIVATIVE/_DERIVED_LABEL from the in-process main() of test 9.
_MAIN_STATE = (
    "OUTPUT_ICC", "_OUTPUT_ICC_LABEL", "_OUTPUT_ICC_BYTES", "_OUTPUT_ICC_PATH",
    "_SRGB_ICC_PATH", "_FORCE_REDERIVE", "RESIZE_MODE", "RESIZE_VALUE",
    "ALLOW_UPSCALE", "SHARPEN", "SHARPEN_SIGMA", "SHARPEN_GAIN",
    "SHARPEN_THRESHOLD", "DERIVATIVE", "_DERIVED_LABEL",
)


@pytest.fixture(autouse=True)
def _restore_main_state():
    saved = {name: getattr(rec, name) for name in _MAIN_STATE if hasattr(rec, name)}
    yield
    for name, value in saved.items():
        setattr(rec, name, value)


# (requested, pixels, distance, effort, avail, fraction, roundtrip, expected)
_TABLE = [
    (30, MP45, 3.0, 9, int(52.8 * GIB), 1.0, False, (6, 3)),
    (30, MP45, 3.0, 9, int(52.8 * GIB), 1.1, False, (6, 4)),
    (30, MP45, 3.0, 9, int(52.8 * GIB), 0.8, False, (3, 3)),
    (30, MP45, 3.0, 9, int(52.8 * GIB), 1.0, True, (3, 3)),
    (2, MP45, 3.0, 9, int(52.8 * GIB), 1.0, False, (2, 2)),
    (4, MP45, 3.0, 9, int(52.8 * GIB), 1.0, False, (4, 3)),
    (30, MP45, 3.0, 7, 40 * GIB, 0.8, False, (8, 8)),
    (30, MP45, 1.0, 7, 40 * GIB, 0.8, False, (18, 18)),
    (30, MP45, 3.0, 9, 40 * GIB, 0.8, False, (4, 2)),
    (30, MP45, 3.0, 9, 10 * GIB, 0.8, False, (1, 1)),
    (30, 0, 3.0, 9, 40 * GIB, 0.8, False, (2, 1)),
]


@pytest.mark.parametrize(
    "requested,pixels,distance,effort,avail,fraction,roundtrip,expected", _TABLE)
def test_pipeline_table(monkeypatch, requested, pixels, distance, effort, avail,
                        fraction, roundtrip, expected):
    monkeypatch.setattr(rec, "WORKER_MEMORY_FRACTION", fraction)
    monkeypatch.setattr(rec, "VERIFY_ROUNDTRIP", roundtrip)
    monkeypatch.setattr(rec, "_available_commit_bytes", lambda: avail)
    assert rec._memory_capped_pipeline(requested, pixels, distance, effort) == expected


@pytest.mark.parametrize("distance,effort", [(1.0, 7), (0.05, 9), (2.9, 7)])
def test_pipeline_matches_old_cap_when_streaming(monkeypatch, distance, effort):
    """Streaming: per_cjxl == per_side, so no extras and the count equals the
    old cap's (this change must not alter those runs)."""
    monkeypatch.setattr(rec, "WORKER_MEMORY_FRACTION", 0.8)
    monkeypatch.setattr(rec, "_available_commit_bytes", lambda: 40 * GIB)
    workers, slots = rec._memory_capped_pipeline(30, MP45, distance, effort)
    assert workers == rec._memory_capped_workers(30, MP45, distance, effort)
    assert slots == workers


@pytest.mark.parametrize(
    "requested,pixels,distance,effort,avail,fraction,roundtrip,expected",
    [row for row in _TABLE if row[7][0] > 1])
def test_pipeline_worst_case_fits(monkeypatch, requested, pixels, distance, effort,
                                  avail, fraction, roundtrip, expected):
    """Every slot at its cjxl peak plus every extra worker at its side-step
    peak stays within the fraction of the budget."""
    workers, slots = expected
    px = pixels or rec._UNKNOWN_IMAGE_PIXELS
    worst = (slots * px * rec._cjxl_bytes_per_pixel(distance, effort)
             + (workers - slots) * px * rec._SIDE_STEP_BYTES_PER_PIXEL)
    assert worst <= avail * fraction


def test_pipeline_log_and_warning(monkeypatch, caplog):
    monkeypatch.setattr(rec, "WORKER_MEMORY_FRACTION", 1.0)
    monkeypatch.setattr(rec, "_available_commit_bytes", lambda: int(52.8 * GIB))
    with caplog.at_level(logging.INFO, logger=rec.logger.name):
        rec._memory_capped_pipeline(30, MP45, 3.0, 9)
    assert "workers 6, cjxl at a time 3" in caplog.text
    assert "--workers 30 reduced to 6" in caplog.text
    assert "3 more worker(s) decode and convert" in caplog.text

    caplog.clear()
    monkeypatch.setattr(rec, "WORKER_MEMORY_FRACTION", 0.8)
    with caplog.at_level(logging.INFO, logger=rec.logger.name):
        rec._memory_capped_pipeline(30, MP45, 3.0, 9)
    assert "reduced to 3" in caplog.text
    assert "more worker(s)" not in caplog.text


def test_pipeline_disabled_and_unknown(monkeypatch, caplog):
    monkeypatch.setattr(rec, "WORKER_MEMORY_FRACTION", 0)

    def _boom():
        raise AssertionError("the cap read the machine's memory while disabled")

    monkeypatch.setattr(rec, "_available_commit_bytes", _boom)
    monkeypatch.setattr(rec, "_available_physical_bytes", _boom)
    assert rec._memory_capped_pipeline(30, MP45, 3.0, 9) == (30, None)

    monkeypatch.setattr(rec, "WORKER_MEMORY_FRACTION", 0.8)
    monkeypatch.setattr(rec, "_available_commit_bytes", lambda: None)
    monkeypatch.setattr(rec, "_available_physical_bytes", lambda: None)
    with caplog.at_level(logging.INFO, logger=rec.logger.name):
        assert rec._memory_capped_pipeline(30, MP45, 3.0, 9) == (30, None)
    assert "could not read" in caplog.text


def test_run_cjxl_full_limits_concurrency(monkeypatch):
    lock = threading.Lock()
    state = {"cur": 0, "max": 0}

    def fake(cmd, timeout, text=False, input=None):
        with lock:
            state["cur"] += 1
            state["max"] = max(state["max"], state["cur"])
        time.sleep(0.1)
        with lock:
            state["cur"] -= 1
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(rec, "_run_captured", fake)

    def six_threads():
        threads = [threading.Thread(target=rec._run_cjxl_full,
                                    args=(["cjxl", "x"],))
                   for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    rec._set_cjxl_slots(2)
    six_threads()
    assert state["max"] == 2

    rec._set_cjxl_slots(None)
    state["cur"], state["max"] = 0, 0
    six_threads()
    assert state["max"] > 2


def test_run_cjxl_full_releases_on_error(monkeypatch):
    """A failed encode must release its slot: three in a row cannot deadlock."""
    def fake(cmd, timeout, text=False, input=None):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(rec, "_run_captured", fake)
    rec._set_cjxl_slots(1)
    seen = []

    def call_three():
        for _ in range(3):
            with pytest.raises(subprocess.TimeoutExpired):
                rec._run_cjxl_full(["cjxl", "x"])
            seen.append(1)

    t = threading.Thread(target=call_three, daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive()
    assert seen == [1, 1, 1]


def _run_rec_main(monkeypatch, tmp_path, extra_argv, captured, avail=40 * GIB):
    """rec.main() driven with the process_group / exiftool doubles (copied from
    tests/test_memory_workers.py, not imported: fake_pg also records the cjxl
    slot state the pool runs with)."""
    src_dir = tmp_path / "in"
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "a.jxl"
    _jxl_stub(src)
    out_dir = tmp_path / "out"

    def fake_pg(items, workers):
        captured["workers"] = workers
        captured["slots"] = getattr(rec, "_CJXL_SLOTS_N", "missing")
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


def test_main_sets_and_clears_slots(tmp_path, monkeypatch):
    captured = {}
    _run_rec_main(monkeypatch, tmp_path, ["--distance", "3", "--effort", "9"],
                  captured)
    assert captured["workers"] == 4
    assert captured["slots"] == 2
    assert rec._CJXL_SEMAPHORE is None and rec._CJXL_SLOTS_N is None

    captured2 = {}
    _run_rec_main(monkeypatch, tmp_path / "second",
                  ["--distance", "1", "--effort", "7"], captured2)
    assert captured2["workers"] == 18
    assert captured2["slots"] is None
    assert rec._CJXL_SEMAPHORE is None and rec._CJXL_SLOTS_N is None


@pytest.mark.parametrize("extra_argv", [[], ["--output-icc", "sRGB"]],
                         ids=["plain", "derivative"])
def test_real_run_overlaps_side_steps_with_one_cjxl_slot(
        tmp_path, monkeypatch, caplog, extra_argv):
    """The whole point: with one cjxl slot the second worker prepares the next
    file while the slot is held, so a side step overlaps a full-size cjxl.

    Against the pre-fix cap (1 worker, everything sequential) no side step can
    ever overlap an encode. The fake memory gives the old cap 1 worker and the
    pipeline 2 workers with 1 slot.
    """
    for tool in ("cjxl", "djxl", "exiftool"):
        if shutil.which(tool) is None:
            pytest.skip(f"needs {tool}")
    if extra_argv and shutil.which("magick") is None:
        pytest.skip("needs magick")

    in_dir = tmp_path / "in"
    in_dir.mkdir()
    for i in range(3):
        rng = np.random.default_rng(i)
        y, x = np.mgrid[0:512, 0:512].astype(np.float32)
        base = np.stack([x / 512, y / 512, (x + y) / 1024], -1) * 50000 + 5000
        img = (base + rng.normal(0, 800, base.shape)).clip(0, 65535).astype(">u2")
        ppm = tmp_path / f"src{i}.ppm"
        ppm.write_bytes(b"P6\n512 512\n65535\n" + img.tobytes())
        r = subprocess.run(["cjxl", str(ppm), str(in_dir / f"src{i}.jxl"),
                            "-d", "0", "--effort", "1"], capture_output=True)
        assert r.returncode == 0, r.stderr

    out_dir = tmp_path / "out"
    avail = 512 * 512 * 440
    monkeypatch.setattr(rec, "_available_commit_bytes", lambda: avail)
    monkeypatch.setattr(rec, "_available_physical_bytes", lambda: avail)
    monkeypatch.setattr(rec, "WORKER_MEMORY_FRACTION", 1.0)
    monkeypatch.setattr(rec, "WORKER_MEMORY_LIMIT", "both")

    real = rec._run_captured
    lock = threading.Lock()
    state = {"cur": 0, "max": 0}
    intervals = []

    def spy(cmd, timeout, text=False, input=None):
        exe = Path(str(cmd[0])).stem.lower()
        full = exe == "cjxl" and "probe" not in str(cmd[1])
        t0 = time.monotonic()
        if full:
            with lock:
                state["cur"] += 1
                state["max"] = max(state["max"], state["cur"])
        try:
            return real(cmd, timeout, text=text, input=input)
        finally:
            if full:
                time.sleep(0.4)
                with lock:
                    state["cur"] -= 1
            intervals.append(("cjxl" if full else "side", t0, time.monotonic()))

    monkeypatch.setattr(rec, "_run_captured", spy)

    monkeypatch.setattr(
        sys, "argv",
        ["jxl_recompressor.py", str(in_dir), str(out_dir), "--mode", "2",
         "--workers", "8", "--distance", "1", "--effort", "9",
         "--on-unknown", "convert", "--on-regeneration", "convert",
         "--on-downgrade", "convert", "--no-preflight"] + list(extra_argv))
    try:
        rec.main()
    except SystemExit as e:
        if e.code not in (0, None):
            if extra_argv:
                pytest.skip("--output-icc sRGB not accepted in this combination "
                            f"(exit {e.code}): {caplog.text[-400:]}")
            raise

    assert state["max"] == 1, f"full cjxl ran {state['max']} at a time"
    full = [iv for iv in intervals if iv[0] == "cjxl"]
    side = [iv for iv in intervals if iv[0] == "side"]
    assert full and side, intervals
    assert any(a0 < c1 and c0 < a1
               for _k, a0, a1 in side for _j, c0, c1 in full), \
        f"no side step overlapped a full cjxl: {intervals}"

    outs = sorted(out_dir.rglob("*.jxl"))
    assert len(outs) == 3, outs
    for i, p in enumerate(outs):
        r = subprocess.run(["djxl", str(p), str(tmp_path / f"dec{i}.png")],
                           capture_output=True)
        assert r.returncode == 0, r.stderr


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


def test_side_steps_fit_their_estimate_real(tmp_path):
    """Each light step of a worker (djxl 16-bit PNG, djxl PFM, the magick ICC
    conversion) stays within _SIDE_STEP_BYTES_PER_PIXEL of the image size."""
    psutil = pytest.importorskip("psutil")
    for tool in ("cjxl", "djxl", "magick"):
        if shutil.which(tool) is None:
            pytest.skip(f"needs {tool}")

    rng = np.random.default_rng(0)
    y, x = np.mgrid[0:4096, 0:4096].astype(np.float32)
    base = np.stack([x / 4096, y / 4096, (x + y) / 8192], -1) * 50000 + 5000
    img = (base + rng.normal(0, 800, base.shape)).clip(0, 65535).astype(">u2")
    ppm = tmp_path / "in.ppm"
    ppm.write_bytes(b"P6\n4096 4096\n65535\n" + img.tobytes())
    src = tmp_path / "src.jxl"
    r = subprocess.run(["cjxl", str(ppm), str(src), "-d", "1", "--effort", "3"],
                       capture_output=True)
    assert r.returncode == 0, r.stderr

    dec_png = tmp_path / "dec.png"
    srgb_icc = tmp_path / "srgb.icc"
    conv_png = tmp_path / "conv.png"

    peak_png = _peak_private(
        ["djxl", str(src), str(dec_png), "--bits_per_sample=16",
         f"--icc_out={srgb_icc}"], psutil)
    peak_pfm = _peak_private(["djxl", str(src), str(tmp_path / "dec.pfm")],
                             psutil)
    peak_magick = _peak_private(
        ["magick", str(dec_png), "-profile", str(srgb_icc), "-intent",
         "Relative", "-black-point-compensation", "-profile", str(srgb_icc),
         "-depth", "16", "png:" + str(conv_png)], psutil)

    per_px = rec._SIDE_STEP_BYTES_PER_PIXEL * 4096 * 4096
    print(f"\nside-step peaks: djxl 16-bit PNG {peak_png / 2**20:.0f} MB "
          f"({peak_png / (4096 * 4096):.1f} B/px), djxl PFM "
          f"{peak_pfm / 2**20:.0f} MB ({peak_pfm / (4096 * 4096):.1f} B/px), "
          f"magick ICC {peak_magick / 2**20:.0f} MB "
          f"({peak_magick / (4096 * 4096):.1f} B/px)")
    assert peak_png <= per_px, f"djxl 16-bit PNG: {peak_png} > {per_px}"
    assert peak_pfm <= per_px, f"djxl PFM: {peak_pfm} > {per_px}"
    assert peak_magick <= per_px, f"magick: {peak_magick} > {per_px}"
