#!/usr/bin/env python3
"""`--output-icc`: colour-converted derivatives (recompressor).

The unit tests pin the profile generator, the source-profile detection and the
metadata rewrite. The real-codec tests run the whole djxl -> magick -> cjxl
pipeline against a synthetic ProPhoto-like master and check the pixels, the
container colour space and the provenance markers.
"""

import io
import json
import re
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_recompressor as rec

REPO = Path(__file__).resolve().parent.parent


def _jxl_stub(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

def test_adobe_rgb_profile_is_a_well_formed_rgb_icc():
    data = rec._adobe_rgb_icc_bytes()
    assert len(data) % 4 == 0
    assert struct.unpack(">I", data[0:4])[0] == len(data)
    assert data[36:40] == b"acsp"
    assert data[16:20] == b"RGB "


def test_adobe_rgb_profile_converts_like_the_reference():
    pytest.importorskip("PIL.ImageCms")
    from PIL import Image, ImageCms
    src = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    dst = ImageCms.ImageCmsProfile(io.BytesIO(rec._adobe_rgb_icc_bytes()))
    xform = ImageCms.buildTransform(src, dst, "RGB", "RGB", renderingIntent=1)
    img = Image.new("RGB", (4, 1))
    img.putdata([(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)])
    out = ImageCms.applyTransform(img, xform)
    assert [out.getpixel((i, 0)) for i in range(4)] == [
        (219, 0, 0), (144, 255, 60), (0, 0, 250), (255, 255, 255)]


def test_resolve_output_icc_aliases_and_files(tmp_path):
    assert rec._resolve_output_icc("srgb")[0] == "sRGB"
    assert rec._resolve_output_icc("ADOBERGB")[0] == "AdobeRGB"
    with pytest.raises(ValueError):
        rec._resolve_output_icc(str(tmp_path / "nao_existe.icc"))
    bad = tmp_path / "bad.icc"
    bad.write_bytes(b"\x00" * 200)
    with pytest.raises(ValueError):
        rec._resolve_output_icc(str(bad))
    cmyk = bytearray(rec._adobe_rgb_icc_bytes())
    cmyk[16:20] = b"CMYK"
    bad2 = tmp_path / "cmyk.icc"
    bad2.write_bytes(bytes(cmyk))
    with pytest.raises(ValueError):
        rec._resolve_output_icc(str(bad2))
    good = tmp_path / "good.icc"
    good.write_bytes(rec._adobe_rgb_icc_bytes())
    assert rec._resolve_output_icc(str(good))[0].startswith("icc-")


def _minimal_png() -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"iCCP", b"x\x00\x00" + zlib.compress(b"abc"))
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
            + chunk(b"IEND", b""))


def test_png_chunk_types_stops_before_idat(tmp_path):
    png = tmp_path / "a.png"
    png.write_bytes(_minimal_png())
    assert rec._png_chunk_types(png) == ["IHDR", "iCCP"]


def test_xmp_icc_from_creator_tool():
    import base64
    prof = rec._adobe_rgb_icc_bytes()
    b64 = base64.b64encode(prof).decode("ascii")
    assert rec._xmp_icc_from_creator_tool(f"Capture One Windows | ICC:{b64}") == prof
    assert rec._xmp_icc_from_creator_tool("ICC:lixo") is None
    assert rec._xmp_icc_from_creator_tool("Capture One Windows") is None


def test_creator_tool_without_icc_keeps_trailing_text():
    import base64
    b64 = base64.b64encode(rec._adobe_rgb_icc_bytes()).decode("ascii")
    assert rec._creator_tool_without_icc(f"Capture One Windows | ICC:{b64}") == \
        "Capture One Windows"
    assert rec._creator_tool_without_icc(f"ICC:{b64} | Real App") == "Real App"
    assert rec._creator_tool_without_icc("Capture One Windows") == "Capture One Windows"


def test_derivative_metadata_args_rewrite(tmp_path, monkeypatch):
    import base64
    prof = rec._adobe_rgb_icc_bytes()
    monkeypatch.setattr(rec, "_OUTPUT_ICC_LABEL", "sRGB")
    monkeypatch.setattr(rec, "_OUTPUT_ICC_BYTES", prof)
    monkeypatch.setattr(rec, "_DERIVED_LABEL", "sRGB")
    tokens = ["jxlphoto-depth:16", "jxlphoto-src:aa", "jxlphoto-srcsum:bb",
              "jxlphoto-icc:inherited", "jxlphoto-mpg:cc"]
    b64 = base64.b64encode(rec._adobe_rgb_icc_bytes()).decode("ascii")
    monkeypatch.setattr(rec, "_read_creator_and_relation",
                        lambda p: (f"App | ICC:{b64}", tokens))
    lines = rec._derivative_metadata_args(tmp_path / "a.jxl")
    joined = "\n".join(lines)
    assert "-XMP-dc:Relation=" in lines
    assert "-XMP-dc:Relation+=jxlphoto-depth:16" in lines
    assert "-XMP-dc:Relation+=jxlphoto-mpg:cc" in lines
    assert "-XMP-dc:Relation+=jxlphoto-derived:sRGB" in lines
    assert "jxlphoto-src:" not in joined
    assert "srcsum" not in joined
    assert "icc:inherited" not in joined
    ct = next(l for l in lines if l.startswith("-XMP-xmp:CreatorTool="))
    assert ct.startswith("-XMP-xmp:CreatorTool=App | ICC:")
    assert base64.b64decode(ct.split("ICC:", 1)[1]) == prof


def test_derivative_metadata_args_keeps_creator_tool_when_not_converted(tmp_path, monkeypatch):
    """The CreatorTool follows what _derive_pixels DID (converted=False for a
    grey image), not the jxlphoto-grayscale marker: a grey JXL without the
    marker was left unconverted but used to get the RGB target stamped in."""
    monkeypatch.setattr(rec, "_OUTPUT_ICC_LABEL", "sRGB")
    monkeypatch.setattr(rec, "_OUTPUT_ICC_BYTES", rec._adobe_rgb_icc_bytes())
    monkeypatch.setattr(rec, "_DERIVED_LABEL", "sRGB")
    for tokens in (["jxlphoto-grayscale"], []):          # with and WITHOUT the marker
        monkeypatch.setattr(rec, "_read_creator_and_relation",
                            lambda p, t=tokens: ("App", t))
        lines = rec._derivative_metadata_args(tmp_path / "a.jxl", converted=False)
        assert not any(l.startswith("-XMP-xmp:CreatorTool=") for l in lines), tokens
        assert "-XMP-dc:Relation+=jxlphoto-derived:sRGB" in lines
    # And a converted image gets the target even if it carries the marker.
    monkeypatch.setattr(rec, "_read_creator_and_relation",
                        lambda p: ("App", ["jxlphoto-grayscale"]))
    lines = rec._derivative_metadata_args(tmp_path / "a.jxl", converted=True)
    assert any(l.startswith("-XMP-xmp:CreatorTool=App | ICC:") for l in lines)


# ---------------------------------------------------------------------------
# CLI / planning
# ---------------------------------------------------------------------------

def test_cli_output_icc_with_delete_source_is_refused(tmp_path):
    _jxl_stub(tmp_path / "a.jxl")
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(tmp_path), "--mode", "1", "--output-icc", "sRGB",
                        "--delete-source", "--dry-run"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "--output-icc" in r.stderr, r.stdout + r.stderr


def test_cli_output_icc_with_verify_roundtrip_is_refused(tmp_path):
    _jxl_stub(tmp_path / "a.jxl")
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(tmp_path), "--mode", "1", "--output-icc", "sRGB",
                        "--verify-roundtrip", "--dry-run"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr


def test_cli_output_icc_mode8_is_refused(tmp_path):
    _jxl_stub(tmp_path / "a.jxl")
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(tmp_path), "--mode", "8", "--output-icc", "sRGB",
                        "--dry-run"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr


def test_cli_output_icc_missing_profile_is_refused(tmp_path):
    _jxl_stub(tmp_path / "a.jxl")
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(tmp_path), "--mode", "1",
                        "--output-icc", str(tmp_path / "nao_existe.icc"),
                        "--dry-run"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr


def test_cli_output_icc_in_place_single_file_is_refused(tmp_path):
    _jxl_stub(tmp_path / "a.jxl")
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(tmp_path / "a.jxl"), "--mode", "0",
                        "--output-icc", "sRGB"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr


def test_cli_output_icc_refuses_a_non_derivative_existing_output(tmp_path):
    _jxl_stub(tmp_path / "a.jxl")
    out = tmp_path / "recompressed_jxl" / "a.jxl"
    _jxl_stub(out)
    before = out.read_bytes()
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(tmp_path), "--mode", "1", "--output-icc", "sRGB",
                        "--dry-run"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "would REFUSE" in r.stdout, r.stdout + r.stderr
    assert out.read_bytes() == before


# ---------------------------------------------------------------------------
# Real codecs
# ---------------------------------------------------------------------------

_HAVE_TOOLS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool", "magick"))
real = pytest.mark.skipif(
    not _HAVE_TOOLS, reason="needs cjxl, djxl, exiftool and magick on PATH")


def _saturated_png(path: Path):
    import numpy as np
    import imagecodecs
    x = np.linspace(0, 1, 64)
    yy, xx = np.meshgrid(x, x, indexing="ij")
    arr = np.stack([(xx * 65535).astype("uint16"),
                    (yy * 65535).astype("uint16"),
                    ((1 - xx) * (1 - yy) * 65535).astype("uint16")], axis=2)
    path.write_bytes(imagecodecs.png_encode(arr))


def _srgb_icc_file(tmp_path: Path) -> Path:
    from PIL import ImageCms
    p = tmp_path / "srgb.icc"
    p.write_bytes(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    return p


def _make_master(tmp_path: Path, name: str = "master.jxl") -> Path:
    """A synthetic ProPhoto-like master with the encoder's markers."""
    import base64
    pp = tmp_path / "pp.icc"
    pp.write_bytes(rec._build_matrix_trc_icc(
        "test-prophoto", [(0.7347, 0.2653), (0.1596, 0.8404), (0.0366, 0.0001)],
        (0.3457, 0.3585), 1.8))
    raw = tmp_path / "raw.png"
    src = tmp_path / "src.png"
    _saturated_png(raw)
    subprocess.run(["magick", str(raw), "-profile", str(pp), str(src)],
                   check=True, capture_output=True)
    master = tmp_path / name
    subprocess.run(["cjxl", str(src), str(master), "-d", "0.05",
                    "--container=1", "-x", "strip=exif", "-x", "strip=xmp"],
                   check=True, capture_output=True)
    b64 = base64.b64encode(pp.read_bytes()).decode("ascii")
    subprocess.run(["exiftool", "-q", "-overwrite_original",
                    f"-XMP-xmp:CreatorTool=Test | ICC:{b64}",
                    "-XMP-dc:Description=gen=1 | cjxl d=0.05 e=7",
                    "-XMP-dc:Relation+=jxlphoto-src:1111",
                    "-XMP-dc:Relation+=jxlphoto-srcsum:2222",
                    "-XMP-dc:Relation+=jxlphoto-depth:16", str(master)],
                   check=True, capture_output=True)
    return master


def _run_derive(tmp_path: Path, target: str, extra=()):
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(tmp_path), "--mode", "1", "--distance", "1.0",
                        "--output-icc", target, "--workers", "1",
                        "--no-preflight", *extra],
                       capture_output=True, text=True, timeout=600,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def _exif_value(path: Path, tag: str):
    r = subprocess.run(["exiftool", "-j", "-s", "-s", tag, str(path)],
                       capture_output=True, text=True, timeout=60)
    entry = json.loads(r.stdout)[0]
    return entry.get(tag.split(":")[-1])


def _relation_tokens(path: Path) -> list:
    rel = _exif_value(path, "-XMP-dc:Relation")
    if rel is None:
        return []
    return [str(t).strip() for t in (rel if isinstance(rel, list) else [rel])]


def _psnr(a: Path, b: Path) -> float:
    r = subprocess.run(["magick", "compare", "-metric", "PSNR",
                        str(a), str(b), "null:"],
                       capture_output=True, text=True, timeout=120)
    m = re.search(r"([0-9.]+)", r.stderr)
    return float(m.group(1))


@real
def test_real_derive_to_srgb_pixels_and_markers(tmp_path):
    pytest.importorskip("PIL.ImageCms")
    master = _make_master(tmp_path)
    _run_derive(tmp_path, "sRGB")
    out = tmp_path / "recompressed_jxl" / "master.jxl"
    assert out.exists()

    info = subprocess.run(["jxlinfo", str(out)], capture_output=True, text=True,
                          timeout=120).stdout
    assert "Primaries: sRGB" in info, info
    assert "16-bit" in info, info

    tokens = _relation_tokens(out)
    assert "jxlphoto-derived:sRGB" in tokens
    assert not any(t.startswith("jxlphoto-src:") for t in tokens), tokens

    desc = _exif_value(out, "-XMP-dc:Description")
    assert desc.endswith("cjxl d=1.0 e=7"), desc
    assert "gen=2" in desc, desc

    from PIL import ImageCms
    ct = _exif_value(out, "-XMP-xmp:CreatorTool")
    icc = rec._xmp_icc_from_creator_tool(ct)
    assert icc, ct
    prof = ImageCms.ImageCmsProfile(io.BytesIO(icc))
    assert "srgb" in ImageCms.getProfileDescription(prof).lower()

    # Pixels: the derivative must match a direct conversion of the source PNG.
    srgb = _srgb_icc_file(tmp_path)
    ref = tmp_path / "ref.png"
    subprocess.run(["magick", str(tmp_path / "src.png"),
                    "-intent", "Relative", "-black-point-compensation",
                    "-profile", str(srgb), "-depth", "16", str(ref)],
                   check=True, capture_output=True)
    dec = tmp_path / "dec.png"
    subprocess.run(["djxl", str(out), str(dec), "--bits_per_sample=16"],
                   check=True, capture_output=True)
    assert _psnr(dec, ref) >= 30.0, "converted derivative lost the colours"

    raw_dec = tmp_path / "raw_dec.png"
    subprocess.run(["djxl", str(master), str(raw_dec), "--bits_per_sample=16"],
                   check=True, capture_output=True)
    assert _psnr(raw_dec, ref) < _psnr(dec, ref), \
        "the test cannot tell a conversion from an unconverted decode"


@real
def test_real_derive_to_adobergb_keeps_native_primaries(tmp_path):
    master = _make_master(tmp_path)
    _run_derive(tmp_path, "AdobeRGB")
    out = tmp_path / "recompressed_jxl" / "master.jxl"
    info = subprocess.run(["jxlinfo", str(out)], capture_output=True, text=True,
                          timeout=120).stdout
    assert "White point: D65" in info, info
    m = re.search(r"red\(x=([0-9.]+)", info)
    assert m, info
    assert abs(float(m.group(1)) - 0.64) < 0.001, info
    assert "jxlphoto-derived:AdobeRGB" in _relation_tokens(out)

    adobe = tmp_path / "adobe.icc"
    adobe.write_bytes(rec._adobe_rgb_icc_bytes())
    ref = tmp_path / "ref_adobe.png"
    subprocess.run(["magick", str(tmp_path / "src.png"),
                    "-intent", "Relative", "-black-point-compensation",
                    "-profile", str(adobe), "-depth", "16", str(ref)],
                   check=True, capture_output=True)
    dec = tmp_path / "dec_adobe.png"
    subprocess.run(["djxl", str(out), str(dec), "--bits_per_sample=16"],
                   check=True, capture_output=True)
    assert _psnr(dec, ref) >= 30.0, "converted derivative lost the colours"
    raw_dec = tmp_path / "raw_dec_adobe.png"
    subprocess.run(["djxl", str(master), str(raw_dec), "--bits_per_sample=16"],
                   check=True, capture_output=True)
    assert _psnr(raw_dec, ref) < _psnr(dec, ref), \
        "the test cannot tell a conversion from an unconverted decode"


@real
def test_real_derive_re_derives_when_the_target_changes(tmp_path):
    _make_master(tmp_path)
    _run_derive(tmp_path, "sRGB")
    out = tmp_path / "recompressed_jxl" / "master.jxl"
    assert "jxlphoto-derived:sRGB" in _relation_tokens(out)
    _run_derive(tmp_path, "AdobeRGB", extra=("--sync",))
    assert "jxlphoto-derived:AdobeRGB" in _relation_tokens(out)


@real
def test_real_derive_sync_skips_an_unchanged_target(tmp_path):
    _make_master(tmp_path)
    _run_derive(tmp_path, "sRGB")
    out = tmp_path / "recompressed_jxl" / "master.jxl"
    before = out.stat().st_mtime_ns
    r = _run_derive(tmp_path, "sRGB", extra=("--sync",))
    assert "SKIP" in r.stdout, r.stdout
    assert out.stat().st_mtime_ns == before


@real
def test_real_derive_with_rename_syncs_on_the_new_name(tmp_path):
    _make_master(tmp_path, name="m_ProPhoto.jxl")
    _run_derive(tmp_path, "sRGB",
                extra=("--rename-from", "ProPhoto", "--rename-to", "sRGB"))
    out = tmp_path / "recompressed_jxl" / "m_sRGB.jxl"
    assert out.exists(), list((tmp_path / "recompressed_jxl").iterdir())
    before = out.stat().st_mtime_ns
    r = _run_derive(tmp_path, "sRGB",
                    extra=("--sync", "--rename-from", "ProPhoto",
                           "--rename-to", "sRGB"))
    assert "SKIP" in r.stdout, r.stdout
    assert out.stat().st_mtime_ns == before


@real
def test_real_grey_jxl_without_marker_keeps_its_creator_tool(tmp_path):
    """A grey JXL not written by this toolkit (no jxlphoto-grayscale marker):
    the pixels are re-encoded unconverted, so the CreatorTool must NOT claim
    the RGB target profile."""
    pytest.importorskip("PIL.ImageCms")
    import numpy as np
    import imagecodecs
    grey = tmp_path / "grey.png"
    grey.write_bytes(imagecodecs.png_encode(
        (np.linspace(0, 65535, 64 * 64).reshape(64, 64)).astype("uint16")))
    master = tmp_path / "grey.jxl"
    subprocess.run(["cjxl", str(grey), str(master), "-d", "0.05",
                    "--container=1"], check=True, capture_output=True)
    subprocess.run(["exiftool", "-q", "-overwrite_original",
                    "-XMP-xmp:CreatorTool=Other App",
                    "-XMP-dc:Description=gen=1 | cjxl d=0.05 e=7", str(master)],
                   check=True, capture_output=True)
    r = _run_derive(tmp_path, "sRGB")
    assert "Grayscale image" in r.stdout + r.stderr
    out = tmp_path / "recompressed_jxl" / "grey.jxl"
    assert out.exists()
    assert _exif_value(out, "-XMP-xmp:CreatorTool") == "Other App"
    assert "jxlphoto-derived:sRGB" in _relation_tokens(out)

