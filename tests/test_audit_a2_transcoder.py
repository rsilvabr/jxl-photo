#!/usr/bin/env python3
"""A2 of the 2026-11-03 audit — the transcoder items.

  T-A (#568) a direct JXL -> JPEG/PNG of a master whose real profile lives
             only in XMP (the encoder skipped the iCCP) came out labelled
             sRGB; a lossy ICC-blob master came out of djxl outside its space.
  T-F (#571) a failed metadata copy / marker write was swallowed, and
             --delete-source deleted the source behind the poorer output.
  T-C (#579) a checksums.md5 lock that timed out was reported as filed.
  T-G (#580) --repair-jbrd swapped the archive in with a new mtime.
  T-E (#581) DELETE_SOURCE_REQUIRE_MD5=False deleted a JXL next to ANY
             same-named JPEG.

Every fixture path is hostile: Japanese, `[ ]`, a space, `%`, parentheses.
Real codecs/exiftool where the item touches them (skipped when absent).

Pre-fix proof (must fail on the base commit's scripts):

    git show 488e8b6:<script>.py > <tmp>/a2pre/<script>.py   (the 4 backends)
    JXL_SCRIPTS_UNDER_TEST=<tmp>/a2pre python -m pytest tests/test_audit_a2_transcoder.py -q
"""

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

_REPO = Path(__file__).resolve().parent.parent
_OVERRIDE = os.environ.get("JXL_SCRIPTS_UNDER_TEST")
_SCRIPTS = Path(_OVERRIDE).resolve() if _OVERRIDE else _REPO
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jxl_jpeg_transcoder as tra  # noqa: E402
from _icc_fixtures import romm_toe_icc  # noqa: E402

HOSTILE = "250118_吾妻山公園_菜の花 [FINAL] 5%"
_HAVE = {t: shutil.which(t) is not None for t in ("cjxl", "djxl", "exiftool", "magick")}
real = pytest.mark.skipif(not all(_HAVE[t] for t in ("cjxl", "djxl", "exiftool")),
                          reason="needs cjxl, djxl and exiftool on PATH")
real_magick = pytest.mark.skipif(not all(_HAVE.values()),
                                 reason="needs cjxl, djxl, exiftool and magick on PATH")

_RUN_FLAGS = ("OVERWRITE", "DELETE_SOURCE", "DELETE_CONFIRM", "DELETE_SKIPPED",
              "DELETE_SOURCE_REQUIRE_MD5", "STORE_MD5", "TEMP2_DIR",
              "_MD5_LOCK_TIMEOUT_S")


@pytest.fixture(autouse=True)
def _restore_flags():
    saved = {n: getattr(tra, n) for n in _RUN_FLAGS if hasattr(tra, n)}
    yield
    for n, v in saved.items():
        setattr(tra, n, v)


def _hostile(tmp_path, sub="01_Z7 (2)") -> Path:
    d = tmp_path / HOSTILE / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def _script(name):
    return str(_SCRIPTS / name)


def _run(script, *args, check=True, timeout=900):
    r = subprocess.run([sys.executable, _script(script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    if check:
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return r


def _main(argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["jxl_jpeg_transcoder.py"] + [str(a) for a in argv])
    try:
        tra.main()
    except SystemExit as e:
        return e.code
    return 0


def _gradient(h=96, w=128):
    x = np.linspace(0.1, 0.9, w)
    y = np.linspace(0.1, 0.9, h)
    yy, xx = np.meshgrid(y, x, indexing="ij")
    return np.stack([(xx * 65535), (yy * 65535),
                     ((1 - xx) * (1 - yy) * 0.8 + 0.1) * 65535], -1).astype(np.uint16)


def _tiff(path: Path, icc: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), _gradient(), photometric="rgb", metadata=None,
                     extratags=[(34675, "B", len(icc), icc, False)])
    return path


def _master(tmp_path, icc, strategy, name="DSC_0001 (2)"):
    src = _tiff(_hostile(tmp_path, "src [TIFF16]") / f"{name}.tif", icc)
    out = tmp_path / HOSTILE / "16B_JXL 5%"
    r = _run("jxl_tiff_encoder.py", src.parent, out, "--mode", "0", "--distance", "1",
             "--workers", "1", "--icc-png-strategy", strategy, "--no-preflight")
    return src, out / f"{name}.jxl", r.stdout + r.stderr


def _icc_of(path: Path) -> bytes:
    from PIL import Image
    with Image.open(path) as im:
        return im.info.get("icc_profile") or b""


def _rgb8(path: Path) -> np.ndarray:
    from PIL import Image
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.float64)


def _jpeg(path: Path, seed=1, size=(96, 128)):
    from PIL import Image
    rng = np.random.default_rng(seed)
    Image.fromarray((rng.random(size + (3,)) * 255).astype("uint8")).save(path, quality=90)
    return path


# ===========================================================================
# T-A (#568)
# ===========================================================================

@real
@pytest.mark.parametrize("fmt", ["jpeg", "png"])
def test_ta_skip_master_carries_the_original_profile(tmp_path, fmt):
    icc = romm_toe_icc()
    src, jxl, log = _master(tmp_path, icc, "cautious")
    assert "Skipping ICC in PNG iCCP" in log, "fixture: the iCCP was not skipped"
    out = tmp_path / HOSTILE / f"out {fmt} (1)"
    _run("jxl_jpeg_transcoder.py", jxl, out, "--mode", "0", "--force-convert",
         "--format", fmt)
    produced = next(out.iterdir())
    assert _icc_of(produced) == icc, (
        f"{produced.name} is not labelled with the master's profile")
    if fmt == "jpeg":
        # Pixels untouched by the relabel: still the TIFF's numbers.
        want = tifffile.imread(str(src)).astype(np.float64) / 257.0
        assert abs(_rgb8(produced).mean() - want.mean()) < 2.0


@real_magick
def test_ta_blob_master_jpeg_is_converted_back(tmp_path):
    icc = romm_toe_icc()
    src, jxl, _log = _master(tmp_path, icc, "always")
    probe = tmp_path / "probe"
    probe.mkdir()
    o, oi = probe / "o.icc", probe / "oi.icc"
    subprocess.run(["djxl", str(jxl), str(probe / "p.png"), f"--icc_out={o}",
                    f"--orig_icc_out={oi}"], check=True, capture_output=True)
    assert o.read_bytes() != oi.read_bytes(), "fixture: not a lossy ICC blob"
    out = tmp_path / HOSTILE / "out jpeg [blob]"
    _run("jxl_jpeg_transcoder.py", jxl, out, "--mode", "0", "--force-convert",
         "--format", "jpeg", "--quality", "95")
    got = _rgb8(next(out.iterdir()))
    want = tifffile.imread(str(src)).astype(np.float64) / 257.0
    mse = float(((got - want) ** 2).mean())
    psnr = 10 * np.log10(255.0 ** 2 / max(mse, 1e-9))
    assert psnr >= 35.0, f"JPEG of a blob master is {psnr:.1f} dB from the source"


@real
def test_ta_native_master_keeps_djxl_jpeg(tmp_path):
    """Control: a matrix/TRC master (native in the codestream) keeps djxl's
    own JPEG bytes — only the profile may change."""
    import jxl_recompressor as rec
    icc = rec._build_matrix_trc_icc("Elle LargeRGB g2.2 (test)",
                                    [(0.7347, 0.2653), (0.1596, 0.8404), (0.0366, 0.0001)],
                                    (0.3457, 0.3585), 2.2)
    _src, jxl, _log = _master(tmp_path, icc, "cautious")
    out = tmp_path / HOSTILE / "out jpeg [native]"
    _run("jxl_jpeg_transcoder.py", jxl, out, "--mode", "0", "--force-convert",
         "--format", "jpeg", "--quality", "90")
    ref = tmp_path / "ref.jpg"
    subprocess.run(["djxl", str(jxl), str(ref), "--jpeg_quality=90"], check=True,
                   capture_output=True)

    def _scan(b):
        return b[b.index(b"\xff\xda"):]
    assert _scan(next(out.iterdir()).read_bytes()) == _scan(ref.read_bytes())


# ===========================================================================
# T-F (#571)
# ===========================================================================

def _iptc_jpeg(d: Path) -> Path:
    jpg = _jpeg(d / "DSC_0001 (2) 5%.jpg")
    r = tra._run_exiftool_argfile(["-overwrite_original",
                                   "-IPTC:Caption-Abstract=吾妻山 caption",
                                   str(jpg)], timeout=120)
    assert r.returncode == 0, r.stderr
    return jpg


def _failing_exiftool(monkeypatch, needle):
    real_call = tra._run_exiftool_argfile

    def fake(lines, *a, **k):
        if any(needle in str(x) for x in lines) and "-overwrite_original" in lines:
            return subprocess.CompletedProcess(lines, 1, "", "Error: simulated failure")
        return real_call(lines, *a, **k)
    monkeypatch.setattr(tra, "_run_exiftool_argfile", fake)


@real
@pytest.mark.parametrize("needle", ["-tagsfromfile", "jxlphoto-src:"],
                         ids=["metadata-copy", "marker-write"])
def test_tf_failed_metadata_keeps_the_jpeg(tmp_path, monkeypatch, needle):
    d = _hostile(tmp_path)
    jpg = _iptc_jpeg(d)
    out = tmp_path / HOSTILE / "JXL out"
    _failing_exiftool(monkeypatch, needle)
    _main([jpg.parent, out, "--mode", "0", "--force-convert", "--distance", "1",
           "--delete-source", "--delete-confirm-off"], monkeypatch)
    assert jpg.exists(), "the source was deleted behind an output that lost metadata"
    assert not (out / (jpg.stem + ".jxl")).exists(), "an incomplete output was kept"


@real
def test_tf_decode_marker_failure_is_an_error(tmp_path, monkeypatch):
    d = _hostile(tmp_path)
    jpg = _jpeg(d / "DSC_0002 [a].jpg")
    jxl_dir = tmp_path / HOSTILE / "JXL (1)"
    _run("jxl_jpeg_transcoder.py", d, jxl_dir, "--mode", "0", "--force-convert",
         "--distance", "1")
    jxl = jxl_dir / "DSC_0002 [a].jxl"
    assert jxl.exists()
    out = tmp_path / HOSTILE / "JPEG back"
    _failing_exiftool(monkeypatch, "jxlphoto-src:")
    _main([jxl, out, "--mode", "0", "--force-convert", "--format", "jpeg"], monkeypatch)
    assert not out.exists() or not any(out.glob("*.jpg")), (
        "a JPEG without its provenance markers was delivered as OK")


@real
def test_tf_control_plain_jpeg_converts(tmp_path, monkeypatch):
    """A JPEG with no IPTC/XMP: exiftool says 'No writable tags' with exit 0,
    which is still a success."""
    d = _hostile(tmp_path)
    jpg = _jpeg(d / "plain (3).jpg")
    r = tra._run_exiftool_argfile(["-overwrite_original", "-all=", str(jpg)], timeout=120)
    assert r.returncode == 0
    out = tmp_path / HOSTILE / "JXL plain"
    _main([jpg.parent, out, "--mode", "0", "--force-convert", "--distance", "1"],
          monkeypatch)
    assert (out / "plain (3).jxl").exists()


