"""The should-fix items of the v2.26.0 review (round 77, #636-#637).

#636 (decoder, transcoder, recompressor)  Without jxlinfo on PATH, a JXL made
     from a rotated JPEG still came out turned twice: the codestream
     orientation was only read through jxlinfo. It is now read from the
     codestream header in Python when jxlinfo cannot answer
     (_header_orientation, identical in the three scripts).
#637 (transcoder)  InteropIndex/InteropVersion (exiftool's "unsafe" tags,
     copied only when named) were lost on every PNG and on a JPEG that went
     through ImageMagick (the original-orientation JPEG, derivatives). They
     are copied by name now, and cleared on a colour-converted output.
Also: the transcoder tells a JXL without the jxlphoto-orientation token from
its XMP box, without an exiftool run (_xmp_box_mentions); tools/ui_tour.py
runs a copy of the scripts, so the repo's own manifests/ never change a
screen.

Real codecs; every file inside a folder named with Japanese, brackets, a
space and %.

Pre-fix proof: point the tests at the HEAD scripts
    git show HEAD:<script> > <tmp>/<script>     (all five)
    JXLPHOTO_SCRIPTS_UNDER_TEST=<tmp> python -m pytest tests/test_should_fix_261110.py
"""
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
import tifffile
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

HOSTILE = "伊豆沼 [tmp] 100%"
TOKEN = "jxlphoto-orientation:"
_TOOLS = ("cjxl", "djxl", "exiftool", "jxlinfo", "magick")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")
ORIENTED = ["jxl_tiff_decoder", "jxl_jpeg_transcoder", "jxl_recompressor"]
RECOMPRESS = ["--on-unknown", "convert", "--on-regeneration", "convert",
              "--on-downgrade", "convert"]

# Runs a script with jxlinfo hidden from shutil.which (every jxlinfo call of
# the toolkit goes through _jxlinfo_text, which asks shutil.which first).
_LAUNCHER = '''import runpy, shutil, sys
from pathlib import Path
_which = shutil.which
shutil.which = lambda n, *a, **k: (None if str(n).lower().startswith("jxlinfo")
                                   else _which(n, *a, **k))
script = sys.argv[1]
sys.argv = [script] + sys.argv[2:]
sys.path.insert(0, str(Path(script).parent))
runpy.run_path(script, run_name="__main__")
'''


# ----------------------------------------------------------------- helpers
def _env(tmp_path):
    return {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"),
            "JXLPHOTO_SETTINGS": "", "JXLPHOTO_JXL_THUMB": "0",
            "JXLPHOTO_NO_UPDATE_CHECK": "1"}


def _run(tmp_path, script, *args, hide_jxlinfo=False):
    cmd = [sys.executable]
    if hide_jxlinfo:
        launcher = tmp_path / "no_jxlinfo.py"
        launcher.write_text(_LAUNCHER, encoding="utf-8")
        cmd.append(str(launcher))
    cmd += [str(SCRIPTS / script), *map(str, args)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, env=_env(tmp_path),
                       stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout + r.stderr


def _folder(tmp_path, name):
    d = tmp_path / HOSTILE / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _only(folder, pattern, but=None):
    found = sorted(p for p in Path(folder).rglob(pattern) if p != but)
    assert len(found) == 1, (pattern, found)
    return found[0]


def _exiftool(args, path):
    path = Path(path)
    return subprocess.run(["exiftool", *args, path.name], cwd=str(path.parent),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def _tags(path, *names):
    r = _exiftool(["-j", "-n", "-G1", *[f"-{n}" for n in names]], path)
    return json.loads(r.stdout)[0] if r.stdout.strip() else {}


def _orientation(path):
    t = _tags(path, "IFD0:Orientation", "XMP-tiff:Orientation")
    return t.get("IFD0:Orientation"), t.get("XMP-tiff:Orientation")


def _tokens(path):
    rel = _tags(path, "XMP-dc:Relation").get("XMP-dc:Relation")
    rel = [] if rel is None else (rel if isinstance(rel, list) else [rel])
    return [str(t) for t in rel if str(t).startswith(TOKEN)]


def _size(path):
    with Image.open(path) as im:
        return im.size


def _corner_jpeg(path, orientation=6, interop=None):
    """A 300x200 JPEG, red block at the stored top-left, a camera-like EXIF
    with this Orientation (and an InteropIndex when asked)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.zeros((200, 300, 3), np.uint8)
    arr[:, :] = (30, 60, 200)
    arr[:40, :60] = (255, 0, 0)
    Image.fromarray(arr).save(path, quality=95, subsampling=0)
    args = ["-overwrite_original", "-n", f"-IFD0:Orientation={orientation}",
            "-Make=Nikon", "-Model=Z 7", "-DateTimeOriginal=2026:01:01 10:00:00"]
    if interop:
        args += [f"-InteropIFD:InteropIndex={interop}", "-InteropIFD:InteropVersion=0100"]
    r = _exiftool(args, path)
    assert r.returncode == 0, r.stdout + r.stderr
    return path


def _jxl_of(tmp_path, name, orientation=6, interop=None):
    """The transcoder's lossless JXL of a rotated JPEG, in a folder of its own."""
    d = _folder(tmp_path, name)
    _corner_jpeg(d / "DSC_0001 (2).jpg", orientation, interop)
    rc, out = _run(tmp_path, "jxl_jpeg_transcoder.py", d, "--force-transcode", "--mode", "1")
    assert rc == 0, out
    jxl = _only(d, "*.jxl")
    moved = _folder(tmp_path, name + "_jxl") / jxl.name
    shutil.move(str(jxl), str(moved))
    return moved


def _decode(tmp_path, jxl, fmt, *extra, hide_jxlinfo=False):
    rc, out = _run(tmp_path, "jxl_jpeg_transcoder.py", jxl.parent, "--force-convert",
                   "--decode", "--format", fmt, "--mode", "1", *extra,
                   hide_jxlinfo=hide_jxlinfo)
    assert rc == 0, out
    return _only(jxl.parent, "*.png" if fmt == "png" else "*.jpg"), out


def _red_at(path, x, y):
    with Image.open(path) as im:
        r, g, b = im.convert("RGB").getpixel((x, y))
    return r > 180 and g < 90 and b < 90


_MODS = {}


def _script_module(name):
    if name not in _MODS:
        sys.path.insert(0, str(SCRIPTS))
        try:
            spec = importlib.util.spec_from_file_location(
                f"sf261110_{name}", str(SCRIPTS / f"{name}.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        finally:
            sys.path.remove(str(SCRIPTS))
        _MODS[name] = mod
    return _MODS[name]


def _jxlinfo_orientation(path):
    """jxlinfo's answer, run from the file's folder by name."""
    path = Path(path)
    r = subprocess.run(["jxlinfo", "-v", path.name], cwd=str(path.parent),
                       capture_output=True, text=True, errors="replace")
    for line in r.stdout.splitlines():
        if line.startswith("Orientation:"):
            return int(line.split()[1])
    return None


def _box_types(path):
    data, i, types = Path(path).read_bytes(), 0, []
    while i + 8 <= len(data):
        size = struct.unpack(">I", data[i:i + 4])[0] or len(data) - i
        types.append(data[i + 4:i + 8])
        i += size
    return types


@pytest.fixture(scope="module")
def jxl_zoo(tmp_path_factory):
    """JXLs of every shape the header reader meets, in a hostile folder:
    jbrd and pixel re-encodes of JPEGs with Orientation 1-8, a bare
    codestream, a large lossless one, a toolkit master (thumbnail uuid box
    before the codestream) and a codestream split into jxlp boxes with the
    cut INSIDE the header."""
    if not all(shutil.which(t) for t in _TOOLS):
        pytest.skip("needs the codecs")
    tmp = tmp_path_factory.mktemp("zoo")
    plain = tmp / "plain"                    # cjxl by hand: an ASCII folder
    plain.mkdir()
    zoo = tmp / HOSTILE / "zoo [x] 5%"
    zoo.mkdir(parents=True)
    for n in range(1, 9):
        _corner_jpeg(plain / f"o{n}.jpg", n)
        for kind, extra in (("jbrd", ["--lossless_jpeg=1"]),
                            ("lossy", ["-d", "1", "--lossless_jpeg=0"])):
            subprocess.run(["cjxl", str(plain / f"o{n}.jpg"), str(plain / f"{kind}{n}.jxl"),
                            "--quiet", *extra], check=True, capture_output=True)
    rng = np.random.default_rng(3)
    Image.fromarray((rng.random((600, 4100, 3)) * 255).astype(np.uint8)).save(plain / "wide.png")
    subprocess.run(["cjxl", str(plain / "wide.png"), str(plain / "bare.jxl"), "-d", "2",
                    "--quiet"], check=True, capture_output=True)
    subprocess.run(["cjxl", str(plain / "wide.png"), str(plain / "big.jxl"), "-d", "0",
                    "-e", "1", "--quiet"], check=True, capture_output=True)
    subprocess.run(["cjxl", str(plain / "wide.png"), str(plain / "boxed.jxl"), "-d", "2",
                    "--container=1", "--quiet"], check=True, capture_output=True)
    assert b"jxlc" in _box_types(plain / "boxed.jxl"), "fixture: no jxlc box"
    # (cjxl's own JXLs of JPEGs already split the codestream into jxlp boxes,
    # the first one 6 bytes long.) jxlp by hand: the jxlc codestream re-boxed
    # in two jxlp boxes, cut 5 bytes in, inside the SizeHeader.
    data = (plain / "boxed.jxl").read_bytes()
    i, boxes = 0, []
    while i < len(data):
        size = struct.unpack(">I", data[i:i + 4])[0] or len(data) - i
        boxes.append((data[i + 4:i + 8], data[i:i + size]))
        i += size
    out = b""
    for typ, box in boxes:
        if typ == b"jxlc":
            cs = box[8:]
            out += (struct.pack(">I", 12 + 5) + b"jxlp" + struct.pack(">I", 0) + cs[:5]
                    + struct.pack(">I", 12 + len(cs) - 5) + b"jxlp"
                    + struct.pack(">I", 0x80000001) + cs[5:])
        else:
            out += box
    (plain / "split.jxl").write_bytes(out)
    assert _box_types(plain / "split.jxl").count(b"jxlp") == 2
    r = subprocess.run(["djxl", str(plain / "split.jxl"), str(plain / "split.png")],
                       capture_output=True, text=True)
    assert r.returncode == 0, "fixture: the jxlp split does not decode: " + r.stderr
    for p in plain.glob("*.jxl"):
        shutil.copy2(p, zoo / f"{p.stem} (2).jxl")
    # A toolkit master: a lossless TIFF -> JXL carries the thumbnail box.
    t = zoo / "master"
    t.mkdir()
    y, x = np.mgrid[0:600, 0:1000]        # noisy: the thumbnail is worth its box
    base = np.stack([x * 65 + 0, y * 109, np.full((600, 1000), 30000)], axis=-1)
    tif = np.clip(base + rng.integers(-9000, 9000, base.shape), 0, 65535).astype(np.uint16)
    tifffile.imwrite(str(t / "DSC_0001 (2).tif"), tif, photometric="rgb")
    r = subprocess.run([sys.executable, str(SCRIPTS / "jxl_tiff_encoder.py"), str(t), "--mode",
                        "1", "--distance", "0", "--effort", "3", "--no-preflight",
                        "--explorer-thumbnail", "on"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       env=_env(tmp), stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    master = _only(t, "*.jxl")
    assert b"uuid" in _box_types(master), "fixture: no thumbnail box"
    shutil.copy2(master, zoo / "master (2).jxl")
    return plain, zoo


# ------------------------------------------------------------------- #636
@needs_tools
@pytest.mark.parametrize("module", ORIENTED)
def test_636_header_orientation_matches_jxlinfo(jxl_zoo, module):
    plain, zoo = jxl_zoo
    m = _script_module(module)
    files = sorted(zoo.glob("*.jxl"))
    assert len(files) >= 20
    got = {p.name: m._header_orientation(p) for p in files}
    want = {}
    for p in files:
        ascii_copy = plain / p.name.replace(" (2)", "")
        want[p.name] = _jxlinfo_orientation(ascii_copy if ascii_copy.exists() else p)
    assert got == want
    assert [got[f"jbrd{n} (2).jxl"] for n in range(1, 9)] == list(range(1, 9))
    assert [got[f"lossy{n} (2).jxl"] for n in range(1, 9)] == list(range(1, 9))
    assert got["split (2).jxl"] == got["boxed (2).jxl"] == 1


@needs_tools
@pytest.mark.parametrize("module", ORIENTED)
def test_636_header_orientation_says_none_when_it_cannot_tell(jxl_zoo, module, tmp_path):
    _plain, zoo = jxl_zoo
    m = _script_module(module)
    split = (zoo / "split (2).jxl").read_bytes()
    d = _folder(tmp_path, "junk")
    cases = {"empty.jxl": b"", "text.jxl": b"not a jxl at all",
             "jpeg.jxl": b"\xff\xd8\xff\xe0" + bytes(60),
             "cut.jxl": split[:split.index(b"jxlp") + 4 + 4 + 5]}   # first 5 bytes only
    for name, data in cases.items():
        (d / name).write_bytes(data)
        assert m._header_orientation(d / name) is None, name
    assert m._header_orientation(d / "missing.jxl") is None


@needs_tools
def test_636_without_jxlinfo_the_jpeg_comes_back_as_stored(tmp_path):
    jxl = _jxl_of(tmp_path, "nj")
    res, out = _decode(tmp_path, jxl, "jpeg", hide_jxlinfo=True)
    assert "jxlinfo not found" in out, "fixture: jxlinfo was not hidden"
    assert _size(res) == (300, 200), _size(res)
    assert _orientation(res)[0] == 6
    assert _red_at(res, 5, 10) and not _red_at(res, 290, 10)


@needs_tools
def test_636_without_jxlinfo_png_and_upright_jpeg_are_shown_once(tmp_path):
    jxl = _jxl_of(tmp_path, "np")
    res, _out = _decode(tmp_path, jxl, "png", hide_jxlinfo=True)
    assert _size(res) == (200, 300), _size(res)
    assert _orientation(res)[0] in (1, None), _orientation(res)
    jxl2 = _jxl_of(tmp_path, "nu")
    res2, _out = _decode(tmp_path, jxl2, "jpeg", "--jpeg-orientation", "upright",
                         hide_jxlinfo=True)
    assert _size(res2) == (200, 300), _size(res2)
    assert _orientation(res2)[0] == 1, _orientation(res2)


@needs_tools
def test_636_without_jxlinfo_the_decoders_tiff_is_shown_once(tmp_path):
    jxl = _jxl_of(tmp_path, "nt")
    rc, out = _run(tmp_path, "jxl_tiff_decoder.py", jxl.parent, "--mode", "1",
                   hide_jxlinfo=True)
    assert rc == 0, out
    tif = _only(jxl.parent, "*.tif")
    assert tifffile.imread(str(tif)).shape[:2] == (300, 200)
    assert _orientation(tif) == (1, None), _orientation(tif)


@needs_tools
def test_636_without_jxlinfo_the_recompressor_records_the_orientation(tmp_path):
    jxl = _jxl_of(tmp_path, "nr")
    rc, out = _run(tmp_path, "jxl_recompressor.py", jxl.parent, "--mode", "1",
                   "--distance", "3", "--no-keep-smaller", "--jbrd-policy", "convert",
                   *RECOMPRESS, hide_jxlinfo=True)
    assert rc == 0, out
    res = _only(jxl.parent, "*.jxl", but=jxl)
    assert _tokens(res) == [TOKEN + "6"], _tokens(res)
    assert _orientation(res)[0] in (1, None), _orientation(res)


# ------------------------------------- the token read without exiftool (S2)
@needs_tools
def test_the_token_is_looked_for_in_the_xmp_box_before_exiftool(tmp_path, monkeypatch):
    m = _script_module("jxl_jpeg_transcoder")
    plain = tmp_path / "plain"
    plain.mkdir()
    Image.fromarray(np.full((40, 60, 3), 90, np.uint8)).save(plain / "a.png")
    subprocess.run(["cjxl", str(plain / "a.png"), str(plain / "a.jxl"), "-d", "1",
                    "--container=1", "--quiet"], check=True, capture_output=True)
    subprocess.run(["cjxl", str(plain / "a.png"), str(plain / "bare.jxl"), "-d", "1",
                    "--quiet"], check=True, capture_output=True)
    d = _folder(tmp_path, "xmp")
    for name, rel in (("none (2).jxl", None), ("title (2).jxl", "-XMP-dc:Title=T"),
                      ("token (2).jxl", "-XMP-dc:Relation+=" + TOKEN + "8")):
        shutil.copy2(plain / "a.jxl", d / name)
        if rel:
            r = _exiftool(["-overwrite_original", rel], d / name)
            assert r.returncode == 0, r.stdout + r.stderr
    shutil.copy2(plain / "bare.jxl", d / "bare (2).jxl")
    calls = []
    real = m._read_creator_and_relation

    def counted(p):
        calls.append(Path(p).name)
        return real(p)

    monkeypatch.setattr(m, "_read_creator_and_relation", counted)
    for name in ("none (2).jxl", "title (2).jxl", "bare (2).jxl"):
        assert m._stored_orientation(d / name) is None, name
    assert calls == [], calls
    assert m._stored_orientation(d / "token (2).jxl") == 8
    assert calls == ["token (2).jxl"], calls
    needle = TOKEN.encode()
    assert m._xmp_box_mentions(d / "token (2).jxl", needle) is True
    assert m._xmp_box_mentions(d / "title (2).jxl", needle) is False
    assert m._xmp_box_mentions(d / "bare (2).jxl", needle) is False


@needs_tools
def test_a_compressed_xmp_box_is_left_to_exiftool(tmp_path):
    m = _script_module("jxl_jpeg_transcoder")
    plain = tmp_path / "plain"
    plain.mkdir()
    _corner_jpeg(plain / "x.jpg", 1)
    r = _exiftool(["-overwrite_original", "-XMP-dc:Relation=" + TOKEN + "6"], plain / "x.jpg")
    assert r.returncode == 0, r.stdout + r.stderr
    subprocess.run(["cjxl", str(plain / "x.jpg"), str(plain / "x.jxl"), "--compress_boxes=1",
                    "--quiet"], check=True, capture_output=True)
    data = (plain / "x.jxl").read_bytes()
    assert b"brob" in data and b"xml " in data, "fixture: no brob XMP box"
    d = _folder(tmp_path, "brob")
    shutil.copy2(plain / "x.jxl", d / "x (2).jxl")
    assert m._xmp_box_mentions(d / "x (2).jxl", TOKEN.encode()) is None


# ------------------------------------------------------------------- #637
@needs_tools
@pytest.mark.parametrize("fmt", ["png", "jpeg"])
def test_637_interop_tags_reach_the_png_and_the_turned_back_jpeg(tmp_path, fmt):
    jxl = _jxl_of(tmp_path, "io_" + fmt, orientation=6, interop="R98")
    res, _out = _decode(tmp_path, jxl, fmt)
    t = _tags(res, "InteropIFD:InteropIndex", "InteropIFD:InteropVersion")
    assert t.get("InteropIFD:InteropIndex") == "R98", t
    assert str(t.get("InteropIFD:InteropVersion")) in ("0100", "100"), t


@needs_tools
def test_637_a_resized_derivative_keeps_them(tmp_path):
    jxl = _jxl_of(tmp_path, "io_der", orientation=1, interop="R98")
    res, _out = _decode(tmp_path, jxl, "jpeg", "--resize-long", "150")
    assert _size(res) == (150, 100)
    assert _tags(res, "InteropIFD:InteropIndex").get("InteropIFD:InteropIndex") == "R98"


@needs_tools
def test_637_control_a_colour_converted_output_does_not_keep_them(tmp_path):
    jxl = _jxl_of(tmp_path, "io_conv", orientation=1, interop="R03")
    res, _out = _decode(tmp_path, jxl, "jpeg", "--icc-profile", "AdobeRGB")
    assert "InteropIFD:InteropIndex" not in _tags(res, "InteropIFD:InteropIndex")


@needs_tools
def test_637_control_a_direct_jpeg_keeps_them(tmp_path):
    jxl = _jxl_of(tmp_path, "io_dir", orientation=1, interop="R98")
    res, _out = _decode(tmp_path, jxl, "jpeg")
    assert _tags(res, "InteropIFD:InteropIndex").get("InteropIFD:InteropIndex") == "R98"


# ------------------------------------------------------- ui_tour isolation
def test_ui_tour_runs_a_copy_of_the_scripts(tmp_path):
    out = tmp_path / HOSTILE / "tour"
    r = subprocess.run([sys.executable, str(REPO / "tools" / "ui_tour.py"), "--out", str(out),
                        "01_start_and_menu"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "01_start_and_menu" in r.stdout and "rc   0" in r.stdout, r.stdout
    for name in ("jxl_photo.py", "jxl_tiff_encoder.py", "jxl_tiff_decoder.py",
                 "jxl_jpeg_transcoder.py", "jxl_recompressor.py"):
        assert (out / "app" / name).read_bytes() == (REPO / name).read_bytes(), name
    assert (out / "app" / "manifests").is_dir()
    assert not list((out / "app" / "manifests").iterdir())
