"""MASTER_MAX_DISTANCE (2.31.1-dev3, the owner's call, 2026-11-28): above a
distance, a JXL is not an archive of its TIFF, and no delete may rest on it.

The encoder writes the JXL as usual (same markers, same record) and its delete
gate keeps the TIFF: a WARNING per file naming the setting, one line at the end,
a warning at the start of a deleting run — never an ERROR, never another exit
code. Each TIFF is judged by its own class's distance (--distance,
--scan-distance, --eight-bit-distance); with --delete-skipped an existing output
by the distance its OWN encode record names (no record: kept unless lossless;
a record that cannot be read: kept).

Real cjxl, djxl and exiftool; every fixture inside a folder named with
Japanese, brackets, a space and `100%off %d`.
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

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import jxl_tiff_encoder as enc  # noqa: E402

pytestmark = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool")),
    reason="needs cjxl, djxl and exiftool on PATH")

HOSTILE = "250118_吾妻山公園 [FINAL] 100%off %d"
COMMON = ["--mode", "0", "--effort", "1", "--workers", "1", "--no-preflight"]
DELETE = ["--delete-source", "--delete-confirm-off"]
PHOTO_SW = "Capture One 23 Windows"
SCAN_SW = "SilverFast 8.8.0"


def _photo(h=200, w=280, seed=1, dtype=np.uint16):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    base = np.stack([x * 200 // w, y * 200 // h, (x + y) * 100 // (w + h)], -1)
    a = base * 257 + rng.integers(0, 4000, (h, w, 3))
    return (a if dtype == np.uint16 else a // 257).astype(dtype)


def _tiff(path, software=PHOTO_SW, dtype=np.uint16, seed=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    a = _photo(seed=seed, dtype=dtype)
    tifffile.imwrite(path, a, photometric="rgb", software=software, metadata=None)
    return path


def _folders(tmp_path, name="case"):
    src = tmp_path / HOSTILE / name / "TIFF16"
    out = tmp_path / HOSTILE / name / "JXL"
    src.mkdir(parents=True)
    return src, out


def _run(*args, script="jxl_tiff_encoder.py", env=None):
    return subprocess.run([sys.executable, str(REPO / script), *map(str, args)],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600,
                          env={**os.environ, **(env or {})})


def _log_files_text(folder):
    return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in sorted(Path(folder).rglob("*.log")))


def _log(r):
    return (r.stdout or "") + (r.stderr or "")


def _flat(text):
    return " ".join(text.split())


def _encoder_main(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["jxl_tiff_encoder.py", *map(str, args)])
    try:
        enc.main()
    except SystemExit as e:
        return e.code or 0
    return 0


def _exif(tmp, *args):
    """exiftool through a UTF-8 argfile (in the test's own `tmp`): the paths
    are Japanese."""
    af = Path(tmp) / f"mmd_{os.getpid()}_{id(args)}.args"
    af.write_text("-charset\nfilename=utf8\n" + "\n".join(map(str, args)) + "\n",
                  encoding="utf-8")
    try:
        return subprocess.run(["exiftool", "-@", str(af)], capture_output=True).stdout
    finally:
        af.unlink()


def _relation(tmp, path):
    d = json.loads(_exif(tmp, "-j", "-XMP-dc:Relation", path))[0]
    rel = d.get("Relation") or []
    return [rel] if isinstance(rel, str) else [str(t) for t in rel]


def _keep_lines(log, name):
    return [ln for ln in log.splitlines()
            if "KEEP source" in ln and "MASTER_MAX_DISTANCE" in ln and name in ln]


# --- 1 ---------------------------------------------------------------------------

def test_above_the_limit_the_tiff_is_kept_and_its_jxl_is_a_normal_master(tmp_path):
    src, out = _folders(tmp_path)
    tif = _tiff(src / "DSC_0001 (2).tif")
    r = _run(src, out, *COMMON, "--distance", "1", *DELETE)
    log = _log(r)
    assert r.returncode == 0, log[-3000:]
    assert tif.exists(), "a TIFF encoded above MASTER_MAX_DISTANCE was deleted"
    jxl = out / "DSC_0001 (2).jxl"
    assert jxl.exists()
    rel = _relation(tmp_path, jxl)
    assert sum(t.startswith(enc.SRC_PREFIX) for t in rel) == 1, rel
    assert sum(t.startswith(enc.SRCSUM_PREFIX) for t in rel) == 1, rel
    assert not any(t.startswith(enc.DERIVED_XMP_PREFIX) for t in rel), rel
    assert _keep_lines(log, tif.name), log[-3000:]
    assert any(ln.split(" | ", 2)[-1].startswith("Kept above MASTER_MAX_DISTANCE")
               for ln in log.splitlines()), log[-3000:]
    lines = log.splitlines()
    first_file = next(i for i, ln in enumerate(lines) if "[1/1]" in ln)
    assert any("MASTER_MAX_DISTANCE" in ln for ln in lines[:first_file]), log[-3000:]
    assert "| ERROR |" not in log
    assert "Traceback" not in log


# --- 2 (control: passes before the change too) -------------------------------------

# The limit itself (before the change the module has no such name: the
# shipped --distance, which the limit equals, so the control still collects).
_AT_LIMIT = (f"{enc.MASTER_MAX_DISTANCE:g}" if getattr(enc, "MASTER_MAX_DISTANCE", None)
             is not None else f"{enc.CJXL_DISTANCE:g}")


@pytest.mark.parametrize("distance", [_AT_LIMIT, "0.05", "0"])
def test_at_or_below_the_limit_the_tiff_is_deleted_as_before(tmp_path, distance):
    src, out = _folders(tmp_path)
    tif = _tiff(src / "DSC_0001 (2).tif")
    r = _run(src, out, *COMMON, "--distance", distance, *DELETE)
    log = _log(r)
    assert r.returncode == 0, log[-3000:]
    assert not tif.exists(), log[-3000:]
    assert (out / "DSC_0001 (2).jxl").exists()
    assert "MASTER_MAX_DISTANCE" not in log


# --- 3 ---------------------------------------------------------------------------

@pytest.mark.parametrize("flags, kept", [
    (["--distance", "1"], {"eight.tif", "photo.tif"}),
    (["--distance", "0.05", "--scan-distance", "1"], {"scan.tif"}),
    (["--distance", "0.05", "--eight-bit-distance", "1"], {"eight.tif"}),
])
def test_each_class_is_judged_by_its_own_distance(tmp_path, flags, kept):
    src, out = _folders(tmp_path)
    _tiff(src / "scan.tif", software=SCAN_SW, seed=2)
    _tiff(src / "eight.tif", dtype=np.uint8, seed=3)
    _tiff(src / "photo.tif", seed=4)
    r = _run(src, out, *COMMON, *flags, *DELETE)
    log = _log(r)
    assert r.returncode == 0, log[-3000:]
    for name in ("scan.tif", "eight.tif", "photo.tif"):
        assert (out / name.replace(".tif", ".jxl")).exists(), name
        if name in kept:
            assert (src / name).exists(), f"{name} was deleted\n{log[-3000:]}"
            assert len(_keep_lines(log, name)) == 1, (name, log[-3000:])
        else:
            assert not (src / name).exists(), f"{name} was kept\n{log[-3000:]}"
            assert not _keep_lines(log, name), (name, log[-3000:])


# --- 4 ---------------------------------------------------------------------------

def test_delete_skipped_judges_the_outputs_own_recorded_distance(tmp_path):
    src, out = _folders(tmp_path)
    tif = _tiff(src / "DSC_0001 (2).tif")
    r = _run(src, out, *COMMON, "--distance", "3")
    assert r.returncode == 0, _log(r)[-3000:]
    jxl = out / "DSC_0001 (2).jxl"
    before = jxl.read_bytes()
    r = _run(src, out, *COMMON, "--distance", "0.05", "--sync", "--delete-skipped", *DELETE)
    log = _flat(_log(r))
    assert tif.exists(), "deleted on an output recorded at distance 3"
    assert jxl.read_bytes() == before
    assert "was encoded at distance 3" in log and "MASTER_MAX_DISTANCE" in log, log[-3000:]
    assert r.returncode == 0, log[-3000:]


# --- 5 (control: passes before the change too) -------------------------------------

def test_delete_skipped_at_a_high_distance_still_deletes_an_archive_within_the_limit(tmp_path):
    src, out = _folders(tmp_path)
    tif = _tiff(src / "DSC_0001 (2).tif")
    r = _run(src, out, *COMMON, "--distance", "0.05")
    assert r.returncode == 0, _log(r)[-3000:]
    r = _run(src, out, *COMMON, "--distance", "3", "--sync", "--delete-skipped", *DELETE)
    log = _log(r)
    assert r.returncode == 0, log[-3000:]
    assert not tif.exists(), log[-3000:]
    assert (out / "DSC_0001 (2).jxl").exists()
    assert "MASTER_MAX_DISTANCE" not in log


# --- 6 ---------------------------------------------------------------------------

@pytest.mark.parametrize("distance, kept", [("0.05", True), ("0", False)])
def test_an_output_without_a_record_keeps_its_tiff_unless_it_is_lossless(tmp_path, distance, kept):
    if not kept and shutil.which("jxlinfo") is None:
        pytest.skip("needs jxlinfo")
    src, out = _folders(tmp_path)
    tif = _tiff(src / "DSC_0001 (2).tif")
    r = _run(src, out, *COMMON, "--distance", distance, "--encode-tag", "off")
    assert r.returncode == 0, _log(r)[-3000:]
    r = _run(src, out, *COMMON, "--distance", distance, "--encode-tag", "off",
             "--sync", "--delete-skipped", *DELETE)
    log = _flat(_log(r))
    assert r.returncode == 0, log[-3000:]
    if kept:
        assert tif.exists(), log[-3000:]
        assert "carries no encode record" in log, log[-3000:]
    else:
        assert not tif.exists(), log[-3000:]


# --- 7 ---------------------------------------------------------------------------

def test_the_gate_never_starts_the_pixel_check_of_a_tiff_above_the_limit(tmp_path, monkeypatch):
    src, out = _folders(tmp_path)
    tif = _tiff(src / "DSC_0001 (2).tif")
    calls = []

    def recorder(*a, **k):
        calls.append(a)
        return (True, "recorded")

    monkeypatch.setattr(enc, "_verify_roundtrip_page", recorder)
    code = _encoder_main(monkeypatch, src, out, *COMMON, "--distance", "1", *DELETE)
    assert tif.exists()
    assert calls == [], "the round-trip check of a TIFF above the limit was started"
    assert code == 0
    assert enc._delete_stats["kept"] == 1, enc._delete_stats
    assert getattr(enc, "_master_limit_kept", {}).get("count") == 1


# --- 8 ---------------------------------------------------------------------------

def test_the_limit_is_read_from_the_setting(tmp_path, monkeypatch):
    # (a) a raised limit lets a d=0.5 TIFF go
    src, out = _folders(tmp_path, "a")
    tif = _tiff(src / "DSC_0001 (2).tif")
    monkeypatch.setattr(enc, "MASTER_MAX_DISTANCE", 1.0, raising=False)
    assert _encoder_main(monkeypatch, src, out, *COMMON, "--distance", "0.5", *DELETE) == 0
    assert not tif.exists(), "MASTER_MAX_DISTANCE = 1.0 kept a TIFF encoded at 0.5"
    # (b) a lowered limit keeps a d=0.05 TIFF
    src, out = _folders(tmp_path, "b")
    tif = _tiff(src / "DSC_0002 (2).tif", seed=2)
    monkeypatch.setattr(enc, "MASTER_MAX_DISTANCE", 0.02, raising=False)
    assert _encoder_main(monkeypatch, src, out, *COMMON, "--distance", "0.05", *DELETE) == 0
    assert tif.exists(), "MASTER_MAX_DISTANCE = 0.02 deleted a TIFF encoded at 0.05"
    # (c) None: no limit
    src, out = _folders(tmp_path, "c")
    tif = _tiff(src / "DSC_0003 (2).tif", seed=3)
    monkeypatch.setattr(enc, "MASTER_MAX_DISTANCE", None, raising=False)
    assert _encoder_main(monkeypatch, src, out, *COMMON, "--distance", "3", *DELETE) == 0
    assert not tif.exists(), "MASTER_MAX_DISTANCE = None kept a TIFF"


# --- 9 ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["abc", -1, 26, True])
def test_a_bad_setting_stops_the_run_before_any_file(tmp_path, monkeypatch, bad):
    src, out = _folders(tmp_path)
    tif = _tiff(src / "DSC_0001 (2).tif")
    monkeypatch.setattr(enc, "MASTER_MAX_DISTANCE", bad, raising=False)
    code = _encoder_main(monkeypatch, src, out, *COMMON, "--distance", "0.05", *DELETE)
    assert code == 2
    assert not (out.exists() and list(out.rglob("*.jxl"))), "a JXL was written"
    assert tif.exists()


# --- 10 --------------------------------------------------------------------------

def test_a_dry_run_says_what_the_limit_keeps(tmp_path):
    # (a) the start warning, nothing written
    src, out = _folders(tmp_path, "a")
    _tiff(src / "DSC_0001 (2).tif")
    r = _run(src, out, *COMMON, "--distance", "1", "--delete-source", "--dry-run")
    log = _log(r)
    assert r.returncode == 0, log[-3000:]
    assert any("MASTER_MAX_DISTANCE" in ln and "1 of 1 TIFF(s)" in ln
               for ln in log.splitlines()), log[-3000:]
    assert not (out.exists() and list(out.rglob("*.jxl")))
    # (b) the --delete-skipped preview over an output recorded at distance 3
    src, out = _folders(tmp_path, "b")
    tif = _tiff(src / "DSC_0002 (2).tif", seed=2)
    r = _run(src, out, *COMMON, "--distance", "3")
    assert r.returncode == 0, _log(r)[-3000:]
    r = _run(src, out, *COMMON, "--distance", "0.05", "--sync", "--delete-source",
             "--delete-skipped", "--dry-run")
    log = _log(r)
    assert r.returncode == 0, log[-3000:]
    lines = [_flat(ln) for ln in log.splitlines()]
    assert any("would KEEP (" in ln and "MASTER_MAX_DISTANCE" in ln and tif.name in ln
               for ln in lines), log[-3000:]
    assert not any("would DELETE |" in ln and tif.name in ln for ln in lines), log[-3000:]
    assert tif.exists()


# --- 11 (no codec) ----------------------------------------------------------------

def test_master_limit_reasons_rules(tmp_path, monkeypatch):
    enc.setup_logger()
    tif = str(tmp_path / HOSTILE / "DSC_0001 (2).tif")
    jxl = str(tmp_path / HOSTILE / "DSC_0001 (2).jxl")
    jxl2 = str(tmp_path / HOSTILE / "DSC_0001 (2)_page1.jxl")
    asked = []
    state = {"recorded": {}, "lossless": None}

    def reader(paths):
        asked.append(list(paths))
        return {str(p): state["recorded"].get(str(p), False) for p in paths}

    monkeypatch.setattr(enc, "_read_encode_distance_batch", reader)
    monkeypatch.setattr(enc, "_jxl_is_lossless", lambda p: state["lossless"])
    monkeypatch.setattr(enc, "DELETE_SKIPPED", True)
    limit = enc._master_limit()
    proof = {(os.path.normcase(tif), os.path.normcase(jxl)): True,
             (os.path.normcase(tif), os.path.normcase(jxl2)): True}
    key = os.path.normcase(tif)

    def fresh(page=0, out=jxl):
        return ((tif, page), "ok", out, None)

    def skipped(page=0, out=jxl):
        return ((tif, page), "skipped", out, None)

    def reasons(results, skip_proof=proof):
        return enc._master_limit_reasons({tif: results}, skip_proof)

    try:
        # fresh page at the limit: kept by nothing; just above: kept
        monkeypatch.setattr(enc, "CJXL_DISTANCE", limit)
        enc._record_scan(tif, None, None, 16)
        assert reasons([fresh()]) == {}
        monkeypatch.setattr(enc, "CJXL_DISTANCE", limit + 0.01)
        enc._record_scan(tif, None, None, 16)
        got = reasons([fresh()])
        assert "encoded at distance" in got[key] and "MASTER_MAX_DISTANCE" in got[key]
        monkeypatch.setattr(enc, "CJXL_DISTANCE", limit)
        enc._record_scan(tif, None, None, 16)

        # skipped, proven, recorded above
        state["recorded"] = {jxl: 3.0}
        got = reasons([skipped()])
        assert "was encoded at distance 3" in got[key] and "MASTER_MAX_DISTANCE" in got[key]
        # skipped, proven, record unreadable
        state["recorded"] = {jxl: False}
        got = reasons([skipped()])
        assert "could not be read" in got[key] and "MASTER_MAX_DISTANCE" in got[key]
        # skipped, proven, no record: lossless passes, lossy / unknown keeps
        state["recorded"] = {jxl: None}
        state["lossless"] = True
        assert reasons([skipped()]) == {}
        for answer in (False, None):
            state["lossless"] = answer
            got = reasons([skipped()])
            assert "carries no encode record" in got[key] and "MASTER_MAX_DISTANCE" in got[key]
        # skipped without its provenance: not read, not judged here
        asked.clear()
        state["recorded"] = {jxl: 3.0}
        assert reasons([skipped()], skip_proof={}) == {}
        assert asked == []
        # one fresh page within the limit + one skipped page above: all or nothing
        state["recorded"] = {jxl2: 3.0}
        got = reasons([fresh(0, jxl), skipped(1, jxl2)])
        assert "was encoded at distance 3" in got[key]
        # without DELETE_SKIPPED the reader is never asked
        asked.clear()
        monkeypatch.setattr(enc, "DELETE_SKIPPED", False)
        reasons([skipped()])
        assert asked == []
        monkeypatch.setattr(enc, "DELETE_SKIPPED", True)
        # the limit off: nothing, and nothing read
        asked.clear()
        monkeypatch.setattr(enc, "MASTER_MAX_DISTANCE", None, raising=False)
        monkeypatch.setattr(enc, "CJXL_DISTANCE", 3.0)
        enc._record_scan(tif, None, None, 16)
        assert reasons([fresh(), skipped(1, jxl2)]) == {}
        assert asked == []
    finally:
        with enc._SCAN_PLAN_LOCK:
            enc._SCAN_PLAN.clear()
            enc._SCAN_REASON.clear()
            enc._EIGHT_BIT_PLAN.clear()


# --- 12 --------------------------------------------------------------------------

def test_read_encode_distance_batch_on_real_masters(tmp_path):
    made = []
    for name, flags in (("d3", ["--distance", "3"]), ("d0", ["--distance", "0"]),
                        ("off", ["--distance", "0.05", "--encode-tag", "off"])):
        src, out = _folders(tmp_path, name)
        _tiff(src / f"DSC_{name} (2).tif")
        r = _run(src, out, *COMMON, *flags)
        assert r.returncode == 0, _log(r)[-3000:]
        made.append(out / f"DSC_{name} (2).jxl")
        assert made[-1].exists()
    missing = tmp_path / HOSTILE / "nowhere [x]" / "missing (2).jxl"
    enc.setup_logger()
    got = enc._read_encode_distance_batch([str(p) for p in made] + [str(missing)])
    assert got == {str(made[0]): 3.0, str(made[1]): 0.0, str(made[2]): None,
                   str(missing): False}, got


# --- Round 2 (Fable's review) ------------------------------------------------------

# S1 (the owner's call: "the highest of the chain"): a skipped output is judged
# by the HIGHEST distance its encode record went through, never the last one —
# pixels never get better than the worst encode they came through.

@pytest.mark.parametrize("again", ["0.05", "0"])
def test_s1_an_output_recompressed_lower_is_judged_by_the_highest_distance_of_its_chain(
        tmp_path, again):
    src, out = _folders(tmp_path)
    tif = _tiff(src / "DSC_0001 (2).tif")
    r = _run(src, out, *COMMON, "--distance", "1")
    assert r.returncode == 0, _log(r)[-3000:]
    jxl = out / "DSC_0001 (2).jxl"
    before = jxl.read_bytes()
    r = _run(out, "--mode", "0", "--distance", again, "--effort", "1", "--workers", "1",
             "--on-downgrade", "convert", "--on-regeneration", "convert",
             "--no-keep-smaller", "--no-preflight", "--delete-confirm-off",
             script="jxl_recompressor.py")
    assert r.returncode == 0, _log(r)[-3000:]
    assert jxl.read_bytes() != before, "the recompressor left the JXL as it was"
    r = _run(src, out, *COMMON, "--distance", "0.05", "--sync", "--delete-skipped", *DELETE)
    log = _flat(_log(r))
    assert r.returncode == 0, log[-3000:]
    assert tif.exists(), ("the TIFF was deleted behind a JXL whose pixels went through "
                          "distance 1 | " + log[-3000:])
    assert "was encoded at distance 1" in log and "MASTER_MAX_DISTANCE" in log, log[-3000:]


@pytest.mark.parametrize("field, value, expected", [
    ("HistoryParameters", "jxl_recompressor: from image/jxl to image/jxl | gen=2 | "
                          "cjxl d=1.0 e=7 | cjxl d=0.05 e=7", 1.0),
    ("Description", "gen=3 | cjxl d=0.05 e=7 | cjxl d=3 e=7 | cjxl d=0 e=9", 3.0),
    ("Software", "gen=1 | cjxl d=0.05 e=7", 0.05),
])
def test_s1_the_reader_answers_the_highest_entry_of_the_chain(tmp_path, monkeypatch,
                                                               field, value, expected):
    """The reader's value is what the gate compares; exiftool's answer is fed to
    its callback through the batch engine."""
    enc.setup_logger()
    jxl = str(tmp_path / HOSTILE / "DSC_0001 (2).jxl")

    def fake(paths, head, label, on_data, **k):
        on_data(paths, None, [{"SourceFile": jxl, field: value}])

    monkeypatch.setattr(enc, "_exiftool_batch", fake)
    assert enc._read_encode_distance_batch([jxl]) == {jxl: expected}


# Round 3 (Fable's R2-S1): the highest distance of the WHOLE chain — every
# xmpMM:History entry, not only the newest record (a "| no encode record"
# entry adds nothing and hides nothing), plus dc:Description/Software; a
# malformed "cjxl d=" anywhere is "cannot tell" (False), never "no record".

@pytest.mark.parametrize("value, expected", [
    (["jxl_tiff_encoder: from image/tiff to image/jxl | gen=1 | cjxl d=1.0 e=1",
      "jxl_recompressor: from image/jxl to image/jxl | no encode record"], 1.0),
    (["jxl_tiff_encoder: from image/tiff to image/jxl | gen=1 | cjxl d=3 e=1",
      "jxl_recompressor: from image/jxl to image/jxl | gen=2 | cjxl d=3 e=1 | cjxl d=0 e=1",
      "jxl_recompressor: from image/jxl to image/jxl | no encode record"], 3.0),
    (["jxl_tiff_encoder: from image/tiff to image/jxl | no encode record"], None),
    (["jxl_tiff_encoder: from image/tiff to image/jxl | gen=1 | cjxl d=1.2.3 e=7"], False),
    (["jxl_tiff_encoder: from image/tiff to image/jxl | gen=1 | cjxl d=0 e=7",
      "jxl_recompressor: from image/jxl to image/jxl | gen=2 | cjxl d=0 e=7 | cjxl d=abc e=7"],
     False),
])
def test_r3_the_reader_takes_every_history_entry_and_a_malformed_one_is_cannot_tell(
        tmp_path, monkeypatch, value, expected):
    enc.setup_logger()
    jxl = str(tmp_path / HOSTILE / "DSC_0001 (2).jxl")

    def fake(paths, head, label, on_data, **k):
        on_data(paths, None, [{"SourceFile": jxl, "HistoryParameters": value}])

    monkeypatch.setattr(enc, "_exiftool_batch", fake)
    got = enc._read_encode_distance_batch([jxl])[jxl]
    assert got == expected and type(got) is type(expected), (value, got)


def test_r3_a_lossless_recompression_without_a_record_does_not_hide_the_d1_master(tmp_path):
    """Fable's reproduction: a d=1 master recompressed in place to d=0 with
    --encode-tag off — the newest History entry says "no encode record" and
    jxlinfo says lossless, but the pixels went through d=1."""
    src, out = _folders(tmp_path)
    tif = _tiff(src / "DSC_0001 (2).tif")
    r = _run(src, out, *COMMON, "--distance", "1")
    assert r.returncode == 0, _log(r)[-3000:]
    jxl = out / "DSC_0001 (2).jxl"
    before = jxl.read_bytes()
    r = _run(out, "--mode", "0", "--distance", "0", "--encode-tag", "off", "--effort", "1",
             "--workers", "1", "--on-downgrade", "convert", "--on-regeneration", "convert",
             "--no-keep-smaller", "--no-preflight", "--delete-confirm-off",
             script="jxl_recompressor.py")
    assert r.returncode == 0, _log(r)[-3000:]
    assert jxl.read_bytes() != before, "the recompressor left the JXL as it was"
    r = _run(src, out, *COMMON, "--distance", "0.05", "--sync", "--delete-skipped", *DELETE)
    log = _flat(_log(r))
    assert r.returncode == 0, log[-3000:]
    assert tif.exists(), ("the TIFF was deleted behind a lossless copy of a d=1 image | "
                          + log[-3000:])
    assert "was encoded at distance 1" in log and "MASTER_MAX_DISTANCE" in log, log[-3000:]


def test_r3_a_malformed_record_keeps_a_lossless_outputs_tiff(tmp_path):
    """A lossless master whose record was edited into something that does not
    parse ("cjxl d=1.2.3"): "cannot tell", the TIFF is kept — never read as
    "no record" (which the lossless rule would let through)."""
    src, out = _folders(tmp_path)
    tif = _tiff(src / "DSC_0001 (2).tif")
    r = _run(src, out, *COMMON, "--distance", "0", "--encode-tag", "off")
    assert r.returncode == 0, _log(r)[-3000:]
    jxl = out / "DSC_0001 (2).jxl"
    _exif(tmp_path, "-q", "-overwrite_original",
          "-XMP-dc:Description=gen=1 | cjxl d=1.2.3 e=7", jxl)
    assert b"cjxl d=1.2.3" in jxl.read_bytes()
    r = _run(src, out, *COMMON, "--distance", "0", "--encode-tag", "off",
             "--sync", "--delete-skipped", *DELETE)
    log = _flat(_log(r))
    assert r.returncode == 0, log[-3000:]
    assert tif.exists(), "a malformed record was read as no record " + log[-3000:]
    assert "MASTER_MAX_DISTANCE" in log, log[-3000:]


# R2-N1: a TIFF within the limit that the limit keeps because of a skipped page
# above it (mixed fresh + skipped) — with an explicit --verify-roundtrip its
# fresh pages are still checked (in the gate: convert_one did not know).

def test_r3_mixed_fresh_and_skipped_above_the_limit_checks_the_fresh_page(tmp_path, monkeypatch):
    src, out = _folders(tmp_path)
    with tifffile.TiffWriter(str(src / "DOC_0001 (2).tif")) as w:
        for seed in (1, 2):
            w.write(_photo(seed=seed), photometric="rgb", software=PHOTO_SW, metadata=None)
    tif = src / "DOC_0001 (2).tif"
    assert _encoder_main(monkeypatch, src, out, *COMMON, "--distance", "3") == 0
    page0 = out / "DOC_0001 (2).jxl"
    assert page0.exists() and (out / "DOC_0001 (2)_page1.jxl").exists()
    page0.unlink()
    calls = []
    real = enc._verify_roundtrip_page

    def recorder(t, page, j, d):
        calls.append((page, Path(j).name))
        return real(t, page, j, d)

    monkeypatch.setattr(enc, "_verify_roundtrip_page", recorder)
    code = _encoder_main(monkeypatch, src, out, *COMMON, "--distance", "0.05", "--sync",
                         "--verify-roundtrip", "--delete-skipped", *DELETE)
    assert code == 0
    assert tif.exists(), "kept by the skipped page recorded at distance 3"
    assert (0, "DOC_0001 (2).jxl") in calls, calls
    assert all(p == 0 for p, _n in calls), calls
    assert enc._master_limit_kept["count"] == 1


# S2 (the owner's call: "verify all the same"): an explicit --verify-roundtrip
# still checks a TIFF the limit keeps in a deleting run, as it would without
# the delete (JP-20: before the page takes its final name, with its own lines)
# — and the TIFF is still never deleted.

def test_s2_explicit_verify_still_checks_a_tiff_kept_by_the_limit(tmp_path):
    src, out = _folders(tmp_path)
    tif = _tiff(src / "DSC_0001 (2).tif")
    logs = tmp_path / "logs [s2] 100%off"
    r = _run(src, out, *COMMON, "--distance", "1", "--verify-roundtrip", *DELETE,
             env={"JXLPHOTO_LOG_DIR": str(logs)})
    console = _log(r)
    assert r.returncode == 0, console[-3000:]
    assert tif.exists(), "a TIFF above MASTER_MAX_DISTANCE was deleted"
    assert (out / "DSC_0001 (2).jxl").exists()
    text = _log_files_text(logs)
    assert any(">Round-trip verified page 0 (" in ln and tif.name in ln
               for ln in text.splitlines()), text[-3000:]
    assert _keep_lines(console, tif.name), console[-3000:]
    assert "| ERROR |" not in console


def test_s2_explicit_verify_failure_above_the_limit_is_the_pages_error(tmp_path, monkeypatch):
    """A page that fails is an error that leaves no JXL — JP-20's rule, as
    without --delete-source — never a master under its final name or an
    `_jxlphoto-error` copy, never a deleted TIFF (an impossible PSNR floor
    forces the failure)."""
    src, out = _folders(tmp_path)
    tif = _tiff(src / "DSC_0001 (2).tif")
    monkeypatch.setattr(enc, "VERIFY_LOSSY_MIN_PSNR", 500.0)
    code = _encoder_main(monkeypatch, src, out, *COMMON, "--distance", "1",
                         "--verify-roundtrip", *DELETE)
    assert tif.exists()
    jxls = sorted(p.name for p in out.rglob("*.jxl")) if out.exists() else []
    assert jxls == [], jxls
    assert code == 1


def test_s2_without_the_flag_a_tiff_kept_by_the_limit_is_not_decoded(tmp_path, monkeypatch):
    """The setting alone (VERIFY_ROUNDTRIP, on by default) gates deletions
    only: a TIFF the limit keeps is not decoded, neither in the gate (test 7)
    nor page by page."""
    src, out = _folders(tmp_path)
    tif = _tiff(src / "DSC_0001 (2).tif")
    calls = []

    def recorder(*a, **k):
        calls.append(a)
        return (True, "recorded")

    monkeypatch.setattr(enc, "_verify_roundtrip_page", recorder)
    assert _encoder_main(monkeypatch, src, out, *COMMON, "--distance", "1", *DELETE) == 0
    assert tif.exists()
    assert calls == []
