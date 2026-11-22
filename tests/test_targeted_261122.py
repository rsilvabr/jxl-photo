"""The bugs of the targeted review after v2.31.0-rc6 (2026-11-22): the parts the
two sweeps had not read in full — the transcoder's auto mode and --repair-jbrd
(AI_tools/261122_Opus_review_targeted-transcoder.md: T1, T1b, A1, A3-A5) and the
three other scripts' core, derivatives and restamp
(AI_tools/261122_Opus_review_targeted-cores.md: F1-F4, P1, O1-O5). One test
each; real codecs where the bug is in what they read or write; every folder is
hostile (Japanese, [ ], a space; '%' where ExifTool or ImageMagick read the path).
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
VUESCAN = Path(r"E:\TESTE\TIFF_scans_varied\rgbi16_vuescan_srgb__0019.tif")
needs_codecs = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool")),
    reason="needs cjxl, djxl and exiftool on PATH")
needs_magick = pytest.mark.skipif(not shutil.which("magick"), reason="needs ImageMagick")


def _script(name, *args, cwd=None, env=None):
    """A backend as a subprocess (its log in the suite's JXLPHOTO_LOG_DIR);
    `env` may set JXLPHOTO_SETTINGS to a settings file."""
    e = dict(os.environ, JXLPHOTO_SETTINGS="")
    e.update(env or {})
    return subprocess.run([sys.executable, str(REPO / name), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL, timeout=900,
                          cwd=cwd, env=e)


def _relation(path: Path) -> str:
    r = rec._run_exiftool_argfile(["-s3", "-XMP-dc:Relation", str(path)], timeout=60)
    return r.stdout or ""


# --- F2: an RGB+IR TIFF without an ExtraSamples tag (VueScan) ---------------------

@needs_codecs
@needs_magick
@pytest.mark.skipif(not VUESCAN.is_file(), reason="needs the owner's VueScan fixture")
def test_f2_a_vuescan_rgbi_scan_gets_its_ir_marker(tmp_path):
    """The real VueScan RGBI scan has 4 samples and no ExtraSamples tag: no
    jxlphoto-extrasamples marker was written, so the sRGB copy came out RGBA
    with the IR as its transparency, the Explorer thumbnail nearly white and
    the decoded TIFF declared the IR as alpha (the targeted review's F2)."""
    src = tmp_path / HOSTILE / "TIFF"
    src.mkdir(parents=True)
    tif = src / "vuescan scan (2).tif"
    shutil.copy2(VUESCAN, tif)
    masters = tmp_path / HOSTILE / "JXL"
    r = _script("jxl_tiff_encoder.py", src, masters, "--mode", "0", "--effort", "1",
                "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    master = masters / "vuescan scan (2).jxl"
    assert "jxlphoto-extrasamples:0" in _relation(master)
    copies = tmp_path / HOSTILE / "sRGB [copy]"
    r = _script("jxl_recompressor.py", master, copies, "--mode", "0", "--output-icc", "sRGB",
                "--resize-long", "512", "--distance", "1", "--effort", "1", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    copy = next(copies.rglob("*.jxl"))
    pam = subprocess.run(["djxl", str(copy), "-", "--output_format=pam"],
                         capture_output=True).stdout
    assert common._pam_array(pam).shape[2] == 3                    # no IR as alpha
    restored = tmp_path / HOSTILE / "TIFF back"
    r = _script("jxl_tiff_decoder.py", masters, restored, "--mode", "0", "--workers", "1",
                "--depth-policy", "preserve_original", "--no-preview")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    with tifffile.TiffFile(str(next(restored.rglob("*.tif")))) as t:
        assert tuple(int(v) for v in (t.pages[0].extrasamples or ())) in ((), (0,))


# --- F1: --provenance adopt stamps only the photo the archive holds -----------------

def _smooth_photo(seed=1):
    """A 16-bit picture with soft gradients and a little noise: a 2 px shift
    of it is a burst's next frame (about 25-30 dB), not another picture."""
    y, x = np.mgrid[0:120, 0:160].astype(np.float64)
    base = np.stack([20000 + 15000 * np.sin(x / 17.0 + k) * np.cos(y / 23.0 - k)
                     for k in (0.0, 1.0, 2.0)], axis=2) + 20000
    noise = np.random.default_rng(seed).normal(0, 300, base.shape)
    return np.clip(base + noise, 0, 65535).astype(np.uint16)


def _set_capture_time(tif: Path, when: str):
    r = rec._run_exiftool_argfile(["-overwrite_original", f"-DateTimeOriginal={when}",
                                   str(tif)], timeout=60)
    assert r.returncode == 0, r.stderr


def _legacy_archive(root: Path, photo, when: str) -> Path:
    """Photo A archived in mode 2, then made a LEGACY archive (its jxlphoto-src
    and -srcsum removed, as before v2.0.0) and its TIFF deleted."""
    cam_a, out = root / "card A", root / "masters [flat]"
    cam_a.mkdir(parents=True)
    tif = cam_a / "DSC_0001.tif"
    tifffile.imwrite(str(tif), photo, photometric="rgb")
    _set_capture_time(tif, when)
    r = _script("jxl_tiff_encoder.py", cam_a, out, "--mode", "2", "--effort", "1",
                "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    m = out / "DSC_0001.jxl"
    marks = [x.strip() for x in _relation(m).split(",")
             if x.strip().startswith(("jxlphoto-src:", "jxlphoto-srcsum:"))]
    r = rec._run_exiftool_argfile(["-overwrite_original"]
                                  + [f"-XMP-dc:Relation-={x}" for x in marks] + [str(m)],
                                  timeout=60)
    assert r.returncode == 0 and "jxlphoto-src" not in _relation(m)
    tif.unlink()
    return m


def _adopt_run(root: Path, photo, when):
    """Another card's DSC_0001.tif (older than the archive: up to date, so
    --delete-skipped would delete it on the adopted archive's word)."""
    cam_b = root / "card B"
    cam_b.mkdir(parents=True)
    tif = cam_b / "DSC_0001.tif"
    tifffile.imwrite(str(tif), photo, photometric="rgb")
    if when:
        _set_capture_time(tif, when)
    old = 1_700_000_000
    os.utime(tif, (old, old))
    r = _script("jxl_tiff_encoder.py", cam_b, root / "masters [flat]", "--mode", "2",
                "--effort", "1", "--workers", "1", "--delete-source", "--delete-skipped",
                "--provenance", "adopt", "--delete-confirm-off")
    return tif, r


@needs_codecs
@pytest.mark.parametrize("case", ["burst frame", "tripod frame"])
def test_f1_adopt_does_not_stamp_another_photo(tmp_path, case):
    """The adopt scan was the delete gate's 20 dB check: a burst frame of the
    same name (25.8 dB from the archive) was stamped as its source, and the
    run then deleted that TIFF on the other photo's archive (the targeted
    review's F1). A tripod frame can be closer than any pixel floor: its
    capture time differs."""
    root = tmp_path / HOSTILE
    a = _smooth_photo(1)
    m = _legacy_archive(root, a, "2025:01:18 10:00:00")
    before = m.read_bytes()
    if case == "burst frame":
        b = np.clip(np.roll(a, 2, axis=1).astype(np.float64) * 1.03, 0, 65535).astype(np.uint16)
        when = None
    else:
        b = np.clip(a.astype(np.float64) + np.random.default_rng(9).normal(0, 60, a.shape),
                    0, 65535).astype(np.uint16)
        when = "2025:01:18 10:00:01"
    tif_b, r = _adopt_run(root, b, when)
    assert tif_b.exists(), (r.stdout + r.stderr)[-3000:]
    assert m.read_bytes() == before
    assert "jxlphoto-src" not in _relation(m)
    log = " ".join((r.stdout + r.stderr).split())
    if case == "tripod frame":      # closer than the floor: the capture time decides
        assert "another photo: its DateTimeOriginal" in log, log[-2000:]
    else:
        assert "dB floor" in log, log[-2000:]


@needs_codecs
def test_f1_adopt_still_stamps_the_photo_the_archive_holds(tmp_path):
    root = tmp_path / HOSTILE
    a = _smooth_photo(1)
    m = _legacy_archive(root, a, "2025:01:18 10:00:00")
    tif_b, r = _adopt_run(root, a, "2025:01:18 10:00:00")
    assert "jxlphoto-srcsum:" in _relation(m), (r.stdout + r.stderr)[-3000:]
    assert not tif_b.exists()                     # deleted: it IS the archived photo


# --- F4: no derivative of a scan's IR mask page -------------------------------------

def _scan_with_ir_page(root: Path) -> Path:
    """A 2-page scan like SilverFast's: the photo, then the infrared as a
    full-size grey page flagged SubfileType 4 (a mask), split by the encoder."""
    src = root / "TIFF"
    src.mkdir(parents=True)
    rng = np.random.default_rng(5)
    tif = src / "scan ir (2).tif"
    # Larger than 160 px: a smaller later page is taken for a camera's
    # thumbnail page and skipped anyway (_is_camera_thumbnail_page).
    with tifffile.TiffWriter(str(tif)) as w:
        w.write(rng.integers(0, 65535, (180, 240, 3), dtype=np.uint16), photometric="rgb",
                software="SilverFast 8.8.0 r24 (Jan  9 2023)")
        w.write(rng.integers(0, 65535, (180, 240), dtype=np.uint16), photometric="minisblack")
    # tifffile will not write a mask page itself: ExifTool sets the flag.
    r = rec._run_exiftool_argfile(["-overwrite_original", "-IFD1:SubfileType#=4", str(tif)],
                                  timeout=60)
    assert r.returncode == 0, r.stderr
    with tifffile.TiffFile(str(tif)) as t:
        assert int(t.pages[1].subfiletype) == 4
    masters = root / "JXL"
    r = _script("jxl_tiff_encoder.py", src, masters, "--mode", "0", "--effort", "1",
                "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    assert sorted(p.name for p in masters.glob("*.jxl")) == ["scan ir (2).jxl",
                                                              "scan ir (2)_page1.jxl"]
    return masters


@needs_codecs
@needs_magick
def test_f4_a_derivative_run_makes_no_copy_of_the_ir_mask_page(tmp_path):
    """Only thumbnail pages were skipped: the IR mask page got a 2048 px grey
    copy in sRGB_JXL beside the photo (the targeted review's F4); the
    transcoder's resized JPEGs the same."""
    masters = _scan_with_ir_page(tmp_path / HOSTILE)
    copies = tmp_path / HOSTILE / "sRGB [copies]"
    r = _script("jxl_recompressor.py", masters, copies, "--mode", "0", "--output-icc", "sRGB",
                "--resize-long", "64", "--distance", "1", "--effort", "1", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    made = sorted(p.name for p in copies.rglob("*.jxl"))
    assert made == ["scan ir (2).jxl"], made


@needs_codecs
@needs_magick
def test_f4_the_transcoder_makes_no_jpeg_of_the_ir_mask_page(tmp_path):
    masters = _scan_with_ir_page(tmp_path / HOSTILE)
    jpegs = tmp_path / HOSTILE / "JPEG [copies]"
    r = _script("jxl_jpeg_transcoder.py", masters, jpegs, "--decode", "--force-convert",
                "--mode", "0", "--workers", "1", "--format", "jpeg", "--resize-long", "64")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    made = sorted(p.name for p in jpegs.rglob("*.jpg"))
    assert made == ["scan ir (2).jpg"], made


@needs_codecs
def test_f4_a_plain_recompression_keeps_the_ir_page(tmp_path):
    """The split stays complete: only derivatives leave the mask page out."""
    masters = _scan_with_ir_page(tmp_path / HOSTILE)
    small = tmp_path / HOSTILE / "small"
    r = _script("jxl_recompressor.py", masters, small, "--mode", "0", "--distance", "1",
                "--effort", "1", "--workers", "1", "--on-scan", "convert",
                "--on-downgrade", "convert")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    assert len(list(small.rglob("*.jxl"))) == 2


# --- F3: the cautious ICC test of a GRAY profile ------------------------------------

EPSON_GRAY = Path(r"E:\TESTE\TIFF_scans_varied\gray16_epsongray18__fomapan400008.tif")


@needs_codecs
@pytest.mark.skipif(not EPSON_GRAY.is_file(), reason="needs the owner's EPSON grey fixture")
def test_f3_a_grey_profile_is_tested_on_a_grey_image(tmp_path):
    """The test image was RGB for every profile: cjxl refused the EPSON Gray
    1.8 profile on it, twice, "skip" was cached, and the lossy master carried
    sRGB grey in its codestream — mid-greys about 25 % too dark in a viewer
    (the targeted review's F3). The cache is a folder of the test's own."""
    src = tmp_path / HOSTILE / "TIFF"
    src.mkdir(parents=True)
    shutil.copy2(EPSON_GRAY, src / "grey scan (2).tif")
    out = tmp_path / HOSTILE / "JXL"
    r = _script("jxl_tiff_encoder.py", src, out, "--mode", "0", "--distance", "0.5",
                "--scan-distance", "0.5", "--effort", "1", "--workers", "1",
                "--icc-png-strategy", "cautious", "--icc-cache-dir", tmp_path / "icc cache")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    assert "Cautious ICC: embed" in r.stdout + r.stderr, (r.stdout + r.stderr)[-2000:]
    info = subprocess.run(["jxlinfo", "-v", str(out / "grey scan (2).jxl")],
                          capture_output=True, text=True).stdout
    assert "Transfer function: sRGB" not in info and "gamma(" in info, info


# --- O4: #701's rename fails (the previous error file open in a viewer) -------------

def _encoder_main(monkeypatch, enc, *args):
    monkeypatch.setattr(sys, "argv", ["jxl_tiff_encoder.py", *map(str, args)])
    try:
        enc.main()
    except SystemExit as e:
        return e.code or 0
    return 0


@needs_codecs
@pytest.mark.skipif(os.name != "nt", reason="a file held open blocks a rename on Windows only")
def test_o4_a_failed_jxl_never_keeps_its_final_name_when_the_rename_fails(
        tmp_path, monkeypatch):
    """The gate's check fails twice (an impossible PSNR floor); the first error
    file is open in a viewer the second time, so the rename onto it fails. In
    rc6 the failed JXL then kept its final name and the next --sync called it
    up to date (the targeted review's O4)."""
    import jxl_tiff_encoder as enc
    src = tmp_path / HOSTILE / "TIFF"
    out = tmp_path / HOSTILE / "JXL"
    src.mkdir(parents=True)
    tif = src / "DSC_0001 (2).tif"
    tifffile.imwrite(str(tif), np.random.default_rng(4).integers(0, 65535, (64, 96, 3),
                                                                 dtype=np.uint16),
                     photometric="rgb")
    monkeypatch.setattr(enc, "VERIFY_LOSSY_MIN_PSNR", 500.0)
    args = [src, out, "--mode", "0", "--distance", "1", "--workers", "1", "--no-preflight",
            "--delete-source", "--delete-confirm-off"]
    assert _encoder_main(monkeypatch, enc, *args) == 1
    err = out / "DSC_0001 (2)_jxlphoto-error.jxl"
    assert err.exists()
    with open(err, "rb"):
        assert _encoder_main(monkeypatch, enc, *args) == 1
    assert tif.exists()
    assert not (out / "DSC_0001 (2).jxl").exists()
    assert (out / "DSC_0001 (2) (2)_jxlphoto-error.jxl").exists()


# --- O5: keep-smaller holds an in-place multi-page group ----------------------------

def test_o5_a_group_held_by_keep_smaller_says_so(tmp_path, monkeypatch):
    """One page of an in-place group re-encoded no smaller (keep-smaller): the
    whole group is held, by the all-or-nothing rule, but rc6 said a sibling
    "did not convert this run", as for an error (the targeted review's O5)."""
    import logging
    items, originals = [], {}
    for name in ("p0.jxl", "p1.jxl"):
        src = tmp_path / HOSTILE / name
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"\xff\x0a" + name.encode() * 8)
        originals[src] = src.read_bytes()
        items.append({"src": src, "final": src,
                      "write": src.parent / f"{'ab' * 16}_{src.stem}.tmp",
                      "action": "convert", "in_place": True, "desc": "", "software": "",
                      "src_d": 1.0})
    rec.setup_logger()
    monkeypatch.setattr(rec, "KEEP_SMALLER", True)
    monkeypatch.setattr(rec, "TEMP2_DIR", None)
    monkeypatch.setattr(rec, "_verify_jxl_integrity", lambda p: True)
    monkeypatch.setattr(rec, "_read_mpg_markers",
                        lambda paths: ({str(p): "group-1" for p in paths}, True))

    def fake_convert(src, w, f, action, in_place, desc, software, src_d, **_kw):
        if Path(src).name == "p1.jxl":
            return (str(src), "skipped", str(f))        # not smaller, original kept
        Path(w).write_bytes(b"RECOMPRESSED")
        return (str(src), "ok", str(f))

    monkeypatch.setattr(rec, "convert_one", fake_convert)

    class _Collect(logging.Handler):
        def __init__(self):
            super().__init__()
            self.lines = []

        def emit(self, record):
            self.lines.append(record.getMessage())

    h = _Collect()
    rec.logger.addHandler(h)
    try:
        rec.process_group(items, 1)
    finally:
        rec.logger.removeHandler(h)
    assert all(p.read_bytes() == b for p, b in originals.items())
    held = [x for x in h.lines if "GROUP HELD IN PLACE" in x]
    assert held and "keep-smaller" in held[0] and "--no-keep-smaller" in held[0], h.lines


# --- T1: the bit-exact recovery deletes an archive only on its checksum -------------

def _jpeg_photo(path: Path, seed=3):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    y, x = np.mgrid[0:120, 0:160]
    arr = np.stack([(x * 1.5 + seed) % 256, (y * 2.0) % 256, ((x + y) * 0.8) % 256],
                   axis=2).astype(np.uint8)
    Image.fromarray(arr).save(str(path), quality=92, subsampling=0)
    return path


def _md5(p):
    import hashlib
    return hashlib.md5(Path(p).read_bytes()).hexdigest()


@needs_codecs
@pytest.mark.parametrize("line", [False, True], ids=["no line", "its line"])
def test_t1_recovery_deletes_an_archive_only_on_its_checksum(tmp_path, line):
    """djxl 0.11/0.12 rebuild ANOTHER JPEG, with exit 0, from an archive cjxl
    0.13 wrote with its defaults (#669): an archive without its checksums.md5
    line was deleted in rc6 on the strength of djxl's exit code (the targeted
    review's T1). With its line, the match proves it and the archive goes."""
    d = tmp_path / HOSTILE / PERCENT
    orig = _jpeg_photo(d / "src" / "photo (1).jpg")
    jxl = d / "photo (1).jxl"
    r = rec._run_captured(["cjxl", str(orig), str(jxl), "--lossless_jpeg=1"], 300)
    assert r.returncode == 0, r.stderr
    if line:
        (d / "checksums.md5").write_text(f"{_md5(orig)}  {jxl.name}\n", encoding="utf-8")
    r = _script("jxl_jpeg_transcoder.py", d, "--mode", "0", "--force-transcode", "--decode",
                "--delete-source", "--delete-confirm-off", "--workers", "1")
    out = r.stdout + r.stderr
    assert (d / "photo (1).jpg").exists(), out[-3000:]
    assert _md5(d / "photo (1).jpg") == _md5(orig)
    if line:
        assert not jxl.exists(), out[-3000:]
    else:
        assert jxl.exists(), out[-3000:]
        assert "no checksums.md5 line proves" in " ".join(out.split())


# --- T1b: the repair audit's answers are checked answers ----------------------------

@needs_codecs
def test_t1b_audit_does_not_call_ok_an_archive_that_rebuilds_another_jpeg(tmp_path):
    """The audit called "OK" any archive djxl rebuilt SOMETHING from; with
    djxl 0.12 an archive cjxl 0.13 wrote with its defaults rebuilds another
    JPEG (#669). Here the archive's checksums.md5 line names another JPEG:
    "OK" must not be the answer (the targeted review's T1b)."""
    d = tmp_path / HOSTILE / PERCENT
    orig = _jpeg_photo(d / "src" / "photo (1).jpg")
    jxl = d / "photo (1).jxl"
    r = rec._run_captured(["cjxl", str(orig), str(jxl), "--lossless_jpeg=1",
                           "--compress_boxes=0"], 300)
    assert r.returncode == 0, r.stderr
    (d / "checksums.md5").write_text(f"{'0' * 32}  {jxl.name}\n", encoding="utf-8")
    r = _script("jxl_jpeg_transcoder.py", jxl, "--repair-jbrd", "--dry-run")
    out = " ".join((r.stdout + r.stderr).split())
    assert r.returncode == 1, out[-2000:]
    assert "REBUILDS ANOTHER JPEG" in out and "1 OK" not in out, out[-2000:]


@pytest.mark.skipif(os.name != "nt", reason="msvcrt byte-range locks are Windows-only")
def test_t1b_an_unreadable_archive_is_not_without_jbrd(tmp_path):
    import msvcrt
    d = tmp_path / HOSTILE
    d.mkdir(parents=True)
    jxl = d / "photo (1).jxl"
    jxl.write_bytes(b"\x00\x00\x00\x0cJXL \x0d\x0a\x87\x0a" + b"\x00" * 64)
    with open(jxl, "r+b") as f:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 76)
        try:
            r = _script("jxl_jpeg_transcoder.py", jxl, "--repair-jbrd", "--dry-run")
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 76)
    out = " ".join((r.stdout + r.stderr).split())
    assert r.returncode == 1 and "could not be read" in out, out[-2000:]


# --- A5: a 16-bit PNG does not replace a master that carries TIFF tags ---------------

@needs_codecs
@needs_magick
def test_a5_png_that_drops_the_tiff_tags_does_not_replace_the_master(tmp_path):
    """A master made from a TIFF with a scanner's private tag (an IPTC block,
    Photoshop resources alike) carries it as a jxlphoto-tifftag token, which a
    PNG never holds (#646): rc6 deleted the master for a 16-bit PNG, and the
    tag was gone once the TIFF was (the targeted review's A5)."""
    d = tmp_path / HOSTILE / PERCENT
    d.mkdir(parents=True)
    rng = np.random.default_rng(3)
    tifffile.imwrite(str(d / "scan 1.tif"),
                     (rng.random((60, 80, 3)) * 65535).astype(np.uint16), photometric="rgb",
                     extratags=[(50000, "s", 0, "scanner data", True)])
    r = _script("jxl_tiff_encoder.py", d, "--mode", "1", "--effort", "1", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    jxl = d / "converted_jxl" / "scan 1.jxl"
    assert "jxlphoto-tifftag:50000" in _relation(jxl)
    r = _script("jxl_jpeg_transcoder.py", jxl.parent, "--mode", "0", "--force-convert",
                "--decode", "--format", "png", "--delete-source", "--delete-confirm-off",
                "--workers", "1")
    out = " ".join((r.stdout + r.stderr).split())
    assert jxl.with_suffix(".png").exists(), out[-3000:]
    assert jxl.exists(), out[-3000:]
    assert "carries TIFF tags a PNG cannot hold" in out, out[-3000:]


# --- A3: auto mode's colour conversion reaches the JPEG archives --------------------

def _icc_description(path: Path):
    r = rec._run_exiftool_argfile(["-s3", "-ICC_Profile:ProfileDescription", str(path)],
                                  timeout=60)
    return (r.stdout or "").strip()


@needs_codecs
@needs_magick
def test_a3_auto_to_srgb_converts_a_jpeg_archive(tmp_path):
    """Auto mode with --to-srgb sent a jbrd JXL to the bit-exact recovery: the
    "sRGB" export of a ProPhoto JPEG archive came back in ProPhoto, without a
    word (the targeted review's A3). It is converted now, like a resize."""
    from PIL import Image
    d = tmp_path / HOSTILE / PERCENT
    src = d / "src"
    src.mkdir(parents=True)
    icc = rec._build_matrix_trc_icc("ProPhoto (test)",
                                    [(0.7347, 0.2653), (0.1596, 0.8404), (0.0366, 0.0001)],
                                    (0.3457, 0.3585), 1.8)
    y, x = np.mgrid[0:120, 0:160]
    arr = np.stack([x % 256, y * 2 % 256, (x + y) % 256], axis=2).astype(np.uint8)
    jpg = src / "pro photo (1).jpg"
    Image.fromarray(arr).save(str(jpg), quality=92, icc_profile=icc)
    archives = d / "archives"
    archives.mkdir()
    r = rec._run_captured(["cjxl", str(jpg), str(archives / "pro photo (1).jxl"),
                           "--lossless_jpeg=1"], 300)
    assert r.returncode == 0, r.stderr
    out = d / "export [sRGB]"
    r = _script("jxl_jpeg_transcoder.py", archives, out, "--mode", "2", "--from-jxl",
                "--to-srgb", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    made = out / "pro photo (1).jpg"
    assert made.exists(), (r.stdout + r.stderr)[-3000:]
    desc = _icc_description(made)
    assert desc != "ProPhoto (test)", desc
    assert "srgb" in desc.lower() or desc == "", desc
    assert (archives / "pro photo (1).jxl").exists()


# --- A1: the documented --sync resume of an auto folder runs ------------------------

@needs_codecs
def test_a1_the_documented_sync_resume_runs(tmp_path):
    """An auto run in place (mode 8) archived a.jpg, then b.jpg was added: the
    README's --sync resume aborted with "Output would overwrite another input"
    (a.jxl -> a.jpg), exit 2, on every later run (the targeted review's A1)."""
    d = tmp_path / HOSTILE / PERCENT
    a = _jpeg_photo(d / "day 1" / "a.jpg", seed=3)
    r = _script("jxl_jpeg_transcoder.py", d, "--mode", "8", "--workers", "1")
    assert r.returncode == 0 and (d / "day 1" / "a.jxl").exists(), (r.stdout + r.stderr)[-3000:]
    a_md5, jxl_bytes = _md5(a), (d / "day 1" / "a.jxl").read_bytes()
    _jpeg_photo(d / "day 1" / "b.jpg", seed=7)
    r = _script("jxl_jpeg_transcoder.py", d, "--mode", "8", "--sync", "--workers", "1")
    out = (r.stdout + r.stderr)[-3000:]
    assert r.returncode == 0, out
    assert (d / "day 1" / "b.jxl").exists(), out
    assert _md5(a) == a_md5 and (d / "day 1" / "a.jxl").read_bytes() == jxl_bytes
    r = _script("jxl_jpeg_transcoder.py", d, "--mode", "8", "--sync", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]


# --- A4: the transcoder's own outputs are not archived again ------------------------

@needs_codecs
def test_a4_a_renamed_recovered_folder_is_not_archived_again(tmp_path):
    """With RECOVERED_JPEG_FOLDER = "JPEG_back" (the settings file), every auto
    run archived the last run's recovered JPEGs again, one folder deeper: only
    the shipped names were skipped (the targeted review's A4)."""
    import json
    d = tmp_path / HOSTILE / "lib"
    _jpeg_photo(d / "shoot (1)" / "wall 14.jpg")
    ov = tmp_path / "settings [x].json"
    ov.write_text(json.dumps({"jxl_jpeg_transcoder": {"RECOVERED_JPEG_FOLDER": "JPEG_back"}}),
                  encoding="utf-8")
    for _ in range(4):
        r = _script("jxl_jpeg_transcoder.py", d, "--mode", "3", "--workers", "1",
                    env={"JXLPHOTO_SETTINGS": str(ov)})
        assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    nested = sorted(str(p.relative_to(d)) for p in d.rglob("*")
                    if p.is_dir() and p.parent.name == "JPEG_back")
    assert nested == [], nested


@needs_codecs
@needs_magick
def test_a4_the_toolkits_own_jpeg_export_is_not_archived(tmp_path):
    """A resized JPEG export of a master (jxlphoto-derived) in _EXPORT/JPG_web
    was archived by the next mode-6 JPEG run and, with --delete-source,
    deleted from the export folder (the targeted review's A4)."""
    lib = tmp_path / HOSTILE / "lib"
    tiffs = lib / "_EXPORT" / "TIFF16"
    tiffs.mkdir(parents=True)
    tifffile.imwrite(str(tiffs / "DSC_0016 (2).tif"),
                     np.random.default_rng(5).integers(0, 65535, (120, 160, 3),
                                                       dtype=np.uint16), photometric="rgb")
    masters = lib / "_EXPORT" / "16B_JXL"
    r = _script("jxl_tiff_encoder.py", tiffs, masters, "--mode", "0", "--effort", "1",
                "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    r = _script("jxl_jpeg_transcoder.py", lib, "--mode", "7", "--export-subfolder", "16B_JXL",
                "--export-jpeg-folder", "JPG_web", "--force-convert", "--decode",
                "--resize-long", "80", "--workers", "1")
    export = lib / "_EXPORT" / "JPG_web" / "DSC_0016 (2).jpg"
    assert export.exists(), (r.stdout + r.stderr)[-3000:]
    r = _script("jxl_jpeg_transcoder.py", lib, "--mode", "6", "--force-transcode",
                "--delete-source", "--delete-confirm-off", "--workers", "1")
    out = " ".join((r.stdout + r.stderr).split())
    assert export.exists(), out[-3000:]
    assert not list(lib.rglob("JXL_jpeg/*.jxl")), out[-3000:]
    assert "Ignored 1 derivative file(s)" in out, out[-3000:]


@needs_codecs
def test_a4_the_xmp_reader_finds_the_mark_in_a_jpeg_and_a_png(tmp_path):
    from PIL import Image
    tr = __import__("jxl_jpeg_transcoder")
    d = tmp_path / HOSTILE / PERCENT
    d.mkdir(parents=True)
    arr = np.zeros((16, 16, 3), dtype=np.uint8)
    needle = b"jxlphoto-derived:"
    for ext in ("jpg", "png"):
        plain, marked = d / f"plain (1).{ext}", d / f"marked (1).{ext}"
        Image.fromarray(arr).save(str(plain))
        Image.fromarray(arr).save(str(marked))
        r = rec._run_exiftool_argfile(["-overwrite_original",
                                       "-XMP-dc:Relation+=jxlphoto-derived:sRGB@long80/q95",
                                       str(marked)], timeout=60)
        assert r.returncode == 0, r.stderr
        assert tr._image_xmp_mentions(marked, needle) is True, ext
        assert tr._image_xmp_mentions(plain, needle) is False, ext
    broken = d / "cut (1).jpg"
    broken.write_bytes((d / "marked (1).jpg").read_bytes()[:6])
    assert tr._image_xmp_mentions(broken, needle) is None


# --- #745 (the rc7 review's M1): the TIFF decoder keeps a JPEG archive -------------

@needs_codecs
def test_745_the_decoder_never_deletes_a_jpeg_archive(tmp_path):
    """A decode to TIFF with --delete-source deleted a lossless JPEG archive
    (jbrd) after writing a TIFF of its pixels: the original JPEG's bytes went
    with it (rc6 too; the class of #692/#733 in the transcoder)."""
    d = tmp_path / HOSTILE / PERCENT
    orig = _jpeg_photo(d / "src" / "camera 14.jpg")
    arch = d / "archives"
    arch.mkdir()
    jxl = arch / "camera 14.jxl"
    r = rec._run_captured(["cjxl", str(orig), str(jxl), "--lossless_jpeg=1"], 300)
    assert r.returncode == 0, r.stderr
    (arch / "checksums.md5").write_text(f"{_md5(orig)}  {jxl.name}\n", encoding="utf-8")
    r = _script("jxl_tiff_decoder.py", arch, d / "TIFF", "--mode", "0", "--workers", "1",
                "--delete-source", "--delete-confirm-off")
    out = " ".join((r.stdout + r.stderr).split())
    assert (d / "TIFF" / "camera 14.tif").exists(), out[-3000:]
    assert jxl.exists(), out[-3000:]
    assert "a lossless JPEG archive" in out, out[-3000:]


# --- A1 after the rc7 review (S1, S6) -----------------------------------------------

def _auto_archive_with_lossy_neighbour(d: Path):
    """a (1).jpg archived in place (its checksums.md5 line written), and a
    lossy m (2).jxl next to it, so the convert group has work."""
    a = _jpeg_photo(d / "a (1).jpg", seed=3)
    r = _script("jxl_jpeg_transcoder.py", d, "--mode", "0", "--workers", "1")
    assert r.returncode == 0 and (d / "a (1).jxl").exists(), (r.stdout + r.stderr)[-3000:]
    png = d.parent / "m.png"
    from PIL import Image
    Image.fromarray(np.full((40, 60, 3), 90, np.uint8)).save(str(png))
    r = rec._run_captured(["cjxl", str(png), str(d / "m (2).jxl"), "-d", "1"], 300)
    assert r.returncode == 0, r.stderr
    return a


@needs_codecs
@needs_magick
@pytest.mark.parametrize("sync", [True, False], ids=["sync", "plain"])
def test_a1_an_archive_beside_its_jpeg_is_left_out_of_a_colour_run(tmp_path, sync):
    """The review's S1: with --to-srgb the archive sits in the convert list,
    which A1 did not clean — its copy would land on its own original JPEG:
    REFUSED, exit 1, on every run, after "neither file is written"."""
    d = tmp_path / HOSTILE / PERCENT / "day 1"
    d.mkdir(parents=True)
    a = _auto_archive_with_lossy_neighbour(d)
    a_md5, arch = _md5(a), (d / "a (1).jxl").read_bytes()
    args = ["--mode", "0", "--to-srgb", "--workers", "1"] + (["--sync"] if sync else [])
    r = _script("jxl_jpeg_transcoder.py", d, *args)
    out = " ".join((r.stdout + r.stderr).split())
    assert r.returncode == 0, out[-3000:]
    assert "REFUSED" not in out, out[-3000:]
    assert _md5(a) == a_md5 and (d / "a (1).jxl").read_bytes() == arch
    assert (d / "m (2).jpg").exists(), out[-3000:]


@needs_codecs
def test_a1_a_plain_auto_run_reads_no_jpeg_to_prove_a_pair(tmp_path, monkeypatch):
    """The review's S6: every auto run hashed every JPEG beside its archive,
    even a plain one, where no collision can arise. Only a pair one of whose
    directions would write is proven now."""
    import jxl_jpeg_transcoder as tr
    d = tmp_path / HOSTILE / "day 1"
    d.mkdir(parents=True)
    _jpeg_photo(d / "a (1).jpg", seed=3)
    r = _script("jxl_jpeg_transcoder.py", d, "--mode", "0", "--workers", "1")
    assert r.returncode == 0 and (d / "a (1).jxl").exists(), (r.stdout + r.stderr)[-3000:]
    hashed = []
    real = tr.md5_of_file
    monkeypatch.setattr(tr, "md5_of_file", lambda p: hashed.append(str(p)) or real(p))
    monkeypatch.setattr(sys, "argv", ["jxl_jpeg_transcoder.py", str(d), "--mode", "0",
                                      "--workers", "1"])
    try:
        tr.main()
    except SystemExit as e:
        assert not e.code, e.code
    assert not [h for h in hashed if h.lower().endswith(".jpg")], hashed


# --- A4 after the rc7 review (S2) ---------------------------------------------------

@needs_codecs
@needs_magick
def test_a4_an_export_beside_a_camera_jpeg_of_its_name_stays_out_on_every_run(tmp_path):
    """The review's S2: the export mark was read only for a pair whose output
    was missing or older. A camera JPEG and an export of one name: run 1 left
    the export out, run 2 (the camera JPEG's archive now on that output)
    kept it and aborted ("Duplicate output destination", exit 2)."""
    lib = tmp_path / HOSTILE / "lib"
    tiffs = lib / "_EXPORT" / "TIFF16"
    tiffs.mkdir(parents=True)
    tifffile.imwrite(str(tiffs / "DSC_0016 (2).tif"),
                     np.random.default_rng(5).integers(0, 65535, (120, 160, 3),
                                                       dtype=np.uint16), photometric="rgb")
    r = _script("jxl_tiff_encoder.py", tiffs, lib / "_EXPORT" / "16B_JXL", "--mode", "0",
                "--effort", "1", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    r = _script("jxl_jpeg_transcoder.py", lib, "--mode", "7", "--export-subfolder", "16B_JXL",
                "--export-jpeg-folder", "JPG_web", "--force-convert", "--decode",
                "--resize-long", "80", "--workers", "1")
    assert (lib / "_EXPORT" / "JPG_web" / "DSC_0016 (2).jpg").exists(), (r.stdout + r.stderr)[-3000:]
    _jpeg_photo(lib / "_EXPORT" / "JPG_only" / "DSC_0016 (2).jpg", seed=11)   # a camera JPEG
    for run in (1, 2, 3):
        r = _script("jxl_jpeg_transcoder.py", lib, "--mode", "6", "--force-transcode",
                    "--workers", "1")
        out = " ".join((r.stdout + r.stderr).split())
        assert r.returncode == 0, (run, out[-3000:])
        assert "Duplicate output destination" not in out, (run, out[-3000:])


# --- A4 after the rc7 review (S3): mode 4's decode folders as tokens ---------------

@needs_codecs
@pytest.mark.parametrize("folder", ["shoot JXL", "shoot-JXL", "JXL_shoot"])
def test_a4_mode4_decode_folders_are_never_archived_again(tmp_path, folder):
    """The review's S3: only a folder ENDING in _JPEG_recovered was known;
    mode 4 puts the token anywhere _replace_suffix_token finds "JXL" (a space,
    a '-', the start), and those layouts grew a new folder on every run."""
    lib = tmp_path / HOSTILE / "lib"
    src = _jpeg_photo(tmp_path / "wall 14.jpg")
    (lib / folder).mkdir(parents=True)
    r = rec._run_captured(["cjxl", str(src), str(lib / folder / "wall 14.jxl"),
                           "--lossless_jpeg=1"], 300)
    assert r.returncode == 0, r.stderr
    seen = []
    for _ in range(4):
        r = _script("jxl_jpeg_transcoder.py", lib, "--mode", "4", "--workers", "1")
        assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
        seen.append(sorted(str(p.relative_to(lib)) for p in lib.rglob("*") if p.is_dir()))
    assert seen[1] == seen[3], seen


# --- F3 and F1 after the rc7 review (S4, S5, S7) ------------------------------------

def _epson_grey_icc() -> bytes:
    """EPSON's "Gray - Gamma 1.8", from the owner's real grey scan: a grey
    profile JPEG XL carries natively, so the probe says "embed" for it."""
    with tifffile.TiffFile(str(EPSON_GRAY)) as tf:
        return bytes(tf.pages[0].tags[34675].value)


@needs_codecs
@pytest.mark.skipif(not EPSON_GRAY.is_file(), reason="needs the owner's EPSON grey scan")
def test_f3_an_rgb_page_with_a_grey_profile_still_encodes(tmp_path):
    """The review's S4: once the probe says "embed" for a grey profile, an RGB
    page carrying it went to cjxl as a colour PNG with a grey iCCP, which cjxl
    refuses: the lossy encode failed (rc6 encoded it, profile in XMP)."""
    src = tmp_path / HOSTILE / "TIFF"
    src.mkdir(parents=True)
    tifffile.imwrite(str(src / "rgb with grey profile (1).tif"),
                     np.random.default_rng(2).integers(0, 65535, (64, 96, 3), dtype=np.uint16),
                     photometric="rgb", iccprofile=_epson_grey_icc())
    out = tmp_path / HOSTILE / "JXL"
    r = _script("jxl_tiff_encoder.py", src, out, "--mode", "0", "--distance", "1",
                "--effort", "1", "--workers", "1", "--icc-cache-dir", tmp_path / "icc [cache]")
    log = " ".join((r.stdout + r.stderr).split())
    assert r.returncode == 0, log[-3000:]
    assert (out / "rgb with grey profile (1).jxl").exists()
    assert "grayscale on colour pixels" in log, log[-3000:]


@needs_codecs
@pytest.mark.skipif(not EPSON_GRAY.is_file(), reason="needs the owner's EPSON grey scan")
def test_f3_the_16_bit_grey_probe_sees_a_darker_decode(tmp_path, monkeypatch):
    """The review's S5: a 16-bit grey decode was read through convert("RGB"),
    which clips at 255 — the 16-bit half of the grey probe could not fail on
    brightness. A decode planted 4x darker must make it say "unsafe"."""
    from PIL import Image
    import jxl_tiff_encoder as enc
    real = enc._run_captured

    def darker(cmd, *a, **k):
        r = real(cmd, *a, **k)
        if Path(str(cmd[0])).stem.lower() == "djxl" and str(cmd[2]).endswith("out.png"):
            p = Path(str(cmd[2]))
            with Image.open(p) as im:
                arr = np.asarray(im, dtype=np.uint32)
            Image.fromarray((arr // 4).astype(np.uint16)).save(str(p))
        return r
    monkeypatch.setattr(enc, "_run_captured", darker)
    assert enc._cautious_test_icc_depth(_epson_grey_icc(), 16, 0.5) is False
    monkeypatch.setattr(enc, "_run_captured", real)
    assert enc._cautious_test_icc_depth(_epson_grey_icc(), 16, 0.5) is True


@needs_codecs
def test_f1_no_adopt_scan_still_refuses_another_capture_time(tmp_path):
    """The review's S7: --no-adopt-scan skipped the capture-time check too,
    and the pixel refusal's own hint sends the user to that flag."""
    root = tmp_path / HOSTILE
    a = _smooth_photo(1)
    m = _legacy_archive(root, a, "2025:01:18 10:00:00")
    before = m.read_bytes()
    cam_b = root / "card B"
    cam_b.mkdir(parents=True)
    tif = cam_b / "DSC_0001.tif"
    tifffile.imwrite(str(tif), np.clip(a.astype(np.float64) + np.random.default_rng(9).normal(
        0, 60, a.shape), 0, 65535).astype(np.uint16), photometric="rgb")
    _set_capture_time(tif, "2025:01:18 10:00:01")
    os.utime(tif, (1_700_000_000, 1_700_000_000))
    r = _script("jxl_tiff_encoder.py", cam_b, root / "masters [flat]", "--mode", "2",
                "--effort", "1", "--workers", "1", "--delete-source", "--delete-skipped",
                "--provenance", "adopt", "--no-adopt-scan", "--delete-confirm-off")
    log = " ".join((r.stdout + r.stderr).split())
    assert tif.exists(), log[-3000:]
    assert m.read_bytes() == before
    assert "another photo: its DateTimeOriginal" in log, log[-3000:]


# --- after the second review of rc7 (R1, R2, the --from-jxl colour case, lossless S4) ---

@needs_codecs
@needs_magick
def test_a4_an_export_png_beside_a_camera_jpeg_stays_out_in_auto_mode(tmp_path):
    """R1: auto mode plans its JPEGs and PNGs apart, so the shared output of a
    camera JPEG and a PNG export of one name was seen by neither group's
    derivative filter: every auto run after the first aborted on the
    duplicate."""
    lib = tmp_path / HOSTILE / "lib"
    tiffs = lib / "_EXPORT" / "TIFF16"
    tiffs.mkdir(parents=True)
    tifffile.imwrite(str(tiffs / "DSC_0016 (2).tif"),
                     np.random.default_rng(5).integers(0, 65535, (120, 160, 3),
                                                       dtype=np.uint16), photometric="rgb")
    r = _script("jxl_tiff_encoder.py", tiffs, lib / "_EXPORT" / "16B_JXL", "--mode", "0",
                "--effort", "1", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    r = _script("jxl_jpeg_transcoder.py", lib, "--mode", "7", "--export-subfolder", "16B_JXL",
                "--export-jpeg-folder", "PNG_web", "--force-convert", "--decode",
                "--format", "png", "--resize-long", "80", "--workers", "1")
    assert (lib / "_EXPORT" / "PNG_web" / "DSC_0016 (2).png").exists(), (r.stdout + r.stderr)[-3000:]
    # The master goes too: auto mode would decode it and the camera JPEG's
    # archive (one name) into one JPEG — a duplicate the guard rightly stops.
    shutil.rmtree(tiffs)
    shutil.rmtree(lib / "_EXPORT" / "16B_JXL")
    _jpeg_photo(lib / "_EXPORT" / "JPG_only" / "DSC_0016 (2).jpg", seed=11)
    for run in (1, 2, 3):
        r = _script("jxl_jpeg_transcoder.py", lib, "--mode", "6", "--workers", "1")
        out = " ".join((r.stdout + r.stderr).split())
        assert r.returncode == 0, (run, out[-3000:])
        assert "Duplicate output destination" not in out, (run, out[-3000:])


@needs_codecs
@pytest.mark.parametrize("setting,mode,folder", [
    ("JPEG_SUFFIX_REPLACE_DEC", "4", "shoot_JPEG"),
    ("JPEG_SIBLING_FOLDER", "3", "shoot/JPEG"),
])
def test_a4_a_symmetric_setting_never_hides_the_users_jpeg_folders(tmp_path, setting, mode,
                                                                   folder):
    """R2: the folders skipped as the tool's own came from the settings for
    every mode: with JPEG_SUFFIX_REPLACE_DEC = "JPEG" (mode 4 shoot_JPEG <->
    shoot_JXL) or JPEG_SIBLING_FOLDER = "JPEG", the user's own JPEG folders
    were skipped — nothing archived, exit 0."""
    import json
    lib = tmp_path / HOSTILE / "lib"
    _jpeg_photo(lib / folder / "a (1).jpg")
    ov = tmp_path / "settings [x].json"
    ov.write_text(json.dumps({"jxl_jpeg_transcoder": {setting: "JPEG"}}), encoding="utf-8")
    r = _script("jxl_jpeg_transcoder.py", lib, "--mode", mode, "--workers", "1",
                env={"JXLPHOTO_SETTINGS": str(ov)})
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    made = [p.relative_to(lib) for p in lib.rglob("a (1).jxl")]
    assert made, (r.stdout + r.stderr)[-3000:]


@needs_codecs
@needs_magick
def test_a3_a_colour_run_from_jxl_leaves_an_archive_beside_its_jpeg(tmp_path):
    """The second review: with --from-jxl the JPEG is not planned, so no pair
    is proven; the archive's sRGB copy would land on its own original JPEG,
    which the derivative check refused (an error) on every run."""
    d = tmp_path / HOSTILE / PERCENT / "day 1"
    d.mkdir(parents=True)
    a = _jpeg_photo(d / "a (1).jpg", seed=3)
    r = _script("jxl_jpeg_transcoder.py", d, "--mode", "0", "--workers", "1")
    assert r.returncode == 0 and (d / "a (1).jxl").exists(), (r.stdout + r.stderr)[-3000:]
    a_md5 = _md5(a)
    r = _script("jxl_jpeg_transcoder.py", d, "--mode", "0", "--from-jxl", "--to-srgb",
                "--workers", "1")
    out = " ".join((r.stdout + r.stderr).split())
    assert r.returncode == 0 and "REFUSED" not in out, out[-3000:]
    assert _md5(a) == a_md5


@needs_codecs
@pytest.mark.skipif(not EPSON_GRAY.is_file(), reason="needs the owner's EPSON grey scan")
def test_f3_an_rgb_page_with_a_grey_profile_encodes_lossless_too(tmp_path):
    """The second review: S4's guard also fixes the lossless encode of an RGB
    page carrying a grey profile (rc6 failed it: cjxl refuses that PNG)."""
    src = tmp_path / HOSTILE / "TIFF"
    src.mkdir(parents=True)
    tifffile.imwrite(str(src / "rgb with grey profile (1).tif"),
                     np.random.default_rng(2).integers(0, 65535, (64, 96, 3), dtype=np.uint16),
                     photometric="rgb", iccprofile=_epson_grey_icc())
    out = tmp_path / HOSTILE / "JXL"
    r = _script("jxl_tiff_encoder.py", src, out, "--mode", "0", "--distance", "0",
                "--effort", "1", "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    assert (out / "rgb with grey profile (1).jxl").exists()
