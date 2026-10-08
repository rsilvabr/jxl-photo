#!/usr/bin/env python3
"""X2/E3 of the 2026-10-08 audit: a source re-exported DURING the run was
deleted without its new version ever being read.

The delete gate only runs once the whole pool has drained — hours on a big
batch. Nothing recorded which file the run had read, so a TIFF re-exported
by Capture One (or a JXL replaced by another tool) in that window was
unlinked on the strength of the output made from its OLD version.

Each test wraps the backend's real conversion function so the source is
rewritten right after it was converted (exactly the audit's reproduction),
then runs the real main() with --delete-source: the source must survive.

Real codecs, in process. Pre-fix proof: point the tests at the HEAD scripts

    git show HEAD:<script> > <tmp>/<script>      (all five)
    $env:JXLPHOTO_SCRIPTS_UNDER_TEST = "<tmp>"
    python -m pytest tests/test_audit_261008_x2.py
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)

_HAS_CODECS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
requires_codecs = pytest.mark.skipif(
    not _HAS_CODECS, reason="cjxl/djxl/exiftool not on PATH")


def _load(script: str, tag: str):
    """A private copy of the script under test (never the shared module the
    rest of the suite imports, so its globals cannot leak)."""
    spec = importlib.util.spec_from_file_location(
        f"x2_{tag}_{script[:-3]}", str(SCRIPTS / script))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tiff(path: Path, seed: int):
    import tifffile
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    tifffile.imwrite(str(path), rng.integers(0, 65535, (48, 64, 3), dtype=np.uint16),
                     photometric="rgb")
    return path


def _jpeg(path: Path, seed: int):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    Image.fromarray(rng.integers(0, 255, (48, 64, 3), dtype=np.uint8)).save(
        str(path), quality=90)
    return path


def _reexport(path: Path, writer):
    """Rewrite the source with other content and a clearly newer mtime."""
    writer()
    t = path.stat().st_mtime + 60
    os.utime(path, (t, t))


def _main(mod, monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    try:
        mod.main()
    except SystemExit as e:
        return e.code
    return 0


def _encode_jxl(tmp_path, name, seed) -> Path:
    t = _tiff(tmp_path / f"t{seed}" / f"{name}.tif", seed)
    out = tmp_path / f"j{seed}"
    r = subprocess.run([sys.executable, str(REPO / "jxl_tiff_encoder.py"), t.parent,
                        out, "--mode", "2", "--distance", "0", "--no-preflight"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    return out / f"{name}.jxl"


@requires_codecs
def test_encoder_keeps_a_tiff_reexported_during_the_run(tmp_path, monkeypatch):
    enc = _load("jxl_tiff_encoder.py", "enc")
    src = _tiff(tmp_path / "f" / "foto.tif", 1)
    orig = enc.convert_one

    def wrapped(*a, **k):
        r = orig(*a, **k)
        _reexport(src, lambda: _tiff(src, 2))
        return r
    monkeypatch.setattr(enc, "convert_one", wrapped)
    _main(enc, monkeypatch, ["jxl_tiff_encoder.py", str(tmp_path / "f"), "--mode", "8",
                             "--distance", "0", "--delete-source",
                             "--delete-confirm-off", "--no-preflight"])
    assert (tmp_path / "f" / "foto.jxl").exists()
    assert src.exists(), "the re-exported TIFF was deleted without being archived"


@requires_codecs
def test_decoder_keeps_a_jxl_replaced_during_the_run(tmp_path, monkeypatch):
    dec = _load("jxl_tiff_decoder.py", "dec")
    a = _encode_jxl(tmp_path, "foto", 1)
    b = _encode_jxl(tmp_path, "foto", 2)
    work = tmp_path / "w"
    work.mkdir()
    src = work / "foto.jxl"
    shutil.copy2(a, src)
    orig = dec.convert_multipage_jxl_group

    def wrapped(*a_, **k):
        r = orig(*a_, **k)
        _reexport(src, lambda: shutil.copyfile(b, src))
        return r
    monkeypatch.setattr(dec, "convert_multipage_jxl_group", wrapped)
    _main(dec, monkeypatch, ["jxl_tiff_decoder.py", str(work), "--mode", "8",
                             "--delete-source", "--delete-confirm-off"])
    assert (work / "foto.tif").exists()
    assert src.exists(), "the replaced JXL was deleted without being decoded"


@requires_codecs
def test_recompressor_keeps_a_jxl_replaced_during_the_run(tmp_path, monkeypatch):
    rec = _load("jxl_recompressor.py", "rec")
    a = _encode_jxl(tmp_path, "foto", 1)
    b = _encode_jxl(tmp_path, "foto", 2)
    work = tmp_path / "w"
    work.mkdir()
    src = work / "foto.jxl"
    shutil.copy2(a, src)
    orig = rec.convert_one

    def wrapped(*a_, **k):
        r = orig(*a_, **k)
        _reexport(src, lambda: shutil.copyfile(b, src))
        return r
    monkeypatch.setattr(rec, "convert_one", wrapped)
    _main(rec, monkeypatch, ["jxl_recompressor.py", str(work), "--mode", "1",
                             "--distance", "1", "--effort", "3",
                             "--on-unknown", "convert", "--on-regeneration", "convert",
                             "--on-downgrade", "convert", "--no-preflight",
                             "--delete-source", "--delete-confirm-off"])
    assert (work / "recompressed_jxl" / "foto.jxl").exists()
    assert src.exists(), "the replaced JXL was deleted without being recompressed"


@requires_codecs
def test_transcoder_lossy_keeps_a_jpeg_replaced_during_the_run(tmp_path, monkeypatch):
    tr = _load("jxl_jpeg_transcoder.py", "tr")
    src = _jpeg(tmp_path / "f" / "foto.jpg", 1)
    orig = tr.encode_to_jxl

    def wrapped(*a, **k):
        r = orig(*a, **k)
        _reexport(src, lambda: _jpeg(src, 2))
        return r
    monkeypatch.setattr(tr, "encode_to_jxl", wrapped)
    _main(tr, monkeypatch, ["jxl_jpeg_transcoder.py", str(tmp_path / "f"),
                            "--mode", "8", "--force-convert", "--distance", "1",
                            "--delete-source", "--delete-confirm-off"])
    assert (tmp_path / "f" / "foto.jxl").exists()
    assert src.exists(), "the replaced JPEG was deleted without being converted"


@requires_codecs
def test_encoder_still_deletes_an_unchanged_source(tmp_path, monkeypatch):
    """No regression: nothing changed, so the gate deletes as before."""
    enc = _load("jxl_tiff_encoder.py", "enc_ok")
    src = _tiff(tmp_path / "f" / "foto.tif", 1)
    _main(enc, monkeypatch, ["jxl_tiff_encoder.py", str(tmp_path / "f"), "--mode", "8",
                             "--distance", "0", "--delete-source",
                             "--delete-confirm-off", "--no-preflight"])
    assert (tmp_path / "f" / "foto.jxl").exists()
    assert not src.exists()
