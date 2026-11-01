#!/usr/bin/env python3
"""v2.19.0: the original ICC copy lives in dc:Relation, not in CreatorTool.

Up to v2.18.x the encoder appended the original ICC profile, base64, to XMP
CreatorTool ("Capture One Windows | ICC:AAAD..."): every viewer that shows the
creating program printed the whole block. It is now a
"jxlphoto-iccprofile:<base64>" token in dc:Relation, with the other markers,
and CreatorTool keeps only the program name.

Files written before keep working: every reader takes the token first and the
CreatorTool copy second, and an output written FROM such a file (a
recompression, a derivative, a re-encode of a TIFF decoded with
--no-icc-cleanup) moves the copy to the token.

Real codecs and exiftool, every file under a folder named with Japanese,
brackets, a space and %. The profile is a table-curve ROMM encoded with the
encoder's "skip" strategy: the pixels are tagged sRGB in the codestream, so
the XMP copy is the ONLY place the real profile exists — a reader that misses
it decodes wrong colours, not just a different profile.

The end-to-end tests use only helpers that exist in v2.18.1 too, so they run
unchanged against the pre-change scripts (the regression proof).
"""
import base64
import os
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import numpy as np
import pytest
import tifffile

import jxl_jpeg_transcoder as tra
import jxl_recompressor as rec
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _icc_fixtures import romm_toe_icc  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "伊豆沼 [tmp] 100%"
TOKEN = "jxlphoto-iccprofile:"
CREATOR = "Capture One Windows"
_TOOLS = ("cjxl", "djxl", "exiftool", "magick")
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in _TOOLS),
                                 reason=f"needs {', '.join(_TOOLS)} on PATH")


# --------------------------------------------------------------------- helpers
def _run(tmp_path, script, *args):
    env = {**os.environ, "JXLPHOTO_LOG_DIR": str(tmp_path / "logs"),
           "JXLPHOTO_JXL_THUMB": "0"}
    r = subprocess.run([sys.executable, str(REPO / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=900, env=env,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, (r.stdout + r.stderr)[-6000:]
    return r.stdout + r.stderr


def _xmp_packet(creator: str) -> bytes:
    return ("<?xpacket begin='﻿' id='W5M0MpCehiHzreSzNTczkc9d'?>"
            "<x:xmpmeta xmlns:x='adobe:ns:meta/'>"
            "<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>"
            "<rdf:Description rdf:about='' xmlns:xmp='http://ns.adobe.com/xap/1.0/'>"
            f"<xmp:CreatorTool>{creator}</xmp:CreatorTool>"
            "</rdf:Description></rdf:RDF></x:xmpmeta>"
            "<?xpacket end='w'?>").encode("utf-8")


def _gradient_tiff(path: Path, icc: bytes):
    """16-bit RGB gradient (10-90 % of full scale), the profile in its ICC
    tag and a Capture One CreatorTool in its XMP."""
    lo, hi = 0.1, 0.9
    x = np.linspace(lo, hi, 128)
    yy, xx = np.meshgrid(x, x, indexing="ij")
    arr = np.stack([(xx * 65535).astype("uint16"),
                    (yy * 65535).astype("uint16"),
                    (((1 - xx) * (1 - yy) * (hi - lo) + lo) * 65535).astype("uint16")],
                   axis=2)
    xmp = _xmp_packet(CREATOR)
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), arr, photometric="rgb", metadata=None,
                     extratags=[(34675, "B", len(icc), icc, False),
                                (700, "B", len(xmp), xmp, False)])


def _meta(path: Path):
    """(CreatorTool, [Relation tokens]) — through the argfile helper, so a
    Japanese path reaches exiftool intact."""
    return rec._read_creator_and_relation(path)


def _icc_tokens(tokens) -> list:
    return [base64.b64decode(t[len(TOKEN):]) for t in tokens if t.startswith(TOKEN)]


def _single(paths, what):
    paths = sorted(paths)
    assert len(paths) == 1, f"expected one {what}, got {paths}"
    return paths[0]


def _event(tmp_path):
    return tmp_path / HOSTILE / "260101_写真 [FINAL] 5%"


def _make_master(tmp_path) -> tuple:
    """(source TIFF, master JXL): ROMM-toe, d=1, the encoder's 'skip'."""
    ev = _event(tmp_path)
    tif = ev / "TIFF16" / "DSC_0001 (2).tif"
    _gradient_tiff(tif, romm_toe_icc())
    out = ev / "16B_JXL"
    _run(tmp_path, "jxl_tiff_encoder.py", tif.parent, out, "--distance", "1",
         "--effort", "7", "--workers", "1", "--icc-png-strategy", "skip")
    return tif, _single(out.rglob("*.jxl"), "master")


def _restyle(jxl: Path, dst: Path, legacy: bool) -> Path:
    """A copy of `jxl` with the ICC copy in ONE known place, whatever wrote
    it: legacy=True -> v2.18.x form ("Capture One Windows | ICC:<b64>", no
    token); False -> v2.19.0 form (token, CreatorTool = program name)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(jxl, dst)
    creator, tokens = _meta(dst)
    icc = (_icc_tokens(tokens) or [rec._xmp_icc_from_creator_tool(creator)])[0]
    assert icc, "the master carries no ICC copy at all"
    b64 = base64.b64encode(icc).decode("ascii")
    lines = ["-overwrite_original"]
    lines += ["-XMP-dc:Relation-=" + t for t in tokens if t.startswith(TOKEN)]
    if legacy:
        lines.append(f"-XMP-xmp:CreatorTool={CREATOR} | ICC:{b64}")
    else:
        lines += [f"-XMP-xmp:CreatorTool={CREATOR}", "-XMP-dc:Relation+=" + TOKEN + b64]
    r = rec._run_exiftool_argfile(lines + [str(dst)], timeout=300)
    assert r.returncode == 0, r.stderr
    return dst


def _decode(tmp_path, jxl_dir: Path, out: Path, *extra) -> Path:
    _run(tmp_path, "jxl_tiff_decoder.py", jxl_dir, out, "--mode", "0",
         "--workers", "1", *extra)
    return _single(out.rglob("*.tif"), "decoded TIFF")


def _tiff_icc(tif: Path) -> bytes:
    with tifffile.TiffFile(str(tif)) as tf:
        tag = tf.pages[0].tags.get(34675)
        return bytes(tag.value) if tag is not None else b""


def _psnr(a: Path, b: Path) -> float:
    with tifffile.TiffFile(str(a)) as ta, tifffile.TiffFile(str(b)) as tb:
        xa = ta.pages[0].asarray().astype(np.float64)
        xb = tb.pages[0].asarray().astype(np.float64)
    assert xa.shape == xb.shape, (xa.shape, xb.shape)
    mse = float(((xa - xb) ** 2).mean())
    return 99.0 if mse == 0 else 10.0 * np.log10(65535.0 ** 2 / mse)


def _png_icc(png: Path) -> bytes:
    data = png.read_bytes()
    pos = 8
    while pos + 8 <= len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        if kind == b"iCCP":
            body = data[pos + 8:pos + 8 + n]
            return zlib.decompress(body[body.index(b"\0") + 2:])
        if kind == b"IDAT":
            break
        pos += 12 + n
    return b""


def _assert_clean_with_token(path: Path, icc: bytes):
    creator, tokens = _meta(path)
    assert creator == CREATOR, f"CreatorTool is {creator[:120]!r}"
    assert _icc_tokens(tokens) == [icc], (
        f"expected exactly one jxlphoto-iccprofile token with the profile, "
        f"got {[t[:40] for t in tokens]}")


# ------------------------------------------------------------------ encoder
@needs_tools
def test_encoder_writes_the_token_and_leaves_creator_tool_alone(tmp_path):
    _tif, jxl = _make_master(tmp_path)
    _assert_clean_with_token(jxl, romm_toe_icc())


# ------------------------------------------------------------------ decoder
@needs_tools
@pytest.mark.parametrize("legacy", [False, True], ids=["token", "v2.18-creatortool"])
def test_decoder_restores_the_profile_from_either_place(tmp_path, legacy):
    tif, jxl = _make_master(tmp_path)
    styled = _restyle(jxl, _event(tmp_path) / "styled" / jxl.name, legacy)
    out = _decode(tmp_path, styled.parent, _event(tmp_path) / "decoded")
    assert _tiff_icc(out) == romm_toe_icc(), "the TIFF lost the original profile"
    psnr = _psnr(tif, out)
    assert psnr >= 45.0, f"decoded colours are {psnr:.1f} dB off — wrong profile used"
    creator, tokens = _meta(out)
    assert creator == CREATOR, creator[:120]
    assert not _icc_tokens(tokens), "the JXL's ICC copy leaked into the TIFF"


@needs_tools
@pytest.mark.parametrize("legacy", [False, True], ids=["token", "v2.18-creatortool"])
def test_reencoding_a_decoded_tiff_keeps_one_clean_copy(tmp_path, legacy):
    """Feed the toolkit its own output: a TIFF decoded with --no-icc-cleanup
    still carries the JXL's copy; the master encoded from it carries exactly
    one token and a clean CreatorTool."""
    _tif, jxl = _make_master(tmp_path)
    styled = _restyle(jxl, _event(tmp_path) / "styled" / jxl.name, legacy)
    out = _decode(tmp_path, styled.parent, _event(tmp_path) / "decoded",
                  "--no-icc-cleanup")
    assert _tiff_icc(out) == romm_toe_icc(), "the decode missed the ICC copy"
    again = _event(tmp_path) / "again"
    _run(tmp_path, "jxl_tiff_encoder.py", out.parent, again, "--distance", "1",
         "--effort", "7", "--workers", "1", "--icc-png-strategy", "skip")
    _assert_clean_with_token(_single(again.rglob("*.jxl"), "re-encoded master"),
                             romm_toe_icc())


# ------------------------------------------------------------- recompressor
@needs_tools
@pytest.mark.parametrize("legacy", [False, True], ids=["token", "v2.18-creatortool"])
def test_recompressor_carries_the_copy_as_a_token(tmp_path, legacy):
    tif, jxl = _make_master(tmp_path)
    styled = _restyle(jxl, _event(tmp_path) / "styled" / jxl.name, legacy)
    out_dir = _event(tmp_path) / "recompressed"
    # --no-keep-smaller: on a fixture this small the re-encode is not smaller
    # and a verbatim copy (the source's bytes, old CreatorTool included)
    # would stand in for the path under test.
    log = _run(tmp_path, "jxl_recompressor.py", styled.parent, out_dir, "--mode", "2",
               "--workers", "1", "--distance", "2", "--effort", "7",
               "--on-downgrade", "convert", "--no-keep-smaller")
    assert "COPY" not in log, log[-3000:]
    out = _single(out_dir.rglob("*.jxl"), "recompressed JXL")
    _assert_clean_with_token(out, romm_toe_icc())
    dec_tif = _decode(tmp_path, out.parent, _event(tmp_path) / "decoded")
    assert _tiff_icc(dec_tif) == romm_toe_icc()
    assert _psnr(tif, dec_tif) >= 40.0


@needs_tools
@pytest.mark.parametrize("legacy", [False, True], ids=["token", "v2.18-creatortool"])
def test_recompressor_output_icc_carries_only_the_target(tmp_path, legacy):
    pytest.importorskip("PIL.ImageCms")
    _tif, jxl = _make_master(tmp_path)
    styled = _restyle(jxl, _event(tmp_path) / "styled" / jxl.name, legacy)
    out_dir = _event(tmp_path) / "srgb"
    _run(tmp_path, "jxl_recompressor.py", styled.parent, out_dir, "--mode", "2",
         "--workers", "1", "--distance", "2", "--effort", "7",
         "--on-downgrade", "convert", "--output-icc", "sRGB")
    out = _single(out_dir.rglob("*.jxl"), "derivative")
    creator, tokens = _meta(out)
    assert creator == CREATOR, f"CreatorTool is {creator[:120]!r}"
    icc = _icc_tokens(tokens)
    # Exactly one profile, the TARGET (the built sRGB profile carries its
    # creation time in the header, so compare what it is, not its bytes).
    assert len(icc) == 1, [t[:40] for t in tokens]
    import io
    from PIL import ImageCms
    desc = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc[0])))
    assert "srgb" in desc.lower(), desc


# --------------------------------------------------------------- transcoder
@needs_tools
@pytest.mark.parametrize("legacy", [False, True], ids=["token", "v2.18-creatortool"])
@pytest.mark.parametrize("recipe", [[], ["--resize-long", "64"]], ids=["plain", "resized"])
def test_transcoder_leaves_no_copy_in_the_png(tmp_path, legacy, recipe):
    """A plain decode and a derivative both deliver a PNG without the ICC
    copy; the derivative (which assigns the source profile explicitly) is in
    the master's profile, read from either place."""
    _tif, jxl = _make_master(tmp_path)
    styled = _restyle(jxl, _event(tmp_path) / "styled" / jxl.name, legacy)
    _run(tmp_path, "jxl_jpeg_transcoder.py", styled, "--decode", "--force-convert",
         "--mode", "1", "--format", "png", "--bit-depth", "16", "--workers", "1",
         *recipe)
    png = _single((styled.parent / "recovered_jpeg").glob("*.png"), "PNG")
    if recipe:
        assert b"ROMM toe" in _png_icc(png), "the PNG is not in the master's profile"
    creator, tokens = _meta(png)
    assert "ICC:" not in creator, creator[:120]
    assert not _icc_tokens(tokens), "the ICC copy was left in a delivered PNG"


# -------------------------------------------------------------------- units
def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@pytest.mark.parametrize("mod", [dec, tra, rec], ids=["decoder", "transcoder", "recompressor"])
def test_carried_icc_reads_the_token_first(mod):
    a = romm_toe_icc()
    b = rec._adobe_rgb_icc_bytes()
    assert mod._carried_icc(f"App | ICC:{_b64(b)}", [TOKEN + _b64(a)]) == a
    assert mod._carried_icc(f"App | ICC:{_b64(b)}", ["jxlphoto-depth:16"]) == b
    # an unreadable token falls back to the old place
    assert mod._carried_icc(f"ICC:{_b64(b)}", [TOKEN + "!!"]) == b
    assert mod._carried_icc("App", [TOKEN + _b64(b"x" * 200)]) is None
    assert mod._carried_icc("", []) is None


def test_the_token_never_collides_with_the_inherited_flag():
    assert not "jxlphoto-icc:inherited".startswith(TOKEN)
    assert not (TOKEN + "AAAA").startswith("jxlphoto-icc:")
    for mod in (enc, dec, tra, rec):
        assert mod.ICC_PROFILE_PREFIX == TOKEN


def test_encoder_drops_an_inherited_token_from_the_users_relation():
    assert (TOKEN + "AAAA").startswith(enc._INTERNAL_RELATION_PREFIXES)


def test_legacy_icc_lines():
    icc = romm_toe_icc()
    old = f"{CREATOR} | ICC:{_b64(icc)}"
    assert rec._legacy_icc_lines(CREATOR, []) == []
    assert rec._legacy_icc_lines("ICC:not-a-profile", []) == []
    assert rec._legacy_icc_lines(old, []) == [
        f"-XMP-xmp:CreatorTool={CREATOR}", "-XMP-dc:Relation+=" + TOKEN + _b64(icc)]
    # a token already there wins: only CreatorTool is cleaned
    assert rec._legacy_icc_lines(old, [TOKEN + "x"]) == [f"-XMP-xmp:CreatorTool={CREATOR}"]
    assert rec._legacy_icc_lines(old, [], migrate=False) == [f"-XMP-xmp:CreatorTool={CREATOR}"]
    # a bare blob leaves an empty CreatorTool (exiftool removes the tag)
    assert rec._legacy_icc_lines(f"ICC:{_b64(icc)}", [])[0] == "-XMP-xmp:CreatorTool="
