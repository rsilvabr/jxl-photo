#!/usr/bin/env python3
"""`--provenance adopt` and the adopt scan (bugs #271, #272).

Round 27's guard refused any output written before the markers existed — which
is EVERY archive anyone already owns. Failing closed was right; having no way
forward was not.

`adopt` resolves only the "I cannot tell" case, and it PROVES the pairing rather
than assuming it: each unmarked output is decoded and compared with the source
(the adopt scan, on by default), then STAMPED, so the archive heals in one pass
and the strict check applies from the next run on. A marker that MISMATCHES is
still refused — adopt never relaxes "I can tell it is wrong".

--no-adopt-scan trades the proof for speed, loudly and per file.

#272: a refusal is a failure. It was dropped from the run and never reached the
exit code, so a scheduled job saw a clean run.
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent
ENCODER = str(REPO / "jxl_tiff_encoder.py")

ARCHIVE = ["--mode", "5", "--distance", "0", "--delete-source",
           "--delete-confirm-off"]


def _tiff(path: Path, value: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.full((48, 48, 3), value, np.uint16),
                     photometric="rgb")


def _run(*args, cwd):
    return subprocess.run([sys.executable, ENCODER, *args], capture_output=True,
                          text=True, timeout=600, cwd=str(cwd),
                          stdin=subprocess.DEVNULL)


def _strip_markers(folder: Path):
    """Turn a fresh archive into a LEGACY one: no provenance at all."""
    for j in folder.glob("*.jxl"):
        subprocess.run(["exiftool", "-overwrite_original", "-XMP-dc:Relation=", str(j)],
                       capture_output=True, timeout=60)


def _legacy_archive(tmp_path, value=1000, name="foto.tif"):
    """An archive that predates the markers, with its source still in place."""
    _tiff(tmp_path / "root" / "A" / name, value)
    r = _run("root", "--mode", "5", "--distance", "0", cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    _strip_markers(tmp_path / "root" / "JXL_16bits")
    return tmp_path / "root" / "A" / name


# ---------------------------------------------------------------------------
# #271 — a legacy archive must have a way forward
# ---------------------------------------------------------------------------

def test_legacy_archive_is_refused_by_default(tmp_path):
    """The strict modes still refuse it — and now say what to do about it."""
    _legacy_archive(tmp_path)
    r = _run("root", *ARCHIVE, "--delete-skipped", cwd=tmp_path)
    assert "REFUSING" in r.stdout
    assert "--provenance adopt" in r.stdout, "the way forward must be named"
    assert (tmp_path / "root" / "A" / "foto.tif").exists()


def test_adopt_accepts_a_legacy_archive_and_stamps_it(tmp_path):
    src = _legacy_archive(tmp_path)
    r = _run("root", *ARCHIVE, "--delete-skipped", "--provenance", "adopt", cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    assert "verified by the adopt scan" in r.stdout
    assert not src.exists(), "the adopted source was not deleted"

    # ...and the archive healed: the marker is there now
    out = tmp_path / "root" / "JXL_16bits" / "foto.jxl"
    marks = enc._read_source_markers_batch([out])[str(out)]
    assert marks["src"] and marks["srcsum"], "adoption did not stamp the output"


def test_adopting_heals_so_the_next_run_is_strict(tmp_path):
    """One pass, not a permanent hole: after adopting, a DIFFERENT source with
    the same name is refused by the ordinary check."""
    _legacy_archive(tmp_path)
    assert _run("root", *ARCHIVE, "--delete-skipped", "--provenance", "adopt",
                cwd=tmp_path).returncode == 0

    _tiff(tmp_path / "root" / "B" / "foto.tif", 60000)
    r = _run("root", *ARCHIVE, "--delete-skipped", "--provenance", "adopt", cwd=tmp_path)
    assert "REFUSING" in r.stdout, r.stdout
    assert "different source" in r.stdout
    assert (tmp_path / "root" / "B" / "foto.tif").exists()


# ---------------------------------------------------------------------------
# the scan is what makes adoption safe
# ---------------------------------------------------------------------------

def test_adopt_scan_refuses_a_wrong_legacy_pairing(tmp_path):
    """A legacy output whose source is NOT the file that made it. Only the scan
    can tell — there is no marker to compare."""
    _legacy_archive(tmp_path)
    import shutil
    shutil.rmtree(tmp_path / "root" / "A")
    _tiff(tmp_path / "root" / "B" / "foto.tif", 60000)      # different photo

    r = _run("root", *ARCHIVE, "--delete-skipped", "--provenance", "adopt", cwd=tmp_path)
    assert "REFUSING" in r.stdout
    assert "adopt scan" in r.stdout
    assert (tmp_path / "root" / "B" / "foto.tif").exists(), "a wrong pairing was adopted"


def test_no_adopt_scan_trusts_the_pairing_loudly(tmp_path):
    """The escape hatch: fast, unproven, and it says so per file."""
    _legacy_archive(tmp_path)
    import shutil
    shutil.rmtree(tmp_path / "root" / "A")
    _tiff(tmp_path / "root" / "B" / "foto.tif", 60000)

    r = _run("root", *ARCHIVE, "--delete-skipped", "--provenance", "adopt",
             "--no-adopt-scan", cwd=tmp_path)
    assert "ADOPTED without proof" in r.stdout
    assert not (tmp_path / "root" / "B" / "foto.tif").exists()


def test_adopt_scan_is_on_by_default():
    assert enc.ADOPT_SCAN is True
    parser_default = enc.PROVENANCE_CHECK
    assert parser_default == "path", "adopt must be opt-in, not the default mode"


def test_adopt_does_not_relax_a_mismatching_marker(tmp_path):
    """adopt only covers 'no marker'. A marker that disagrees is a real
    conflict and stays refused."""
    _tiff(tmp_path / "root" / "A" / "foto.tif", 1000)
    assert _run("root", "--mode", "5", "--distance", "0", cwd=tmp_path).returncode == 0
    import shutil
    shutil.rmtree(tmp_path / "root" / "A")
    _tiff(tmp_path / "root" / "B" / "foto.tif", 60000)

    r = _run("root", *ARCHIVE, "--delete-skipped", "--provenance", "adopt", cwd=tmp_path)
    assert "REFUSING" in r.stdout
    assert "different source" in r.stdout, "a mismatching marker must not be adopted"
    assert (tmp_path / "root" / "B" / "foto.tif").exists()


# ---------------------------------------------------------------------------
# #272 — a refusal is a failure
# ---------------------------------------------------------------------------

def test_refusal_exits_non_zero(tmp_path):
    """A scheduled run must be able to tell that something was refused."""
    _legacy_archive(tmp_path)
    r = _run("root", *ARCHIVE, "--delete-skipped", cwd=tmp_path)
    assert r.returncode == 1, f"refusal exited {r.returncode}"


def test_refusal_reaches_the_summary_failures(tmp_path):
    """...and the wrapper's manifest recap, which reads this list."""
    import json
    _legacy_archive(tmp_path)
    r = _run("root", *ARCHIVE, "--delete-skipped", "--summary-json", cwd=tmp_path)
    line = [l for l in r.stdout.splitlines() if l.startswith("##JXLSUM## ")][-1]
    payload = json.loads(line[len("##JXLSUM## "):])
    assert payload["errors"] >= 1
    assert any("refused" in f["reason"] for f in payload["failures"])


def test_a_clean_run_still_exits_zero(tmp_path):
    """No over-fix: nothing refused, nothing wrong."""
    _tiff(tmp_path / "root" / "A" / "foto.tif", 1000)
    r = _run("root", *ARCHIVE, cwd=tmp_path)
    assert r.returncode == 0, r.stdout
