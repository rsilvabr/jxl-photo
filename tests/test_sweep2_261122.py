"""The bugs of Fable's second sweep (2026-11-22, after v2.31.0-rc5,
AI_tools/261122_Fable_review_full-sweep-2.md: D1-D16, P1), one test each, made
from its reproduction scripts. Real codecs where the bug is in what they read or
write; every folder is hostile (Japanese, [ ], a space; '%' where exiftool or
ImageMagick read the path).
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_photo_common as common  # noqa: E402,F401
import jxl_recompressor as rec  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
HOSTILE = "250118_吾妻山公園 [FINAL] x"
PERCENT = "100%off %d [x] y"
needs_codecs = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool")),
    reason="needs cjxl, djxl and exiftool on PATH")
needs_magick = pytest.mark.skipif(not shutil.which("magick"), reason="needs ImageMagick")


def _script(name, *args, cwd=None, env=None):
    """A backend as a subprocess (its log in the suite's JXLPHOTO_LOG_DIR)."""
    return subprocess.run([sys.executable, str(REPO / name), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL, timeout=600,
                          cwd=cwd, env=dict(os.environ, JXLPHOTO_SETTINGS="", **(env or {})))


def _tags(path: Path, *names):
    """exiftool's values (-s3) of `names`, through an argfile: on a command
    line exiftool reads the '[ ]' of a hostile folder as a wildcard."""
    r = rec._run_exiftool_argfile(["-s3", *names, str(path)], timeout=60)
    return (r.stdout or "").split()


# --- D2: a later page of a multi-page TIFF keeps its own Orientation --------------

@needs_codecs
@pytest.mark.parametrize("folder", [HOSTILE, PERCENT])
def test_d2_a_later_page_keeps_its_own_orientation_and_resolution(tmp_path, folder):
    """rc5's `-IFD0:all<IFD1:all#` (#696, review N1) wrote the page's values
    through their PRINTED form: Orientation 1 matched 'Rotate 180' (3) and the
    page's ResolutionUnit was not copied at all (Fable's b1). Both routes: a '%'
    path copies through _exiftool_source's MIE container."""
    src = tmp_path / folder / "TIFF"
    src.mkdir(parents=True)
    tif = src / "two (2).tif"
    img = np.full((24, 32, 3), 1000, np.uint16)
    with tifffile.TiffWriter(str(tif)) as w:
        w.write(img, photometric="rgb", resolution=(118, 118), resolutionunit=3,
                extratags=[(274, "H", 1, 6, True)])
        w.write(img, photometric="rgb", resolution=(300, 300), resolutionunit=2,
                extratags=[(274, "H", 1, 1, True)])
    out = tmp_path / folder / "JXL"
    r = _script("jxl_tiff_encoder.py", src, out, "--mode", "0", "--distance", "0",
                "--effort", "3", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    want = ("-IFD0:Orientation#", "-IFD0:ResolutionUnit#", "-IFD0:XResolution#")
    assert _tags(out / "two (2).jxl", *want) == ["6", "3", "118"]
    assert _tags(out / "two (2)_page1.jxl", *want) == ["1", "2", "300"]


@needs_codecs
@pytest.mark.parametrize("folder", [HOSTILE, PERCENT])
def test_s5_page_2_keeps_its_own_values_in_a_percent_folder_too(tmp_path, folder):
    """The review of rc6, S5: a '%' path copies through a MIE container, which
    holds a TIFF's IFD0 and IFD1 only; page 2 and after took page 0's values
    (left over from #696). The plain folder was right."""
    src = tmp_path / folder / "TIFF"
    src.mkdir(parents=True)
    tif = src / "three pages (2).tif"
    img = np.full((24, 32, 3), 1000, np.uint16)
    pages = (("Zero", 6, 3, 118), ("One", 1, 2, 300), ("Two", 8, 2, 72))
    with tifffile.TiffWriter(str(tif)) as w:
        for n, (name, orient, unit, res) in enumerate(pages):
            w.write(img, photometric="rgb", resolution=(res, res), resolutionunit=unit,
                    software=f"Soft{name}",
                    extratags=[(274, "H", 1, orient, True),
                               (315, "s", 0, f"Artist{name}", True),
                               (271, "s", 0, f"Make{name}", True),
                               (297, "H", 2, (n, 3), True)])
    out = tmp_path / folder / "JXL"
    r = _script("jxl_tiff_encoder.py", src, out, "--mode", "0", "--distance", "0",
                "--effort", "3", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    want = ("-IFD0:Orientation#", "-IFD0:ResolutionUnit#", "-IFD0:XResolution#",
            "-IFD0:Artist", "-IFD0:Make", "-IFD0:Software", "-IFD0:PageNumber")
    for n, (name, orient, unit, res) in enumerate(pages):
        jxl = out / ("three pages (2)" + (f"_page{n}" if n else "") + ".jxl")
        # The second review of rc6, S2: Make, Artist, PageNumber... of page 2
        # were page 1's in a '%' folder after the first fix.
        assert _tags(jxl, *want) == [str(orient), str(unit), str(res), f"Artist{name}",
                                     f"Make{name}", f"Soft{name}", str(n), "3"], jxl.name


# --- P1: the plain recompression shares the cores among its cjxl too ---------------

def _rgb16_tiff(path: Path, seed=4, shape=(64, 96, 3)):
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    tifffile.imwrite(str(path), rng.integers(0, 65535, shape, dtype=np.uint16),
                     photometric="rgb")


def _masters(root: Path, n=3, distance="0.5") -> Path:
    src = root / "TIFF"
    for i in range(n):
        _rgb16_tiff(src / f"DSC_{i:04d} (2).tif", seed=i + 1)
    out = root / "JXL"
    r = _script("jxl_tiff_encoder.py", src, out, "--mode", "0", "--distance", distance,
                "--effort", "1", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    return out


@needs_codecs
def test_p1_a_plain_recompression_of_several_workers_passes_the_threads(
        tmp_path, monkeypatch):
    """rc5 gave each derivative's cjxl its share of the cores (V1) but not the
    plain recompression's (Fable's p1: 23.3 -> 19.7 s, the same codestreams)."""
    masters = _masters(tmp_path / HOSTILE)
    seen = []
    real = rec._run_captured

    def spy(cmd, *a, **k):
        if Path(str(cmd[0])).stem.lower() == "cjxl":
            seen.append([str(c) for c in cmd])
        return real(cmd, *a, **k)

    monkeypatch.setattr(rec, "_run_captured", spy)
    monkeypatch.setattr(common.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(sys, "argv", ["jxl_recompressor.py", str(masters),
                                      str(tmp_path / HOSTILE / "small"), "--mode", "0",
                                      "--distance", "2", "--effort", "1", "--workers", "3"])
    try:
        rec.main()
    except SystemExit as e:
        assert not e.code
    encodes = [c for c in seen if "-d" in c]
    assert len(encodes) == 3, seen
    assert all("--num_threads=21" in c for c in encodes), encodes
    assert rec._CJXL_THREADS is None


# --- D3: a transcoder derivative leaves a scanner's IR channel out -----------------

def _ir_scan_master(root: Path) -> Path:
    """A 4-sample scan, ExtraSamples 0 (VueScan's RGBI): red columns with the IR
    full, blue ones with the IR at zero."""
    src = root / "TIFF"
    src.mkdir(parents=True)
    img = np.zeros((64, 64, 4), np.uint16)
    img[:, 0::2, 0] = 65535
    img[:, 0::2, 3] = 65535
    img[:, 1::2, 2] = 65535
    tifffile.imwrite(str(src / "scan ir (2).tif"), img, photometric="rgb",
                     extrasamples=[0])
    masters = root / "JXL"
    r = _script("jxl_tiff_encoder.py", src, masters, "--mode", "0", "--distance", "0",
                "--effort", "3", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    return masters / "scan ir (2).jxl"


@needs_codecs
@needs_magick
@pytest.mark.parametrize("fmt", [
    ("--format", "jpeg", "--resize-percent", "50"),
    ("--format", "png", "--bit-depth", "8", "--resize-percent", "50"),
    ("--format", "png", "--bit-depth", "16", "--to-srgb"),
], ids=["jpeg-resized", "png8-resized", "png16-srgb"])
def test_d3_a_transcoder_derivative_leaves_the_ir_channel_out(tmp_path, fmt):
    """The IR reached ImageMagick as alpha: Lanczos weighted the colour by it
    (red, not purple: [233, 1, 1] against [117, 0, 117]) and a PNG copy came
    out RGBA with the IR as its transparency (Fable's b2). #695 fixed the
    recompressor's derivatives; these are the transcoder's."""
    from PIL import Image
    master = _ir_scan_master(tmp_path / HOSTILE)
    out = tmp_path / HOSTILE / "out [copy]"
    r = _script("jxl_jpeg_transcoder.py", master, out, "--decode", "--force-convert",
                "--mode", "0", "--workers", "1", *fmt)
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    made = [p for p in out.rglob("*") if p.suffix.lower() in (".jpg", ".png")]
    assert len(made) == 1, made
    with Image.open(made[0]) as im:
        assert im.mode in ("RGB", "I;16", "I") or "A" not in im.getbands(), im.mode
        arr = np.asarray(im.convert("RGB"), dtype=np.float64)
    red, blue = arr[..., 0].mean(), arr[..., 2].mean()
    assert abs(red - blue) < 0.15 * max(red, blue), (red, blue)    # purple, not red
    rel = rec._run_exiftool_argfile(["-s3", "-XMP-dc:Relation", str(made[0])],
                                    timeout=60).stdout or ""
    assert "jxlphoto-extrasamples" not in rel
    assert "jxlphoto-derived:" in rel


@needs_codecs
@needs_magick
def test_d12_a_thumbnail_made_from_the_file_takes_the_profile_carried_in_xmp(tmp_path):
    """A lossy master whose profile went to XMP only (the encoder's 'skip':
    pixels tagged sRGB in the codestream) got a thumbnail in the wrong
    colours wherever it is made from the file — the recompressor, the
    refresh, the encoder's fallback (Fable's e6/b5: [30.6 27.0 30.3] against
    [19.7 23.1 30.4] on a real SFprofT scan). Here a linear profile: the
    linear 0.2 grey is 124 in sRGB, and 51 when its numbers are read as sRGB."""
    import io
    from PIL import Image
    src = tmp_path / HOSTILE / "TIFF"
    src.mkdir(parents=True)
    linear = rec._build_matrix_trc_icc(
        "linear sRGB primaries (test)",
        [(0.64, 0.33), (0.30, 0.60), (0.15, 0.06)], (0.3127, 0.3290), 1.0)
    rng = np.random.default_rng(7)
    img = np.clip(0.2 * 65535 + rng.normal(0, 600, (600, 800, 3)), 0, 65535).astype(np.uint16)
    tifffile.imwrite(str(src / "linear (2).tif"), img, photometric="rgb",
                     iccprofile=linear)
    masters = tmp_path / HOSTILE / "JXL"
    r = _script("jxl_tiff_encoder.py", src, masters, "--mode", "0", "--distance", "0.5",
                "--effort", "3", "--workers", "1", "--icc-png-strategy", "skip")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    master = masters / "linear (2).jxl"
    assert "jxlphoto-iccprofile:" in " ".join(_tags(master, "-XMP-dc:Relation"))
    work = tmp_path / "work"
    work.mkdir()
    jpeg = rec._make_explorer_thumbnail(master, work)
    assert jpeg, "no thumbnail"
    with Image.open(io.BytesIO(jpeg)) as im:
        mean = np.asarray(im.convert("RGB"), dtype=np.float64).mean()
    assert abs(mean - 124) < 6, mean


@needs_codecs
def test_d3_a_faithful_png_keeps_the_fourth_channel(tmp_path):
    """The owner's call: a faithful 16-bit decode keeps the JXL's channels."""
    from PIL import Image
    master = _ir_scan_master(tmp_path / HOSTILE)
    out = tmp_path / HOSTILE / "out [faithful]"
    r = _script("jxl_jpeg_transcoder.py", master, out, "--decode", "--force-convert",
                "--mode", "0", "--workers", "1", "--format", "png", "--bit-depth", "16")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    made = [p for p in out.rglob("*.png")]
    assert len(made) == 1, made
    with Image.open(made[0]) as im:
        assert "A" in im.getbands() or im.mode not in ("RGB",), im.mode
