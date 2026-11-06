"""v2.24.0: the encode record lives in xmpMM:History, not in the Title.

Windows shows XMP dc:description as a photo's Title, so every master showed
"gen=1 | cjxl d=0.05 e=9" there and a caption got the chain glued to it. The
record now ends the parameters of the History entry each write appends;
--encode-tag only decides where a VISIBLE copy goes (history = none, the
default; comment = exif:UserComment, Explorer's Comments; title = the old
dc:description copy; software; xmp = history; off = "no encode record").
Files written before keep the chain in dc:description / Software: every
reader still finds it there, and the next output moves it to the History.

Real codecs and exiftool, in a folder named with Japanese, brackets, a space
and %.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

import jxl_recompressor as rec
import jxl_tiff_encoder as enc
from _encode_record import record_fields

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "吾妻山 [hist] 100%"
CAPTION = "[minor] Captured: autumn 1958"
_TOOLS = ("cjxl", "djxl", "exiftool")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")


def _run(tmp_path, script, *args, ok=True):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs")}
    r = subprocess.run([sys.executable, str(REPO / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, env=env, stdin=subprocess.DEVNULL)
    if ok:
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return r.returncode, " ".join((r.stdout + r.stderr).split())


def _exiftool(*args):
    af = Path(os.environ.get("TEMP", ".")) / f"ehist_{os.getpid()}.args"
    af.write_text("-charset\nfilename=utf8\n" + "\n".join(map(str, args)) + "\n",
                  encoding="utf-8")
    try:
        return subprocess.run(["exiftool", "-@", str(af)], capture_output=True)
    finally:
        af.unlink()


def _tiff(path, caption=CAPTION, seed=1):
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 65535, (120, 160, 3)).astype(np.uint16)
    tifffile.imwrite(path, a, photometric="rgb", software="Capture One 23", metadata=None)
    if caption:
        _exiftool("-overwrite_original", f"-XMP-dc:Description={caption}", path)


def _history_params(path):
    out = _exiftool("-j", "-XMP-xmpMM:HistoryParameters", path).stdout
    v = json.loads(out.decode("utf-8"))[0].get("HistoryParameters")
    return [] if v is None else (v if isinstance(v, list) else [v])


# ----------------------------------------------------------------- T1-T3 encoder
@needs_tools
def test_t1_by_default_the_record_is_only_in_the_history(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    _tiff(d / "DSC_0001 (2).tif")
    _run(tmp_path, "jxl_tiff_encoder.py", d, "--distance", "1", "--effort", "3", "--no-preflight")
    f = record_fields(d / "DSC_0001 (2).jxl")
    assert f["history"] == "gen=1 | cjxl d=1.0 e=3", f
    assert f["desc"] == CAPTION, f                     # the Title is the photo's own
    assert f["software"] == "Capture One 23" and f["comment"] == "", f


@needs_tools
@pytest.mark.parametrize("mode, field, value", [
    ("comment", "comment", "gen=1 | cjxl d=1.0 e=3"),
    ("title", "desc", f"{CAPTION} | gen=1 | cjxl d=1.0 e=3"),
    ("software", "software", "Capture One 23 | gen=1 | cjxl d=1.0 e=3"),
])
def test_t2_t3_a_visible_copy_where_the_mode_says(tmp_path, mode, field, value):
    d = tmp_path / HOSTILE
    d.mkdir()
    _tiff(d / "p [1].tif")
    _run(tmp_path, "jxl_tiff_encoder.py", d, "--distance", "1", "--effort", "3",
         "--no-preflight", "--encode-tag", mode)
    f = record_fields(d / "p [1].jxl")
    assert f["history"] == "gen=1 | cjxl d=1.0 e=3", f
    assert f[field] == value, f
    others = {"comment": "", "desc": CAPTION, "software": "Capture One 23"}
    for k, v in others.items():
        if k != field:
            assert f[k] == v, (k, f)


@needs_tools
def test_t3_xmp_is_the_old_name_of_history(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    _tiff(d / "x.tif")
    _run(tmp_path, "jxl_tiff_encoder.py", d, "--distance", "1", "--effort", "3",
         "--no-preflight", "--encode-tag", "xmp")
    f = record_fields(d / "x.jxl")
    assert f["history"] == "gen=1 | cjxl d=1.0 e=3" and f["desc"] == CAPTION, f


# ------------------------------------------------------- T4 a master written before
def _legacy_master(tmp_path, d, distance="0.05"):
    """A master as v2.23.0 wrote it: the chain only in dc:Description after
    the caption, and History entries without a record."""
    _tiff(d / "old.tif")
    _run(tmp_path, "jxl_tiff_encoder.py", d, "--distance", distance, "--effort", "3",
         "--no-preflight", "--encode-tag", "title")
    m = d / "old.jxl"
    # drop the History's copy: what is left is a v2.23.0 master. Two calls: a
    # list reset and an add in ONE call only add (the #548 trap).
    _exiftool("-overwrite_original", "-XMP-xmpMM:History=", m)
    _exiftool("-overwrite_original",
              "-XMP-xmpMM:History+={Action=converted,When=2026-10-01T10:00:00+09:00,"
              "SoftwareAgent=jxl-photo 2.23.0,Parameters=jxl_tiff_encoder: from "
              "image/tiff to image/jxl}", m)
    f = record_fields(m)
    assert f["history"] == "" and f["desc"] == f"{CAPTION} | gen=1 | cjxl d={float(distance)} e=3", f
    return m


@needs_tools
def test_t4_a_legacy_master_is_read_and_its_chain_moves_to_the_history(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    m = _legacy_master(tmp_path, d)
    _run(tmp_path, "jxl_recompressor.py", m, tmp_path / "out [1]", "--mode", "0",
         "--distance", "1", "--effort", "3", "--no-preflight")
    f = record_fields(tmp_path / "out [1]" / "old.jxl")
    assert f["history"] == "gen=2 | cjxl d=0.05 e=3 | cjxl d=1.0 e=3", f
    assert f["desc"] == CAPTION, f


@needs_tools
def test_t4_the_downgrade_guard_still_sees_a_legacy_record(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    m = _legacy_master(tmp_path, d, distance="1")
    _rc, log = _run(tmp_path, "jxl_recompressor.py", m, tmp_path / "out", "--mode", "0",
                    "--distance", "1", "--effort", "3", "--no-preflight",
                    "--on-downgrade", "skip")
    assert "SKIP" in log and not (tmp_path / "out" / "old.jxl").exists(), log[-2000:]


# --------------------------------------------- T5 the regeneration guard reads it
@needs_tools
def test_t5_the_regeneration_guard_reads_the_history(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    _tiff(d / "r.tif")
    _run(tmp_path, "jxl_tiff_encoder.py", d, "--distance", "0.5", "--effort", "3", "--no-preflight")
    _run(tmp_path, "jxl_recompressor.py", d / "r.jxl", tmp_path / "g2", "--mode", "0",
         "--distance", "1", "--effort", "3", "--no-preflight", "--on-regeneration", "convert")
    assert record_fields(tmp_path / "g2" / "r.jxl")["history"].startswith("gen=2 |")
    _rc, log = _run(tmp_path, "jxl_recompressor.py", tmp_path / "g2" / "r.jxl", tmp_path / "g3",
                    "--mode", "0", "--distance", "2", "--effort", "3", "--no-preflight",
                    "--on-regeneration", "skip")
    assert "--on-regeneration" in log and not (tmp_path / "g3" / "r.jxl").exists(), log[-2000:]


# ------------------------------------------------------- T6 through the decoder
@needs_tools
def test_t6_a_decoded_tiff_carries_the_record_to_the_next_master(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    _tiff(d / "c.tif")
    _run(tmp_path, "jxl_tiff_encoder.py", d, "--distance", "1", "--effort", "3", "--no-preflight")
    _run(tmp_path, "jxl_tiff_decoder.py", d / "c.jxl", tmp_path / "dec [x]")
    t = tmp_path / "dec [x]" / "c.tif"
    _run(tmp_path, "jxl_tiff_encoder.py", t, "--mode", "0", "--distance", "1", "--effort", "3",
         "--no-preflight")
    f = record_fields(tmp_path / "dec [x]" / "c.jxl")
    assert f["history"] == "gen=2 | cjxl d=1.0 e=3 | cjxl d=1.0 e=3", f
    assert f["desc"] == CAPTION, f


# ------------------------------------------------------------------- T7 off
@needs_tools
def test_t7_off_leaves_no_record_even_with_an_older_history_entry(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    _tiff(d / "o.tif")
    _run(tmp_path, "jxl_tiff_encoder.py", d, "--distance", "1", "--effort", "3", "--no-preflight")
    _run(tmp_path, "jxl_recompressor.py", d / "o.jxl", tmp_path / "off", "--mode", "0",
         "--distance", "2", "--effort", "3", "--no-preflight", "--encode-tag", "off",
         "--on-regeneration", "convert")
    out = tmp_path / "off" / "o.jxl"
    params = _history_params(out)
    assert any("cjxl d=1.0" in p for p in params[:-1])          # the older entry is kept
    assert params[-1].endswith("| no encode record"), params
    assert record_fields(out)["history"] == ""
    info = rec._read_encode_params_batch([out])[str(out)]
    assert info["params"] is None and info["gen"] == 0, info     # the guard sees no record


# ------------------------------------------------------------------ T8 origin
@needs_tools
def test_t8_a_record_only_in_the_history_means_origin_unknown(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    _tiff(d / "u.tif")
    _exiftool("-overwrite_original",
              "-XMP-xmpMM:History+={Action=converted,When=2026-10-01T10:00:00+09:00,"
              "Parameters=jxl_tiff_encoder: from image/tiff to image/jxl || gen=1 || "
              "cjxl d=0.5 e=7}", d / "u.tif")
    tokens, has_record = enc._read_origin_inputs(d / "u.tif")
    assert has_record and not any(t.startswith("jxlphoto-origin:") for t in tokens)
    _run(tmp_path, "jxl_tiff_encoder.py", d, "--distance", "1", "--effort", "3", "--no-preflight")
    rel = _exiftool("-j", "-XMP-dc:Relation", d / "u.jxl").stdout.decode("utf-8")
    assert "jxlphoto-origin:" not in rel, rel


# ------------------------------------------------------------------- T9 units
@pytest.mark.parametrize("values, expected", [
    (None, ""),
    ("jxl_tiff_encoder: from image/tiff to image/jxl | gen=1 | cjxl d=0.05 e=9",
     "gen=1 | cjxl d=0.05 e=9"),
    (["jxl_tiff_encoder: from a to b | gen=1 | cjxl d=0.05 e=9",
      "jxl_tiff_decoder: from image/jxl to image/tiff"], "gen=1 | cjxl d=0.05 e=9"),
    (["jxl_tiff_encoder: x | gen=1 | cjxl d=0.05 e=9",
      "jxl_recompressor: y | gen=2 | cjxl d=0.05 e=9 | cjxl d=1.0 e=7"],
     "gen=2 | cjxl d=0.05 e=9 | cjxl d=1.0 e=7"),
    (["jxl_tiff_encoder: x | gen=1 | cjxl d=0.05 e=9",
      "jxl_recompressor: y | no encode record"], ""),
    (["Lightroom: Project gen=3 phase 2"], ""),
    (["jxl_tiff_encoder: x | cjxl d=0.1 e=7"], "cjxl d=0.1 e=7"),
])
def test_t9_history_record(values, expected):
    for mod in (enc, rec):
        assert mod._history_record(values) == expected


def test_t9_merge_with_the_history():
    m = rec._merge_lineage_blocks
    # a title copy mirroring the History: one history
    assert m("Cap | gen=2 | cjxl d=0.1 e=7 | cjxl d=1.0 e=7", "",
             "gen=2 | cjxl d=0.1 e=7 | cjxl d=1.0 e=7") == ([("0.1", "7"), ("1.0", "7")], 2)
    # the History extends the legacy chain: the longer one
    assert m("gen=1 | cjxl d=0.1 e=7", "", "gen=2 | cjxl d=0.1 e=7 | cjxl d=1.0 e=7") == \
        ([("0.1", "7"), ("1.0", "7")], 2)
    # split history: both, oldest first; stored gen = the max of all three
    assert m("gen=1 | cjxl d=0.1 e=7", "", "gen=5 | cjxl d=2.0 e=7") == \
        ([("0.1", "7"), ("2.0", "7")], 5)
    # history="" is the two-field merge
    assert m("gen=1 | cjxl d=0.1 e=7", "") == m("gen=1 | cjxl d=0.1 e=7", "", "")


def test_t9_the_three_writers_agree_on_the_constants():
    import jxl_jpeg_transcoder as tr
    assert enc._NO_RECORD_SEGMENT == rec._NO_RECORD_SEGMENT == "no encode record"
    assert enc._MACHINE_BLOCK_RE.pattern == rec._MACHINE_BLOCK_RE.pattern
    assert enc._ORIGIN_RECORD_RE.pattern == rec._ORIGIN_RECORD_RE.pattern == tr._ORIGIN_RECORD_RE.pattern


@pytest.mark.parametrize("mode, expected", [
    ("history", ["-XMP-dc:Description=Cap", "-Software=C1"]),
    ("xmp", ["-XMP-dc:Description=Cap", "-Software=C1"]),
    ("off", ["-XMP-dc:Description=Cap", "-Software=C1"]),
    ("title", ["-XMP-dc:Description=Cap | REC", "-Software=C1"]),
    ("comment", ["-XMP-dc:Description=Cap", "-Software=C1", "-XMP-exif:UserComment=REC"]),
    ("software", ["-XMP-dc:Description=Cap", "-Software=C1 | REC"]),
])
def test_t9_visible_record_args(mode, expected):
    for mod in (enc, rec):
        got = mod._visible_record_args(mode, "REC", "Cap | gen=1 | cjxl d=0.1 e=7",
                                       "C1 | cjxl d=0.1 e=7", "")
        assert got == expected, (mod.__name__, got)
