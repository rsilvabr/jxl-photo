"""v2.27.0 (#640): the IPTC block of a TIFF reaches the master.

A JXL has no IPTC box and the encoder copied only `-exif:all` / `-xmp:all`, so a
field that lives ONLY in the IPTC (the capture date of the SilverFast scans:
no EXIF, no XMP) was dropped. Two layers:

1. the IPTC fields are copied into the master's XMP, only the ones the XMP does
   not hold (`IPTC_TO_XMP_FILL`, `_fill_xmp_from_iptc`, `-wm cg`); nothing goes
   into the EXIF (the IPTC has a date, not a time);
2. the IIM block rides in a `jxlphoto-tifftag:33723:7:<hex>` token and the
   decoder writes it back on page 0: the bytes come back identical.

Real codecs and exiftool; every file inside a folder named with Japanese,
brackets, a space, parentheses and %. A `%` in the path changes the ROUTE of
the IPTC copy (a small IPTC-only TIFF instead of the TIFF itself, see
_fill_xmp_from_iptc), so the tests that prove the copy run in both kinds of
folder (BOTH_ROUTES): with only the `%` folder the direct route, the one almost
every library takes, was never run.
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

import jxl_tiff_encoder as enc
from test_scan_preservation import _scan, TAG_ID, TAG_FILM

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "写真 [IPTC] 50%off (2)"
PLAIN = "写真 [IPTC] off (2)"          # same traps, no '%': the direct route
BOTH_ROUTES = pytest.mark.parametrize("folder", [HOSTILE, PLAIN],
                                      ids=["percent", "no-percent"])
_TOOLS = ("cjxl", "djxl", "exiftool")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")
REAL_PROVIA = Path(r"E:\TESTE\silverfast_xmp\provia_slide.tif")
REAL_SFPROFT = Path(r"E:\TESTE\TIFF_scans_varied\rgb16_sfproft_linear__0005.tif")


def _exif(tmp_path, *args):
    """exiftool through a UTF-8 argfile (the argfile itself in an ASCII place:
    its own path is on the command line, #553)."""
    af = tmp_path / "x.args"
    af.write_text(enc._ARGFILE_CHARSET + "\n".join(map(str, args)) + "\n", encoding="utf-8")
    r = subprocess.run(["exiftool", "-@", str(af)], capture_output=True)
    return r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def _write_iptc(tmp_path, tif, utf8=False, **fields):
    """Write IPTC fields into a TIFF (exiftool stores tag 33723 as LONG)."""
    args = ["-overwrite_original"] + (["-codedcharacterset=utf8"] if utf8 else [])
    for name, value in fields.items():
        name = name.replace("_", "-")
        for v in (value if isinstance(value, list) else [value]):
            args.append(f"-IPTC:{name}={v}")
    args.append(tif)
    _o, e = _exif(tmp_path, *args)
    assert "error" not in e.lower(), e


def _xmp_values(tmp_path, path, tag):
    out, _e = _exif(tmp_path, "-j", "-n", f"-{tag}", path)
    data = json.loads(out)[0]
    v = data.get(tag.split(":")[-1])
    return v if isinstance(v, list) else ([] if v is None else [v])


def _xmp_text(tmp_path, path):
    out, _e = _exif(tmp_path, "-b", "-XMP", path)
    return out


def _relation(tmp_path, path):
    return _xmp_values(tmp_path, path, "XMP-dc:Relation")


def _photo(seed=1, h=48, w=64):
    rng = np.random.default_rng(seed)
    return (rng.integers(0, 65535, (h, w, 3))).astype(np.uint16)


def _tif(path, byteorder=">", **kw):
    tifffile.imwrite(str(path), _photo(), photometric="rgb", byteorder=byteorder, **kw)
    return path


def _run(tmp_path, script, *args, ok=True):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs")}
    r = subprocess.run([sys.executable, str(REPO / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, env=env)
    if ok:
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return r.stdout + r.stderr


def _encode(tmp_path, d, *extra):
    return _run(tmp_path, "jxl_tiff_encoder.py", d, d / "jxl", "--distance", "0",
                "--effort", "3", "--no-preflight", *extra)


def _iim(path):
    """(dtype, byte order, raw bytes) of page 0's tag 33723, as stored."""
    with tifffile.TiffFile(path) as t:
        tag = t.pages[0].tags.get(33723)
        if tag is None:
            return None
        t.filehandle.seek(tag.valueoffset)
        return int(tag.dtype), t.byteorder, t.filehandle.read(int(tag.valuebytecount))


def _iptc_dump(tmp_path, path):
    out, _e = _exif(tmp_path, "-a", "-G1", "-s", "-IPTC:All", path)
    return out


def _folder(tmp_path, name=HOSTILE):
    d = tmp_path / name
    (d / "jxl").mkdir(parents=True)
    return d


# (IPTC field, XMP field, value written, value expected in the XMP)
PAIRS = [
    ("Keywords", "XMP-dc:Subject", ["k1", "k2 [x]"], ["k1", "k2 [x]"]),
    ("Caption-Abstract", "XMP-dc:Description", "cap 100%", ["cap 100%"]),
    ("By-line", "XMP-dc:Creator", "the author", ["the author"]),
    ("ObjectName", "XMP-dc:Title", "the title", ["the title"]),
    ("CopyrightNotice", "XMP-dc:Rights", "(c) 2019", ["(c) 2019"]),
    ("Headline", "XMP-photoshop:Headline", "headline", ["headline"]),
    ("City", "XMP-photoshop:City", "Kyoto", ["Kyoto"]),
    ("Province-State", "XMP-photoshop:State", "Kyoto-fu", ["Kyoto-fu"]),
    ("Country-PrimaryLocationName", "XMP-photoshop:Country", "Japan", ["Japan"]),
    ("Country-PrimaryLocationCode", "XMP-iptcCore:CountryCode", "JPN", ["JPN"]),
    ("Sub-location", "XMP-iptcCore:Location", "Gion", ["Gion"]),
    ("Credit", "XMP-photoshop:Credit", "credit", ["credit"]),
    ("Source", "XMP-photoshop:Source", "source", ["source"]),
    ("SpecialInstructions", "XMP-photoshop:Instructions", "instructions", ["instructions"]),
    ("DateCreated", "XMP-photoshop:DateCreated", "2019-05-08", ["2019-05-08"]),
]


def test_the_table_names_every_pair_once():
    assert [p[0] for p in PAIRS] == [i for i, _x in enc.IPTC_TO_XMP_FILL]
    assert [p[1] for p in PAIRS] == [x for _i, x in enc.IPTC_TO_XMP_FILL]


@needs_tools
@pytest.mark.parametrize("iptc,xmp,value,expected", PAIRS, ids=[p[0] for p in PAIRS])
def test_each_iptc_field_reaches_the_xmp(tmp_path, iptc, xmp, value, expected):
    """1. A TIFF with only that IPTC field and no XMP: the master has it."""
    d = _folder(tmp_path)
    src = _tif(d / "scan (1) [a].tif")
    _write_iptc(tmp_path, src, **{iptc: value})
    _encode(tmp_path, d)
    master = d / "jxl" / "scan (1) [a].jxl"
    if iptc == "DateCreated":
        assert "2019-05-08" in _xmp_text(tmp_path, master)
    else:
        assert _xmp_values(tmp_path, master, xmp) == expected


@needs_tools
@BOTH_ROUTES
def test_the_xmp_that_is_already_there_wins(tmp_path, folder):
    """2. Fill-only: a field (or a list) the XMP holds is never replaced."""
    d = _folder(tmp_path, folder)
    src = _tif(d / "scan (2).tif")
    _o, e = _exif(tmp_path, "-overwrite_original", "-XMP-dc:Description=A",
                  "-XMP-dc:Subject=x", "-XMP-photoshop:DateCreated=2020-01-01", src)
    _write_iptc(tmp_path, src, Caption_Abstract="B", Keywords=["k1", "k2"],
                DateCreated="2019-05-08", City="Kyoto")
    _encode(tmp_path, d)
    master = d / "jxl" / "scan (2).jxl"
    assert _xmp_values(tmp_path, master, "XMP-dc:Description") == ["A"]
    assert _xmp_values(tmp_path, master, "XMP-dc:Subject") == ["x"]
    text = _xmp_text(tmp_path, master)
    assert "2020-01-01" in text and "2019-05-08" not in text
    # ...while the field it lacks is filled.
    assert _xmp_values(tmp_path, master, "XMP-photoshop:City") == ["Kyoto"]


@needs_tools
@BOTH_ROUTES
def test_no_capture_time_is_invented(tmp_path, folder):
    """3. The IPTC has a date and no usable hour: nothing goes into the EXIF."""
    d = _folder(tmp_path, folder)
    src = _tif(d / "scan (3).tif")
    _write_iptc(tmp_path, src, DateCreated="2019-05-08", TimeCreated="12:34:56")
    _encode(tmp_path, d)
    master = d / "jxl" / "scan (3).jxl"
    out, _e = _exif(tmp_path, "-s3", "-EXIF:DateTimeOriginal", "-EXIF:CreateDate",
                    "-XMP-exif:DateTimeOriginal", "-XMP-xmp:CreateDate", master)
    assert out.strip() == "", out
    assert "2019-05-08" in _xmp_text(tmp_path, master)


@needs_tools
@BOTH_ROUTES
def test_japanese_caption_and_keyword_arrive_unchanged(tmp_path, folder):
    """4. An IIM marked UTF-8 (CodedCharacterSet): same text in the XMP."""
    d = _folder(tmp_path, folder)
    src = _tif(d / "菜の花 [1].tif")
    cap, kw = "吾妻山公園の菜の花 100%", "菜の花"
    _write_iptc(tmp_path, src, utf8=True, Caption_Abstract=cap, Keywords=[kw, "Canola"])
    _encode(tmp_path, d)
    master = d / "jxl" / "菜の花 [1].jxl"
    assert _xmp_values(tmp_path, master, "XMP-dc:Description") == [cap]
    assert _xmp_values(tmp_path, master, "XMP-dc:Subject") == [kw, "Canola"]


@needs_tools
def test_a_tiff_without_iptc_costs_no_exiftool_call(tmp_path, monkeypatch):
    """5. The decision is the tag of page 0: no IPTC, no call (zero extra
    exiftool spawns); with it, exactly one."""
    def count(with_iptc):
        d = tmp_path / ("with" if with_iptc else "without") / HOSTILE
        (d / "jxl").mkdir(parents=True)
        src = _tif(d / "scan (5).tif")
        if with_iptc:
            _write_iptc(tmp_path, src, DateCreated="2019-05-08")
        fills, calls = [], []     # calls: the exiftool argfile calls with -wm
        real_fill, real_run = enc._fill_xmp_from_iptc, enc._run_exiftool_argfile

        def fill(*a, **k):
            fills.append(a)
            return real_fill(*a, **k)

        def run(args, *a, **k):
            if "-wm" in [str(x) for x in args]:
                calls.append(args)
            return real_run(args, *a, **k)
        monkeypatch.setattr(enc, "_fill_xmp_from_iptc", fill)
        monkeypatch.setattr(enc, "_run_exiftool_argfile", run)
        monkeypatch.setattr(sys, "argv", [
            "jxl_tiff_encoder.py", str(d), str(d / "jxl"), "--mode", "0", "--distance", "0",
            "--effort", "3", "--no-preflight", "--workers", "1"])
        monkeypatch.setenv("JXLPHOTO_LOG_DIR", str(tmp_path / "logs"))
        try:
            enc.main()
        except SystemExit:
            pass
        monkeypatch.setattr(enc, "_fill_xmp_from_iptc", real_fill)
        monkeypatch.setattr(enc, "_run_exiftool_argfile", real_run)
        assert (d / "jxl" / "scan (5).jxl").exists()
        return len(fills), len(calls)
    assert count(False) == (0, 0)
    assert count(True) == (1, 1)


@needs_tools
@BOTH_ROUTES
@pytest.mark.parametrize("byteorder", [">", "<"], ids=["big-endian", "little-endian"])
def test_the_iim_block_comes_back_byte_for_byte(tmp_path, byteorder, folder):
    """6. TIFF (IIM stored as LONG, either byte order) -> JXL -> TIFF: the raw
    bytes of tag 33723 and exiftool's reading of them are the original's. The
    token says UNDEFINED; the decoded tag is UNDEFINED with the same payload."""
    d = _folder(tmp_path, folder)
    src = _tif(d / "scan (6).tif", byteorder=byteorder)
    _write_iptc(tmp_path, src, DateCreated="2019-05-08")
    before = _iim(src)
    assert before[0] == 4 and before[1] == byteorder and len(before[2]) % 4 == 0
    _encode(tmp_path, d)
    rel = _relation(tmp_path, d / "jxl" / "scan (6).jxl")
    assert any(t.startswith("jxlphoto-tifftag:33723:7:" + before[2].hex()) for t in rel), rel
    _run(tmp_path, "jxl_tiff_decoder.py", d / "jxl", d / "tif")
    out = d / "tif" / "scan (6).tif"
    after = _iim(out)
    assert after is not None and after[2] == before[2]
    assert after[0] == 7 and after[1] == byteorder
    assert _iptc_dump(tmp_path, out) == _iptc_dump(tmp_path, src)
    assert not any(t.startswith("jxlphoto-tifftag:")
                   for t in _relation(tmp_path, out))
    # Layer 1 on this route too: the date reached the master's XMP.
    assert "2019-05-08" in _xmp_text(tmp_path, d / "jxl" / "scan (6).jxl")


@needs_tools
def test_a_big_iim_is_left_out_alone_and_the_other_private_tags_stay(tmp_path):
    """7. Over PRIVATE_TAG_MAX_BYTES the IIM is not carried (the log says so,
    its fields still reach the XMP) and SilverFast's tags are still carried:
    the limit is the block's own, no longer all-or-nothing for the page."""
    d = _folder(tmp_path)
    src = d / "scan (7) [IR].tif"
    _scan(src)
    big = "a long caption " * 120
    _write_iptc(tmp_path, src, Caption_Abstract=big, DateCreated="2019-05-08",
                Keywords=[f"keyword number {i:03d} of the long list" for i in range(120)])
    size = len(_iim(src)[2])
    assert size > enc.PRIVATE_TAG_MAX_BYTES
    log = _encode(tmp_path, d)
    assert f"IPTC block is {size:,} bytes (limit {enc.PRIVATE_TAG_MAX_BYTES:,}): " \
           f"kept as XMP fields only" in log, log[-1500:]
    master = d / "jxl" / "scan (7) [IR].jxl"
    rel = _relation(tmp_path, master)
    assert not any(t.startswith("jxlphoto-tifftag:33723:") for t in rel)
    assert any(t.startswith("jxlphoto-tifftag:35243:1:" + TAG_ID.hex()) for t in rel), rel
    assert any(t.startswith("jxlphoto-tifftag:35244:1:") for t in rel)
    assert [v.strip() for v in _xmp_values(tmp_path, master, "XMP-dc:Description")] == [
        big.strip()]
    assert "2019-05-08" in _xmp_text(tmp_path, master)
    # ...and a TIFF whose IIM is too big to carry is still deletable (it was
    # before this change; its fields are in the XMP): no #620 veto.
    with tifffile.TiffFile(src) as t:
        assert enc._private_tiff_tags(t, t.pages[0])[1] is None


@needs_tools
def test_the_iim_survives_a_recompression(tmp_path):
    """8. master -> jxl_recompressor -> decoder: the block is still identical."""
    d = _folder(tmp_path)
    src = _tif(d / "scan (8).tif")
    _write_iptc(tmp_path, src, DateCreated="2019-05-08", Keywords=["a", "b"])
    before = _iim(src)
    _run(tmp_path, "jxl_tiff_encoder.py", d, d / "jxl", "--distance", "1", "--effort", "3",
         "--no-preflight")
    _run(tmp_path, "jxl_recompressor.py", d / "jxl", "--mode", "8", "--distance", "0.5",
         "--effort", "3", "--no-keep-smaller", "--on-downgrade", "convert",
         "--on-regeneration", "convert", "--on-unknown", "convert", "--no-preflight",
         "--delete-confirm-off")
    master = d / "jxl" / "scan (8).jxl"
    assert any(t.startswith("jxlphoto-tifftag:33723:7:") for t in _relation(tmp_path, master))
    assert "2019-05-08" in _xmp_text(tmp_path, master)
    _run(tmp_path, "jxl_tiff_decoder.py", d / "jxl", d / "tif")
    assert _iim(d / "tif" / "scan (8).tif")[2] == before[2]


@needs_tools
@BOTH_ROUTES
@pytest.mark.parametrize("real", [REAL_PROVIA, REAL_SFPROFT], ids=lambda p: p.stem)
def test_the_two_real_scans_keep_their_capture_date(tmp_path, real, folder):
    """9. SilverFast scans whose date is ONLY in the IPTC: the master has it in
    the XMP and the decoded TIFF has the identical IIM block."""
    if not real.exists():
        pytest.skip(f"{real} is not on this machine")
    d = _folder(tmp_path, folder)
    src = d / "real scan (9).tif"
    shutil.copyfile(real, src)
    before = _iim(src)
    assert before is not None
    _encode(tmp_path, d)
    master = d / "jxl" / "real scan (9).jxl"
    assert "2019-05-08" in _xmp_text(tmp_path, master)
    out_e, _e = _exif(tmp_path, "-s3", "-EXIF:DateTimeOriginal", master)
    assert out_e.strip() == ""
    _run(tmp_path, "jxl_tiff_decoder.py", d / "jxl", d / "tif")
    out = d / "tif" / "real scan (9).tif"
    assert _iim(out)[2] == before[2]
    assert _iptc_dump(tmp_path, out) == _iptc_dump(tmp_path, src)


@needs_tools
def test_strip_keeps_neither_layer(tmp_path):
    """--strip means no metadata: no XMP fill, no token."""
    d = _folder(tmp_path)
    src = _tif(d / "scan (10).tif")
    _write_iptc(tmp_path, src, DateCreated="2019-05-08")
    _encode(tmp_path, d, "--strip")
    master = d / "jxl" / "scan (10).jxl"
    assert "2019-05-08" not in _xmp_text(tmp_path, master)
    assert not any(t.startswith("jxlphoto-tifftag:") for t in _relation(tmp_path, master))


def test_private_tags_read_the_long_iptc_as_undefined(tmp_path):
    """The unit under 6: LONG in, type 7 and the stored bytes out; the other
    private tags are unaffected."""
    src = tmp_path / "u.tif"
    tifffile.imwrite(str(src), _photo(), photometric="rgb",
                     extratags=[(35243, 1, 8, TAG_ID, True)])
    if not shutil.which("exiftool"):
        pytest.skip("needs exiftool")
    _write_iptc(tmp_path, src, DateCreated="2019-05-08")
    with tifffile.TiffFile(src) as t:
        tags, why = enc._private_tiff_tags(t, t.pages[0])
        assert why is None
        assert [(c, ty) for c, ty, _r in tags] == [(33723, 7), (35243, 1)]
        assert enc._has_iptc(t) is True
        assert enc._iptc_left_out_note(t.pages[0]) is None


@needs_tools
@pytest.mark.parametrize("folder,copies", [(HOSTILE, 1), (PLAIN, 0)],
                         ids=["percent", "no-percent"])
def test_only_a_percent_path_takes_the_iptc_only_copy(tmp_path, monkeypatch, folder, copies):
    """The two routes of _fill_xmp_from_iptc: the TIFF itself is the
    -tagsFromFile source unless its path has a '%' (#550), and both leave the
    same XMP. Pins which route each folder of this file exercises."""
    d = _folder(tmp_path, folder)
    src = _tif(d / "菜の花 (11) [a].tif")
    cap = "吾妻山公園の菜の花"
    _write_iptc(tmp_path, src, utf8=True, DateCreated="2019-05-08", Caption_Abstract=cap)
    made = []
    real_only = enc._iptc_only_source

    def only(*a, **k):
        made.append(a)
        return real_only(*a, **k)
    monkeypatch.setattr(enc, "_iptc_only_source", only)
    monkeypatch.setattr(sys, "argv", [
        "jxl_tiff_encoder.py", str(d), str(d / "jxl"), "--mode", "0", "--distance", "0",
        "--effort", "3", "--no-preflight", "--workers", "1"])
    monkeypatch.setenv("JXLPHOTO_LOG_DIR", str(tmp_path / "logs"))
    try:
        enc.main()
    except SystemExit:
        pass
    master = d / "jxl" / "菜の花 (11) [a].jxl"
    assert master.exists()
    assert len(made) == copies, made
    assert _xmp_values(tmp_path, master, "XMP-dc:Description") == [cap]
    assert "2019-05-08" in _xmp_text(tmp_path, master)


def test_an_iptc_block_of_another_tiff_type_is_left_out_with_a_note(tmp_path):
    """A block stored as SHORT (not a byte stream here) is not carried as a
    token, and that is said: the caller logs _iptc_left_out_note."""
    src = tmp_path / "short.tif"
    tifffile.imwrite(str(src), _photo(), photometric="rgb",
                     extratags=[(33723, 3, 4, (0x021C, 0x0037, 0x0008, 0x3032), True)])
    with tifffile.TiffFile(src) as t:
        page = t.pages[0]
        assert int(page.tags[33723].dtype) == 3
        tags, why = enc._private_tiff_tags(t, page)
        assert tags == [] and why is None
        assert enc._iptc_left_out_note(page) == (
            "IPTC block is stored as TIFF type 3: kept as XMP fields only")


@needs_tools
@BOTH_ROUTES
def test_another_tag_too_large_does_not_cost_the_iptc_block(tmp_path, folder):
    """The other direction of 7: a page whose OWN private tag is too large to
    carry (Affinity Photo's document; found by the battery on a real scan)
    keeps none of the program's tags, and the TIFF stays undeletable (#620) --
    but the IPTC block is judged apart and still rides and comes back."""
    d = _folder(tmp_path, folder)
    src = d / "affinity (12) [a].tif"
    big = bytes(range(256)) * ((enc.PRIVATE_TAG_MAX_BYTES // 256) + 2)
    tifffile.imwrite(str(src), _photo(), photometric="rgb",
                     extratags=[(51168, 1, 8, TAG_ID, True),
                                (51169, 1, len(big), big, True)])
    _write_iptc(tmp_path, src, DateCreated="2019-05-08", ObjectName="kodakgold2000001")
    before = _iim(src)
    with tifffile.TiffFile(src) as t:
        tags, why = enc._private_tiff_tags(t, t.pages[0])
        assert [(c, ty) for c, ty, _r in tags] == [(33723, 7)]
        assert why == (f"tag 51169 is {len(big):,} bytes "
                       f"(limit {enc.PRIVATE_TAG_MAX_BYTES:,})")
    log = _encode(tmp_path, d)
    assert "its private TIFF tags are not kept" in " ".join(log.split()), log[-1500:]
    master = d / "jxl" / "affinity (12) [a].jxl"
    rel = _relation(tmp_path, master)
    assert [t[:25] for t in rel if t.startswith("jxlphoto-tifftag:")] == [
        "jxlphoto-tifftag:33723:7:"], rel
    assert "2019-05-08" in _xmp_text(tmp_path, master)
    _run(tmp_path, "jxl_tiff_decoder.py", d / "jxl", d / "tif")
    assert _iim(d / "tif" / "affinity (12) [a].tif")[2] == before[2]
    # ...and the delete gate still keeps such a TIFF (its own tags are not in
    # the JXL): --delete-source leaves it.
    out = _run(tmp_path, "jxl_tiff_encoder.py", d, d / "jxl", "--distance", "0", "--effort",
               "3", "--no-preflight", "--overwrite", "--delete-source",
               "--delete-confirm-off", ok=False)
    assert src.exists(), out[-1500:]
