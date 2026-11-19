#!/usr/bin/env python3
"""--verify-roundtrip WITHOUT --delete-source checks every page (v2.31.0).

Until v2.30.x the flag was only a gate in front of a deletion: typed alone it
logged "has no effect without --delete-source" and checked nothing.
photo-backup's encode command (its JP-2: `--mode 0 ... --workers 1
--verify-roundtrip`, never deleting) counted on that check. Now convert_one
decodes each page it writes and compares it with its TIFF page BEFORE the page
takes its final name (_VERIFY_EACH_PAGE); a page that fails, or cannot be
checked, is an error and leaves no JXL. A deleting run checks in its delete
gate only (never twice), and a run without the flag never decodes anything.

Real cjxl/djxl/exiftool, small synthetic images, every path hostile (Japanese,
[ ], a space, parentheses, `%`). Pre-fix proof: extract the HEAD encoder and
common module into one folder and point JXL_ENCODER_UNDER_TEST at it:

    git show HEAD:jxl_tiff_encoder.py > <tmp>/jxl_tiff_encoder.py
    git show HEAD:jxl_photo_common.py > <tmp>/jxl_photo_common.py
    $env:JXL_ENCODER_UNDER_TEST = "<tmp>/jxl_tiff_encoder.py"
    python -m pytest tests/test_verify_each_page.py

Every test but the dry-run one fails there.
"""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

_REPO = Path(__file__).resolve().parent.parent
_OVERRIDE = os.environ.get("JXL_ENCODER_UNDER_TEST")
sys.path.insert(0, str(Path(_OVERRIDE).resolve().parent) if _OVERRIDE else str(_REPO))
import jxl_tiff_encoder as enc  # noqa: E402

ENCODER = Path(_OVERRIDE).resolve() if _OVERRIDE else _REPO / "jxl_tiff_encoder.py"

pytestmark = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool")),
    reason="needs cjxl, djxl and exiftool on PATH")

# The owner's library has all of these in one path (AGENTS.md, Verification):
# "%off %d" because ImageMagick formats a name it writes like printf and
# exiftool expands %d/%f in a source name.
HOSTILE = "検証 [テスト] (2) 100%off %d"
NAME = "写真 [1] 50%.tif"


def _image(scale=1.0):
    """A small image with real structure (pure noise is destroyed by lossy).
    Larger than 160 px, so a second page is never a camera thumbnail (#642)."""
    y, x = np.mgrid[0:192, 0:256]
    base = (np.sin(x / 9.0) * 0.25 + np.cos(y / 11.0) * 0.25 + 0.5) * 60000 * scale
    return np.stack([base, base * 0.8, base * 0.6], axis=2).astype(np.uint16)


def _tiff(path: Path, pages: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tifffile.TiffWriter(str(path)) as w:
        for i in range(pages):
            w.write(_image(1.0 - 0.3 * i), photometric="rgb")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _setup(tmp_path):
    root = tmp_path / HOSTILE
    return _tiff(root / NAME), root / "out [JXL] 50%"


# photo-backup's encode command (JP-2), lossless and lossy.
LOSSLESS = ("--distance", "0", "--scan-distance", "0", "--eight-bit-distance", "0",
            "--effort", "7", "--workers", "1")
LOSSY = ("--distance", "1", "--scan-distance", "1", "--eight-bit-distance", "1",
         "--effort", "7", "--workers", "1")


def _main(monkeypatch, tmp_path, src, out, *flags):
    """One in-process main() (so a test can patch the module), its log read
    back: (exit code, log text)."""
    logs = tmp_path / f"logs_{len(list(tmp_path.glob('logs_*')))}"
    monkeypatch.setattr(enc, "LOG_DIR", logs)
    monkeypatch.setattr(sys, "argv", ["jxl_tiff_encoder.py", str(src), str(out),
                                      "--mode", "0", *map(str, flags)])
    try:
        enc.main()
        rc = 0
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in sorted(logs.glob("*.log")))
    return rc, text


def _counting(monkeypatch, replacement=None):
    """Count the calls of _verify_roundtrip_page (the real one, unless a
    replacement is given)."""
    calls = []
    real = enc._verify_roundtrip_page

    def fake(tiff_path, page_idx, jxl_path, distance):
        calls.append((Path(tiff_path).name, page_idx, distance))
        return (replacement or real)(tiff_path, page_idx, jxl_path, distance)

    monkeypatch.setattr(enc, "_verify_roundtrip_page", fake)
    return calls


def _subprocess(src, out, logs, *flags):
    r = subprocess.run(
        [sys.executable, str(ENCODER), str(src), str(out), "--mode", "0", *flags],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600, stdin=subprocess.DEVNULL,
        env={**os.environ, "JXLPHOTO_LOG_DIR": str(logs)})
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in sorted(Path(logs).rglob("*.log")))
    return r, text


# ---------------------------------------------------------------------------
# V1: the check runs, as photo-backup calls the encoder
# ---------------------------------------------------------------------------

def test_every_page_of_a_lossless_split_is_verified(tmp_path):
    root = tmp_path / HOSTILE
    src = _tiff(root / NAME, pages=2)
    before = _sha(src)
    out = root / "out [JXL] 50%"
    r, log = _subprocess(src, out, tmp_path / "logs", *LOSSLESS, "--verify-roundtrip")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no effect without --delete-source" not in r.stdout
    assert "Round-trip verification of every page written" in r.stdout
    assert len(list(out.glob("*.jxl"))) == 2, sorted(p.name for p in out.iterdir())
    assert "Round-trip verified page 0 (pixel-identical)" in log
    assert "Round-trip verified page 1 (pixel-identical)" in log
    assert _sha(src) == before, "the TIFF must never change (JP-8)"


def test_a_lossy_page_is_verified_by_brightness_and_psnr(tmp_path):
    src, out = _setup(tmp_path)
    r, log = _subprocess(src, out, tmp_path / "logs", *LOSSY, "--verify-roundtrip")
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(list(out.glob("*.jxl"))) == 1
    line = next((ln for ln in log.splitlines() if "Round-trip verified page 0" in ln), "")
    assert "mean ratio" in line and "PSNR" in line, log[-2000:]


# ---------------------------------------------------------------------------
# V2: a page that fails, or cannot be checked, gets no JXL
# ---------------------------------------------------------------------------

def test_a_failed_check_leaves_no_jxl_and_exits_1(tmp_path, monkeypatch):
    src, out = _setup(tmp_path)
    before = _sha(src)
    _counting(monkeypatch, lambda *a: (False, "test: pixels differ"))
    rc, log = _main(monkeypatch, tmp_path, src, out, *LOSSLESS, "--verify-roundtrip")
    assert rc == 1
    assert "round-trip verification FAILED on page 0: test: pixels differ" in log
    left = sorted(p.name for p in out.rglob("*")) if out.exists() else []
    assert not any(n.lower().endswith((".jxl", ".tmp")) for n in left), left
    assert _sha(src) == before


def test_a_check_that_cannot_run_fails_closed(tmp_path, monkeypatch):
    """"Could not tell" is a failure, never a pass: here djxl "fails"."""
    src, out = _setup(tmp_path)

    def broken(*a, **k):
        raise RuntimeError("djxl: simulated failure")

    monkeypatch.setattr(enc, "_decode_jxl_for_verify", broken)
    rc, log = _main(monkeypatch, tmp_path, src, out, *LOSSLESS, "--verify-roundtrip")
    assert rc == 1
    assert "could not verify: djxl: simulated failure" in log
    assert not (out.exists() and list(out.glob("*.jxl")))


def test_a_failed_check_never_replaces_an_existing_jxl(tmp_path, monkeypatch):
    """The check runs BEFORE the swap: an archive already there survives."""
    src, out = _setup(tmp_path)
    rc, _ = _main(monkeypatch, tmp_path, src, out, *LOSSLESS)
    assert rc == 0
    (jxl,) = list(out.glob("*.jxl"))
    kept = _sha(jxl)
    _counting(monkeypatch, lambda *a: (False, "test: pixels differ"))
    rc, log = _main(monkeypatch, tmp_path, src, out, *LOSSLESS, "--overwrite",
                    "--verify-roundtrip")
    assert rc == 1, log[-2000:]
    assert _sha(jxl) == kept, "a page that failed its check replaced the archive"
    assert [p.name for p in out.iterdir() if p.suffix.lower() in (".jxl", ".tmp")] \
        == [jxl.name], "a partial output was left behind"


# ---------------------------------------------------------------------------
# V3/V4: who checks, and how often
# ---------------------------------------------------------------------------

def test_without_the_flag_nothing_is_decoded_and_the_state_is_per_run(tmp_path, monkeypatch):
    src, out = _setup(tmp_path)
    calls = _counting(monkeypatch)
    rc, log = _main(monkeypatch, tmp_path, src, out, *LOSSLESS, "--verify-roundtrip")
    assert rc == 0, log[-2000:]
    assert [c[1] for c in calls] == [0]
    assert enc._VERIFY_EACH_PAGE is True

    def never(*a):
        raise AssertionError("a run without --verify-roundtrip decoded a page")

    calls2 = _counting(monkeypatch, never)
    rc, log = _main(monkeypatch, tmp_path, src, tmp_path / HOSTILE / "out2", *LOSSLESS)
    assert rc == 0, log[-2000:]
    assert calls2 == []
    assert enc._VERIFY_EACH_PAGE is False, "the first run's flag leaked into the second"


def test_a_deleting_run_checks_each_page_once_in_its_gate(tmp_path, monkeypatch):
    src, out = _setup(tmp_path)
    calls = _counting(monkeypatch)
    rc, log = _main(monkeypatch, tmp_path, src, out, *LOSSLESS, "--verify-roundtrip",
                    "--delete-source", "--delete-confirm-off")
    assert rc == 0, log[-2000:]
    assert [c[1] for c in calls] == [0], "decoded twice (convert_one and the gate)?"
    assert enc._VERIFY_EACH_PAGE is False
    assert len(list(out.glob("*.jxl"))) == 1
    assert not src.exists(), "the gate passed: the TIFF is deleted"


# ---------------------------------------------------------------------------
# What the check needs
# ---------------------------------------------------------------------------

def test_no_imagecodecs_still_checks_a_16_bit_page(tmp_path, monkeypatch):
    """The page comes back as a PAM in memory (#673): imagecodecs is not a
    requirement of the run (only of a 12-bit or float page's PNG route)."""
    monkeypatch.setitem(sys.modules, "imagecodecs", None)   # import fails
    src, out = _setup(tmp_path)
    rc, log = _main(monkeypatch, tmp_path, src, out, *LOSSLESS, "--verify-roundtrip")
    assert rc == 0, log[-2000:]
    assert "needs imagecodecs" not in log
    assert "Round-trip verified page 0 (pixel-identical)" in log
    assert len(list(out.glob("*.jxl"))) == 1


def _without_djxl(monkeypatch):
    real = shutil.which

    def which(cmd, *a, **k):
        if Path(str(cmd)).stem.lower() == "djxl":
            return None
        return real(cmd, *a, **k)

    monkeypatch.setattr(enc.shutil, "which", which)


def test_no_djxl_stops_the_run_before_any_file(tmp_path, monkeypatch):
    src, out = _setup(tmp_path)
    _without_djxl(monkeypatch)
    rc, log = _main(monkeypatch, tmp_path, src, out, *LOSSLESS, "--verify-roundtrip")
    assert rc == 1
    assert "This run needs djxl in PATH" in log
    assert not (out.exists() and list(out.iterdir()))


def test_a_dry_run_needs_no_djxl_and_checks_nothing(tmp_path, monkeypatch):
    src, out = _setup(tmp_path)
    _without_djxl(monkeypatch)
    calls = _counting(monkeypatch)
    rc, log = _main(monkeypatch, tmp_path, src, out, *LOSSLESS, "--verify-roundtrip",
                    "--dry-run")
    assert rc == 0, log[-2000:]
    assert calls == []
    assert not (out.exists() and list(out.glob("*.jxl")))
