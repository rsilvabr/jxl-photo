"""Review of 2026-11-10 (#615-#617).

#615 (transcoder, encoder, recompressor) An output was judged at planning and
     swapped in later without a second look: of two simultaneous runs mapping
     different sources to one output file, the second replaced the first
     one's archive after that run had verified it and deleted its source.
#616 A lossy JXL -> JPEG/PNG decode with --sync replaced a marker-less
     JPEG/PNG of the same name at its output path (the camera's own file).
#617 Nothing forced an output to the disk before its source was deleted
     (all four scripts).
#618 The recompressor's delete gate trusted "promoted this run" + integrity:
     an output another run replaced after the promotion certified the delete.
#619 Two misleading messages: a source gone before the gate was blamed on
     the jbrd box; a damaged (truncated) output was refused as "another
     source's". Also: the wizard refuses [D] at once for a lossy JXL -> JPEG.

Real codecs; every file inside a folder named with Japanese, brackets, a
space and %.
"""
import importlib.util
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import jxl_jpeg_transcoder as tra

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "伊豆沼 [tmp] 100%"
_TOOLS = ("cjxl", "djxl", "exiftool")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")
needs_magick = pytest.mark.skipif(not shutil.which("magick"), reason="needs ImageMagick")


def _run_rc(tmp_path, *args):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"), "JXLPHOTO_JXL_THUMB": "0"}
    r = subprocess.run([sys.executable, str(REPO / "jxl_jpeg_transcoder.py"), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, env=env, stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout + r.stderr


def _jpeg(path, seed):
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    Image.fromarray((rng.random((120, 160, 3)) * 255).astype(np.uint8)).save(path, quality=90)
    return path


def _archive_of(jpg, dst):
    """The lossless archive of `jpg`, as another run would have written it."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cjxl", str(jpg), str(dst), "--lossless_jpeg=1"], check=True,
                   capture_output=True)
    return dst


def _lossy_jxl(tmp_path, dst, seed):
    """A lossy JXL in a container, no jbrd, no toolkit marker."""
    rng = np.random.default_rng(seed)
    png = tmp_path / f"plain_{seed}.png"
    Image.fromarray((rng.random((120, 160, 3)) * 255).astype(np.uint8)).save(png)
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cjxl", str(png), str(dst), "-d", "1", "--container=1"], check=True,
                   capture_output=True)
    return dst


def _arm(monkeypatch):
    monkeypatch.setattr(tra, "TEMP2_DIR", None)
    monkeypatch.setattr(tra, "STORE_MD5", True)
    monkeypatch.setattr(tra, "DELETE_SOURCE", False)
    monkeypatch.setattr(tra, "DELETE_SKIPPED", False)
    tra.setup_logger()
    tra._reset_abort()
    tra._source_identity.clear()
    _forget()


def _forget():
    planned = getattr(tra, "_planned_outputs", None)
    if planned is not None:
        planned.clear()


def _plan(pairs):
    """What cmd_* does right before judging the outputs. A no-op on the code
    before #615, so these tests fail there on what the run DOES."""
    getattr(tra, "_record_planned_outputs", lambda _pairs: None)(pairs)


def _pair(tmp_path):
    mine = _jpeg(tmp_path / HOSTILE / "sel B" / "DSC_0001.jpg", 2)
    other = _jpeg(tmp_path / HOSTILE / "sel A" / "DSC_0001.jpg", 1)
    final = tmp_path / HOSTILE / "JXL_jpeg" / "DSC_0001.jxl"
    return mine, other, final


# ------------------------------------------------------------------- #615
@needs_tools
def test_615_local_swap_refuses_an_output_that_appeared_after_planning(tmp_path, monkeypatch):
    _arm(monkeypatch)
    mine, other, final = _pair(tmp_path)
    _plan([(mine, final)])                      # planned: nothing there
    theirs = _archive_of(other, final).read_bytes()     # another run lands meanwhile
    r = tra.encode_one_transcode(mine, final, final, True, 3, False)
    assert r[1] == "error", r
    assert final.read_bytes() == theirs
    assert mine.exists()
    assert not list(final.parent.glob("*.tmp"))


@needs_tools
def test_615_local_swap_refuses_an_output_that_changed_after_it_was_judged(tmp_path, monkeypatch):
    _arm(monkeypatch)
    mine, other, final = _pair(tmp_path)
    _archive_of(mine, final)                    # this photo's own earlier archive
    _plan([(mine, final)])                      # judged: fine to rewrite
    theirs = _archive_of(other, final).read_bytes()     # another run rewrote it
    st = final.stat()
    os.utime(final, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    r = tra.encode_one_transcode(mine, final, final, True, 3, False)
    assert r[1] == "error", r
    assert final.read_bytes() == theirs
    assert not list(final.parent.glob("*.tmp"))


@needs_tools
def test_615_an_unchanged_plan_still_writes_and_rewrites(tmp_path, monkeypatch):
    """The legitimate cases: a new output, and an existing one that is still
    the file judged."""
    _arm(monkeypatch)
    mine, _other, final = _pair(tmp_path)
    _plan([(mine, final)])
    assert tra.encode_one_transcode(mine, final, final, False, 3, False)[1] == "ok"
    assert final.exists()
    _forget()
    _plan([(mine, final)])
    assert tra.encode_one_transcode(mine, final, final, True, 3, False)[1] == "reconvert"


@needs_tools
def test_615_staged_move_refuses_and_the_source_is_kept(tmp_path, monkeypatch):
    """The reproduced loss: with staging and --delete-source, the other run's
    archive lands after this run's conversion and before its move."""
    _arm(monkeypatch)
    staging = tmp_path / "staging [一時]"
    staging.mkdir()
    monkeypatch.setattr(tra, "TEMP2_DIR", str(staging))
    monkeypatch.setattr(tra, "DELETE_SOURCE", True)
    monkeypatch.setattr(tra, "DELETE_SKIPPED", True)
    mine, other, final = _pair(tmp_path)
    side = _archive_of(other, tmp_path / "side.jxl")
    _plan([(mine, final)])
    real = tra.encode_one_transcode

    def other_run_lands_first(src, *a, **k):
        res = real(src, *a, **k)
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(side, final)
        return res

    monkeypatch.setattr(tra, "encode_one_transcode", other_run_lands_first)
    results = tra.process_group_transcode([(mine, final)], 1, decode=False, verify=False,
                                          mode=6, reconvert_val=False, smart=True)
    assert final.read_bytes() == side.read_bytes()      # their archive is intact
    assert mine.exists()                                # and this JPEG was not deleted
    assert results[0][1] == "error", results
    assert any(staging.glob("*.jxl"))                   # this run's output waits in staging


def _png(path, seed):
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    Image.fromarray((rng.random((120, 160, 3)) * 255).astype(np.uint8)).save(path)
    return path


_THEIRS = b"another run's output " * 64


@needs_tools
@needs_magick
@pytest.mark.parametrize("writer", ["decode_one_transcode", "encode_to_jxl", "decode_to_image"])
def test_615_the_other_local_swaps_refuse_an_output_that_appeared(tmp_path, monkeypatch, writer):
    """The three other beside-the-final swaps of the transcoder (review S3)."""
    _arm(monkeypatch)
    d = tmp_path / HOSTILE / "shoot"
    if writer == "decode_one_transcode":            # lossless JPEG recovery
        src = _archive_of(_jpeg(tmp_path / "cam" / "DSC_0001.jpg", 7), d / "DSC_0001.jxl")
        final = d / "out" / "DSC_0001.jpg"
        call = lambda: tra.decode_one_transcode(src, final, final, False, True, False)
    elif writer == "encode_to_jxl":                 # PNG -> JXL
        src = _png(d / "DSC_0001.png", 8)
        final = d / "out" / "DSC_0001.jxl"
        call = lambda: tra.encode_to_jxl(src, final, final, 3, 1.0, True, False)
    else:                                           # lossy JXL -> JPEG
        src = _lossy_jxl(tmp_path, d / "DSC_0001.jxl", 9)
        final = d / "out" / "DSC_0001.jpg"
        call = lambda: tra.decode_to_image(src, final, final, 90, "jpeg", 8, None,
                                           False, True, False)
    _plan([(src, final)])                           # planned: nothing there
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(_THEIRS)                      # another run lands meanwhile
    r = call()
    assert r[1] == "error", r
    assert final.read_bytes() == _THEIRS
    assert src.exists()
    assert not list(final.parent.glob("*.tmp"))


@needs_tools
def test_615_convert_staged_move_refuses_and_reports_an_error(tmp_path, monkeypatch):
    """process_group_convert's mover (review S3): the output stays in staging,
    the other run's file is intact, the result is an error."""
    _arm(monkeypatch)
    staging = tmp_path / "staging [一時]"
    staging.mkdir()
    monkeypatch.setattr(tra, "TEMP2_DIR", str(staging))
    src = _png(tmp_path / HOSTILE / "DSC_0001.png", 10)
    final = tmp_path / HOSTILE / "JXL" / "DSC_0001.jxl"
    _plan([(src, final)])
    real = tra.encode_to_jxl

    def other_run_lands_first(s, *a, **k):
        res = real(s, *a, **k)
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(_THEIRS)
        return res

    monkeypatch.setattr(tra, "encode_to_jxl", other_run_lands_first)
    results, moved = tra.process_group_convert(
        [(src, final)], 1, "to_jxl", 90, 1.0, None, 8, None, False, 3, False, False, True)
    assert final.read_bytes() == _THEIRS
    assert src.exists()
    assert results[0][1] == "error", results
    assert os.path.normcase(str(final)) not in moved
    assert any(staging.glob("*.jxl"))


@needs_tools
def test_615_encoder_refused_move_is_an_error_in_the_summary(tmp_path, monkeypatch, capsys):
    """Review S1: the encoder's refused staged move was counted as OK (the
    run exited 1 with 0 errors in its summary)."""
    import json
    enc = _load("jxl_tiff_encoder.py", "enc_summary")
    src = _tiff(tmp_path / HOSTILE / "sel B" / "DSC_0001.tif", 4)
    final = src.with_suffix(".jxl")
    orig = enc.convert_one

    def wrapped(tiff_path, write_path, final_path, *a, **k):
        res = orig(tiff_path, write_path, final_path, *a, **k)
        final.write_bytes(_FOREIGN)
        return res

    monkeypatch.setattr(enc, "convert_one", wrapped)
    argv = ["jxl_tiff_encoder.py", src.parent, "--mode", "8", "--distance", "0",
            "--workers", "1", "--no-preflight", "--staging", tmp_path / "staging [一時]",
            "--summary-json"]
    capsys.readouterr()
    rc = _main(enc, monkeypatch, argv)
    out = capsys.readouterr().out
    assert rc == 1
    assert final.read_bytes() == _FOREIGN
    line = [ln for ln in out.splitlines() if ln.startswith(enc.SUMMARY_PREFIX)]
    assert line, out[-2000:]
    data = json.loads(line[-1][len(enc.SUMMARY_PREFIX):])
    assert data["errors"] == 1, data
    assert data["ok"] == 0, data
    assert any("REFUSED" in f["reason"] for f in data["failures"]), data


# ------------------------------------------------------------------- #616
@needs_tools
@needs_magick
@pytest.mark.parametrize("fmt, name", [("jpeg", "DSC_0001.jpg"), ("png", "DSC_0001.png")])
def test_616_a_lossy_decode_does_not_replace_a_markerless_original(tmp_path, fmt, name):
    d = tmp_path / HOSTILE / "shoot"
    _lossy_jxl(tmp_path, d / "DSC_0001.jxl", 5)
    cam = d / name
    if fmt == "jpeg":
        _jpeg(cam, 6)
    else:
        Image.new("RGB", (64, 48), (200, 10, 10)).save(cam)
    old = time.time() - 86400
    os.utime(cam, (old, old))                   # the camera file is older than the JXL
    before = cam.read_bytes()
    args = (d, "--force-convert", "--decode", "--format", fmt, "--mode", "0", "--workers", "1")

    rc, log = _run_rc(tmp_path, *args, "--sync")
    assert rc == 1, log[-3000:]
    assert cam.read_bytes() == before
    assert "no jxlphoto-src marker" in " ".join(log.split())
    # Not "another source's output": the --provenance content hint cannot help.
    assert "this tool did not write" in " ".join(log.split())
    assert "--provenance content" not in log

    rc, log = _run_rc(tmp_path, *args, "--overwrite")   # explicit: replaced
    assert rc == 0, log[-3000:]
    assert cam.read_bytes() != before

    os.utime(cam, (old, old))                   # now this tool's own decode, older again
    rc, log = _run_rc(tmp_path, *args, "--sync")
    assert rc == 0, log[-3000:]
    assert "RECONVERT" in log


# ------------------------------------------------------------------- #617
def test_617_flush_to_disk(tmp_path):
    f = tmp_path / HOSTILE / "x.bin"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"data")
    assert tra._flush_to_disk(f) is True
    assert tra._flush_to_disk(tmp_path / HOSTILE / "missing.bin") is False
    # A read-only output cannot be flushed (needs write access): the gate
    # keeps the source rather than trusting the OS cache.
    import stat
    os.chmod(f, stat.S_IREAD)
    try:
        assert tra._flush_to_disk(f) is False
    finally:
        os.chmod(f, stat.S_IREAD | stat.S_IWRITE)


@needs_tools
def test_617_an_output_that_cannot_be_flushed_keeps_its_source(tmp_path, monkeypatch):
    _arm(monkeypatch)
    monkeypatch.setattr(tra, "DELETE_SOURCE", True)
    mine = _jpeg(tmp_path / HOSTILE / "DSC_0002.jpg", 3)
    final = mine.with_suffix(".jxl")
    # raising=False: on the pre-fix code there is no such name, and the test
    # must fail there on what the run DOES (the JPEG deleted), not on a typo.
    monkeypatch.setattr(tra, "_flush_to_disk", lambda _p: False, raising=False)
    tra.process_group_transcode([(mine, final)], 1, decode=False, verify=False,
                                mode=8, reconvert_val=False, smart=True)
    assert final.exists()
    assert mine.exists()


@needs_tools
def test_617_a_flushed_output_still_deletes_its_source(tmp_path, monkeypatch):
    _arm(monkeypatch)
    monkeypatch.setattr(tra, "DELETE_SOURCE", True)
    mine = _jpeg(tmp_path / HOSTILE / "DSC_0003.jpg", 4)
    original = mine.read_bytes()
    final = mine.with_suffix(".jxl")
    tra.process_group_transcode([(mine, final)], 1, decode=False, verify=False,
                                mode=8, reconvert_val=False, smart=True)
    assert not mine.exists()
    out = tmp_path / "recon.jpg"
    subprocess.run(["djxl", "--reconstruct_jpeg", str(final), str(out)], check=True,
                   capture_output=True)
    assert out.read_bytes() == original


# ------------------------------------------- #615, encoder and recompressor
def _load(script, tag):
    """A private copy of the script under test (its globals cannot leak)."""
    spec = importlib.util.spec_from_file_location(f"r261110_{tag}", str(REPO / script))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _main(mod, monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", [str(a) for a in argv])
    try:
        mod.main()
    except SystemExit as e:
        return e.code or 0
    return 0


def _tiff(path, seed):
    import tifffile
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    tifffile.imwrite(str(path), rng.integers(0, 65535, (48, 64, 3), dtype=np.uint16),
                     photometric="rgb")
    return path


_FOREIGN = b"another run's archive " * 64


@needs_tools
@pytest.mark.parametrize("staged", [True, False])
def test_615_encoder_never_replaces_a_file_that_appeared_after_planning(tmp_path, monkeypatch,
                                                                       staged):
    enc = _load("jxl_tiff_encoder.py", f"enc{int(staged)}")
    src = _tiff(tmp_path / HOSTILE / "sel B" / "DSC_0001.tif", 2)
    final = src.with_suffix(".jxl")             # mode 8: beside the TIFF
    orig = enc.convert_one

    def wrapped(tiff_path, write_path, final_path, *a, **k):
        if staged:                              # lands after the conversion, before the move
            res = orig(tiff_path, write_path, final_path, *a, **k)
            final.write_bytes(_FOREIGN)
            return res
        final.write_bytes(_FOREIGN)             # lands before this file's own swap
        return orig(tiff_path, write_path, final_path, *a, **k)

    monkeypatch.setattr(enc, "convert_one", wrapped)
    argv = ["jxl_tiff_encoder.py", src.parent, "--mode", "8", "--distance", "0",
            "--workers", "1", "--delete-source", "--delete-confirm-off", "--no-preflight"]
    # Without staging the file is already there when the worker starts: only
    # --overwrite makes the worker write at all (otherwise it is a SKIP).
    argv += ["--staging", tmp_path / "staging [一時]"] if staged else ["--overwrite"]
    rc = _main(enc, monkeypatch, argv)
    assert final.read_bytes() == _FOREIGN       # the other run's file is intact
    assert src.exists()                         # and this TIFF was not deleted
    assert rc == 1


@needs_tools
@pytest.mark.parametrize("staged", [True, False])
def test_615_recompressor_never_replaces_a_file_that_appeared_after_planning(tmp_path,
                                                                            monkeypatch, staged):
    rec = _load("jxl_recompressor.py", f"rec{int(staged)}")
    t = _tiff(tmp_path / HOSTILE / "t" / "DSC_0001.tif", 3)
    shoot = tmp_path / HOSTILE / "shoot 2"
    r = subprocess.run([sys.executable, str(REPO / "jxl_tiff_encoder.py"), str(t.parent),
                        str(shoot), "--mode", "2", "--distance", "0", "--no-preflight"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL,
                       env={**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"),
                            "JXLPHOTO_JXL_THUMB": "0"})
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    src = shoot / "DSC_0001.jxl"
    final = tmp_path / HOSTILE / "flat [o]" / "DSC_0001.jxl"
    orig = rec.convert_one

    def wrapped(jxl_path, write_path, final_path, *a, **k):
        res = orig(jxl_path, write_path, final_path, *a, **k)
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(_FOREIGN)             # lands before this run's promotion
        return res

    monkeypatch.setattr(rec, "convert_one", wrapped)
    argv = ["jxl_recompressor.py", shoot, final.parent, "--mode", "2", "--distance", "1",
            "--workers", "1", "--on-downgrade", "convert", "--on-regeneration", "convert",
            "--on-unknown", "convert", "--on-scan", "convert", "--delete-source",
            "--delete-confirm-off", "--no-preflight"]
    if staged:
        argv += ["--staging", tmp_path / "staging [一時]"]
    rc = _main(rec, monkeypatch, argv)
    assert final.read_bytes() == _FOREIGN
    assert src.exists()
    assert rc == 1


# ------------------------------------------------ follow-ups of the review
# #617 in the other three scripts; #618 the recompressor's gate after a
# replaced promotion (review 4.2); #619 two misleading messages (review 1.3);
# the wizard's [D] on a lossy JXL -> JPEG.

def _encode_masters(tmp_path, tiffs_dir, out_dir):
    r = subprocess.run([sys.executable, str(REPO / "jxl_tiff_encoder.py"), str(tiffs_dir),
                        str(out_dir), "--mode", "2", "--distance", "0", "--no-preflight"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL,
                       env={**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"),
                            "JXLPHOTO_JXL_THUMB": "0"})
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]


@needs_tools
@pytest.mark.parametrize("script", ["encoder", "decoder", "recompressor"])
def test_617_the_other_scripts_keep_a_source_whose_output_cannot_be_flushed(
        tmp_path, monkeypatch, script):
    base = tmp_path / HOSTILE
    if script == "encoder":
        mod = _load("jxl_tiff_encoder.py", "flush_enc")
        src = _tiff(base / "t" / "DSC_0001.tif", 11)
        final = src.with_suffix(".jxl")
        argv = ["jxl_tiff_encoder.py", src.parent, "--mode", "8", "--distance", "0",
                "--workers", "1", "--no-preflight"]
    else:
        _tiff(base / "t" / "DSC_0001.tif", 12)
        _encode_masters(tmp_path, base / "t", base / "shoot")
        src = base / "shoot" / "DSC_0001.jxl"
        if script == "decoder":
            mod = _load("jxl_tiff_decoder.py", "flush_dec")
            final = base / "out" / "DSC_0001.tif"
            argv = ["jxl_tiff_decoder.py", src.parent, final.parent, "--mode", "2",
                    "--workers", "1"]
        else:
            mod = _load("jxl_recompressor.py", "flush_rec")
            final = base / "out" / "DSC_0001.jxl"
            argv = ["jxl_recompressor.py", src.parent, final.parent, "--mode", "2",
                    "--distance", "1", "--workers", "1", "--on-downgrade", "convert",
                    "--on-regeneration", "convert", "--on-unknown", "convert",
                    "--on-scan", "convert", "--no-preflight"]
    # raising=False: before the fix the script has no such name, and the test
    # must fail there on what the run DOES (the source deleted).
    monkeypatch.setattr(mod, "_flush_to_disk", lambda _p: False, raising=False)
    _main(mod, monkeypatch, argv + ["--delete-source", "--delete-confirm-off"])
    assert final.exists()
    assert src.exists(), "deleted although its output could not be flushed to disk"


@needs_tools
def test_618_recompressor_keeps_a_source_whose_output_was_replaced_after_promotion(
        tmp_path, monkeypatch):
    """Review 4.2: another run replaced the output after this run promoted it;
    the gate trusted "promoted this run" + integrity and deleted the source."""
    rec = _load("jxl_recompressor.py", "gate618")
    base = tmp_path / HOSTILE
    _tiff(base / "t" / "DSC_0001.tif", 13)
    _encode_masters(tmp_path, base / "t", base / "shoot")
    _tiff(base / "t2" / "DSC_0001.tif", 14)                 # another photo, same name
    _encode_masters(tmp_path, base / "t2", base / "other")
    src = base / "shoot" / "DSC_0001.jxl"
    final = base / "flat [o]" / "DSC_0001.jxl"
    theirs = (base / "other" / "DSC_0001.jxl").read_bytes()
    real_gate = rec._delete_gate

    def other_run_replaces_it(items, results, promoted):
        tmp = final.with_name("their.tmp")
        tmp.write_bytes(theirs)
        os.replace(tmp, final)                              # a valid JXL of another photo
        return real_gate(items, results, promoted)

    monkeypatch.setattr(rec, "_delete_gate", other_run_replaces_it)
    _main(rec, monkeypatch, ["jxl_recompressor.py", src.parent, final.parent, "--mode", "2",
                             "--distance", "1", "--workers", "1", "--on-downgrade", "convert",
                             "--on-regeneration", "convert", "--on-unknown", "convert",
                             "--on-scan", "convert", "--no-preflight", "--delete-source",
                             "--delete-confirm-off"])
    assert final.read_bytes() == theirs
    assert src.exists(), "deleted on the strength of another run's file"


class _Records(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@needs_tools
def test_619_a_source_gone_before_the_gate_is_not_blamed_on_jbrd(tmp_path, monkeypatch):
    """Review 1.3: a JPEG another run deleted while this one converted it was
    reported as "djxl --reconstruct_jpeg does not reproduce this JPEG; run
    --repair-jbrd" — the wrong problem."""
    _arm(monkeypatch)
    monkeypatch.setattr(tra, "DELETE_SOURCE", True)
    mine = _jpeg(tmp_path / HOSTILE / "DSC_0004.jpg", 15)
    final = mine.with_suffix(".jxl")
    real = tra.encode_one_transcode

    def another_run_deletes_it(src, *a, **k):
        res = real(src, *a, **k)
        os.remove(src)
        return res

    monkeypatch.setattr(tra, "encode_one_transcode", another_run_deletes_it)
    h = _Records()
    tra.logger.addHandler(h)
    try:
        tra.process_group_transcode([(mine, final)], 1, decode=False, verify=False,
                                    mode=8, reconvert_val=False, smart=True)
    finally:
        tra.logger.removeHandler(h)
    text = "\n".join(h.lines)
    assert "repair-jbrd" not in text, text
    assert "no longer exists" in text, text


@needs_tools
def test_619_a_damaged_output_is_named_as_damaged(tmp_path, monkeypatch):
    """Review 1.3: a truncated archive (what a power cut leaves) was refused as
    "not the file the checksum was recorded for / another source" — the user
    had to guess that the .jxl itself is broken."""
    _arm(monkeypatch)
    monkeypatch.setattr(tra, "DELETE_SOURCE", True)
    monkeypatch.setattr(tra, "_run_collapses_structure", lambda *a, **k: True)
    mine = _jpeg(tmp_path / HOSTILE / "sel" / "DSC_0005.jpg", 16)
    final = tmp_path / HOSTILE / "JXL_jpeg" / "DSC_0005.jxl"
    data = _archive_of(mine, final).read_bytes()
    final.write_bytes(data[: len(data) // 2])                # truncated
    kept, refused = tra._provenance_filter([(mine, final)], 6, would_write=lambda s, o: True)
    assert not kept
    assert "damaged" in refused[0][2].lower(), refused


def test_D_is_refused_at_once_for_a_lossy_jxl_to_jpeg(tmp_path, monkeypatch):
    """The wizard offered [D] for the lossy JXL -> JPEG and refused it only at
    the review, after both delete confirmations (review of delivery C)."""
    import jxl_photo as wp
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    asked = []
    answers = iter(["3"])                    # back at the mode list: plain layout 3

    def _input(prompt=""):
        asked.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", _input)
    wf = {"origin_format": "jxl", "dest_format": "jpeg", "input_dir": str(tmp_path),
          "mode": None, "conversion_type": "jxl_to_jpeg_force"}
    modes = [(str(i), f"mode {i}", "") for i in range(9)]
    assert menu._wizard_delete_gateway(wf, modes) is True
    assert wf["mode"] == 3
    assert not wf.get("delete_source")
    assert not any("Continue?" in p for p in asked), asked
