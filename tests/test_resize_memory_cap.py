#!/usr/bin/env python3
"""The recompressor's memory cap counts what cjxl ENCODES (review 261013).

A resized derivative decodes the full master but encodes the small copy: the
MOBILE preset (2048 px long edge, d=1 e=9) was capped as if cjxl encoded the
45 MP source — ~11 GB per cjxl, a handful of workers — while the real encode is
~3 MP. The side steps (djxl, the magick recipe) still decode the full size, so
only the cjxl estimate shrinks.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_recompressor as rec

GIB = 2 ** 30
MP45 = 8256 * 5504

_STATE = ("DERIVATIVE", "RESIZE_MODE", "RESIZE_VALUE", "ALLOW_UPSCALE")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(rec, "_available_physical_bytes", lambda: 10 ** 15)
    monkeypatch.setattr(rec, "_available_commit_bytes", lambda: 42 * GIB)
    monkeypatch.setattr(rec, "WORKER_MEMORY_LIMIT", "both")
    monkeypatch.setattr(rec, "WORKER_MEMORY_FRACTION", 1.0)
    monkeypatch.setattr(rec, "VERIFY_ROUNDTRIP", False)
    saved = {n: getattr(rec, n) for n in _STATE}
    yield
    for n, v in saved.items():
        setattr(rec, n, v)


def _resize_long(value, upscale=False):
    rec.DERIVATIVE = True
    rec.RESIZE_MODE, rec.RESIZE_VALUE, rec.ALLOW_UPSCALE = "long", value, upscale


def test_largest_encode_pixels_of_a_2048_copy():
    _resize_long(2048)
    items = [{"dims": (8256, 5504)}, {"dims": (5504, 8256)}, {"dims": (6000, 4000)}]
    assert rec._largest_encode_pixels(items) == 2048 * 1365


def test_largest_encode_pixels_falls_back_to_the_source():
    _resize_long(2048)
    # a small source is never upscaled: it is encoded at its own size
    assert rec._largest_encode_pixels([{"dims": (1600, 1200)}]) == 1600 * 1200
    # unknown dimensions: no smaller estimate (fail closed)
    assert rec._largest_encode_pixels([{"dims": (8256, 5504)}, {"dims": (0, 0)}]) is None
    assert rec._largest_encode_pixels([{"pixels": MP45}]) is None
    # not a resize: the source size is the answer
    rec.RESIZE_MODE = None
    assert rec._largest_encode_pixels([{"dims": (8256, 5504)}]) is None
    rec.DERIVATIVE, rec.RESIZE_MODE = False, "long"
    assert rec._largest_encode_pixels([{"dims": (8256, 5504)}]) is None


def test_a_small_encode_buys_more_workers():
    full = rec._memory_capped_pipeline(30, MP45, 1.0, 9)
    small = rec._memory_capped_pipeline(30, MP45, 1.0, 9, cjxl_pixels=2048 * 1365)
    assert small[0] > full[0], (full, small)
    assert small[1] > full[1], (full, small)


def test_cjxl_pixels_never_raises_the_estimate():
    """A cjxl_pixels above the source (a bad caller) is clamped to the source."""
    assert (rec._memory_capped_pipeline(30, MP45, 1.0, 9, cjxl_pixels=10 * MP45)
            == rec._memory_capped_pipeline(30, MP45, 1.0, 9))


def test_without_cjxl_pixels_nothing_changes():
    assert (rec._memory_capped_pipeline(30, MP45, 1.0, 9, cjxl_pixels=None)
            == rec._memory_capped_pipeline(30, MP45, 1.0, 9))


@pytest.mark.skipif(not all(shutil.which(t) for t in ("cjxl", "exiftool")),
                    reason="needs cjxl and exiftool")
def test_the_planning_read_keeps_width_and_height(tmp_path):
    from PIL import Image
    png = tmp_path / "s.png"
    Image.fromarray((np.random.default_rng(1).random((48, 64, 3)) * 255)
                    .astype("uint8")).save(png)
    jxl = tmp_path / "s.jxl"
    subprocess.run(["cjxl", str(png), str(jxl), "-d", "1", "--container=1"],
                   check=True, capture_output=True)
    info = rec._read_encode_params_batch([jxl])[str(jxl)]
    assert (info["width"], info["height"], info["pixels"]) == (64, 48, 64 * 48)
