"""#639 (S5): a lossy JPEG -> JXL (--force-convert --from-jpeg, d > 0) of a JPEG
whose EXIF is malformed.

cjxl copies the JPEG's EXIF into the container as a block; when that block is
malformed (a broken IFD1, maker notes exiftool cannot parse) exiftool refuses
every LATER write to the JXL, so the metadata copy or the provenance write of
the toolkit failed and the file was an error (nothing deleted — #571). Measured
on the owner's `bad_ifd1__0005 (2).jpg` and `unrecognized_makernotes__*.jpg`:

* broken IFD1: the retry (cjxl again with `-x strip=exif`, then the copy from
  the JPEG through exiftool) works — the EXIF arrives, the markers too.
* unparseable maker notes: the copy recreates the block, and the next write
  fails again. Still an error, but with its cause and the way out
  (`--force-transcode`, which writes no metadata and works).

Fixtures are synthetic (no dependence on E:\\TESTE) in a hostile folder:
Japanese, `[ ]`, a space, `%` and parentheses. REAL cjxl and exiftool.
"""
import hashlib
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)

HOSTILE = "写真 [S5] 100%off (2)"
_TOOLS = ("cjxl", "djxl", "exiftool")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")

OWNER_FILES = Path(r"E:\TESTE\JPEG_varied")
OWNER_BAD_IFD1 = OWNER_FILES / "bad_ifd1__0005 (2).jpg"
OWNER_NOKIA = OWNER_FILES / "unrecognized_makernotes__06012006006.jpg"


# ----------------------------------------------------------------- helpers
def _run(tmp_path, *args, timeout=600):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"),
           "JXLPHOTO_SETTINGS": "", "JXLPHOTO_JXL_THUMB": "0",
           "JXLPHOTO_NO_UPDATE_CHECK": "1"}
    r = subprocess.run([sys.executable, str(SCRIPTS / "jxl_jpeg_transcoder.py"),
                        *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, env=env,
                       stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout + r.stderr


def _lossy(tmp_path, folder, *extra):
    return _run(tmp_path, folder, "--mode", "1", "--force-convert", "--from-jpeg",
                "--distance", "1", *extra)


def _folder(tmp_path, name="shoot"):
    d = tmp_path / HOSTILE / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _plain_jpeg(path, seed=1, w=160, h=120):
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    Image.fromarray((rng.random((h, w, 3)) * 255).astype(np.uint8)).save(path, quality=90)
    return path


def _jpeg_with_exif(path, exif):
    _plain_jpeg(path)
    data = path.read_bytes()
    seg = b"Exif\x00\x00" + exif
    path.write_bytes(data[:2] + b"\xff\xe1" + struct.pack(">H", len(seg) + 2) + seg + data[2:])
    return path


def _malformed_exif():
    """IFD1 with a BitsPerSample pointing past the end of the block (what an
    HDR program wrote into one of the owner's JPEGs)."""
    make = b"TESTCAM\x00"
    ifd0_off = 8
    ifd1_off = ifd0_off + 2 + 12 + 4
    make_off = ifd1_off + 2 + 12 + 4
    ifd0 = (struct.pack("<H", 1) + struct.pack("<HHII", 0x010F, 2, len(make), make_off)
            + struct.pack("<I", ifd1_off))
    ifd1 = (struct.pack("<H", 1) + struct.pack("<HHII", 0x0102, 3, 3, 0x00FFFF00)
            + struct.pack("<I", 0))
    return b"II*\x00" + struct.pack("<I", ifd0_off) + ifd0 + ifd1 + make


def _malformed_exif_rotated(orientation=6):
    """The same broken IFD1, in a JPEG that also says Orientation = 6."""
    make = b"TESTCAM\x00"
    ifd0_off = 8
    ifd1_off = ifd0_off + 2 + 2 * 12 + 4
    make_off = ifd1_off + 2 + 12 + 4
    ifd0 = (struct.pack("<H", 2)
            + struct.pack("<HHII", 0x010F, 2, len(make), make_off)
            + struct.pack("<HHII", 0x0112, 3, 1, orientation)
            + struct.pack("<I", ifd1_off))
    ifd1 = (struct.pack("<H", 1) + struct.pack("<HHII", 0x0102, 3, 3, 0x00FFFF00)
            + struct.pack("<I", 0))
    return b"II*\x00" + struct.pack("<I", ifd0_off) + ifd0 + ifd1 + make


def _nokia_exif():
    """Make = Nokia and 64 bytes exiftool does not recognise as maker notes."""
    make = b"Nokia\x00"
    note = bytes(range(1, 65))
    ifd0_off = 8
    exif_off = ifd0_off + 2 + 2 * 12 + 4
    make_off = exif_off + 2 + 12 + 4
    note_off = make_off + len(make)
    ifd0 = (struct.pack("<H", 2)
            + struct.pack("<HHII", 0x010F, 2, len(make), make_off)
            + struct.pack("<HHII", 0x8769, 4, 1, exif_off)
            + struct.pack("<I", 0))
    exif = (struct.pack("<H", 1) + struct.pack("<HHII", 0x927C, 7, len(note), note_off)
            + struct.pack("<I", 0))
    return b"II*\x00" + struct.pack("<I", ifd0_off) + ifd0 + exif + make + note


def _htc_exif():
    """Make = HTC and the start of an HTC Desire 626's maker notes (a JPEG that
    went through WhatsApp, found on the owner's disk on 2026-11-11): exiftool
    finds an IFD in them whose entries point far outside the block and says
    `[minor] Bad MakerNotes offset for Unknown_0x0000` on every write."""
    make = b"HTC\x00"
    note = (b"cameraid_I\x00\x00\x00\x00"
            + bytes.fromhex("0400" "0000" "0100" "00000100" "00001000"
                            "0000" "0400" "00000100" "00000d00")
            + bytes(64))
    ifd0_off = 8
    exif_off = ifd0_off + 2 + 2 * 12 + 4
    make_off = exif_off + 2 + 12 + 4
    note_off = make_off + len(make)
    ifd0 = (struct.pack("<H", 2)
            + struct.pack("<HHII", 0x010F, 2, len(make), make_off)
            + struct.pack("<HHII", 0x8769, 4, 1, exif_off)
            + struct.pack("<I", 0))
    exif = (struct.pack("<H", 1) + struct.pack("<HHII", 0x927C, 7, len(note), note_off)
            + struct.pack("<I", 0))
    return b"II*\x00" + struct.pack("<I", ifd0_off) + ifd0 + exif + make + note


def _healthy_jpeg(path):
    _plain_jpeg(path)
    r = subprocess.run(["exiftool", "-overwrite_original", "-Make=Nikon", "-Model=Z 7",
                        "-ISO=100", path.name], cwd=str(path.parent),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stdout + r.stderr
    return path


def _tags(path, *names):
    path = Path(path)
    r = subprocess.run(["exiftool", "-j", "-n", "-G1", *[f"-{n}" for n in names], path.name],
                       cwd=str(path.parent), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return json.loads(r.stdout)[0] if r.stdout.strip() else {}


def _relation(path):
    rel = _tags(path, "XMP-dc:Relation").get("XMP-dc:Relation", [])
    return [rel] if isinstance(rel, str) else list(rel)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _jxls(folder):
    return sorted(p for p in Path(folder).rglob("*.jxl"))


def _leftovers(folder):
    return [p for p in Path(folder).rglob("*.tmp")]


_MODS = {}


def _tr():
    if "tr" not in _MODS:
        sys.path.insert(0, str(SCRIPTS))
        try:
            spec = importlib.util.spec_from_file_location(
                "s5_lossy_transcoder", str(SCRIPTS / "jxl_jpeg_transcoder.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        finally:
            sys.path.remove(str(SCRIPTS))
        _MODS["tr"] = mod
    return _MODS["tr"]


def _encode(m, src, distance=1.0):
    out = src.parent / "converted_jxl"
    out.mkdir(exist_ok=True)
    final = out / (src.stem + ".jxl")
    return m.encode_to_jxl(src, final, final, 7, distance, False, False), final


# ------------------------------------------------------------------- tests
@needs_tools
def test_639_a_broken_ifd1_converts_with_its_exif_and_markers(tmp_path):
    jpg = _jpeg_with_exif(_folder(tmp_path, "ifd1") / "DSC_0001 (2).jpg", _malformed_exif())
    before = _sha(jpg)
    rc, out = _lossy(tmp_path, jpg.parent)
    flat = " ".join(out.split())
    assert rc == 0, out
    assert "ERROR" not in out, out
    assert "metadata copied again without the EXIF the codec wrote | DSC_0001 (2).jpg" in flat, out
    jxl = _jxls(jpg.parent)
    assert len(jxl) == 1, jxl
    assert _tags(jxl[0], "IFD0:Make").get("IFD0:Make") == "TESTCAM"
    rel = " ".join(_relation(jxl[0]))
    assert "jxlphoto-src:" in rel and "jxlphoto-srcsum:" in rel, rel
    assert "jxlphoto-origin:jpeg" in rel, rel
    assert _sha(jpg) == before and jpg.exists()
    assert not _leftovers(jpg.parent)


@needs_tools
def test_639_a_rotated_jpeg_with_a_broken_ifd1_is_not_retried(tmp_path):
    """Review fix: cjxl keeps a JPEG's EXIF Orientation in the CODESTREAM, and
    `-x strip=exif` drops it with the EXIF — the retried JXL showed the picture
    sideways in any JXL viewer while its Exif box said 'Rotate 90 CW'. A rotated
    JPEG is refused with the way out instead, nothing written, nothing deleted."""
    jpg = _jpeg_with_exif(_folder(tmp_path, "rot") / "DSC_0001 (2).jpg",
                          _malformed_exif_rotated(6))
    before = _sha(jpg)
    rc, out = _lossy(tmp_path, jpg.parent, "--delete-source", "--delete-confirm-off")
    flat = " ".join(out.split())
    assert rc != 0, out
    assert "rotated" in flat and "--force-transcode" in flat, out
    assert "metadata copied again" not in flat, out     # no second encode
    assert not _jxls(jpg.parent), out
    assert not _leftovers(jpg.parent)
    assert jpg.exists() and _sha(jpg) == before


@needs_tools
def test_639_the_unrotated_broken_ifd1_still_retries(tmp_path):
    """Orientation 1 (what the owner's file has) is not 'rotated': it retries."""
    jpg = _jpeg_with_exif(_folder(tmp_path, "rot1") / "DSC_0001 (2).jpg",
                          _malformed_exif_rotated(1))
    rc, out = _lossy(tmp_path, jpg.parent)
    assert rc == 0, out
    assert len(_jxls(jpg.parent)) == 1, out


@needs_tools
def test_639_an_unreadable_orientation_is_not_taken_for_upright(tmp_path, monkeypatch):
    """Second review: the retry drops the codestream orientation, so it runs
    only when the first encode is PROVEN upright. "Cannot tell" (neither
    jxlinfo nor the header parse answers) is an error with the way out, the
    output removed, cjxl run once."""
    m = _tr()
    jpg = _jpeg_with_exif(_folder(tmp_path, "unread") / "DSC_0001 (2).jpg", _malformed_exif())
    monkeypatch.setattr(m, "_jxl_orientation", lambda p: None)
    cjxl_runs = []
    real_run = m._run_captured
    monkeypatch.setattr(m, "_run_captured",
                        lambda cmd, *a, **k: (cjxl_runs.append(cmd) if "cjxl" in str(cmd[0])
                                              else None, real_run(cmd, *a, **k))[1])
    result, final = _encode(m, jpg)
    assert result[1] == "error", result
    assert "could not be read" in result[2] and "--force-transcode" in result[2], result
    assert len(cjxl_runs) == 1, cjxl_runs
    assert not final.exists() and not _leftovers(jpg.parent)
    assert jpg.exists()


@needs_tools
def test_639_nothing_is_written_twice_in_the_retried_output(tmp_path):
    """The markers appear once each (no #531-style doubling in the retry)."""
    jpg = _jpeg_with_exif(_folder(tmp_path, "once") / "DSC_0001 (2).jpg", _malformed_exif())
    rc, out = _lossy(tmp_path, jpg.parent)
    assert rc == 0, out
    rel = _relation(_jxls(jpg.parent)[0])
    assert len(rel) == len(set(rel)), rel
    assert len([t for t in rel if t.startswith("jxlphoto-srcsum:")]) == 1, rel


@needs_tools
def test_639_broken_ifd1_with_delete_source_deletes_after_a_complete_output(tmp_path):
    """--delete-source on a retried file: the JPEG goes only because the output
    is a complete JXL with its EXIF; the JXL stays."""
    jpg = _jpeg_with_exif(_folder(tmp_path, "del") / "DSC_0001 (2).jpg", _malformed_exif())
    rc, out = _lossy(tmp_path, jpg.parent, "--delete-source", "--delete-confirm-off")
    jxl = _jxls(jpg.parent)
    assert rc == 0, out
    assert len(jxl) == 1, out
    assert _tags(jxl[0], "IFD0:Make").get("IFD0:Make") == "TESTCAM", out
    assert not jpg.exists(), out       # the output is complete, EXIF included
    assert not _leftovers(jpg.parent)


@needs_tools
def test_639_unparseable_makernotes_stay_an_error_with_the_way_out(tmp_path):
    jpg = _jpeg_with_exif(_folder(tmp_path, "nokia") / "DSC_0001 (2).jpg", _nokia_exif())
    before = _sha(jpg)
    rc, out = _lossy(tmp_path, jpg.parent)
    flat = " ".join(out.split())
    assert rc != 0, out
    assert "Maker notes could not be parsed" in flat, out
    assert "--force-transcode" in flat, out
    assert not _jxls(jpg.parent), out
    assert not _leftovers(jpg.parent)
    assert _sha(jpg) == before and jpg.exists()


@needs_tools
def test_639_a_bad_makernotes_offset_names_the_way_out_too(tmp_path):
    """exiftool has more than one wording for maker notes it cannot rewrite: an
    HTC Desire 626 JPEG gives `Bad MakerNotes offset for Unknown_0x0000`, this
    cut-down copy of its block `Overlapping MakerNotes values`. The same
    error, so the same way out in the message; nothing written or deleted,
    and the way out works."""
    jpg = _jpeg_with_exif(_folder(tmp_path, "htc") / "DSC_0001 (2).jpg", _htc_exif())
    before = _sha(jpg)
    rc, out = _lossy(tmp_path, jpg.parent, "--delete-source", "--delete-confirm-off")
    flat = " ".join(out.split())
    assert rc != 0, out
    assert "MakerNotes" in flat and "Maker notes could not be parsed" not in flat, out
    assert "--force-transcode" in flat, out
    assert not _jxls(jpg.parent), out
    assert not _leftovers(jpg.parent)
    assert _sha(jpg) == before and jpg.exists()
    rc, out = _run(tmp_path, jpg.parent, "--mode", "1", "--force-transcode")
    assert rc == 0, out
    assert len(_jxls(jpg.parent)) == 1, out


@pytest.mark.parametrize("wording", [
    "Error: [minor] Maker notes could not be parsed - x.jxl.tmp",
    "Error: [minor] Bad MakerNotes offset for Unknown_0x0000 - x.jxl.tmp",
    "Warning: [minor] Overlapping MakerNotes values - x.jxl.tmp",
])
def test_639_the_hint_covers_every_maker_note_wording(wording):
    m = _tr()
    msg = m._with_makernote_hint("provenance/history write failed (exiftool rc=1): " + wording)
    assert msg.startswith("provenance/history write failed") and wording in msg
    assert "--force-transcode" in msg


def test_639_the_hint_stays_off_other_failures():
    m = _tr()
    msg = "provenance/history write failed (exiftool rc=1): Error: File not found - x.jxl.tmp"
    assert m._with_makernote_hint(msg) == msg


@needs_tools
def test_639_unparseable_makernotes_never_delete_the_source(tmp_path):
    jpg = _jpeg_with_exif(_folder(tmp_path, "nokia_del") / "DSC_0001 (2).jpg", _nokia_exif())
    rc, out = _lossy(tmp_path, jpg.parent, "--delete-source", "--delete-confirm-off")
    assert rc != 0, out
    assert jpg.exists(), out
    assert not _jxls(jpg.parent), out


@needs_tools
def test_639_the_way_out_works_the_lossless_transcode_of_the_same_file(tmp_path):
    jpg = _jpeg_with_exif(_folder(tmp_path, "nokia_ll") / "DSC_0001 (2).jpg", _nokia_exif())
    rc, out = _run(tmp_path, jpg.parent, "--mode", "1", "--force-transcode")
    assert rc == 0, out
    assert len(_jxls(jpg.parent)) == 1, out


@needs_tools
def test_639_a_healthy_jpeg_never_reaches_the_retry(tmp_path, monkeypatch):
    jpg = _healthy_jpeg(_folder(tmp_path, "ok") / "DSC_0001 (2).jpg")
    m = _tr()
    m.setup_logger()
    calls = []
    real = m._verify_exif_kept
    monkeypatch.setattr(m, "_verify_exif_kept",
                        lambda *a, **k: (calls.append(a), real(*a, **k))[1])
    cjxl_cmds = []
    real_run = m._run_captured
    monkeypatch.setattr(m, "_run_captured",
                        lambda cmd, *a, **k: (cjxl_cmds.append(list(cmd)), real_run(cmd, *a, **k))[1])
    res, final = _encode(m, jpg)
    assert res[1] == "ok", res
    assert not calls, calls
    assert len([c for c in cjxl_cmds if c and c[0] == "cjxl"]) == 1, cjxl_cmds
    assert not any("strip=exif" in c for cmd in cjxl_cmds for c in cmd)
    assert _tags(final, "IFD0:Make").get("IFD0:Make") == "Nikon"


@needs_tools
def test_639_an_output_that_lost_its_exif_is_an_error_and_removed(tmp_path, monkeypatch):
    """Fail closed: when the retry's output has no EXIF although the JPEG had
    some (METADATA PARTIAL), the file is an error, the partial output is gone
    and the source stays — --delete-source can never act on it."""
    jpg = _jpeg_with_exif(_folder(tmp_path, "lost") / "DSC_0001 (2).jpg", _malformed_exif())
    before = _sha(jpg)
    m = _tr()
    m.setup_logger()
    real = m._exif_tag_count
    monkeypatch.setattr(m, "_exif_tag_count",
                        lambda p: 0 if Path(p).suffix == ".tmp" or ".jxl" in Path(p).name
                        else real(p))
    res, final = _encode(m, jpg)
    assert res[1] == "error", res
    assert "METADATA PARTIAL" in res[2], res
    assert not final.exists()
    assert not _leftovers(jpg.parent)
    assert _sha(jpg) == before


@needs_tools
def test_639_a_failing_retry_is_one_retry_and_the_file_s_error(tmp_path, monkeypatch):
    jpg = _jpeg_with_exif(_folder(tmp_path, "twice") / "DSC_0001 (2).jpg", _malformed_exif())
    m = _tr()
    m.setup_logger()
    calls = []

    def failing(src, dst):
        calls.append(Path(dst).name)
        raise RuntimeError("metadata copy failed (exiftool rc=1): simulated")

    monkeypatch.setattr(m, "_copy_metadata", failing)
    res, final = _encode(m, jpg)
    assert res[1] == "error" and "simulated" in res[2], res
    assert len(calls) == 2, calls
    assert not final.exists() and not _leftovers(jpg.parent)


@needs_tools
def test_639_a_png_input_gets_no_retry(tmp_path, monkeypatch):
    """The retry is for a JPEG (S5): a PNG whose copy fails is an error at
    once, as before."""
    folder = _folder(tmp_path, "png")
    png = folder / "DSC_0001 (2).png"
    Image.fromarray((np.random.default_rng(3).random((60, 80, 3)) * 255).astype(np.uint8)).save(png)
    m = _tr()
    m.setup_logger()
    calls = []

    def failing(src, dst):
        calls.append(1)
        raise RuntimeError("metadata copy failed (exiftool rc=1): simulated")

    monkeypatch.setattr(m, "_copy_metadata", failing)
    res, final = _encode(m, png)
    assert res[1] == "error" and len(calls) == 1, (res, calls)


# ------------------------------------------------- the owner's two real files
@needs_tools
@pytest.mark.skipif(not OWNER_BAD_IFD1.exists(), reason="E:\\TESTE\\JPEG_varied not present")
def test_639_owner_file_with_a_broken_ifd1(tmp_path):
    folder = _folder(tmp_path, "owner1")
    jpg = folder / OWNER_BAD_IFD1.name
    shutil.copy2(OWNER_BAD_IFD1, jpg)
    rc, out = _lossy(tmp_path, folder)
    assert rc == 0, out
    jxl = _jxls(folder)
    assert len(jxl) == 1, out
    assert _tags(jxl[0], "IFD0:Make").get("IFD0:Make"), out
    assert any(t.startswith("jxlphoto-srcsum:") for t in _relation(jxl[0]))


@needs_tools
@pytest.mark.skipif(not OWNER_NOKIA.exists(), reason="E:\\TESTE\\JPEG_varied not present")
def test_639_owner_nokia_file_is_an_error_with_the_way_out(tmp_path):
    folder = _folder(tmp_path, "owner2")
    jpg = folder / OWNER_NOKIA.name
    shutil.copy2(OWNER_NOKIA, jpg)
    rc, out = _lossy(tmp_path, folder, "--delete-source", "--delete-confirm-off")
    flat = " ".join(out.split())
    assert rc != 0 and jpg.exists(), out
    assert "--force-transcode" in flat, out
    rc2, out2 = _run(tmp_path, folder, "--mode", "1", "--force-transcode")
    assert rc2 == 0, out2
