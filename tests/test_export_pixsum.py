"""L2 (item 2 of plan 261127_Fable_plan_pending-code): every JPEG/PNG the
transcoder exports from a JXL carries `jxlphoto-pixsum:c1:<md5>`, the MD5 of
its coded image (_coded_image_sum: what an edit of the pixels changes and a
metadata write does not). A run about to write over an export whose image
data no longer matches its record refuses that file (an error), unless
--overwrite says to replace it — then a WARNING per file and a WARNING recap
at the end (the owner's answer to Q1). No record = judged as before.

Real cjxl/djxl/exiftool/magick; every path is hostile.

Pre-fix proof: extract the six scripts of HEAD into a folder, then
    set JXLPHOTO_SCRIPTS_UNDER_TEST=<that folder>
    python -m pytest tests/test_export_pixsum.py -q
"""

import hashlib
import importlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)
sys.path.insert(0, str(SCRIPTS))
tr = importlib.import_module("jxl_jpeg_transcoder")

HOSTILE = "250118_吾妻山公園 [FINAL] 100%off %d"
SUMMARY = "##JXLSUM## "
_TOOLS = ("cjxl", "djxl", "exiftool", "magick")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason="needs cjxl, djxl, exiftool and magick on PATH")
_SRGB = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "spool" / \
    "drivers" / "color" / "sRGB Color Space Profile.icm"
NAMES = ("DSC_0001 (2)", "吾妻山 [b] (2)")


def _script(name, *args):
    e = dict(os.environ, JXLPHOTO_SETTINGS="")
    return subprocess.run([sys.executable, str(SCRIPTS / name), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL, timeout=900, env=e)


def _tr(*args):
    r = _script("jxl_jpeg_transcoder.py", *args, "--workers", "1", "--summary-json")
    out = r.stdout + r.stderr
    summary = None
    for line in out.splitlines():
        if line.startswith(SUMMARY):
            summary = json.loads(line[len(SUMMARY):])
    return r.returncode, " ".join(out.split()), summary


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _relation(p) -> list:
    r = tr._run_exiftool_argfile(["-j", "-XMP-dc:Relation", str(p)], timeout=120)
    assert r.returncode == 0, r.stderr
    rel = json.loads(r.stdout)[0].get("Relation", [])
    return rel if isinstance(rel, list) else [rel]


def _pixsums(p) -> list:
    return [str(v)[len("jxlphoto-pixsum:"):] for v in _relation(p)
            if str(v).startswith("jxlphoto-pixsum:")]


@pytest.fixture(scope="module")
def master_set(tmp_path_factory):
    """Two 16-bit masters made by the real encoder (lossless, effort 1)."""
    if not all(shutil.which(t) for t in _TOOLS):
        pytest.skip("needs cjxl, djxl, exiftool and magick on PATH")
    np = pytest.importorskip("numpy")
    tifffile = pytest.importorskip("tifffile")
    base = tmp_path_factory.mktemp("pixsum") / HOSTILE
    src, masters = base / "TIFF", base / "JXL"
    src.mkdir(parents=True)
    y, x = np.mgrid[0:480, 0:640]
    rng = np.random.default_rng(768)
    extratags = []
    if _SRGB.exists():
        icc = _SRGB.read_bytes()
        extratags = [(34675, 7, len(icc), icc, True)]
    for i, name in enumerate(NAMES):
        img = np.stack([x * 100 + i * 700, y * 130, (x + y) * 50], axis=2).astype(np.int64)
        img = np.clip(img + rng.integers(0, 2000, img.shape), 0, 65535).astype(np.uint16)
        tifffile.imwrite(src / f"{name}.tif", img, photometric="rgb", extratags=extratags)
    r = _script("jxl_tiff_encoder.py", src, masters, "--mode", "0", "--distance", "0",
                "--effort", "1", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    assert all((masters / f"{n}.jxl").exists() for n in NAMES)
    return masters


@pytest.fixture
def work(master_set, tmp_path):
    root = tmp_path / HOSTILE
    masters = root / "masters"
    shutil.copytree(master_set, masters)
    return root, masters


def _retouch(path: Path, scratch: Path):
    """Another pixel, the file's own XMP (its marks) put back — as an editor
    that keeps the metadata would save it."""
    scratch.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()
    orig, edited = scratch / f"orig{ext}", scratch / f"edited{ext}"
    shutil.copyfile(path, orig)
    m = subprocess.run(["magick", str(orig), "-fill", "red", "-draw", "point 5,5", str(edited)],
                       capture_output=True, stdin=subprocess.DEVNULL)
    assert m.returncode == 0, m.stderr
    shutil.copyfile(edited, path)
    r = tr._run_exiftool_argfile(["-overwrite_original", "-tagsfromfile", str(orig),
                                  "-xmp:all", str(path)], timeout=120)
    assert r.returncode == 0, r.stderr
    rel = " ".join(_relation(path))
    assert "jxlphoto-src:" in rel or "jxlphoto-derived:" in rel, rel


def _newer(master: Path):
    st = master.stat()
    os.utime(master, (st.st_atime + 3600, st.st_mtime + 3600))


_EXPORTS = {
    "jpeg": (["--format", "jpeg", "--quality", "90"], ".jpg", False),
    "png16": (["--format", "png", "--bit-depth", "16"], ".png", False),
    "png8": (["--format", "png", "--bit-depth", "8"], ".png", False),
    "jpeg_resized": (["--format", "jpeg", "--quality", "90", "--resize-long", "320"],
                     ".jpg", True),
    "png16_srgb": (["--format", "png", "--bit-depth", "16", "--to-srgb"], ".png", True),
}


def _export(master: Path, out: Path, kind: str, *extra):
    args, ext, _der = _EXPORTS[kind]
    rc, text, summary = _tr(master, out, "--mode", "0", "--force-convert", "--decode",
                            *args, *extra)
    return rc, text, summary, out / f"{master.stem}{ext}"


# --- T2.1 ------------------------------------------------------------------------

@needs_tools
@pytest.mark.parametrize("kind", sorted(_EXPORTS))
def test_every_export_records_its_coded_image(work, kind):
    root, masters = work
    rc, text, _s, out = _export(masters / f"{NAMES[1]}.jxl", root / "out", kind)
    assert rc == 0, text[-3000:]
    sums = _pixsums(out)
    assert len(sums) == 1, (sums, _relation(out))
    assert re.fullmatch(r"c1:[0-9a-f]{32}", sums[0]), sums
    assert sums[0] == tr._coded_image_sum(out)
    assert tr._export_recorded_sum(out) == sums[0]
    rel = " ".join(_relation(out))
    if _EXPORTS[kind][2]:
        assert "jxlphoto-src:" not in rel and "jxlphoto-derived:" in rel, rel
    else:
        assert "jxlphoto-src:" in rel, rel


# --- T2.2 / Q1 ------------------------------------------------------------------------

@needs_tools
@pytest.mark.parametrize("kind", ["jpeg", "png16"])
def test_a_retouched_export_is_not_written_over_by_sync(work, kind, tmp_path):
    root, masters = work
    master = masters / f"{NAMES[0]}.jxl"
    rc, text, _s, out = _export(master, root / "out", kind)
    assert rc == 0, text[-3000:]
    _retouch(out, tmp_path / "scratch")
    _newer(master)
    before = _sha(out)
    rc, text, s = _export(master, root / "out", kind, "--sync")[:3]
    assert rc == 1, text[-3000:]
    assert "REFUSED" in text and "EDITED" in text, text[-3000:]
    assert _sha(out) == before
    assert s and s["errors"] == 1, s


@needs_tools
@pytest.mark.parametrize("kind", ["jpeg", "png16"])
def test_overwrite_replaces_an_edited_export_and_says_so(work, kind, tmp_path):
    """Q1 (the owner, 2026-11-27): --overwrite replaces it without asking,
    never silently — one WARNING when it is replaced and the recap at the
    end, both at WARNING (never ERROR)."""
    root, masters = work
    master = masters / f"{NAMES[1]}.jxl"
    rc, text, _s, out = _export(master, root / "out", kind)
    assert rc == 0, text[-3000:]
    _retouch(out, tmp_path / "scratch")
    before = _sha(out)
    r = _script("jxl_jpeg_transcoder.py", master, root / "out", "--mode", "0",
                "--force-convert", "--decode", *_EXPORTS[kind][0], "--overwrite",
                "--workers", "1")
    text = r.stdout + r.stderr
    assert r.returncode == 0, text[-3000:]
    assert _sha(out) != before
    assert _pixsums(out) == [tr._coded_image_sum(out)]
    lines = text.splitlines()
    per_file = [ln for ln in lines if f"replaced an edited export: {out.name} "
                "(its pixels were changed after it was written)" in ln]
    assert len(per_file) == 1 and "| WARNING |" in per_file[0], text[-3000:]
    i = next(i for i, ln in enumerate(lines) if "Edited exports replaced (1):" in ln)
    assert "| WARNING |" in lines[i] and out.name in lines[i + 1], lines[i:i + 2]
    assert "| WARNING |" in lines[i + 1]
    assert not any("| ERROR |" in ln and "edited export" in ln.lower() for ln in lines)


# --- T2.3 --------------------------------------------------------------------------------

@needs_tools
def test_a_recipe_change_keeps_a_retouched_derivative(work, tmp_path):
    root, masters = work
    out = root / "small"
    args = ["--mode", "0", "--force-convert", "--decode", "--format", "jpeg",
            "--resize-long", "320"]
    rc, text, _s = _tr(masters, out, *args, "--quality", "80")
    assert rc == 0, text[-3000:]
    edited, plain = out / f"{NAMES[0]}.jpg", out / f"{NAMES[1]}.jpg"
    _retouch(edited, tmp_path / "scratch")
    before = _sha(edited)
    rc, text, s = _tr(masters, out, *args, "--quality", "70")
    assert rc == 1, text[-3000:]
    assert _sha(edited) == before
    assert any("/q70" in v for v in _relation(plain)), _relation(plain)
    assert s and s["errors"] == 1, s


# --- T2.4 / T2.5 -----------------------------------------------------------------------------

@needs_tools
def test_a_metadata_edit_is_not_an_edit(work):
    root, masters = work
    master = masters / f"{NAMES[0]}.jxl"
    rc, text, _s, out = _export(master, root / "out", "jpeg")
    assert rc == 0, text[-3000:]
    r = tr._run_exiftool_argfile(["-overwrite_original", "-XMP-dc:Description=caption",
                                  "-IPTC:Keywords=k", str(out)], timeout=120)
    assert r.returncode == 0, r.stderr
    _newer(master)
    rc, text, _s = _export(master, root / "out", "jpeg", "--sync")[:3]
    assert rc == 0, text[-3000:]
    assert "RECONVERT" in text, text[-3000:]


@needs_tools
def test_an_export_without_a_record_is_judged_as_before(work, tmp_path):
    root, masters = work
    master = masters / f"{NAMES[0]}.jxl"
    rc, text, _s, out = _export(master, root / "out", "jpeg")
    assert rc == 0, text[-3000:]
    for v in _pixsums(out):
        r = tr._run_exiftool_argfile(["-overwrite_original",
                                      "-XMP-dc:Relation-=jxlphoto-pixsum:" + v, str(out)],
                                     timeout=120)
        assert r.returncode == 0, r.stderr
    assert _pixsums(out) == []
    _retouch(out, tmp_path / "scratch")
    _newer(master)
    before = _sha(out)
    rc, text, _s = _export(master, root / "out", "jpeg", "--sync")[:3]
    assert rc == 0, text[-3000:]
    assert _sha(out) != before


# --- T2.6 ------------------------------------------------------------------------------------

def _cut_after_sof(path: Path):
    data = path.read_bytes()
    i = 2
    while i + 4 <= len(data):
        marker = data[i + 1]
        size = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            path.write_bytes(data[:i + 2 + size])
            return
        i += 2 + size
    raise AssertionError("no SOF segment")


@needs_tools
def test_a_record_that_cannot_be_compared_refuses(work):
    root, masters = work
    master = masters / f"{NAMES[1]}.jxl"
    rc, text, _s, out = _export(master, root / "out", "jpeg")
    assert rc == 0, text[-3000:]
    _cut_after_sof(out)
    _newer(master)
    before = _sha(out)
    rc, text, _s = _export(master, root / "out", "jpeg", "--sync")[:3]
    assert rc == 1, text[-3000:]
    assert _sha(out) == before
    assert ("could not be read to compare" in text or "could not be read" in text
            or "REFUSED" in text or "refus" in text.lower()), text[-3000:]


@needs_tools
def test_overwrite_of_an_export_that_cannot_be_compared_says_so(work):
    """S1 (review 261127): with --overwrite, an export whose record cannot be
    compared (T2.6's cut JPEG) is replaced with a WARNING of its own words —
    never silently, never as 'an edited export' (it may not be one). Since
    the e2e test's S2 a cut file is said to be DAMAGED."""
    root, masters = work
    master = masters / f"{NAMES[1]}.jxl"
    rc, text, _s, out = _export(master, root / "out", "jpeg")
    assert rc == 0, text[-3000:]
    _cut_after_sof(out)
    before = _sha(out)
    r = _script("jxl_jpeg_transcoder.py", master, root / "out", "--mode", "0",
                "--force-convert", "--decode", *_EXPORTS["jpeg"][0], "--overwrite",
                "--workers", "1")
    text = r.stdout + r.stderr
    assert r.returncode == 0, text[-3000:]
    assert _sha(out) != before
    lines = text.splitlines()
    said = [ln for ln in lines if f"replaced a damaged export: {out.name}" in ln]
    assert len(said) == 1 and "| WARNING |" in said[0], text[-3000:]
    assert not any("replaced an edited export" in ln for ln in lines), text[-3000:]
    assert not any("Edited exports replaced" in ln for ln in lines), text[-3000:]


def _cut_in_scan(path: Path):
    """Cut in the middle of the image data: the XMP (and its record) stay."""
    data = path.read_bytes()
    path.write_bytes(data[:len(data) - (len(data) // 4)])


@needs_tools
@pytest.mark.parametrize("cut", ["in_scan", "after_sof"])
def test_a_damaged_export_is_damaged_not_edited(work, cut):
    """e2e S2 (261127): a truncated export that still carries its record is
    refused on a plain --sync as DAMAGED (ERROR naming --overwrite), never as
    EDITED; --overwrite replaces it with its own WARNING, outside the recap
    of edited exports."""
    root, masters = work
    master = masters / f"{NAMES[0]}.jxl"
    rc, text, _s, out = _export(master, root / "out", "jpeg")
    assert rc == 0, text[-3000:]
    (_cut_in_scan if cut == "in_scan" else _cut_after_sof)(out)
    assert tr._export_edited(out) == tr._EXPORT_DAMAGED_WHY
    _newer(master)
    before = _sha(out)
    rc, text, s = _export(master, root / "out", "jpeg", "--sync")[:3]
    assert rc == 1, text[-3000:]
    assert _sha(out) == before
    assert "is DAMAGED" in text and "--overwrite" in text, text[-3000:]
    assert "was EDITED" not in text, text[-3000:]
    assert s and s["errors"] == 1, s
    r = _script("jxl_jpeg_transcoder.py", master, root / "out", "--mode", "0",
                "--force-convert", "--decode", *_EXPORTS["jpeg"][0], "--overwrite",
                "--workers", "1")
    text = r.stdout + r.stderr
    assert r.returncode == 0, text[-3000:]
    lines = text.splitlines()
    said = [ln for ln in lines if f"replaced a damaged export: {out.name}" in ln]
    assert len(said) == 1 and "| WARNING |" in said[0], text[-3000:]
    assert not any("Edited exports replaced" in ln for ln in lines), text[-3000:]
    assert _pixsums(out) == [tr._coded_image_sum(out)]


@needs_tools
def test_the_dry_run_agrees_with_the_real_run_on_a_jpeg_cut_before_its_scan(work):
    """e2e S1 (261127): a JPEG cut right after its SOF still carries the
    record the walk read: the dry run (no ExifTool) says `would REFUSE`, as
    the real run refuses — it used to say `would be converted`."""
    root, masters = work
    master = masters / f"{NAMES[1]}.jxl"
    rc, text, _s, out = _export(master, root / "out", "jpeg")
    assert rc == 0, text[-3000:]
    recorded = tr._export_recorded_sum(out)
    _cut_after_sof(out)
    assert tr._export_recorded_sum(out, ask_exiftool=False) == recorded
    _newer(master)
    before = _sha(out)
    rc, text, s = _export(master, root / "out", "jpeg", "--sync", "--dry-run")[:3]
    assert f"would REFUSE | {master.name}" in text, text[-3000:]
    assert s and s["errors"] == 1 and s["ok"] == 0, s
    assert _sha(out) == before


@needs_tools
def test_overwrite_dry_run_names_the_edited_exports_it_would_replace(work, tmp_path):
    """e2e S3 (261127): `--overwrite --dry-run` names each edited export the
    real run will replace (Q1: never silent), once, at WARNING; the counts
    are the real run's (it is converted)."""
    root, masters = work
    master = masters / f"{NAMES[0]}.jxl"
    rc, text, _s, out = _export(master, root / "out", "jpeg")
    assert rc == 0, text[-3000:]
    _retouch(out, tmp_path / "scratch")
    before = _sha(out)
    r = _script("jxl_jpeg_transcoder.py", master, root / "out", "--mode", "0",
                "--force-convert", "--decode", *_EXPORTS["jpeg"][0], "--overwrite",
                "--dry-run", "--workers", "1")
    text = r.stdout + r.stderr
    said = [ln for ln in text.splitlines()
            if f"DRY | would REPLACE an edited export | {out.name}" in ln]
    assert len(said) == 1 and "| WARNING |" in said[0], text[-3000:]
    assert "would REFUSE" not in text, text[-3000:]
    assert _sha(out) == before


def test_main_clears_the_edited_recap_of_the_run_before(tmp_path):
    """S2 (review 261127): a second main() in the same process never names
    the first run's replaced exports again."""
    tr._EDITED_REPLACED.append("from a run before.jpg")
    empty = tmp_path / HOSTILE / "empty"
    empty.mkdir(parents=True)
    old_argv = sys.argv
    sys.argv = ["jxl_jpeg_transcoder.py", str(empty), "--mode", "0", "--dry-run"]
    try:
        try:
            tr.main()
        except SystemExit:
            pass
    finally:
        sys.argv = old_argv
    assert tr._EDITED_REPLACED == []


# --- T2.7 ------------------------------------------------------------------------------------

@needs_tools
def test_run_twice(work):
    root, masters = work
    master = masters / f"{NAMES[0]}.jxl"
    rc, text, _s, out = _export(master, root / "out", "png16")
    assert rc == 0, text[-3000:]
    before = _sha(out)
    rc, text, _s = _export(master, root / "out", "png16")[:3]
    assert rc == 0 and "SKIP" in text, text[-3000:]
    assert _sha(out) == before
    _newer(master)
    rc, text, _s = _export(master, root / "out", "png16", "--sync")[:3]
    assert rc == 0 and "RECONVERT" in text, text[-3000:]
    assert _pixsums(out) == [tr._coded_image_sum(out)]


# --- T2.8 ------------------------------------------------------------------------------------

@needs_tools
def test_the_dry_run_previews_the_refusal_and_runs_nothing(work, tmp_path):
    root, masters = work
    master = masters / f"{NAMES[0]}.jxl"
    rc, text, _s, out = _export(master, root / "out", "jpeg")
    assert rc == 0, text[-3000:]
    _retouch(out, tmp_path / "scratch")
    _newer(master)
    before = _sha(out)
    rc, text, s = _export(master, root / "out", "jpeg", "--sync", "--dry-run")[:3]
    assert "would REFUSE" in text, text[-3000:]
    assert "would be REFUSED, not overwritten" in text, text[-3000:]
    assert s and s["errors"] == 1 and s["ok"] == 0, s
    assert _sha(out) == before


# --- T2.9 ------------------------------------------------------------------------------------

@needs_tools
def test_an_export_fed_back_carries_no_stale_record(work):
    root, masters = work
    master = masters / f"{NAMES[0]}.jxl"
    rc, text, _s, jpg = _export(master, root / "out", "jpeg")
    assert rc == 0, text[-3000:]
    assert len(_pixsums(jpg)) == 1
    # (a) a lossy JPEG -> JXL drops the copied record
    lossy = root / "lossy"
    rc, text, _s = _tr(jpg, lossy, "--mode", "0", "--force-convert", "--distance", "1")
    assert rc == 0, text[-3000:]
    jxl_a = lossy / f"{jpg.stem}.jxl"
    assert jxl_a.exists() and _pixsums(jxl_a) == []
    # (b) the lossless archive keeps the JPEG's bytes: djxl rebuilds it bit for bit
    arch = root / "arch"
    rc, text, _s = _tr(jpg, arch, "--mode", "0", "--force-transcode")
    assert rc == 0, text[-3000:]
    rebuilt = root / "rebuilt.jpg"
    d = subprocess.run(["djxl", str(arch / f"{jpg.stem}.jxl"), str(rebuilt)],
                       capture_output=True, stdin=subprocess.DEVNULL, timeout=300)
    assert d.returncode == 0, d.stderr[-500:]
    assert rebuilt.read_bytes() == jpg.read_bytes()
    # (c) the JXL of (a) exported again: one record, its own
    again = root / "again"
    rc, text, _s = _tr(jxl_a, again, "--mode", "0", "--force-convert", "--decode",
                       "--format", "jpeg", "--quality", "90")
    assert rc == 0, text[-3000:]
    out_c = again / f"{jxl_a.stem}.jpg"
    assert _pixsums(out_c) == [tr._coded_image_sum(out_c)]


# --- T2.10: the sum itself (no codec) ---------------------------------------------------------

def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def _png(extra_before_idat: bytes = b"", flip_idat=False) -> bytes:
    raw = b"".join(b"\x00" + bytes((x * 7 + y) % 256 for x in range(8 * 3)) for y in range(8))
    idat = bytearray(zlib.compress(raw))
    if flip_idat:
        idat[len(idat) // 2] ^= 0xFF
    return (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
            + extra_before_idat + _png_chunk(b"IDAT", bytes(idat)) + _png_chunk(b"IEND", b""))


def _jpeg_bytes() -> bytes:
    from PIL import Image
    import io
    np = pytest.importorskip("numpy")
    arr = (np.arange(16 * 16 * 3) % 251).reshape(16, 16, 3).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def test_coded_image_sum_ignores_metadata_and_sees_the_image(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir(parents=True)

    def s(name, data):
        p = d / name
        p.write_bytes(data)
        return tr._coded_image_sum(p)

    jpg = _jpeg_bytes()
    base = s("a (1).jpg", jpg)
    assert base and base.startswith("c1:")
    com = b"\xff\xfe" + struct.pack(">H", 2 + 5) + b"hello"
    app1 = b"\xff\xe1" + struct.pack(">H", 2 + 8) + b"Exif\x00\x00ab"
    assert s("com (1).jpg", jpg[:2] + com + jpg[2:]) == base
    assert s("app1 (1).jpg", jpg[:2] + app1 + jpg[2:]) == base
    flipped = bytearray(jpg)
    flipped[-4] ^= 0x01                       # inside the scan, before EOI
    assert s("flip (1).jpg", bytes(flipped)) != base

    png = _png()
    pbase = s("a (1).png", png)
    assert pbase and pbase.startswith("c1:")
    text = _png_chunk(b"tEXt", b"Comment\x00hello")
    assert s("text (1).png", _png(text)) == pbase
    assert s("flip (1).png", _png(flip_idat=True)) != pbase
    assert s("cut (1).png", png[:-12]) is None          # no IEND
    assert s("zeros (1).jpg", b"\x00" * 64) is None
    assert s("empty (1).png", b"") is None
    assert tr._coded_image_sum(d / "missing (1).jpg") is None
    assert s("x (1).tif", b"II*\x00" + b"\x00" * 60) is None


# --- T2.12: the #747 gate is untouched ----------------------------------------------------------

@needs_tools
def test_a_skipped_png_export_with_a_matching_record_still_keeps_its_master(work):
    root, masters = work
    master = masters / f"{NAMES[0]}.jxl"
    args = [master, root / "out", "--mode", "0", "--force-convert", "--decode",
            "--format", "png", "--bit-depth", "16"]
    rc, text, _s = _tr(*args)
    assert rc == 0, text[-3000:]
    png = root / "out" / f"{master.stem}.png"
    assert _pixsums(png) == [tr._coded_image_sum(png)]
    rc, text, _s = _tr(*args, "--delete-source", "--delete-skipped", "--delete-confirm-off")
    assert master.exists(), text[-3000:]


def test_the_dry_run_check_runs_no_subprocess(tmp_path, monkeypatch):
    """A dry run runs no subprocess (test_round43_transcoder_fixes T-1): the
    L2 preview reads the record in Python only, even for a file the walk
    cannot read (a real run would ask ExifTool)."""
    calls = []
    monkeypatch.setattr(tr.subprocess, "run", lambda *a, **k: calls.append(a) or None)
    monkeypatch.setattr(tr.subprocess, "Popen", lambda *a, **k: calls.append(a) or None)
    d = tmp_path / HOSTILE
    d.mkdir(parents=True)
    jpg = _jpeg_bytes()
    xmp = (b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta><rdf:li>jxlphoto-pixsum:c1:"
           + b"0" * 32 + b"</rdf:li></x:xmpmeta>")
    app1 = b"\xff\xe1" + struct.pack(">H", 2 + len(xmp)) + xmp
    good = d / "edited (1).jpg"
    good.write_bytes(jpg[:2] + app1 + jpg[2:])
    cut = d / "cut (1).jpg"
    cut.write_bytes((jpg[:2] + app1 + jpg[2:])[:len(app1) + 10])
    src = d / "master (1).jxl"
    src.write_bytes(b"x")
    found = tr._dry_edited_exports([(src, good, True), (src, cut, True)], False)
    # The cut file's record was read before the walk stopped (e2e S1): it is
    # judged — DAMAGED — as the real run judges it.
    assert [(o.name, w) for _s, o, w in found] == [(good.name, tr._EXPORT_EDITED_WHY),
                                                    (cut.name, tr._EXPORT_DAMAGED_WHY)]
    assert tr._dry_edited_exports([(src, good, True)], True) == []
    assert calls == []


@pytest.mark.skipif(not shutil.which("exiftool"), reason="needs exiftool on PATH")
def test_the_dry_run_names_an_export_it_cannot_check_without_exiftool(tmp_path, caplog):
    """Review 3, S2: an export whose XMP has an extended segment (Photoshop
    writes one for a long history) carries a stale record. The real run asks
    ExifTool and refuses it; the dry run starts no ExifTool, so it cannot
    judge it — it names it once (INFO), changes no count, starts nothing.
    Pre-fix: the dry run was silent and counted it as "would convert"."""
    import logging
    d = tmp_path / HOSTILE
    d.mkdir(parents=True)
    f = d / "吾妻山 [b] (2).jpg"
    f.write_bytes(_jpeg_bytes())
    r = tr._run_exiftool_argfile(["-overwrite_original",
                                  "-XMP-dc:Relation=" + tr.PIXSUM_PREFIX + "c1:" + "0" * 32,
                                  str(f)], timeout=120)
    assert r.returncode == 0, r.stderr
    b = f.read_bytes()
    i = b.index(b"\xff\xe1")
    while not b[i + 4:].startswith(tr._XMP_APP1_HEADS[0]):
        i = b.index(b"\xff\xe1", i + 2)
    end = i + 2 + struct.unpack(">H", b[i + 2:i + 4])[0]
    body = b"<x:xmpmeta xmlns:x='adobe:ns:meta/'></x:xmpmeta>"
    payload = (tr._XMP_APP1_HEADS[1] + b"0123456789ABCDEF0123456789ABCDEF"
               + struct.pack(">II", len(body), 0) + body)
    f.write_bytes(b[:end] + b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload + b[end:])

    assert tr._export_recorded_sum(f, ask_exiftool=False) is False
    assert tr._export_edited(f) == tr._EXPORT_EDITED_WHY          # the real run refuses
    assert tr._export_edited(f, ask_exiftool=False) is None
    jxl = d / "吾妻山 [b] (2).jxl"                                   # never opened
    calls = []
    with pytest.MonkeyPatch.context() as mp, \
            caplog.at_level(logging.INFO, logger=tr.logger.name):
        mp.setattr(tr.subprocess, "run", lambda *a, **k: calls.append(a) or None)
        mp.setattr(tr.subprocess, "Popen", lambda *a, **k: calls.append(a) or None)
        found = tr._dry_edited_exports([(jxl, f, True)], False)
    assert found == []
    assert calls == []
    named = [rec.getMessage() for rec in caplog.records
             if "not checked for an edit" in rec.getMessage()]
    assert len(named) == 1 and f.name in named[0], named


def test_an_unreadable_export_is_not_said_to_have_a_record(tmp_path, monkeypatch):
    """e2e N1 (261127): a 0-byte file has no record anyone can see: the
    reason says it could not be read, a record "if it has one"."""
    d = tmp_path / HOSTILE
    d.mkdir(parents=True)
    empty = d / "c zero bytes (1).jpg"
    empty.write_bytes(b"")
    monkeypatch.setattr(tr, "_export_recorded_sum", lambda p, ask_exiftool=True: False)
    why = tr._export_edited(empty)
    assert why == "could not be read (a pixel record, if it has one, could not be checked)"
    assert tr._export_edited(empty, ask_exiftool=False) is None
