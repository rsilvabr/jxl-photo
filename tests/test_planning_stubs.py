"""#676 — the planning-time batch reads read a metadata stub of each JXL.

exiftool reads the whole JXL to answer any question (50 MB for a 48 MB master,
-fast or not), so a run with nothing to do — a sync, a derivative run whose
copies are up to date — read every master in full before its first file
(86 GB for one year of the owner's masters). The batch engine now hands
exiftool a stub per JXL container: every box but the Explorer thumbnail, the
codestream cut to its first bytes. Every reader must give the same answers;
anything less than a clean answer for every stub reads the chunk from the
files, as before.

Real codecs and exiftool, in a folder named with Japanese, brackets, a space
and `100%off %d`.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

import jxl_jpeg_transcoder as tr
import jxl_photo_common as common
import jxl_recompressor as rec
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc
from _icc_fixtures import romm_toe_icc

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "吾妻山 [計画] 100%off %d"
_TOOLS = ("cjxl", "djxl", "exiftool", "magick")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")


@pytest.fixture(scope="module")
def jxls(tmp_path_factory):
    """Masters as the toolkit writes them (one with an Explorer thumbnail, a
    split scan, a derivative), a JPEG archive and a bare codestream."""
    root = tmp_path_factory.mktemp("stubs") / HOSTILE
    src = root / "tiff"
    src.mkdir(parents=True)
    rng = np.random.default_rng(8)
    y, x = np.mgrid[0:600, 0:900]
    rgb = np.clip(np.stack([x * 70, y * 100, np.full(x.shape, 25000)], -1)
                  + rng.integers(-500, 500, (600, 900, 3)), 0, 65535).astype(np.uint16)
    tifffile.imwrite(src / "写真 (1).tif", rgb, photometric="rgb", resolution=(300, 300),
                     resolutionunit=2,
                     extratags=[(34675, "B", len(romm_toe_icc()), romm_toe_icc(), True)])
    with tifffile.TiffWriter(src / "走査 [2].tif") as w:
        w.write(rgb, photometric="rgb")
        w.write(rgb[::4, ::4, 0], photometric="minisblack", subfiletype=1)
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(root / "logs"), "JXLPHOTO_SETTINGS": "",
           "JXLPHOTO_JXL_THUMB": "0"}
    r = subprocess.run([sys.executable, str(REPO / "jxl_tiff_encoder.py"), str(src), "--mode", "1",
                        "--distance", "0", "--effort", "1", "--no-preflight",
                        "--explorer-thumbnail", "on"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=600, env=env)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    masters = sorted((src / "converted_jxl").glob("*.jxl"))
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"), str(src / "converted_jxl"),
                        str(root / "copies"), "--mode", "2", "--resize-long", "300",
                        "--distance", "1", "--effort", "1", "--on-unknown", "convert",
                        "--on-regeneration", "convert", "--on-downgrade", "convert",
                        "--on-scan", "convert", "--no-preflight"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=600, env=env)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    copies = sorted((root / "copies").glob("*.jxl"))
    from PIL import Image
    Image.fromarray((rgb >> 8).astype(np.uint8)).save(root / "写真 3.jpg", quality=90)
    archive = root / "写真 3.jxl"
    subprocess.run(["cjxl", str(root / "写真 3.jpg"), str(archive), "--lossless_jpeg=1",
                    "--jpeg_reconstruction_lfs=0", "-e", "1"], check=True, capture_output=True)
    bare = root / "裸 4.jxl"
    pam = root / "bare.pam"
    pam.write_bytes(b"P7\nWIDTH 64\nHEIGHT 48\nDEPTH 3\nMAXVAL 255\nTUPLTYPE RGB\nENDHDR\n"
                    + bytes(range(256)) * 36)
    subprocess.run(["cjxl", str(pam), str(bare), "-d", "1", "-e", "1"], check=True,
                   capture_output=True)
    pam.unlink()
    assert bare.read_bytes()[:2] == b"\xff\x0a", "cjxl wrote a container"
    files = masters + copies + [archive, bare]
    assert enc._thumbnail_box_fingerprint(masters[0]), "no Explorer thumbnail in the master"
    return files


def _readers(paths):
    for mod in (enc, dec, tr, rec):
        mod.setup_logger()
    return {
        "source markers": common._read_source_markers_batch(enc, paths),
        "derived markers": common._read_derived_markers_batch(rec, paths),
        "encoder page markers": enc._read_page_markers_batch(paths),
        "encoder group markers": enc._read_group_markers_batch(paths),
        "decoder xmp icc": dec._xmp_icc_jxls_batch(paths),
        "decoder multipage": dec._read_multipage_markers_batch(paths),
        "transcoder thumb markers": tr._read_thumb_markers_batch(paths),
        "recompressor encode params": rec._read_encode_params_batch(paths),
        "recompressor mpg markers": rec._read_mpg_markers(paths),
        "recompressor mpg pages": rec._read_mpg_pages_batch(paths),
    }


@needs_tools
def test_every_batch_reader_answers_the_same_from_stubs(jxls, monkeypatch):
    argfiles = []
    real = common._batch_exiftool

    def spy(g, head, names):
        argfiles.append(list(names))
        return real(g, head, names)

    monkeypatch.setattr(common, "_batch_exiftool", spy)
    with_stubs = _readers(jxls)
    used = [n for names in argfiles for n in names if "jxlstub_" in n]
    assert used, "no stub was read"
    # The bare codestream has no boxes: it is read itself, in the same call.
    assert any(n.endswith("裸 4.jxl") for names in argfiles for n in names)
    monkeypatch.setattr(common, "_batch_on_stubs", lambda *a, **k: None)
    from_files = _readers(jxls)
    assert with_stubs == from_files


@needs_tools
def test_a_stub_exiftool_cannot_read_sends_the_chunk_to_the_files(jxls, monkeypatch):
    real = common._write_metadata_stub
    calls = {"n": 0}

    def broken_once(src, stub, drop_thumbnail=False):
        calls["n"] += 1
        if calls["n"] == 1:
            Path(stub).write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"garbage" * 10)
            return
        return real(src, stub, drop_thumbnail=drop_thumbnail)

    monkeypatch.setattr(common, "_write_metadata_stub", broken_once)
    got = common._read_source_markers_batch(enc, jxls)
    monkeypatch.setattr(common, "_write_metadata_stub", real)
    monkeypatch.setattr(common, "_batch_on_stubs", lambda *a, **k: None)
    want = common._read_source_markers_batch(enc, jxls)
    assert got == want
    assert not any(v.get("unread") for v in got.values()), got


@needs_tools
def test_the_stub_drops_the_thumbnail_and_cuts_the_codestream(jxls, tmp_path):
    master = jxls[0]
    stub = tmp_path / "s.jxl"
    common._write_metadata_stub(master, stub, drop_thumbnail=True)
    assert stub.stat().st_size < 64 * 1024 < master.stat().st_size
    assert enc._thumbnail_box_fingerprint(stub) == ""
    import json
    af = tmp_path / "a.args"
    af.write_text("\n".join(["-charset", "filename=utf8", "-j", "-s", "-s", "-ImageWidth",
                             "-ImageHeight", "-XMP-dc:Relation", str(master), str(stub)]) + "\n",
                  encoding="utf-8")
    out = json.loads(subprocess.run(["exiftool", "-@", str(af)], capture_output=True,
                                    timeout=120).stdout)
    for e in out:
        e.pop("SourceFile")
    assert out[0] == out[1]
