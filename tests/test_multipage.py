#!/usr/bin/env python3
"""
Quick multi-page TIFF regression test.

Creates a synthetic multi-page TIFF (2 real pages + 1 thumbnail),
runs the encoder in all multi-page modes, and verifies the decoder
reconstructs the expected number of pages.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import tifffile


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jxl_tiff_encoder import USE_RAM_FOR_PNG, apply_d50_policy, setup_logger
from jxl_tiff_decoder import read_png_to_numpy


setup_logger()


def create_multipage_tiff(path: Path):
    """Create a TIFF with page 0 (real), page 1 (thumbnail), page 2 (real)."""
    img0 = np.random.randint(0, 65535, (100, 100, 3), dtype=np.uint16)
    img1 = np.random.randint(0, 255, (50, 50, 3), dtype=np.uint8)
    img2 = np.random.randint(0, 65535, (80, 80, 3), dtype=np.uint16)

    with tifffile.TiffWriter(str(path)) as tif:
        tif.write(img0, photometric='rgb')
        tif.write(img1, photometric='rgb', subfiletype=tifffile.FILETYPE.REDUCEDIMAGE)
        tif.write(img2, photometric='rgb')


SCRIPT_DIR = Path(__file__).resolve().parent.parent


def run_encoder(input_dir: Path, output_dir: Path, *extra_args) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable, str(SCRIPT_DIR / "jxl_tiff_encoder.py"),
        str(input_dir), str(output_dir),
        "--mode", "2", "--distance", "0", "--effort", "1", "--no-ram", "--workers", "1"
    ] + list(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def run_decoder(input_dir: Path, output_dir: Path, *extra_args) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable, str(SCRIPT_DIR / "jxl_tiff_decoder.py"),
        str(input_dir), str(output_dir),
        "--mode", "2", "--depth", "16", "--compression", "zip", "--workers", "1"
    ] + list(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def check_pages(tiff_path: Path, expected_pages: int, expected_reduced: list):
    with tifffile.TiffFile(str(tiff_path)) as tif:
        assert len(tif.pages) == expected_pages, f"expected {expected_pages} pages, got {len(tif.pages)}"
        for i, page in enumerate(tif.pages):
            assert page.is_reduced == expected_reduced[i], (
                f"page {i} reduced flag mismatch: expected {expected_reduced[i]}, got {page.is_reduced}"
            )


def create_singlepage_tiff_with_metadata(path: Path):
    """Create a single-page TIFF and stamp a Make/Software tag via exiftool."""
    img = np.random.randint(0, 65535, (64, 64, 3), dtype=np.uint16)
    tifffile.imwrite(str(path), img, photometric='rgb')
    subprocess.run(
        ["exiftool", "-overwrite_original", "-Make=TESTMAKE", "-Software=TestSoftware/1.0",
         str(path)],
        capture_output=True
    )


def read_tag(tiff_path: Path, tag: str) -> str:
    r = subprocess.run(
        ["exiftool", "-s", "-s", "-s", f"-{tag}", str(tiff_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return r.stdout.strip() if r.returncode == 0 else ""


def get_icc_bytes_from_jxl(jxl_path: Path) -> bytes:
    """Extract the ICC:<base64> marker from XMP-xmp:CreatorTool."""
    r = subprocess.run(
        ["exiftool", "-s", "-s", "-s", "-XMP-xmp:CreatorTool", str(jxl_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if r.returncode != 0 or not r.stdout:
        return b""
    content = r.stdout.strip()
    # Format is "... | ICC:<base64>" or "ICC:<base64>"
    for part in content.split("|"):
        part = part.strip()
        if part.startswith("ICC:"):
            b64 = part[4:].strip()
            try:
                import base64
                return base64.b64decode(b64)
            except Exception:
                return b""
    return b""


def get_page_icc(tiff_path: Path, page_idx: int) -> bytes:
    """Return the ICC bytes from a specific TIFF page, or b'' if absent."""
    with tifffile.TiffFile(str(tiff_path)) as tif:
        tag = tif.pages[page_idx].tags.get(34675)
        if tag is None or not tag.value:
            return b""
        return bytes(tag.value)


def find_test_iccs() -> tuple:
    """Generate two distinct ICC profiles deterministically via PIL.ImageCms.
    Returns (icc_a_bytes, icc_b_bytes)."""
    from PIL import ImageCms
    icc_a = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    # Create icc_b by altering the description text of icc_a, keeping it a
    # valid RGB-like profile so libpng/cjxl accept it during encoding.
    icc_b = bytearray(icc_a)
    text_a = 'sRGB built-in'.encode('utf-16-be')
    text_b = 'sRGB test-v2 '.encode('utf-16-be')
    idx = icc_b.find(text_a)
    if idx != -1 and len(text_a) == len(text_b):
        icc_b[idx:idx + len(text_b)] = text_b
    else:
        # Fallback: just flip a few bytes if the description text is unexpected
        icc_b[100:104] = b'\x00\x00\x00\x01'
    assert icc_a != bytes(icc_b), "generated ICC profiles must be distinct"
    return icc_a, bytes(icc_b)


def main():
    with tempfile.TemporaryDirectory(prefix="jxl_mp_test_") as tmp:
        tmp = Path(tmp)
        input_dir = tmp / "input"
        input_dir.mkdir()
        tiff_path = input_dir / "multipage.tif"
        create_multipage_tiff(tiff_path)

        print("Created synthetic multi-page TIFF:")
        with tifffile.TiffFile(str(tiff_path)) as tif:
            for i, p in enumerate(tif.pages):
                print(f"  page {i}: shape={p.shape}, reduced={p.is_reduced}")

        # ---- ignore mode ----
        enc_out = tmp / "enc_ignore"
        r = run_encoder(input_dir, enc_out, "--multipage-mode", "ignore")
        assert r.returncode == 0, f"encoder ignore failed:\n{r.stderr}"
        assert (enc_out / "multipage.jxl").exists()
        assert not (enc_out / "multipage_page2.jxl").exists()

        # ---- skip mode ----
        enc_out = tmp / "enc_skip"
        r = run_encoder(input_dir, enc_out, "--multipage-mode", "skip")
        assert r.returncode == 0, f"encoder skip failed:\n{r.stderr}"
        assert not (enc_out / "multipage.jxl").exists()
        assert "SKIP multi-page TIFF" in r.stdout

        # ---- split exclude ----
        enc_out = tmp / "enc_split_ex"
        r = run_encoder(input_dir, enc_out, "--multipage-mode", "split", "--thumbnail-mode", "exclude")
        assert r.returncode == 0, f"encoder split exclude failed:\n{r.stderr}"
        assert (enc_out / "multipage.jxl").exists()
        assert (enc_out / "multipage_page2.jxl").exists()
        assert not (enc_out / "multipage_page1_thumbnail.jxl").exists()

        # ---- split include ----
        enc_out = tmp / "enc_split_in"
        r = run_encoder(input_dir, enc_out, "--multipage-mode", "split", "--thumbnail-mode", "include")
        assert r.returncode == 0, f"encoder split include failed:\n{r.stderr}"
        assert (enc_out / "multipage.jxl").exists()
        assert (enc_out / "multipage_page2.jxl").exists()
        assert (enc_out / "multipage_page1_thumbnail.jxl").exists()

        # ---- decoder include ----
        dec_out = tmp / "dec_include"
        r = run_decoder(enc_out, dec_out, "--thumbnail-handling", "include")
        assert r.returncode == 0, f"decoder include failed:\n{r.stderr}"
        check_pages(dec_out / "multipage.tif", 3, [False, True, False])

        # ---- decoder ignore ----
        dec_out = tmp / "dec_ignore"
        r = run_decoder(enc_out, dec_out, "--thumbnail-handling", "ignore")
        assert r.returncode == 0, f"decoder ignore failed:\n{r.stderr}"
        check_pages(dec_out / "multipage.tif", 2, [False, False])

        # ---- single-page metadata roundtrip (guards against preview wiping EXIF) ----
        sp_in = tmp / "sp_input"
        sp_in.mkdir()
        create_singlepage_tiff_with_metadata(sp_in / "single.tif")
        sp_jxl = tmp / "sp_jxl"
        r = run_encoder(sp_in, sp_jxl, "--multipage-mode", "ignore")
        assert r.returncode == 0, f"single-page encode failed:\n{r.stderr}"
        sp_tif = tmp / "sp_tif"
        r = run_decoder(sp_jxl, sp_tif)
        assert r.returncode == 0, f"single-page decode failed:\n{r.stderr}"
        make = read_tag(sp_tif / "single.tif", "Make")
        software = read_tag(sp_tif / "single.tif", "Software")
        assert make == "TESTMAKE", f"Make not preserved through roundtrip: got {make!r}"
        assert software.startswith("TestSoftware"), f"Software not preserved: got {software!r}"
        rel_sp = read_tag(sp_tif / "single.tif", "XMP-dc:Relation")
        assert "jxlphoto-depth" not in rel_sp, f"depth marker leaked into single-page TIFF Relation: {rel_sp!r}"

        # --depth 8 must still work for single-page files
        sp_tif_8 = tmp / "sp_tif_8"
        r = run_decoder(sp_jxl, sp_tif_8, "--depth", "8")
        assert r.returncode == 0, f"single-page depth 8 decode failed:\n{r.stderr}"
        with tifffile.TiffFile(str(sp_tif_8 / "single.tif")) as tif:
            assert tif.pages[0].bitspersample == 8, "--depth 8 should produce 8-bit output"

        # ---- independent files must NOT be merged (no marker => standalone) ----
        ind_in = tmp / "ind_input"
        ind_in.mkdir()
        tifffile.imwrite(str(ind_in / "scan.tif"),
                         np.random.randint(0, 65535, (40, 40, 3), dtype=np.uint16), photometric='rgb')
        tifffile.imwrite(str(ind_in / "scan_page2.tif"),
                         np.random.randint(0, 65535, (50, 50, 3), dtype=np.uint16), photometric='rgb')
        ind_jxl = tmp / "ind_jxl"
        r = run_encoder(ind_in, ind_jxl, "--multipage-mode", "ignore")
        assert r.returncode == 0, f"independent encode failed:\n{r.stderr}"
        ind_tif = tmp / "ind_tif"
        r = run_decoder(ind_jxl, ind_tif)
        assert r.returncode == 0, f"independent decode failed:\n{r.stderr}"
        assert (ind_tif / "scan.tif").exists(), "scan.tif missing — files were wrongly merged"
        assert (ind_tif / "scan_page2.tif").exists(), "scan_page2.tif missing — files were wrongly merged"

        # ---- split must preserve a user's existing dc:Relation and not leak
        #      the internal marker into the reconstructed TIFF ----
        rel_in = tmp / "rel_input"
        rel_in.mkdir()
        rel_src = rel_in / "rel.tif"
        with tifffile.TiffWriter(str(rel_src)) as tif:
            tif.write(np.random.randint(0, 65535, (60, 60, 3), dtype=np.uint16), photometric='rgb')
            tif.write(np.random.randint(0, 65535, (50, 50, 3), dtype=np.uint16), photometric='rgb')
        subprocess.run(["exiftool", "-overwrite_original", "-XMP-dc:Relation=UserRelationValue",
                        str(rel_src)], capture_output=True)
        rel_jxl = tmp / "rel_jxl"
        r = run_encoder(rel_in, rel_jxl, "--multipage-mode", "split")
        assert r.returncode == 0, f"relation encode failed:\n{r.stderr}"
        # marker present on the JXL, alongside the user's value
        rel_on_jxl = read_tag(rel_jxl / "rel.jxl", "XMP-dc:Relation")
        assert "UserRelationValue" in rel_on_jxl, "user's Relation lost on JXL"
        assert "jxlphoto-mpg" in rel_on_jxl, "marker missing on split JXL"
        rel_out = tmp / "rel_out"
        r = run_decoder(rel_jxl, rel_out)
        assert r.returncode == 0, f"relation decode failed:\n{r.stderr}"
        rel_final = read_tag(rel_out / "rel.tif", "XMP-dc:Relation")
        assert "UserRelationValue" in rel_final, "user's Relation not restored in TIFF"
        assert "jxlphoto-mpg" not in rel_final, "internal marker leaked into final TIFF"
        assert "jxlphoto-icc" not in rel_final, "inherited-ICC flag leaked into final TIFF"
        assert "jxlphoto-depth" not in rel_final, "depth marker leaked into final TIFF"

        # ---- single-file encoder mode 2 ----
        sf_in = tmp / "sf_input"
        sf_in.mkdir()
        sf_src = sf_in / "single.tif"
        tifffile.imwrite(str(sf_src), np.random.randint(0, 65535, (30, 30, 3), dtype=np.uint16), photometric='rgb')
        sf_out = tmp / "sf_out"
        r = run_encoder(str(sf_src), str(sf_out), "--mode", "2")
        assert r.returncode == 0, f"single-file mode 2 encode failed:\n{r.stderr}"
        assert (sf_out / "single.jxl").exists(), "single-file mode 2 did not create JXL"

        # ---- invalid XMP ICC should not be attached ----
        bad_in = tmp / "bad_input"
        bad_in.mkdir()
        bad_src = bad_in / "bad.tif"
        tifffile.imwrite(str(bad_src), np.random.randint(0, 65535, (30, 30, 3), dtype=np.uint16), photometric='rgb')
        bad_jxl = tmp / "bad_jxl"
        r = run_encoder(bad_in, bad_jxl)
        assert r.returncode == 0, f"bad ICC encode failed:\n{r.stderr}"
        # Inject a fake ICC-like string into CreatorTool
        subprocess.run(["exiftool", "-overwrite_original",
                        "-XMP-xmp:CreatorTool=ICC:AAAAAAABBBBBBBBCCCCCCCCDDDDDDDDEEEEEEEEFFFFFFFFGGGGGGGGHHHHHHHHIIIIIIIIJJJJJJJJ",
                        str(bad_jxl / "bad.jxl")], capture_output=True)
        bad_out = tmp / "bad_out"
        r = run_decoder(bad_jxl, bad_out)
        assert r.returncode == 0, f"bad ICC decode failed:\n{r.stderr}"
        with tifffile.TiffFile(str(bad_out / "bad.tif")) as tif:
            # The fake payload is not a valid ICC, so no ICC tag should be written
            assert tif.pages[0].tags.get(34675) is None, "invalid ICC should not be attached"

        # ---- per-page ICC preservation ----
        # Page 0 gets its own ICC, page 1 has no own ICC (inherits from IFD0),
        # page 2 gets a different ICC. After round trip the tags must match.
        icc_a, icc_b = find_test_iccs()

        icc_in = tmp / "icc_input"
        icc_in.mkdir()
        icc_src = icc_in / "icc.tif"
        with tifffile.TiffWriter(str(icc_src)) as tif:
            tif.write(np.random.randint(0, 65535, (60, 60, 3), dtype=np.uint16),
                      photometric='rgb', iccprofile=icc_a)
            tif.write(np.random.randint(0, 65535, (50, 50, 3), dtype=np.uint16),
                      photometric='rgb')
            tif.write(np.random.randint(0, 65535, (55, 55, 3), dtype=np.uint16),
                      photometric='rgb', iccprofile=icc_b)
        icc_jxl = tmp / "icc_jxl"
        r = run_encoder(icc_in, icc_jxl, "--multipage-mode", "split")
        assert r.returncode == 0, f"per-page ICC encode failed:\n{r.stderr}"

        # Each split JXL must carry the effective ICC (own or inherited)
        icc0 = get_icc_bytes_from_jxl(icc_jxl / "icc.jxl")
        icc1 = get_icc_bytes_from_jxl(icc_jxl / "icc_page1.jxl")
        icc2 = get_icc_bytes_from_jxl(icc_jxl / "icc_page2.jxl")
        assert icc0 == icc_a, "page 0 JXL lost its own ICC"
        assert icc1 == icc_a, "page 1 JXL did not inherit ICC_A"
        assert icc2 == icc_b, "page 2 JXL lost its own ICC"
        # inherited flag should be on page 1 (zero-indexed page_idx=1; output suffix is _page1)
        rel_p1 = read_tag(icc_jxl / "icc_page1.jxl", "XMP-dc:Relation")
        assert "jxlphoto-icc:inherited" in rel_p1, "inherited flag missing on page 1 JXL"

        icc_out = tmp / "icc_out"
        r = run_decoder(icc_jxl, icc_out)
        assert r.returncode == 0, f"per-page ICC decode failed:\n{r.stderr}"
        tif_out = icc_out / "icc.tif"
        assert get_page_icc(tif_out, 0) == icc_a, "page 0 ICC not restored"
        assert get_page_icc(tif_out, 1) == b"", "page 1 inherited ICC should be absent in reconstructed TIFF"
        assert get_page_icc(tif_out, 2) == icc_b, "page 2 ICC not restored"

        # ---- grayscale page + non-zero SubfileType preservation ----
        # SubfileType=4 (MASK) is not writable by tifffile, so we test with the
        # supported PAGE value (2); the encoder still records the original value
        # and the decoder restores the PAGE semantics.
        gray_in = tmp / "gray_input"
        gray_in.mkdir()
        gray_src = gray_in / "gray.tif"
        with tifffile.TiffWriter(str(gray_src)) as tif:
            tif.write(np.random.randint(0, 65535, (60, 60, 3), dtype=np.uint16), photometric='rgb')
            tif.write(np.random.randint(0, 255, (30, 30, 3), dtype=np.uint8),
                      photometric='rgb', subfiletype=tifffile.FILETYPE.REDUCEDIMAGE)
            tif.write(np.random.randint(0, 65535, (50, 50), dtype=np.uint16),
                      photometric='minisblack', subfiletype=tifffile.FILETYPE.PAGE)
        gray_jxl = tmp / "gray_jxl"
        r = run_encoder(gray_in, gray_jxl, "--multipage-mode", "split", "--thumbnail-mode", "include")
        assert r.returncode == 0, f"grayscale encode failed:\n{r.stderr}"
        assert (gray_jxl / "gray_page2.jxl").exists(), "grayscale page JXL missing"

        gray_out = tmp / "gray_out"
        r = run_decoder(gray_jxl, gray_out)
        assert r.returncode == 0, f"grayscale decode failed:\n{r.stderr}"
        tif_gray = gray_out / "gray.tif"
        with tifffile.TiffFile(str(tif_gray)) as tif:
            assert len(tif.pages) == 3, f"expected 3 pages, got {len(tif.pages)}"
            assert tif.pages[0].shape == (60, 60, 3), "page 0 shape wrong"
            assert tif.pages[1].is_reduced, "page 1 should be reduced (thumbnail)"
            assert tif.pages[2].shape == (50, 50), "page 2 should be grayscale 2D"
            assert tif.pages[2].samplesperpixel == 1, "page 2 should have 1 sample"
            assert tif.pages[2].subfiletype == tifffile.FILETYPE.PAGE, "page 2 SubfileType not preserved"
            assert tif.pages[2].tags.get(34675) is None, "page 2 (grayscale inherited) should not get an ICC tag"

        # ---- bit depth policy per page ----
        depth_in = tmp / "depth_input"
        depth_in.mkdir()
        depth_src = depth_in / "depth.tif"
        with tifffile.TiffWriter(str(depth_src)) as tif:
            tif.write(np.random.randint(0, 65535, (40, 40, 3), dtype=np.uint16), photometric='rgb')
            tif.write(np.random.randint(0, 255, (20, 20, 3), dtype=np.uint8),
                      photometric='rgb', subfiletype=tifffile.FILETYPE.REDUCEDIMAGE)
        depth_jxl = tmp / "depth_jxl"
        r = run_encoder(depth_in, depth_jxl, "--multipage-mode", "split", "--thumbnail-mode", "include")
        assert r.returncode == 0, f"depth encode failed:\n{r.stderr}"

        # force16: both pages 16-bit
        depth_out = tmp / "depth_out_force16"
        r = run_decoder(depth_jxl, depth_out, "--depth-policy", "force16")
        assert r.returncode == 0, f"depth decode force16 failed:\n{r.stderr}"
        with tifffile.TiffFile(str(depth_out / "depth.tif")) as tif:
            assert tif.pages[0].bitspersample == 16, "force16: page 0 should be 16-bit"
            assert tif.pages[1].bitspersample == 16, "force16: thumbnail page should be 16-bit"

        # preserve_thumbnails: main 16-bit, thumbnail 8-bit
        depth_out = tmp / "depth_out_preserve_thumbnails"
        r = run_decoder(depth_jxl, depth_out, "--depth-policy", "preserve_thumbnails")
        assert r.returncode == 0, f"depth decode preserve_thumbnails failed:\n{r.stderr}"
        with tifffile.TiffFile(str(depth_out / "depth.tif")) as tif:
            assert tif.pages[0].bitspersample == 16, "preserve_thumbnails: page 0 should be 16-bit"
            assert tif.pages[1].bitspersample == 8, "preserve_thumbnails: thumbnail page should be 8-bit"

        # preserve_original: keep each page's original depth
        depth_out = tmp / "depth_out_preserve_original"
        r = run_decoder(depth_jxl, depth_out, "--depth-policy", "preserve_original")
        assert r.returncode == 0, f"depth decode preserve_original failed:\n{r.stderr}"
        with tifffile.TiffFile(str(depth_out / "depth.tif")) as tif:
            assert tif.pages[0].bitspersample == 16, "preserve_original: page 0 should be 16-bit"
            assert tif.pages[1].bitspersample == 8, "preserve_original: thumbnail page should be 8-bit"

        print("\nAll multi-page tests passed.")


def test_misc():
    # ---- USE_RAM_FOR_PNG default is True ----
    assert USE_RAM_FOR_PNG is True, "USE_RAM_FOR_PNG should default to True"

    # ---- apply_d50_policy leaves tiny ICC profiles unchanged ----
    tiny_icc = b"\x00\x00\x00\x40" + b"acsp" + b"\x00" * 52
    tmp_tif = tempfile.NamedTemporaryFile(suffix=".tif", delete=False)
    tmp_tif.close()
    try:
        tifffile.imwrite(tmp_tif.name, np.zeros((10, 10, 3), dtype=np.uint8), photometric='rgb', software="Capture One 23")
        result = apply_d50_policy(tiny_icc, tmp_tif.name)
        assert result == tiny_icc, "tiny ICC should be returned unchanged"
    finally:
        Path(tmp_tif.name).unlink(missing_ok=True)

    # ---- read_png_to_numpy target_depth=8 converts 16-bit grayscale to 8-bit 2D ----
    # Grayscale PNGs stay single-channel (2D) so single-channel JXLs decode back
    # to single-channel TIFF pages instead of being expanded to RGB.
    img16 = np.random.randint(0, 65535, (20, 20), dtype=np.uint16)
    tmp_png = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_png.close()
    try:
        tifffile.imwrite(tmp_png.name, img16, photometric='minisblack')
        arr8, _ = read_png_to_numpy(tmp_png.name, target_depth=8)
        assert arr8.dtype == np.uint8, f"expected uint8, got {arr8.dtype}"
        assert arr8.shape == (20, 20), f"expected 2D grayscale shape (20, 20), got {arr8.shape}"
    finally:
        Path(tmp_png.name).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
    test_misc()


# ---------------------------------------------------------------------------
# --multipage-mode ignore must SAY it is dropping pages (21st audit round)
# ---------------------------------------------------------------------------

import jxl_tiff_encoder as enc
import jxl_photo as wrapper


def _reset_ignored_counter():
    enc._multipage_ignored["files"] = 0
    enc._multipage_ignored["pages"] = 0


def test_ignore_mode_warns_and_counts_dropped_pages(tmp_path, monkeypatch, caplog):
    """`ignore` encodes page 0 and discards the rest. That used to happen with
    no output at all: the wizard's default never asked, and the encoder never
    counted the pages, so a 2-page TIFF silently became a 1-page JXL."""
    tif = tmp_path / "twopage.tif"
    create_multipage_tiff(tif)          # 2 real pages + 1 thumbnail
    _reset_ignored_counter()
    monkeypatch.setattr(enc, "MULTIPAGE_TIFF_MODE", "ignore")

    with caplog.at_level("WARNING"):
        items = enc.convert_multipage(tif, tmp_path / "out", mode=0)

    assert len(items) == 1, "ignore must plan exactly one output"
    assert enc._multipage_ignored["files"] == 1
    assert enc._multipage_ignored["pages"] == 2, "3-page TIFF drops 2 pages"
    assert any("DISCARDING" in r.message for r in caplog.records), \
        "dropping pages must be visible in the log"


def test_single_page_tiff_does_not_warn(tmp_path, monkeypatch, caplog):
    """Control: the common case must stay quiet."""
    tif = tmp_path / "single.tif"
    tifffile.imwrite(str(tif), np.zeros((16, 16, 3), dtype=np.uint16), photometric="rgb")
    _reset_ignored_counter()
    monkeypatch.setattr(enc, "MULTIPAGE_TIFF_MODE", "ignore")

    with caplog.at_level("WARNING"):
        enc.convert_multipage(tif, tmp_path / "out", mode=0)

    assert enc._multipage_ignored["files"] == 0
    assert not any("DISCARDING" in r.message for r in caplog.records)


def _summary(**adv):
    cm = wrapper.ConfigManager()
    menu = wrapper.InteractiveMenu(cm, wrapper.DependencyChecker(cm))
    return menu._multipage_summary({"advanced_options": adv})


def test_wizard_summary_flags_page_loss():
    """The Step 7 summary is the last gate before YES — page-dropping policies
    must be flagged there, including the default (no advanced options set)."""
    label, warn = _summary()
    assert warn and "DISCARDED" in label, "the wizard default must be flagged"

    label, warn = _summary(multipage_mode="skip")
    assert warn and "NOT converted" in label

    for mp in ("split", "split_all"):
        label, warn = _summary(multipage_mode=mp)
        assert not warn, f"{mp} keeps every page and must not be flagged"
        assert "one JXL per page" in label


def test_split_warns_when_excluding_thumbnails(tmp_path, monkeypatch, caplog):
    """`split` means "keep my pages" — dropping the thumbnail ones must be as
    visible as the `ignore` path, not silent."""
    tif = tmp_path / "twopage.tif"
    create_multipage_tiff(tif)          # 2 real pages + 1 thumbnail
    _reset_ignored_counter()
    monkeypatch.setattr(enc, "MULTIPAGE_TIFF_MODE", "split")
    monkeypatch.setattr(enc, "THUMBNAIL_MODE", "exclude")

    with caplog.at_level("WARNING"):
        items = enc.convert_multipage(tif, tmp_path / "out", mode=0)

    assert len(items) == 2, "the two real pages are still encoded"
    assert enc._multipage_ignored["pages"] == 1
    assert any("thumbnail page(s)" in r.message for r in caplog.records)


def test_split_include_is_quiet_and_matches_split_all(tmp_path, monkeypatch, caplog):
    """Control: including thumbnails drops nothing, so nothing is reported —
    and it must plan exactly what split_all plans (they are the same policy)."""
    tif = tmp_path / "twopage.tif"
    create_multipage_tiff(tif)

    _reset_ignored_counter()
    monkeypatch.setattr(enc, "MULTIPAGE_TIFF_MODE", "split")
    monkeypatch.setattr(enc, "THUMBNAIL_MODE", "include")
    with caplog.at_level("WARNING"):
        split_incl = enc.convert_multipage(tif, tmp_path / "out", mode=0)
    assert enc._multipage_ignored["pages"] == 0
    assert not any("DISCARDING" in r.message for r in caplog.records)

    monkeypatch.setattr(enc, "MULTIPAGE_TIFF_MODE", "split_all")
    split_all = enc.convert_multipage(tif, tmp_path / "out", mode=0)
    assert split_incl == split_all, "split+include and split_all must agree"
