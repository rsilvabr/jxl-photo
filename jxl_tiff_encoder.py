#!/usr/bin/env python3
"""
jxl_tiff_encoder.py — Batch TIFF 16-bit -> JPEG XL converter with proper XMP preservation

Usage:
  py jxl_tiff_encoder.py <input> [output] --mode 0-8 [--workers N] [--overwrite] [--sync]

Requirements:
  pip install tifffile numpy
  cjxl / djxl  ->  https://github.com/libjxl/libjxl/releases
  exiftool     ->  https://exiftool.org
"""

import subprocess, os, platform, tempfile, threading, zlib, struct, logging, sys, shutil, base64, uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict
import argparse
import numpy as np
import tifffile


def _verify_jxl_integrity(jxl_path: Path) -> bool:
    """Verify JXL file integrity before deleting source.
    
    Checks:
    1. File exists and size > 0
    2. File has valid JXL signature (0xFF 0x0A for bare JXL or ISOBMFF box)
    """
    if not jxl_path.exists():
        return False
    
    try:
        stat = jxl_path.stat()
        if stat.st_size == 0:
            return False
        
        # Check JXL signature (first 2 bytes for bare JXL, or first 12 for container)
        with open(jxl_path, 'rb') as f:
            header = f.read(12)
        
        if len(header) < 2:
            return False
        
        # Bare JXL starts with 0xFF 0x0A
        if header[0:2] == b'\xff\x0a':
            return True
        
        # Container format starts with 0x00 0x00 0x00 0x0C 0x4A 0x58 0x4C 0x20 0x0D 0x0A 0x87 0x0A
        if header == b'\x00\x00\x00\x0cJXL \r\n\x87\n':
            return True
        
        return False
    except (OSError, IOError):
        return False


def _is_relative_to(path: Path, anchor: Path) -> bool:
    """Backport of Path.is_relative_to for Python < 3.9."""
    try:
        path.relative_to(anchor)
        return True
    except ValueError:
        return False


# ExifTool detection - try multiple name variants
_exiftool_cmd = None
def _get_exiftool_cmd():
    global _exiftool_cmd
    if _exiftool_cmd is None:
        candidates = ["exiftool", "exiftool-k", "exiftool(-k)"]
        for cmd in candidates:
            if shutil.which(cmd) is not None:
                _exiftool_cmd = cmd
                break
        else:
            _exiftool_cmd = "exiftool"
    return _exiftool_cmd

# ─────────────────────────────────────────────
# USER SETTINGS - GENERAL
# ─────────────────────────────────────────────

CJXL_EFFORT = 7
# Compression effort (1–10).
# Controls output file size — does NOT affect quality (quality is set by CJXL_DISTANCE).
# Higher effort = smaller file, but more CPU time.
# 7 is the sweet spot for camera photos — effort 8-10 is much slower and can
# increase file size for high-ISO or texture-heavy images.

CJXL_DISTANCE = 0.1
# 0   = mathematically lossless (pixel-perfect)
# 0.1 = near-lossless (~25MB for 36MP), imperceptible difference
# 0.5 = high quality lossy — recommended starting point (libjxl authors)
# 1.0 = "visually lossless" per libjxl documentation

CJXL_MODULAR = False
# False (default) — lossy uses VarDCT encoder + XYB colorspace.
#   This is the standard lossy mode: DCT-based, like JPEG but much more advanced.
#   Compresses photo content very efficiently. File sizes as shown in the table above.
#
# True — forces Modular encoder for lossy (--modular=1).
#   Modular is entropy-coded (similar to FLIF/PNG). It is the only encoder used
#   for lossless, but is significantly less efficient for lossy photo content.
#   Good for UI/screenshots, text, pixel art and rasterized vector graphics.
#   Use only if you need non-XYB encoding for compatibility reasons.
#
# Note: lossless (d=0) always uses Modular regardless of this setting.
#       CJXL_MODULAR only affects lossy (d > 0).

USE_RAM_FOR_PNG = False
# True  -> PNG intermediate stays entirely in RAM (faster, ~400MB RAM per worker)
# False -> PNG is written to disk in TEMP_DIR (useful if RAM is limited)

PIL_MAX_IMAGE_PIXELS = None
# PIL's decompression bomb protection limit (prevents DOS attacks with malicious images).
# None  -> Disable the limit completely (recommended for trusted local files/panoramas)
# N     -> Maximum number of pixels (e.g., 500_000_000 for ~500MP limit)

TEMP_DIR = None
# Temporary directory for small intermediate files (EXIF binary, PNG if USE_RAM_FOR_PNG=False).
# None -> use system temp (usually C:\Users\...\AppData\Local\Temp on Windows)
# Ex:  -> r"E:\\temp_jxl"

TEMP2_DIR = None
# Staging directory for output JXLs during conversion.
# None -> disabled: JXLs are written directly to their final destination.
# If set: JXLs are written here during conversion, then moved in bulk to the final
# destination when each folder group finishes. Separates read I/O (HDD with TIFFs)
# from write I/O (SSD for new JXLs), reducing seek contention on HDDs.
# Example: r"E:\\staging_jxl"

OVERWRITE = "smart"
# False   -> skip JXLs that already exist at the final destination. Safe for resuming.
# True    -> always overwrite existing JXLs.
# "smart" -> same as --sync flag: only reconvert if the TIFF is newer than the JXL.
#            Useful after re-editing and re-exporting from Capture One.
# Never overwrites TIFFs or any other non-JXL format.

ENCODE_TAG_MODE = "xmp"
# Records encoding parameters (distance and effort) in the JXL metadata.
# "software" -> appends to the EXIF Software field (e.g. "Capture One | cjxl d=0.5 e=7")
#              Visible in IrfanView, exiftool, and most viewers.
# "xmp"      -> writes as XMP-dc:Description custom field
#              Cleaner — does not touch the original Software field
#              Visible in Windows Properties, but not in IrfanView
# "off"      -> does not add anything
# NOTE: When EMBED_ICC_IN_JXL is True and ENCODE_TAG_MODE is "xmp",
# the encoding tag is concatenated to dc:Description, and ICC goes to CreatorTool.

EMBED_ICC_IN_JXL = True
# Embeds the original ICC profile as metadata in the JXL file.
# The ICC is NOT used by the JXL decoder (JXL uses native primaries),
# but is preserved for round-trip conversion back to TIFF/JPEG.
# This ensures the exact original ICC (with TRC curves, copyright, etc.)
# is available when converting JXL -> TIFF, even for lossy JXLs.
# True  -> embed ICC profile in JXL XMP CreatorTool (recommended, default)
# False -> do not embed ICC (smaller file, but lossy JXLs will use generic ICC on decode)

D50_PATCH_MODE = "auto"
# Controls the D50 illuminant patch for Capture One compatibility.
# "on"   -> Always apply D50 patch (fixes Capture One ICC non-conformance)
# "off"  -> Never apply D50 patch (use original ICC values)
# "auto" -> Apply only if source software matches D50_PATCH_SOFTWARE_LIST
# The patch fixes a rounding error in ICC profiles from Capture One.
# "auto" is recommended and safe for all workflows.

D50_PATCH_SOFTWARE_LIST = [
    "capture one",
    "captureone",
    # "my software",  # <-- add more software names here (uncomment to enable)
]
# Software names that trigger D50 patch when D50_PATCH_MODE="auto".
# Case-insensitive matching. Add your own software if it has the same ICC bug.
# Example: ["capture one", "myapp"] will match "Capture One 23" or "MYAPP Pro"

CLEANUP_XMP_ICC_MARKER = False
# Remove legacy ICC markers from XMP if present.
# True  -> clears xmp-icc:all and xmp-photoshop:ICCProfile tags that might conflict
# False -> keeps existing ICC markers (default)

EMBED_JPEG_THUMBNAIL = False
# Embed a JPEG thumbnail (256px) in the JXL file EXIF metadata.
# True  -> creates a 256px JPEG preview and embeds it as EXIF thumbnail
#          Increases file size by ~10-30KB per image
#          Useful for fast preview in IrfanView, XnView, digiKam
#          (Windows Explorer with JXL codec usually ignores EXIF thumbnail)
# False -> no embedded thumbnail (default, smaller files)
#
# The thumbnail is generated from the PNG intermediate and injected via exiftool
# after the JXL encoding is complete.

DELETE_SOURCE = False
# [MODE 8 only] Whether to delete the source TIFF after successful encoding.
# Only deletes if ALL of the following are true:
#   - encode status is ok or overwrite (never deletes on error or skip)
#   - the JXL file exists at its final destination (after staging move if applicable)
#
# False (default) -> never delete source TIFFs. JXL and TIFF coexist in the same folder.
# True            -> delete source TIFF after confirmed successful encode.
#
# WARNING: irreversible. Only enable after testing on a small batch first.
# Has no effect on modes 0–7.

MULTIPAGE_TIFF_MODE = "ignore"
# How to handle TIFFs with more than one page.
# "ignore"    -> Always encode page 0 (series[0]) and silently ignore extra pages.
#                This is the original behavior and the default.
# "skip"      -> If the TIFF has more than one "real" page (non-thumbnail),
#                skip the entire file and log a warning.
# "split"     -> Encode each real page to a separate JXL:
#                page 0 -> photo.jxl, page N -> photo_pageN.jxl.
#                Thumbnails are handled according to THUMBNAIL_MODE below.
# "split_all" -> Encode every page, including thumbnails, to separate JXLs.
#
# A "real" page is one where is_reduced=False and is_subifd=False.
# Thumbnails/pyramids are detected via the standard TIFF SubfileType flags.

THUMBNAIL_MODE = "exclude"
# Only used when MULTIPAGE_TIFF_MODE is "split".
# "exclude" -> Do not encode thumbnail pages.
# "include" -> Encode thumbnail pages too, with a _thumbnail suffix.

THUMBNAIL_SUFFIX = "_thumbnail"
# Suffix appended to the output name when THUMBNAIL_MODE="include".
# Example: photo_page1_thumbnail.jxl

MULTIPAGE_XMP_MARKER = "jxlphoto-mpg:"
# Prefix for the group id appended to the dc:Relation XMP bag on split pages.
# The decoder only reconstructs a multi-page TIFF from files carrying a value
# with this prefix, so independently-named files like scan.jxl + scan_page2.jxl
# are never silently merged. Files without the marker decode as standalone TIFFs.
# dc:Relation is a list, so appending preserves any Relation the user already had.

ICC_INHERITED_XMP_FLAG = "jxlphoto-icc:inherited"
# Marker appended to dc:Relation when a page (page_idx > 0) has no own ICC and
# inherits the ICC from IFD0. The decoder uses this to reconstruct the original
# TIFF structure: inherited pages get no ICC tag, while the effective color is
# still applied via the inherited profile during JXL encoding.

SUBFILETYPE_XMP_PREFIX = "jxlphoto-subfiletype:"
# Prefix for the original SubfileType value stored in dc:Relation when it is
# non-zero (e.g. 4 for transparency/IR mask pages). The decoder restores the
# original subfiletype so scanner pages keep their semantic role.

GRAYSCALE_XMP_FLAG = "jxlphoto-grayscale"
# Marker appended to dc:Relation when a page is encoded as single-channel
# grayscale. The decoder restores a 2D TIFF page instead of expanding it to RGB.


# ─────────────────────────────────────────────
# USER SETTINGS - MODES CONFIGURATION
# ─────────────────────────────────────────────


# || MODE 0 SETTINGS ||
# No settings needed. Just use 
# py convert_jxl.py <input> <output> [--mode 0] [--workers N] [--overwrite] [--sync]
# or just py convert_jxl.py <input> , input can be file or directory. 


# || MODE 1 SETTINGS ||
CONVERTED_JXL_FOLDER = "converted_jxl"
# [MODE 1] Name of the subfolder created inside each TIFF folder.
# Example: .../TIFF_FOLDER/converted_jxl/photo.jxl

# || MODE 2 SETTINGS ||
# No settings needed. Flat: input directory -> output directory.
# py jxl_tiff_encoder.py <input_dir> <output_dir> --mode 2

# || MODE 3 SETTINGS ||
JXL_FOLDER_NAME = "JXL_16bits"
# [MODE 3] Subfolder created inside each TIFF folder for output.
# Example: .../TIFF_FOLDER/JXL_16bits/photo.jxl

# || MODE 4 SETTINGS ||
TIFF_SUFFIX_TO_REPLACE = "TIFF"
JXL_SUFFIX_REPLACE     = "JXL"
# [MODE 4] Replaces TIFF_SUFFIX_TO_REPLACE with JXL_SUFFIX_REPLACE in the folder name.
# Case-insensitive (TIFF, tiff, Tiff all match).
# Example: C1_Export_1_TIFF -> C1_Export_1_JXL

# || MODE 5 SETTINGS ||
# Sibling folder next to each TIFF folder — no extra settings needed.
# Example: .../JXL_FOLDER_NAME/photo.jxl  (uses JXL_FOLDER_NAME above)

# || MODES 6 and 7 SETTINGS ||
EXPORT_MARKER     = "_EXPORT"
EXPORT_JXL_FOLDER = "16B_JXL"
# [MODE 6/7] Uses EXPORT_MARKER as an anchor in the path.
# All JXLs go into EXPORT_MARKER/EXPORT_JXL_FOLDER/.
# Mode 6: processes ALL TIFFs inside EXPORT_MARKER recursively (ignores TIFFs outside).
# Mode 7: only processes TIFFs inside a specific subfolder of EXPORT_MARKER (ignores everything else).
#
# TIFFs inside EXPORT_MARKER: immediate subfolder (e.g. color space name) is dropped.
#
# Example (mode 7, EXPORT_TIFF_SUBFOLDER = "TIFF16"):
#   EXPORT_MARKER/TIFF16/photo.tif      ->  EXPORT_MARKER/EXPORT_JXL_FOLDER/photo.jxl
#   EXPORT_MARKER/AdobeRGB/photo.tif    ->  ignored
#   EXPORT_MARKER/sRGB/photo.tif        ->  ignored

EXPORT_TIFF_SUBFOLDER = ""
# [MODE 7] If set, only TIFFs in this specific subfolder of EXPORT_MARKER are processed,
# and this subfolder name is dropped from the output path.
# If empty (""), all TIFFs inside EXPORT_MARKER are processed (first subfolder is dropped).
# OBS: Empty value can cause filename collisions if different subfolders contain files
# with the same name (e.g. AdobeRGB/photo.tif and TIFF16/photo.tif).

# || MODE 8 SETTINGS ||
# No extra settings. Mode 8 converts TIFFs recursively and outputs JXLs in the same
# folder as each source TIFF. Controlled by DELETE_SOURCE above.
# Example: .../session/photo.tif -> .../session/photo.jxl


# ─────────────────────────────────────────────
# ─────────────────────────────────────────────

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SAFETY SETTINGS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DELETE_CONFIRM = True
# Only relevant when DELETE_SOURCE = True (mode 8).
# True  (default) -> require interactive confirmation before deleting any source file.
#   - Lossless conversion: type "yes" to confirm.
#   - Lossy conversion: type the current time (HHMM) shown on screen. This cannot
#     be automated and forces a conscious decision — you are about to delete TIFFs
#     that cannot be recovered from a lossy JXL.
# False -> skip all confirmations. Useful if running the script from another program
#         or automation pipeline. Leave True unless you have a specific reason.
#
# Recommendation: leave this True. It takes 3 seconds and prevents accidents.
# If you disable it, you are one misconfigured run away from losing originals.

STRIP_METADATA = False
# If True, strip all metadata from output (no EXIF/XMP preservation).
# Only encoding params (cjxl d=X e=Y) are added to dc:Description.
# Useful for creating clean JXL files without embedded metadata.
# Can also be set via --strip CLI flag.

SCRIPT_DIR = Path(__file__).parent
LOG_DIR    = SCRIPT_DIR / "Logs" / Path(__file__).stem
logger     = None
counter_lock = threading.Lock()
_counter = {"done": 0, "total": 0}
_d50_patch_count = {"applied": 0, "skipped": 0, "already_correct": 0, "skipped_needed": 0}
_d50_patch_lock = threading.Lock()

def setup_logger():
    global logger
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file  = LOG_DIR / f"{timestamp}.log"

    logger = logging.getLogger("jxl_convert")
    logger.setLevel(logging.INFO)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)

    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
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

def confirm_deletion_tiff(is_lossy: bool) -> bool:
    """Interactive confirmation before deleting source TIFFs (mode 8, DELETE_CONFIRM=True).
    Lossless: type 'yes'. Lossy: type the current time (HHMM) shown on screen.
    Returns True if confirmed, False if cancelled."""
    from datetime import datetime as _dt
    print()
    print()
    print()
    if is_lossy:
        print("  [!] WARNING -- DELETE_SOURCE is enabled")
        print(f"     Converting LOSSY (distance={CJXL_DISTANCE}) -- source TIFFs cannot be")
        print("     recovered from a lossy JXL. This deletion is IRREVERSIBLE.")
        now   = _dt.now()
        token = now.strftime("%H%M")
        print(f"     Current time: {now.strftime('%H:%M')}  ->  to confirm, type: {token}")
        print()
        try:
            answer = input("     > ").strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer == token:
            print("     Confirmed. Source TIFFs will be deleted after successful encode.")
            print()
            return True
        else:
            print("     Cancelled. No files will be deleted.")
            print()
            return False
    else:
        print("  [!] WARNING -- DELETE_SOURCE is enabled")
        print("     Source TIFFs will be deleted after successful lossless encode.")
        print("     Type 'yes' to confirm, anything else to cancel.")
        print()
        try:
            answer = input("     > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer == "yes":
            print("     Confirmed. Source TIFFs will be deleted after successful encode.")
            print()
            return True
        else:
            print("     Cancelled. No files will be deleted.")
            print()
            return False

def resolve_output(tiff_path: Path, mode: int, input_root: Path) -> Path:
    # Mode 0: single file in-place — handled in main() before calling this
    # Mode 1: single file -> converted_jxl/ subfolder — handled in main() before calling this

    if mode == 2:
        # Flat directory: input_root/photo.jxl
        return input_root / tiff_path.with_suffix(".jxl").name

    elif mode == 3:
        # Subfolder inside each TIFF folder
        return tiff_path.parent / JXL_FOLDER_NAME / tiff_path.with_suffix(".jxl").name

    elif mode == 4:
        # Rename folder replacing TIFF suffix with JXL suffix
        old_name = tiff_path.parent.name
        new_name = None
        for variant in [TIFF_SUFFIX_TO_REPLACE, TIFF_SUFFIX_TO_REPLACE.lower(),
                        TIFF_SUFFIX_TO_REPLACE.title()]:
            if variant in old_name:
                new_name = old_name.replace(variant, JXL_SUFFIX_REPLACE)
                break
        if new_name is None:
            new_name = old_name + "_" + JXL_SUFFIX_REPLACE
            logger.warning(f"'{TIFF_SUFFIX_TO_REPLACE}' not found in '{old_name}', using '{new_name}'")
        return tiff_path.parent.parent / new_name / tiff_path.with_suffix(".jxl").name

    elif mode == 5:
        # Sibling folder next to each TIFF folder
        return tiff_path.parent.parent / JXL_FOLDER_NAME / tiff_path.with_suffix(".jxl").name

    elif mode == 6:
        # EXPORT_MARKER anchor — only TIFFs INSIDE export marker folder
        parts = tiff_path.parts
        marker_lower = EXPORT_MARKER.lower()
        # Match folders starting or ending with EXPORT_MARKER case-insensitively
        export_idx = next((i for i, p in enumerate(parts)
                           if p.lower().startswith(marker_lower) or p.lower().endswith(marker_lower)), None)
        if export_idx is None:
            return None  # Skip files outside export marker folder

        export_dir = Path(*parts[:export_idx + 1])
        rel_parts = tiff_path.relative_to(export_dir).parts
        if len(rel_parts) > 1:
            rel = Path(*rel_parts[1:])
        else:
            rel = Path(rel_parts[0])
        return export_dir / EXPORT_JXL_FOLDER / rel.with_suffix(".jxl")

    elif mode == 7:
        # EXPORT_MARKER anchor — only TIFFs inside export marker/[subfolder]
        parts = tiff_path.parts
        marker_lower = EXPORT_MARKER.lower()
        export_idx = next((i for i, p in enumerate(parts)
                           if p.lower().startswith(marker_lower) or p.lower().endswith(marker_lower)), None)
        if export_idx is None:
            return None  # Skip files outside export marker folder

        export_dir = Path(*parts[:export_idx + 1])

        if EXPORT_TIFF_SUBFOLDER:
            anchor = export_dir / EXPORT_TIFF_SUBFOLDER
            if not _is_relative_to(tiff_path, anchor):
                return None  # Not inside the specific subfolder
            rel = tiff_path.relative_to(anchor)
        else:
            rel_parts = tiff_path.relative_to(export_dir).parts
            rel = Path(*rel_parts[1:]) if len(rel_parts) > 1 else Path(rel_parts[0])

        return export_dir / EXPORT_JXL_FOLDER / rel.with_suffix(".jxl")

    elif mode == 8:
        # In-place recursive: JXL goes to the same folder as the source TIFF.
        return tiff_path.parent / tiff_path.with_suffix(".jxl").name

    raise ValueError(f"Invalid mode: {mode}")

def extract_exif_raw(tiff_path, tmp_dir):
    arg_file = tmp_dir / "exif_extract.args"
    arg_file.write_text(f"-b\n-Exif\n{tiff_path}\n", encoding="utf-8")
    r = subprocess.run([_get_exiftool_cmd(), "-@", str(arg_file)], capture_output=True, timeout=60)
    if r.returncode == 0 and r.stdout and len(r.stdout) > 8:
        p = tmp_dir / f"{tiff_path.stem}.exif.bin"
        p.write_bytes(r.stdout)
        return p
    return None

def get_exif_software(tiff_path):
    """Extracts Software field from EXIF metadata.
    Returns software string or empty string if not found."""
    try:
        # Use -@ argument file to avoid wildcard expansion issues with brackets in paths
        with tempfile.TemporaryDirectory(prefix="exiftmp_") as tmp:
            arg_file = Path(tmp) / "args.txt"
            arg_file.write_text(f"-s\n-Software\n{tiff_path}\n", encoding="utf-8")
            r = subprocess.run(
                [_get_exiftool_cmd(), "-@", str(arg_file)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
            )
        if r.returncode == 0 and r.stdout:
            stdout = r.stdout.strip()
            # Parse "Software : Capture One 23" format
            if " : " in stdout:
                return stdout.split(" : ", 1)[1].strip()
            elif ":" in stdout:
                return stdout.split(":", 1)[1].strip()
    except Exception:
        pass
    return ""

def _is_d50_already_correct(icc_bytes: bytes) -> bool:
    """Check if ICC profile already has correct D50 illuminant values.
    Returns True if D50 bytes are already correct (no patch needed)."""
    if len(icc_bytes) < 80:
        return False
    CORRECT_D50 = bytes.fromhex("0000f6d6000100000000d32d")
    return icc_bytes[68:80] == CORRECT_D50

def should_apply_d50_patch(tiff_path):
    """Determine if D50 patch should be applied based on D50_PATCH_MODE setting.
    Returns True if patch should be applied, False otherwise."""
    mode = D50_PATCH_MODE.lower()

    if mode == "on":
        return True
    elif mode == "off":
        return False
    elif mode == "auto":
        software = get_exif_software(tiff_path).lower()
        for pattern in D50_PATCH_SOFTWARE_LIST:
            if pattern.lower() in software:
                return True
        return False
    else:
        # Invalid mode, default to auto
        logger.warning(f"Invalid D50_PATCH_MODE '{D50_PATCH_MODE}', using 'auto'")
        software = get_exif_software(tiff_path).lower()
        for pattern in D50_PATCH_SOFTWARE_LIST:
            if pattern.lower() in software:
                return True
        return False

def extract_icc_bytes(tiff_path):
    """Extract the ICC profile bytes from a TIFF using ExifTool (IFD0 by default).
    Returns the raw ICC bytes or None if none is found."""
    with tempfile.TemporaryDirectory(prefix="icc_", dir=TEMP_DIR) as tmp:
        arg_file = Path(tmp) / "icc_extract.args"
        arg_file.write_text(f"-b\n-ICC_Profile\n{tiff_path}\n", encoding="utf-8")
        r = subprocess.run([_get_exiftool_cmd(), "-@", str(arg_file)], capture_output=True, timeout=60)
    if r.returncode == 0 and len(r.stdout) > 128:
        return bytes(r.stdout)
    return None


def apply_d50_policy(icc_bytes, tiff_path):
    """Apply the D50 illuminant patch (if policy says so) and update stats.
    Accepts and returns raw ICC bytes. Safe to call with None (returns None)."""
    if icc_bytes is None:
        return None
    icc = bytearray(icc_bytes)

    if should_apply_d50_patch(tiff_path):
        was_correct = _is_d50_already_correct(bytes(icc))
        icc[68:80] = bytes.fromhex("0000f6d6000100000000d32d")  # fix D50 illuminant
        with _d50_patch_lock:
            _d50_patch_count["applied"] += 1
            if was_correct:
                _d50_patch_count["already_correct"] += 1
        logger.debug(f"Applied D50 patch to {Path(tiff_path).name}" + (" (was already correct)" if was_correct else ""))
    else:
        was_correct = _is_d50_already_correct(bytes(icc))
        with _d50_patch_lock:
            _d50_patch_count["skipped"] += 1
            if was_correct:
                _d50_patch_count["already_correct"] += 1
            else:
                _d50_patch_count["skipped_needed"] += 1
        logger.debug(f"D50 patch skipped for {Path(tiff_path).name}" + (" (already correct)" if was_correct else " (would have needed patch)"))

    return bytes(icc)


def get_page_icc(tif, page_idx: int):
    """Return (icc_bytes, inherited) for a TIFF page.

    Own ICC (tag 34675 on the page's IFD) wins. If absent and page_idx > 0,
    fall back to IFD0's ICC with inherited=True. If IFD0 also has none,
    return (None, False). This matches how TIFF viewers resolve ICC inheritance.
    """
    tag = tif.pages[page_idx].tags.get(34675)
    if tag is not None and tag.value:
        return bytes(tag.value), False
    if page_idx != 0:
        tag0 = tif.pages[0].tags.get(34675)
        if tag0 is not None and tag0.value:
            return bytes(tag0.value), True
    return None, False


# ═══════════════════════════════════════════════════════════════════════════════
# NEW FUNCTIONS FOR XMP PRESERVATION (XMP OVERWRITE BUG FIX)
# ═══════════════════════════════════════════════════════════════════════════════

def extract_xmp_original(tiff_path, tmp_dir):
    """Extract original XMP from TIFF as separate file for preservation.
    Returns path to XMP file or None if no XMP exists."""
    xmp_path = tmp_dir / f"{tiff_path.stem}_original.xmp"
    # Correct order: -o output.xmp -b -XMP input.tif
    r = subprocess.run(
        [_get_exiftool_cmd(), "-o", str(xmp_path), "-b", "-XMP", str(tiff_path)],
        capture_output=True, timeout=60
    )
    if xmp_path.exists() and xmp_path.stat().st_size > 0:
        return xmp_path
    return None

import re

def read_existing_description(xmp_path):
    """Read existing dc:description from XMP file if present.
    Returns empty string if not found."""
    if not xmp_path or not xmp_path.exists():
        return ""
    try:
        r = subprocess.run(
            [_get_exiftool_cmd(), "-s", "-XMP-dc:Description", str(xmp_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
        )
        if r.returncode == 0 and r.stdout:
            stdout = r.stdout.strip()
            # Filter out exiftool warnings from output
            lines = [ln for ln in stdout.splitlines() 
                     if not ln.strip().startswith(("Warning:", "[minor]", "[major]"))]
            stdout = "\n".join(lines).strip()
            if not stdout:
                return ""
            # Try multiple parsing strategies
            # Strategy 1: Split by " : " (standard exiftool output)
            if " : " in stdout:
                parts = stdout.split(" : ", 1)
                if len(parts) > 1:
                    return parts[1].strip()
            # Strategy 2: Use regex to find content after first colon
            match = re.search(r'^[^:]+:(.+)$', stdout, re.DOTALL)
            if match:
                return match.group(1).strip()
            # Strategy 3: If no colon, return whole string (might be just the value)
            return stdout
    except Exception as e:
        logger.debug(f"Failed to read description: {e}")
    return ""

def read_existing_creator_tool(xmp_path):
    """Read existing CreatorTool from XMP file if present.
    Returns empty string if not found."""
    if not xmp_path or not xmp_path.exists():
        return ""
    r = subprocess.run(
        [_get_exiftool_cmd(), "-s", "-XMP-xmp:CreatorTool", str(xmp_path)],
        capture_output=True, text=True, timeout=10
    )
    if r.returncode == 0 and r.stdout:
        stdout = r.stdout.strip()
        if " : " in stdout:
            return stdout.split(" : ", 1)[1].strip()
        elif ":" in stdout:
            return stdout.split(":", 1)[1].strip()
    return ""

def build_metadata_injection_args(tiff_path, write_path, tmp_dir, exif_bin, icc_bytes, xmp_original, strip_metadata=False):
    """Build exiftool arguments for metadata injection with proper XMP preservation.
    
    Strategy:
    1. Inject EXIF binary if available
    2. Copy all metadata from source TIFF (preserving original XMP)
    3. Add/modify specific XMP tags without overwriting the whole package
    
    Args:
        strip_metadata: If True, strip all metadata (no EXIF/XMP preservation)
    
    Returns path to arg file.
    """
    args_lines = ["-overwrite_original"]
    
    # If stripping metadata, only add minimal encoding info and exit
    if strip_metadata:
        # Strip all EXIF first
        args_lines.append("-exif:all=")
        # Strip all XMP (must come BEFORE setting new Description)
        args_lines.append("-xmp:all=")
        # Then set encoding params in dc:Description
        encoding_desc = f"cjxl d={CJXL_DISTANCE} e={CJXL_EFFORT}"
        args_lines.append(f"-xmp-dc:Description={encoding_desc}")
        # Target file
        args_lines.append(str(write_path))
        # Write args file
        arg_file = tmp_dir / "inject.args"
        arg_file.write_text("\n".join(args_lines), encoding="utf-8")
        return arg_file
    
    # 1. Inject raw EXIF binary blob if extracted
    if exif_bin:
        args_lines.append(f"-Exif<={exif_bin}")
    
    # 2. Copy tags from source file (preserves original XMP, EXIF, etc.)
    args_lines.append("-tagsfromfile")
    args_lines.append(str(tiff_path))
    args_lines.append("-exif:all")
    args_lines.append("-xmp:all")
    args_lines.append("--Orientation")  # Strip orientation to prevent double-rotation issues
    
    # 3. Handle encoding parameters and ICC embedding in XMP
    encoding_desc = f"cjxl d={CJXL_DISTANCE} e={CJXL_EFFORT}"
    
    # Read existing description from original XMP if available
    existing_desc = ""
    if xmp_original:
        existing_desc = read_existing_description(xmp_original)
    
    # Build final dc:Description (concatenate if original exists)
    if ENCODE_TAG_MODE == "xmp":
        if existing_desc and existing_desc != encoding_desc:
            # Concatenate: original | encoding_params
            final_description = f"{existing_desc} | {encoding_desc}"
        elif existing_desc:
            final_description = existing_desc
        else:
            final_description = encoding_desc
            
        # Set dc:Description with concatenated content
        args_lines.append(f"-xmp-dc:Description={final_description}")
        
    elif ENCODE_TAG_MODE == "software":
        # For software mode, we don't modify dc:Description
        # Instead, we update the EXIF Software field
        sw_arg = tmp_dir / "sw_read.args"
        sw_arg.write_text(f"-s\n-s\n-s\n-Software\n{tiff_path}\n", encoding="utf-8")
        r_sw = subprocess.run([_get_exiftool_cmd(), "-@", str(sw_arg)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        original_sw = r_sw.stdout.strip() if r_sw.returncode == 0 and r_sw.stdout else "cjxl"
        new_sw = f"{original_sw} | {encoding_desc}"
        args_lines.append(f"-Software={new_sw}")
    
    # 4. Embed ICC in XMP CreatorTool if enabled (for round-trip preservation)
    # This operates independently of ENCODE_TAG_MODE
    if EMBED_ICC_IN_JXL and icc_bytes:
        icc_b64 = base64.b64encode(icc_bytes).decode('ascii')
        
        # Read existing CreatorTool to concatenate if present
        existing_creator = ""
        if xmp_original:
            existing_creator = read_existing_creator_tool(xmp_original)
        
        # Build CreatorTool content: existing | ICC:base64 or just ICC:base64
        if existing_creator:
            creator_tool = f"{existing_creator} | ICC:{icc_b64}"
        else:
            creator_tool = f"ICC:{icc_b64}"
            
        args_lines.append(f"-xmp-xmp:CreatorTool={creator_tool}")
    
    # 5. Cleanup legacy ICC markers from XMP if requested
    if CLEANUP_XMP_ICC_MARKER:
        # Remove common legacy ICC marker tags that might conflict
        args_lines.append("-xmp-icc:all=")  # Clear any XMP ICC tags if present
        args_lines.append("-xmp-photoshop:ICCProfile=")  # Clear Photoshop ICC refs if any
    
    # 6. Ensure byte order consistency
    args_lines.append("-ExifByteOrder=Little-endian")
    
    # 7. Target file
    args_lines.append(str(write_path))
    
    # Write args file
    arg_file = tmp_dir / "inject.args"
    arg_file.write_text("\n".join(args_lines), encoding="utf-8")
    return arg_file

# ═══════════════════════════════════════════════════════════════════════════════

def make_png_bytes(img, icc_bytes=None):
    """Encodes a 16-bit numpy array as PNG in memory (pure Python, no temp file)."""
    import numpy as np

    if img.dtype.kind == 'f':
        raise ValueError(f"Unsupported TIFF dtype: {img.dtype}. Float TIFFs are not supported by make_png_bytes.")
    if img.dtype not in (np.uint8, np.uint16):
        raise ValueError(f"Unsupported TIFF dtype: {img.dtype}. Expected uint8 or uint16.")

    if img.ndim == 2:
        img = img[:, :, np.newaxis]
    if img.ndim != 3:
        raise ValueError(f"Unsupported image shape: {img.shape}. Expected 2D or 3D array.")

    h, w, c = img.shape
    if c == 1:
        color_type = 0
    elif c == 3:
        color_type = 2
    elif c == 4:
        color_type = 6
    else:
        raise ValueError(f"Unsupported channel count: {c}. Expected 1, 3, or 4.")

    def chunk(name, data):
        p = name + data
        return struct.pack(">I", len(data)) + p + struct.pack(">I", zlib.crc32(p) & 0xFFFFFFFF)

    # Scale 8-bit to 16-bit correctly (multiply by 257: 65535/255)
    if img.dtype == np.uint8:
        img_16 = img.astype(np.uint16) * 257  # 0-255 -> 0-65535
        img_be = img_16.astype(">u2")
    else:
        img_be = img.astype(">u2")
    raw = b"".join(b"\x00" + row.tobytes() for row in img_be)

    out = b"\x89PNG\r\n\x1a\n"
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, color_type, 0, 0, 0))
    if icc_bytes:
        out += chunk(b"iCCP", b"ICC Profile\x00\x00" + zlib.compress(icc_bytes))
    out += chunk(b"IDAT", zlib.compress(raw, 1))
    out += chunk(b"IEND", b"")
    return out

def reorder_jxl_boxes(jxl_path):
    """Reorders ISOBMFF boxes so Exif comes BEFORE the codestream.
    IrfanView reads JXL boxes linearly and stops at the codestream — Exif must come first.
    Supports both lossless (single jxlc) and lossy (multiple jxlp) JXL."""
    data = jxl_path.read_bytes()
    file_size = len(data)
    
    # Sanity check: reasonable file size (prevent OOM on malformed files)
    MAX_JXL_SIZE = 4 * 1024 * 1024 * 1024  # 4GB max
    if file_size > MAX_JXL_SIZE:
        raise RuntimeError(f"JXL file too large ({file_size} bytes), skipping box reorder")
    if file_size < 12:  # Minimum valid JXL: 12-byte signature
        return  # Too small to have boxes, leave as-is
    
    boxes = []
    i = 0
    MAX_BOX_SIZE = min(file_size, MAX_JXL_SIZE)
    
    while i < file_size:
        if i + 8 > file_size:
            break
        
        size = int.from_bytes(data[i:i+4], "big")
        name = data[i+4:i+8]
        
        # Validate size to prevent integer overflow / OOM
        if size > MAX_BOX_SIZE:
            raise RuntimeError(f"Invalid JXL box size {size} at offset {i}, possible corrupted file")
        if 1 < size < 8:
            raise RuntimeError(f"Invalid JXL box size {size} at offset {i}, minimum is 8")
        
        if size == 1:
            # Extended size (64-bit)
            if i + 16 > file_size:
                raise RuntimeError(f"Truncated extended box at offset {i}: file too short for 16-byte header")
            ext_size = int.from_bytes(data[i+8:i+16], "big")
            if ext_size > MAX_JXL_SIZE:
                raise RuntimeError(f"Invalid JXL extended box size {ext_size}, possible corrupted file")
            if ext_size < 16:
                raise RuntimeError(f"Invalid JXL extended box size {ext_size}, minimum is 16")
            if i + ext_size > file_size:
                raise RuntimeError(f"Truncated extended box at offset {i}: declared {ext_size} but only {file_size - i} bytes remain")
            header, payload = data[i:i+16], data[i+16:i+ext_size]
            size = ext_size
            boxes.append((name, header, payload))
        elif size == 0:
            # Box extends to end of file
            header, payload = data[i:i+8], data[i+8:]
            boxes.append((name, header, payload))
            break
        else:
            if i + size > file_size:
                raise RuntimeError(f"Truncated box at offset {i}: declared {size} but only {file_size - i} bytes remain")
            header, payload = data[i:i+8], data[i+8:i+size]
            boxes.append((name, header, payload))
        i += size if size != 0 else file_size

    # Split boxes into groups: metadata (before codestream) and codestream
    CODESTREAM = {b"jxlc", b"jxlp"}
    META_ORDER = [b"JXL ", b"ftyp", b"jxll"]  # required structure first
    META_EXTRA = [b"Exif", b"xml "]            # metadata we want before codestream

    # Group by type preserving appearance order (important for multiple jxlp)
    meta_order_boxes  = []
    meta_extra_boxes  = []
    codestream_boxes  = []
    other_boxes       = []

    for name, h, p in boxes:
        if name in {b"JXL ", b"ftyp", b"jxll"}:
            meta_order_boxes.append((name, h, p))
        elif name in {b"Exif", b"xml "}:
            meta_extra_boxes.append((name, h, p))
        elif name in CODESTREAM:
            codestream_boxes.append((name, h, p))
        else:
            other_boxes.append((name, h, p))

    # Final order: structure -> metadata -> codestream -> others
    out = b""
    for _, h, p in meta_order_boxes:  out += h + p
    for _, h, p in meta_extra_boxes:  out += h + p
    for _, h, p in codestream_boxes:  out += h + p
    for _, h, p in other_boxes:       out += h + p

    jxl_path.write_bytes(out)

def _is_thumbnail_page(page) -> bool:
    """Return True if the TIFF page is a thumbnail or pyramid level.

    Uses standard TIFF SubfileType flags exposed by tifffile:
    - is_reduced  : reduced-resolution image (thumbnail / pyramid)
    - is_subifd   : SubIFD of another page (pyramid / preview)
    """
    return bool(page.is_reduced or page.is_subifd)

def _analyze_tiff_pages(tiff_path: Path):
    """Analyze a TIFF and return lists of real/thumbnail page indices plus metadata.

    Returns (real_pages, thumb_pages, page_info) where page_info is a dict
    mapping page index to {'subfiletype': int, 'samples': int}.
    """
    page_info = {}
    with tifffile.TiffFile(str(tiff_path)) as tif:
        real_pages = []
        thumb_pages = []
        for i, page in enumerate(tif.pages):
            page_info[i] = {
                'subfiletype': int(page.subfiletype) if page.subfiletype else 0,
                'samples': int(page.samplesperpixel) if page.samplesperpixel else 1,
            }
            if _is_thumbnail_page(page):
                thumb_pages.append(i)
            else:
                real_pages.append(i)
    return real_pages, thumb_pages, page_info

def _page_output_name(stem: str, page_idx: int, is_thumbnail: bool) -> str:
    """Build output filename for a given page index."""
    if page_idx == 0:
        base = stem
    else:
        base = f"{stem}_page{page_idx}"
    if is_thumbnail:
        base += THUMBNAIL_SUFFIX
    return base + ".jxl"

def convert_one(tiff_path: Path, write_path: Path, final_path: Path, page_idx: int = 0,
                is_thumbnail: bool = False, subfiletype: int = 0, samples: int = 3, multipage_group: str = None):
    """
    Converts a single TIFF page to JXL with proper XMP preservation.
    write_path: where the JXL is initially written (staging or final destination)
    final_path: the final destination path (for overwrite checking and logging)
    page_idx:   which TIFF page to encode (default 0)
    is_thumbnail: whether this page was detected as a thumbnail
    subfiletype: original TIFF SubfileType value (e.g. 1 for thumbnail, 4 for mask)
    samples:     samples per pixel of the source page (1 for grayscale, 3 for RGB)
    multipage_group: if set, this page is part of a split multi-page TIFF; the
                     value is a stable group id written into XMP so the decoder
                     can reconstruct ONLY genuinely-split files and never merge
                     independently-named files that happen to look like pages.
    """
    overwritten = final_path.exists()

    if overwritten:
        if OVERWRITE is False:
            n, total = next_count()
            logger.info(f"[{n}/{total}] SKIP (exists) | {tiff_path.name}")
            # Key must match the ok/error returns ((path, page_idx)) so the
            # staging status_map and mode-8 grouping resolve skipped pages
            # correctly instead of defaulting to "error".
            return ((str(tiff_path), page_idx), "skipped", str(final_path), None)
        elif OVERWRITE == "smart":
            tiff_mtime = tiff_path.stat().st_mtime
            jxl_mtime  = final_path.stat().st_mtime
            if tiff_mtime <= jxl_mtime:
                n, total = next_count()
                logger.info(f"[{n}/{total}] SKIP (sync: JXL up to date) | {tiff_path.name}")
                return ((str(tiff_path), page_idx), "skipped", str(final_path), None)
            logger.info(f"  >SYNC: TIFF newer than JXL, reconverting | {tiff_path.name}")

    try:
        write_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # Another thread may have created it

    with tempfile.TemporaryDirectory(prefix="jxl_", dir=TEMP_DIR) as tmp:
        tmp_dir = Path(tmp)
        try:
            # 1. Extract raw EXIF binary
            exif_bin = extract_exif_raw(tiff_path, tmp_dir)

            # 2. Extract ICC profiles:
            #    - icc_bytes: patched for PNG iCCP (cjxl encoding)
            #    - icc_original: unmodified for XMP CreatorTool (round-trip preservation)
            #    Per-page: the page's own ICC tag (34675) wins; if absent, page N>0
            #    inherits IFD0's ICC for color interpretation, but we record that
            #    inheritance so the decoder can reproduce the original tag structure.
            # 3. Extract original XMP for preservation analysis (NEW)
            xmp_original = extract_xmp_original(tiff_path, tmp_dir)

            # 4. Read TIFF pixel data for the requested page
            with tifffile.TiffFile(str(tiff_path)) as tif:
                if page_idx >= len(tif.pages):
                    raise ValueError(
                        f"Page index {page_idx} out of range ({len(tif.pages)} pages)"
                    )
                page = tif.pages[page_idx]
                # Reject CMYK early — it would otherwise be mis-detected as RGBA
                photometric = page.photometric
                if photometric == tifffile.PHOTOMETRIC.SEPARATED:
                    raise ValueError("CMYK TIFFs are not supported")
                icc_original, icc_inherited = get_page_icc(tif, page_idx)
                icc_bytes = apply_d50_policy(icc_original, tiff_path)  # With D50 patch for cjxl
                img = page.asarray()
                # Convert 8-bit to 16-bit with proper scaling (multiply by 257)
                if img.dtype == np.uint8:
                    img = img.astype(np.uint16) * 257  # 0-255 -> 0-65535
                else:
                    img = img.astype(np.uint16)
            is_grayscale = (samples == 1)
            if img.ndim == 2:
                if not is_grayscale:
                    img = img[:, :, np.newaxis]
            elif img.ndim == 3 and img.shape[2] == 1:
                is_grayscale = True

            # If a page inherited its ICC from IFD0 but is actually grayscale, do not
            # apply the inherited RGB ICC. Grayscale pages with no own ICC tag are
            # encoded without an ICC profile, matching the original structure.
            if icc_inherited and is_grayscale:
                icc_original = None
                icc_inherited = False
                icc_bytes = None

            # 5. Encode PNG with ICC in iCCP chunk (for cjxl encoding)
            # --container=1 is required for lossy JXL (d>0): without it, cjxl outputs a raw
            # codestream and exiftool cannot inject EXIF. Do NOT use for lossless (d=0):
            # it changes how the ICC is stored (blob instead of native primaries) and
            # breaks color display in IrfanView.
            container_flag = ["--container=1"] if CJXL_DISTANCE > 0 else []

            # --modular=1 forces the Modular encoder for lossy output.
            # Only applied when CJXL_MODULAR=True and d>0 (lossless always uses Modular).
            # Modular lossy produces 2-3x larger files than VarDCT for photos.
            modular_flag = ["--modular=1"] if (CJXL_MODULAR and CJXL_DISTANCE > 0) else []

            if USE_RAM_FOR_PNG:
                png_input = make_png_bytes(img, icc_bytes)
                del img
                cjxl_cmd = ["cjxl", "-", str(write_path), "-d", str(CJXL_DISTANCE), "--effort", str(CJXL_EFFORT)] + container_flag + modular_flag
                r = subprocess.run(cjxl_cmd, input=png_input, capture_output=True, timeout=600)
                del png_input
            else:
                png_path = tmp_dir / f"{tiff_path.stem}.png"
                png_bytes = make_png_bytes(img, icc_bytes)
                del img
                png_path.write_bytes(png_bytes)
                del png_bytes
                cjxl_cmd = ["cjxl", str(png_path), str(write_path), "-d", str(CJXL_DISTANCE), "--effort", str(CJXL_EFFORT)] + container_flag + modular_flag
                r = subprocess.run(cjxl_cmd, capture_output=True, timeout=600)

            if r.returncode != 0:
                err = (r.stderr or b"").decode(errors='replace')[:200]
                raise RuntimeError(f"cjxl: {err}")

            # 6. Build and execute unified metadata injection (CORRECTED - replaces old steps 5+7)
            # This preserves original XMP, adds encoding tags, and embeds ICC if configured
            # Uses icc_original (unmodified) for round-trip preservation
            inject_args = build_metadata_injection_args(
                tiff_path, write_path, tmp_dir, exif_bin, icc_original, xmp_original, 
                strip_metadata=STRIP_METADATA
            )
            
            r2 = subprocess.run([_get_exiftool_cmd(), "-@", str(inject_args)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            if r2.returncode != 0:
                err_msg = (r2.stderr or r2.stdout or "no output")[:300].strip()
                raise RuntimeError(f"exiftool failed: {err_msg}")

            # 7. Reorder JXL boxes so Exif comes before the codestream
            reorder_jxl_boxes(write_path)

            # 8. Embed JPEG thumbnail if enabled
            if EMBED_JPEG_THUMBNAIL:
                try:
                    from PIL import Image, ImageCms
                    import io
                    # Apply user's PIL pixel limit setting (for large panoramas)
                    Image.MAX_IMAGE_PIXELS = PIL_MAX_IMAGE_PIXELS
                    # Read the original TIFF to generate thumbnail
                    with Image.open(str(tiff_path)) as img:
                        # Extract ICC profile BEFORE any conversion
                        icc_profile = img.info.get('icc_profile')
                        
                        # Convert color space if ICC profile exists
                        if icc_profile:
                            try:
                                rgb_profile = ImageCms.createProfile('sRGB')
                                src_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc_profile))
                                # Convert to sRGB using LittleCMS
                                img = ImageCms.profileToProfile(img, src_profile, rgb_profile)
                            except Exception as e:
                                logger.debug(f"  >Thumbnail color conversion failed: {e}")
                                # Fallback: convert to RGB without color management
                                if img.mode != 'RGB':
                                    img = img.convert('RGB')
                        else:
                            # No ICC, just convert to RGB
                            if img.mode != 'RGB':
                                img = img.convert('RGB')
                        
                        # Calculate thumbnail size (max 256px)
                        max_size = 256
                        ratio = min(max_size / img.width, max_size / img.height)
                        new_size = (int(img.width * ratio), int(img.height * ratio))
                        thumb = img.resize(new_size, Image.Resampling.LANCZOS)
                        # Save as JPEG temporary (sRGB, no ICC embedded)
                        thumb_path = tmp_dir / "thumbnail.jpg"
                        thumb.save(str(thumb_path), "JPEG", quality=85)
                        # Inject thumbnail into JXL
                        r_thumb = subprocess.run(
                            [_get_exiftool_cmd(), "-overwrite_original", "-ThumbnailImage<=" + str(thumb_path), str(write_path)],
                            capture_output=True, timeout=10
                        )
                        if r_thumb.returncode == 0:
                            logger.debug(f"  >Embedded sRGB thumbnail ({new_size[0]}x{new_size[1]})")
                        else:
                            logger.debug(f"  >Thumbnail embedding failed (non-critical)")
                except Exception as e:
                    logger.debug(f"  >Thumbnail PIL approach failed: {e}")
                    
                    # Fallback: try tifffile approach (only if PIL might be available)
                    if 'Image' not in locals():
                        logger.debug("  >PIL not available, skipping thumbnail entirely")
                    else:
                        try:
                            # Read TIFF with tifffile (preserves ICC and bit depth)
                            with tifffile.TiffFile(str(tiff_path)) as tif:
                                # Read main image data as numpy array
                                img_array = tif.pages[0].asarray()
                                # Extract ICC profile
                                icc_profile = None
                                try:
                                    icc_profile = tif.pages[0].icc_profile
                                except Exception:
                                    pass
                            
                            # Convert 16-bit to 8-bit if necessary
                            if img_array.dtype == np.uint16:
                                img_8bit = (img_array / 257).astype(np.uint8)  # 65535/255 = 257
                            else:
                                img_8bit = img_array
                            
                            # Ensure RGB
                            if img_8bit.ndim == 2:
                                img_8bit = np.stack([img_8bit] * 3, axis=-1)
                            elif img_8bit.shape[2] == 4:
                                img_8bit = img_8bit[:, :, :3]  # Remove alpha
                            
                            # Create PIL Image from array
                            pil_img = Image.fromarray(img_8bit)
                            
                            # Convert to sRGB using ICC profile (same as decoder)
                            if icc_profile:
                                try:
                                    rgb_profile = ImageCms.createProfile('sRGB')
                                    src_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc_profile))
                                    pil_img = ImageCms.profileToProfile(pil_img, src_profile, rgb_profile)
                                except Exception as e:
                                    logger.debug(f"  >Thumbnail color conversion failed: {e}, using original")
                            
                            # Calculate thumbnail size (max 256px)
                            max_size = 256
                            ratio = min(max_size / pil_img.width, max_size / pil_img.height)
                            new_size = (int(pil_img.width * ratio), int(pil_img.height * ratio))
                            thumb = pil_img.resize(new_size, Image.Resampling.LANCZOS)
                            
                            # Save as JPEG temporary (sRGB, no ICC embedded)
                            thumb_path = tmp_dir / "thumbnail.jpg"
                            thumb.save(str(thumb_path), "JPEG", quality=85)
                            
                            # Inject thumbnail into JXL
                            r_thumb = subprocess.run(
                                [_get_exiftool_cmd(), "-overwrite_original", "-ThumbnailImage<=" + str(thumb_path), str(write_path)],
                                capture_output=True, timeout=10
                            )
                            if r_thumb.returncode == 0:
                                logger.debug(f"  >Embedded sRGB thumbnail ({new_size[0]}x{new_size[1]})")
                            else:
                                logger.debug(f"  >Thumbnail embedding failed (non-critical)")
                        except Exception as e2:
                            logger.debug(f"  >Thumbnail fallback also failed: {e2}")

            # Multi-page group marker: tag split pages so the decoder reconstructs
            # only genuinely-split files. Stored as an ADDITIONAL value in the
            # dc:Relation list (a bag), so any Relation the user already had is
            # preserved — unlike a scalar field, appending never overwrites.
            # Absence of the marker means "standalone file" even if the name has _pageN.
            if multipage_group:
                try:
                    relation_args = [
                        "-XMP-dc:Relation+=" + MULTIPAGE_XMP_MARKER + multipage_group,
                    ]
                    if icc_inherited and page_idx > 0:
                        relation_args.append("-XMP-dc:Relation+=" + ICC_INHERITED_XMP_FLAG)
                    if subfiletype != 0:
                        relation_args.append("-XMP-dc:Relation+=" + SUBFILETYPE_XMP_PREFIX + str(subfiletype))
                    if is_grayscale:
                        relation_args.append("-XMP-dc:Relation+=" + GRAYSCALE_XMP_FLAG)
                    subprocess.run(
                        [_get_exiftool_cmd(), "-overwrite_original"] + relation_args +
                        [str(write_path)],
                        capture_output=True, timeout=10
                    )
                except Exception as e_mark:
                    logger.debug(f"  >Multi-page marker write failed (non-critical): {e_mark}")

            n, total = next_count()
            status = "overwrite" if overwritten else "ok"
            label  = "OVERWRITE" if overwritten else "OK"
            page_label = f" page{page_idx}" if page_idx > 0 else ""
            thumb_label = " [thumbnail]" if is_thumbnail else ""
            logger.info(f"[{n}/{total}] {label}{page_label}{thumb_label} | {tiff_path.name} -> {final_path}")
            return ((str(tiff_path), page_idx), status, str(final_path), tiff_path)

        except Exception as e:
            n, total = next_count()
            page_label = f" page{page_idx}" if page_idx > 0 else ""
            logger.error(f"[{n}/{total}] ERROR{page_label} | {tiff_path.name} | {e}")
            return ((str(tiff_path), page_idx), "error", str(e), None)

def convert_multipage(tiff_path: Path, output_dir: Path, mode: int = 0) -> list:
    """
    Decide which pages of a TIFF to encode based on MULTIPAGE_TIFF_MODE and
    THUMBNAIL_MODE, and return a list of (tiff_path, final_jxl_path, page_idx,
    is_thumbnail, subfiletype, samples) tuples ready for process_group.

    Returns an empty list if the file is skipped.

    May raise if the TIFF cannot be opened/analyzed; callers are expected to
    catch this and log a per-file error so one bad file doesn't kill the batch.
    """
    stem = tiff_path.stem
    mp_mode = MULTIPAGE_TIFF_MODE.lower()

    # Fast path: in ignore mode we only ever encode page 0, so there's no need
    # to open and analyze the whole TIFF here (it's opened again during the
    # actual conversion). This avoids a redundant per-file open across large
    # batches, and a corrupt file then fails inside convert_one — logged as a
    # single error — instead of aborting the whole run at planning time.
    if mp_mode == "ignore":
        final_jxl = output_dir / _page_output_name(stem, 0, False)
        # Assume RGB for the fast path; convert_one will re-read if needed.
        return [(tiff_path, final_jxl, 0, False, 0, 3)]

    real_pages, thumb_pages, page_info = _analyze_tiff_pages(tiff_path)

    pages_to_encode = []

    if mp_mode == "skip":
        if len(real_pages) > 1:
            logger.warning(f"SKIP multi-page TIFF ({len(real_pages)} real pages) | {tiff_path.name}")
            return []
        # Single real page (or only thumbnails) -> encode page 0
        info = page_info.get(0, {'subfiletype': 0, 'samples': 3})
        pages_to_encode.append((0, 0 in thumb_pages, info['subfiletype'], info['samples']))

    elif mp_mode == "split":
        for idx in real_pages:
            info = page_info.get(idx, {'subfiletype': 0, 'samples': 3})
            pages_to_encode.append((idx, False, info['subfiletype'], info['samples']))
        if THUMBNAIL_MODE.lower() == "include":
            for idx in thumb_pages:
                info = page_info.get(idx, {'subfiletype': 1, 'samples': 3})
                pages_to_encode.append((idx, True, info['subfiletype'], info['samples']))
        if not pages_to_encode:
            logger.warning(f"SKIP TIFF with no encodable pages | {tiff_path.name}")
            return []

    elif mp_mode == "split_all":
        for idx in real_pages:
            info = page_info.get(idx, {'subfiletype': 0, 'samples': 3})
            pages_to_encode.append((idx, False, info['subfiletype'], info['samples']))
        for idx in thumb_pages:
            info = page_info.get(idx, {'subfiletype': 1, 'samples': 3})
            pages_to_encode.append((idx, True, info['subfiletype'], info['samples']))
        if not pages_to_encode:
            logger.warning(f"SKIP TIFF with no pages | {tiff_path.name}")
            return []

    else:
        # Unknown mode, fall back to ignore
        info = page_info.get(0, {'subfiletype': 0, 'samples': 3})
        pages_to_encode.append((0, 0 in thumb_pages, info['subfiletype'], info['samples']))

    # Resolve final output path for each page
    results = []
    for page_idx, is_thumbnail, subfiletype, samples in pages_to_encode:
        name = _page_output_name(stem, page_idx, is_thumbnail)
        final_jxl = output_dir / name
        results.append((tiff_path, final_jxl, page_idx, is_thumbnail, subfiletype, samples))

    return results

def process_group(group_items: list, workers: int, mode: int = 0):
    """
    Converts a group of (tiff, final_jxl, page_idx, is_thumbnail, subfiletype, samples)
    items in parallel. If TEMP2_DIR is set, writes to staging first then moves in bulk.
    """
    use_staging = TEMP2_DIR is not None
    staging_dir = Path(TEMP2_DIR) if use_staging else None

    if use_staging:
        staging_dir.mkdir(parents=True, exist_ok=True)

    # A source TIFF that yields more than one output is a genuine split; every
    # page from it gets a stable group marker so the decoder can safely rejoin
    # them. Single-output TIFFs get no marker (standalone).
    outputs_per_tiff: Dict[str, int] = {}
    for tiff, _final_jxl, _page_idx, _is_thumbnail, _subfiletype, _samples in group_items:
        outputs_per_tiff[str(tiff.resolve())] = outputs_per_tiff.get(str(tiff.resolve()), 0) + 1

    tasks = []
    for tiff, final_jxl, page_idx, is_thumbnail, subfiletype, samples in group_items:
        if use_staging:
            # Unique staging name to avoid collisions across different source folders/pages
            write_jxl = staging_dir / f"{uuid.uuid4().hex}_{tiff.stem}_p{page_idx}.jxl"
        else:
            write_jxl = final_jxl
        tiff_key = str(tiff.resolve())
        group_id = tiff_key if outputs_per_tiff.get(tiff_key, 0) > 1 else None
        tasks.append((tiff, write_jxl, final_jxl, page_idx, is_thumbnail, subfiletype, samples, group_id))

    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(convert_one, t, w, f, p, th, sft, spl, g): (t, w, f, p, th, sft, spl, g)
                   for t, w, f, p, th, sft, spl, g in tasks}
        for fut in as_completed(futures):
            results.append(fut.result())

    # Move from staging to final destination in bulk
    if use_staging:
        moved = 0
        status_map = {r[0]: r[1] for r in results}
        for tiff, write_jxl, final_jxl, page_idx, _, _, _, _ in tasks:
            status = status_map.get((str(tiff), page_idx), "error")
            if status not in ("ok", "overwrite"):
                if status != "skipped":
                    logger.warning(f"  KEEP in staging ({status}) | {write_jxl.name}")
                continue
            if not write_jxl.exists():
                logger.warning(f"  KEEP (staging file missing) | {write_jxl.name}")
                continue
            final_jxl.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(write_jxl), str(final_jxl))
            moved += 1
        if moved:
            logger.info(f"  -> Moved {moved} file(s) from staging to final destination")

    # Delete source TIFFs after confirmed encode — only for mode 8, only after staging.
    # A source TIFF is deleted only if ALL of its encoded pages succeeded and every
    # resulting JXL exists at its final destination.
    if DELETE_SOURCE and mode == 8:
        deleted = 0
        # Group results by source TIFF path
        results_by_tiff: Dict[str, list] = {}
        for result in results:
            key = result[0][0]  # str(tiff_path)
            results_by_tiff.setdefault(key, []).append(result)

        for tiff_key, tiff_results in results_by_tiff.items():
            # Every page must have freshly succeeded. Skipped pages (r[3] is None)
            # intentionally block deletion: if a page was skipped we can't be sure
            # this run produced/verified it, so we keep the source rather than risk
            # deleting a TIFF whose JXLs weren't all (re)written this pass.
            all_ok = all(r[1] in ("ok", "overwrite") and r[3] is not None for r in tiff_results)
            if not all_ok:
                logger.warning(f"  KEEP source (not all pages succeeded) | {Path(tiff_key).name}")
                continue

            # All final JXLs must exist and pass integrity check
            can_delete = True
            for _, _, final_jxl, _, _, _, _, _ in tasks:
                if str(final_jxl) not in {r[2] for r in tiff_results}:
                    continue
                if not Path(final_jxl).exists():
                    can_delete = False
                    break
                if not _verify_jxl_integrity(Path(final_jxl)):
                    can_delete = False
                    break

            if not can_delete:
                logger.warning(f"  KEEP source (JXL integrity check failed) | {Path(tiff_key).name}")
                continue

            src_tiff = Path(tiff_key)
            src_tiff.unlink()
            deleted += 1
            logger.info(f"  DELETED source | {src_tiff.name}")
        if deleted:
            logger.info(f"  -> Deleted {deleted} source TIFF(s)")

    return results

def find_files_mode0(input_path: Path):
    seen = set()
    files = []
    for ext in ("*.tif", "*.tiff"):
        for f in input_path.glob(ext):
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                files.append(f)
    return files

def find_tiffs_recursive(input_path: Path):
    seen = set()
    files = []
    for ext in ("*.tif", "*.tiff"):
        for f in input_path.rglob(ext):
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                files.append(f)
    return files

def find_tiffs_mode6(input_path: Path):
    """Mode 6: only TIFFs inside folders containing EXPORT_MARKER in their path (any subfolder)."""
    all_tiffs = find_tiffs_recursive(input_path)
    filtered = []
    marker_lower = EXPORT_MARKER.lower()
    for t in all_tiffs:
        parts_str = list(t.parts)
        # Match folders starting or ending with EXPORT_MARKER case-insensitively
        export_idx = next((i for i, p in enumerate(parts_str)
                           if p.lower().startswith(marker_lower) or p.lower().endswith(marker_lower)), None)
        if export_idx is not None:
            filtered.append(t)
    return filtered

def find_tiffs_mode7(input_path: Path):
    """Mode 7: only TIFFs inside EXPORT_MARKER/EXPORT_TIFF_SUBFOLDER specific subfolder."""
    all_tiffs = find_tiffs_recursive(input_path)
    filtered = []
    marker_lower = EXPORT_MARKER.lower()
    for t in all_tiffs:
        parts_str = list(t.parts)
        export_idx = next((i for i, p in enumerate(parts_str)
                           if p.lower().startswith(marker_lower) or p.lower().endswith(marker_lower)), None)
        if export_idx is None:
            continue
        if EXPORT_TIFF_SUBFOLDER:
            if export_idx + 1 < len(parts_str) and parts_str[export_idx + 1] == EXPORT_TIFF_SUBFOLDER:
                filtered.append(t)
        else:
            filtered.append(t)
    return filtered

def main():
    parser = argparse.ArgumentParser(description="Batch TIFF 16-bit -> JPEG XL converter")
    parser.add_argument("input",             type=Path, help="Input root folder")
    parser.add_argument("output", nargs="?", type=Path, help="Output folder (mode 0 only)")
    parser.add_argument("--mode",            type=int, default=0, choices=[0,1,2,3,4,5,6,7,8])
    parser.add_argument("--workers",         type=int, default=min(os.cpu_count(), 16))
    parser.add_argument("--overwrite",       action="store_true",
                        help="Always overwrite existing JXLs")
    parser.add_argument("--sync",            action="store_true",
                        help="Only reconvert TIFFs newer than their existing JXL")
    parser.add_argument("--distance",        type=float, default=None,
                        help="JXL distance (0=lossless, 0.1=near-lossless, higher=more lossy)")
    parser.add_argument("--effort",          type=int, default=None, choices=range(1,11),
                        help="Compression effort 1-10 (default: 7)")
    parser.add_argument("--ram",            action="store_true", default=None,
                        help="Keep PNG intermediate in RAM (faster, more memory)")
    parser.add_argument("--no-ram",         action="store_true", default=None,
                        help="Write PNG intermediate to disk (slower, less memory)")
    parser.add_argument("--delete-source",   action="store_true",
                        help="Delete source TIFFs after successful encode (mode 8 only)")
    parser.add_argument("--multipage-mode",  type=str, default=None,
                        choices=["ignore", "skip", "split", "split_all"],
                        help="How to handle multi-page TIFFs: ignore (default), skip, split, split_all")
    parser.add_argument("--thumbnail-mode",  type=str, default=None,
                        choices=["exclude", "include"],
                        help="When splitting: exclude thumbnails (default) or include them")
    parser.add_argument("--thumbnail-suffix", type=str, default=None,
                        help="Suffix for thumbnail outputs when --thumbnail-mode=include (default: _thumbnail)")
    parser.add_argument("--dry-run",         action="store_true",
                        help="Show what would be converted without converting")
    parser.add_argument("--staging",         type=str, default=None,
                        help="Staging directory for output JXLs (reduces HDD seek contention)")
    parser.add_argument("--export-marker",  type=str, default="_EXPORT",
                        help="Folder name marker for modes 6/7 (default: _EXPORT)")
    parser.add_argument("--encode-tag",     type=str, default=None, choices=["xmp", "software", "off"],
                        help="Where to record encoding params: xmp (default), software, or off")
    parser.add_argument("--d50-patch",      type=str, default=None, choices=["on", "off", "auto"],
                        help="D50 illuminant patch: on (always), off (never), auto (detect from software)")
    parser.add_argument("--strip",           action="store_true",
                        help="Strip all metadata from output (no EXIF/XMP preservation)")
    parser.add_argument("--embed-thumbnail", action="store_true",
                        help="Embed a 256px JPEG thumbnail in EXIF for fast preview in viewers (~20KB)")
    args = parser.parse_args()

    global OVERWRITE, CJXL_DISTANCE, CJXL_EFFORT, USE_RAM_FOR_PNG, DELETE_SOURCE, TEMP2_DIR, ENCODE_TAG_MODE, D50_PATCH_MODE, EMBED_JPEG_THUMBNAIL, MULTIPAGE_TIFF_MODE, THUMBNAIL_MODE, THUMBNAIL_SUFFIX
    if args.sync:
        OVERWRITE = "smart"
    elif args.overwrite:
        OVERWRITE = True

    if args.delete_source:
        DELETE_SOURCE = True

    if args.distance is not None:
        if not 0 <= args.distance <= 15:
            parser.error("--distance must be between 0 and 15")
        CJXL_DISTANCE = args.distance
    if args.effort is not None:
        CJXL_EFFORT = args.effort
    if args.no_ram:
        USE_RAM_FOR_PNG = False
    elif args.ram is not None:
        USE_RAM_FOR_PNG = args.ram
    if args.staging is not None:
        TEMP2_DIR = args.staging
    if args.export_marker:
        global EXPORT_MARKER
        EXPORT_MARKER = args.export_marker
    if args.encode_tag is not None:
        ENCODE_TAG_MODE = args.encode_tag
    if args.d50_patch is not None:
        D50_PATCH_MODE = args.d50_patch

    if args.embed_thumbnail:
        EMBED_JPEG_THUMBNAIL = True

    if args.multipage_mode is not None:
        MULTIPAGE_TIFF_MODE = args.multipage_mode
    if args.thumbnail_mode is not None:
        THUMBNAIL_MODE = args.thumbnail_mode
    if args.thumbnail_suffix is not None:
        THUMBNAIL_SUFFIX = args.thumbnail_suffix

    # Handle --strip flag - store in global for use in convert_one
    global STRIP_METADATA
    if args.strip:
        STRIP_METADATA = True

    log_file = setup_logger()
    _modular_label = "modular" if (CJXL_MODULAR and CJXL_DISTANCE > 0) else "VarDCT"
    _delete_label  = f"delete_source=ON (confirm={'ON' if DELETE_CONFIRM else 'OFF'})" if DELETE_SOURCE else "delete_source=OFF"
    _overwrite_str = "sync" if args.sync else ("yes" if args.overwrite else ("smart" if OVERWRITE == "smart" else "no"))
    _tag_label     = ENCODE_TAG_MODE  # xmp, software, or off
    logger.info(
        f"Mode: {args.mode} | Effort: {CJXL_EFFORT} | "
        f"Distance: {CJXL_DISTANCE} ({'lossless' if CJXL_DISTANCE == 0 else f'lossy/{_modular_label}'}) | "
        f"RAM PNG: {USE_RAM_FOR_PNG} | Staging: {TEMP2_DIR or 'disabled'} | "
        f"Overwrite: {_overwrite_str} | Tag: {_tag_label} | D50 patch: {D50_PATCH_MODE} | {_delete_label} | "
        f"Multi-page: {MULTIPAGE_TIFF_MODE} | Thumbnail: {THUMBNAIL_MODE} | Workers: {args.workers}"
    )
    logger.info(f"Input: {args.input}")

    # Collect input files
    # Modes 0 and 1 accept a single file OR a directory
    if args.mode in (0, 1) and args.input.is_file():
        tiffs = [args.input]
        output_root = args.output or args.input.parent
    elif args.mode in (0, 1):
        # Directory input: flat (non-recursive)
        # Mode 0: output_root = output_dir if given, else same folder as each TIFF
        tiffs = find_files_mode0(args.input)
        output_root = args.output or args.input
    elif args.mode == 2:
        # Mode 2: recursive, all files to output_root
        tiffs = find_tiffs_recursive(args.input)
        output_root = args.output or args.input
        output_root.mkdir(parents=True, exist_ok=True)
    elif args.mode == 6:
        tiffs = find_tiffs_mode6(args.input)
        output_root = args.input
    elif args.mode == 7:
        tiffs = find_tiffs_mode7(args.input)
        output_root = args.input
    elif args.mode == 8:
        tiffs = find_tiffs_recursive(args.input)
        output_root = args.input
    else:
        tiffs = find_tiffs_recursive(args.input)
        output_root = args.input

    logger.info(f"Files found: {len(tiffs)}")

    # Build (tiff, final_jxl, page_idx, is_thumbnail) items.
    # Each TIFF may produce one or more JXLs depending on MULTIPAGE_TIFF_MODE.
    all_items = []
    skipped_files = 0
    analyze_errors = 0
    for t in tiffs:
        # Resolve the main JXL path just to know the output directory
        if args.mode == 0:
            if output_root != args.input:
                main_jxl = output_root / t.with_suffix(".jxl").name
            else:
                main_jxl = t.parent / t.with_suffix(".jxl").name
        elif args.mode == 1:
            main_jxl = t.parent / CONVERTED_JXL_FOLDER / t.with_suffix(".jxl").name
        elif args.mode == 2:
            if args.input.is_file():
                main_jxl = t.parent / CONVERTED_JXL_FOLDER / t.with_suffix(".jxl").name
            else:
                main_jxl = output_root / t.with_suffix(".jxl").name
        else:
            main_jxl = resolve_output(t, args.mode, args.input)

        if main_jxl is None:
            skipped_files += 1
            continue  # Skip files that don't match mode criteria (e.g., outside _EXPORT)

        try:
            items = convert_multipage(t, main_jxl.parent, args.mode)
        except Exception as e:
            # A corrupt/unreadable TIFF must not abort the whole batch at
            # planning time — log one error and move on, matching pre-multipage
            # behavior where such files failed individually during conversion.
            logger.error(f"SKIP (cannot analyze TIFF) | {t.name} | {e}")
            analyze_errors += 1
            continue
        all_items.extend(items)

    planned_msg = f"JXL outputs planned: {len(all_items)} (from {len(tiffs)} TIFFs, {skipped_files} skipped by mode"
    if analyze_errors:
        planned_msg += f", {analyze_errors} unreadable"
    planned_msg += ")"
    logger.info(planned_msg)
    _counter["total"] = len(all_items)

    # Dry run
    if args.dry_run:
        for t, j, page_idx, is_thumb, subfiletype, samples in all_items:
            thumb_label = " [thumbnail]" if is_thumb else ""
            gray_label = " [grayscale]" if samples == 1 else ""
            logger.info(f" DRY | {t.name} page{page_idx}{thumb_label}{gray_label} > {j}")
        logger.info(f"Dry run: {len(all_items)} output(s) would be generated from {len(tiffs)} TIFF(s).")
        return

    if args.mode == 8 and DELETE_SOURCE:
        logger.info("Mode 8 -- in-place recursive | DELETE_SOURCE=True: source TIFFs will be deleted after successful encode")
        if DELETE_CONFIRM:
            is_lossy = CJXL_DISTANCE > 0
            if not confirm_deletion_tiff(is_lossy):
                logger.info("Deletion not confirmed -- exiting.")
                return
    elif args.mode == 8:
        logger.info("Mode 8 -- in-place recursive | DELETE_SOURCE=False: TIFF and JXL will coexist")

    # Group by output folder (one bulk move per group)
    groups: Dict[Path, list] = {}
    for t, j, page_idx, is_thumb, subfiletype, samples in all_items:
        groups.setdefault(j.parent, []).append((t, j, page_idx, is_thumb, subfiletype, samples))

    logger.info(f"Output groups: {len(groups)}")

    ok = skipped = overwritten = synced = 0
    err = analyze_errors  # count TIFFs that couldn't be analyzed at planning time

    for dest_folder, group_items in groups.items():
        if len(groups) > 1:
            logger.info(f"-- Group: {dest_folder} ({len(group_items)} output(s))")

        results = process_group(group_items, args.workers, args.mode)

        for result in results:
            status = result[1]
            if   status == "ok":
                ok += 1
                if args.sync:
                    synced += 1
            elif status == "overwrite":
                ok += 1; overwritten += 1; synced += 1
            elif status == "skipped":   skipped += 1
            elif status == "error":     err += 1

    logger.info(f"\n{'-'*50}")
    if args.sync:
        logger.info(f"SYNC done: {synced} reconverted | {skipped} up to date | {err} errors")
        logger.info(f"  -> Reconverted: TIFFs newer than their existing JXL")
        logger.info(f"  -> Up to date: JXL is newer than or equal to TIFF")
    else:
        logger.info(f"Done: {ok} OK | {overwritten} overwrites | {skipped} skipped | {err} errors")

    # D50 patch summary
    applied = _d50_patch_count["applied"]
    d50_skipped = _d50_patch_count["skipped"]
    already_correct = _d50_patch_count["already_correct"]
    skipped_needed = _d50_patch_count["skipped_needed"]

    # Total files analyzed for D50 = those where patch was applied + those skipped
    total_analyzed = applied + d50_skipped
    # Total that actually needed the patch (were corrected + would have needed but were skipped)
    total_needed_patch = applied + skipped_needed

    if total_analyzed > 0:
        if D50_PATCH_MODE == "off":
            # For mode off, we still tracked correctness so user knows how many would have needed patch
            logger.info(f"D50 patch: {already_correct} already correct | {skipped_needed} would have needed (mode: off)")
        else:
            # Files that were actually patched (had wrong D50)
            actually_patched = applied - already_correct
            # Files that were skipped because they were already correct
            skipped_already_correct = d50_skipped - skipped_needed
            # Total files that needed patching vs total that were already correct
            total_needed_patch = actually_patched + skipped_needed
            logger.info(f"D50 patch: {actually_patched} applied | {skipped_already_correct} skipped (already correct) | {total_needed_patch} needed patch, {already_correct} already correct (mode: {D50_PATCH_MODE})")

    logger.info(f"Log: {log_file}")

if __name__ == "__main__":
    main()
