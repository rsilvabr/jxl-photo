#!/usr/bin/env python3
"""Regressions for the directory-scan progress report.

Walking a large tree on a slow or network drive costs real time -- measured at
25s for 3312 files on an external drive with a cold OS cache -- and the run
printed NOTHING between "Input: ..." and "Files found: N". Twenty-five silent
seconds reads as a freeze, and the natural reaction is to kill the run.

(The page analysis that follows was never the problem: it already reports as it
goes, and it was only slow on the same cold cache -- 38.8s cold against 1.6s
warm, at identical worker counts.)

The two properties that matter: it must stay SILENT on a fast scan, or every
small run grows noise; and it must not read the clock once per directory entry,
since it runs for every file on the volume rather than only the matches.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_tiff_encoder as enc
import jxl_tiff_decoder as dec
import jxl_jpeg_transcoder as tr

BACKENDS = pytest.mark.parametrize(
    "mod", [enc, dec, tr], ids=["encoder", "decoder", "transcoder"])


@pytest.fixture
def logged(monkeypatch):
    def grab(mod):
        lines = []
        monkeypatch.setattr(mod.logger, "info", lambda m, *a: lines.append(str(m)))
        return lines
    return grab


@BACKENDS
def test_a_fast_scan_says_nothing(mod, logged):
    """A small local folder must not gain three lines of progress noise."""
    lines = logged(mod)
    st = mod._scan_state("C:/photos")
    for _ in range(10000):
        mod._scan_tick(st, 0)
    mod._scan_done(st, 0)

    assert lines == []


@BACKENDS
def test_a_slow_scan_announces_itself(mod, logged, monkeypatch):
    """The point of the whole thing: say 'still working' before it looks dead."""
    lines = logged(mod)
    clock = iter([1000.0] + [1000.0 + 99.0] * 50)
    monkeypatch.setattr(mod.time, "monotonic", lambda: next(clock))

    st = mod._scan_state("G:/2025")
    for _ in range(mod._SCAN_CHECK_INTERVAL):
        mod._scan_tick(st, 7)

    assert any("Searching for files under G:/2025" in x for x in lines), lines
    assert any("7 match(es) so far" in x for x in lines), lines


@BACKENDS
def test_done_is_silent_when_nothing_was_announced(mod, logged):
    """No opening line means no closing line -- otherwise a fast scan still
    prints a summary nobody needed."""
    lines = logged(mod)
    st = mod._scan_state("C:/photos")
    mod._scan_done(st, 12)

    assert lines == []


@BACKENDS
def test_done_closes_a_report_it_opened(mod, logged, monkeypatch):
    lines = logged(mod)
    clock = iter([1000.0] + [1100.0] * 60)
    monkeypatch.setattr(mod.time, "monotonic", lambda: next(clock))

    st = mod._scan_state("G:/2025")
    for _ in range(mod._SCAN_CHECK_INTERVAL):
        mod._scan_tick(st, 3)
    mod._scan_done(st, 3)

    assert any("Scan finished" in x for x in lines), lines


@BACKENDS
def test_reports_back_off_instead_of_growing_without_bound(mod, logged, monkeypatch):
    """A fixed gap has no ceiling on line count. At 3s flat, a five-minute
    network scan produced 100 progress lines -- the same wall of noise the
    reporter exists to avoid, wearing a different hat. Doubling the gap keeps a
    five-minute scan near 8 lines while still proving the run is alive."""
    lines = logged(mod)
    clock = iter([1000.0 + i for i in range(400)])   # 1s per 512-entry block
    monkeypatch.setattr(mod.time, "monotonic", lambda: next(clock))

    st = mod._scan_state("Z:/slow")
    for i in range(300 * mod._SCAN_CHECK_INTERVAL):
        mod._scan_tick(st, i // 100)

    progress = [x for x in lines if "Scanned" in x]
    assert 4 <= len(progress) <= 12, f"{len(progress)} lines over 5 minutes"


@BACKENDS
def test_the_gap_is_capped(mod):
    """Backing off forever would eventually go quiet for so long that a live
    run looks dead again."""
    assert mod._SCAN_REPORT_MAX <= 120.0


@BACKENDS
def test_the_clock_is_not_read_every_entry(mod, monkeypatch):
    """This runs once per entry on the VOLUME, not once per match: a syscall
    per entry would tax the very scan it is reporting on."""
    reads = []
    real = time.monotonic
    monkeypatch.setattr(mod.time, "monotonic", lambda: (reads.append(1), real())[1])

    st = mod._scan_state("C:/photos")
    reads.clear()
    for _ in range(5000):
        mod._scan_tick(st, 0)

    assert len(reads) <= 5000 // mod._SCAN_CHECK_INTERVAL + 1, len(reads)


@BACKENDS
def test_state_starts_clean(mod):
    st = mod._scan_state("C:/x")
    assert st["scanned"] == 0
    assert st["announced"] is False
    assert st["root"] == "C:/x"


@BACKENDS
def test_a_missing_root_does_not_crash(mod, logged, monkeypatch):
    """Callers that have no root path still have to be able to report."""
    lines = logged(mod)
    clock = iter([1000.0] + [1100.0] * 60)
    monkeypatch.setattr(mod.time, "monotonic", lambda: next(clock))

    st = mod._scan_state(None)
    for _ in range(mod._SCAN_CHECK_INTERVAL):
        mod._scan_tick(st, 1)

    assert any("Searching for files" in x for x in lines)


def test_finders_pass_their_root_through(tmp_path):
    """Without the root the announcement cannot name the folder being scanned."""
    (tmp_path / "a.tif").write_bytes(b"x")
    src = Path(enc.__file__).read_text(encoding="utf-8")
    assert '_iter_tiffs(input_path.glob("*"), input_path)' in src
    assert '_iter_tiffs(input_path.rglob("*"), input_path)' in src
    # and it still returns what it always did
    assert [p.name for p in enc.find_files_mode0(tmp_path)] == ["a.tif"]
