#!/usr/bin/env python3
"""
jxl_tiff_encoder.py — Batch TIFF 16-bit -&gt; JPEG XL converter with proper XMP preservation

Usage:
  py jxl_tiff_encoder.py <input> [output] --mode 0-8 [--workers N] [--overwrite] [--sync]

Requirements:
  pip install tifffile numpy
  cjxl / djxl  -&gt;  https://github.com/libjxl/libjxl/releases
  exiftool     -&gt;  https://exiftool.org
"""

import subprocess, os, tempfile, threading, zlib, struct, logging, sys, shutil, base64
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import argparse
import numpy as np
import tifffile

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
# True  -&gt; PNG intermediate stays entirely in RAM (faster, ~400MB RAM per worker)
# False -&gt; PNG is written to disk in TEMP_DIR (useful if RAM is limited)

TEMP_DIR = None
# Temporary directory for small intermediate files (EXIF binary, PNG if USE_RAM_FOR_PNG=False).
# None -&gt; use system temp (usually C:\Users\...\AppData\Local\Temp on Windows)
# Ex:  -&gt; r"E:\\temp_jxl"

TEMP2_DIR = None
# Staging directory for output JXLs during conversion.
# None -&gt; disabled: JXLs are written directly to their final destination.
# If set: JXLs are written here during conversion, then moved in bulk to the final
# destination when each folder group finishes. Separates read I/O (HDD with TIFFs)
# from write I/O (SSD for new JXLs), reducing seek contention on HDDs.
# Example: r"E:\\staging_jxl"

OVERWRITE = "smart"
# False   -&gt; skip JXLs that already exist at the final destination. Safe for resuming.
# True    -&gt; always overwrite existing JXLs.
# "smart" -&gt; same as --sync flag: only reconvert if the TIFF is newer than the JXL.
#            Useful after re-editing and re-exporting from Capture One.
# Never overwrites TIFFs or any other non-JXL format.

ENCODE_TAG_MODE = "xmp"
# Records encoding parameters (distance and effort) in the JXL metadata.
# "software" -&gt; appends to the EXIF Software field (e.g. "Capture One | cjxl d=0.5 e=7")
#              Visible in IrfanView, exiftool, and most viewers.
# "xmp"      -&gt; writes as XMP-dc:Description custom field
#              Cleaner — does not touch the original Software field
#              Visible in Windows Properties, but not in IrfanView
# "off"      -&gt; does not add anything
# NOTE: When EMBED_ICC_IN_JXL is True and ENCODE_TAG_MODE is "xmp",
# the encoding tag is concatenated to dc:Description, and ICC goes to CreatorTool.

EMBED_ICC_IN_JXL = True
# Embeds the original ICC profile as metadata in the JXL file.
# The ICC is NOT used by the JXL decoder (JXL uses native primaries),
# but is preserved for round-trip conversion back to TIFF/JPEG.
# This ensures the exact original ICC (with TRC curves, copyright, etc.)
# is available when converting JXL -&gt; TIFF, even for lossy JXLs.
# True  -&gt; embed ICC profile in JXL XMP CreatorTool (recommended, default)
# False -&gt; do not embed ICC (smaller file, but lossy JXLs will use generic ICC on decode)

CLEANUP_XMP_ICC_MARKER = False
# Remove legacy ICC markers from XMP if present.
# True  -&gt; clears xmp-icc:all and xmp-photoshop:ICCProfile tags that might conflict
# False -&gt; keeps existing ICC markers (default)

DELETE_SOURCE = False
# [MODE 8 only] Whether to delete the source TIFF after successful encoding.
# Only deletes if ALL of the following are true:
#   - encode status is ok or overwrite (never deletes on error or skip)
#   - the JXL file exists at its final destination (after staging move if applicable)
#
# False (default) -&gt; never delete source TIFFs. JXL and TIFF coexist in the same folder.
# True            -&gt; delete source TIFF after confirmed successful encode.
#
# WARNING: irreversible. Only enable after testing on a small batch first.
# Has no effect on modes 0–7.



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
# No settings needed. Flat: input directory -&gt; output directory.
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
# Example: C1_Export_1_TIFF -&gt; C1_Export_1_JXL

# || MODE 5 SETTINGS ||
# Sibling folder next to each TIFF folder — no extra settings needed.
# Example: .../JXL_FOLDER_NAME/photo.jxl  (uses JXL_FOLDER_NAME above)

# || MODES 6 and 7 SETTINGS ||
EXPORT_MARKER     = "_EXPORT"
EXPORT_JXL_FOLDER = "16B_JXL"
# [MODE 6/7] Uses EXPORT_MARKER as an anchor in the path.
# All JXLs go into EXPORT_MARKER/EXPORT_JXL_FOLDER/.
# Mode 6: processes TIFFs both inside and outside EXPORT_MARKER (same parent hierarchy).
# Mode 7: only processes TIFFs inside EXPORT_MARKER (ignores TIFFs outside).
#
# TIFFs inside EXPORT_MARKER: immediate subfolder (e.g. color space name) is dropped.
# TIFFs outside EXPORT_MARKER (mode 6 only): relative path from EXPORT_MARKER's parent is preserved.
#
# Example (mode 7, EXPORT_TIFF_SUBFOLDER = "TIFF16"):
#   EXPORT_MARKER/TIFF16/photo.tif      -&gt;  EXPORT_MARKER/EXPORT_JXL_FOLDER/photo.jxl
#   EXPORT_MARKER/AdobeRGB/photo.tif    -&gt;  ignored
#   EXPORT_MARKER/sRGB/photo.tif        -&gt;  ignored

EXPORT_TIFF_SUBFOLDER = ""
# [MODE 7] If set, only TIFFs in this specific subfolder of EXPORT_MARKER are processed,
# and this subfolder name is dropped from the output path.
# If empty (""), all TIFFs inside EXPORT_MARKER are processed (first subfolder is dropped).
# OBS: Empty value can cause filename collisions if different subfolders contain files
# with the same name (e.g. AdobeRGB/photo.tif and TIFF16/photo.tif).

# || MODE 8 SETTINGS ||
# No extra settings. Mode 8 converts TIFFs recursively and outputs JXLs in the same
# folder as each source TIFF. Controlled by DELETE_SOURCE above.
# Example: .../session/photo.tif -&gt; .../session/photo.jxl


# ─────────────────────────────────────────────
# ─────────────────────────────────────────────

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SAFETY SETTINGS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DELETE_CONFIRM = True
# Only relevant when DELETE_SOURCE = True (mode 8).
# True  (default) -&gt; require interactive confirmation before deleting any source file.
#   - Lossless conversion: type "yes" to confirm.
#   - Lossy conversion: type the current time (HHMM) shown on screen. This cannot
#     be automated and forces a conscious decision — you are about to delete TIFFs
#     that cannot be recovered from a lossy JXL.
# False -&gt; skip all confirmations. Useful if running the script from another program
#         or automation pipeline. Leave True unless you have a specific reason.
#
# Recommendation: leave this True. It takes 3 seconds and prevents accidents.
# If you disable it, you are one misconfigured run away from losing originals.


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

    logger = logging.getLogger("jxl_convert")
    logger.setLevel(logging.DEBUG)

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
        print(f"     Current time: {now.strftime('%H:%M')}  -&gt;  to confirm, type: {token}")
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
    # Mode 1: single file -&gt; converted_jxl/ subfolder — handled in main() before calling this

    if mode == 2:
        # Flat directory: input_root/photo.jxl
        return input_root / tiff_path.with_suffix(".jxl").name

    elif mode == 3:
        # Subfolder inside each TIFF folder
        return tiff_path.parent / CONVERTED_JXL_FOLDER / tiff_path.with_suffix(".jxl").name

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
        # _EXPORT anchor — only TIFFs INSIDE _EXPORT (ignores everything outside)
        parts = tiff_path.parts
        export_idx = next((i for i, p in enumerate(parts) if EXPORT_MARKER in p), None)
        if export_idx is None:
            return None  # Skip files outside _EXPORT

        export_dir = Path(*parts[:export_idx + 1])
        rel_parts = tiff_path.relative_to(export_dir).parts
        if len(rel_parts) > 1:
            rel = Path(*rel_parts[1:])
        else:
            rel = Path(rel_parts[0])
        return export_dir / EXPORT_JXL_FOLDER / rel.with_suffix(".jxl")

    elif mode == 7:
        # _EXPORT anchor — only TIFFs inside _EXPORT/[subfolder]
        parts = tiff_path.parts
        export_idx = next((i for i, p in enumerate(parts) if EXPORT_MARKER in p), None)
        if export_idx is None:
            return None  # Skip files outside _EXPORT

        export_dir = Path(*parts[:export_idx + 1])

        if EXPORT_TIFF_SUBFOLDER:
            anchor = export_dir / EXPORT_TIFF_SUBFOLDER
            if not tiff_path.is_relative_to(anchor):
                return None  # Not inside the specific subfolder
            rel = tiff_path.relative_to(anchor)
        else:
            rel_parts = tiff_path.relative_to(export_dir).parts
            rel = Path(*rel_parts[1:]) if len(rel_parts) > 1 else Path(rel_parts[0])

        return export_dir / EXPORT_JXL_FOLDER / rel.with_suffix(".jxl")

    elif mode == 8:
        # In-place recursive: JXL goes to the same folder as the source TIFF.
        return tiff_path.parent / tiff_path.with_suffix(".jxl").name

    raise ValueError(f"Modo invalido: {mode}")

def extract_exif_raw(tiff_path, tmp_dir):
    arg_file = tmp_dir / "exif_extract.args"
    arg_file.write_text(f"-b\n-Exif\n{tiff_path}\n", encoding="utf-8")
    r = subprocess.run(["exiftool", "-@", str(arg_file)], capture_output=True)
    if r.returncode == 0 and len(r.stdout) > 8:
        p = tmp_dir / f"{tiff_path.stem}.exif.bin"
        p.write_bytes(r.stdout)
        return p
    return None

def extract_icc_fixed(tiff_path):
    """Extracts ICC profile and patches D50 illuminant rounding error (Capture One non-conformance).
    Safe for any ICC profile: if bytes are already correct, the patch has no effect.
    Returns patched ICC bytes or None."""
    with tempfile.TemporaryDirectory(prefix="icc_", dir=TEMP_DIR) as tmp:
        arg_file = Path(tmp) / "icc_extract.args"
        arg_file.write_text(f"-b\n-ICC_Profile\n{tiff_path}\n", encoding="utf-8")
        r = subprocess.run(["exiftool", "-@", str(arg_file)], capture_output=True)
    if r.returncode == 0 and len(r.stdout) > 128:
        icc = bytearray(r.stdout)
        icc[68:80] = bytes.fromhex("0000f6d6000100000000d32d")  # fix D50 illuminant
        return bytes(icc)
    return None

def extract_icc_original(tiff_path):
    """Extracts original ICC profile WITHOUT patching.
    Used for round-trip preservation (XMP CreatorTool).
    Returns original ICC bytes or None."""
    with tempfile.TemporaryDirectory(prefix="icc_", dir=TEMP_DIR) as tmp:
        arg_file = Path(tmp) / "icc_extract.args"
        arg_file.write_text(f"-b\n-ICC_Profile\n{tiff_path}\n", encoding="utf-8")
        r = subprocess.run(["exiftool", "-@", str(arg_file)], capture_output=True)
    if r.returncode == 0 and len(r.stdout) > 128:
        return bytes(r.stdout)  # Return original, unmodified ICC
    return None

# ═══════════════════════════════════════════════════════════════════════════════
# NOVAS FUNÇÕES PARA PRESERVAÇÃO DE XMP (CORREÇÃO DO BUG DE OVERWRITE)
# ═══════════════════════════════════════════════════════════════════════════════

def extract_xmp_original(tiff_path, tmp_dir):
    """Extract original XMP from TIFF as separate file for preservation.
    Returns path to XMP file or None if no XMP exists."""
    xmp_path = tmp_dir / f"{tiff_path.stem}_original.xmp"
    # Correct order: -o output.xmp -b -XMP input.tif
    r = subprocess.run(
        ["exiftool", "-o", str(xmp_path), "-b", "-XMP", str(tiff_path)],
        capture_output=True
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
            ["exiftool", "-s", "-XMP-dc:Description", str(xmp_path)],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0 and r.stdout:
            stdout = r.stdout.strip()
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
            if stdout and not stdout.startswith("Warning"):
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
        ["exiftool", "-s", "-XMP-xmp:CreatorTool", str(xmp_path)],
        capture_output=True, text=True
    )
    if r.returncode == 0 and r.stdout:
        stdout = r.stdout.strip()
        if " : " in stdout:
            return stdout.split(" : ", 1)[1].strip()
        elif ":" in stdout:
            return stdout.split(":", 1)[1].strip()
    return ""

def build_metadata_injection_args(tiff_path, write_path, tmp_dir, exif_bin, icc_bytes, xmp_original):
    """Build exiftool arguments for metadata injection with proper XMP preservation.
    
    Strategy:
    1. Inject EXIF binary if available
    2. Copy all metadata from source TIFF (preserving original XMP)
    3. Add/modify specific XMP tags without overwriting the whole package
    
    Returns path to arg file.
    """
    args_lines = ["-overwrite_original"]
    
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
        r_sw = subprocess.run(["exiftool", "-@", str(sw_arg)], capture_output=True, text=True)
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
    h, w, c = img.shape
    color_type = 2 if c == 3 else (0 if c == 1 else 6)

    def chunk(name, data):
        p = name + data
        return struct.pack(">I", len(data)) + p + struct.pack(">I", zlib.crc32(p) & 0xFFFFFFFF)

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
    boxes = []
    i = 0
    while i < len(data):
        if i + 8 > len(data): break
        size = int.from_bytes(data[i:i+4], "big")
        name = data[i+4:i+8]
        if size == 1:
            size = int.from_bytes(data[i+8:i+16], "big")
            header, payload = data[i:i+16], data[i+16:i+size]
        elif size == 0:
            header, payload = data[i:i+8], data[i+8:]
            boxes.append((name, header, payload))
            break
        else:
            header, payload = data[i:i+8], data[i+8:i+size]
        boxes.append((name, header, payload))
        i += size if size != 0 else len(data)

    # Separa boxes em grupos: metadados (antes do codestream) e codestream
    CODESTREAM = {b"jxlc", b"jxlp"}
    META_ORDER = [b"JXL ", b"ftyp", b"jxll"]  # estrutura obrigatória primeiro
    META_EXTRA = [b"Exif", b"xml "]            # metadados que queremos antes do codestream

    # Agrupa por tipo preservando ordem de aparição (importante para múltiplos jxlp)
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

    # Final order: structure -&gt; metadata -&gt; codestream -&gt; others
    out = b""
    for _, h, p in meta_order_boxes:  out += h + p
    for _, h, p in meta_extra_boxes:  out += h + p
    for _, h, p in codestream_boxes:  out += h + p
    for _, h, p in other_boxes:       out += h + p

    jxl_path.write_bytes(out)

def convert_one(tiff_path: Path, write_path: Path, final_path: Path):
    """
    Converts a single TIFF to JXL with proper XMP preservation.
    write_path: where the JXL is initially written (staging or final destination)
    final_path: the final destination path (for overwrite checking and logging)
    """
    overwritten = final_path.exists()

    if overwritten:
        if OVERWRITE == False:
            n, total = next_count()
            logger.info(f"[{n}/{total}] SKIP (exists) | {tiff_path.name}")
            return (str(tiff_path), "skipped", str(final_path), None)
        elif OVERWRITE == "smart":
            tiff_mtime = tiff_path.stat().st_mtime
            jxl_mtime  = final_path.stat().st_mtime
            if tiff_mtime <= jxl_mtime:
                n, total = next_count()
                logger.info(f"[{n}/{total}] SKIP (sync: JXL up to date) | {tiff_path.name}")
                return (str(tiff_path), "skipped", str(final_path), None)
            logger.info(f"  >SYNC: TIFF newer than JXL, reconverting | {tiff_path.name}")

    write_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="jxl_", dir=TEMP_DIR) as tmp:
        tmp_dir = Path(tmp)
        try:
            # 1. Extract raw EXIF binary
            exif_bin = extract_exif_raw(tiff_path, tmp_dir)

            # 2. Extract ICC profiles:
            #    - icc_bytes: patched for PNG iCCP (cjxl encoding)
            #    - icc_original: unmodified for XMP CreatorTool (round-trip preservation)
            icc_bytes = extract_icc_fixed(tiff_path)  # With D50 patch for cjxl
            icc_original = extract_icc_original(tiff_path)  # Original for preservation

            # 3. Extract original XMP for preservation analysis (NEW)
            xmp_original = extract_xmp_original(tiff_path, tmp_dir)

            # 4. Read TIFF pixel data (series[0] = main image, ignores thumbnails)
            with tifffile.TiffFile(str(tiff_path)) as tif:
                img = tif.series[0].asarray().astype(np.uint16)
            if img.ndim == 2:
                img = img[:, :, np.newaxis]

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
                r = subprocess.run(cjxl_cmd, input=png_input, capture_output=True)
                del png_input
            else:
                png_path = tmp_dir / f"{tiff_path.stem}.png"
                png_bytes = make_png_bytes(img, icc_bytes)
                del img
                png_path.write_bytes(png_bytes)
                del png_bytes
                cjxl_cmd = ["cjxl", str(png_path), str(write_path), "-d", str(CJXL_DISTANCE), "--effort", str(CJXL_EFFORT)] + container_flag + modular_flag
                r = subprocess.run(cjxl_cmd, capture_output=True)

            if r.returncode != 0:
                err = (r.stderr or b"").decode(errors='replace')[:200]
                raise RuntimeError(f"cjxl: {err}")

            # 6. Build and execute unified metadata injection (CORRECTED - replaces old steps 5+7)
            # This preserves original XMP, adds encoding tags, and embeds ICC if configured
            # Uses icc_original (unmodified) for round-trip preservation
            inject_args = build_metadata_injection_args(
                tiff_path, write_path, tmp_dir, exif_bin, icc_original, xmp_original
            )
            
            r2 = subprocess.run(["exiftool", "-@", str(inject_args)], 
                              capture_output=True, text=True)
            if r2.returncode != 0:
                err_msg = (r2.stderr or r2.stdout or "no output")[:300].strip()
                raise RuntimeError(f"exiftool failed: {err_msg}")

            # 7. Reorder JXL boxes so Exif comes before the codestream
            reorder_jxl_boxes(write_path)

            n, total = next_count()
            status = "overwrite" if overwritten else "ok"
            label  = "OVERWRITE" if overwritten else "OK"
            logger.info(f"[{n}/{total}] {label} | {tiff_path.name} -&gt; {final_path}")
            return (str(tiff_path), status, str(final_path), tiff_path)

        except Exception as e:
            n, total = next_count()
            logger.error(f"[{n}/{total}] ERROR | {tiff_path.name} | {e}")
            return (str(tiff_path), "error", str(e), None)

def process_group(group_pairs: list, workers: int, mode: int = 0):
    """
    Converts a group of (tiff, final_jxl) pairs in parallel.
    If TEMP2_DIR is set, writes to staging first then moves in bulk.
    """
    use_staging = TEMP2_DIR is not None
    staging_dir = Path(TEMP2_DIR) if use_staging else None

    if use_staging:
        staging_dir.mkdir(parents=True, exist_ok=True)

    
    tasks = []
    for tiff, final_jxl in group_pairs:
        if use_staging:
            # Unique staging name to avoid collisions across different source folders
            write_jxl = staging_dir / f"{tiff.parent.name}__{tiff.stem}.jxl"
        else:
            write_jxl = final_jxl
        tasks.append((tiff, write_jxl, final_jxl))

    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(convert_one, t, w, f): (t, w, f) for t, w, f in tasks}
        for fut in as_completed(futures):
            results.append(fut.result())

    # Move from staging to final destination in bulk
    if use_staging:
        moved = 0
        for tiff, write_jxl, final_jxl in tasks:
            if write_jxl.exists():
                final_jxl.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(write_jxl), str(final_jxl))
                moved += 1
        if moved:
            logger.info(f"  -&gt; Moved {moved} file(s) from staging to final destination")

    # Delete source TIFFs after confirmed encode — only for mode 8, only after staging.
    # Checks: encode succeeded + JXL exists at final destination.
    if DELETE_SOURCE and mode == 8:
        deleted = 0
        src_map = {str(t): (t, f) for t, _, f in tasks}
        for result in results:
            status    = result[1]
            src_tiff  = result[3]
            if status not in ("ok", "overwrite") or src_tiff is None:
                continue
            _, final_jxl = src_map.get(result[0], (None, None))
            if final_jxl is None or not final_jxl.exists():
                logger.warning(f"  KEEP (JXL not found at destination) | {src_tiff.name}")
                continue
            src_tiff.unlink()
            deleted += 1
            logger.info(f"  DELETED source | {src_tiff.name}")
        if deleted:
            logger.info(f"  -&gt; Deleted {deleted} source TIFF(s)")

    return results

def find_files_mode0(input_path: Path):
    seen = set()
    files = []
    for ext in ("*.jpg", "*.jpeg", "*.tif", "*.tiff"):
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
    for t in all_tiffs:
        parts_str = list(t.parts)
        export_idx = next((i for i, p in enumerate(parts_str) if EXPORT_MARKER in p), None)
        if export_idx is not None:
            filtered.append(t)
    return filtered

def find_tiffs_mode7(input_path: Path):
    """Mode 7: only TIFFs inside _EXPORT/EXPORT_TIFF_SUBFOLDER specific subfolder."""
    all_tiffs = find_tiffs_recursive(input_path)
    filtered = []
    for t in all_tiffs:
        parts_str = list(t.parts)
        export_idx = next((i for i, p in enumerate(parts_str) if EXPORT_MARKER in p), None)
        if export_idx is None:
            continue
        if EXPORT_TIFF_SUBFOLDER:
            if export_idx + 1 < len(parts_str) and parts_str[export_idx + 1] == EXPORT_TIFF_SUBFOLDER:
                filtered.append(t)
        else:
            filtered.append(t)
    return filtered

def main():
    parser = argparse.ArgumentParser(description="Batch TIFF 16-bit -&gt; JPEG XL converter")
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
    parser.add_argument("--dry-run",         action="store_true",
                        help="Show what would be converted without converting")
    parser.add_argument("--staging",         type=str, default=None,
                        help="Staging directory for output JXLs (reduces HDD seek contention)")
    parser.add_argument("--encode-tag",     type=str, default=None, choices=["xmp", "software", "off"],
                        help="Where to record encoding params: xmp (default), software, or off")
    args = parser.parse_args()

    global OVERWRITE, CJXL_DISTANCE, CJXL_EFFORT, USE_RAM_FOR_PNG, DELETE_SOURCE, TEMP2_DIR, ENCODE_TAG_MODE
    if args.sync:
        OVERWRITE = "smart"
    elif args.overwrite:
        OVERWRITE = True

    if args.delete_source:
        DELETE_SOURCE = True

    if args.distance is not None:
        CJXL_DISTANCE = args.distance
    if args.effort is not None:
        CJXL_EFFORT = args.effort
    if args.ram is not None:
        USE_RAM_FOR_PNG = args.ram
    elif args.no_ram is not None:
        USE_RAM_FOR_PNG = not args.no_ram
    if args.staging is not None:
        TEMP2_DIR = args.staging
    if args.encode_tag is not None:
        ENCODE_TAG_MODE = args.encode_tag

    log_file = setup_logger()
    _modular_label = "modular" if (CJXL_MODULAR and CJXL_DISTANCE > 0) else "VarDCT"
    _delete_label  = f"delete_source=ON (confirm={'ON' if DELETE_CONFIRM else 'OFF'})" if DELETE_SOURCE else "delete_source=OFF"
    _overwrite_str = "sync" if args.sync else ("yes" if args.overwrite else "no")
    _tag_label     = ENCODE_TAG_MODE  # xmp, software, or off
    logger.info(
        f"Mode: {args.mode} | Effort: {CJXL_EFFORT} | "
        f"Distance: {CJXL_DISTANCE} ({'lossless' if CJXL_DISTANCE == 0 else f'lossy/{_modular_label}'}) | "
        f"RAM PNG: {USE_RAM_FOR_PNG} | Staging: {TEMP2_DIR or 'disabled'} | "
        f"Overwrite: {_overwrite_str} | Tag: {_tag_label} | {_delete_label} | Workers: {args.workers}"
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
    _counter["total"] = len(tiffs)

    # Build (tiff, jxl_destination) pairs
    pairs = []
    for t in tiffs:
        if args.mode == 0:
            # File or directory: if output_root differs from source parent, use it
            if output_root != args.input:
                jxl = output_root / t.with_suffix(".jxl").name
            else:
                jxl = t.parent / t.with_suffix(".jxl").name
        elif args.mode in (1, 2):
            if args.input.is_file():
                # Single file -&gt; converted_jxl/ subfolder
                jxl = t.parent / CONVERTED_JXL_FOLDER / t.with_suffix(".jxl").name
            else:
                jxl = output_root / t.with_suffix(".jxl").name
        else:
            jxl = resolve_output(t, args.mode, args.input)
        if jxl is None:
            continue  # Skip files that don't match mode criteria (e.g., outside _EXPORT)
        pairs.append((t, jxl))

    # Dry run
    if args.dry_run:
        for t, j in pairs:
            logger.info(f" DRY | {t.name} >{j}")
        logger.info(f"Dry run: {len(pairs)} file(s) would be converted.")
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
    groups: dict[Path, list] = {}
    for t, j in pairs:
        groups.setdefault(j.parent, []).append((t, j))

    logger.info(f"Output groups: {len(groups)}")

    ok = err = skipped = overwritten = synced = 0

    for dest_folder, group_pairs in groups.items():
        if len(groups) > 1:
            logger.info(f"-- Group: {dest_folder} ({len(group_pairs)} file(s))")

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
        logger.info(f"  -&gt; Reconverted: TIFFs newer than their existing JXL")
        logger.info(f"  -&gt; Up to date: JXL is newer than or equal to TIFF")
    else:
        logger.info(f"Done: {ok} OK | {overwritten} overwrites | {skipped} skipped | {err} errors")
    logger.info(f"Log: {log_file}")

if __name__ == "__main__":
    main()
