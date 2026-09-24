#!/usr/bin/env python3
"""The lossy distance floor depends on the installed cjxl.

libjxl 0.12 (PR #4238, "stay within Level 5") clamps every lossy distance
below 0.05 to the same output. cjxl 0.11.2 did not: measured on two real
16-bit photos, only 0.005 and 0.01 were identical there, and d=0.01 bought
+8.7 dB PSNR over d=0.05 at 1.75x the size. A fixed 0.05 floor told 0.11
users that --distance 0.02 buys nothing (false) and made the recompressor
treat a 0.05 request over a real d=0.02 source as "the same distance".
"""

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_jpeg_transcoder as tr
import jxl_recompressor as rec
import jxl_tiff_encoder as enc

SCRIPTS = [enc, rec, tr]


@pytest.mark.parametrize("mod", SCRIPTS, ids=lambda m: m.__name__)
@pytest.mark.parametrize("version,floor", [
    ((0, 12, 0), 0.05),
    ((0, 13, 1), 0.05),
    ((1, 0, 0), 0.05),
    ((0, 11, 2), 0.01),
    ((0, 10, 5), 0.01),
    (None, 0.05),            # unknown -> current behaviour
])
def test_floor_follows_the_cjxl_version(monkeypatch, mod, version, floor):
    monkeypatch.setattr(mod, "_tool_version", lambda exe: version)
    assert mod._min_effective_distance("cjxl") == floor


class _Log:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, *a, **k):
        self.warnings.append(str(msg))

    def __getattr__(self, name):
        return lambda *a, **k: None


@pytest.mark.parametrize("mod", [rec, tr], ids=lambda m: m.__name__)
def test_warning_uses_the_given_floor(monkeypatch, mod):
    log = _Log()
    monkeypatch.setattr(mod, "logger", log)
    mod._warn_distance_clamp(0.02, 0.01)          # cjxl 0.11: 0.02 is a real step
    assert log.warnings == []
    mod._warn_distance_clamp(0.02, 0.05)          # cjxl 0.12: same file as 0.05
    assert len(log.warnings) == 1 and "0.05" in log.warnings[0]
    mod._warn_distance_clamp(0.005, 0.01)         # below either floor
    assert len(log.warnings) == 2 and "0.01" in log.warnings[1]


def test_classify_uses_the_installed_floor():
    """A d=0.05 request over a d=0.02 source is a real, smaller target under
    cjxl 0.11 ('ok'), and the same file under cjxl 0.12 ('downgrade')."""
    assert rec._classify((0.02, 7), 0.05, 7, floor=0.01)[0] == "ok"
    assert rec._classify((0.02, 7), 0.05, 7, floor=0.05)[0] == "downgrade"
    # the default stays the current behaviour
    assert rec._classify((0.02, 7), 0.05, 7)[0] == "downgrade"
    # below the 0.11 floor both collapse again
    assert rec._classify((0.005, 7), 0.01, 7, floor=0.01)[0] == "downgrade"


def _noise_png(path: Path):
    np = pytest.importorskip("numpy")
    imagecodecs = pytest.importorskip("imagecodecs")
    rng = np.random.default_rng(7)
    x = np.linspace(0, 1, 160)
    yy, xx = np.meshgrid(x, x, indexing="ij")
    base = np.stack([xx, yy, (xx + yy) / 2], axis=2) * 50000
    arr = np.clip(base + rng.normal(0, 900, base.shape), 0, 65535).astype("uint16")
    path.write_bytes(imagecodecs.png_encode(arr))


@pytest.mark.skipif(shutil.which("cjxl") is None, reason="needs cjxl on PATH")
def test_detected_floor_matches_the_installed_cjxl(tmp_path):
    """The floor the scripts derive from `cjxl --version` must be what the
    binary actually does: half the floor and the floor encode to the same
    bytes, and a distance just above the floor does not."""
    png = tmp_path / "in.png"
    _noise_png(png)
    floor = enc._min_effective_distance("cjxl")

    def encode(d):
        out = tmp_path / f"d{d}.jxl"
        subprocess.run(["cjxl", str(png), str(out), "-d", str(d), "-e", "7"],
                       check=True, capture_output=True)
        return hashlib.md5(out.read_bytes()).hexdigest()

    assert encode(floor / 2) == encode(floor), \
        f"cjxl does not clamp below {floor}: the detected floor is wrong"
    assert encode(round(floor * 1.4, 4)) != encode(floor), \
        f"cjxl clamps above {floor}: the detected floor is too low"
