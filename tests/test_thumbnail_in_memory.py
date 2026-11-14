"""Round 86 (#671) — the Explorer thumbnail's full-size decode never touches
the disk.

Until v2.30.1 djxl wrote the whole image as a PAM into the run's temp folder
(272 MB for a 45 MP 16-bit page) and ImageMagick read it back, once per
output. On a slow staging SSD (45 MB/s) those writes set the pace of a whole
rebuild: 7 masters a minute instead of 34 (2026-11-14). Now djxl writes the
PAM to stdout, read into memory (`_run_to_memory`), and magick reads the
samples RAW from stdin (`_pam_raw_input`): given the PAM, PNG or MIFF on stdin,
magick copies it to a temp file first (measured), raw samples it reads as
they come. A PAM the raw read cannot carry (a 12-bit MAXVAL) still goes
through the file, exactly the old route.

Real codecs: every JXL sits in a folder named with Japanese, brackets, a space
and `100%off %d`; the temp folder has Japanese and a space (the toolkit's own
temp folder is the one magick reads the profiles from).
"""
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

import jxl_recompressor as rec
import jxl_tiff_encoder as enc
from _icc_fixtures import grey_toe_icc, romm_toe_icc

HOSTILE = "吾妻山 [サムネ] 100%off %d"
_TOOLS = ("cjxl", "djxl", "magick")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")


def _pam(path, arr, maxval, tupltype):
    h, w, depth = arr.shape
    hdr = (f"P7\nWIDTH {w}\nHEIGHT {h}\nDEPTH {depth}\nMAXVAL {maxval}\n"
           f"TUPLTYPE {tupltype}\nENDHDR\n").encode()
    path.write_bytes(hdr + arr.astype(">u2" if maxval > 255 else "u1").tobytes())


def _photo(h, w, channels, maxval, seed=5):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    planes = [x * maxval // (w - 1), y * maxval // (h - 1),
              np.full((h, w), maxval // 3), (x + y) * maxval // (w + h)]
    base = np.stack(planes[:channels], axis=-1)
    return np.clip(base + rng.integers(-maxval // 40, maxval // 40, base.shape), 0, maxval)


def _jxl(folder, name, channels, maxval, tupltype, distance="0", icc=None, h=900, w=1200):
    """A JXL made by cjxl from a PAM, optionally with an ICC profile."""
    folder.mkdir(parents=True, exist_ok=True)
    src = folder / (name + ".pam")
    _pam(src, _photo(h, w, channels, maxval), maxval, tupltype)
    out = folder / (name + ".jxl")
    cmd = ["cjxl", str(src), str(out), "-d", distance, "-e", "1"]
    if icc:
        prof = folder / (name + ".icc")
        prof.write_bytes(icc)
        cmd += ["-x", f"icc_pathname={prof}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    src.unlink()
    return out


def _old_route(jxl, tmp):
    """The thumbnail as v2.30.1 made it: djxl -> thumb.pam -> magick."""
    tmp.mkdir(parents=True, exist_ok=True)
    pam, icc, jpg, srgb = (tmp / "thumb.pam", tmp / "thumb_src.icc", tmp / "thumb.jpg",
                           tmp / "thumb_srgb.icc")
    from PIL import ImageCms
    srgb.write_bytes(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    subprocess.run(["djxl", str(jxl), str(pam), f"--icc_out={icc}"], check=True,
                   capture_output=True, timeout=300)
    size = int(enc.EXPLORER_THUMBNAIL_SIZE)
    cmd = ["magick", str(pam), "-resize", f"{size}x{size}>"]
    if icc.exists() and icc.stat().st_size:
        cmd += ["-profile", str(icc)]
    cmd += ["-profile", str(srgb), "-background", "white", "-flatten", "-strip",
            "-quality", "85", "-sampling-factor", "2x2", "-interlace", "none",
            "jpeg:" + enc._magick_out_path(jpg)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    return jpg.read_bytes()


# ── the decode never lands in the temp folder ──────────────────────────────
@needs_tools
@pytest.mark.parametrize("mod", [enc, rec], ids=["encoder", "recompressor"])
def test_the_full_size_decode_never_lands_in_the_temp_folder(tmp_path, monkeypatch, mod):
    jxl = _jxl(tmp_path / HOSTILE, "写真 (1)", 3, 65535, "RGB", icc=romm_toe_icc())
    pam_bytes = 900 * 1200 * 3 * 2
    tmp = tmp_path / "一時 temp"
    tmp.mkdir()
    seen, djxl_cmds = [], []
    real_captured = mod._run_captured
    # Looked up, not assumed: the same test runs against the code before
    # #671 (the pre-fix proof), where djxl went through _run_captured.
    real_memory = getattr(mod, "_run_to_memory", None)

    def spy_captured(cmd, timeout, *a, **k):
        if os.path.basename(str(cmd[0])).lower().startswith("djxl"):
            djxl_cmds.append(list(cmd))
        if os.path.basename(str(cmd[0])).lower().startswith("magick"):
            # What the temp folder holds while magick reads the image.
            seen.append({p.name: p.stat().st_size for p in tmp.iterdir()})
        return real_captured(cmd, timeout, *a, **k)

    def spy_memory(cmd, timeout, *a, **k):
        djxl_cmds.append(list(cmd))
        return real_memory(cmd, timeout, *a, **k)

    monkeypatch.setattr(mod, "_run_captured", spy_captured)
    if real_memory is not None:
        monkeypatch.setattr(mod, "_run_to_memory", spy_memory)
    jpeg = mod._make_explorer_thumbnail(jxl, tmp)
    assert jpeg and jpeg[:2] == b"\xff\xd8"
    assert seen, "magick never ran"
    big = {n: s for n, s in seen[0].items() if s >= pam_bytes // 10}
    assert not big, f"a decoded image sat in the temp folder while magick ran: {big}"
    assert djxl_cmds and all(c[2] == "-" for c in djxl_cmds), \
        f"djxl was asked to write a file: {djxl_cmds}"


# ── the same JPEG, byte for byte, as the old route ─────────────────────────
@needs_tools
@pytest.mark.parametrize("name,channels,maxval,tupl,icc,distance,raw", [
    ("rgb16 prophoto lossless", 3, 65535, "RGB", "romm", "0", True),
    ("rgb16 prophoto lossy", 3, 65535, "RGB", "romm", "1", True),
    ("rgb8 srgb", 3, 255, "RGB", None, "0", True),
    ("gray16 toe", 1, 65535, "GRAYSCALE", "grey", "0", True),
    ("gray8", 1, 255, "GRAYSCALE", None, "1", True),
    ("graya16", 2, 65535, "GRAYSCALE_ALPHA", None, "0", True),
    ("rgba16", 4, 65535, "RGB_ALPHA", "romm", "0", True),
    ("rgb12 (the file route)", 3, 4095, "RGB", None, "0", False),
])
def test_same_jpeg_as_the_old_route(tmp_path, monkeypatch, name, channels, maxval, tupl,
                                    icc, distance, raw):
    profile = {"romm": romm_toe_icc, "grey": grey_toe_icc}.get(icc)
    jxl = _jxl(tmp_path / HOSTILE, name, channels, maxval, tupl, distance,
               icc=profile() if profile else None)
    magick_inputs = []
    real = enc._run_captured

    def spy(cmd, timeout, *a, **k):
        if os.path.basename(str(cmd[0])).lower().startswith("magick"):
            magick_inputs.append((cmd[1], k.get("input") is not None))
        return real(cmd, timeout, *a, **k)

    monkeypatch.setattr(enc, "_run_captured", spy)
    new_tmp = tmp_path / "一時 new"
    new_tmp.mkdir()
    new = enc._make_explorer_thumbnail(jxl, new_tmp)
    old = _old_route(jxl, tmp_path / "一時 old")
    assert new == old, f"{name}: {len(new or b'')} vs {len(old)} bytes"
    # Which route ran: raw samples on stdin, or the PAM file (12-bit).
    assert magick_inputs and magick_inputs[0][1] is raw, magick_inputs
    assert (magick_inputs[0][0] == "-size") is raw


@needs_tools
def test_a_broken_file_still_only_warns(tmp_path, monkeypatch):
    bad = tmp_path / HOSTILE / "壊れた.jxl"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 64)
    tmp = tmp_path / "一時"
    tmp.mkdir()
    warnings = []
    monkeypatch.setattr(enc.logger, "warning", lambda msg, *a, **k: warnings.append(msg))
    assert enc._make_explorer_thumbnail(bad, tmp) is None
    assert len(warnings) == 1 and "Explorer thumbnail skipped (djxl:" in warnings[0], warnings
    assert "壊れた.jxl" in warnings[0]


# ── _pam_raw_input ─────────────────────────────────────────────────────────
def _hdr(w=4, h=3, depth=3, maxval=65535, tupl="RGB", extra=""):
    return (f"P7\nWIDTH {w}\nHEIGHT {h}\nDEPTH {depth}\nMAXVAL {maxval}\n{extra}"
            f"TUPLTYPE {tupl}\nENDHDR\n").encode()


@pytest.mark.parametrize("tupl,depth,fmt", [("GRAYSCALE", 1, "gray"), ("GRAYSCALE_ALPHA", 2, "graya"),
                                            ("RGB", 3, "rgb"), ("RGB_ALPHA", 4, "rgba")])
@pytest.mark.parametrize("maxval,bits", [(255, 8), (65535, 16)])
def test_pam_raw_input_maps_each_tuple_type(tupl, depth, fmt, maxval, bits):
    payload = bytes((i * 7) % 256 for i in range(4 * 3 * depth * (bits // 8)))
    data = _hdr(depth=depth, maxval=maxval, tupl=tupl, extra="# a comment\n") + payload
    args, pixels = enc._pam_raw_input(data)
    assert args == ["-size", "4x3", "-depth", str(bits), "-endian", "MSB", f"{fmt}:-",
                    "+size", "+depth", "+endian"]
    assert bytes(pixels) == payload


@pytest.mark.parametrize("data", [
    _hdr(maxval=4095) + b"\x00" * (4 * 3 * 3 * 2),               # 12-bit: read as full range
    _hdr() + b"\x00" * (4 * 3 * 3 * 2 - 1),                      # a short read
    _hdr() + b"\x00" * (4 * 3 * 3 * 2) + _hdr(),                 # a second frame
    _hdr(depth=4, tupl="RGB") + b"\x00" * (4 * 3 * 4 * 2),       # DEPTH and type disagree
    _hdr(tupl="CMYK", depth=4) + b"\x00" * (4 * 3 * 4 * 2),      # an unknown type
    _hdr(extra="TUPLTYPE RGB\n") + b"\x00" * (4 * 3 * 3 * 2),    # two TUPLTYPE lines
    b"P6\n4 3\n65535\n" + b"\x00" * (4 * 3 * 3 * 2),             # not a PAM
    b"P7\nWIDTH 4\n" + b"\x00" * 100,                            # no ENDHDR
    b"",
], ids=["12-bit", "short", "two frames", "depth mismatch", "unknown type",
        "two tupltypes", "not P7", "no ENDHDR", "empty"])
def test_pam_raw_input_refuses_what_raw_samples_cannot_carry(data):
    assert enc._pam_raw_input(data) is None


# ── _run_to_memory ─────────────────────────────────────────────────────────
_WRITER = ("import sys, time; time.sleep(0.3); "
           "sys.stdout.buffer.write(bytes(range(256)) * 80000); "
           "sys.stderr.write('done'); sys.exit(3)")


@pytest.mark.parametrize("mod", [enc, rec], ids=["encoder", "recompressor"])
def test_run_to_memory_reads_everything_without_a_thread(mod, monkeypatch):
    def _no_thread(*_a, **_k):
        raise AssertionError("a thread was started")

    monkeypatch.setattr(threading.Thread, "start", _no_thread)
    r = mod._run_to_memory([sys.executable, "-c", _WRITER], 60)
    assert r.returncode == 3
    assert r.stdout == bytes(range(256)) * 80000
    assert r.stderr == b"done"


def test_run_to_memory_times_out_and_kills_the_child(tmp_path):
    held = tmp_path / "held by the child.txt"
    child = ("import sys, time; f = open(sys.argv[1], 'w'); f.write('x'); f.flush(); "
             "time.sleep(60)")
    t = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        enc._run_to_memory([sys.executable, "-c", child, str(held)], 2)
    assert time.monotonic() - t < 30
    # The child held the file open; on Windows it can be deleted only once
    # the child is gone.
    for _ in range(50):
        try:
            held.unlink()
            break
        except PermissionError:
            time.sleep(0.1)
    assert not held.exists(), "the child was left running"
