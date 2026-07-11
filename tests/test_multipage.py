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

        print("\nAll multi-page tests passed.")


if __name__ == "__main__":
    main()
