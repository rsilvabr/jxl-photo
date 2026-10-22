"""Explorer thumbnails (EXPLORER_THUMBNAIL / --explorer-thumbnail /
--refresh-explorer-thumbnails / menu option 9).

A 768 px sRGB JPEG in an ISO BMFF `uuid` box of the JXL container, after the
line `jxlphoto-thumbnail:v1:<fingerprint>`, the fingerprint naming the
codestream it was made from (jxl-thumb 0.2.0+ shows it only while they
match). Not EXIF: a 768 px JPEG does not fit a JPEG's 64 KB EXIF segment and
djxl's JPEG output then fails. Real codecs and exiftool; every file inside a
folder named with Japanese, brackets, a space and %.
"""
import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest
import tifffile

import jxl_tiff_encoder as enc
import jxl_recompressor as rec
from _icc_fixtures import romm_toe_icc

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "吾妻山 [thumb] 100%"
_TOOLS = ("cjxl", "djxl", "exiftool", "magick", "jxlinfo")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")
UUID = bytes.fromhex("4e0b4016f51c473d9669bc35aebe3c16")
MARKER = b"jxlphoto-thumbnail:v1:"


# ── helpers: an independent reader, as jxl-thumb reads ────────────────────
def _boxes(data):
    out, pos = [], 0
    while pos + 8 <= len(data):
        size, typ = struct.unpack(">I4s", data[pos:pos + 8])
        hdr = 8
        if size == 1:
            size, hdr = struct.unpack(">Q", data[pos + 8:pos + 16])[0], 16
        elif size == 0:
            size = len(data) - pos
        out.append((typ, data[pos + hdr:pos + size]))
        pos += size
    return out


def _fingerprint(data):
    if data[:2] == b"\xff\x0a":
        cs = data
    else:
        cs = b"".join(p if t == b"jxlc" else p[4:] for t, p in _boxes(data)
                      if t in (b"jxlc", b"jxlp"))
    h = 0xcbf29ce484222325
    for b in cs[:65536]:
        h = ((h ^ b) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return f"{len(cs)}:{h:016x}"


def _thumb_box(path):
    """(recorded fingerprint, JPEG bytes, box index, index of the first
    codestream box), or None when the file has no thumbnail box."""
    data = Path(path).read_bytes()
    boxes = _boxes(data)
    first_cs = next(i for i, (t, _) in enumerate(boxes) if t in (b"jxlc", b"jxlp"))
    for i, (t, p) in enumerate(boxes):
        if t == b"uuid" and p[:16] == UUID:
            assert p[16:16 + len(MARKER)] == MARKER
            line, jpeg = p[16 + len(MARKER):].split(b"\n", 1)
            return line.decode(), jpeg, i, first_cs
    return None


def _valid(path):
    tb = _thumb_box(path)
    return tb is not None and tb[0] == _fingerprint(Path(path).read_bytes())


def _image(jpeg):
    from PIL import Image
    return Image.open(io.BytesIO(jpeg)).convert("RGB")


def _env(tmp_path, **extra):
    return {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"), **extra}


def _run(tmp_path, script, *args, env=None):
    r = subprocess.run([sys.executable, str(REPO / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, env=_env(tmp_path, **(env or {})))
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return r.stdout + r.stderr


def _exiftool_bytes(*args):
    fd, af = tempfile.mkstemp(suffix=".args")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write("-charset\nfilename=utf8\n" + "\n".join(map(str, args)) + "\n")
    try:
        return subprocess.run(["exiftool", "-@", af], capture_output=True, timeout=120).stdout
    finally:
        os.unlink(af)


def _tiff(folder, name, arr, icc=None):
    folder.mkdir(parents=True, exist_ok=True)
    extra = [(34675, "B", len(icc), icc, True)] if icc else []
    tifffile.imwrite(folder / name, arr, photometric="rgb", extratags=extra)
    return folder / name


def _photo(h=600, w=1000, seed=1):
    """Noisy enough that the 768 px JPEG is far over 64 KB, like a photo's."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    base = np.stack([x * 65535 // (w - 1), y * 65535 // (h - 1),
                     np.full((h, w), 30000)], axis=-1)
    return np.clip(base + rng.integers(-9000, 9000, base.shape), 0, 65535).astype(np.uint16)


# ── fingerprint and box writer ─────────────────────────────────────────────
def _box(typ, payload):
    return struct.pack(">I4s", 8 + len(payload), typ) + payload


def test_fingerprint_names_the_codestream_not_the_container(tmp_path):
    cs = b"\xff\x0a" + bytes(range(256)) * 600
    bare = tmp_path / "bare.jxl"
    bare.write_bytes(cs)
    sig = _box(b"JXL ", b"\r\n\x87\n") + _box(b"ftyp", b"jxl \x00\x00\x00\x00jxl ")
    one = tmp_path / "jxlc.jxl"
    one.write_bytes(sig + _box(b"Exif", b"\x00" * 40) + _box(b"jxlc", cs))
    split = tmp_path / "jxlp.jxl"
    split.write_bytes(sig + _box(b"jxlp", struct.pack(">I", 0) + cs[:21])
                      + _box(b"brob", b"Exif" + b"\x01" * 30)
                      + _box(b"jxlp", struct.pack(">I", 0x80000001) + cs[21:])
                      + _box(b"xml ", b"<x/>"))
    fps = {enc._codestream_fingerprint(p) for p in (bare, one, split)}
    assert fps == {_fingerprint(cs)}
    assert rec._codestream_fingerprint(split) == enc._codestream_fingerprint(split)
    changed = tmp_path / "changed.jxl"
    changed.write_bytes(cs[:1000] + b"\x00" + cs[1001:])
    assert enc._codestream_fingerprint(changed) != _fingerprint(cs)
    not_jxl = tmp_path / "x.jxl"
    not_jxl.write_bytes(b"GIF89a" + b"\x00" * 100)
    assert enc._codestream_fingerprint(not_jxl) is None

    # The box goes right before the codestream, replaces an older one, keeps
    # every other box byte for byte, and None removes it.
    before = split.read_bytes()
    enc._write_thumbnail_box(split, b"\xff\xd8JPEG1\xff\xd9", "1:x")
    enc._write_thumbnail_box(split, b"\xff\xd8JPEG2\xff\xd9", _fingerprint(cs))
    fp, jpeg, i, first_cs = _thumb_box(split)
    assert (fp, jpeg) == (_fingerprint(cs), b"\xff\xd8JPEG2\xff\xd9")
    assert i == first_cs - 1
    assert enc._thumbnail_box_fingerprint(split) == _fingerprint(cs)
    others = [b for b in _boxes(split.read_bytes()) if not b[1].startswith(UUID)]
    assert others == _boxes(before)
    enc._write_thumbnail_box(split, None, None)
    assert split.read_bytes() == before
    assert enc._thumbnail_box_fingerprint(split) == ""
    assert not list(tmp_path.glob("*.tmp"))


# ── encoder ────────────────────────────────────────────────────────────────
@needs_tools
def test_encoder_lossless_auto_writes_a_valid_srgb_thumbnail(tmp_path):
    """auto: a lossless output gets one, 768 px, converted to sRGB from a
    wide-gamut profile (a ROMM/ProPhoto-like table-curve profile here)."""
    src = tmp_path / HOSTILE
    arr = np.zeros((600, 1000, 3), np.int64)
    arr[:, :500] = (40000, 20000, 9000)        # a warm ProPhoto colour
    arr[:, 500:] = (30000, 30000, 30000)       # neutral grey
    # Faint noise (±1/256 of a level): the lossless file gets photo-sized
    # (a thumbnail is only stored when it is < 10% of the file) while the
    # patches stay flat.
    arr = (arr + np.random.default_rng(3).integers(-256, 256, arr.shape)).astype(np.uint16)
    icc = romm_toe_icc()
    _tiff(src, "写真 (1).tif", arr, icc)
    _run(tmp_path, "jxl_tiff_encoder.py", src, "--mode", "1", "--distance", "0",
         "--effort", "1", "--no-preflight")
    out = src / "converted_jxl" / "写真 (1).jxl"
    assert _valid(out)
    fp, jpeg, i, first_cs = _thumb_box(out)
    assert i < first_cs, "before the codestream: a reader never crosses the image data"
    im = _image(jpeg)
    assert im.size == (768, 461)
    from PIL import Image, ImageCms
    ref = Image.fromarray(np.array([[(156, 78, 35), (117, 117, 117)]], np.uint8), "RGB")
    ref = ImageCms.profileToProfile(
        ref, ImageCms.ImageCmsProfile(io.BytesIO(icc)),
        ImageCms.createProfile("sRGB"), renderingIntent=1)
    got, want = np.asarray(im), np.asarray(ref)
    for (gy, gx), (ry, rx) in (((230, 190), (0, 0)), ((230, 580), (0, 1))):
        assert np.abs(got[gy, gx].astype(int) - want[ry, rx].astype(int)).max() <= 6, (
            got[gy, gx], want[ry, rx])
    assert np.abs(got[230, 190].astype(int) - (156, 78, 35)).max() > 10   # converted


@needs_tools
@pytest.mark.parametrize("flag,expect", [(None, False), ("on", True), ("off", False)])
def test_encoder_lossy(tmp_path, flag, expect):
    src = tmp_path / HOSTILE
    _tiff(src, "a [x].tif", _photo(2000, 3000))
    extra = ["--explorer-thumbnail", flag] if flag else []
    _run(tmp_path, "jxl_tiff_encoder.py", src, "--mode", "1", "--distance", "1",
         "--effort", "3", "--no-preflight", *extra)
    out = src / "converted_jxl" / "a [x].jxl"
    assert _valid(out) is expect
    if not expect:
        assert _thumb_box(out) is None


@needs_tools
def test_encoder_strip_writes_none(tmp_path):
    src = tmp_path / HOSTILE
    _tiff(src, "a.tif", _photo())
    _run(tmp_path, "jxl_tiff_encoder.py", src, "--mode", "1", "--distance", "1",
         "--effort", "3", "--no-preflight", "--strip", "--explorer-thumbnail", "on")
    assert _thumb_box(src / "converted_jxl" / "a.jxl") is None


@needs_tools
def test_a_large_thumbnail_breaks_nothing_downstream(tmp_path):
    """The reason it is not EXIF: djxl copies a JXL's EXIF into the JPEG it
    writes, where a 768 px JPEG does not fit. In its own box nothing else
    sees it: djxl to JPEG and PNG, exiftool edits (the box stays valid),
    the decoder's TIFF and the transcoder's JPEG."""
    src = tmp_path / HOSTILE
    _tiff(src, "m.tif", _photo())
    _run(tmp_path, "jxl_tiff_encoder.py", src, "--mode", "1", "--distance", "0",
         "--effort", "1", "--no-preflight")
    jdir = src / "converted_jxl"
    m = jdir / "m.jxl"
    assert _valid(m)
    assert len(_thumb_box(m)[1]) > 66000, "the test needs a thumbnail over 64 KB"
    for ext in ("jpg", "png"):
        r = subprocess.run(["djxl", str(m), str(tmp_path / f"d.{ext}")],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    _exiftool_bytes("-overwrite_original", "-XMP-dc:Title=edited", "-Artist=me", m)
    assert _valid(m), "a metadata edit keeps it current"
    _run(tmp_path, "jxl_tiff_decoder.py", jdir, "--mode", "1")
    _run(tmp_path, "jxl_jpeg_transcoder.py", jdir, "--mode", "1", "--from-jxl",
         "--format", "jpeg")
    jpg = next(jdir.rglob("m.jpg"))
    assert b"jxlphoto-thumbnail" not in jpg.read_bytes()
    for tif in jdir.rglob("m.tif"):
        assert b"jxlphoto-thumbnail" not in tif.read_bytes()


# ── recompressor ───────────────────────────────────────────────────────────
@needs_tools
def test_recompressor_never_inherits_the_sources(tmp_path):
    src = tmp_path / HOSTILE
    _tiff(src, "m.tif", _photo())
    _run(tmp_path, "jxl_tiff_encoder.py", src, "--mode", "1", "--distance", "0",
         "--effort", "1", "--no-preflight")
    master_dir = src / "converted_jxl"
    assert _valid(master_dir / "m.jxl")
    _run(tmp_path, "jxl_recompressor.py", master_dir, "--mode", "1",
         "--distance", "1", "--effort", "3", "--no-keep-smaller")
    out = master_dir / "recompressed_jxl" / "m.jxl"
    assert out.exists() and _thumb_box(out) is None


@needs_tools
def test_recompressor_lossless_output_gets_its_own(tmp_path):
    src = tmp_path / HOSTILE
    _tiff(src, "m.tif", _photo(2000, 3000))
    _run(tmp_path, "jxl_tiff_encoder.py", src, "--mode", "1", "--distance", "1",
         "--effort", "3", "--no-preflight", "--explorer-thumbnail", "on")
    master_dir = src / "converted_jxl"
    _run(tmp_path, "jxl_recompressor.py", master_dir, "--mode", "1",
         "--distance", "0", "--effort", "1", "--no-keep-smaller",
         "--on-downgrade", "convert", "--on-unknown", "convert")
    out = master_dir / "recompressed_jxl" / "m.jxl"
    assert _valid(out)
    assert _thumb_box(out)[0] != _thumb_box(master_dir / "m.jxl")[0]


# ── --refresh-explorer-thumbnails ──────────────────────────────────────────
@needs_tools
def test_refresh_adds_renews_keeps_the_date_and_removes(tmp_path):
    src = tmp_path / HOSTILE
    _tiff(src, "a.tif", _photo())
    _tiff(src, "b.tif", _photo(seed=2))
    _run(tmp_path, "jxl_tiff_encoder.py", src, "--mode", "1", "--distance", "0",
         "--effort", "1", "--no-preflight", "--explorer-thumbnail", "off")
    folder = src / "converted_jxl"
    a, b = folder / "a.jxl", folder / "b.jxl"
    old = 1_600_000_000
    for f in (a, b):
        os.utime(f, (old, old))
    cs_before = rec._codestream_sha256(a)
    log = _run(tmp_path, "jxl_recompressor.py", folder, "--mode", "1",
               "--refresh-explorer-thumbnails")
    assert "2 written" in log, log[-2000:]
    for f in (a, b):
        assert _valid(f)
        assert int(f.stat().st_mtime) == old, "the modified date must be kept (sync)"
    assert rec._codestream_sha256(a) == cs_before
    assert not list(folder.glob("*.tmp"))

    data = a.read_bytes()
    log = _run(tmp_path, "jxl_recompressor.py", folder, "--mode", "1",
               "--refresh-explorer-thumbnails")
    assert "2 already current" in log, log[-2000:]
    assert a.read_bytes() == data

    # A stale one (another codestream's fingerprint) is renewed.
    rec._write_thumbnail_box(b, b"\xff\xd8old\xff\xd9", "1:0000000000000000")
    os.utime(b, (old, old))
    log = _run(tmp_path, "jxl_recompressor.py", folder, "--mode", "1",
               "--refresh-explorer-thumbnails")
    assert "1 written" in log and _valid(b), log[-2000:]

    log = _run(tmp_path, "jxl_recompressor.py", folder, "--mode", "1",
               "--refresh-explorer-thumbnails", "--explorer-thumbnail", "off")
    assert "2 removed" in log, log[-2000:]
    for f in (a, b):
        assert _thumb_box(f) is None
        assert int(f.stat().st_mtime) == old


@needs_tools
def test_refresh_skips_lossy_in_auto_wraps_a_bare_file_never_touches_jbrd(tmp_path):
    folder = tmp_path / HOSTILE
    folder.mkdir()
    from PIL import Image
    img = Image.fromarray((_photo(2000, 3000) >> 8).astype(np.uint8))
    img.save(folder / "p.jpg", quality=90)
    img.save(folder / "p.png")
    jbrd = folder / "j.jxl"
    subprocess.run(["cjxl", str(folder / "p.jpg"), str(jbrd), "--quiet"], check=True)
    lossy = folder / "l.jxl"           # cjxl without metadata: a bare codestream
    subprocess.run(["cjxl", str(folder / "p.png"), str(lossy), "-d", "1", "-e", "3",
                    "--quiet"], check=True)
    assert lossy.read_bytes()[:2] == b"\xff\x0a"
    before = {p.name: p.read_bytes() for p in (jbrd, lossy)}
    log = _run(tmp_path, "jxl_recompressor.py", folder, "--mode", "1",
               "--refresh-explorer-thumbnails", "--explorer-thumbnail", "on")
    assert "never edited" in log
    assert jbrd.read_bytes() == before["j.jxl"]
    assert _valid(lossy)                      # wrapped in a container, same codestream
    assert _fingerprint(lossy.read_bytes()) == _fingerprint(before["l.jxl"])
    lossy.write_bytes(before["l.jxl"])
    log = _run(tmp_path, "jxl_recompressor.py", folder, "--mode", "1",
               "--refresh-explorer-thumbnails")
    assert "lossless only" in log
    assert lossy.read_bytes() == before["l.jxl"]


# ── wrapper: menu option 9 ─────────────────────────────────────────────────
@pytest.mark.parametrize("answers,mode,policy,dry", [
    (["", "", "", ""], "8", "auto", False),
    (["", "n", "2", "y"], "0", "on", True),
    (["", "", "3", ""], "8", "off", False),
])
def test_menu_option_9_runs_the_refresh(tmp_path, monkeypatch, answers, mode, policy, dry):
    import jxl_photo as wp
    folder = tmp_path / HOSTILE
    folder.mkdir()
    path = tmp_path / ".jxl_tools_config.json"
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path", lambda self: path)
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    config = wp.ConfigManager()
    config.config.last_input_dir = str(folder)
    menu = wp.InteractiveMenu(config, wp.DependencyChecker(config))
    seen = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child", lambda self, cmd: seen.append(cmd) or 0)
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda *a: next(it))
    menu.explorer_thumbnail_flow({"djxl": True, "exiftool": True, "magick": True})
    cmd = seen[0]
    assert cmd[1].endswith("jxl_recompressor.py") and cmd[2] == str(folder)
    assert cmd[cmd.index("--mode") + 1] == mode
    assert "--refresh-explorer-thumbnails" in cmd
    assert cmd[cmd.index("--explorer-thumbnail") + 1] == policy
    assert ("--dry-run" in cmd) is dry


@needs_tools
def test_small_files_get_none(tmp_path):
    """Not worth it: an 800 px d=3 JXL of a few dozen KB (the thumbnail would
    be bigger than the file), or an image no larger than the thumbnail —
    even with `on`. The refresh says why."""
    src = tmp_path / HOSTILE
    _tiff(src, "web [800].tif", _photo(533, 800))
    _tiff(src, "tiny.tif", _photo(400, 600))
    _run(tmp_path, "jxl_tiff_encoder.py", src, "--mode", "1", "--distance", "3",
         "--effort", "3", "--no-preflight", "--explorer-thumbnail", "on")
    out = src / "converted_jxl"
    assert _thumb_box(out / "web [800].jxl") is None
    assert _thumb_box(out / "tiny.jxl") is None
    log = _run(tmp_path, "jxl_recompressor.py", out, "--mode", "1",
               "--refresh-explorer-thumbnails", "--explorer-thumbnail", "on")
    assert "2 skipped" in log and "small file" in log, log[-1500:]


@needs_tools
@pytest.mark.parametrize("installed,expect", [("1", True), ("0", False)])
def test_auto_covers_lossy_outputs_when_jxl_thumb_is_installed(tmp_path, installed, expect):
    """auto = every output when jxl-thumb shows them, lossless only otherwise
    (JXLPHOTO_JXL_THUMB pins the registry check)."""
    src = tmp_path / HOSTILE
    _tiff(src, "a.tif", _photo(2000, 3000))
    log = _run(tmp_path, "jxl_tiff_encoder.py", src, "--mode", "1", "--distance", "1",
               "--effort", "3", "--no-preflight", env={"JXLPHOTO_JXL_THUMB": installed})
    assert _valid(src / "converted_jxl" / "a.jxl") is expect
    assert ("jxl-thumb is installed" in log) is expect


def test_jxl_thumb_detection_reads_the_handler(monkeypatch):
    import types
    monkeypatch.delenv("JXLPHOTO_JXL_THUMB", raising=False)
    monkeypatch.setattr(enc.os, "name", "nt")

    def reg(values):
        class K:
            def __init__(self, sub): self.sub = sub
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def open_key(root, sub):
            if sub not in values:
                raise FileNotFoundError(sub)
            return K(sub)
        return types.SimpleNamespace(HKEY_CLASSES_ROOT=0, OpenKey=open_key,
                                     QueryValue=lambda k, n: values[k.sub])
    key = enc._JXL_THUMB_HANDLER_KEYS[0]
    for values, want in (({key: "{EF830030-FBD6-4B62-BFD4-0DBD590D6135}"}, True),
                         ({key: "{C7657C4A-9F68-40fa-A4DF-96BC08EB3551}"}, False),
                         ({}, False)):
        monkeypatch.setitem(sys.modules, "winreg", reg(values))
        enc._jxl_thumb_seen.clear()
        assert enc._jxl_thumb_installed() is want
    enc._jxl_thumb_seen.clear()
