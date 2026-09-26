#!/usr/bin/env python3
"""Lossy JXL with a whole ICC blob (a table-curve profile with no native JXL
form: ROMM with its linear toe, eciRGB v2, scanner LUTs).

djxl decodes such a file to LINEAR sRGB, so pasting/assigning the original
ICC on those pixels gives wrong colours (measured 2026-09-25: 15.4 dB). The
fix detects the case with djxl's own --icc_out vs --orig_icc_out (equal in
every correct case, different ONLY here) and decodes to a float PFM that is
then CONVERTED to the original profile (measured 51.9 dB — the native-path
figure). These tests run the real codecs end to end.
"""

import hashlib
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jxl_jpeg_transcoder as tr
import jxl_recompressor as rec
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc

from _icc_fixtures import grey_toe_icc, romm_toe_icc

REPO = Path(__file__).resolve().parent.parent

_HAVE_TOOLS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool", "magick"))
real = pytest.mark.skipif(
    not _HAVE_TOOLS, reason="needs cjxl, djxl, exiftool and magick on PATH")

_PROPHOTO_PRIMARIES = [(0.7347, 0.2653), (0.1596, 0.8404), (0.0366, 0.0001)]
_D50 = (0.3457, 0.3585)


def _elle_g22_icc() -> bytes:
    """ProPhoto primaries with a plain gamma-2.2 TRC: HAS a native JXL form,
    so a lossy cjxl encodes it natively and djxl returns the file's space."""
    return rec._build_matrix_trc_icc("Elle LargeRGB g2.2 (test)",
                                     _PROPHOTO_PRIMARIES, _D50, 2.2)


# ---------------------------------------------------------------------------
# Unit: the shared detector
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mod", [dec, rec, tr, enc],
                         ids=["decoder", "recompressor", "transcoder", "encoder"])
def test_decoded_in_original_space_unit(mod, tmp_path):
    a = tmp_path / "a.icc"
    b = tmp_path / "b.icc"
    a.write_bytes(b"x" * 200)
    b.write_bytes(b"x" * 200)
    assert mod._decoded_in_original_space(a, b) is True
    b.write_bytes(b"y" * 200)
    assert mod._decoded_in_original_space(a, b) is False
    assert mod._decoded_in_original_space(a, tmp_path / "missing.icc") is None


# ---------------------------------------------------------------------------
# Real-codec fixtures and helpers
# ---------------------------------------------------------------------------

def _gradient_tiff(path: Path, icc: bytes):
    """A 16-bit TIFF with a saturated gradient tagged with `icc`. The range is
    10–90% of full scale: the corners are still outside the sRGB gamut in
    ProPhoto space (so the float decode matters), but not pure primaries —
    those would cap the PSNR at ~40 dB through XYB codec loss alone (measured:
    bug path 14.1 dB, fixed path 52.4 dB at d=1 with this fixture)."""
    import tifffile
    lo, hi = 0.1, 0.9
    x = np.linspace(lo, hi, 128)
    yy, xx = np.meshgrid(x, x, indexing="ij")
    arr = np.stack([(xx * 65535).astype("uint16"),
                    (yy * 65535).astype("uint16"),
                    (((1 - xx) * (1 - yy) * (hi - lo) + lo) * 65535).astype("uint16")],
                   axis=2)
    tifffile.imwrite(str(path), arr, photometric="rgb", metadata=None,
                     extratags=[(34675, "B", len(icc), icc, False)])


def _run(script, *args, timeout=900):
    r = subprocess.run([sys.executable, str(REPO / script), *map(str, args)],
                       capture_output=True, text=True, timeout=timeout)
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def _encode(src_dir: Path, dst_dir: Path, strategy: str) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    _run("jxl_tiff_encoder.py", src_dir, dst_dir, "--distance", "1",
         "--effort", "7", "--workers", "1", "--icc-png-strategy", strategy)
    jxls = sorted(dst_dir.rglob("*.jxl"))
    assert len(jxls) == 1, f"expected one JXL in {dst_dir}"
    return jxls[0]


def _decode(jxl_dir: Path, dst_dir: Path) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    _run("jxl_tiff_decoder.py", jxl_dir, dst_dir, "--mode", "0", "--workers", "1")
    tifs = sorted(p for p in dst_dir.rglob("*.tif*"))
    assert len(tifs) == 1, f"expected one TIFF in {dst_dir}"
    return tifs[0]


def _blob_state(jxl: Path, work: Path):
    """True/False/None of the --icc_out vs --orig_icc_out comparison, computed
    WITHOUT the scripts' new helpers so this same probe works on pre-fix
    checkouts (the regression proof must fail on the assertions, not on an
    AttributeError)."""
    work.mkdir(parents=True, exist_ok=True)
    out_icc, orig_icc = work / "djxl_out.icc", work / "djxl_orig.icc"
    subprocess.run(["djxl", str(jxl), str(work / "probe.png"),
                    f"--icc_out={out_icc}", f"--orig_icc_out={orig_icc}"],
                   check=True, capture_output=True, timeout=300)
    if not out_icc.exists() or not orig_icc.exists():
        return None
    return out_icc.read_bytes() == orig_icc.read_bytes()


def _tiff_psnr(a: Path, b: Path) -> float:
    import tifffile
    xa = tifffile.imread(str(a)).astype(np.float64)
    xb = tifffile.imread(str(b)).astype(np.float64)
    assert xa.shape == xb.shape, (xa.shape, xb.shape)
    mse = float(((xa - xb) ** 2).mean())
    assert mse > 0, "identical pixels — the fixture is not exercising anything"
    return 10.0 * np.log10(65535.0 ** 2 / mse)


def _compare_psnr(a: Path, b: Path) -> float:
    r = subprocess.run(["magick", "compare", "-metric", "PSNR",
                        str(a), str(b), "null:"],
                       capture_output=True, text=True, timeout=300)
    m = re.search(r"([0-9.]+)", r.stderr)
    assert m, f"magick compare gave no PSNR: {r.stderr}"
    return float(m.group(1))


def _make_master(tmp_path: Path, name: str, icc: bytes, strategy: str):
    """(source TIFF, master JXL) encoded at d=1 with the given strategy."""
    src_dir = tmp_path / f"src_{name}"
    src_dir.mkdir()
    tif = src_dir / f"{name}.tif"
    _gradient_tiff(tif, icc)
    jxl = _encode(src_dir, tmp_path / f"jxl_{name}", strategy)
    return tif, jxl


# ---------------------------------------------------------------------------
# Decoder, roundtrip mode (the main bug)
# ---------------------------------------------------------------------------

@real
def test_blob_master_is_detected_and_decoded_correctly(tmp_path):
    tif, jxl = _make_master(tmp_path, "romm", romm_toe_icc(), "always")
    assert _blob_state(jxl, tmp_path / "probe") is False, (
        "the fixture did not produce a lossy ICC-blob file")
    out = _decode(jxl.parent, tmp_path / "decoded")
    psnr = _tiff_psnr(tif, out)
    assert psnr >= 45.0, (
        f"roundtrip of a lossy ICC-blob file came back at {psnr:.1f} dB — "
        f"the original ICC was pasted on linear-sRGB pixels (the bug)")


@real
def test_native_profile_control_unchanged(tmp_path):
    """Elle g2.2 has a native JXL form: djxl returns the file's own space and
    the roundtrip keeps working exactly as before."""
    tif, jxl = _make_master(tmp_path, "elle", _elle_g22_icc(), "always")
    assert _blob_state(jxl, tmp_path / "probe") is True
    out = _decode(jxl.parent, tmp_path / "decoded")
    assert _tiff_psnr(tif, out) >= 45.0


@real
def test_skip_strategy_control_unchanged(tmp_path):
    """An encoder 'skip' file (no ICC in the PNG) also decodes in the file's
    own space: pasting the XMP ICC is correct there."""
    tif, jxl = _make_master(tmp_path, "skip", romm_toe_icc(), "skip")
    assert _blob_state(jxl, tmp_path / "probe") is True
    out = _decode(jxl.parent, tmp_path / "decoded")
    assert _tiff_psnr(tif, out) >= 45.0


# ---------------------------------------------------------------------------
# Derivatives (recompressor / transcoder)
# ---------------------------------------------------------------------------

@real
def test_recompressor_output_icc_on_a_blob_master(tmp_path):
    pytest.importorskip("PIL.ImageCms")
    tif, jxl = _make_master(tmp_path, "romm", romm_toe_icc(), "always")
    assert _blob_state(jxl, tmp_path / "probe") is False

    def _derive_srgb(master: Path, out_dir: Path) -> Path:
        _run("jxl_recompressor.py", master.parent, out_dir, "--mode", "2",
             "--workers", "1", "--distance", "1", "--effort", "7",
             "--on-downgrade", "convert", "--output-icc", "sRGB")
        derivs = sorted(out_dir.rglob("*.jxl"))
        assert len(derivs) == 1
        dec_png = out_dir / "deriv.png"
        subprocess.run(["djxl", str(derivs[0]), str(dec_png),
                        "--bits_per_sample=16"],
                       check=True, capture_output=True, timeout=300)
        return dec_png

    _lbl, srgb_bytes = rec._resolve_output_icc("sRGB")
    srgb = tmp_path / "srgb.icc"
    srgb.write_bytes(srgb_bytes)
    ref = tmp_path / "ref.png"
    subprocess.run(["magick", str(tif), "-intent", "Relative",
                    "-black-point-compensation", "-profile", str(srgb),
                    "-depth", "16", str(ref)],
                   check=True, capture_output=True, timeout=300)

    blob_png = _derive_srgb(jxl, tmp_path / "recompressed")
    blob_psnr = _compare_psnr(blob_png, ref)

    # Native-path control on the same fixture: a master whose profile HAS a
    # native JXL form derives through the unchanged code path. Two lossy d=1
    # encodes cap this fixture at ~38-39 dB either way (measured 2026-09-26:
    # blob 38.5, native 38.5, 'skip' 38.8 — the pre-fix blob path scores ~15),
    # so the assertion is parity with the control, not an absolute number.
    _tif2, elle = _make_master(tmp_path, "elle", _elle_g22_icc(), "always")
    ctrl_png = _derive_srgb(elle, tmp_path / "recompressed_ctrl")
    ref2 = tmp_path / "ref2.png"
    subprocess.run(["magick", str(_tif2), "-intent", "Relative",
                    "-black-point-compensation", "-profile", str(srgb),
                    "-depth", "16", str(ref2)],
                   check=True, capture_output=True, timeout=300)
    ctrl_psnr = _compare_psnr(ctrl_png, ref2)

    assert blob_psnr >= 35.0 and blob_psnr >= ctrl_psnr - 0.5, (
        f"sRGB derivative of a lossy ICC-blob master: {blob_psnr:.1f} dB vs "
        f"{ctrl_psnr:.1f} dB for the native-path control — the XMP ICC was "
        f"assigned to linear pixels")


@real
def test_transcoder_icc_profile_on_a_blob_master(tmp_path):
    tif, jxl = _make_master(tmp_path, "romm", romm_toe_icc(), "always")
    assert _blob_state(jxl, tmp_path / "probe") is False

    _run("jxl_jpeg_transcoder.py", jxl, "--decode", "--force-convert",
         "--mode", "1", "--format", "png", "--bit-depth", "16",
         "--icc-profile", "AdobeRGB", "--workers", "1")
    out = jxl.parent / "recovered_jpeg" / (jxl.stem + ".png")
    assert out.exists()

    adobe = tmp_path / "adobe.icc"
    adobe.write_bytes(rec._adobe_rgb_icc_bytes())
    ref = tmp_path / "ref.png"
    subprocess.run(["magick", str(tif), "-intent", "Relative",
                    "-black-point-compensation", "-profile", str(adobe),
                    "-depth", "16", str(ref)],
                   check=True, capture_output=True, timeout=300)
    psnr = _compare_psnr(out, ref)
    assert psnr >= 45.0, (
        f"AdobeRGB conversion of a lossy ICC-blob master is {psnr:.1f} dB "
        f"from the correct conversion")


@real
def test_recompressor_keep_resize_stays_in_the_master_space(tmp_path):
    """A resize-only derivative of a blob master must stay in the ORIGINAL
    profile (djxl's linear sRGB must be converted, never re-assigned)."""
    tif, jxl = _make_master(tmp_path, "romm", romm_toe_icc(), "always")

    out_dir = tmp_path / "recompressed"
    _run("jxl_recompressor.py", jxl.parent, out_dir, "--mode", "2",
         "--workers", "1", "--distance", "1", "--effort", "7",
         "--on-downgrade", "convert", "--resize-long", "64")
    derivs = sorted(out_dir.rglob("*.jxl"))
    assert len(derivs) == 1

    # The derivative's own profile is still the ROMM one (ProPhoto primaries).
    orig_icc = tmp_path / "deriv_orig.icc"
    subprocess.run(["djxl", str(derivs[0]), str(tmp_path / "deriv.png"),
                    f"--orig_icc_out={orig_icc}"],
                   check=True, capture_output=True, timeout=300)
    assert b"ROMM toe" in orig_icc.read_bytes(), (
        "the keep-derivative lost the master's profile")

    # Colours: decode the derivative with the fixed decoder and compare page 0
    # (the decoded TIFF also carries a JPEG preview page, which magick compare
    # would mix in) with a direct Lanczos resize of the source TIFF.
    import imagecodecs
    import tifffile
    out_tif = _decode(derivs[0].parent, tmp_path / "decoded")
    ref = tmp_path / "ref.png"
    subprocess.run(["magick", str(tif), "-filter", "Lanczos", "-resize",
                    "64x64!", "-depth", "16", str(ref)],
                   check=True, capture_output=True, timeout=300)
    with tifffile.TiffFile(str(out_tif)) as tf:
        got = tf.pages[0].asarray().astype(np.float64)
    want = imagecodecs.png_decode(ref.read_bytes()).astype(np.float64)
    assert got.shape == want.shape
    mse = float(((got - want) ** 2).mean())
    psnr = 10.0 * np.log10(65535.0 ** 2 / mse)
    assert psnr >= 40.0, (
        f"keep-derivative colours are {psnr:.1f} dB from the reference resize")


@real
def test_non_blob_derivatives_are_byte_identical_to_before(tmp_path, monkeypatch):
    """Controls: Elle g2.2 (native) and ROMM 'skip' masters derive to exactly
    the same bytes as the pre-fix code — the new djxl flags and the blob
    branch must not touch the correct paths."""
    old_src = subprocess.run(["git", "show", "HEAD:jxl_recompressor.py"],
                             capture_output=True, text=True, cwd=REPO,
                             check=True).stdout
    old_path = tmp_path / "jxl_recompressor_head.py"
    old_path.write_text(old_src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("jxl_recompressor_head",
                                                  old_path)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)

    label, icc_bytes = rec._resolve_output_icc("sRGB")
    icc_path = tmp_path / "srgb.icc"
    icc_path.write_bytes(icc_bytes)

    masters = [_make_master(tmp_path, "elle", _elle_g22_icc(), "always")[1],
               _make_master(tmp_path, "skip", romm_toe_icc(), "skip")[1]]
    for jxl in masters:
        digests = []
        for mod, tag in ((rec, "new"), (old, "old")):
            out = tmp_path / f"{jxl.stem}_{tag}.jxl"
            monkeypatch.setattr(mod, "TEMP_DIR", str(tmp_path))
            monkeypatch.setattr(mod, "OUTPUT_ICC", "sRGB")
            monkeypatch.setattr(mod, "_OUTPUT_ICC_LABEL", label)
            monkeypatch.setattr(mod, "_OUTPUT_ICC_BYTES", icc_bytes)
            monkeypatch.setattr(mod, "_OUTPUT_ICC_PATH", icc_path)
            monkeypatch.setattr(mod, "RESIZE_MODE", None)
            monkeypatch.setattr(mod, "SHARPEN", "none")
            monkeypatch.setattr(mod, "CJXL_DISTANCE", 1.0)
            monkeypatch.setattr(mod, "CJXL_EFFORT", 1)
            converted, _size = mod._derive_pixels(jxl, out)
            assert converted is True
            digests.append(hashlib.md5(out.read_bytes()).hexdigest())
        assert digests[0] == digests[1], (
            f"{jxl.name}: the derivative bytes changed for a NON-blob master")


# ---------------------------------------------------------------------------
# Grey masters with a table-curve GREY profile (Photoshop "Dot Gain"-style):
# djxl returns LINEAR grey, the same bug in one channel. Grey is never
# colour-converted, so every path goes back to the file's own grey profile.
# ---------------------------------------------------------------------------

def _grey_tiff(path: Path, icc: bytes):
    import tifffile
    x = np.linspace(0.02, 0.98, 128)
    yy, xx = np.meshgrid(x, x, indexing="ij")
    arr = (((xx + yy) / 2) * 65535).astype("uint16")
    tifffile.imwrite(str(path), arr, photometric="minisblack", metadata=None,
                     extratags=[(34675, "B", len(icc), icc, False)])


def _make_grey_master(tmp_path: Path):
    src_dir = tmp_path / "src_grey"
    src_dir.mkdir()
    tif = src_dir / "grey.tif"
    _grey_tiff(tif, grey_toe_icc())
    jxl = _encode(src_dir, tmp_path / "jxl_grey", "always")
    assert _blob_state(jxl, tmp_path / "probe") is False, (
        "the grey fixture did not produce a lossy ICC-blob file")
    return tif, jxl


def _page0_psnr(got: np.ndarray, want: np.ndarray) -> float:
    got = got.astype(np.float64).squeeze()
    want = want.astype(np.float64).squeeze()
    assert got.shape == want.shape, (got.shape, want.shape)
    mse = float(((got - want) ** 2).mean())
    return 10.0 * np.log10(65535.0 ** 2 / mse)


def _resized_ref(tif: Path, tmp_path: Path) -> np.ndarray:
    import imagecodecs
    ref = tmp_path / "ref_resized.png"
    subprocess.run(["magick", str(tif), "-filter", "Lanczos", "-resize",
                    "64x64!", "-depth", "16", str(ref)],
                   check=True, capture_output=True, timeout=300)
    return imagecodecs.png_decode(ref.read_bytes())


@real
def test_grey_blob_master_decodes_correctly(tmp_path):
    tif, jxl = _make_grey_master(tmp_path)
    out = _decode(jxl.parent, tmp_path / "decoded")
    assert _tiff_psnr(tif, out) >= 45.0


@real
@pytest.mark.parametrize("recipe", [["--resize-long", "64"],
                                    ["--output-icc", "sRGB"]],
                         ids=["resize", "output-icc"])
def test_recompressor_grey_blob_derivative_keeps_its_tones(tmp_path, recipe):
    """Resize-only, and --output-icc (which never converts grey): the
    derivative must come back in the master's grey profile, not in djxl's
    linear grey labelled with the master's profile by the copied XMP."""
    import tifffile
    tif, jxl = _make_grey_master(tmp_path)
    out_dir = tmp_path / "recompressed"
    _run("jxl_recompressor.py", jxl.parent, out_dir, "--mode", "2",
         "--workers", "1", "--distance", "1", "--effort", "7",
         "--on-downgrade", "convert", *recipe)
    derivs = sorted(out_dir.rglob("*.jxl"))
    assert len(derivs) == 1
    orig_icc = tmp_path / "deriv_orig.icc"
    subprocess.run(["djxl", str(derivs[0]), str(tmp_path / "deriv.png"),
                    f"--orig_icc_out={orig_icc}"],
                   check=True, capture_output=True, timeout=300)
    assert b"Grey ROMM toe" in orig_icc.read_bytes(), (
        "the grey derivative lost the master's grey profile")

    out_tif = _decode(derivs[0].parent, tmp_path / "decoded")
    with tifffile.TiffFile(str(out_tif)) as tf:
        got = tf.pages[0].asarray()
    if recipe[0] == "--resize-long":
        want = _resized_ref(tif, tmp_path)
    else:
        want = tifffile.imread(str(tif))
    psnr = _page0_psnr(got, want)
    assert psnr >= 40.0, f"grey derivative tones are {psnr:.1f} dB off"


@real
def test_transcoder_grey_blob_derivative_keeps_its_tones(tmp_path):
    import imagecodecs
    tif, jxl = _make_grey_master(tmp_path)
    _run("jxl_jpeg_transcoder.py", jxl, "--decode", "--force-convert",
         "--mode", "1", "--format", "png", "--bit-depth", "16",
         "--resize-long", "64", "--workers", "1")
    out = jxl.parent / "recovered_jpeg" / (jxl.stem + ".png")
    assert out.exists()
    icc = tmp_path / "out.icc"
    subprocess.run(["magick", str(out), str(icc)],
                   check=True, capture_output=True, timeout=300)
    assert b"Grey ROMM toe" in icc.read_bytes(), (
        "the grey PNG is not in the master's grey profile")
    psnr = _page0_psnr(imagecodecs.png_decode(out.read_bytes()),
                       _resized_ref(tif, tmp_path))
    assert psnr >= 40.0, f"grey PNG tones are {psnr:.1f} dB off"


# ---------------------------------------------------------------------------
# Encoder cautious test
# ---------------------------------------------------------------------------

@real
def test_cautious_refuses_a_table_curve_profile_when_lossy(tmp_path, monkeypatch):
    monkeypatch.setattr(enc, "CJXL_DISTANCE", 1.0)
    monkeypatch.setattr(enc, "CJXL_MODULAR", False)
    icc = romm_toe_icc()
    assert enc._cautious_test_icc_depth(icc, 8) is False
    assert enc._cautious_test_icc_depth(icc, 16) is False


@real
def test_cautious_still_embeds_a_matrix_trc_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(enc, "CJXL_DISTANCE", 1.0)
    monkeypatch.setattr(enc, "CJXL_MODULAR", False)
    icc = _elle_g22_icc()
    assert enc._cautious_test_icc_depth(icc, 8) is True
    assert enc._cautious_test_icc_depth(icc, 16) is True


@real
def test_cautious_cache_key_bumped_and_old_verdicts_ignored(tmp_path, monkeypatch):
    """The cache key carries :t=2 so every profile is retested once: a t=1
    'embed' verdict for a table-curve profile must not keep working."""
    monkeypatch.setattr(enc, "CJXL_DISTANCE", 1.0)
    monkeypatch.setattr(enc, "CJXL_MODULAR", False)
    stored = {}
    monkeypatch.setattr(enc, "_load_icc_cache", lambda: dict(stored))

    def _save(cache):
        stored.clear()
        stored.update(cache)
    monkeypatch.setattr(enc, "_save_icc_cache", _save)

    icc = romm_toe_icc()
    assert enc._cautious_should_embed_icc(icc, tmp_path / "x.tif") is False
    key = next(iter(stored))
    assert key.endswith(":t=2"), key

    old_key = key[: -len(":t=2")]
    assert old_key != key
    stored.clear()
    stored[old_key] = {"embed": True}
    assert enc._cautious_should_embed_icc(icc, tmp_path / "x.tif") is False, (
        "a pre-t=2 'embed' verdict was honoured — the profile was not retested")
