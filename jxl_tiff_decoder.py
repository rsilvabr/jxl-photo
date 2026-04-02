#!/usr/bin/env python3
"""
jxl_tiff_decoder.py — Batch JPEG XL → TIFF 16-bit converter with ICC preservation

Usage:
 py jxl_tiff_decoder.py input/ [--mode 0-8] [--workers N] [--overwrite] [--sync]
 py jxl_tiff_decoder.py photo.jxl --mode 1

Requirements:
 pip install tifffile numpy Pillow
 djxl (libjxl) → https://github.com/libjxl/libjxl/releases
 exiftool → https://exiftool.org
"""

import subprocess, os, tempfile, threading, logging, sys, shutil, re, base64, struct
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import argparse
import numpy as np
from PIL import Image
try:
    from PIL import ImageCms
except ImportError:
    ImageCms = None
import tifffile

# ─────────────────────────────────────────────
# USER SETTINGS - GENERAL
# ─────────────────────────────────────────────

DJXL_OUTPUT_DEPTH = 16
# Output bit depth for TIFF (8 or 16).
# 16 is recommended for maximum quality preservation (especially for further editing).
# 8 can be used for web/delivery to save ~50% space.

TIFF_COMPRESSION = "zip"
# TIFF compression method. Options: "uncompressed", "lzw", "zip"
# "uncompressed" - No compression, largest files, fastest write
# "lzw" - LZW compression, good compatibility, medium size
# "zip" - Deflate/ZIP compression, best compression, recommended (default)

ADD_JPEG_PREVIEW = True
# Add an embedded JPEG preview/thumbnail to the TIFF file.
# True → Add JPEG preview (default, recommended)
# False → No preview, slightly smaller file

JPEG_PREVIEW_SIZE = 1024
# Maximum dimension (width or height) of the JPEG preview.
# Default: 1024 pixels.

TEMP_DIR = None
# Temporary directory for intermediate files.
# None → use system temp
# Ex: → r"E:\\temp_jxl"

TEMP2_DIR = None
# Staging directory for output TIFFs during conversion.
# None → disabled: TIFFs written directly to final destination
# Example: r"E:\\staging_tiff"

OVERWRITE = "smart"
# False → skip existing TIFFs (safe for resuming)
# True → always overwrite
# "smart" → only reconvert if JXL is newer than TIFF

DELETE_SOURCE = False
# [MODE 8 only] Delete source JXL after successful decode
# WARNING: irreversible

DELETE_CONFIRM = True
# Require interactive confirmation before deleting (MODE 8)

# ICC Color Management
CLEANUP_XMP_ICC_MARKER = True
# Remove ICC:base64 marker from XMP CreatorTool after extraction

USE_MATRIX_MODE = False
# Use Matrix decode mode (linear + LittleCMS color transform)

FORCE_BASIC_MODE = False
# Force Basic mode (ignore ICC handling)

# ─────────────────────────────────────────────
# USER SETTINGS - MODES CONFIGURATION
# ─────────────────────────────────────────────

# || MODE 0 SETTINGS ||
# No settings needed. Single file or flat directory output.
# py jxl_tiff_decoder.py input.jxl
# py jxl_tiff_decoder.py input_dir/

# || MODE 1 SETTINGS ||
CONVERTED_TIFF_FOLDER = "converted_tiff"
# [MODE 1] Subfolder created inside each JXL folder
# Example: .../JXL_FOLDER/converted_tiff/photo.tif

# || MODE 2 SETTINGS ||
# No settings needed. Flat output to specified directory.
# py jxl_tiff_decoder.py input_dir/ output_dir/ --mode 2

# || MODE 3 SETTINGS ||
TIFF_FOLDER_NAME = "TIFF_16bits"
# [MODE 3] Subfolder created inside each JXL folder
# Example: .../JXL_FOLDER/TIFF_16bits/photo.tif

# || MODE 4 SETTINGS ||
JXL_SUFFIX_TO_REPLACE = "JXL"
TIFF_SUFFIX_REPLACE = "TIFF"
# [MODE 4] Replaces JXL_SUFFIX_TO_REPLACE with TIFF_SUFFIX_REPLACE in folder name
# Case-insensitive
# Example: C1_Export_1_JXL → C1_Export_1_TIFF

# || MODE 5 SETTINGS ||
# Sibling folder next to each JXL folder
# Example: .../TIFF_FOLDER_NAME/photo.tif (uses TIFF_FOLDER_NAME above)

# || MODES 6 and 7 SETTINGS ||
EXPORT_MARKER = "_EXPORT"
EXPORT_TIFF_FOLDER = "16B_TIFF"
EXPORT_JXL_SUBFOLDER = ""
# [MODE 6/7] Uses EXPORT_MARKER as an anchor in the path
# All TIFFs go into EXPORT_MARKER/EXPORT_TIFF_FOLDER/
# Mode 6: processes JXLs both inside and outside EXPORT_MARKER
# Mode 7: only processes JXLs inside EXPORT_MARKER
#
# Example (mode 7, EXPORT_JXL_SUBFOLDER = "JXL"):
# EXPORT_MARKER/JXL/photo.jxl → EXPORT_MARKER/EXPORT_TIFF_FOLDER/photo.tif

# || MODE 8 SETTINGS ||
# No extra settings. In-place recursive conversion.
# Example: .../session/photo.jxl → .../session/photo.tif
# Controlled by DELETE_SOURCE above.

# ─────────────────────────────────────────────
# SAFETY SETTINGS
# ─────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
LOG_DIR = SCRIPT_DIR / "Logs" / Path(__file__).stem
logger = None
counter_lock = threading.Lock()
_counter = {"done": 0, "total": 0}

def setup_logger():
    global logger
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"{timestamp}.log"

    logger = logging.getLogger("jxl_decode")
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    ch = logging.StreamHandler(sys.stdout)

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

def confirm_deletion_jxl():
    """Interactive confirmation before deleting source JXLs"""
    from datetime import datetime as _dt
    print("\n\n")
    print(" [!] WARNING -- DELETE_SOURCE is enabled")
    print(" Source JXLs will be deleted after successful decode.")
    print(" This deletion is IRREVERSIBLE.")
    now = _dt.now()
    token = now.strftime("%H%M")
    print(f" Current time: {now.strftime('%H:%M')} -> to confirm, type: {token}")
    print()
    try:
        answer = input(" > ").strip()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer == token:
        print(" Confirmed. Source JXLs will be deleted after successful decode.\n")
        return True
    else:
        print(" Cancelled. No files will be deleted.\n")
        return False

# ═══════════════════════════════════════════════════════════════════════════════
# ICC EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

def extract_icc_from_xmp(jxl_path):
    """
    Extract ICC profile from XMP CreatorTool (base64 encoded by jxl_tiff_encoder).
    Returns ICC bytes or None.
    """
    try:
        r = subprocess.run(
            ["exiftool", "-b", "-XMP-xmp:CreatorTool", str(jxl_path)],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0 and r.stdout:
            # Look for ICC: prefix followed by base64 data (flexible regex)
            # Allows for line breaks, different lengths
            match = re.search(r'ICC:([A-Za-z0-9+/=\s]{100,})', r.stdout)
            if match:
                # Remove whitespace (line breaks in XML)
                b64_data = re.sub(r'\s', '', match.group(1))
                data = base64.b64decode(b64_data)
                if len(data) > 128:  # Minimum valid ICC size
                    return data
    except Exception as e:
        logger.debug(f"XMP ICC extraction failed: {e}")
    return None

def extract_icc_native(jxl_path, tmp_dir):
    """Extract ICC profile directly from JXL (for lossless files).
    Returns ICC bytes or None.
    """
    try:
        icc_path = tmp_dir / "native.icc"
        r = subprocess.run(
            ["exiftool", "-b", "-ICC_Profile", str(jxl_path), "-o", str(icc_path)],
            capture_output=True, timeout=10
        )
        if icc_path.exists() and icc_path.stat().st_size > 128:
            return icc_path.read_bytes()
    except Exception as e:
        logger.debug(f"Native ICC extraction failed: {e}")
    return None

def get_source_icc(jxl_path, tmp_dir):
    """Get ICC profile from JXL, trying XMP first then native.
    Returns (icc_bytes, source) tuple or (None, None).
    """
    icc = extract_icc_from_xmp(jxl_path)
    if icc:
        return icc, "xmp"
    icc = extract_icc_native(jxl_path, tmp_dir)
    if icc:
        return icc, "native"
    return None, None

def load_target_icc(path):
    """Load target ICC profile from file
        Returns ICC bytes or None.
    """
   
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        logger.error(f"Target ICC not found: {path}")
        return None
    try:
        return p.read_bytes()
    except Exception as e:
        logger.error(f"Failed to load target ICC: {e}")
        return None

def analyze_icc_profile(icc_data):
    """Analyze ICC profile data to identify color space.
    Returns: 'prophoto', 'adobe', 'srgb', '2020', 'p3', or 'unknown'
    """
    if len(icc_data) < 128:
        return 'unknown'

    try:
        # Read profile description from ICC header+tag area
        data_str = icc_data[:512].decode('ascii', errors='ignore').lower()

        if 'prophoto' in data_str or 'kodak' in data_str or 'romm' in data_str:
            return 'prophoto'
        elif 'adobe' in data_str and 'rgb' in data_str:
            return 'adobe'
        elif 'srgb' in data_str:
            return 'srgb'
        elif '2020' in data_str or 'bt2020' in data_str or 'rec.2020' in data_str:
            return '2020'
        elif 'p3' in data_str or 'display p3' in data_str or 'dci-p3' in data_str:
            return 'p3'
    except:
        pass

    return 'unknown' 

# ═══════════════════════════════════════════════════════════════════════════════
# DECODE STRATEGY
# ═══════════════════════════════════════════════════════════════════════════════

def select_decode_strategy(has_original_icc=False):
    """
    Select decode strategy based on ICC presence and mode flags.

    Three modes available:
    - Roundtrip: Default when ICC present. djxl auto + original ICC attachment.
                 Best for files converted with jxl_tiff_encoder.py
    - Basic: Default when no ICC. djxl auto only, no color management.
             For consumer JXLs without embedded ICC
    - Matrix: Linear decode + LittleCMS transformation.
              For special color space conversion needs

    Returns: (mode, reason) tuple
             mode: 'roundtrip', 'basic', or 'matrix'
             reason: human-readable explanation
    """
    # Matrix mode override (for special color conversion needs)
    if USE_MATRIX_MODE:
        return 'matrix', "Matrix mode (linear + LittleCMS transform)"

    # Force basic mode (ignore ICC)
    if FORCE_BASIC_MODE:
        return 'basic', "Basic mode forced (no ICC handling)"

    # Default logic: ICC present -> Roundtrip, no ICC -> Basic
    if has_original_icc:
        return 'roundtrip', "Roundtrip mode (ICC from XMP + djxl auto)"
    else:
        return 'basic', "Basic mode (djxl auto, no ICC)"

# ═══════════════════════════════════════════════════════════════════════════════
# DECODING
# ═══════════════════════════════════════════════════════════════════════════════

def decode_auto(jxl_path, output_ppm):
    """
    Decode JXL using djxl auto mode (optimized for display).
    Format inferred from .ppm extension — do NOT pass --output_format flag
    (not supported in djxl 0.11.x; causes "Unknown flag" error).
    Raises RuntimeError on failure.
    """
    cmd = ["djxl", str(jxl_path), str(output_ppm)]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    if r.returncode != 0:
        err = (r.stderr or b"").decode(errors='replace')[:200]
        raise RuntimeError(f"djxl auto failed: {err}")
    return True

def decode_rec2020_linear(jxl_path, output_ppm, icc_out_path):
    """
    Decode JXL to Rec.2020 linear color space.
    Also extracts ICC profile generated by djxl for verification.
    Format inferred from .ppm extension — do NOT pass --output_format flag.
    Raises RuntimeError on failure.
    """
    cmd = [
        "djxl", str(jxl_path), str(output_ppm),
        "--color_space=RGB_D65_2020_Per_Lin",   # was: RGB_D65_202_Per_Lin (typo — missing '0')
        f"--icc_out={icc_out_path}",
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    if r.returncode != 0:
        err = (r.stderr or b"").decode(errors='replace')[:200]
        raise RuntimeError(f"djxl Rec.2020 failed: {err}")
    return True

def read_ppm_to_numpy(ppm_path):
    """
    Read PPM/PGM file and convert to numpy array.
    Supports P6 (RGB) and P5 (grayscale) with 8-bit or 16-bit depth.
    Returns uint16 numpy array (always 3-channel for RGB, 2D for grayscale).
    """
    with open(ppm_path, 'rb') as f:
        magic = f.readline().strip()
        if magic not in (b'P6', b'P5'):
            raise ValueError(f"Unsupported PPM/PGM format: {magic}")

        # Skip comments
        line = f.readline()
        while line.startswith(b'#'):
            line = f.readline()

        # Parse dimensions
        dimensions = line.strip()
        while dimensions.startswith(b'#'):
            dimensions = f.readline().strip()
        width, height = map(int, dimensions.split())

        # Parse max value
        maxval_line = f.readline().strip()
        while maxval_line.startswith(b'#'):
            maxval_line = f.readline().strip()
        maxval = int(maxval_line)

        raw = f.read()

        if magic == b'P6':
            # RGB
            if maxval <= 255:
                pixel_data = np.frombuffer(raw, dtype=np.uint8)
                img = pixel_data.reshape((height, width, 3))
                img = img.astype(np.uint16) * 257   # 0-255 → 0-65535
            else:
                pixel_data = np.frombuffer(raw, dtype=np.dtype('>u2')).astype(np.uint16)
                img = pixel_data.reshape((height, width, 3))
            return img

        else:  # P5 — grayscale (djxl emits this for grayscale JXLs)
            if maxval <= 255:
                pixel_data = np.frombuffer(raw, dtype=np.uint8)
                img = pixel_data.reshape((height, width)).astype(np.uint16) * 257
            else:
                pixel_data = np.frombuffer(raw, dtype=np.dtype('>u2')).astype(np.uint16)
                img = pixel_data.reshape((height, width))
            # Expand grayscale → RGB so the rest of the pipeline stays uniform
            return np.stack([img, img, img], axis=2)

# ═══════════════════════════════════════════════════════════════════════════════
# COLOR TRANSFORMATION (MATRIX MODE)
# ═══════════════════════════════════════════════════════════════════════════════

def extract_trc_from_icc(icc_bytes):
    """
    Extract TRC (Tone Response Curve) from ICC profile.

    The TRC defines how pixel values are transformed from linear to perceptual space.
    Most RGB profiles use the same curve for all channels (shared TRC).

    Returns: ('gamma', value) or ('lut', [array of values]) or None if failed
    """
    if len(icc_bytes) < 128:
        return None

    try:
        # ICC profile header is 128 bytes, tag table starts at offset 128
        # Tag table format: 4 bytes tag count, then 12 bytes per tag (signature, offset, size)
        tag_count = struct.unpack_from('>I', icc_bytes, 128)[0]

        # Look for rTRC, gTRC, bTRC (red, green, blue Tone Response Curves)
        # Assume they are equal (RGB shares same curve in most profiles)
        # kTRC is for grayscale profiles
        trc_tags = {
            'rTRC': 0x72545243,  # 'rTRC' in hex
            'gTRC': 0x67545243,  # 'gTRC' in hex
            'bTRC': 0x62545243,  # 'bTRC' in hex
            'kTRC': 0x6B545243   # 'kTRC' for grayscale
        }

        curves = {}

        for i in range(tag_count):
            # Each tag entry is 12 bytes: 4-byte signature, 4-byte offset, 4-byte size
            offset = 128 + 4 + (i * 12)
            if offset + 12 > len(icc_bytes):
                break

            sig, idx, size = struct.unpack_from('>4sII', icc_bytes, offset)
            sig_int = struct.unpack('>I', sig)[0]

            for name, val in trc_tags.items():
                if sig_int == val:
                    data_offset = idx
                    if data_offset + 8 > len(icc_bytes):
                        continue

                    curve_type = icc_bytes[data_offset:data_offset+4]

                    if curve_type == b'para':
                        # Parametric curve type (modern profiles like ProPhoto)
                        # Type 0: Y = X^gamma (simple power law)
                        # Type 1: Y = (aX + b)^gamma (with offset)
                        # Type 2: Segmented curve (ProPhoto uses this)
                        func_type = struct.unpack_from('>H', icc_bytes, data_offset+8)[0]

                        if func_type == 0:
                            # Simple gamma: Y = X^gamma
                            gamma = struct.unpack_from('>f', icc_bytes, data_offset+12)[0]
                            curves[name] = ('gamma', gamma)
                        elif func_type == 1:
                            # Gamma with offset: Y = (aX + b)^gamma
                            # We approximate with just the gamma parameter
                            gamma = struct.unpack_from('>f', icc_bytes, data_offset+16)[0]
                            curves[name] = ('gamma', gamma)
                        elif func_type == 2:
                            # Segmented curve (ProPhoto uses type 2)
                            # For simplicity, we approximate with the main gamma parameter
                            gamma = struct.unpack_from('>f', icc_bytes, data_offset+16)[0]
                            curves[name] = ('gamma', gamma)

                    elif curve_type == b'curv':
                        # Curve type: either gamma value or LUT
                        count = struct.unpack_from('>I', icc_bytes, data_offset+8)[0]

                        if count == 0:
                            # 0 entries = linear curve (gamma 1.0)
                            curves[name] = ('gamma', 1.0)
                        elif count == 1:
                            # 1 entry = simple gamma value (fixed point 8.8)
                            gamma_fixed = struct.unpack_from('>H', icc_bytes, data_offset+12)[0]
                            gamma = gamma_fixed / 256.0
                            curves[name] = ('gamma', gamma)
                        else:
                            # LUT with multiple points (large lookup table)
                            # Read up to 4096 points for performance
                            lut = []
                            for j in range(min(count, 4096)):
                                if data_offset + 12 + j*2 + 2 > len(icc_bytes):
                                    break
                                val = struct.unpack_from('>H', icc_bytes, data_offset+12 + j*2)[0]
                                lut.append(val / 65535.0)
                            curves[name] = ('lut', lut)

        # Return curve from first channel found (assume RGB shared)
        for ch in ['rTRC', 'gTRC', 'bTRC', 'kTRC']:
            if ch in curves:
                return curves[ch]

    except Exception as e:
        logger.debug(f"TRC extraction failed: {e}")

    return None




def apply_icc_transform(img_array, source_icc, target_icc, tmp_dir):
    """
    Apply ICC transformation: convert from source ICC to target ICC.
    Uses LittleCMS for matrix conversion, manual TRC application as fallback.
    """
    if not target_icc:
        logger.warning("No target ICC provided, skipping color transform")
        return img_array

    try:
        # Extract TRC from target ICC
        trc = extract_trc_from_icc(target_icc)
        if not trc:
            logger.warning("Could not extract TRC from target ICC, using fallback gamma 2.2")
            trc = ('gamma', 2.2)

        curve_type, curve_data = trc
        logger.info(f" -> Target TRC extracted: {curve_type}={curve_data if curve_type=='gamma' else 'LUT'}")

        # Try LittleCMS for matrix conversion
        lcms_success = False
        result_float = None

        if ImageCms and source_icc:
            try:
                tgt_path = tmp_dir / "target.icc"
                src_path = tmp_dir / "source.icc"
                tgt_path.write_bytes(target_icc)
                src_path.write_bytes(source_icc)

                src_profile = ImageCms.ImageCmsProfile(str(src_path))
                tgt_profile = ImageCms.ImageCmsProfile(str(tgt_path))

                transform = ImageCms.buildTransform(
                    src_profile, tgt_profile, "RGB", "RGB",
                    renderingIntent=0  # Perceptual
                )

                # Workaround: LittleCMS often fails with 16-bit directly
                # Convert to 8-bit temporary, transform, back to float
                temp_8bit = (img_array.astype(np.float32) / 257.0).astype(np.uint8)
                pil_img = Image.fromarray(temp_8bit, mode='RGB')

                result = ImageCms.applyTransform(pil_img, transform)

                # Back to float 0-1 range
                result_float = np.array(result).astype(np.float32) / 255.0
                lcms_success = True

                logger.debug(" -> LittleCMS: matrix + curve applied")

            except Exception as e:
                logger.warning(f"LittleCMS failed: {e}")

        # Apply manual TRC only if LittleCMS failed
        if lcms_success and result_float is not None:
            # LittleCMS already did everything (matrix + curve), just convert to 16-bit
            logger.debug(" -> Using LittleCMS result (no manual curve)")
            result_array = (result_float * 65535.0).astype(np.uint16)
        else:
            # Fallback: apply TRC curve manually (assumes same primaries or already converted)
            logger.debug(" -> Applying TRC manually as fallback")
            pixels = img_array.astype(np.float32) / 65535.0

            if curve_type == 'gamma':
                gamma = curve_data
                if gamma > 0 and abs(gamma - 1.0) > 0.001:
                    pixels = np.power(pixels, 1.0 / gamma)
                    logger.debug(f" -> Applied gamma {gamma} TRC")
            elif curve_type == 'lut':
                lut = np.array(curve_data)
                for c in range(3):
                    channel = pixels[:,:,c]
                    indices = (channel * (len(lut)-1)).astype(np.int32)
                    indices = np.clip(indices, 0, len(lut)-2)
                    frac = (channel * (len(lut)-1)) - indices
                    pixels[:,:,c] = lut[indices] + frac * (lut[indices+1] - lut[indices])
                logger.debug(f" -> Applied LUT TRC")

            result_array = (pixels * 65535.0).astype(np.uint16)

        return result_array

    except Exception as e:
        logger.error(f"ICC transform failed completely: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return img_array

# ═══════════════════════════════════════════════════════════════════════════════
# TIFF OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

def write_tiff(img_array, path, icc_data, compression="zip"):
    """Write numpy array to TIFF file with optional ICC profile"""
    comp_map = {"uncompressed": None, "lzw": "lzw", "zip": "zlib", "none": None}
    comp = comp_map.get(compression, "zlib")
    if icc_data:
        tifffile.imwrite(str(path), img_array, compression=comp, iccprofile=icc_data, metadata=None)
    else:
        tifffile.imwrite(str(path), img_array, compression=comp, metadata=None)

def copy_metadata(jxl_path, tiff_path, tmp_dir):
    """Copy metadata from JXL to TIFF using exiftool"""
    try:
        subprocess.run(
            ["exiftool", "-overwrite_original", "-tagsfromfile", str(jxl_path),
             "-exif:all", str(tiff_path)],
            capture_output=True, timeout=10
        )
        subprocess.run(
            ["exiftool", "-overwrite_original", "-tagsfromfile", str(jxl_path),
             "-xmp:all", "-iptc:all", str(tiff_path)],
            capture_output=True, timeout=10
        )
    except Exception as e:
        logger.debug(f"Metadata copy warning: {e}")

def cleanup_xmp_icc(tiff_path):
    """Remove ICC:base64 marker from XMP CreatorTool"""
    if not CLEANUP_XMP_ICC_MARKER:
        return
    try:
        r = subprocess.run(
            ["exiftool", "-XMP-xmp:CreatorTool", str(tiff_path)],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0 and r.stdout and "ICC:" in r.stdout:
            content = r.stdout.strip()
            clean = re.sub(r'ICC:[A-Za-z0-9+/=]+', '', content).strip()
            clean = re.sub(r'\s*\|\s*$', '', clean)
            if not clean:
                clean = "jxl_tiff_decoder"
            subprocess.run(
                ["exiftool", "-overwrite_original",
                 f"-XMP-xmp:CreatorTool={clean}", str(tiff_path)],
                capture_output=True, timeout=10
            )
            logger.debug(f" -> Cleaned up XMP CreatorTool")
    except Exception as e:
        logger.debug(f"XMP cleanup skipped: {e}")

def add_jpeg_preview(tiff_path, tmp_dir):
    """Add JPEG preview as second page of TIFF.
    
    Uses tifffile to read the main image data reliably for 16-bit TIFFs.
    PIL Image.open() often fails or returns incorrect data for 16-bit ZIP TIFFs on Windows.
    """
    if not ADD_JPEG_PREVIEW:
        return
    try:
        # Read page 0 only — reliable for any bit depth or compression
        with tifffile.TiffFile(str(tiff_path)) as tif:
            img_data = tif.series[0].asarray()

        if img_data.ndim == 2:
            h, w = img_data.shape
        else:
            h, w = img_data.shape[:2]

        max_dim = JPEG_PREVIEW_SIZE
        if w >= h:
            new_w = max_dim
            new_h = max(1, int(h * max_dim / w))
        else:
            new_h = max_dim
            new_w = max(1, int(w * max_dim / h))

        # Convert to 8-bit for JPEG preview
        if img_data.dtype == np.uint16:
            img_8bit = (img_data >> 8).astype(np.uint8)
        elif img_data.dtype == np.uint8:
            img_8bit = img_data
        else:
            mx = img_data.max()
            img_8bit = ((img_data.astype(np.float32) / mx) * 255).astype(np.uint8) if mx > 0 else img_data.astype(np.uint8)

        pil_img = Image.fromarray(img_8bit)
        try:
            resample = Image.Resampling.LANCZOS   # Pillow >= 9.1
        except AttributeError:
            resample = Image.LANCZOS              # Pillow < 9.1
        preview = pil_img.resize((new_w, new_h), resample)
        preview_arr = np.array(preview)

        tifffile.imwrite(str(tiff_path), preview_arr, compression='jpeg', append=True)
        logger.info(f" -> Added JPEG preview ({new_w}x{new_h})")
    except Exception as e:
        logger.debug(f"Preview failed: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# PATH RESOLUTION (ALL MODES 0-8)
# ═══════════════════════════════════════════════════════════════════════════════

def resolve_output(jxl_path: Path, mode: int, input_root: Path) -> Path:
    """Resolve output TIFF path based on mode (0-8)"""

    if mode == 0:
        # Mode 0: Single file in-place or flat directory
        if input_root != jxl_path.parent:
            return input_root / jxl_path.with_suffix(".tif").name
        return jxl_path.parent / jxl_path.with_suffix(".tif").name

    elif mode == 1:
        # Mode 1: Subfolder inside each JXL folder
        return jxl_path.parent / CONVERTED_TIFF_FOLDER / jxl_path.with_suffix(".tif").name

    elif mode == 2:
        # Mode 2: Flat output to specified directory
        return input_root / jxl_path.with_suffix(".tif").name

    elif mode == 3:
        # Mode 3: Subfolder inside each JXL folder (custom name)
        return jxl_path.parent / TIFF_FOLDER_NAME / jxl_path.with_suffix(".tif").name

    elif mode == 4:
        # Mode 4: Rename folder replacing JXL suffix with TIFF suffix
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
        # Mode 5: Sibling folder next to each JXL folder
        return jxl_path.parent.parent / TIFF_FOLDER_NAME / jxl_path.with_suffix(".tif").name

    elif mode == 6:
        # Mode 6: EXPORT anchor - all JXLs in hierarchy
        parts = list(jxl_path.parts)
        export_idx = next((i for i, p in enumerate(parts) if EXPORT_MARKER in p), None)
        if export_idx is None:
            logger.warning(f"'{EXPORT_MARKER}' not found in {jxl_path}, using local folder")
            return jxl_path.parent / EXPORT_TIFF_FOLDER / jxl_path.with_suffix(".tif").name

        export_dir = Path(*parts[:export_idx + 1])

        if jxl_path.is_relative_to(export_dir):
            rel_parts = jxl_path.relative_to(export_dir).parts
            if len(rel_parts) > 1:
                rel = Path(*rel_parts[1:])
            else:
                rel = Path(rel_parts[0])
        else:
            rel = jxl_path.relative_to(export_dir.parent)

        return export_dir / EXPORT_TIFF_FOLDER / rel.with_suffix(".tif")

    elif mode == 7:
        # Mode 7: EXPORT anchor - only JXLs inside EXPORT_MARKER
        parts = list(jxl_path.parts)
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
            if len(rel_parts) > 1:
                rel = Path(*rel_parts[1:])
            else:
                rel = Path(rel_parts[0])

        return export_dir / EXPORT_TIFF_FOLDER / rel.with_suffix(".tif")

    elif mode == 8:
        # Mode 8: In-place recursive - TIFF goes to same folder as JXL
        return jxl_path.parent / jxl_path.with_suffix(".tif").name

    raise ValueError(f"Invalid mode: {mode}")

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN CONVERSION
# ═══════════════════════════════════════════════════════════════════════════════

def convert_one(jxl_path, write_path, final_path, target_icc_path=None):
    """
    Convert a single JXL to TIFF with smart color management.

    Automatically selects between three decode strategies:
    1. Roundtrip (default with ICC): djxl auto + original ICC attachment
       Best for files converted with jxl_tiff_encoder.py
    2. Matrix (linear + LittleCMS): For color space conversion needs
    3. Basic (djxl auto only): For consumer JXLs without embedded ICC
    """
    already_exists = final_path.exists()

    if already_exists:
        if OVERWRITE == False:
            n, total = next_count()
            logger.info(f"[{n}/{total}] SKIP (exists) | {jxl_path.name}")
            return str(jxl_path), "skipped", str(final_path)
        elif OVERWRITE == "smart":
            if jxl_path.stat().st_mtime <= final_path.stat().st_mtime:
                n, total = next_count()
                logger.info(f"[{n}/{total}] SKIP (sync: TIFF up to date) | {jxl_path.name}")
                return str(jxl_path), "skipped", str(final_path)
            logger.info(f" -> SYNC: JXL newer than TIFF, reconverting | {jxl_path.name}")

    overwritten = already_exists

    write_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="tiff_", dir=TEMP_DIR) as tmp:
        tmp_dir = Path(tmp)
        try:
            ppm_path = tmp_dir / "decoded.ppm"
            djxl_icc_path = tmp_dir / "djxl.icc"

            # Extract ICC first to decide strategy
            original_icc, icc_source = get_source_icc(jxl_path, tmp_dir)

            # Analyze ICC to get hint for logging
            original_icc_hint = None
            if original_icc:
                original_icc_hint = analyze_icc_profile(original_icc)
                logger.info(f" -> ICC extracted from {icc_source} ({original_icc_hint})")

            # Select decode strategy based on ICC presence
            mode, reason = select_decode_strategy(has_original_icc=original_icc is not None)
            logger.info(f" -> {reason}")

            if mode == 'roundtrip':
                # === ROUNDTRIP MODE (DEFAULT WITH ICC) ===
                # Best for files converted with jxl_tiff_encoder.py
                # djxl auto handles display optimization, we attach the original ICC
                logger.info(" -> Using Roundtrip decode (djxl auto + original ICC)")

                decode_auto(jxl_path, ppm_path)
                pixels = read_ppm_to_numpy(ppm_path)
                if DJXL_OUTPUT_DEPTH == 8:
                    pixels = (pixels >> 8).astype(np.uint8)
                write_tiff(pixels, write_path, original_icc, TIFF_COMPRESSION)
                copy_metadata(jxl_path, write_path, tmp_dir)
                cleanup_xmp_icc(write_path)
                add_jpeg_preview(write_path, tmp_dir)

            elif mode == 'matrix':
                # === MATRIX MODE (LINEAR + LITTLECMS) ===
                # For color space conversion or when precise transformation needed
                # Decodes to Rec.2020 linear, then transforms to target ICC
                logger.info(" -> Using Matrix decode (linear + LittleCMS)")

                decode_rec2020_linear(jxl_path, ppm_path, djxl_icc_path)
                pixels = read_ppm_to_numpy(ppm_path)
                djxl_icc = djxl_icc_path.read_bytes() if djxl_icc_path.exists() else None

                if target_icc_path:
                    # Convert to specific target ICC
                    target_icc = load_target_icc(target_icc_path)
                    final_pixels = apply_icc_transform(pixels, djxl_icc, target_icc, tmp_dir)
                    final_icc = target_icc
                elif original_icc:
                    # Transform to original ICC profile
                    final_pixels = apply_icc_transform(pixels, djxl_icc, original_icc, tmp_dir)
                    final_icc = original_icc
                else:
                    # No transform, keep Rec.2020
                    final_pixels = pixels
                    final_icc = djxl_icc

                if DJXL_OUTPUT_DEPTH == 8:
                    final_pixels = (final_pixels >> 8).astype(np.uint8)

                write_tiff(final_pixels, write_path, final_icc, TIFF_COMPRESSION)
                copy_metadata(jxl_path, write_path, tmp_dir)
                cleanup_xmp_icc(write_path)
                add_jpeg_preview(write_path, tmp_dir)

            else:  # mode == 'basic'
                # === BASIC MODE (DEFAULT WITHOUT ICC) ===
                # For consumer JXLs without embedded ICC
                logger.info(" -> Using Basic decode (djxl auto, no ICC)")

                decode_auto(jxl_path, ppm_path)
                pixels = read_ppm_to_numpy(ppm_path)
                if DJXL_OUTPUT_DEPTH == 8:
                    pixels = (pixels >> 8).astype(np.uint8)
                write_tiff(pixels, write_path, None, TIFF_COMPRESSION)

                # Minimal metadata copy
                subprocess.run(
                    ["exiftool", "-overwrite_original", "-tagsfromfile", str(jxl_path),
                     "-exif:all", str(write_path)],
                    capture_output=True, timeout=10
                )

                # Clear ImageDescription that may have been added by tifffile
                subprocess.run(
                    ["exiftool", "-overwrite_original", "-IFD1:ImageDescription=",
                     "-ImageDescription=", str(write_path)],
                    capture_output=True, timeout=10
                )

            n, total = next_count()
            status = "OVERWRITE" if overwritten else "OK"
            logger.info(f"[{n}/{total}] {status} | {jxl_path.name} ({reason})")
            return str(jxl_path), "ok", str(final_path)

        except Exception as e:
            n, total = next_count()
            logger.error(f"[{n}/{total}] ERROR | {jxl_path.name} | {e}")
            return str(jxl_path), "error", str(e)

def process_group(group_pairs, workers, mode, target_icc=None):
    """Process a group of (jxl, tiff) pairs in parallel"""
    use_staging = TEMP2_DIR is not None
    staging_dir = Path(TEMP2_DIR) if use_staging else None

    if use_staging:
        staging_dir.mkdir(parents=True, exist_ok=True)

    tasks = []
    for jxl, final_tiff in group_pairs:
        if use_staging:
            write_tiff_path = staging_dir / f"{jxl.parent.name}__{jxl.stem}.tif"
        else:
            write_tiff_path = final_tiff
        tasks.append((jxl, write_tiff_path, final_tiff))

    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(convert_one, j, w, f, target_icc): (j, w, f)
                   for j, w, f in tasks}
        for fut in as_completed(futures):
            results.append(fut.result())

    if use_staging:
        moved = 0
        for _, write_tiff, final_tiff in tasks:
            if write_tiff.exists():
                final_tiff.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(write_tiff), str(final_tiff))
                moved += 1
        if moved:
            logger.info(f" -> Moved {moved} file(s) from staging to final")

    if DELETE_SOURCE and mode == 8:
        deleted = 0
        src_map = {str(j): (j, f) for j, _, f in tasks}
        for result in results:
            status = result[1]
            src_jxl = src_map.get(result[0], (None, None))[0]
            if status not in ("ok", "overwrite") or src_jxl is None:
                continue
            final_tiff = src_map.get(result[0], (None, None))[1]
            if final_tiff is None or not final_tiff.exists():
                continue
            src_jxl.unlink()
            deleted += 1
            logger.info(f" DELETED source | {src_jxl.name}")
        if deleted:
            logger.info(f" -> Deleted {deleted} source JXL(s)")

    return results

def find_jxls_flat(path):
    """Find JXL files in the top-level directory only (no subfolders) — modes 0 and 1."""
    seen = set()
    files = []
    for ext in ("*.jxl", "*.jif"):
        for f in path.glob(ext):
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                files.append(f)
    return files

def find_jxls_recursive(path):
    """Find all JXL files recursively"""
    seen = set()
    files = []
    for ext in ("*.jxl", "*.jif"):
        for f in path.rglob(ext):
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                files.append(f)
    return files

def find_jxls_mode7(input_path):
    """Mode 7: only JXLs inside folders containing EXPORT_MARKER"""
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

# ═══════════════════════════════════════════════════════════════════════════════
# ARGUMENT PARSING AND MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="JPEG XL to TIFF converter with ICC preservation",
        epilog="""
Decode modes:
  Roundtrip (default with ICC) - djxl auto + original ICC attachment
  Basic (default no ICC) - djxl auto only, no color management
  Matrix (--matrix) - linear decode + LittleCMS transform

Modes:
  0 = In-place (default)
  1 = Subfolder (converted_tiff/)
  2 = Flat output directory
  3 = Subfolder (TIFF_16bits/)
  4 = Rename folder (JXL->TIFF)
  5 = Sibling folder
  6 = EXPORT marker full hierarchy
  7 = EXPORT marker only inside
  8 = In-place recursive + delete source option

Examples:
  %(prog)s photo.jxl                    # Auto mode
  %(prog)s photo.jxl --matrix           # Force Matrix mode
  %(prog)s folder/ --workers 8          # Batch conversion
  %(prog)s folder/ --mode 1             # Subfolder output
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("input", type=Path, help="Input JXL file or directory")
    parser.add_argument("output", nargs="?", type=Path, default=None,
                      help="Output directory (mode 0, 2)")
    parser.add_argument("--mode", type=int, default=0, choices=range(9),
                      help="Output path mode (0-8)")
    parser.add_argument("--workers", type=int, default=min(os.cpu_count(), 16),
                      help="Number of parallel workers")
    parser.add_argument("--overwrite", action="store_true",
                      help="Overwrite existing files")
    parser.add_argument("--sync", action="store_true",
                      help="Only reconvert if JXL is newer than TIFF")

    # Decode mode flags
    parser.add_argument("--matrix", action="store_true", dest="use_matrix",
                      help="Use Matrix decode mode")
    parser.add_argument("--basic", action="store_true", dest="force_basic",
                      help="Force Basic mode")

    # ICC options
    parser.add_argument("--target-icc", type=Path, default=None,
                      help="Convert to specific ICC profile")
    parser.add_argument("--no-icc-cleanup", action="store_true", dest="no_icc_clean",
                      help="Keep ICC:base64 marker in XMP")

    # Output options
    parser.add_argument("--depth", type=int, choices=[8, 16], default=None,
                      help="Output bit depth")
    parser.add_argument("--compression", choices=["zip", "lzw", "none"], default=None,
                      help="TIFF compression")
    parser.add_argument("--staging", type=str, default=None,
                      help="Staging directory for output files")
    parser.add_argument("--delete-source", action="store_true",
                      help="Delete source JXLs after successful decode (mode 8 only)")
    parser.add_argument("--dry-run", action="store_true",
                      help="Preview operations without converting")

    args = parser.parse_args()

    # Apply globals
    global OVERWRITE, USE_MATRIX_MODE, FORCE_BASIC_MODE
    global CLEANUP_XMP_ICC_MARKER, DJXL_OUTPUT_DEPTH, TIFF_COMPRESSION, TEMP2_DIR, DELETE_SOURCE

    if args.sync:
        OVERWRITE = "smart"
    elif args.overwrite:
        OVERWRITE = True

    if args.delete_source:
        DELETE_SOURCE = True

    USE_MATRIX_MODE = args.use_matrix
    FORCE_BASIC_MODE = args.force_basic

    if args.no_icc_clean:
        CLEANUP_XMP_ICC_MARKER = False

    if USE_MATRIX_MODE and not ImageCms:
        print("WARNING: Matrix mode requested but ImageCms unavailable. Install with: pip install Pillow --upgrade")

    if args.depth:
        DJXL_OUTPUT_DEPTH = args.depth
    if args.compression:
        TIFF_COMPRESSION = args.compression
    if args.staging:
        TEMP2_DIR = args.staging

    log_file = setup_logger()

    # Mode 8 confirmation
    if args.mode == 8 and DELETE_SOURCE:
        logger.info("Mode 8 -- DELETE_SOURCE=True: source JXLs will be deleted after successful decode")
        if DELETE_CONFIRM:
            if not confirm_deletion_jxl():
                logger.info("Deletion not confirmed -- exiting.")
                return

    logger.info(f"Mode: {args.mode} | Depth: {DJXL_OUTPUT_DEPTH} | "
                f"Compression: {TIFF_COMPRESSION} | Workers: {args.workers}")
    logger.info(f"Matrix: {USE_MATRIX_MODE} | Basic: {FORCE_BASIC_MODE} | "
                f"Overwrite: {OVERWRITE}")
    logger.info(f"Input: {args.input}")

    # Collect files
    if args.input.is_file():
        jxls = [args.input]
        output_root = args.output or args.input.parent
    else:
        if args.mode in (0, 1):
            jxls = find_jxls_flat(args.input)      # flat — subfolders not touched
        elif args.mode == 7:
            jxls = find_jxls_mode7(args.input)
        else:
            jxls = find_jxls_recursive(args.input)
        output_root = args.output or args.input

    logger.info(f"Files found: {len(jxls)}")
    _counter["total"] = len(jxls)

    if len(jxls) == 0:
        logger.warning("No JXL files found")
        return

    # Build pairs
    pairs = []
    for j in jxls:
        tiff = resolve_output(j, args.mode, output_root)
        pairs.append((j, tiff))

    # Dry run
    if args.dry_run:
        for j, t in pairs:
            logger.info(f" DRY | {j.name} -> {t}")
        logger.info(f"Dry run: {len(pairs)} file(s) would be converted.")
        return

    # Group by output folder
    groups = {}
    for j, t in pairs:
        groups.setdefault(t.parent, []).append((j, t))

    logger.info(f"Output groups: {len(groups)}")

    # Process
    ok = err = skipped = overwritten = 0

    for dest_folder, group_pairs in groups.items():
        if len(groups) > 1:
            logger.info(f"-- Group: {dest_folder} ({len(group_pairs)} file(s))")

        results = process_group(group_pairs, args.workers, args.mode,
                               target_icc=args.target_icc)

        for result in results:
            status = result[1]
            if status == "ok":
                ok += 1
            elif status == "overwrite":
                ok += 1
                overwritten += 1
            elif status == "skipped":
                skipped += 1
            elif status == "error":
                err += 1

    logger.info("\n" + "-"*50)
    if args.sync:
        logger.info(f"SYNC done: {ok} reconverted | {skipped} up to date | {err} errors")
    else:
        logger.info(f"Done: {ok} OK | {overwritten} overwrites | {skipped} skipped | {err} errors")
    logger.info(f"Log: {log_file}")

if __name__ == "__main__":
    main()
