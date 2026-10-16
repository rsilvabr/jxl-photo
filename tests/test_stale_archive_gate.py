#!/usr/bin/env python3
"""Round 2026-10-17 (external audit): two destructive paths that trusted too
little evidence.

  A  --delete-skipped deleted a source replaced IN PLACE by a different file
     with an older date (a restored backup, a copy that kept its timestamps):
     smart sync skipped it, and the gate accepted the existing output because
     its marker named the same PATH — the srcsum (the bytes) was never
     compared. Encoder, decoder, the transcoder's lossy gate and the
     recompressor (`_markers_match`) all had it.
  B  the recompressor's in-place replacement swapped its re-encode over the
     source without checking that the source was still the file it had read:
     a version written by another program during the re-encode was destroyed.
     A multi-page group must check EVERY page before replacing the first.

Real codecs (cjxl/djxl/exiftool); every test in its own tmp_path. Each A test
has a control: an untouched source in the same run IS still deleted, so the
fix cannot pass by refusing everything.

Pre-fix proof (must fail on 52bdbe1):

    mkdir <tmp>\\sagpre
    git show 52bdbe1:jxl_tiff_encoder.py    > <tmp>\\sagpre\\jxl_tiff_encoder.py
    git show 52bdbe1:jxl_tiff_decoder.py    > <tmp>\\sagpre\\jxl_tiff_decoder.py
    git show 52bdbe1:jxl_jpeg_transcoder.py > <tmp>\\sagpre\\jxl_jpeg_transcoder.py
    git show 52bdbe1:jxl_recompressor.py    > <tmp>\\sagpre\\jxl_recompressor.py
    $env:SAG_SCRIPTS = "<tmp>\\sagpre"
    python -m pytest tests/test_stale_archive_gate.py -q
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import numpy as np
import pytest
import tifffile

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("SAG_SCRIPTS") or REPO)
ENCODER = SCRIPTS / "jxl_tiff_encoder.py"
DECODER = SCRIPTS / "jxl_tiff_decoder.py"
TRANSCODER = SCRIPTS / "jxl_jpeg_transcoder.py"
RECOMPRESSOR = SCRIPTS / "jxl_recompressor.py"

_HAVE_TOOLS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
real = pytest.mark.skipif(not _HAVE_TOOLS,
                          reason="needs cjxl, djxl and exiftool on PATH")

DELETE = ["--delete-source", "--delete-skipped", "--delete-confirm-off"]


def _run(script, *args):
    return subprocess.run([sys.executable, str(script)] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=600,
                          stdin=subprocess.DEVNULL)


def _tiff(path: Path, seed: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path),
                     (np.random.default_rng(seed).random((48, 64, 3)) * 65535)
                     .astype("uint16"), photometric="rgb")


def _jxl(path: Path, seed: int, distance="1"):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    png = path.parent / f"_{uuid.uuid4().hex}.png"
    Image.fromarray((np.random.default_rng(seed).random((48, 64, 3)) * 255)
                    .astype("uint8")).save(png)
    try:
        subprocess.run(["cjxl", str(png), str(path), "-d", distance, "-e", "3",
                        "--container=1"], check=True, capture_output=True)
    finally:
        png.unlink()


def _relation(path: Path, *tokens):
    subprocess.run(["exiftool", "-q", "-overwrite_original"]
                   + [f"-XMP-dc:Relation+={t}" for t in tokens] + [str(path)],
                   check=True, capture_output=True)


def _age(path: Path, older_than: Path):
    """Date `path` an hour before `older_than`: what a restored backup or a
    timestamp-keeping copy looks like to smart sync."""
    t = older_than.stat().st_mtime - 3600
    os.utime(path, (t, t))


def _log(r):
    return r.stdout + r.stderr


# ---------------------------------------------------------------------------
# A — a source replaced in place is never deleted on a stale archive
# ---------------------------------------------------------------------------

@real
def test_encoder_keeps_a_tiff_replaced_since_its_archive(tmp_path):
    lib = tmp_path / "lib"
    _tiff(lib / "a.tif", 1)
    _tiff(lib / "b.tif", 2)
    r = _run(ENCODER, lib, "--mode", "0", "--distance", "1", "--effort", "3",
             "--no-preflight")
    assert r.returncode == 0, _log(r)
    assert (lib / "a.jxl").exists() and (lib / "b.jxl").exists(), _log(r)

    _tiff(lib / "a.tif", 99)                 # a new version, same path...
    _age(lib / "a.tif", lib / "a.jxl")       # ...older than its archive
    r = _run(ENCODER, lib, "--mode", "0", "--distance", "1", "--effort", "3",
             "--no-preflight", *DELETE)
    assert r.returncode == 0, _log(r)
    assert (lib / "a.tif").exists(), (
        "the replaced TIFF was deleted on the strength of an archive of its "
        "OLD version:\n" + _log(r))
    assert not (lib / "b.tif").exists(), (
        "control: an untouched, archived TIFF must still be deleted\n" + _log(r))


@real
def test_decoder_keeps_a_jxl_replaced_since_its_decode(tmp_path):
    src = tmp_path / "src"
    _jxl(src / "a.jxl", 1, distance="0")
    _jxl(src / "b.jxl", 2, distance="0")
    r = _run(DECODER, src, "--mode", "1", "--delete-confirm-off")
    assert r.returncode == 0, _log(r)
    tif_a = src / "converted_tiff" / "a.tif"
    assert tif_a.exists() and (src / "converted_tiff" / "b.tif").exists(), _log(r)

    _jxl(src / "a.jxl", 99, distance="0")
    _age(src / "a.jxl", tif_a)
    r = _run(DECODER, src, "--mode", "1", *DELETE)
    assert r.returncode == 0, _log(r)
    assert (src / "a.jxl").exists(), (
        "the replaced JXL was deleted on the strength of a TIFF decoded from its "
        "OLD version:\n" + _log(r))
    assert not (src / "b.jxl").exists(), (
        "control: an untouched, decoded JXL must still be deleted\n" + _log(r))


@real
def test_transcoder_lossy_keeps_a_jxl_replaced_since_its_jpeg(tmp_path):
    src = tmp_path / "src"
    _jxl(src / "a.jxl", 1)
    _jxl(src / "b.jxl", 2)
    r = _run(TRANSCODER, src, "--mode", "1", "--format", "jpeg",
             "--delete-confirm-off")
    assert r.returncode == 0, _log(r)
    outs = {p.stem: p for p in src.rglob("*.jpg")}
    assert set(outs) == {"a", "b"}, _log(r)

    _jxl(src / "a.jxl", 99)
    _age(src / "a.jxl", outs["a"])
    r = _run(TRANSCODER, src, "--mode", "1", "--format", "jpeg", *DELETE)
    assert r.returncode == 0, _log(r)
    assert (src / "a.jxl").exists(), (
        "the replaced JXL was deleted on the strength of a JPEG made from its "
        "OLD version:\n" + _log(r))
    assert not (src / "b.jxl").exists(), (
        "control: an untouched JXL with a proven JPEG must still be deleted\n"
        + _log(r))


@real
def test_recompressor_keeps_a_master_rebuilt_from_a_changed_tiff(tmp_path):
    """The recompressor's proof is marker EQUALITY between master and output.
    A master re-encoded from a changed TIFF keeps the TIFF's path id but gets
    a new srcsum; the path alone deleted it."""
    src = tmp_path / "src"
    _jxl(src / "a.jxl", 1, distance="0.1")
    _relation(src / "a.jxl", "jxlphoto-src:AAAAAAAAAAAAAAAA",
              "jxlphoto-srcsum:1111111111111111")
    _jxl(src / "b.jxl", 2, distance="0.1")
    _relation(src / "b.jxl", "jxlphoto-src:BBBBBBBBBBBBBBBB",
              "jxlphoto-srcsum:2222222222222222")
    common = ["--mode", "1", "--on-unknown", "convert", "--distance", "1",
              "--effort", "3", "--no-preflight"]
    r = _run(RECOMPRESSOR, src, *common, "--delete-confirm-off")
    assert r.returncode == 0, _log(r)
    out_a = src / "recompressed_jxl" / "a.jxl"
    assert out_a.exists() and (src / "recompressed_jxl" / "b.jxl").exists(), _log(r)

    # The master rebuilt from the same TIFF path after the TIFF changed.
    _jxl(src / "a.jxl", 99, distance="0.1")
    _relation(src / "a.jxl", "jxlphoto-src:AAAAAAAAAAAAAAAA",
              "jxlphoto-srcsum:9999999999999999")
    _age(src / "a.jxl", out_a)
    r = _run(RECOMPRESSOR, src, *common, *DELETE)
    assert r.returncode == 0, _log(r)
    assert (src / "a.jxl").exists(), (
        "the rebuilt master was deleted on the strength of a recompression of "
        "the OLD master:\n" + _log(r))
    assert not (src / "b.jxl").exists(), (
        "control: an untouched master with a proven output must still be "
        "deleted\n" + _log(r))


# ---------------------------------------------------------------------------
# B — in place, a source changed during the re-encode is never replaced
# ---------------------------------------------------------------------------

def _load_recompressor():
    """A fresh module object per test, from SCRIPTS (the pre-fix copy under
    the proof), so monkeypatching it never leaks into other tests."""
    name = f"_sag_rec_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, RECOMPRESSOR)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _rewrite_during(rec, monkeypatch, victims, trigger):
    """Run the real convert_one; when `trigger` finishes, another 'program'
    writes a new version over each of `victims`. Returns {victim: new bytes}."""
    real_convert = rec.convert_one
    new_bytes = {}

    def convert_then_rewrite(src, *a, **k):
        res = real_convert(src, *a, **k)
        if Path(src).name == trigger:
            for v in victims:
                _jxl(v, 1000 + len(new_bytes), distance="0.1")
                t = time.time() + 5          # a fresh, distinct mtime
                os.utime(v, (t, t))
                new_bytes[v] = v.read_bytes()
        return res

    monkeypatch.setattr(rec, "convert_one", convert_then_rewrite)
    return new_bytes


def _main_in_place(rec, monkeypatch, root, *extra):
    monkeypatch.setattr(sys, "argv",
                        ["jxl_recompressor.py", str(root), str(root), "--mode", "0",
                         "--on-unknown", "convert", "--distance", "1",
                         "--effort", "3", "--workers", "1", "--no-preflight",
                         "--delete-confirm-off", *extra])
    try:
        rec.main()
    except SystemExit:
        pass


@real
def test_in_place_never_replaces_a_source_rewritten_during_the_reencode(
        tmp_path, monkeypatch):
    monkeypatch.setenv("JXLPHOTO_LOG_DIR", str(tmp_path / "logs"))
    rec = _load_recompressor()
    root = tmp_path / "photos"
    _jxl(root / "a.jxl", 1, distance="0.1")
    new = _rewrite_during(rec, monkeypatch, [root / "a.jxl"], trigger="a.jxl")
    _main_in_place(rec, monkeypatch, root)
    assert (root / "a.jxl").read_bytes() == new[root / "a.jxl"], (
        "the version written during the re-encode was replaced by a re-encode "
        "of the OLD one")
    assert not list(root.glob("*.tmp")), "the discarded re-encode was left behind"


@real
def test_in_place_group_is_held_when_an_earlier_page_changed(
        tmp_path, monkeypatch):
    """p0 settles and waits for its group; p0 is rewritten while p1 encodes.
    Replacing the group would destroy p0's new version, and replacing only
    p1 would split the document: NO page may be replaced."""
    monkeypatch.setenv("JXLPHOTO_LOG_DIR", str(tmp_path / "logs"))
    rec = _load_recompressor()
    root = tmp_path / "photos"
    _jxl(root / "p0.jxl", 1, distance="0.1")
    _jxl(root / "p1.jxl", 2, distance="0.1")
    p1_before = (root / "p1.jxl").read_bytes()
    monkeypatch.setattr(rec, "_read_mpg_markers",
                        lambda paths: ({str(p): "group-1" for p in paths}, True))
    new = _rewrite_during(rec, monkeypatch, [root / "p0.jxl"], trigger="p1.jxl")
    _main_in_place(rec, monkeypatch, root)
    assert (root / "p0.jxl").read_bytes() == new[root / "p0.jxl"], (
        "p0's newer version was replaced by a re-encode of its old one")
    assert (root / "p1.jxl").read_bytes() == p1_before, (
        "p1 was replaced alone: the document is now split across two versions")
    assert not list(root.glob("*.tmp")), "a discarded re-encode was left behind"
