#!/usr/bin/env python3
"""The overwritable guard: OVERWRITE_GUARD / --no-overwrite-guard and
--restamp-moved (plan 261013, topic `overwrite-guard`).

U1-U5 are unit tests; R1-R8 run the real cjxl/djxl/exiftool, because the guard
reads and rewrites XMP through exiftool — a mocked exiftool would prove nothing
about the marker read-back or the box order.

R3b and R5b are NOT new-feature tests: they pin that the guard OFF still keeps
protecting an original master (decoder) and a lossless JPEG archive (jbrd,
transcoder). Against the pre-fix code they fail at argparse (the flag does not
exist); against a WRONG implementation that disables every refusal they are the
tests that catch it.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_jpeg_transcoder as tra
import jxl_photo as wp
import jxl_recompressor as rec
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent
ENCODER = str(REPO / "jxl_tiff_encoder.py")
DECODER = str(REPO / "jxl_tiff_decoder.py")
TRANSCODER = str(REPO / "jxl_jpeg_transcoder.py")
RECOMPRESSOR = str(REPO / "jxl_recompressor.py")

# getattr, not a direct attribute: against the pre-fix code the proof run must
# show every test FAILING, not the module erroring at collection.
BACKENDS = [(enc, "jxl_tiff_encoder"), (dec, "jxl_tiff_decoder"),
            (tra, "jxl_jpeg_transcoder"), (rec, "jxl_recompressor")]

_HAVE_TOOLS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
real = pytest.mark.skipif(not _HAVE_TOOLS,
                          reason="needs cjxl, djxl and exiftool on PATH")

FAST = ["--distance", "1", "--effort", "3", "--no-preflight"]


def _run(script: str, *args, cwd=None):
    return subprocess.run([sys.executable, script] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=600,
                          cwd=str(cwd) if cwd else None,
                          stdin=subprocess.DEVNULL)


def _src_marker(path):
    r = subprocess.run(["exiftool", "-j", "-XMP-dc:Relation", str(path)],
                       capture_output=True, text=True, check=True)
    rel = json.loads(r.stdout)[0].get("Relation") or []
    rel = rel if isinstance(rel, list) else [rel]
    return [t for t in rel if str(t).startswith("jxlphoto-src:")]


def _md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _tiff(path: Path, value: int, with_exif: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.full((48, 64, 3), value, np.uint16),
                     photometric="rgb")
    if with_exif:
        subprocess.run(["exiftool", "-q", "-overwrite_original", "-Make=NIKON",
                        str(path)], check=True, capture_output=True)


def _jpeg(path: Path, seed: int):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = (np.random.default_rng(seed).random((96, 128, 3)) * 255).astype("uint8")
    Image.fromarray(arr).save(path, quality=90)


def _boxes(path: Path):
    d = path.read_bytes()
    i, out = 0, []
    while i < len(d):
        s = int.from_bytes(d[i:i + 4], "big")
        t = d[i + 4:i + 8].decode("latin-1")
        if s == 1:
            s = int.from_bytes(d[i + 8:i + 16], "big")
        if s == 0:
            s = len(d) - i
        out.append(t)
        i += s
    return out


def _first_codestream_box(boxes):
    for i, b in enumerate(boxes):
        if b in ("jxlc", "jxlp"):
            return i
    return None


def _history_len(path: Path) -> int:
    r = subprocess.run(["exiftool", "-struct", "-j", "-XMP-xmpMM:History",
                        str(path)], capture_output=True, text=True, check=True)
    h = json.loads(r.stdout)[0].get("History")
    return 0 if h is None else (len(h) if isinstance(h, list) else 1)


def _djxl_pixel_md5(jxl: Path, out_png: Path) -> str:
    """md5 of the DECODED PIXELS. The PNG bytes carry the JXL's metadata, and a
    restamp changes it by design — only the pixels must stay identical."""
    from PIL import Image
    r = subprocess.run(["djxl", str(jxl), str(out_png)], capture_output=True)
    assert r.returncode == 0, f"djxl failed on {jxl}"
    with Image.open(out_png) as im:
        return hashlib.md5(im.tobytes()).hexdigest()


def _menu():
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mod,stem", BACKENDS, ids=[b[1] for b in BACKENDS])
def test_u1_guard_setting_and_reset(mod, stem):
    assert getattr(mod, "OVERWRITE_GUARD", None) is True
    defaults = getattr(mod, "_RUN_DEFAULTS", None)
    if isinstance(defaults, dict):
        assert "OVERWRITE_GUARD" in defaults
        assert defaults["OVERWRITE_GUARD"] is True
    else:
        assert "OVERWRITE_GUARD" in getattr(mod, "_RUN_ASSIGNABLE", ())


@pytest.mark.parametrize("mod,stem", BACKENDS, ids=[b[1] for b in BACKENDS])
def test_u2_hint_lines(mod, stem):
    joined = "\n".join(mod._guard_refusal_hint(True))
    assert "--restamp-moved" in joined
    assert "--no-overwrite-guard" in joined
    assert "OVERWRITE_GUARD = False" in joined
    assert mod.GUARD_DOC_URL in joined
    assert "--restamp-moved" not in "\n".join(mod._guard_refusal_hint(False))


def test_u3_doc_url_and_anchor():
    assert enc.GUARD_DOC_URL.endswith(
        "/docs/behavior_and_limitations.md#renamed-folders-and-the-overwrite-guard")
    doc = (REPO / "docs" / "behavior_and_limitations.md").read_text(encoding="utf-8")
    slugs = set()
    for line in doc.splitlines():
        m = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            t = m.group(1).strip().lower()
            slugs.add(re.sub(r"[^\w\- ]", "", t).replace(" ", "-"))
    assert "renamed-folders-and-the-overwrite-guard" in slugs


def test_u4_wrapper_option_table_and_field():
    for module in ("jxl_tiff_encoder", "jxl_tiff_decoder",
                   "jxl_jpeg_transcoder", "jxl_recompressor"):
        assert wp._CHILD_OPTION_DEFAULTS[module]['overwrite_guard'] == \
            ('OVERWRITE_GUARD', True)
    field = wp.ToolConfig.__dataclass_fields__['last_overwrite_guard']
    assert field.default is None


def test_u5_wrapper_emission_and_b2():
    menu = _menu()
    cmd = []
    menu._append_overwrite_guard_flag(cmd, {'overwrite_guard': False})
    assert cmd == ['--no-overwrite-guard']
    cmd = []
    menu._append_overwrite_guard_flag(cmd, {})
    assert cmd == []

    # B2: a saved `provenance` must reach the child in a run WITHOUT a delete.
    cmd = menu._build_manifest_entry_cmd(
        script="jxl_tiff_encoder.py", source=str(Path("F:/lib")),
        dest_path=str(Path("F:/out")), mode=2, origin="tiff", dest="jxl",
        workers=2, workflow={"distance": 1.0, "effort": 7, "mode_config": {}},
        advanced={"provenance": "content", "sync": True})
    assert "--delete-source" not in cmd
    assert "--provenance" in cmd
    assert cmd[cmd.index("--provenance") + 1] == "content"


# ---------------------------------------------------------------------------
# Real codecs — the guard
# ---------------------------------------------------------------------------

@real
def test_r1_encoder_renamed_folder_reexport(tmp_path):
    d = tmp_path / "lib" / "2024-05 Viagem"
    _tiff(d / "p.tif", 1000)
    r = _run(ENCODER, "lib", "--mode", "8", *FAST, cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    d.rename(tmp_path / "lib" / "2024-05 Kyoto")
    new_d = tmp_path / "lib" / "2024-05 Kyoto"
    tif_new = new_d / "p.tif"
    jxl = new_d / "p.jxl"
    _tiff(tif_new, 60000)                       # re-exported, other pixels
    t = time.time() + 60
    os.utime(tif_new, (t, t))

    r = _run(ENCODER, "lib", "--mode", "8", *FAST, "--sync", cwd=tmp_path)
    assert r.returncode == 1, r.stdout
    assert "REFUSING" in r.stdout
    assert enc.GUARD_DOC_URL in r.stdout, "the refusal must point at the docs"
    assert "--no-overwrite-guard" in r.stdout, "the way out must be named"
    before = _md5(jxl)

    r = _run(ENCODER, "lib", "--mode", "8", *FAST, "--sync",
             "--no-overwrite-guard", cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    assert "overwrite guard off" in r.stdout
    assert _md5(jxl) != before, "the guard-off run did not re-encode"
    assert _src_marker(jxl) == ["jxlphoto-src:" + enc._source_path_id(tif_new)]


@real
def test_r2_encoder_guard_off_with_delete_is_refused(tmp_path):
    d = tmp_path / "g"
    _tiff(d / "a.tif", 1000)
    r = _run(ENCODER, "g", "--mode", "8", *FAST,
             "--no-overwrite-guard", "--delete-source", "--delete-confirm-off",
             cwd=tmp_path)
    assert r.returncode == 2, r.stdout
    assert "overwrite guard is off" in r.stdout.lower(), r.stdout
    assert (d / "a.tif").exists()
    assert not (d / "a.jxl").exists()


@real
@pytest.mark.parametrize("script", [DECODER, TRANSCODER, RECOMPRESSOR],
                         ids=["decoder", "transcoder", "recompressor"])
def test_r2b_other_backends_refuse_guard_off_with_delete(tmp_path, script):
    """Rule 2 in the other three backends. The review found the transcoder
    checking the combination BEFORE reading --no-overwrite-guard: the JPEG was
    converted and DELETED with the guard off (rc 0)."""
    from PIL import Image
    d = tmp_path / "g"
    d.mkdir()
    arr = (np.random.default_rng(2).random((48, 64, 3)) * 255).astype("uint8")
    if script == TRANSCODER:
        src = d / "j.jpg"
        Image.fromarray(arr).save(src, quality=90)
    else:
        png = tmp_path / "s.png"
        Image.fromarray(arr).save(png)
        src = d / "p.jxl"
        subprocess.run(["cjxl", str(png), str(src), "-d", "1", "--container=1"],
                       check=True, capture_output=True)
    before = _md5(src)
    r = _run(script, d, "--no-overwrite-guard", "--delete-source",
             "--delete-confirm-off", cwd=tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "overwrite guard is off" in out.lower(), out
    assert src.exists() and _md5(src) == before, "the source must be kept"


@real
def test_r3_decoder_renamed_folder(tmp_path):
    a = tmp_path / "A"
    _tiff(a / "p.tif", 1000)
    assert _run(ENCODER, "A", "--mode", "0", *FAST, cwd=tmp_path).returncode == 0
    assert _run(DECODER, "A", "--mode", "1", cwd=tmp_path).returncode == 0
    (tmp_path / "A").rename(tmp_path / "B")
    b = tmp_path / "B"
    jxl = b / "p.jxl"
    tif = b / "converted_tiff" / "p.tif"
    t = time.time() + 60
    os.utime(jxl, (t, t))

    r = _run(DECODER, "B", "--mode", "1", "--sync", cwd=tmp_path)
    assert r.returncode == 1, r.stdout
    assert "DIFFERENT JXL" in r.stdout or "REFUSING" in r.stdout, r.stdout

    before = tif.stat().st_mtime_ns
    r = _run(DECODER, "B", "--mode", "1", "--sync", "--no-overwrite-guard",
             cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    assert tif.stat().st_mtime_ns != before, "the decoded TIFF was not rewritten"


@real
def test_r3b_original_master_still_kept_with_guard_off(tmp_path):
    a = tmp_path / "A"
    _tiff(a / "p.tif", 1000)
    assert _run(ENCODER, "A", "--mode", "0", *FAST, cwd=tmp_path).returncode == 0
    assert _run(DECODER, "A", "--mode", "1", cwd=tmp_path).returncode == 0
    tif = a / "converted_tiff" / "p.tif"
    shutil.copy2(a / "p.tif", tif)              # the original master, no marker
    os.utime(tif, (0, 0))                       # older than the JXL
    before = _md5(tif)
    t = time.time() + 60
    os.utime(a / "p.jxl", (t, t))

    r = _run(DECODER, "A", "--mode", "1", "--sync", "--no-overwrite-guard",
             cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    assert _md5(tif) == before, "the original master was overwritten"
    assert "KEEP (refusing to overwrite" in r.stdout, r.stdout
    assert "carries no jxlphoto-src marker" in r.stdout, r.stdout


@real
def test_r4_recompressor_different_origin(tmp_path):
    _tiff(tmp_path / "A" / "p.tif", 1000)
    _tiff(tmp_path / "B" / "p.tif", 60000)
    for n in ("A", "B"):
        assert _run(ENCODER, n, "--mode", "0", *FAST, cwd=tmp_path).returncode == 0
    common = ["--mode", "0", "--distance", "2", "--effort", "3",
              "--no-preflight", "--on-downgrade", "convert",
              "--on-unknown", "convert", "--on-regeneration", "convert"]
    r = _run(RECOMPRESSOR, "A/p.jxl", "OUT", *common, cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    out = tmp_path / "OUT" / "p.jxl"
    before = _md5(out)

    r = _run(RECOMPRESSOR, "B/p.jxl", "OUT", *common, "--overwrite", cwd=tmp_path)
    assert r.returncode == 1, r.stdout
    assert "different origin" in r.stdout, r.stdout
    assert _md5(out) == before, "the other origin's archive was overwritten"
    assert rec.GUARD_DOC_URL in r.stdout

    r = _run(RECOMPRESSOR, "B/p.jxl", "OUT", *common, "--overwrite",
             "--no-overwrite-guard", cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    assert "overwrite guard off" in r.stdout


@real
def test_r5_transcoder_lossy_different_source(tmp_path):
    _jpeg(tmp_path / "A" / "j.jpg", 1)
    _jpeg(tmp_path / "B" / "j.jpg", 2)
    common = ["--mode", "0", "--force-convert", "--distance", "1"]
    r = _run(TRANSCODER, "A/j.jpg", "OUT", *common, cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    out = tmp_path / "OUT" / "j.jxl"
    before = _md5(out)

    r = _run(TRANSCODER, "B/j.jpg", "OUT", *common, "--overwrite", cwd=tmp_path)
    assert r.returncode == 1, r.stdout
    assert "different source" in r.stdout, r.stdout
    assert _md5(out) == before

    r = _run(TRANSCODER, "B/j.jpg", "OUT", *common, "--overwrite",
             "--no-overwrite-guard", cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    assert "overwrite guard off" in r.stdout


@real
def test_r5b_jbrd_still_refused_with_guard_off(tmp_path):
    _jpeg(tmp_path / "A" / "j.jpg", 1)
    _jpeg(tmp_path / "B" / "j.jpg", 2)
    r = _run(TRANSCODER, "A/j.jpg", "OUT", "--mode", "0", "--force-convert",
             "--distance", "0", cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    out = tmp_path / "OUT" / "j.jxl"
    assert tra.has_jbrd_box(out), "fixture setup: not a jbrd archive"
    before = _md5(out)

    r = _run(TRANSCODER, "B/j.jpg", "OUT", "--mode", "0", "--force-convert",
             "--distance", "0", "--overwrite", "--no-overwrite-guard",
             cwd=tmp_path)
    assert r.returncode == 1, r.stdout
    assert "lossless archive of a DIFFERENT JPEG" in r.stdout, r.stdout
    assert _md5(out) == before, "the only copy of the other JPEG was overwritten"


# ---------------------------------------------------------------------------
# Real codecs — --restamp-moved
# ---------------------------------------------------------------------------

@real
def test_r6_restamp_rewrites_the_record_only(tmp_path):
    d = tmp_path / "lib" / "2024-05 Viagem"
    _tiff(d / "p.tif", 1000, with_exif=True)
    assert _run(ENCODER, "lib", "--mode", "8", *FAST, cwd=tmp_path).returncode == 0
    d.rename(tmp_path / "lib" / "2024-05 Kyoto")
    new_d = tmp_path / "lib" / "2024-05 Kyoto"
    jxl = new_d / "p.jxl"
    tif = new_d / "p.tif"
    png_before = tmp_path / "before.png"
    png_md5_before = _djxl_pixel_md5(jxl, png_before)
    mtime_before = jxl.stat().st_mtime_ns
    hist_before = _history_len(jxl)
    marker_before = enc._read_source_markers_batch([jxl])[str(jxl)]

    r = _run(ENCODER, "lib", "--mode", "8", *FAST, "--restamp-moved", cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    assert "RESTAMP |" in r.stdout
    after = enc._read_source_markers_batch([jxl])[str(jxl)]
    assert after["src"] == enc._source_path_id(tif)
    assert after["srcsum"] == marker_before["srcsum"]
    assert jxl.stat().st_mtime_ns == mtime_before, "the file date changed"
    assert _djxl_pixel_md5(jxl, tmp_path / "after.png") == png_md5_before
    assert _history_len(jxl) == hist_before, "the restamp wrote a History entry"
    assert enc._verify_jxl_integrity(jxl)
    boxes = _boxes(jxl)
    assert "Exif" in boxes, boxes
    assert boxes.index("Exif") < _first_codestream_box(boxes), boxes
    assert not list(new_d.glob("*.tmp"))

    after_first = _md5(jxl)
    r = _run(ENCODER, "lib", "--mode", "8", *FAST, "--restamp-moved", cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    assert "1 already correct" in r.stdout, r.stdout
    assert _md5(jxl) == after_first

    # After the restamp the re-edited photo syncs with the guard ON.
    _tiff(tif, 60000)
    t = time.time() + 60
    os.utime(tif, (t, t))
    r = _run(ENCODER, "lib", "--mode", "8", *FAST, "--sync", cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    assert "REFUSING" not in r.stdout, r.stdout


@real
def test_r6b_restamp_lists_content_differs(tmp_path):
    d = tmp_path / "lib" / "2024-05 Viagem"
    _tiff(d / "p.tif", 1000)
    assert _run(ENCODER, "lib", "--mode", "8", *FAST, cwd=tmp_path).returncode == 0
    d.rename(tmp_path / "lib" / "2024-05 Kyoto")
    tif = tmp_path / "lib" / "2024-05 Kyoto" / "p.tif"
    jxl = tmp_path / "lib" / "2024-05 Kyoto" / "p.jxl"
    _tiff(tif, 60000)                       # renamed AND edited since
    t = time.time() + 60
    os.utime(tif, (t, t))
    before = _md5(jxl)

    r = _run(ENCODER, "lib", "--mode", "8", *FAST, "--restamp-moved", cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    assert "content differs" in r.stdout, r.stdout
    assert "NOT restamped" in r.stdout, r.stdout
    assert _md5(jxl) == before, "a content mismatch must leave the master alone"


@real
def test_r6c_restamp_dry_run_does_not_touch(tmp_path):
    d = tmp_path / "lib" / "2024-05 Viagem"
    _tiff(d / "p.tif", 1000)
    assert _run(ENCODER, "lib", "--mode", "8", *FAST, cwd=tmp_path).returncode == 0
    d.rename(tmp_path / "lib" / "2024-05 Kyoto")
    jxl = tmp_path / "lib" / "2024-05 Kyoto" / "p.jxl"
    before = _md5(jxl)

    r = _run(ENCODER, "lib", "--mode", "8", *FAST, "--restamp-moved", "--dry-run",
             cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    assert "would be rewritten" in r.stdout, r.stdout
    assert _md5(jxl) == before


@real
def test_r6d_restamp_refused_with_overwrite(tmp_path):
    d = tmp_path / "lib"
    _tiff(d / "p.tif", 1000)
    assert _run(ENCODER, "lib", "--mode", "8", *FAST, cwd=tmp_path).returncode == 0
    r = _run(ENCODER, "lib", "--mode", "8", *FAST, "--restamp-moved", "--overwrite",
             cwd=tmp_path)
    assert r.returncode == 2, r.stdout
    assert "--restamp-moved only rewrites provenance records" in (r.stdout + r.stderr), \
        r.stdout + r.stderr


@real
def test_r7_adopt_preserves_mtime_and_box_order(tmp_path):
    _tiff(tmp_path / "root" / "A" / "foto.tif", 1000, with_exif=True)
    assert _run(ENCODER, "root", "--mode", "5", "--distance", "0", "--effort", "3",
                "--no-preflight", cwd=tmp_path).returncode == 0
    jxl = tmp_path / "root" / "JXL_16bits" / "foto.jxl"
    subprocess.run(["exiftool", "-q", "-overwrite_original",
                    "-XMP-dc:Relation=", str(jxl)], check=True, capture_output=True)
    mtime_before = jxl.stat().st_mtime_ns

    r = _run(ENCODER, "root", "--mode", "5", "--distance", "0", "--effort", "3",
             "--no-preflight", "--delete-source", "--delete-confirm-off",
             "--delete-skipped", "--provenance", "adopt", cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    assert _src_marker(jxl), "adoption did not stamp the output"
    assert jxl.stat().st_mtime_ns == mtime_before, "the adopt stamp changed the mtime"
    boxes = _boxes(jxl)
    assert "Exif" in boxes, boxes
    assert boxes.index("Exif") < _first_codestream_box(boxes), boxes


@real
def test_r8_false_provenance_warnings_are_gone(tmp_path):
    _tiff(tmp_path / "lib" / "a.tif", 1000)
    assert _run(ENCODER, "lib", "--mode", "0", *FAST, cwd=tmp_path).returncode == 0
    r = _run(ENCODER, "lib", "--mode", "8", *FAST, "--sync",
             "--provenance", "content", cwd=tmp_path)
    assert "has no effect" not in r.stdout, r.stdout
    e = tmp_path / "dec"
    e.mkdir()
    shutil.copy2(tmp_path / "lib" / "a.jxl", e / "a.jxl")
    r = _run(DECODER, "dec", "--mode", "0", "--provenance", "content", cwd=tmp_path)
    assert "has no effect" not in r.stdout, r.stdout
