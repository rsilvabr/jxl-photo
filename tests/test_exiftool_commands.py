"""Several exiftool commands in ONE process (refactor F6, B1).

Starting exiftool costs ~0.15 s, and the encoder started it up to ~11 times
per file. _exiftool_commands runs a file's commands with `-execute` between
them in one process; each keeps its own exit status (an `-echo3`/`-echo4`
marker carrying `${status}`, exiftool 12.15 or later) and its own output, so
an error lands on the command that made it and never on the next one.
Without that support, or when a test has replaced _run_exiftool_argfile or
_run_captured, every command runs in its own process as before.

Real exiftool, in a folder with Japanese, brackets, a space and %.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import jxl_photo_common as common  # noqa: E402
import jxl_tiff_decoder as dec  # noqa: E402
import jxl_tiff_encoder as enc  # noqa: E402

HOSTILE = "250118_吾妻山公園 [FINAL] 100%off %d"
needs_exiftool = pytest.mark.skipif(not shutil.which("exiftool"), reason="needs exiftool")
needs_tools = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool")),
    reason="needs cjxl, djxl and exiftool on PATH")


def _tif(path: Path, value=1000):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.full((16, 16, 3), value, np.uint16), photometric="rgb")
    return path


def _tag(path, tag):
    r = enc._run_exiftool_argfile(["-s3", f"-{tag}", str(path)], timeout=60)
    return r.stdout.strip()


# --- the marker split, without exiftool --------------------------------------

def test_the_split_gives_each_command_its_own_text():
    tag = "{jxlphotoabc"
    text = f"one\n{tag} 0 0}}\ntwo\nthree\n{tag} 1 1}}\n{tag} 2 0}}\n"
    assert common._split_by_marker(text, tag, 3) == [(0, "one\n"), (1, "two\nthree\n"), (0, "")]


def test_a_missing_marker_or_one_without_a_number_fails_closed():
    tag = "{jxlphotoabc"
    old = f"x\n{tag} 0 ${{status}}}}\n"       # an exiftool older than 12.15
    assert common._split_by_marker(old, tag, 2) == [(-1, ""), (-1, "")]


# --- real exiftool ------------------------------------------------------------

@needs_exiftool
def test_this_exiftool_reports_each_command(monkeypatch):
    monkeypatch.setattr(enc, "_EXIFTOOL_BATCH", {})
    assert common._exiftool_batch_ok(enc._G) is True


@needs_exiftool
def test_a_failed_command_in_the_middle_keeps_its_error_to_itself(tmp_path):
    folder = tmp_path / HOSTILE
    a = _tif(folder / "DSC_0001 (2).tif")
    missing = folder / "no such [file].tif"
    results = common._exiftool_one_process(enc._G, [
        ["-overwrite_original", "-XMP-dc:Title=first", str(a)],
        ["-overwrite_original", "-XMP-dc:Title=never", str(missing)],
        ["-overwrite_original", "-XMP-dc:Description=third", str(a)],
    ], 120)
    assert [r.returncode for r in results] == [0, 1, 0]
    assert "no such [file].tif" in results[1].stderr
    assert "no such" not in results[0].stderr + results[2].stderr
    assert "1 image files updated" in results[2].stdout
    assert _tag(a, "XMP-dc:Title") == "first"
    assert _tag(a, "XMP-dc:Description") == "third"


@needs_exiftool
def test_one_process_and_one_per_command_answer_alike(tmp_path, monkeypatch):
    folder = tmp_path / HOSTILE
    a = _tif(folder / "a (1).tif")
    cmds = [["-s3", "-ImageWidth", str(a)], ["-s3", "-ImageHeight", str(a)]]
    monkeypatch.setattr(enc, "_EXIFTOOL_BATCH", {})
    together = common._exiftool_commands(enc._G, cmds, 120)
    monkeypatch.setattr(common, "_exiftool_batch_ok", lambda g: False)
    apart = common._exiftool_commands(enc._G, cmds, 120)
    assert [(r.returncode, r.stdout.strip()) for r in together] == \
           [(r.returncode, r.stdout.strip()) for r in apart] == [(0, "16"), (0, "16")]


@needs_exiftool
def test_a_prefetched_read_answers_exactly_like_its_own_run(tmp_path, monkeypatch):
    """The kept result is what the reader's own call returns, used once; a
    different command is not answered from it."""
    a = _tif(tmp_path / HOSTILE / "c (3).tif")
    cmd = ["-j", "-ImageWidth", str(a)]
    own = enc._run_exiftool_argfile(cmd, timeout=60)
    monkeypatch.setattr(enc, "_EXIFTOOL_BATCH", {})
    enc._prefetch_exiftool_reads([cmd, ["-s3", "-ImageHeight", str(a)]], 60)
    kept = enc._EXIFTOOL_PREFETCH.results[tuple(cmd)]
    assert enc._run_exiftool_argfile(cmd, timeout=60) is kept
    assert (kept.returncode, kept.stdout) == (own.returncode, own.stdout)
    assert tuple(cmd) not in enc._EXIFTOOL_PREFETCH.results          # used once
    enc._clear_exiftool_prefetch()
    assert enc._EXIFTOOL_PREFETCH.results == {}


def test_a_test_double_sees_every_command_on_its_own(monkeypatch):
    """A replaced _run_exiftool_argfile keeps its old view: one call per
    command, stopping at the first that fails (as the old code did)."""
    seen = []

    def fake(args, timeout=60):
        seen.append(list(args))
        return subprocess.CompletedProcess(args, 1 if "bad" in args else 0, "", "")
    monkeypatch.setattr(enc, "_run_exiftool_argfile", fake)
    results = common._exiftool_commands(enc._G, [["ok"], ["bad"], ["never"]], 60)
    assert seen == [["ok"], ["bad"]]
    assert [r.returncode for r in results] == [0, 1]


@needs_tools
def test_the_encoder_writes_a_file_s_metadata_in_one_process(tmp_path, monkeypatch):
    """A two-page TIFF with an XMP block: per output, the XMP block, the
    metadata command and the page markers share ONE exiftool process — and
    every marker is in the master."""
    folder = tmp_path / HOSTILE
    src = folder / "scan (3).tif"
    src.parent.mkdir(parents=True)
    xmp = ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
           'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<dc:title><rdf:Alt><rdf:li xml:lang="x-default">写真 [1]</rdf:li></rdf:Alt></dc:title>'
           '</rdf:Description></rdf:RDF></x:xmpmeta>').encode("utf-8")
    with tifffile.TiffWriter(str(src)) as w:
        w.write(np.full((32, 32, 3), 900, np.uint16), photometric="rgb",
                extratags=[(700, 1, len(xmp), xmp, True)])
        w.write(np.full((32, 32, 3), 1900, np.uint16), photometric="rgb")
    batches = []
    real = common._exiftool_one_process

    def counting(g, commands, timeout):
        batches.append(len(commands))
        return real(g, commands, timeout)
    monkeypatch.setattr(common, "_exiftool_one_process", counting)
    monkeypatch.setattr(enc, "_EXIFTOOL_BATCH", {})
    monkeypatch.setattr(sys, "argv", [
        "jxl_tiff_encoder.py", str(folder), str(folder / "jxl"), "--mode", "0",
        "--distance", "0", "--effort", "3", "--no-preflight", "--workers", "1"])
    monkeypatch.setenv("JXLPHOTO_LOG_DIR", str(tmp_path / "logs"))
    try:
        enc.main()
    except SystemExit as e:
        assert e.code in (0, None)
    outs = sorted((folder / "jxl").glob("*.jxl"))
    assert len(outs) == 2, outs
    # the probe (one command), then per page ONE process for the reads (record
    # fields, origin, the XMP's Relation/Description/CreatorTool) and ONE for
    # the writes (XMP block, metadata, markers)
    assert batches == [1, 5, 3, 5, 3], batches
    for out in outs:
        rel = _tag(out, "XMP-dc:Relation")
        assert "jxlphoto-page:" in rel and "jxlphoto-src:" in rel, rel
    assert _tag(outs[0], "XMP-dc:Title") == "写真 [1]"


@needs_tools
def test_the_decoder_copies_a_tiff_s_metadata_in_two_processes(tmp_path, monkeypatch):
    """copy_metadata: the EXIF/XMP copies, the IFD1 clean-ups and three reads
    in one process, the clean-ups they decide in a second. The TIFF comes
    back with the XMP and without the master's internal markers."""
    folder = tmp_path / HOSTILE
    src = _tif(folder / "DSC_0001 (2).tif")
    r = subprocess.run([sys.executable, str(REPO / "jxl_tiff_encoder.py"), str(folder),
                        str(folder / "jxl"), "--mode", "2", "--distance", "0",
                        "--workers", "1"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    jxl = folder / "jxl" / "DSC_0001 (2).jxl"
    enc._run_exiftool_argfile(["-overwrite_original", "-XMP-dc:Title=写真 [2]", str(jxl)],
                              timeout=60)
    src.unlink()
    batches = []
    real = common._exiftool_one_process

    def counting(g, commands, timeout):
        batches.append(len(commands))
        return real(g, commands, timeout)
    monkeypatch.setattr(common, "_exiftool_one_process", counting)
    monkeypatch.setattr(dec, "_EXIFTOOL_BATCH", {})
    monkeypatch.setattr(sys, "argv", ["jxl_tiff_decoder.py", str(jxl), str(folder / "tif"),
                                      "--workers", "1"])
    monkeypatch.setenv("JXLPHOTO_LOG_DIR", str(tmp_path / "logs"))
    try:
        dec.main()
    except SystemExit as e:
        assert e.code in (0, None)
    tif = folder / "tif" / "DSC_0001 (2).tif"
    assert tif.is_file()
    # the probe, then copy_metadata's first process (copies, clean-ups, reads)
    assert batches[0] == 1 and max(batches[1:]) >= 5, batches
    assert _tag(tif, "XMP-dc:Title") == "写真 [2]"
    rel = _tag(tif, "XMP-dc:Relation")
    assert "jxlphoto-page:" not in rel and "jxlphoto-depth:" not in rel, rel
