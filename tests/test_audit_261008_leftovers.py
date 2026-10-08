#!/usr/bin/env python3
"""What v2.8.0 left open from the 2026-10-08 audit (round 50).

  D3      the decoder wrote every 2nd/4th channel back as UNASSOCIATED alpha:
          an RGB+IR scan's IR channel (ExtraSamples=0) came back as a
          transparency mask. The encoder now records the page's ExtraSamples
          (jxlphoto-extrasamples:) and the decoder restores it.
  D5      a decoded TIFF retouched in Photoshop keeps the jxlphoto-src marker,
          so a sync decoded over the edit as soon as the JXL was newer, and
          --delete-skipped deleted the JXL on the strength of the edited TIFF.
          The decoder now records the pixels it wrote (jxlphoto-pixsum:).
  jxlp    the JXL integrity checks accepted a codestream split into jxlp boxes
          and cut exactly at a box boundary (the "last box" bit was not read).
  D-dry   the decoder's --dry-run promised to delete the JXLs that a
          --depth 8 or --basic decode keeps.
  T-a2b   the transcoder converted INTO an A2B-only --icc-profile.
  W7      Step 6 offered a derivative after [D]; the run was refused only
          after the Step 7 YES.
  W9      the manifest collision scan modelled only page 0 of a split TIFF:
          foto.tif's page 1 (foto_page1.jxl) and another entry's
          foto_page1.tif met unseen.

Real codecs where the item touches what cjxl/djxl/exiftool write; the scripts
run as subprocesses (or load in process) from JXLPHOTO_SCRIPTS_UNDER_TEST.
Pre-fix proof:
    git show HEAD:<script> > <tmp>/<script>      (all five)
    $env:JXLPHOTO_SCRIPTS_UNDER_TEST = "<tmp>"
    python -m pytest tests/test_audit_261008_leftovers.py
"""

import hashlib
import importlib.util
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _icc_fixtures import romm_toe_icc  # noqa: E402

_HAS_CODECS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
requires_codecs = pytest.mark.skipif(
    not _HAS_CODECS, reason="cjxl/djxl/exiftool not on PATH")


def _run(script, *args, timeout=900):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *map(str, args)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, stdin=subprocess.DEVNULL)


def _out(r):
    return (r.stdout or "") + (r.stderr or "")


def _relation(path: Path) -> str:
    return subprocess.run(["exiftool", "-s3", "-XMP-dc:Relation", str(path)],
                          capture_output=True, text=True).stdout


def _md5(p: Path) -> str:
    return hashlib.md5(Path(p).read_bytes()).hexdigest()


def _load(name: str):
    """A script under test, loaded in process from SCRIPTS under its own
    module name (so the pre-fix copy never shadows the repo's)."""
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(
            f"left261008_{name}", str(SCRIPTS / f"{name}.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(SCRIPTS))
    return mod


# ---------------------------------------------------------------------------
# D3 — the role of a 2nd/4th channel survives TIFF -> JXL -> TIFF
# ---------------------------------------------------------------------------

def _extra_channel_tiff(path: Path, extrasamples, channels: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(3)
    a = rng.integers(1000, 60000, (40, 56, channels), dtype=np.uint16)
    tifffile.imwrite(str(path), a, photometric="rgb" if channels == 4 else "minisblack",
                     extrasamples=extrasamples, metadata=None)
    return path


def _extrasamples(tif: Path, page: int = 0) -> tuple:
    with tifffile.TiffFile(str(tif)) as t:
        return tuple(int(v) for v in (t.pages[page].extrasamples or ()))


@requires_codecs
@pytest.mark.parametrize("es,channels,distance", [
    ((0,), 4, "0"),      # RGB + IR (VueScan RGBI layout), lossless
    ((0,), 4, "0.5"),    # ... and lossy
    ((1,), 4, "0"),      # associated alpha
    ((0,), 2, "0"),      # grey + IR
])
def test_d3_extrasamples_survive_the_round_trip(tmp_path, es, channels, distance):
    src = _extra_channel_tiff(tmp_path / "t" / "scan.tif", es, channels)
    r = _run("jxl_tiff_encoder.py", src.parent, tmp_path / "m", "--mode", "2",
             "--distance", distance, "--no-preflight")
    assert r.returncode == 0, _out(r)
    jxl = tmp_path / "m" / "scan.jxl"
    r = _run("jxl_tiff_decoder.py", jxl.parent, tmp_path / "d", "--mode", "2")
    assert r.returncode == 0, _out(r)
    tif = tmp_path / "d" / "scan.tif"
    assert _extrasamples(tif) == es, "the channel came back with another role"
    assert "jxlphoto-extrasamples" not in _relation(tif), "marker leaked into the TIFF"


@requires_codecs
def test_d3_reencoding_the_decoded_tiff_records_the_role_once(tmp_path):
    src = _extra_channel_tiff(tmp_path / "t" / "scan.tif", (0,), 4)
    assert _run("jxl_tiff_encoder.py", src.parent, tmp_path / "m", "--mode", "2",
                "--distance", "0", "--no-preflight").returncode == 0
    assert _run("jxl_tiff_decoder.py", tmp_path / "m", tmp_path / "d",
                "--mode", "2").returncode == 0
    r = _run("jxl_tiff_encoder.py", tmp_path / "d", tmp_path / "m2", "--mode", "2",
             "--distance", "0", "--no-preflight")
    assert r.returncode == 0, _out(r)
    rel = _relation(tmp_path / "m2" / "scan.jxl")
    assert rel.count("jxlphoto-extrasamples:0") == 1, rel
    assert "jxlphoto-pixsum" not in rel, "the decoder's TIFF record reached a master"


@requires_codecs
def test_d3_multipage_group_restores_the_role(tmp_path):
    src = tmp_path / "t" / "scan.tif"
    src.parent.mkdir()
    rng = np.random.default_rng(5)
    with tifffile.TiffWriter(str(src)) as w:
        w.write(rng.integers(0, 65535, (40, 56, 4), dtype=np.uint16),
                photometric="rgb", extrasamples=(0,), metadata=None)
        w.write(rng.integers(0, 65535, (40, 56), dtype=np.uint16),
                photometric="minisblack", metadata=None)
    r = _run("jxl_tiff_encoder.py", src.parent, tmp_path / "m", "--mode", "2",
             "--distance", "0", "--no-preflight")
    assert r.returncode == 0, _out(r)
    assert (tmp_path / "m" / "scan_page1.jxl").exists(), _out(r)
    r = _run("jxl_tiff_decoder.py", tmp_path / "m", tmp_path / "d", "--mode", "2")
    assert r.returncode == 0, _out(r)
    tif = tmp_path / "d" / "scan.tif"
    with tifffile.TiffFile(str(tif)) as t:
        assert len(t.pages) == 2, "the group was not reconstructed"
    assert _extrasamples(tif, 0) == (0,)


# ---------------------------------------------------------------------------
# D5 — a decoded TIFF edited since the decode
# ---------------------------------------------------------------------------

def _decoded_pair(tmp_path):
    src = tmp_path / "t" / "foto.tif"
    src.parent.mkdir()
    rng = np.random.default_rng(9)
    tifffile.imwrite(str(src), rng.integers(0, 65535, (48, 64, 3), dtype=np.uint16),
                     photometric="rgb", metadata=None)
    r = _run("jxl_tiff_encoder.py", src.parent, tmp_path / "m", "--mode", "2",
             "--distance", "0", "--no-preflight")
    assert r.returncode == 0, _out(r)
    r = _run("jxl_tiff_decoder.py", tmp_path / "m", tmp_path / "d", "--mode", "2")
    assert r.returncode == 0, _out(r)
    return tmp_path / "m" / "foto.jxl", tmp_path / "d" / "foto.tif"


def _retouch(tif: Path):
    """An edit saved the way Photoshop saves one: new pixels, the same XMP
    (our jxlphoto-src marker included)."""
    orig = tif.with_name("orig_copy.tif")
    shutil.copy2(tif, orig)
    a = tifffile.imread(str(tif)).copy()
    a[:8, :8] = 0
    tifffile.imwrite(str(tif), a, photometric="rgb", metadata=None)
    subprocess.run(["exiftool", "-overwrite_original", "-tagsfromfile", str(orig),
                    "-all:all", str(tif)], check=True, capture_output=True)
    orig.unlink()
    assert "jxlphoto-src:" in _relation(tif), "fixture: the edit lost the marker"


def _make_newer(newer: Path, than: Path):
    t = than.stat().st_mtime + 120
    os.utime(newer, (t, t))


@requires_codecs
def test_d5_sync_does_not_decode_over_an_edited_tiff(tmp_path):
    jxl, tif = _decoded_pair(tmp_path)
    _retouch(tif)
    _make_newer(jxl, tif)
    before = _md5(tif)
    r = _run("jxl_tiff_decoder.py", tmp_path / "m", tmp_path / "d", "--mode", "2")
    assert _md5(tif) == before, "the sync decoded over the edited TIFF"
    assert "EDITED" in _out(r), _out(r)


@requires_codecs
def test_d5_an_untouched_decode_is_still_refreshed(tmp_path):
    jxl, tif = _decoded_pair(tmp_path)
    _make_newer(jxl, tif)
    r = _run("jxl_tiff_decoder.py", tmp_path / "m", tmp_path / "d", "--mode", "2")
    assert r.returncode == 0, _out(r)
    assert "JXL newer than TIFF, reconverting" in _out(r), _out(r)
    assert "EDITED" not in _out(r)


@requires_codecs
def test_d5_overwrite_still_replaces_an_edited_tiff(tmp_path):
    jxl, tif = _decoded_pair(tmp_path)
    _retouch(tif)
    r = _run("jxl_tiff_decoder.py", tmp_path / "m", tmp_path / "d", "--mode", "2",
             "--overwrite")
    assert r.returncode == 0, _out(r)
    assert tifffile.imread(str(tif))[:8, :8].any(), "--overwrite kept the edit"


@requires_codecs
def test_d5_delete_skipped_keeps_the_jxl_of_an_edited_tiff(tmp_path):
    jxl, tif = _decoded_pair(tmp_path)
    _retouch(tif)            # the TIFF is now newer than the JXL: a sync skip
    r = _run("jxl_tiff_decoder.py", tmp_path / "m", tmp_path / "d", "--mode", "2",
             "--delete-source", "--delete-skipped", "--delete-confirm-off")
    assert jxl.exists(), "the JXL was deleted on the strength of an edited TIFF"
    assert "EDITED" in _out(r), _out(r)


@requires_codecs
def test_d5_delete_skipped_still_deletes_with_an_untouched_decode(tmp_path):
    jxl, tif = _decoded_pair(tmp_path)
    r = _run("jxl_tiff_decoder.py", tmp_path / "m", tmp_path / "d", "--mode", "2",
             "--delete-source", "--delete-skipped", "--delete-confirm-off")
    assert r.returncode == 0, _out(r)
    assert not jxl.exists(), _out(r)


@requires_codecs
def test_d5_the_pixel_record_matches_the_tiff(tmp_path):
    dec = _load("jxl_tiff_decoder")
    _jxl, tif = _decoded_pair(tmp_path)
    rel = _relation(tif)
    assert "jxlphoto-pixsum:1:" in rel, rel
    assert dec._decoded_tiff_edited(tif) is None


# ---------------------------------------------------------------------------
# jxlp — a codestream cut at a box boundary
# ---------------------------------------------------------------------------

def _boxes(data: bytes):
    i = 12
    while i < len(data):
        size = struct.unpack(">I", data[i:i + 4])[0]
        btype = data[i + 4:i + 8]
        head = 8
        if size == 1:
            size = struct.unpack(">Q", data[i + 8:i + 16])[0]
            head = 16
        elif size == 0:
            size = len(data) - i
        yield i, size, head, btype
        i += size


def _jxlp_files(tmp_path):
    """(whole, cut): the same JXL with its codestream in two jxlp boxes, and
    the file cut right after the first one (a well-formed chain to EOF)."""
    png = tmp_path / "p.png"
    from PIL import Image
    rng = np.random.default_rng(2)
    Image.fromarray(rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)).save(png)
    src = tmp_path / "src.jxl"
    r = subprocess.run(["cjxl", str(png), str(src), "-d", "1", "--container=1"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    data = src.read_bytes()
    jxlc = [(i, s, h) for i, s, h, t in _boxes(data) if t == b"jxlc"]
    assert len(jxlc) == 1, "fixture: no jxlc box"
    i, size, head = jxlc[0]
    payload = data[i + head:i + size]
    half = len(payload) // 2
    p1 = struct.pack(">I", 12 + half) + b"jxlp" + struct.pack(">I", 0) + payload[:half]
    p2 = (struct.pack(">I", 12 + len(payload) - half) + b"jxlp"
          + struct.pack(">I", 1 | 0x80000000) + payload[half:])
    whole = tmp_path / "whole.jxl"
    whole.write_bytes(data[:i] + p1 + p2 + data[i + size:])
    cut = tmp_path / "cut.jxl"
    cut.write_bytes(data[:i] + p1)
    r = subprocess.run(["djxl", str(whole), str(tmp_path / "w.png")],
                       capture_output=True, text=True)
    assert r.returncode == 0, "fixture: the jxlp split does not decode: " + r.stderr
    return whole, cut


@requires_codecs
@pytest.mark.parametrize("script,func", [
    ("jxl_tiff_encoder", "_verify_jxl_integrity"),
    ("jxl_recompressor", "_verify_jxl_integrity"),
    ("jxl_jpeg_transcoder", "_verify_file_integrity"),
])
def test_jxlp_cut_at_a_box_boundary_fails_the_integrity_check(tmp_path, script, func):
    whole, cut = _jxlp_files(tmp_path)
    check = getattr(_load(script), func)
    assert check(whole) is True
    assert check(cut) is False, "a codestream missing its last jxlp box passed"


# ---------------------------------------------------------------------------
# D-dry — the decoder's dry run previews the degraded-decode keeps
# ---------------------------------------------------------------------------

def _master(tmp_path, distance="0", icc=None) -> Path:
    src = tmp_path / "t" / "foto.tif"
    src.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(4)
    extra = [(34675, "B", len(icc), icc, False)] if icc else []
    tifffile.imwrite(str(src), rng.integers(0, 65535, (48, 64, 3), dtype=np.uint16),
                     photometric="rgb", metadata=None, extratags=extra)
    r = _run("jxl_tiff_encoder.py", src.parent, tmp_path / "m", "--mode", "2",
             "--distance", distance, "--no-preflight")
    assert r.returncode == 0, _out(r)
    return tmp_path / "m" / "foto.jxl"


@requires_codecs
def test_dry_run_previews_the_depth8_keep(tmp_path):
    jxl = _master(tmp_path)
    r = _run("jxl_tiff_decoder.py", jxl.parent, "--mode", "8", "--depth", "8",
             "--delete-source", "--dry-run")
    assert r.returncode == 0, _out(r)
    assert "Up to 0 source JXL(s) would be DELETED" in _out(r), _out(r)
    assert "would be KEPT: --depth 8" in _out(r), _out(r)
    assert jxl.exists()


@requires_codecs
def test_dry_run_previews_the_basic_keep(tmp_path):
    jxl = _master(tmp_path, distance="1", icc=romm_toe_icc())
    r = _run("jxl_tiff_decoder.py", jxl.parent, "--mode", "8", "--basic",
             "--delete-source", "--dry-run")
    assert r.returncode == 0, _out(r)
    assert "may be KEPT" in _out(r), _out(r)


# ---------------------------------------------------------------------------
# T-a2b — the transcoder never converts INTO an A2B-only profile
# ---------------------------------------------------------------------------

def test_transcoder_refuses_an_a2b_only_icc_profile(tmp_path):
    from test_audit_261008_r1 import a2b_only_icc
    p = tmp_path / "scanner.icc"
    p.write_bytes(a2b_only_icc())
    (tmp_path / "in").mkdir()
    r = _run("jxl_jpeg_transcoder.py", tmp_path / "in", "--icc-profile", p)
    assert r.returncode == 2, _out(r)
    assert "no B2A" in _out(r)


def test_transcoder_still_accepts_a_matrix_icc_profile(tmp_path):
    p = tmp_path / "romm.icc"
    p.write_bytes(romm_toe_icc())
    (tmp_path / "in").mkdir()
    r = _run("jxl_jpeg_transcoder.py", tmp_path / "in", "--icc-profile", p)
    assert "no B2A" not in _out(r), _out(r)


# ---------------------------------------------------------------------------
# W7 — Step 6 offers no derivative once [D] is on
# ---------------------------------------------------------------------------

@pytest.fixture
def wp():
    return _load("jxl_photo")


@pytest.fixture
def menu(wp, tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


_SEEDED = {"output_icc": "sRGB", "rename_from": "ProPhoto", "rename_to": "sRGB",
           "resize_mode": "long", "resize_value": 2048, "sharpen": "screen"}


def _step6_workflow(tmp_path, origin, dest, conv, mode=7):
    adv = dict(_SEEDED)
    adv["delete_source"] = True
    return {
        "origin_format": origin, "dest_format": dest, "mode": mode,
        "conversion_type": conv, "input_dir": str(tmp_path),
        "workers": 2, "effort": 7, "distance": 1.0, "quality": 95,
        "staging": None, "bit_depth": 16, "compression": "zip",
        "use_ram": True, "dry_run": False, "mode_config": {},
        "delete_source": True, "icc_profile": "sRGB",
        "advanced_options": adv,
    }


def _record_rich(wp, monkeypatch):
    asked = []

    def ask(prompt, *a, **k):
        asked.append(str(prompt))
        if "Output colour space" in str(prompt):
            return "sRGB"
        if "Resize" == str(prompt):
            return "long"
        return k.get("default", "")

    def confirm(prompt, *a, **k):
        asked.append(str(prompt))
        return True

    monkeypatch.setattr(wp.Prompt, "ask", staticmethod(ask))
    monkeypatch.setattr(wp.IntPrompt, "ask",
                        staticmethod(lambda *a, **k: int(k.get("default", 1) or 1)))
    monkeypatch.setattr(wp.Confirm, "ask", staticmethod(confirm))
    return asked


_DERIV_PROMPTS = ("Output colour space", "Convert to sRGB", "Resize")


@pytest.mark.parametrize("origin,dest,conv", [
    ("jxl", "jxl", "jxl_recompress"),
    ("jxl", "jpeg", "jxl_to_jpeg_auto"),
])
def test_w7_step6_offers_no_derivative_with_delete_rich(wp, menu, tmp_path, monkeypatch,
                                                        origin, dest, conv):
    if not wp.RICH_AVAILABLE:
        pytest.skip("rich branch")
    asked = _record_rich(wp, monkeypatch)
    wf = _step6_workflow(tmp_path, origin, dest, conv)
    menu._wizard_parameters_basic(wf, {"cjxl": True, "magick": True})
    assert not [p for p in asked if p.startswith(_DERIV_PROMPTS)], asked
    assert not any(k in wf["advanced_options"] for k in _SEEDED), wf["advanced_options"]
    if dest != "jxl":
        assert not wf.get("icc_profile"), "Convert to sRGB survived [D]"


@pytest.mark.parametrize("origin,dest,conv", [
    ("jxl", "jxl", "jxl_recompress"),
    ("jxl", "jpeg", "jxl_to_jpeg_auto"),
])
def test_w7_step6_offers_no_derivative_with_delete_plain(wp, menu, tmp_path, monkeypatch,
                                                         origin, dest, conv):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    asked = []

    def _input(prompt=""):
        asked.append(str(prompt))
        return ""

    monkeypatch.setattr("builtins.input", _input)
    wf = _step6_workflow(tmp_path, origin, dest, conv)
    menu._wizard_parameters_basic(wf, {"cjxl": True, "magick": True})
    assert not [p for p in asked if p.startswith(_DERIV_PROMPTS)], asked
    assert not any(k in wf["advanced_options"] for k in _SEEDED), wf["advanced_options"]
    if dest != "jxl":
        assert not wf.get("icc_profile"), "Convert to sRGB survived [D]"


# ---------------------------------------------------------------------------
# W9 — split pages in the manifest collision scan
# ---------------------------------------------------------------------------

def _two_page_tiff(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    a = np.zeros((16, 16, 3), np.uint16)
    with tifffile.TiffWriter(str(path)) as w:
        w.write(a, photometric="rgb", metadata=None)
        w.write(a, photometric="rgb", metadata=None)
    return path


def _one_page_tiff(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.zeros((16, 16, 3), np.uint16), photometric="rgb",
                     metadata=None)
    return path


_TIF_EXTS = {".tif", ".tiff"}


def test_w9_a_split_page_landing_on_another_entry_is_a_collision(menu, tmp_path):
    a, b, out = tmp_path / "A", tmp_path / "B", tmp_path / "out"
    _two_page_tiff(a / "foto.tif")
    _one_page_tiff(b / "foto_page1.tif")
    out.mkdir()
    entries = [(str(a), str(out), 2), (str(b), str(out), 2)]
    cols = menu._manifest_output_collisions(entries, _TIF_EXTS, origin="tiff", dest="jxl")
    assert cols, "foto.tif page 1 and foto_page1.tif both write foto_page1.jxl"
    names = {Path(x).name for c in cols for x in c[:2]}
    assert names == {"foto.tif", "foto_page1.tif"}, cols


def test_w9_no_collision_when_the_run_encodes_page_0_only(menu, tmp_path):
    a, b, out = tmp_path / "A", tmp_path / "B", tmp_path / "out"
    _two_page_tiff(a / "foto.tif")
    _one_page_tiff(b / "foto_page1.tif")
    out.mkdir()
    entries = [(str(a), str(out), 2), (str(b), str(out), 2)]
    assert not menu._manifest_output_collisions(
        entries, _TIF_EXTS, origin="tiff", dest="jxl",
        page_options={"multipage_mode": "ignore"})


def test_w9_a_single_page_base_is_not_a_collision(menu, tmp_path):
    a, b, out = tmp_path / "A", tmp_path / "B", tmp_path / "out"
    _one_page_tiff(a / "foto.tif")          # writes foto.jxl only
    _one_page_tiff(b / "foto_page1.tif")
    out.mkdir()
    entries = [(str(a), str(out), 2), (str(b), str(out), 2)]
    assert not menu._manifest_output_collisions(entries, _TIF_EXTS,
                                                origin="tiff", dest="jxl")


@pytest.mark.parametrize("mp", ["ignore", "skip", "split", "split_all", "bogus"])
@pytest.mark.parametrize("tm", ["exclude", "include"])
@pytest.mark.parametrize("real,thumbs", [
    ([0], []), ([0, 1], []), ([0], [1]), ([], [0]), ([0, 2], [1]),
    ([1], [0]), ([], []), ([0, 1, 2], [3]),
])
def test_planned_page_names_match_the_planner(tmp_path, monkeypatch, mp, tm, real, thumbs):
    """_planned_page_names is the planner's naming without its side effects;
    the wrapper's collision scan trusts it, so the two must agree."""
    import jxl_tiff_encoder as enc
    tif = _one_page_tiff(tmp_path / "foto.tif")
    info = {i: {"subfiletype": 1 if i in thumbs else 0, "samples": 3}
            for i in real + thumbs}
    monkeypatch.setattr(enc, "_analyze_tiff_pages", lambda p: (list(real), list(thumbs), info))
    monkeypatch.setattr(enc, "MULTIPAGE_TIFF_MODE", mp)
    monkeypatch.setattr(enc, "THUMBNAIL_MODE", tm)
    try:
        planned = [Path(t[1]).name for t in enc.convert_multipage(tif, tmp_path / "o")]
    except enc.UnreadableTiff:
        planned = []
    assert planned == enc._planned_page_names(tif.stem, list(real), list(thumbs))
