#!/usr/bin/env python3
"""Regressions for the advisory space preflight.

The estimate is MEASURED, not guessed: a few 2048 crops of the batch's own
files are encoded at the run's own settings. That choice is backed by 130
full-size encodes of real 16-bit photos across 13 distances:

  * a 2048 crop reproduces its own full file's ratio within 3% (d=0 .. 1.0),
    so the sample can be cheap;
  * a synthetic probe matched real photos only inside one narrow distance band
    and was off by 4x outside it;
  * fitting a curve on one photo and rescaling it to another broke on 1 of 4
    photos (38% error), so there is no universal shape to lean on.

Two properties matter more than accuracy, and are what these tests pin:
  * it must never BLOCK -- a projection that refuses a run would eventually
    refuse one that fits (what stops a run is the evidence-based disk-full
    abort, tested in test_disk_full_abort.py);
  * it must never kill a run by raising -- an advisory check taking down a real
    batch would be worse than having no check at all.
"""

import collections
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_tiff_encoder as enc

Usage = collections.namedtuple("usage", "total used free")
GB = 1024 ** 3
MB = 1024 ** 2


@pytest.fixture
def small_threshold(monkeypatch):
    """Exercise the "large batch" arithmetic without allocating real GB.

    truncate() on NTFS still reserves the space, which made this file the
    slowest in the suite. The threshold is a constant; scaling it and the
    fixtures together tests identical logic.
    """
    monkeypatch.setattr(enc, "_PREFLIGHT_MIN_BYTES", 5 * MB)


def _items(src_dir, dest, sizes, tag=""):
    """Fake planning items: (tiff, final_jxl, page, thumb, subfiletype, samples).

    truncate() rather than writing real bytes: the projection only reads
    st_size, and materialising gigabytes of zeros would cost RAM and disk to
    prove nothing.
    """
    src_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for i, n in enumerate(sizes):
        p = src_dir / f"{tag}f{i}.tif"
        with open(p, "wb") as fh:
            fh.truncate(n)
        out.append((p, dest / f"{tag}f{i}.jxl", 0, False, 0, 3))
    return out


def _peak_bytes(line):
    m = re.search(r"peak ~([\d.]+) (KB|MB|GB|TB)", line)
    return float(m.group(1)) * {"KB": 1024, "MB": MB, "GB": GB, "TB": 1024**4}[m.group(2)]


def _needs_bytes(line):
    m = re.search(r"needs ~([\d.]+) (KB|MB|GB|TB)", line)
    return float(m.group(1)) * {"KB": 1024, "MB": MB, "GB": GB, "TB": 1024**4}[m.group(2)]


def _capture(monkeypatch):
    lines = []
    monkeypatch.setattr(enc.logger, "info", lambda m, *a: lines.append(str(m)))
    monkeypatch.setattr(enc.logger, "warning", lambda m, *a: lines.append(str(m)))
    return lines


# --------------------------------------------------------------------------
# The measured distance floor
# --------------------------------------------------------------------------

def test_min_effective_distance_matches_what_cjxl_does():
    """cjxl was measured emitting BYTE-IDENTICAL output for --distance 0.005
    through 0.05 (20,188,082 bytes on a real 16-bit photo at every one of
    them). Projecting from a requested 0.01 would model a file cjxl will never
    write."""
    assert enc._MIN_EFFECTIVE_DISTANCE == 0.05


# --------------------------------------------------------------------------
# Sampling
# --------------------------------------------------------------------------

def test_worst_sample_wins_not_the_mean(tmp_path, monkeypatch):
    """Photos in one batch spread 1.1x-2.1x apart depending on distance. An
    estimate built on the average under-promises space for half the batch."""
    for i in range(3):
        (tmp_path / f"f{i}.tif").write_bytes(b"x" * (1000 * (i + 1)))
    seen = iter([0.10, 0.50, 0.20])
    monkeypatch.setattr(enc, "_sample_one_ratio", lambda p, d, e: next(seen))

    worst, samples = enc._measure_batch_ratio(sorted(tmp_path.glob("*.tif")), 0.1, 7)

    assert worst == 0.50
    assert len(samples) == 3


def test_samples_span_the_size_range(tmp_path, monkeypatch):
    """Taking the first N files would sample one corner of the batch."""
    for i in range(20):
        (tmp_path / f"f{i:02d}.tif").write_bytes(b"x" * (1000 * (i + 1)))
    picked = []
    monkeypatch.setattr(enc, "_sample_one_ratio",
                        lambda p, d, e: (picked.append(p.stat().st_size), 0.2)[1])

    enc._measure_batch_ratio(sorted(tmp_path.glob("*.tif")), 0.1, 7)

    assert min(picked) == 1000, "smallest file was not sampled"
    assert max(picked) == 20000, "largest file was not sampled"


def test_unmeasurable_batch_reports_nothing(tmp_path, monkeypatch):
    (tmp_path / "a.tif").write_bytes(b"x" * 1000)
    monkeypatch.setattr(enc, "_sample_one_ratio", lambda p, d, e: None)

    worst, samples = enc._measure_batch_ratio([tmp_path / "a.tif"], 0.1, 7)

    assert worst is None and samples == []


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------

def test_small_batch_is_not_probed(tmp_path, monkeypatch):
    """A ~15s probe on a two-minute job is a visible tax for no benefit."""
    called = []
    monkeypatch.setattr(enc, "_measure_batch_ratio",
                        lambda *a, **k: (called.append(1), (0.2, []))[1])
    dest = tmp_path / "out"
    groups = {dest: _items(tmp_path / "src", dest, [1024, 2048])}

    enc._preflight_space(groups, 0.1, 7, None)

    assert called == []


def test_large_batch_warns_when_it_will_not_fit(tmp_path, monkeypatch, small_threshold):
    monkeypatch.setattr(enc, "_measure_batch_ratio", lambda *a, **k: (0.5, [("f0.tif", 0.5)]))
    monkeypatch.setattr(enc.shutil, "disk_usage", lambda p: Usage(100 * MB, 99 * MB, 1 * MB))
    lines = _capture(monkeypatch)
    dest = tmp_path / "out"
    # 6 MB in, ratio 0.5 -> ~3 MB needed, against 1 MB free
    groups = {dest: _items(tmp_path / "src", dest, [6 * MB])}

    enc._preflight_space(groups, 0.1, 7, str(tmp_path))

    text = "\n".join(lines)
    assert "NOT ENOUGH" in text
    assert "staging" in text and "destination" in text


def test_large_batch_stays_quiet_when_it_fits(tmp_path, monkeypatch, small_threshold):
    monkeypatch.setattr(enc, "_measure_batch_ratio", lambda *a, **k: (0.2, [("f0.tif", 0.2)]))
    monkeypatch.setattr(enc.shutil, "disk_usage", lambda p: Usage(900 * MB, 0, 800 * MB))
    lines = _capture(monkeypatch)
    dest = tmp_path / "out"
    groups = {dest: _items(tmp_path / "src", dest, [6 * MB])}

    enc._preflight_space(groups, 0.1, 7, str(tmp_path))

    assert "NOT ENOUGH" not in "\n".join(lines)


def test_staging_peak_uses_two_groups_not_the_whole_batch(tmp_path, monkeypatch, small_threshold):
    """Staging drains per destination folder, so summing every folder would
    demand space for output that is never co-resident -- and would refuse runs
    that fit. Two folders, not one, because the pool no longer drains at folder
    boundaries and a second folder can start before the first flushes."""
    monkeypatch.setattr(enc, "_measure_batch_ratio", lambda *a, **k: (1.0, []))
    monkeypatch.setattr(enc.shutil, "disk_usage", lambda p: Usage(900 * MB, 0, 800 * MB))
    lines = _capture(monkeypatch)

    groups = {}
    for i, size in enumerate([4 * MB, 3 * MB, 2 * MB, 1 * MB]):
        dest = tmp_path / f"out{i}"
        groups[dest] = _items(tmp_path / f"src{i}", dest, [size], tag=f"g{i}_")

    enc._preflight_space(groups, 0.1, 7, str(tmp_path))

    line = next((c for c in lines if "staging" in c), "")
    # The two largest groups (4 + 3), never all four (10) and never just one.
    assert abs(_peak_bytes(line) - 7 * MB) < MB * 0.1, f"expected 4+3 MB, got: {line}"


def test_multipage_source_is_counted_once(tmp_path, monkeypatch, small_threshold):
    """A split multi-page TIFF appears once per page in the plan, but its bytes
    exist once -- counting per item would inflate the estimate per page."""
    monkeypatch.setattr(enc, "_measure_batch_ratio", lambda *a, **k: (1.0, []))
    monkeypatch.setattr(enc.shutil, "disk_usage", lambda p: Usage(900 * MB, 0, 800 * MB))
    lines = _capture(monkeypatch)

    src = tmp_path / "src"
    src.mkdir()
    tif = src / "multi.tif"
    with open(tif, "wb") as fh:
        fh.truncate(6 * MB)
    dest = tmp_path / "out"
    groups = {dest: [(tif, dest / f"multi_page{i}.jxl", i, False, 0, 3) for i in range(3)]}

    enc._preflight_space(groups, 0.1, 7, None)

    line = next((c for c in lines if "destination" in c), "")
    assert abs(_needs_bytes(line) - 6 * MB) < MB * 0.1, \
        f"3 pages inflated a 6 MB source: {line}"


def test_preflight_failure_cannot_kill_the_run():
    """The call site in main() must swallow anything the estimate throws."""
    src = Path(enc.__file__).read_text(encoding="utf-8")
    call = "_preflight_space(groups, CJXL_DISTANCE"
    assert call in src, "preflight call site not found"
    idx = src.index(call)
    assert "except Exception" in src[idx:idx + 400], \
        "main() must swallow preflight failures"
