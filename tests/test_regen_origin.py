#!/usr/bin/env python3
"""Regeneration guard counts the pre-toolkit loss, and one provenance pair
(plan 261010, topic `regen-origin`).

U1-U3 are unit tests. R1-R5 run the real cjxl/djxl/exiftool/magick/jxlinfo:
the origin value comes from jxlinfo's header read and the marker from
exiftool, so a mocked tool would prove nothing.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_recompressor as rec

REPO = Path(__file__).resolve().parent.parent

_HAVE_TOOLS = all(shutil.which(t) for t in
                  ("cjxl", "djxl", "exiftool", "magick", "jxlinfo"))
real = pytest.mark.skipif(not _HAVE_TOOLS,
                          reason="needs cjxl, djxl, exiftool, magick and jxlinfo on PATH")


def _relation(path):
    r = subprocess.run(["exiftool", "-j", "-XMP-dc:Relation", str(path)],
                       capture_output=True, text=True, check=True)
    rel = json.loads(r.stdout)[0].get("Relation")
    return [] if rel is None else [str(v) for v in (rel if isinstance(rel, list) else [rel])]


def _origins(path):
    return [t for t in _relation(path) if t.startswith("jxlphoto-origin:")]


def _src_tokens(path):
    return [t for t in _relation(path) if t.startswith("jxlphoto-src:")]


def _srcsum_tokens(path):
    return [t for t in _relation(path) if t.startswith("jxlphoto-srcsum:")]


def _run(script, *args):
    r = subprocess.run([sys.executable, str(REPO / script)] + [str(a) for a in args],
                       capture_output=True, text=True, timeout=300,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, f"{script} failed:\n{r.stdout}{r.stderr}"
    return r


def _rgb_tiff(path: Path, dtype):
    import tifffile
    top = 255 if dtype == np.uint8 else 65535
    data = (np.random.default_rng(4).random((40, 60, 3)) * top).astype(dtype)
    tifffile.imwrite(str(path), data, photometric="rgb")


def _random_png(path: Path, seed: int):
    from PIL import Image
    Image.fromarray((np.random.default_rng(seed).random((64, 64, 3)) * 255)
                    .astype("uint8")).save(path)


def _set_meta(path: Path, *args):
    subprocess.run(["exiftool", "-q", "-overwrite_original", *map(str, args), str(path)],
                   check=True, capture_output=True)


def _foreign_jxl(in_dir: Path, png: Path, distance: str) -> Path:
    """A JXL no toolkit script wrote, at `distance` (0 = lossless)."""
    jxl = in_dir / "foreign.jxl"
    subprocess.run(["cjxl", str(png), str(jxl), "-d", distance, "--container=1"],
                   check=True, capture_output=True)
    return jxl


def _encoded_master(tmp_path: Path, name: str = "src") -> Path:
    """A TIFF uint16 -> JXL master (gen=1, origin tiff16), returned as the JXL."""
    d = tmp_path / name
    d.mkdir()
    _rgb_tiff(d / "p.tif", np.uint16)
    _run("jxl_tiff_encoder.py", d / "p.tif", "--mode", "0", "--delete-confirm-off")
    return d / "p.jxl"


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("origin,expected", [
    ("jpeg", 1),
    ("jxl", 1),
    ("jxl-lossy", 1),
    ("jxl-lossless", 0),
    ("tiff16", 0),
    ("tiff8", 0),
    ("png16", 0),
    ("png8", 0),
    ("png", 0),
    (None, 0),
    ("something-else", 0),
])
def test_u1_hidden_generations(origin, expected):
    assert rec._hidden_generations(origin) == expected


@pytest.mark.parametrize("gen,new_d,origin,expected", [
    (1, 1.0, "jpeg", "ask"),
    (1, 1.0, "jxl-lossy", "ask"),
    (1, 1.0, "jxl", "ask"),
    (1, 1.0, "jxl-lossless", None),
    (1, 1.0, "tiff16", None),
    (1, 1.0, None, None),
    (0, 1.0, "jpeg", None),
    (1, 0.0, "jpeg", None),
    (2, 1.0, "tiff16", "ask"),
])
def test_u2_regeneration_action_counts_the_hidden_generation(
        monkeypatch, gen, new_d, origin, expected):
    monkeypatch.setattr(rec, "ON_REGENERATION", "ask")
    assert rec._regeneration_action(gen, new_d, origin) == expected


def test_u2_default_two_positional_call_is_unchanged(monkeypatch):
    monkeypatch.setattr(rec, "ON_REGENERATION", "ask")
    assert rec._regeneration_action(1, 1.0) is None


class _FakeExiftoolRun:
    def __init__(self, stdout=""):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


def _batch(monkeypatch, entries):
    run = _FakeExiftoolRun(json.dumps(entries))
    monkeypatch.setattr(rec, "subprocess", type(
        "S", (), {"run": staticmethod(lambda *a, **k: run),
                  "TimeoutExpired": subprocess.TimeoutExpired}))
    monkeypatch.setattr(rec, "_get_exiftool_cmd", lambda: "exiftool")
    return rec._read_encode_params_batch(["NOFILE.jxl"])


@pytest.mark.parametrize("relation,expected", [
    (["user", "jxlphoto-origin:jpeg", "jxlphoto-src:ab"], "jpeg"),
    ("jxlphoto-origin:jxl-lossy", "jxl-lossy"),
    (["jxlphoto-origin:"], None),
    (["jxlphoto-src:ab"], None),
])
def test_u3_batch_reader_reads_the_origin(monkeypatch, relation, expected):
    info = _batch(monkeypatch, [{"SourceFile": "NOFILE.jxl",
                                 "Description": "", "Software": "",
                                 "Relation": relation}])
    assert info["NOFILE.jxl"]["origin"] == expected


def test_u3_missing_relation_is_none(monkeypatch):
    info = _batch(monkeypatch, [{"SourceFile": "NOFILE.jxl",
                                 "Description": "", "Software": ""}])
    assert info["NOFILE.jxl"]["origin"] is None


# ---------------------------------------------------------------------------
# R1/R2 - a foreign JXL records whether its header is lossy
# ---------------------------------------------------------------------------

@real
def test_r1_foreign_lossy_jxl_gets_jxl_lossy(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    png = tmp_path / "rand.png"
    _random_png(png, seed=6)
    _foreign_jxl(in_dir, png, "1")
    _run("jxl_recompressor.py", in_dir, "--mode", "1", "--distance", "2",
         "--no-preflight", "--no-keep-smaller")
    out = in_dir / "recompressed_jxl" / "foreign.jxl"
    assert _origins(out) == ["jxlphoto-origin:jxl-lossy"]


@real
def test_r2_foreign_lossless_jxl_gets_jxl_lossless(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    png = tmp_path / "rand.png"
    _random_png(png, seed=6)
    _foreign_jxl(in_dir, png, "0")
    _run("jxl_recompressor.py", in_dir, "--mode", "1", "--distance", "2",
         "--no-preflight", "--no-keep-smaller")
    out = in_dir / "recompressed_jxl" / "foreign.jxl"
    assert _origins(out) == ["jxlphoto-origin:jxl-lossless"]


# ---------------------------------------------------------------------------
# R3 - the guard counts the hidden generation
# ---------------------------------------------------------------------------

_GUARD_ARGS = ["--mode", "1", "--no-preflight", "--no-keep-smaller",
               "--on-regeneration", "skip"]


@real
def test_r3_the_guard_fires_one_generation_earlier(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    png = tmp_path / "rand.png"
    _random_png(png, seed=6)
    _foreign_jxl(in_dir, png, "1")
    _run("jxl_recompressor.py", in_dir, "--mode", "1", "--distance", "2",
         "--no-preflight", "--no-keep-smaller")
    gen1 = in_dir / "recompressed_jxl" / "foreign.jxl"
    assert _origins(gen1) == ["jxlphoto-origin:jxl-lossy"]

    again = tmp_path / "again"
    again.mkdir()
    shutil.copy(str(gen1), str(again / "foreign.jxl"))
    r = _run("jxl_recompressor.py", again, "--distance", "3", *_GUARD_ARGS)
    assert not (again / "recompressed_jxl" / "foreign.jxl").exists()
    out = r.stdout + r.stderr
    assert "SKIP (policy)" in out and "before this toolkit" in out


@real
def test_r3b_lossless_origin_control_is_written(tmp_path):
    # The lossless-source control of R3: this test has to PASS against the
    # pre-fix code too (the plan's pre-fix proof lists it), so it does NOT
    # re-assert the origin value — R2 pins `jxl-lossless`, and what the guard
    # decides from it is the check here.
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    png = tmp_path / "rand.png"
    _random_png(png, seed=6)
    _foreign_jxl(in_dir, png, "0")
    _run("jxl_recompressor.py", in_dir, "--mode", "1", "--distance", "2",
         "--no-preflight", "--no-keep-smaller")
    gen1 = in_dir / "recompressed_jxl" / "foreign.jxl"

    again = tmp_path / "again"
    again.mkdir()
    shutil.copy(str(gen1), str(again / "foreign.jxl"))
    _run("jxl_recompressor.py", again, "--distance", "3", *_GUARD_ARGS)
    assert (again / "recompressed_jxl" / "foreign.jxl").exists()


@real
def test_r3c_tiff_master_control_is_written(tmp_path):
    master = _encoded_master(tmp_path)
    assert _origins(master) == ["jxlphoto-origin:tiff16"]
    r_dir = tmp_path / "rec"
    r_dir.mkdir()
    shutil.copy(str(master), str(r_dir / "p.jxl"))
    _run("jxl_recompressor.py", r_dir, "--distance", "3", *_GUARD_ARGS)
    assert (r_dir / "recompressed_jxl" / "p.jxl").exists()


@real
def test_r3d_plain_jxl_origin_is_treated_as_lossy(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    png = tmp_path / "rand.png"
    _random_png(png, seed=6)
    _foreign_jxl(in_dir, png, "1")
    _run("jxl_recompressor.py", in_dir, "--mode", "1", "--distance", "2",
         "--no-preflight", "--no-keep-smaller")
    gen1 = in_dir / "recompressed_jxl" / "foreign.jxl"
    # A v2.10.0 file: a foreign JXL recorded as plain `jxl`.
    _set_meta(gen1, "-XMP-dc:Relation-=jxlphoto-origin:jxl-lossy",
              "-XMP-dc:Relation+=jxlphoto-origin:jxl")
    assert _origins(gen1) == ["jxlphoto-origin:jxl"]

    again = tmp_path / "again"
    again.mkdir()
    shutil.copy(str(gen1), str(again / "foreign.jxl"))
    _run("jxl_recompressor.py", again, "--distance", "3", *_GUARD_ARGS)
    assert not (again / "recompressed_jxl" / "foreign.jxl").exists()


# ---------------------------------------------------------------------------
# R4/R5 - exactly one src/srcsum pair, the output's own
# ---------------------------------------------------------------------------

@real
def test_r4_decode_writes_one_src_pair_its_own(tmp_path):
    master = _encoded_master(tmp_path)
    master_src = _src_tokens(master)
    assert len(master_src) == 1
    _run("jxl_jpeg_transcoder.py", master, "--force-convert", "--format", "jpeg")
    jpg = master.parent / "p.jpg"
    assert len(_src_tokens(jpg)) == 1
    assert len(_srcsum_tokens(jpg)) == 1
    assert _src_tokens(jpg) != master_src
    assert _origins(jpg) == ["jxlphoto-origin:tiff16"]


@real
def test_r4b_reencode_writes_one_src_pair_its_own(tmp_path):
    master = _encoded_master(tmp_path)
    master_src = _src_tokens(master)
    _run("jxl_jpeg_transcoder.py", master, "--force-convert", "--format", "jpeg")
    jpg = master.parent / "p.jpg"
    jpg_src = _src_tokens(jpg)

    back = tmp_path / "back"
    back.mkdir()
    shutil.copy(str(jpg), str(back / "p.jpg"))
    _run("jxl_jpeg_transcoder.py", back / "p.jpg", "--force-convert", "--distance", "1")
    out = back / "p.jxl"
    assert len(_src_tokens(out)) == 1
    assert len(_srcsum_tokens(out)) == 1
    assert _src_tokens(out) not in (master_src, jpg_src)
    assert _origins(out) == ["jxlphoto-origin:tiff16"]


@real
def test_r4c_png_decode_writes_one_src_pair(tmp_path):
    master = _encoded_master(tmp_path)
    master_src = _src_tokens(master)
    _run("jxl_jpeg_transcoder.py", master, "--force-convert", "--format", "png")
    png = master.parent / "p.png"
    assert len(_src_tokens(png)) == 1
    assert len(_srcsum_tokens(png)) == 1
    assert _src_tokens(png) != master_src
    assert _origins(png) == ["jxlphoto-origin:tiff16"]


@real
def test_r5_user_relation_value_survives_the_decode(tmp_path):
    master = _encoded_master(tmp_path)
    _set_meta(master, "-XMP-dc:Relation+=my-tag")
    _run("jxl_jpeg_transcoder.py", master, "--force-convert", "--format", "jpeg")
    assert "my-tag" in _relation(master.parent / "p.jpg")
