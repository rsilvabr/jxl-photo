#!/usr/bin/env python3
"""
jxl_to_tiff.py — Batch JPEG XL → TIFF 16-bit converter

Usage:
  py jxl_to_tiff.py <input> [output] --mode 0-5 [--workers N] [--overwrite] [--sync]

Requirements:
  pip install tifffile numpy
  cjxl / djxl  →  https://github.com/libjxl/libjxl/releases
  exiftool     →  https://exiftool.org
"""

import subprocess, os, tempfile, threading, logging, sys, shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import argparse
import numpy as np
from PIL import Image
import tifffile
import json


# ─────────────────────────────────────────────
# USER SETTINGS - GENERAL
# ─────────────────────────────────────────────

DJXL_OUTPUT_DEPTH = 16
# Output bit depth for TIFF (8 or 16).
# 16 is recommended for maximum quality preservation (especially for further editing).
# 8 can be used for web/delivery to save space.

TIFF_COMPRESSION = "zip"
# TIFF compression method. Options: "uncompressed", "lzw", "zip"
# "uncompressed" - No compression, largest files, fastest write
# "lzw"          - LZW compression, good compatibility, medium size
# "zip"          - Deflate/ZIP compression, best compression, recommended (default)

ADD_JPEG_PREVIEW = True
# Add an embedded JPEG preview/thumbnail to the TIFF file.
# This enables fast preview in file explorers (Windows Explorer, etc.)
# and image viewers without loading the full-resolution image.
# True  → Add JPEG preview (default, recommended)
# False → No preview, slightly smaller file

JPEG_PREVIEW_SIZE = 1024
# Maximum dimension (width or height) of the JPEG preview.
# Default: 1024 pixels. Larger = better preview quality, larger file.

TEMP_DIR = None
# Temporary directory for intermediate files.
# None → use system temp (usually C:\Users\...\AppData\Local\Temp on Windows)
# Ex:  → "E:\\temp_jxl"

TEMP2_DIR = None
# Staging directory for output TIFFs during conversion.
# None → disabled: TIFFs are written directly to their final destination.
# If set: TIFFs are written here during conversion, then moved in bulk to the final
# destination when each folder group finishes. Separates read I/O from write I/O.
# Example: r"E:\\staging_tiff"

OVERWRITE = "smart"
# False   → skip TIFFs that already exist at the final destination. Safe for resuming.
# True    → always overwrite existing TIFFs.
# "smart" → same as --sync flag: only reconvert if the JXL is newer than the TIFF.
#            Useful for incremental updates.
# Never overwrites JXLs or any other non-TIFF format.

DELETE_SOURCE = False
# [MODE 6/8 only] Whether to delete the source JXL after successful decode.
# Only deletes if ALL of the following are true:
#   - decode status is ok or overwrite (never deletes on error or skip)
#   - the TIFF file exists at its final destination
#
# False (default) → never delete source JXLs. TIFF and JXL coexist.
# True            → delete source JXL after confirmed successful decode.
#
# WARNING: irreversible. Only enable after testing on a small batch first.


# ─────────────────────────────────────────────
# USER SETTINGS - MODES CONFIGURATION
# ─────────────────────────────────────────────

# || MODE 0 SETTINGS ||
# No settings needed. Just use 
# py jxl_to_tiff.py <input> <output> [--mode 0] [--workers N] [--overwrite] [--sync]
# or just py jxl_to_tiff.py <input> , input can be file or directory.


# || MODE 1 SETTINGS ||
CONVERTED_TIFF_FOLDER = "converted_tiff"
# [MODE 1] Name of the subfolder created inside each JXL folder.
# Example: .../JXL_FOLDER/converted_tiff/photo.tif

# || MODE 2 SETTINGS ||
# No settings needed. Flat: input directory → output directory.
# py jxl_to_tiff.py <input_dir> <output_dir> --mode 2

# || MODE 3 SETTINGS ||
TIFF_FOLDER_NAME = "TIFF_16bits"
# [MODE 3] Subfolder created inside each JXL folder for output.
# Example: .../JXL_FOLDER/TIFF_16bits/photo.tif

# || MODE 4 SETTINGS ||
JXL_SUFFIX_TO_REPLACE = "JXL"
TIFF_SUFFIX_REPLACE     = "TIFF"
# [MODE 4] Replaces JXL_SUFFIX_TO_REPLACE with TIFF_SUFFIX_REPLACE in the folder name.
# Case-insensitive (JXL, jxl, Jxl all match).
# Example: C1_Export_1_JXL → C1_Export_1_TIFF

# || MODE 5 SETTINGS ||
# Sibling folder next to each JXL folder — no extra settings needed.
# Example: .../TIFF_FOLDER_NAME/photo.tif  (uses TIFF_FOLDER_NAME above)

# || MODES 6 and 7 SETTINGS ||
EXPORT_MARKER     = "_EXPORT"
EXPORT_TIFF_FOLDER = "16B_TIFF"
# [MODE 6/7] Uses EXPORT_MARKER as an anchor in the path.
# All TIFFs go into EXPORT_MARKER/EXPORT_TIFF_FOLDER/.
# Mode 6: processes JXLs both inside and outside EXPORT_MARKER (same parent hierarchy).
# Mode 7: only processes JXLs inside EXPORT_MARKER (ignores JXLs outside).
#
# JXLs inside EXPORT_MARKER: immediate subfolder (e.g. color space name) is dropped.
# JXLs outside EXPORT_MARKER (mode 6 only): relative path from EXPORT_MARKER's parent is preserved.
#
# Example (mode 7):
#   EXPORT_MARKER/JXL/photo.jxl      →  EXPORT_MARKER/EXPORT_TIFF_FOLDER/photo.tif

EXPORT_JXL_SUBFOLDER = ""
# [MODE 7] If set, only JXLs in this specific subfolder of EXPORT_MARKER are processed,
# and this subfolder name is dropped from the output path.
# If empty (""), all JXLs inside EXPORT_MARKER are processed (first subfolder is dropped).
# OBS: Empty value can cause filename collisions if different subfolders contain files
# with the same name.

# || MODE 8 SETTINGS ||
# No extra settings. Mode 8 converts JXLs recursively and outputs TIFFs in the same
# folder as each source JXL. Controlled by DELETE_SOURCE above.
# Example: .../session/photo.jxl → .../session/photo.tif


# ─────────────────────────────────────────────
# ─────────────────────────────────────────────

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SAFETY SETTINGS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DELETE_CONFIRM = True
# Only relevant when DELETE_SOURCE = True.
# True  (default) → require interactive confirmation before deleting any source file.
#   Type the current time (HHMM) shown on screen. This cannot be automated and forces
#   a conscious decision — you are about to delete JXLs that cannot be recovered if
#   the TIFF decode had any issues.
# False → skip all confirmations. Useful if running the script from another program.
# Recommendation: leave this True. It takes 3 seconds and prevents accidents.




def extract_icc_from_jxl_metadata(jxl_path, tmp_dir):
    """
    Extract ICC profile directly from JXL metadata (embedded by tiff_to_jxl.py).
    First tries XMP CreatorTool (base64 encoded ICC), then falls back to direct ICC extraction.
    Returns path to extracted ICC file or None.
    """
    try:
        # Method 1: Try to extract ICC from XMP CreatorTool (base64 encoded)
        r = subprocess.run(["exiftool", "-b", "-XMP-xmp:CreatorTool", str(jxl_path)],
                          capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout:
            import re
            # Look for ICC: prefix followed by base64 data
            match = re.search(r'ICC:([A-Za-z0-9+/=]+)', r.stdout)
            if match:
                import base64
                icc_data = base64.b64decode(match.group(1))
                if len(icc_data) > 500:  # Valid ICC size
                    icc_path = tmp_dir / "extracted_xmp.icc"
                    icc_path.write_bytes(icc_data)
                    return icc_path
        
        # Method 2: Try direct ICC extraction (for lossless JXLs that preserved ICC)
        icc_path = tmp_dir / "extracted.icc"
        r = subprocess.run(["exiftool", "-b", "-ICC_Profile", str(jxl_path), "-o", str(icc_path)],
                          capture_output=True, timeout=10)
        if icc_path.exists() and icc_path.stat().st_size > 500:
            return icc_path
            
        return None
    except Exception:
        return None

def extract_icc_from_jxl_via_png(jxl_path, tmp_dir):
    """
    Extract ICC profile from JXL by converting to PNG using djxl.
    djxl embeds the ICC in the PNG, which we can then extract.
    Returns path to extracted ICC file or None.
    """
    try:
        png_path = tmp_dir / "temp_icc_extract.png"
        icc_path = tmp_dir / "extracted_png.icc"
        
        # Convert JXL to PNG using djxl (ICC is embedded in PNG)
        r = subprocess.run(["djxl", str(jxl_path), str(png_path)],
                          capture_output=True, timeout=60)
        if r.returncode != 0 or not png_path.exists():
            return None
        
        # Extract ICC from PNG
        r = subprocess.run(["exiftool", "-b", "-ICC_Profile", str(png_path), "-o", str(icc_path)],
                          capture_output=True, timeout=10)
        
        # Clean up PNG
        png_path.unlink(missing_ok=True)
        
        if icc_path.exists() and icc_path.stat().st_size > 0:
            return icc_path
        return None
    except Exception:
        return None


SCRIPT_DIR = Path(__file__).parent
LOG_DIR    = SCRIPT_DIR / "Logs" / Path(__file__).stem
logger     = None
counter_lock = threading.Lock()
_counter = {"done": 0, "total": 0}

def setup_logger():
    global logger
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file  = LOG_DIR / f"{timestamp}.log"

    logger = logging.getLogger("jxl_decode")
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.info(f"Log saved to: {log_file}")
    return log_file

def next_count():
    with counter_lock:
        _counter["done"] += 1
        return _counter["done"], _counter["total"]

def confirm_deletion_jxl() -> bool:
    """Interactive confirmation before deleting source JXLs (DELETE_CONFIRM=True).
    Type the current time (HHMM) shown on screen.
    Returns True if confirmed, False if cancelled."""
    from datetime import datetime as _dt
    print()
    print()
    print()
    print("  ⚠  WARNING — DELETE_SOURCE is enabled")
    print("     Source JXLs will be deleted after successful decode.")
    print("     This deletion is IRREVERSIBLE.")
    now   = _dt.now()
    token = now.strftime("%H%M")
    print(f"     Current time: {now.strftime('%H:%M')}  →  to confirm, type: {token}")
    print()
    try:
        answer = input("     > ").strip()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer == token:
        print("     Confirmed. Source JXLs will be deleted after successful decode.")
        print()
        return True
    else:
        print("     Cancelled. No files will be deleted.")
        print()
        return False

def resolve_output(jxl_path: Path, mode: int, input_root: Path) -> Path:
    # Mode 0: single file in-place — handled in main() before calling this
    # Mode 1: single file → converted_tiff/ subfolder — handled in main() before calling this

    if mode == 2:
        # Flat directory: input_root/photo.tif
        return input_root / jxl_path.with_suffix(".tif").name

    elif mode == 3:
        # Subfolder inside each JXL folder
        return jxl_path.parent / CONVERTED_TIFF_FOLDER / jxl_path.with_suffix(".tif").name

    elif mode == 4:
        # Rename folder replacing JXL suffix with TIFF suffix
        old_name = jxl_path.parent.name
        new_name = None
        for variant in [JXL_SUFFIX_TO_REPLACE, JXL_SUFFIX_TO_REPLACE.lower(),
                        JXL_SUFFIX_TO_REPLACE.title()]:
            if variant in old_name:
                new_name = old_name.replace(variant, TIFF_SUFFIX_REPLACE)
                break
        if new_name is None:
            new_name = old_name + "_" + TIFF_SUFFIX_REPLACE
            logger.warning(f"'{JXL_SUFFIX_TO_REPLACE}' not found in '{old_name}', using '{new_name}'")
        return jxl_path.parent.parent / new_name / jxl_path.with_suffix(".tif").name

    elif mode == 5:
        # Sibling folder next to each JXL folder
        return jxl_path.parent.parent / TIFF_FOLDER_NAME / jxl_path.with_suffix(".tif").name

    elif mode == 6:
        # _EXPORT anchor — all JXLs in hierarchy
        parts = jxl_path.parts
        export_idx = next((i for i, p in enumerate(parts) if EXPORT_MARKER in p), None)
        if export_idx is None:
            logger.warning(f"'{EXPORT_MARKER}' not found in {jxl_path}, using local folder")
            return jxl_path.parent / EXPORT_TIFF_FOLDER / jxl_path.with_suffix(".tif").name

        export_dir    = Path(*parts[:export_idx + 1])
        project_root  = export_dir.parent

        if jxl_path.is_relative_to(export_dir):
            rel_parts = jxl_path.relative_to(export_dir).parts
            if len(rel_parts) > 1:
                rel = Path(*rel_parts[1:])
            else:
                rel = Path(rel_parts[0])
        else:
            rel = jxl_path.relative_to(project_root)

        return export_dir / EXPORT_TIFF_FOLDER / rel.with_suffix(".tif")

    elif mode == 7:
        # _EXPORT anchor — only JXLs inside _EXPORT
        parts = jxl_path.parts
        export_idx = next((i for i, p in enumerate(parts) if EXPORT_MARKER in p), None)
        if export_idx is None:
            logger.warning(f"'{EXPORT_MARKER}' not found in {jxl_path}, using local folder")
            return jxl_path.parent / EXPORT_TIFF_FOLDER / jxl_path.with_suffix(".tif").name

        export_dir = Path(*parts[:export_idx + 1])

        if EXPORT_JXL_SUBFOLDER:
            anchor = export_dir / EXPORT_JXL_SUBFOLDER
            rel = jxl_path.relative_to(anchor)
        else:
            rel_parts = jxl_path.relative_to(export_dir).parts
            rel = Path(*rel_parts[1:]) if len(rel_parts) > 1 else Path(rel_parts[0])

        return export_dir / EXPORT_TIFF_FOLDER / rel.with_suffix(".tif")

    elif mode == 8:
        # In-place recursive: TIFF goes to the same folder as the source JXL.
        return jxl_path.parent / jxl_path.with_suffix(".tif").name

    raise ValueError(f"Modo inválido: {mode}")

def convert_one(jxl_path: Path, write_path: Path, final_path: Path):
    """
    Converts a single JXL to TIFF.
    write_path: where the TIFF is initially written (staging or final destination)
    final_path: the final destination path (for overwrite checking and logging)
    """
    overwritten = final_path.exists()

    if overwritten:
        if OVERWRITE == False:
            n, total = next_count()
            logger.info(f"[{n}/{total}] SKIP (exists) | {jxl_path.name}")
            return (str(jxl_path), "skipped", str(final_path), None)
        elif OVERWRITE == "smart":
            jxl_mtime = jxl_path.stat().st_mtime
            tiff_mtime  = final_path.stat().st_mtime
            if jxl_mtime <= tiff_mtime:
                n, total = next_count()
                logger.info(f"[{n}/{total}] SKIP (sync: TIFF up to date) | {jxl_path.name}")
                return (str(jxl_path), "skipped", str(final_path), None)
            logger.info(f"  → SYNC: JXL newer than TIFF, reconverting | {jxl_path.name}")

    write_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="tiff_", dir=TEMP_DIR) as tmp:
        tmp_dir = Path(tmp)
        try:
            # 1. Decode JXL to PPM using djxl
            # PPM supports 16-bit, unlike PNG which djxl always outputs as 8-bit
            ppm_path = tmp_dir / f"{jxl_path.stem}.ppm"
            
            # Use PPM format for 16-bit support
            djxl_cmd = ["djxl", str(jxl_path), str(ppm_path), "--output_format", "ppm"]
            r = subprocess.run(djxl_cmd, capture_output=True)
            
            if r.returncode != 0:
                raise RuntimeError(f"djxl: {r.stderr.decode(errors='replace')[:200]}")

            if not ppm_path.exists():
                raise RuntimeError("djxl did not produce output PPM")

            # 2. Read PPM and convert to TIFF using tifffile
            # PPM is raw pixel data, easy to read directly
            with open(ppm_path, 'rb') as f:
                # Read PPM header
                magic = f.readline().strip()
                if magic not in (b'P6', b'P5'):
                    raise RuntimeError(f"Unsupported PPM format: {magic}")
                
                # Skip comments
                while True:
                    line = f.readline()
                    if line.startswith(b'#'):
                        continue
                    break
                
                # Parse dimensions (width height)
                dimensions = line.strip() if not line.startswith(b'#') else f.readline().strip()
                while dimensions.startswith(b'#'):
                    dimensions = f.readline().strip()
                width, height = map(int, dimensions.split())
                
                # Parse max value (bit depth indicator)
                maxval_line = f.readline().strip()
                while maxval_line.startswith(b'#'):
                    maxval_line = f.readline().strip()
                maxval = int(maxval_line)
                
                # Read raw pixel data
                # PPM uses big-endian for 16-bit data
                if maxval <= 255:
                    pixel_data = np.frombuffer(f.read(), dtype=np.uint8)
                else:
                    # Read as big-endian uint16
                    pixel_data = np.frombuffer(f.read(), dtype=np.dtype('>u2')).astype(np.uint16)
                
                # Reshape based on format
                if magic == b'P6':  # RGB
                    img_array = pixel_data.reshape((height, width, 3))
                else:  # P5 - Grayscale
                    img_array = pixel_data.reshape((height, width))
                    img_array = np.expand_dims(img_array, axis=-1)  # Add channel dim
            
            # Handle bit depth if needed
            if DJXL_OUTPUT_DEPTH == 16 and img_array.dtype != np.uint16:
                img_array = img_array.astype(np.uint16) * 257
            elif DJXL_OUTPUT_DEPTH == 8 and img_array.dtype != np.uint8:
                img_array = (img_array / 257).astype(np.uint8)
            
            # 3. Save as TIFF
            compression_map = {
                "uncompressed": None,
                "lzw": "lzw",
                "zip": "zlib"
            }
            compression = compression_map.get(TIFF_COMPRESSION.lower(), "zlib")
            
            tifffile.imwrite(
                str(write_path), 
                img_array,
                compression=compression,
                metadata=None
            )
            
            del img_array

            # 3. Copy metadata from JXL to TIFF using exiftool
            # Note: JXL stores colorspace as native primaries, not ICC blob
            # We copy all metadata from JXL and let exiftool handle what it can
            
            arg_inject = tmp_dir / "inject.args"
            arg_lines = [
                "-overwrite_original",
                "-tagsfromfile", str(jxl_path),
                "-all:all",
                "--JXLSignature", "--JXLSize", "--JXLVersion",
                "--FileType", "--FileTypeExtension", "--MIMEType",
                "--ImageWidth", "--ImageHeight", "--BitDepth", "--ColorType",
                "--Compression", "--PhotometricInterpretation",
                "--StripOffsets", "--StripByteCounts", "--RowsPerStrip",
                str(write_path)
            ]
            
            arg_inject.write_text("\n".join(arg_lines), encoding="utf-8")
            
            r2 = subprocess.run(["exiftool", "-@", str(arg_inject)], 
                               capture_output=True, text=True)
            
            if r2.returncode != 0:
                err_msg = (r2.stderr or r2.stdout or "no output")[:300].strip()
                logger.warning(f"Metadata copy warning for {jxl_path.name}: {err_msg}")
            
            # 4. Copy XMP if present in JXL
            xmp_path = tmp_dir / f"{jxl_path.stem}.xmp"
            subprocess.run(["exiftool", "-b", "-XMP", str(jxl_path), "-o", str(xmp_path)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if xmp_path.exists() and xmp_path.stat().st_size > 0:
                subprocess.run(["exiftool", "-overwrite_original", "-tagsfromfile", str(xmp_path), 
                               "-xmp:all", str(write_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # 5. Handle ICC Profile
            # Try to extract ICC from JXL metadata first (embedded by tiff_to_jxl.py)
            icc_path = extract_icc_from_jxl_metadata(jxl_path, tmp_dir)
            icc_source = "metadata"
            
            if not icc_path:
                # Fallback: extract ICC via PNG (djxl generates ICC from primaries)
                icc_path = extract_icc_from_jxl_via_png(jxl_path, tmp_dir)
                icc_source = "generated"
            
            if icc_path:
                subprocess.run(["exiftool", "-overwrite_original",
                               f"-ICC_Profile<={icc_path}",
                               str(write_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                logger.info(f"  -> ICC profile extracted from {icc_source} ({icc_path.stat().st_size} bytes)")
                
                # Clean up XMP CreatorTool if it contains our ICC marker (base64 data)
                # The CreatorTool is preserved with the encoding params, we just remove the ICC: prefix
                r_ct = subprocess.run(["exiftool", "-XMP-xmp:CreatorTool", str(write_path)],
                                     capture_output=True, text=True, timeout=5)
                if r_ct.returncode == 0 and "ICC:" in r_ct.stdout:
                    # Extract just the encoding params part before " | " if present
                    import re
                    match = re.search(r'ICC:[A-Za-z0-9+/=]+(.*)$', r_ct.stdout.strip())
                    if match and match.group(1):
                        # Keep the suffix (e.g., " | TIFF to JXL with ICC preservation")
                        new_ct = match.group(1).lstrip(" |")
                    else:
                        new_ct = "JXL to TIFF converter"
                    subprocess.run(["exiftool", "-overwrite_original", f"-XMP-xmp:CreatorTool={new_ct}", str(write_path)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    logger.debug(f"  -> Cleaned up XMP CreatorTool (removed embedded ICC)")
            else:
                logger.debug(f"  -> No ICC profile could be extracted from JXL")
            
            # 6. Add JPEG preview/thumbnail
            if ADD_JPEG_PREVIEW:
                try:
                    from PIL import Image
                    # Open the main TIFF and create a preview
                    img = Image.open(write_path)
                    # Calculate new size maintaining aspect ratio
                    width, height = img.size
                    max_size = JPEG_PREVIEW_SIZE
                    if width > height:
                        new_width = max_size
                        new_height = int(height * max_size / width)
                    else:
                        new_height = max_size
                        new_width = int(width * max_size / height)
                    
                    # Resize for preview
                    preview = img.resize((new_width, new_height), Image.LANCZOS)
                    
                    # Save as second page/subIFD
                    # Use JPEG compression for the preview
                    preview_path = tmp_dir / "preview.jpg"
                    preview.save(preview_path, "JPEG", quality=85)
                    
                    # Add preview as second page using tifffile
                    # Append to existing TIFF using imwrite with append=True
                    preview_data = np.array(preview)
                    tifffile.imwrite(str(write_path), preview_data, compression='jpeg', append=True)
                    
                    img.close()
                    preview.close()
                    logger.info(f"  -> Added JPEG preview ({new_width}x{new_height})")
                except Exception as e:
                    logger.debug(f"  -> JPEG preview skipped: {e}")

            n, total = next_count()
            status = "overwrite" if overwritten else "ok"
            label  = "OVERWRITE" if overwritten else "OK"
            logger.info(f"[{n}/{total}] {label} | {jxl_path.name} -> {final_path}")
            return (str(jxl_path), status, str(final_path), jxl_path)

        except Exception as e:
            n, total = next_count()
            logger.error(f"[{n}/{total}] ERROR | {jxl_path.name} | {e}")
            return (str(jxl_path), "error", str(e), None)

def process_group(group_pairs: list, workers: int, mode: int = 0):
    """
    Converts a group of (jxl, final_tiff) pairs in parallel.
    If TEMP2_DIR is set, writes to staging first then moves in bulk.
    """
    use_staging = TEMP2_DIR is not None
    staging_dir = Path(TEMP2_DIR) if use_staging else None

    if use_staging:
        staging_dir.mkdir(parents=True, exist_ok=True)

    
    tasks = []
    for jxl, final_tiff in group_pairs:
        if use_staging:
            # Unique staging name to avoid collisions across different source folders
            write_tiff = staging_dir / f"{jxl.parent.name}__{jxl.stem}.tif"
        else:
            write_tiff = final_tiff
        tasks.append((jxl, write_tiff, final_tiff))

    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(convert_one, t, w, f): (t, w, f) for t, w, f in tasks}
        for fut in as_completed(futures):
            results.append(fut.result())

    # Move from staging to final destination in bulk
    if use_staging:
        moved = 0
        for jxl, write_tiff, final_tiff in tasks:
            if write_tiff.exists():
                final_tiff.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(write_tiff), str(final_tiff))
                moved += 1
        if moved:
            logger.info(f"  → Moved {moved} file(s) from staging to final destination")

    # Delete source JXLs after confirmed decode — only for modes 6/8, only after staging.
    if DELETE_SOURCE and mode in (6, 8):
        deleted = 0
        src_map = {str(j): (j, f) for j, _, f in tasks}
        for result in results:
            status    = result[1]
            src_jxl   = result[3]
            if status not in ("ok", "overwrite") or src_jxl is None:
                continue
            _, final_tiff = src_map.get(result[0], (None, None))
            if final_tiff is None or not final_tiff.exists():
                logger.warning(f"  KEEP (TIFF not found at destination) | {src_jxl.name}")
                continue
            src_jxl.unlink()
            deleted += 1
            logger.info(f"  DELETED source | {src_jxl.name}")
        if deleted:
            logger.info(f"  → Deleted {deleted} source JXL(s)")

    return results

def find_jxls_recursive(input_path: Path):
    """Find all JXL files recursively."""
    seen = set()
    files = []
    for ext in ("*.jxl", "*.jiff"):
        for f in input_path.rglob(ext):
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                files.append(f)
    return files

def find_jxls_mode7(input_path: Path):
    """Mode 7: only JXLs inside folders containing EXPORT_MARKER in their path."""
    all_jxls = find_jxls_recursive(input_path)
    filtered = []
    for j in all_jxls:
        parts_str = list(j.parts)
        export_idx = next((i for i, p in enumerate(parts_str) if EXPORT_MARKER in p), None)
        if export_idx is None:
            continue
        if EXPORT_JXL_SUBFOLDER:
            if export_idx + 1 < len(parts_str) and parts_str[export_idx + 1] == EXPORT_JXL_SUBFOLDER:
                filtered.append(j)
        else:
            filtered.append(j)
    return filtered

def main():
    parser = argparse.ArgumentParser(description="Batch JPEG XL → TIFF 16-bit converter")
    parser.add_argument("input",             type=Path, help="Input root folder or file")
    parser.add_argument("output", nargs="?", type=Path, help="Output folder (mode 0 only)")
    parser.add_argument("--mode",            type=int, default=0, choices=[0,1,2,3,4,5,6,7,8])
    parser.add_argument("--workers",         type=int, default=min(os.cpu_count(), 16))
    parser.add_argument("--overwrite",       action="store_true",
                        help="Always overwrite existing TIFFs")
    parser.add_argument("--sync",            action="store_true",
                        help="Only reconvert JXLs newer than their existing TIFF")
    parser.add_argument("--depth",           type=int, choices=[8, 16], default=None,
                        help="Output bit depth (8 or 16). Overrides DJXL_OUTPUT_DEPTH setting.")
    parser.add_argument("--compression",     type=str, choices=["uncompressed", "lzw", "zip"], default=None,
                        help="TIFF compression: uncompressed, lzw, or zip (default: zip). Overrides TIFF_COMPRESSION setting.")
    args = parser.parse_args()

    global OVERWRITE, DJXL_OUTPUT_DEPTH, TIFF_COMPRESSION
    if args.sync:
        OVERWRITE = "smart"
    elif args.overwrite:
        OVERWRITE = True
    
    if args.depth:
        DJXL_OUTPUT_DEPTH = args.depth
    
    if args.compression:
        TIFF_COMPRESSION = args.compression

    log_file = setup_logger()
    _delete_label = f"delete_source=ON (confirm={'ON' if DELETE_CONFIRM else 'OFF'})" if DELETE_SOURCE else "delete_source=OFF"
    logger.info(
        f"Mode: {args.mode} | Output Depth: {DJXL_OUTPUT_DEPTH}-bit | Compression: {TIFF_COMPRESSION} | "
        f"JPEG Preview: {'ON (' + str(JPEG_PREVIEW_SIZE) + 'px)' if ADD_JPEG_PREVIEW else 'OFF'} | "
        f"Staging: {TEMP2_DIR or 'disabled'} | "
        f"Overwrite: {'sync (smart)' if args.sync else OVERWRITE} | {_delete_label} | Workers: {args.workers}"
    )
    logger.info(f"Input: {args.input}")

    # Collect input files
    if args.mode in (0, 1) and args.input.is_file():
        jxls = [args.input]
        # If output is specified and is a directory, use it; otherwise use input's parent
        if args.output and args.output.is_dir():
            output_root = args.output
        else:
            output_root = args.input.parent
    elif args.mode in (0, 1):
        # Directory input: flat (non-recursive)
        jxls = list(args.input.glob("*.jxl"))
        output_root = args.output or args.input
    elif args.mode == 2:
        # Flat output directory
        jxls = find_jxls_recursive(args.input)
        output_root = args.output or args.input
    elif args.mode == 7:
        jxls = find_jxls_mode7(args.input)
        output_root = args.input
    elif args.mode == 8:
        jxls = find_jxls_recursive(args.input)
        output_root = args.input
    else:
        jxls = find_jxls_recursive(args.input)
        output_root = args.input

    logger.info(f"Files found: {len(jxls)}")
    _counter["total"] = len(jxls)

    # Build (jxl, tiff_destination) pairs
    pairs = []
    for j in jxls:
        if args.mode == 0:
            # If output is explicitly specified and looks like a file (has .tif/.tiff extension)
            if args.output and args.output.suffix.lower() in ('.tif', '.tiff'):
                tiff = args.output
            # File or directory: if output_root differs from source parent, use it
            elif output_root != args.input:
                tiff = output_root / j.with_suffix(".tif").name
            else:
                tiff = j.parent / j.with_suffix(".tif").name
        elif args.mode in (1, 2):
            if args.input.is_file():
                # Single file → converted_tiff/ subfolder
                tiff = j.parent / CONVERTED_TIFF_FOLDER / j.with_suffix(".tif").name
            else:
                tiff = output_root / j.with_suffix(".tif").name
        else:
            tiff = resolve_output(j, args.mode, args.input)
        pairs.append((j, tiff))

    if args.mode in (6, 8) and DELETE_SOURCE:
        logger.info(f"Mode {args.mode} — DELETE_SOURCE=True: source JXLs will be deleted after successful decode")
        if DELETE_CONFIRM:
            if not confirm_deletion_jxl():
                logger.info("Deletion not confirmed — exiting.")
                return
    elif args.mode == 8:
        logger.info("Mode 8 — in-place recursive | DELETE_SOURCE=False: JXL and TIFF will coexist")

    # Group by output folder (one bulk move per group)
    groups: dict[Path, list] = {}
    for j, t in pairs:
        groups.setdefault(t.parent, []).append((j, t))

    logger.info(f"Output groups: {len(groups)}")

    ok = err = skipped = overwritten = synced = 0

    for dest_folder, group_pairs in groups.items():
        if len(groups) > 1:
            logger.info(f"── Group: {dest_folder} ({len(group_pairs)} file(s))")

        results = process_group(group_pairs, args.workers, args.mode)

        for result in results:
            status = result[1]
            if   status == "ok":        ok += 1
            elif status == "overwrite": ok += 1; overwritten += 1; synced += 1
            elif status == "skipped":   skipped += 1
            elif status == "error":     err += 1

    logger.info(f"\n{'-'*50}")
    if args.sync:
        logger.info(f"SYNC done: {synced} reconverted | {skipped} up to date | {err} errors")
        logger.info(f"  → Reconverted: JXLs newer than their existing TIFF")
        logger.info(f"  → Up to date: TIFF is newer than or equal to JXL")
    else:
        logger.info(f"Done: {ok} OK | {overwritten} overwrites | {skipped} skipped | {err} errors")
    logger.info(f"Log: {log_file}")

if __name__ == "__main__":
    main()
