#!/usr/bin/env python3
"""
jxl_jpeg_transcoder.py — Unified JPEG XL toolkit (Round-trip optimized)

Auto-detect workflow:
  JPEG input -> transcode (lossless encode to JXL)
  JXL input  -> checks for jbrd box -> transcode decode (lossless recovery) if present
               otherwise convert (lossy to JPEG/PNG)
  PNG input  -> convert (to JXL, lossy or modular lossless)

Usage:
  python jxl_jpeg_transcoder.py photo.jpg                    # auto: transcode encode
  python jxl_jpeg_transcoder.py photo.jxl                  # auto: transcode decode (if brob present)
  python jxl_jpeg_transcoder.py photo.jxl --format png     # auto: convert to PNG (if no brob)
  python jxl_jpeg_transcoder.py --help

Requirements:
  cjxl / djxl -> https://github.com/libjxl/libjxl/releases
  exiftool    -> https://exiftool.org
  magick      -> https://imagemagick.org (optional, for ICC)
"""

import subprocess
import os
import sys
import shutil
import logging
import tempfile
import threading
import hashlib
import argparse
import re
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional


def _run_pipeline_safe(cmd1: list, cmd2: list, timeout: float = 300) -> tuple:
    """Run two commands in a pipeline (cmd1 | cmd2) safely without deadlock.
    
    Returns: (returncode1, returncode2, stderr1, stderr2)
    Raises: RuntimeError if either command fails
    """
    import threading
    
    # Start first process
    proc1 = subprocess.Popen(cmd1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # Start second process with stdin from proc1
    proc2 = subprocess.Popen(cmd2, stdin=proc1.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc1.stdout.close()  # Allow proc1 to receive SIGPIPE if proc2 exits
    
    # Collect stderr from both processes using threads to prevent deadlock
    stderr1_data = [b""]
    stderr2_data = [b""]
    
    def read_stderr1():
        if proc1.stderr:
            stderr1_data[0] = proc1.stderr.read()
    
    def read_stderr2():
        if proc2.stderr:
            stderr2_data[0] = proc2.stderr.read()
    
    t1 = threading.Thread(target=read_stderr1)
    t2 = threading.Thread(target=read_stderr2)
    t1.start()
    t2.start()
    
    try:
        # Wait for proc2 to complete (it will consume proc1's output)
        proc2.wait(timeout=timeout)
        t2.join(timeout=5)
        
        # Wait for proc1 to complete
        proc1.wait(timeout=timeout)
        t1.join(timeout=5)
        
        if proc1.returncode != 0 or proc2.returncode != 0:
            err_msg = (stderr1_data[0] + stderr2_data[0]).decode(errors='replace')[:500]
            raise RuntimeError(f"Pipeline failed (codes: {proc1.returncode}, {proc2.returncode}): {err_msg}")
        
        return proc1.returncode, proc2.returncode, stderr1_data[0], stderr2_data[0]
        
    except subprocess.TimeoutExpired:
        # Cleanup on timeout
        proc1.kill()
        proc2.kill()
        proc1.wait()
        proc2.wait()
        raise RuntimeError(f"Pipeline timeout after {timeout}s")


def _verify_file_integrity(file_path: Path) -> bool:
    """Verify output file integrity before deleting source.
    
    Checks based on file extension:
    - JXL: Valid JXL signature
    - JPEG: Valid JPEG markers (SOI)
    - PNG: Valid PNG signature
    - TIFF: Valid TIFF header
    """
    if not file_path.exists():
        return False
    
    try:
        stat = file_path.stat()
        if stat.st_size == 0:
            return False
        
        ext = file_path.suffix.lower()
        
        with open(file_path, 'rb') as f:
            header = f.read(12)
        
        if len(header) < 2:
            return False
        
        if ext == '.jxl':
            # Bare JXL: 0xFF 0x0A, Container: ISOBMFF
            if header[0:2] == b'\xff\x0a':
                return True
            if header == b'\x00\x00\x00\x0cJXL \r\n\x87\n':
                return True
            return False
        
        elif ext in ('.jpg', '.jpeg'):
            # JPEG starts with SOI marker 0xFFD8
            return header[0:2] == b'\xff\xd8'
        
        elif ext == '.png':
            # PNG signature: 0x89PNG\r\n\x1a\n
            return header[0:8] == b'\x89PNG\r\n\x1a\n'
        
        elif ext in ('.tif', '.tiff'):
            # TIFF: II (little) or MM (big) followed by 42
            if header[0:2] not in (b'II', b'MM'):
                return False
            return header[2:4] in (b'\x2a\x00', b'\x00\x2a')
        
        # Unknown extension - allow deletion (conservative)
        return True
        
    except (OSError, IOError):
        return False


def _is_relative_to(path: Path, anchor: Path) -> bool:
    """Backport of Path.is_relative_to for Python < 3.9."""
    try:
        path.relative_to(anchor)
        return True
    except ValueError:
        return False


# --------------------------------------------─
# USER SETTINGS - GENERAL
# --------------------------------------------─

# Default behaviors (can be overridden by CLI)
TRANSCODE_DEFAULT_MODE = 0
# 0 = in-place (same folder as source) [default, consistent with TIFF scripts]
# 1 = subfolder (converted_jxl/ or recovered_jpeg/)
# 8 = in-place recursive (for batch)

CONVERT_DEFAULT_MODE = 0
# 0 = in-place (same folder as source)
# 1 = sibling folder (../converted/)
# 2 = suffix folder (source_converted/)

# Output settings
PNG_DEFAULT_BIT_DEPTH = 16
# 16 = default for PNG (preserves full data, your archival workflow)
# 8 = optional for web compatibility

JPEG_DEFAULT_QUALITY = 95
# 1-100, 95 = high quality archival

# Transcode settings (lossless JPEG <-> JXL)
CJXL_EFFORT = 7
# Compression effort (1-10). 7 = sweet spot for photos.
# Does NOT affect quality in lossless mode.

STORE_MD5 = True
# Store MD5 checksums during transcode encode (for verify during decode)

DELETE_SOURCE = False
# [MODE 8 only] Delete source after successful encode/decode
# WARNING: irreversible. Only enable after testing on a small batch.

DELETE_SOURCE_REQUIRE_MD5 = True
DELETE_CONFIRM = True

# Paths
TEMP_DIR = None
# None = system temp. Set to custom path if needed.

TEMP2_DIR = None
# Staging directory for output files during conversion
# Example: r"E:\staging_jxl"

# ImageMagick detection (auto, do not modify)
MAGICK_AVAILABLE = shutil.which("magick") is not None

# ExifTool detection - try both name variants
_exiftool_cmd = None
def _get_exiftool_cmd():
    global _exiftool_cmd
    if _exiftool_cmd is None:
        candidates = ["exiftool", "exiftool(-k)", "exiftool-k"]
        for cmd in candidates:
            if shutil.which(cmd) is not None:
                _exiftool_cmd = cmd
                break
        else:
            _exiftool_cmd = "exiftool"  # defer and let subprocess fail naturally
    return _exiftool_cmd

# --------------------------------------------─
# USER SETTINGS - TRANSCODE MODE CONFIGURATION
# --------------------------------------------─

# Mode 1 folders
CONVERTED_JXL_FOLDER = "converted_jxl"
RECOVERED_JPEG_FOLDER = "recovered_jpeg"

# Mode 3
JXL_FOLDER_NAME = "JXL_jpeg"
JPEG_FOLDER_NAME = "JPEG_recovered"

# Mode 4 (sibling)
JXL_SIBLING_FOLDER = "JXL_jpeg"
JPEG_SIBLING_FOLDER = "JPEG_recovered"

# Mode 5 (suffix replacement)
JPEG_SUFFIX_TO_REPLACE = "JPEG"
JXL_SUFFIX_REPLACE = "JXL"
JXL_SUFFIX_TO_REPLACE = "JXL"
JPEG_SUFFIX_REPLACE_DEC = "JPEG_recovered"

# Modes 6/7 (EXPORT marker)
EXPORT_MARKER = "_EXPORT"
EXPORT_JXL_FOLDER = "JXL_jpeg"
EXPORT_JPEG_FOLDER = "JPEG_recovered"
EXPORT_JPEG_SUBFOLDER = ""

# --------------------------------------------─
# USER SETTINGS - CONVERT MODE CONFIGURATION
# --------------------------------------------─

CONVERT_OUTPUT_FOLDER = "converted"
CONVERT_OUTPUT_SUFFIX = "_converted"

# Container flag for lossy JXL encoding
# True = adds --container=1 for IrfanView EXIF compatibility
# Required for lossy (d>0) to allow exiftool to inject metadata
FORCE_CONTAINER_FOR_LOSSY = True

# --------------------------------------------─
# GLOBAL SETUP
# --------------------------------------------─

SCRIPT_DIR = Path(__file__).parent
LOG_DIR = SCRIPT_DIR / "Logs" / Path(__file__).stem
logger = None
counter_lock = threading.Lock()
_md5_db_lock = threading.Lock()
_counter = {"done": 0, "total": 0}

def setup_logger():
    global logger
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"{timestamp}.log"

    logger = logging.getLogger("jxl_jpeg_transcoder")
    logger.setLevel(logging.INFO)

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

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
    logger.info(f"Log: {log_file}")
    return log_file


def _log_rejected_file(file_path, reason):
    """Log rejected files to Logs/jxl_jpeg_transcoder/rejected_files.log for easy review."""
    try:
        rej_dir = SCRIPT_DIR / "Logs" / "jxl_jpeg_transcoder"
        rej_dir.mkdir(parents=True, exist_ok=True)
        rej_file = rej_dir / "rejected_files.log"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(rej_file, "a", encoding="utf-8") as f:
            f.write(f"{timestamp} | {reason} | {file_path}\n")
    except Exception:
        pass


def next_count():
    with counter_lock:
        _counter["done"] += 1
        return _counter["done"], _counter["total"]

# --------------------------------------------─
# MD5 UTILITIES (Transcode only)
# --------------------------------------------─

def md5_of_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

CHECKSUMS_FILENAME = "checksums.md5"

def store_md5_db(jxl_path: Path, md5: str):
    db_path = jxl_path.parent / CHECKSUMS_FILENAME
    entry = f"{md5}  {jxl_path.name}\n"
    with _md5_db_lock:
        with open(db_path, "a", encoding="utf-8") as f:
            f.write(entry)

def read_md5_db(jxl_path: Path) -> Optional[str]:
    db_path = jxl_path.parent / CHECKSUMS_FILENAME
    if not db_path.exists():
        return None
    target = jxl_path.name
    with open(db_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    # Read from bottom to top to get the most recent entry
    for line in reversed(lines):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            stored_hash, stored_name = parts
            stored_name = stored_name.lstrip("*").strip()
            if stored_name == target:
                return stored_hash
    return None

# --------------------------------------------─
# JXL DETECTION UTILITIES
# --------------------------------------------─

def has_jbrd_box(jxl_path: Path) -> bool:
    """Check if JXL has jbrd (JPEG Bitstream Reconstruction Data) box.
    Returns True if this JXL can be losslessly transcoded back to JPEG.
    Parses ISOBMFF boxes sequentially until jbrd is found or EOF, so files
    with large metadata headers before jbrd are detected correctly.
    """
    try:
        with open(jxl_path, 'rb') as f:
            header = f.read(12)

            if header[:2] == b'\xff\x0a':  # Bare codestream
                return False
            if header[:12] != b'\x00\x00\x00\x0cJXL \x0d\x0a\x87\x0a':
                return False

            while True:
                box_header = f.read(8)
                if len(box_header) < 8:
                    return False

                size = int.from_bytes(box_header[:4], 'big')
                box_type = box_header[4:8]

                if box_type == b'jbrd':
                    return True

                if size == 0:  # Box extends to end of file
                    return False
                elif size == 1:  # Extended 64-bit size
                    ext_size = f.read(8)
                    if len(ext_size) < 8:
                        return False
                    size = int.from_bytes(ext_size, 'big')
                    if size < 16:
                        return False
                    payload = size - 16
                else:
                    if size < 8:
                        return False
                    payload = size - 8

                if payload > 0:
                    f.seek(payload, 1)
    except Exception:
        return False

def reorder_jxl_boxes(jxl_path: Path):
    """Reorder boxes so Exif comes BEFORE codestream (IrfanView compatibility)."""
    data = jxl_path.read_bytes()
    file_size = len(data)
    
    # Sanity check: reasonable file size (prevent OOM on malformed files)
    MAX_JXL_SIZE = 4 * 1024 * 1024 * 1024  # 4GB max
    if file_size > MAX_JXL_SIZE:
        raise RuntimeError(f"JXL file too large ({file_size} bytes), skipping box reorder")
    if file_size < 12:  # Minimum valid JXL: 12-byte signature
        return  # Too small to have boxes, leave as-is

    # Bare codestream has no boxes to reorder; leave as-is.
    if data[:2] == b'\xff\x0a':
        return

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
                break
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

    CODESTREAM = {b"jxlc", b"jxlp"}
    meta_order_boxes, meta_extra_boxes, codestream_boxes, other_boxes = [], [], [], []

    for name, h, p in boxes:
        if name in {b"JXL ", b"ftyp", b"jxll"}:
            meta_order_boxes.append((name, h, p))
        elif name in {b"Exif", b"xml ", b"jbrd", b"brob"}:
            meta_extra_boxes.append((name, h, p))
        elif name in CODESTREAM:
            codestream_boxes.append((name, h, p))
        else:
            other_boxes.append((name, h, p))

    out = b""
    for _, h, p in meta_order_boxes:
        out += h + p
    for _, h, p in meta_extra_boxes:
        out += h + p
    for _, h, p in codestream_boxes:
        out += h + p
    for _, h, p in other_boxes:
        out += h + p
    jxl_path.write_bytes(out)

# --------------------------------------------─
# FILE FINDERS
# --------------------------------------------─

def find_jpegs_flat(input_path: Path):
    seen, files = set(), []
    for ext in ("*.jpg", "*.jpeg", "*.JPG", "*.JPEG"):
        for f in input_path.glob(ext):
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                files.append(f)
    return files

def find_jpegs_recursive(input_path: Path):
    seen, files = set(), []
    for ext in ("*.jpg", "*.jpeg", "*.JPG", "*.JPEG"):
        for f in input_path.rglob(ext):
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                files.append(f)
    return files

def find_jxls_flat(input_path: Path):
    seen, files = set(), []
    for f in input_path.glob("*.jxl"):
        key = f.resolve()
        if key not in seen:
            seen.add(key)
            files.append(f)
    return sorted(files)

def find_jxls_recursive(input_path: Path):
    seen, files = set(), []
    for f in input_path.rglob("*.jxl"):
        key = f.resolve()
        if key not in seen:
            seen.add(key)
            files.append(f)
    return sorted(files)

def find_pngs_recursive(input_path: Path):
    seen, files = set(), []
    for ext in ("*.png", "*.PNG"):
        for f in input_path.rglob(ext):
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                files.append(f)
    return files

def find_pngs_flat(input_path: Path):
    seen, files = set(), []
    for ext in ("*.png", "*.PNG"):
        for f in input_path.glob(ext):
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                files.append(f)
    return files

# --------------------------------------------─
# SMART RECONVERT CHECK
# --------------------------------------------─

def should_process(src: Path, dst: Path, smart: bool, reconvert_val: bool) -> bool:
    """Check if file should be processed based on reconvert settings.
    smart=True: only process if src is newer than dst (or dst doesn't exist)
    smart=False: process based on reconvert_val (True=reconvert, False=skip)
    """
    if not dst.exists():
        return True
    if smart:
        # Check if source is newer than destination
        return src.stat().st_mtime > dst.stat().st_mtime
    # Not smart mode: use reconvert_val
    return reconvert_val

# --------------------------------------------─
# SAFETY CONFIRMATIONS
# --------------------------------------------─

def confirm_deletion_jpeg() -> bool:
    """Confirmation for lossless transcode deletion (simple yes)."""
    print()
    print(" [!] WARNING -- DELETE_SOURCE is enabled")
    print(" Source files will be deleted after successful operation.")
    print(" This is IRREVERSIBLE. Type 'yes' to confirm.")
    print()
    try:
        answer = input(" > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer == "yes":
        print(" Confirmed.")
        print()
        return True
    else:
        print(" Cancelled.")
        print()
        return False

def confirm_deletion_lossy() -> bool:
    """Confirmation for lossy convert deletion (requires HHMM for safety)."""
    print()
    print(" [!] WARNING -- DELETE_SOURCE is enabled for LOSSY conversion")
    print(" Source files will be PERMANENTLY DELETED after conversion.")
    print(" This operation involves LOSSY compression and is IRREVERSIBLE.")
    print()
    now_str = datetime.now().strftime("%H%M")
    print(f" Type the current time ({now_str}) to confirm you understand the risks.")
    print(" (Any other input will cancel)")
    print()
    try:
        answer = input(" > ").strip()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer == now_str:
        print(" Confirmed.")
        print()
        return True
    else:
        print(" Cancelled.")
        print()
        return False

# --------------------------------------------─
# COMMAND ROUTING (Auto-detect)
# --------------------------------------------─

def determine_command(input_path: Path, force_transcode: bool = False, 
                       force_convert: bool = False) -> tuple:
    """Determine which command to run based on input file.
    Returns (command_str, auto_decode_bool, explanation)
    """
    if force_transcode:
        return ("transcode", False, "Forced transcode")
    if force_convert:
        return ("convert", False, "Forced convert")

    if not input_path.exists():
        return ("error", False, f"Input not found: {input_path}")

    # Single file detection
    if input_path.is_file():
        ext = input_path.suffix.lower()

        if ext in ('.jpg', '.jpeg', '.jfif', '.jpe'):
            # JPEG -> always transcode encode (lossless)
            return ("transcode", False, "JPEG detected: lossless encode to JXL")

        elif ext == '.jxl':
            # JXL -> check for jbrd box
            if has_jbrd_box(input_path):
                return ("transcode", True, "JXL with jbrd detected: lossless decode to JPEG")
            else:
                return ("convert", False, "JXL without jbrd: convert (lossy decode)")

        elif ext == '.png':
            # PNG -> convert to JXL (no lossless transcode for PNG)
            return ("convert", False, "PNG detected: encode to JXL")

        else:
            return ("error", False, f"Unsupported extension: {ext}")

    else:
        # Directory - will be handled by respective commands
        # Default to transcode for mixed content? Or require explicit?
        return ("auto", False, "Directory detected: will check contents")

# --------------------------------------------─
# TRANSCODE IMPLEMENTATION
# --------------------------------------------─

def resolve_output_transcode(src_path: Path, mode: int, input_root: Path, decode: bool) -> Path:
    out_ext = ".jpg" if decode else ".jxl"
    conv_folder = RECOVERED_JPEG_FOLDER if decode else CONVERTED_JXL_FOLDER
    input_root = Path(input_root)
    sibling_jxl = JPEG_SIBLING_FOLDER if decode else JXL_SIBLING_FOLDER
    exp_out = EXPORT_JPEG_FOLDER if decode else EXPORT_JXL_FOLDER
    sfx_from = JXL_SUFFIX_TO_REPLACE if decode else JPEG_SUFFIX_TO_REPLACE
    sfx_to = JPEG_SUFFIX_REPLACE_DEC if decode else JXL_SUFFIX_REPLACE

    if mode == 0:
        if input_root != src_path.parent:
            return input_root / src_path.with_suffix(out_ext).name
        return src_path.parent / src_path.with_suffix(out_ext).name
    elif mode == 1:
        return src_path.parent / conv_folder / src_path.with_suffix(out_ext).name
    elif mode == 2:
        return input_root / src_path.with_suffix(out_ext).name
    elif mode == 3:
        return src_path.parent / conv_folder / src_path.with_suffix(out_ext).name
    elif mode == 4:
        return src_path.parent.parent / sibling_jxl / src_path.with_suffix(out_ext).name
    elif mode == 5:
        old_name = src_path.parent.name
        new_name = None
        for variant in [sfx_from, sfx_from.lower(), sfx_from.title()]:
            if variant in old_name:
                new_name = old_name.replace(variant, sfx_to)
                break
        if new_name is None:
            new_name = old_name + "_" + sfx_to
            logger.warning(f"'{sfx_from}' not found in '{old_name}', using '{new_name}'")
        return src_path.parent.parent / new_name / src_path.with_suffix(out_ext).name
    elif mode in (6, 7):
        parts = src_path.parts
        marker_lower = EXPORT_MARKER.lower()
        # Match folders starting or ending with EXPORT_MARKER case-insensitively
        export_idx = next((i for i, p in enumerate(parts)
                           if p.lower().startswith(marker_lower) or p.lower().endswith(marker_lower)), None)
        if export_idx is None:
            # Files outside the export marker must be ignored in modes 6/7.
            return None
        export_dir = Path(*parts[:export_idx + 1])
        if mode == 6:
            if _is_relative_to(src_path, export_dir):
                rel_parts = src_path.relative_to(export_dir).parts
                rel = Path(*rel_parts[1:]) if len(rel_parts) > 1 else Path(rel_parts[0])
            else:
                rel = src_path.relative_to(Path(*parts[:export_idx]))
        else:
            if EXPORT_JPEG_SUBFOLDER:
                anchor = export_dir / EXPORT_JPEG_SUBFOLDER
                if not _is_relative_to(src_path, anchor):
                    return None
                rel = src_path.relative_to(anchor)
            else:
                rel_parts = src_path.relative_to(export_dir).parts
                rel = Path(*rel_parts[1:]) if len(rel_parts) > 1 else Path(rel_parts[0])
        return export_dir / exp_out / rel.with_suffix(out_ext)
    elif mode == 8:
        return src_path.parent / src_path.with_suffix(out_ext).name
    else:
        raise ValueError(f"Invalid mode: {mode}")

def encode_one_transcode(src_path: Path, write_path: Path, final_path: Path, 
                         reconvert_val: bool, effort: int, smart: bool) -> tuple:
    # Check if should process - pass both smart and reconvert_val
    if not should_process(src_path, final_path, smart, reconvert_val):
        n, total = next_count()
        if smart:
            logger.info(f"[{n}/{total}] SKIP (destination newer or exists) | {src_path.name}")
        else:
            logger.info(f"[{n}/{total}] SKIP (exists) | {src_path.name}")
        return (str(src_path), "skipped", str(final_path), None)
    
    overwritten = final_path.exists()

    try:
        write_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    try:
        src_md5 = md5_of_file(src_path) if STORE_MD5 else None

        # Determine if this is an encode (JPEG -> JXL) or decode (JXL -> JPEG)
        is_jpeg_encode = src_path.suffix.lower() in ('.jpg', '.jpeg', '.jfif', '.jpe')

        if is_jpeg_encode:
            r = subprocess.run(
                ["cjxl", str(src_path), str(write_path), "--lossless_jpeg=1",
                 "--effort", str(effort)],
                capture_output=True, timeout=600
            )
        else:
            r = subprocess.run(
                ["djxl", str(src_path), str(write_path)],
                capture_output=True, timeout=600
            )
        if r.returncode != 0:
            raise RuntimeError(f"{'cjxl' if is_jpeg_encode else 'djxl'}: {r.stderr.decode(errors='replace')[:200]}")

        if is_jpeg_encode:
            reorder_jxl_boxes(write_path)

        if src_md5:
            # Use write_path directory but final_path name (no UUID) for checksum entry
            # This ensures checksums.md5 has correct filenames even with staging
            checksum_path = write_path.parent / final_path.name
            store_md5_db(checksum_path, src_md5)

        n, total = next_count()
        label = "RECONVERT" if overwritten else "OK"
        logger.info(f"[{n}/{total}] {label} | {src_path.name} -> {write_path.name}")
        return (str(src_path), "reconvert" if overwritten else "ok", str(final_path), src_md5)
    except Exception as e:
        n, total = next_count()
        logger.error(f"[{n}/{total}] ERROR | {src_path.name} | {e}")
        return (str(src_path), "error", str(e), None)

def decode_one_transcode(jxl_path: Path, write_path: Path, final_path: Path,
                         verify: bool, reconvert_val: bool, smart: bool) -> tuple:
    # This function now also handles JPEG -> JXL encode in auto mode; the
    # verification path only applies to JXL decode.
    is_jxl_decode = jxl_path.suffix.lower() == '.jxl'

    # Check if should process - pass both smart and reconvert_val
    if not should_process(jxl_path, final_path, smart, reconvert_val):
        n, total = next_count()
        if smart:
            logger.info(f"[{n}/{total}] SKIP (destination newer or exists) | {jxl_path.name}")
        else:
            logger.info(f"[{n}/{total}] SKIP (exists) | {jxl_path.name}")
        return (str(jxl_path), "skipped", str(final_path), None)

    # Force-transcode decode is documented as requiring a jbrd box for lossless
    # recovery. Without jbrd, djxl would re-encode lossy and silently label it as
    # lossless, risking data loss (especially with --delete-source). Reject these
    # files early and log them for review.
    if is_jxl_decode and not has_jbrd_box(jxl_path):
        _log_rejected_file(str(jxl_path), "force-transcode decode requires jbrd box")
        raise RuntimeError(
            f"{jxl_path.name}: force-transcode decode requires jbrd box. "
            "Use auto mode or --force-convert for lossy decode."
        )

    overwritten = final_path.exists()
    write_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        stored_md5 = read_md5_db(jxl_path) if verify and is_jxl_decode else None

        r = subprocess.run(
            ["djxl", str(jxl_path), str(write_path)],
            capture_output=True, timeout=600
        )
        if r.returncode != 0:
            raise RuntimeError(f"djxl: {r.stderr.decode(errors='replace')[:200]}")

        n, total = next_count()

        if verify:
            if stored_md5 is None:
                logger.warning(f"[{n}/{total}] OK (no MD5 stored) | {jxl_path.name}")
            else:
                recovered_md5 = md5_of_file(write_path)
                if recovered_md5 == stored_md5:
                    logger.info(f"[{n}/{total}] OK [MD5 PASS] | {jxl_path.name}")
                else:
                    logger.error(f"[{n}/{total}] MD5 FAIL | {jxl_path.name}")
                    return (str(jxl_path), "md5_fail", str(final_path), None)
        else:
            logger.info(f"[{n}/{total}] OK | {jxl_path.name} -> {write_path.name}")

        return (str(jxl_path), "ok", str(final_path), None)
    except Exception as e:
        n, total = next_count()
        logger.error(f"[{n}/{total}] ERROR | {jxl_path.name} | {e}")
        return (str(jxl_path), "error", str(e), None)

def process_group_transcode(group_pairs: list, workers: int, decode: bool, 
                            verify: bool, mode: int, reconvert_val: bool, smart: bool, effort: int = 7) -> list:
    use_staging = TEMP2_DIR is not None
    staging_dir = Path(TEMP2_DIR) if use_staging else None
    if use_staging:
        staging_dir.mkdir(parents=True, exist_ok=True)

    ext = ".jpg" if decode else ".jxl"
    tasks = []
    for src, final_out in group_pairs:
        write_out = (staging_dir / f"{uuid.uuid4().hex}_{src.stem}{ext}") if use_staging else final_out
        tasks.append((src, write_out, final_out))

    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        if decode:
            futures = {ex.submit(decode_one_transcode, s, w, f, verify, reconvert_val, smart): (s, w, f) 
                      for s, w, f in tasks}
        else:
            futures = {ex.submit(encode_one_transcode, s, w, f, reconvert_val, effort, smart): (s, w, f) 
                      for s, w, f in tasks}
        for fut in as_completed(futures):
            results.append(fut.result())

    if use_staging:
        moved = 0
        status_map = {r[0]: r[1] for r in results}
        for src, write_out, final_out in tasks:
            status = status_map.get(str(src), "error")
            if status not in ("ok", "reconvert"):
                if status != "skipped":
                    logger.warning(f"  KEEP in staging ({status}) | {write_out.name}")
                continue
            if not write_out.exists():
                logger.warning(f"  KEEP (staging file missing) | {write_out.name}")
                continue
            final_out.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(write_out), str(final_out))
            moved += 1

        if not decode:  # Only for encode (decode doesn't create checksums in staging)
            staging_db = staging_dir / CHECKSUMS_FILENAME
            if staging_db.exists() and tasks:
                # Map each filename in staging checksums to its final destination folder
                from collections import defaultdict
                # Build lookup: filename -> final parent folder (only for successful tasks)
                dest_map = {}
                for src, write_out, final_out in tasks:
                    status = status_map.get(str(src), "error")
                    if status in ("ok", "reconvert"):
                        dest_map[final_out.name] = final_out.parent
                db_lines = staging_db.read_text(encoding="utf-8").splitlines(keepends=True)
                folder_lines = defaultdict(list)
                for line in db_lines:
                    parts = line.strip().split("  ", 1)
                    if len(parts) == 2:
                        fname = parts[1]
                        dest_folder = dest_map.get(fname)
                        if dest_folder:
                            folder_lines[dest_folder].append(line)
                        else:
                            # Fallback: put in first successful task's folder if unmatched
                            first_success = next((final_out.parent for src, _, final_out in tasks
                                                  if status_map.get(str(src), "error") in ("ok", "reconvert")), None)
                            if first_success:
                                folder_lines[first_success].append(line)
                for folder, lines in folder_lines.items():
                    final_db = folder / CHECKSUMS_FILENAME
                    final_db.parent.mkdir(parents=True, exist_ok=True)
                    with _md5_db_lock:
                        with open(final_db, "a", encoding="utf-8") as dst:
                            dst.writelines(lines)
                staging_db.unlink()

        if moved:
            logger.info(f" -> Moved {moved} file(s) from staging to destination")

    if DELETE_SOURCE and mode == 8:
        deleted = 0
        src_map = {str(s): (s, f) for s, _, f in tasks}
        for result in results:
            status = result[1]
            src_md5 = result[3] if len(result) > 3 else None
            if status not in ("ok", "reconvert"):
                continue
            src_path, final_file = src_map.get(result[0], (None, None))
            if src_path is None or not final_file.exists():
                continue
            if STORE_MD5 and DELETE_SOURCE_REQUIRE_MD5 and not decode:
                if src_md5 is None or read_md5_db(final_file) is None:
                    logger.warning(f" KEEP (MD5 not confirmed) | {src_path.name}")
                    continue
            if not _verify_file_integrity(final_file):
                logger.warning(f" KEEP (output failed integrity check) | {src_path.name}")
                continue
            src_path.unlink()
            deleted += 1
            logger.info(f" DELETED source | {src_path.name}")
        if deleted:
            logger.info(f" -> Deleted {deleted} source file(s)")

    return results

def cmd_transcode(args, auto_decode: bool = False):
    global _counter, STORE_MD5, DELETE_SOURCE, TEMP2_DIR
    _counter = {"done": 0, "total": 0}

    TEMP2_DIR = args.staging
    # Extract reconvert settings
    smart_mode = args.sync
    reconvert_explicit = args.overwrite
    if args.no_md5:
        STORE_MD5 = False
    if args.delete_source:
        DELETE_SOURCE = True

    # Determine direction
    decode = args.decode or auto_decode

    log_file = setup_logger()
    direction_str = "DECODE (JXL -> JPEG)" if decode else "ENCODE (JPEG -> JXL)"

    op_type = "TRANSCODE lossless" if not decode else "TRANSCODE decode (lossless recovery)"
    # Determine mode string
    if smart_mode:
        mode_str = "smart (source newer -> reconvert)"
    elif reconvert_explicit:
        mode_str = "reconvert=ON"
    else:
        mode_str = "reconvert=OFF (skip existing)"
    logger.info(f"{op_type} | Mode: {args.mode} | Effort: {args.effort} | "
                f"Store MD5: {STORE_MD5} | delete_source={DELETE_SOURCE} | "
                f"{mode_str} | Staging: {TEMP2_DIR or 'disabled'} | Workers: {args.workers}")
    logger.info(f"Input: {args.input}")

    # Collect files
    if args.input.is_file():
        files = [args.input]
        output_root = args.output or args.input.parent
    elif decode:
        files = find_jxls_flat(args.input) if args.mode == 0 else find_jxls_recursive(args.input)
        output_root = args.output or args.input
        if args.mode == 2:
            output_root.mkdir(parents=True, exist_ok=True)
    else:
        files = find_jpegs_flat(args.input) if args.mode == 0 else find_jpegs_recursive(args.input)
        output_root = args.output or args.input
        if args.mode == 2:
            output_root.mkdir(parents=True, exist_ok=True)

    if not files:
        logger.warning("No input files found.")
        return

    _counter["total"] = len(files)
    logger.info(f"Files found: {len(files)}")

    # Build pairs
    pairs = []
    for f in files:
        out = resolve_output_transcode(f, args.mode, output_root, decode)
        if out is None:
            continue  # Skip files outside _EXPORT for modes 6/7
        pairs.append((f, out))

    # Group by output folder
    groups = {}
    for f, out in pairs:
        groups.setdefault(out.parent, []).append((f, out))

    if args.mode == 8 and DELETE_SOURCE:
        if DELETE_CONFIRM:
            # Check if this is lossy decode (JXL -> JPEG) or lossless transcode
            is_lossy_decode = decode and not args.force_transcode
            if is_lossy_decode:
                if not confirm_deletion_lossy():
                    logger.info("Deletion not confirmed -- exiting.")
                    return
            else:
                if not confirm_deletion_jpeg():
                    logger.info("Deletion not confirmed -- exiting.")
                    return

    logger.info(f"Output groups: {len(groups)}")

    ok = err = skipped = overwritten = md5_fail = 0
    for dest_folder, group_pairs in groups.items():
        if len(groups) > 1:
            logger.info(f"-- Group: {dest_folder} ({len(group_pairs)} file(s))")

        results = process_group_transcode(group_pairs, args.workers, decode,
                                         not args.no_verify, args.mode, reconvert_explicit, smart_mode, args.effort)

        for result in results:
            status = result[1]
            if status == "ok":
                ok += 1
            elif status == "reconvert":
                ok += 1
                overwritten += 1
            elif status == "skipped":
                skipped += 1
            elif status == "md5_fail":
                err += 1
                md5_fail += 1
            elif status == "error":
                err += 1

    logger.info(f"\n{'-'*50}")
    if decode and md5_fail:
        logger.info(f"Done: {ok} OK | {skipped} skipped | {err} errors ({md5_fail} MD5 failures)")
    else:
        logger.info(f"Done: {ok} OK | {overwritten} reconverted | {skipped} up to date | {err} errors")
    logger.info(f"Log: {log_file}")

# --------------------------------------------─
# CONVERT IMPLEMENTATION
# --------------------------------------------─

def resolve_output_convert(src_path: Path, mode: int, output_name: str, suffix: str,
                           ext: str, rename_from: str = "", rename_to: str = "",
                           output_root: Path = None, decode: bool = False) -> Path:
    stem = src_path.stem
    if rename_from and rename_from in stem:
        stem = stem.replace(rename_from, rename_to, 1)

    # Determine folder names based on direction
    conv_folder = RECOVERED_JPEG_FOLDER if decode else CONVERTED_JXL_FOLDER
    sibling_folder = JPEG_SIBLING_FOLDER if decode else JXL_SIBLING_FOLDER
    sfx_from = JXL_SUFFIX_TO_REPLACE if decode else JPEG_SUFFIX_TO_REPLACE
    sfx_to = JPEG_SUFFIX_REPLACE_DEC if decode else JXL_SUFFIX_REPLACE
    exp_out = EXPORT_JPEG_FOLDER if decode else EXPORT_JXL_FOLDER

    if mode == 0:
        if output_root and Path(output_root) != src_path.parent:
            return Path(output_root) / f"{stem}.{ext}"
        return src_path.parent / f"{stem}.{ext}"
    elif mode == 1:
        if output_root:
            return Path(output_root) / output_name / f"{stem}.{ext}"
        return src_path.parent.parent / output_name / f"{stem}.{ext}"
    elif mode == 2:
        if output_root:
            return Path(output_root) / f"{stem}.{ext}"
        new_folder = src_path.parent.name + suffix
        return src_path.parent.parent / new_folder / f"{stem}.{ext}"
    elif mode == 3:
        # Subfolder (same as mode 1, used for recursive processing)
        if output_root:
            return Path(output_root) / output_name / f"{stem}.{ext}"
        return src_path.parent / conv_folder / f"{stem}.{ext}"
    elif mode == 4:
        # Sibling folder (e.g., JXL_jpeg/ or JPEG_recovered/)
        return src_path.parent.parent / sibling_folder / f"{stem}.{ext}"
    elif mode == 5:
        # Folder suffix replacement
        old_name = src_path.parent.name
        new_name = None
        for variant in [sfx_from, sfx_from.lower(), sfx_from.title()]:
            if variant in old_name:
                new_name = old_name.replace(variant, sfx_to)
                break
        if new_name is None:
            new_name = old_name + "_" + sfx_to
            logger.warning(f"'{sfx_from}' not found in '{old_name}', using '{new_name}'")
        return src_path.parent.parent / new_name / f"{stem}.{ext}"
    elif mode in (6, 7):
        # Export marker modes - only process files INSIDE export marker folder
        parts = src_path.parts
        marker_lower = EXPORT_MARKER.lower()
        # Match folders starting or ending with EXPORT_MARKER case-insensitively
        export_idx = next((i for i, p in enumerate(parts)
                           if p.lower().startswith(marker_lower) or p.lower().endswith(marker_lower)), None)
        if export_idx is None:
            return None  # Skip files outside export marker folder
        export_dir = Path(*parts[:export_idx + 1])
        if mode == 6:
            # Mode 6: any file inside export marker folder
            rel_parts = src_path.relative_to(export_dir).parts
            rel = Path(*rel_parts[1:]) if len(rel_parts) > 1 else Path(rel_parts[0])
        else:
            # Mode 7: only files inside export marker / EXPORT_JPEG_SUBFOLDER
            if EXPORT_JPEG_SUBFOLDER:
                anchor = export_dir / EXPORT_JPEG_SUBFOLDER
                if not _is_relative_to(src_path, anchor):
                    return None  # Not in the specific subfolder
                rel = src_path.relative_to(anchor)
            else:
                rel_parts = src_path.relative_to(export_dir).parts
                rel = Path(*rel_parts[1:]) if len(rel_parts) > 1 else Path(rel_parts[0])
        return export_dir / exp_out / rel.with_suffix(f".{ext}")
    elif mode == 8:
        # In-place (same as mode 0)
        return src_path.parent / f"{stem}.{ext}"
    else:
        raise ValueError(f"Invalid convert mode: {mode}")

def encode_to_jxl(src_path: Path, write_path: Path, final_path: Path, 
                  effort: int, distance: float, reconvert_val: bool, smart: bool) -> tuple:
    """Convert any image (JPEG/PNG) to JXL."""
    # Use should_process for consistent logic
    if not should_process(src_path, final_path, smart, reconvert_val):
        n, total = next_count()
        if smart:
            logger.info(f"[{n}/{total}] SKIP (destination newer or exists) | {src_path.name}")
        else:
            logger.info(f"[{n}/{total}] SKIP (exists) | {src_path.name}")
        return (str(src_path), "skipped", str(final_path), None)
    
    overwritten = final_path.exists()
    write_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Build cjxl command
        cmd = ["cjxl", str(src_path), str(write_path), "--effort", str(effort), "-d", str(distance)]
        # cjxl 0.11.2 default --lossless_jpeg=1 is incompatible with distance>0
        if distance > 0:
            cmd.append("--lossless_jpeg=0")

        # Add container flag for metadata support (needed for EXIF in IrfanView)
        if FORCE_CONTAINER_FOR_LOSSY:
            cmd.append("--container=1")

        r = subprocess.run(cmd, capture_output=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"cjxl: {r.stderr.decode(errors='replace')[:200]}")

        # Reorder boxes for IrfanView compatibility
        reorder_jxl_boxes(write_path)

        n, total = next_count()
        label = "RECONVERT" if overwritten else "OK"
        logger.info(f"[{n}/{total}] {label} | {src_path.name} -> {write_path.name}")
        return (str(src_path), "reconvert" if overwritten else "ok", str(final_path), None)
    except Exception as e:
        n, total = next_count()
        logger.error(f"[{n}/{total}] ERROR | {src_path.name} | {e}")
        return (str(src_path), "error", str(e), None)

def decode_to_image(jxl_path: Path, write_path: Path, final_path: Path,
                    quality: int, fmt: str, bit_depth: int,
                    output_icc: str, use_ram: bool, reconvert_val: bool, smart: bool) -> tuple:
    """Convert JXL to JPEG or PNG."""
    # Use should_process for consistent logic
    if not should_process(jxl_path, final_path, smart, reconvert_val):
        n, total = next_count()
        if smart:
            logger.info(f"[{n}/{total}] SKIP (destination newer or exists) | {jxl_path.name}")
        else:
            logger.info(f"[{n}/{total}] SKIP (exists) | {jxl_path.name}")
        return (str(jxl_path), "skipped", str(final_path), None)
    
    overwritten = final_path.exists()
    write_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        actual_out = write_path
        original_final_path = final_path

        is_png = (fmt == "png")
        if fmt == "jpeg" and bit_depth == 16:
            logger.warning(f" JPEG doesn't support 16-bit, switching to PNG | {jxl_path.name}")
            is_png = True
            actual_out = write_path.with_suffix(".png")
            final_path = final_path.with_suffix(".png")

        if is_png:
            # PNG output
            if output_icc and MAGICK_AVAILABLE:
                # Handle built-in color spaces vs ICC file paths
                builtins = ('sRGB',)
                if output_icc in builtins:
                    cs_name = output_icc.replace(' ', '')
                    magick_output = ["-colorspace", cs_name, "-depth", str(bit_depth)]
                    logger.debug(f"Using colorspace conversion: {cs_name}")
                else:
                    magick_output = ["-profile", output_icc, "-depth", str(bit_depth)]
                    logger.debug(f"Using ICC profile: {output_icc}")
                if use_ram:
                    djxl_cmd = ["djxl", str(jxl_path), "-", "--output_format=png"]
                    magick_cmd = ["magick", "-"] + magick_output + [str(actual_out)]
                    _run_pipeline_safe(djxl_cmd, magick_cmd, timeout=300)
                else:
                    with tempfile.TemporaryDirectory(dir=TEMP_DIR) as tmp:
                        tmp_png = Path(tmp) / "tmp.png"
                        subprocess.run(["djxl", str(jxl_path), str(tmp_png)], check=True, timeout=600)
                        subprocess.run(["magick", str(tmp_png)] + magick_output + [str(actual_out)], check=True, timeout=600)
            else:
                # Direct djxl to PNG
                r = subprocess.run(["djxl", str(jxl_path), str(actual_out), f"--bits_per_sample={bit_depth}"], capture_output=True, timeout=600)
                if r.returncode != 0:
                    raise RuntimeError(f"djxl: {r.stderr.decode(errors='replace')[:200]}")
        else:
            # JPEG output via djxl directly (no magick needed unless ICC conversion)
            if output_icc and MAGICK_AVAILABLE:
                # Handle built-in color spaces vs ICC file paths
                builtins = ('sRGB',)
                if output_icc in builtins:
                    # Use colorspace conversion (no ICC file needed)
                    cs_name = output_icc.replace(' ', '')  # 'Adobe RGB' -> 'AdobeRGB'
                    magick_output = ["-colorspace", cs_name, "-quality", str(quality)]
                    logger.debug(f"Using colorspace conversion: {cs_name}")
                else:
                    # Use ICC profile file
                    magick_output = ["-profile", output_icc, "-quality", str(quality)]
                    logger.debug(f"Using ICC profile: {output_icc}")
                if use_ram:
                    djxl_cmd = ["djxl", str(jxl_path), "-", "--output_format=png"]
                    magick_cmd = ["magick", "-"] + magick_output + [str(actual_out)]
                    _run_pipeline_safe(djxl_cmd, magick_cmd, timeout=300)
                else:
                    with tempfile.TemporaryDirectory(dir=TEMP_DIR) as tmp:
                        tmp_png = Path(tmp) / "tmp.png"
                        subprocess.run(["djxl", str(jxl_path), str(tmp_png)], check=True, timeout=600)
                        subprocess.run(["magick", str(tmp_png)] + magick_output + [str(actual_out)], check=True, timeout=600)
            else:
                # Direct djxl to JPG (preserves embedded ICC)
                quality_flag = f"--jpeg_quality={quality}"
                r = subprocess.run(["djxl", quality_flag, str(jxl_path), str(actual_out)], capture_output=True, timeout=600)
                if r.returncode != 0:
                    raise RuntimeError(f"djxl: {r.stderr.decode(errors='replace')[:200]}")

        n, total = next_count()
        label = "RECONVERT" if overwritten else "OK"
        logger.info(f"[{n}/{total}] {label} | {jxl_path.name} -> {actual_out.name}")
        return (str(jxl_path), "reconvert" if overwritten else "ok", str(final_path), None)

    except Exception as e:
        # Clean up any alternate extension orphan created during bit_depth fallback
        if actual_out != write_path and actual_out.exists():
            try:
                actual_out.unlink()
            except OSError:
                pass
        n, total = next_count()
        logger.error(f"[{n}/{total}] ERROR | {jxl_path.name} | {e}")
        return (str(jxl_path), "error", str(e), None)

def process_group_convert(group_pairs: list, workers: int, direction: str,
                          quality: int, distance: float, fmt: str, bit_depth: int,
                          output_icc: str, use_ram: bool, effort: int, reconvert_val: bool,
                          use_internal_srgb: bool, smart: bool) -> list:
    use_staging = TEMP2_DIR is not None
    staging_dir = Path(TEMP2_DIR) if use_staging else None
    if use_staging:
        staging_dir.mkdir(parents=True, exist_ok=True)

    tasks = []
    for src, final_out in group_pairs:
        if use_staging:
            ext = final_out.suffix
            write_out = staging_dir / f"{uuid.uuid4().hex}_{src.stem}{ext}"
        else:
            write_out = final_out
        tasks.append((src, write_out, final_out))

    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        if direction == "to_jxl":
            futures = {ex.submit(encode_to_jxl, s, w, f, effort, distance, reconvert_val, smart): (s, w, f) 
                      for s, w, f in tasks}
        else:
            futures = {ex.submit(decode_to_image, s, w, f, quality, fmt, bit_depth, 
                                output_icc, use_ram, reconvert_val, smart): (s, w, f) 
                      for s, w, f in tasks}
        for fut in as_completed(futures):
            results.append(fut.result())

    if use_staging:
        moved = 0
        status_map = {r[0]: r[1] for r in results}
        for src, write_out, final_out in tasks:
            status = status_map.get(str(src), "error")
            if status in ("ok", "reconvert", "overwrite") and write_out.exists():
                final_out.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(write_out), str(final_out))
                moved += 1
            elif write_out.exists():
                # Failed conversion: do not promote a partial/corrupt file to the
                # final destination. Leave it in staging so the user can inspect.
                logger.warning(f"Staging: not promoting {write_out.name} (status={status})")
        if moved:
            logger.info(f" -> Moved {moved} file(s) from staging to destination")

    return results

def cmd_convert(args, from_jxl: bool = True):
    global _counter, TEMP2_DIR, DELETE_SOURCE
    _counter = {"done": 0, "total": 0}

    if args.icc_profile and not MAGICK_AVAILABLE:
        print("ERROR: --icc-profile requires ImageMagick (magick) in PATH.")
        sys.exit(1)

    TEMP2_DIR = args.staging
    smart_mode = args.sync
    reconvert_explicit = args.overwrite
    
    # Handle DELETE_SOURCE from CLI
    if args.delete_source:
        DELETE_SOURCE = True

    log_file = setup_logger()

    # Determine direction and set defaults
    if from_jxl:
        direction = "from_jxl"
        # Default to JPEG if format not specified
        if args.format is None:
            args.format = "jpeg"
            logger.debug("Defaulting format to JPEG for JXL decode")

        # PNG default 16-bit, JPEG 8-bit
        if args.format == "png" and args.bit_depth is None:
            args.bit_depth = PNG_DEFAULT_BIT_DEPTH
            logger.info(f"PNG output: defaulting to {PNG_DEFAULT_BIT_DEPTH}-bit depth")
        elif args.bit_depth is None:
            args.bit_depth = 8
    else:
        direction = "to_jxl"
        if args.bit_depth is None:
            args.bit_depth = 8  # Irrelevant for JXL output, but keep a sane default


    # Collect files (ICC label, op_type, and mode_str are set after this)
    if args.input.is_file():
        files = [args.input]
        output_root = args.output or args.input.parent
    elif direction == "to_jxl":
        jpegs = find_jpegs_flat(args.input) if args.mode == 0 else find_jpegs_recursive(args.input)
        pngs = find_pngs_flat(args.input) if args.mode == 0 else find_pngs_recursive(args.input)
        files = jpegs + pngs
        # If no JPEGs/PNGs found, fall back to JXLs (auto-detect direction)
        if not files:
            jxls = find_jxls_flat(args.input) if args.mode == 0 else find_jxls_recursive(args.input)
            if jxls:
                files = jxls
                direction = "from_jxl"
                if args.format is None:
                    args.format = "jpeg"
                if args.bit_depth is None:
                    args.bit_depth = 8
                logger.debug("Auto-detected JXL content, switching to from_jxl")
        output_root = args.output or args.input
        if args.mode == 2:
            output_root.mkdir(parents=True, exist_ok=True)
    else:
        files = find_jxls_flat(args.input) if args.mode == 0 else find_jxls_recursive(args.input)
        output_root = args.output or args.input
        if args.mode == 2:
            output_root.mkdir(parents=True, exist_ok=True)

    if not files:
        logger.warning("No input files found.")
        return

    _counter["total"] = len(files)

    # ICC label logic (after direction may have auto-changed)
    if args.icc_profile:
        icc_label = f"converting to {Path(args.icc_profile).stem}"
    elif direction == "to_jxl":
        icc_label = "preserving embedded from source"
    else:
        icc_label = "preserving embedded from JXL"

    op_type = "CONVERT lossy" if direction == "from_jxl" else "CONVERT to JXL"

    if smart_mode:
        mode_str = "smart (source newer -> reconvert)"
    elif reconvert_explicit:
        mode_str = "reconvert=ON"
    else:
        mode_str = "reconvert=OFF (skip existing)"

    logger.info(f"{op_type} | Mode: {args.mode} | "
                f"Format: {args.format} | Quality: {args.quality} | "
                f"Bit depth: {args.bit_depth} | ICC: {icc_label} | "
                f"RAM: {args.ram} | delete_source={DELETE_SOURCE} | {mode_str} | "
                f"Staging: {TEMP2_DIR or 'disabled'} | Workers: {args.workers}")
    if args.rename_from:
        logger.info(f"Filename rename: '{args.rename_from}' -> '{args.rename_to}'")
    logger.info(f"Input: {args.input}")
    logger.info(f"Files found: {len(files)}")

    # Build pairs
    pairs = []
    for f in files:
        is_decode = (direction == "from_jxl")
        if direction == "to_jxl":
            out = resolve_output_convert(f, args.mode, args.output_name,
                                         args.output_suffix, "jxl",
                                         args.rename_from, args.rename_to,
                                         output_root, decode=False)
        else:
            # Default to jpg if format is somehow None, else use specified
            fmt = args.format if args.format else "jpeg"
            ext = "jpg" if fmt == "jpeg" else "png"
            out = resolve_output_convert(f, args.mode, args.output_name,
                                         args.output_suffix, ext,
                                         args.rename_from, args.rename_to,
                                         output_root, decode=True)
        if out is None:
            continue  # Skip files outside _EXPORT for modes 6/7
        pairs.append((f, out))

    if args.dry_run:
        for f, out in pairs:
            logger.info(f" DRY | {f.name} -> {out}")
        logger.info(f"Dry run: {len(pairs)} files would be converted.")
        return

    groups = {}
    for f, out in pairs:
        groups.setdefault(out.parent, []).append((f, out))

    logger.info(f"Output groups: {len(groups)}")

    # Safety confirmation for Mode 8 + DELETE_SOURCE
    # Determine if operation is lossy based on direction and settings
    if args.mode == 8 and DELETE_SOURCE:
        if DELETE_CONFIRM:
            is_lossy = False
            if direction == "to_jxl":
                # PNG/JPEG -> JXL: lossy if distance > 0
                is_lossy = args.distance > 0
            else:
                # JXL -> JPEG/PNG: lossy if output is JPEG, or if PNG with ICC conversion
                fmt = args.format if args.format else "jpeg"
                is_lossy = (fmt == "jpeg") or (args.icc_profile is not None)
            
            if is_lossy:
                if not confirm_deletion_lossy():
                    logger.info("Deletion not confirmed -- exiting.")
                    return
            else:
                if not confirm_deletion_jpeg():
                    logger.info("Deletion not confirmed -- exiting.")
                    return

    ok = err = skipped = overwritten = 0
    for dest_folder, group_pairs in groups.items():
        if len(groups) > 1:
            logger.info(f"-- Group: {dest_folder} ({len(group_pairs)} file(s))")

        results = process_group_convert(
            group_pairs, args.workers, direction,
            args.quality, args.distance, args.format, args.bit_depth,
            args.icc_profile, args.ram, args.effort, reconvert_explicit,
            False, smart_mode
        )
        
        # Handle DELETE_SOURCE for convert mode (lossy)
        if DELETE_SOURCE and args.mode == 8:
            deleted = 0
            src_map = {str(s): (s, out) for s, out in group_pairs}
            for result in results:
                status = result[1]
                if status not in ("ok", "reconvert"):
                    continue
                src_path, final_file = src_map.get(result[0], (None, None))
                if src_path is None:
                    continue
                if final_file is None or not _verify_file_integrity(final_file):
                    logger.warning(f" KEEP (output failed integrity check) | {src_path.name}")
                    continue
                src_path.unlink()
                deleted += 1
                logger.info(f" DELETED source | {src_path.name}")
            if deleted:
                logger.info(f" -> Deleted {deleted} source file(s)")

        for _, status, _, _ in results:
            if status == "ok":
                ok += 1
            elif status == "reconvert":
                ok += 1
                overwritten += 1
            elif status == "skipped":
                skipped += 1
            elif status == "error":
                err += 1

    logger.info(f"\n{'-'*50}")
    logger.info(f"Done: {ok} OK | {overwritten} reconverts | {skipped} skipped | {err} errors")
    logger.info(f"Log: {log_file}")

# --------------------------------------------─
# AUTO MODE (Per-file detection for directories)
# --------------------------------------------─

def cmd_auto(args):
    """Auto-detect per-file for batch processing.
    
    For directories containing JPEG and/or JXL files:
    - JPEG files         -> lossless transcode encode to JXL
    - JXL files WITH jbrd box -> lossless transcode decode to JPEG
    - JXL files WITHOUT jbrd  -> lossy convert
    """
    global _counter, TEMP2_DIR, DELETE_SOURCE
    _counter = {"done": 0, "total": 0}
    
    TEMP2_DIR = args.staging
    smart_mode = args.sync
    reconvert_explicit = args.overwrite
    
    log_file = setup_logger()
    
    # Collect JPEG files (encode direction) and JXL files (decode/convert direction)
    if args.mode == 0:
        jpeg_files = find_jpegs_flat(args.input)
        jxl_files = find_jxls_flat(args.input)
    else:
        jpeg_files = find_jpegs_recursive(args.input)
        jxl_files = find_jxls_recursive(args.input)
    
    if not jpeg_files and not jxl_files:
        logger.warning("No JPEG or JXL files found.")
        return
    
    # Separate JXL files by jbrd presence
    jxl_transcode_files = []  # Have jbrd - can decode losslessly to JPEG
    jxl_convert_files = []    # No jbrd - must do lossy convert
    
    for f in jxl_files:
        if has_jbrd_box(f):
            jxl_transcode_files.append(f)
        else:
            jxl_convert_files.append(f)
    
    total_files = len(jpeg_files) + len(jxl_transcode_files) + len(jxl_convert_files)
    _counter["total"] = total_files
    
    # Confirm source deletion BEFORE any processing. Lossy conversion requires
    # stricter confirmation than lossless transcode. Only meaningful in mode 8.
    if args.delete_source and args.mode == 8:
        has_lossy = bool(jxl_convert_files)
        has_lossless = bool(jpeg_files) or bool(jxl_transcode_files)
        if has_lossy:
            if not confirm_deletion_lossy():
                logger.info("Deletion not confirmed -- exiting.")
                return
        elif has_lossless:
            if not confirm_deletion_jpeg():
                logger.info("Deletion not confirmed -- exiting.")
                return
        DELETE_SOURCE = True
    
    logger.info(f"AUTO MODE | Directory: {args.input}")
    logger.info(f"JPEG files (lossless encode): {len(jpeg_files)}")
    logger.info(f"JXL with jbrd (lossless decode): {len(jxl_transcode_files)}")
    logger.info(f"JXL without jbrd (lossy): {len(jxl_convert_files)}")
    logger.info(f"Mode: {args.mode} | Workers: {args.workers} | Staging: {TEMP2_DIR or 'disabled'}")
    
    # Process JPEG files (lossless encode to JXL)
    if jpeg_files:
        logger.info(f"\n--- Processing {len(jpeg_files)} JPEG files (lossless encode) ---")
        _process_file_group(jpeg_files, args, use_transcode=True)
    
    # Process JXL transcode files (lossless decode to JPEG)
    if jxl_transcode_files:
        logger.info(f"\n--- Processing {len(jxl_transcode_files)} JXL files with jbrd (lossless) ---")
        _process_file_group(jxl_transcode_files, args, use_transcode=True)
    
    # Process JXL convert files (lossy)
    if jxl_convert_files:
        logger.info(f"\n--- Processing {len(jxl_convert_files)} JXL files without jbrd (lossy) ---")
        _process_file_group(jxl_convert_files, args, use_transcode=False)
    
    logger.info(f"\n{'-'*50}")
    logger.info(f"AUTO MODE complete | Total: {total_files} files")
    logger.info(f"Log: {log_file}")

def _process_file_group(files, args, use_transcode=True):
    """Process a group of files with the same method."""
    # Use explicit output directory if provided, otherwise fall back to input root
    output_root = args.output if args.output is not None else args.input

    # Build output pairs
    pairs = []
    for f in files:
        if use_transcode:
            # Lossless transcode: direction depends on input extension
            is_jpeg_input = f.suffix.lower() in ('.jpg', '.jpeg', '.jfif', '.jpe')
            out = resolve_output_transcode(f, args.mode, output_root, decode=not is_jpeg_input)
        else:
            # Lossy convert: output format defaults to JPEG if not specified
            out_ext = "jpg" if (args.format or "jpeg") in ("jpeg", "jpg") else "png"
            # Default bit depth per format, matching cmd_convert behavior
            default_depth = 8 if (args.format or "jpeg") in ("jpeg", "jpg") else PNG_DEFAULT_BIT_DEPTH
            out = resolve_output_convert(
                f, args.mode, args.output_name, args.output_suffix,
                out_ext, args.rename_from, args.rename_to,
                output_root, decode=True
            )
        if out:
            pairs.append((f, out))
    
    if not pairs:
        return
    
    # Group by output folder
    groups = {}
    for f, out in pairs:
        groups.setdefault(out.parent, []).append((f, out))
    
    # Process each group
    for dest_folder, group_pairs in groups.items():
        if use_transcode:
            # Separate JPEG encode vs JXL decode within the transcode group
            encode_pairs = [(s, f) for s, f in group_pairs if s.suffix.lower() in ('.jpg', '.jpeg', '.jfif', '.jpe')]
            decode_pairs = [(s, f) for s, f in group_pairs if s.suffix.lower() == '.jxl']
            if encode_pairs:
                process_group_transcode(
                    encode_pairs, args.workers, decode=False,
                    verify=not args.no_verify, mode=args.mode,
                    reconvert_val=args.overwrite, smart=args.sync, effort=args.effort
                )
            if decode_pairs:
                process_group_transcode(
                    decode_pairs, args.workers, decode=True,
                    verify=not args.no_verify, mode=args.mode,
                    reconvert_val=args.overwrite, smart=args.sync, effort=args.effort
                )
        else:
            results = process_group_convert(
                group_pairs, args.workers, direction="from_jxl",
                quality=args.quality, distance=args.distance,
                fmt=args.format or "jpeg", bit_depth=args.bit_depth or default_depth,
                output_icc=args.icc_profile, use_ram=args.ram,
                effort=args.effort, reconvert_val=args.overwrite,
                use_internal_srgb=False, smart=args.sync
            )
            # Handle DELETE_SOURCE for lossy convert (auto mode), only in mode 8
            if DELETE_SOURCE and args.mode == 8:
                deleted = 0
                src_map = {str(s): (s, out) for s, out in group_pairs}
                for result in results:
                    status = result[1]
                    if status not in ("ok", "reconvert"):
                        continue
                    src_path, final_file = src_map.get(result[0], (None, None))
                    if src_path is None:
                        continue
                    if final_file is None or not _verify_file_integrity(final_file):
                        logger.warning(f" KEEP (output failed integrity check) | {src_path.name}")
                        continue
                    src_path.unlink()
                    deleted += 1
                    logger.info(f" DELETED source | {src_path.name}")
                if deleted:
                    logger.info(f" -> Deleted {deleted} source file(s)")

# --------------------------------------------─
# AUTO MODE (Per-file detection for directories)
# --------------------------------------------─
# MAIN ENTRY POINT (Auto-routing)
# --------------------------------------------─

def main():
    parser = argparse.ArgumentParser(
        description="JPEG XL Toolkit - Auto-routing edition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Auto-detection (no subcommand needed for single files):
  photo.jpg       -> transcode encode (lossless to JXL)
  photo.jxl       -> transcode decode if jbrd present, else convert
  photo.png       -> convert to JXL

Explicit flags (for directories or override):
  %(prog)s <input> --force-transcode [options]   # lossless JPEG<->JXL
  %(prog)s <input> --force-convert  [options]     # lossy with ICC support

Examples:
  %(prog)s photo.jpg --mode 1                      # auto: transcode to converted_jxl/
  %(prog)s photo.jxl --format png                  # auto: to PNG (16-bit default)
  %(prog)s ./folder --force-transcode --mode 8     # explicit: batch transcoding
  %(prog)s photo.jxl --force-convert --format jpeg --quality 95
        """
    )

    # Global options
    parser.add_argument("input", type=Path, help="Input file or folder")
    parser.add_argument("--mode", type=int, default=None,
                        help="Output mode (0=in-place, 1=subfolder, etc)")
    parser.add_argument("--workers", type=int, default=min(os.cpu_count() or 4, 16),
                        help="Parallel workers")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    parser.add_argument("--sync", action="store_true",
                        help="Smart mode: only process if source is newer than destination")
    parser.add_argument("--staging", type=str, default=None, help="Staging directory")

    # Format options (for convert/from_jxl)
    parser.add_argument("--format", type=str, choices=["jpeg", "jpg", "png"], default=None,
                        help="Output format (for JXL decode). PNG defaults to 16-bit.")
    parser.add_argument("--quality", type=int, default=JPEG_DEFAULT_QUALITY,
                        help="JPEG quality 1-100")
    parser.add_argument("--distance", type=float, default=1.0,
                        help="JXL butteraugli distance for lossy encoding (0.0=lossless, 1.0=default, higher=smaller)")
    parser.add_argument("--bit-depth", type=int, choices=[8, 16], default=None,
                        help="Output bit depth (PNG only, default: 16)")
    parser.add_argument("--icc-profile", type=str, default=None,
                        help="ICC profile for color conversion (requires ImageMagick). "
                             "Can be a file path or the built-in name: sRGB")
    parser.add_argument("--to-srgb", action="store_true",
                        help="Shortcut: convert to sRGB using ImageMagick built-in color space")

    # Transcode specific
    parser.add_argument("--decode", action="store_true", help="Force decode direction")
    parser.add_argument("--no-md5", action="store_true", help="Skip MD5 storage")
    parser.add_argument("--no-verify", action="store_true", help="Skip MD5 verify on decode")
    parser.add_argument("--delete-source", action="store_true", help="Delete after mode 8")
    parser.add_argument("--effort", type=int, default=CJXL_EFFORT, choices=range(1, 11),
                        help="cjxl effort 1-10")

    # Convert specific
    parser.add_argument("--ram", action="store_true", default=True, help="Use RAM pipeline")
    parser.add_argument("--no-ram", dest="ram", action="store_false", help="Use disk")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen")
    parser.add_argument("--output-name", type=str, default=CONVERT_OUTPUT_FOLDER,
                        help="Output folder name for modes 0,1")
    parser.add_argument("output", nargs="?", type=Path, default=None,
                        help="Output directory (mode 0 single file)")
    parser.add_argument("--output-suffix", type=str, default=CONVERT_OUTPUT_SUFFIX,
                        help="Suffix for mode 2")
    parser.add_argument("--rename-from", type=str, default="", help="Rename pattern")
    parser.add_argument("--rename-to", type=str, default="", help="Rename replacement")

    # Export marker (must match wrapper's configured marker)
    parser.add_argument("--export-marker", type=str, default="_EXPORT",
                        help="Folder name marker for modes 6/7 (default: _EXPORT)")

    # Force override
    parser.add_argument("--force-transcode", action="store_true",
                        help="Force transcode command")
    parser.add_argument("--force-convert", action="store_true",
                        help="Force convert command")

    args = parser.parse_args()

    # Normalize format: jpg -> jpeg
    if args.format == "jpg":
        args.format = "jpeg"

    # Apply configurable export marker before resolving outputs
    global EXPORT_MARKER
    if args.export_marker:
        EXPORT_MARKER = args.export_marker

    # Handle --to-srgb shortcut
    if args.to_srgb:
        args.icc_profile = 'sRGB'

    # Determine command
    cmd, auto_decode, reason = determine_command(args.input, args.force_transcode, 
                                                  args.force_convert)

    if cmd == "error":
        print(f"ERROR: {reason}")
        sys.exit(1)

    # Set default mode based on command if not specified
    if args.mode is None:
        if cmd == "transcode":
            args.mode = TRANSCODE_DEFAULT_MODE  # 0 = in-place
        else:
            args.mode = CONVERT_DEFAULT_MODE     # 0 = in-place

    # Route to appropriate command
    if cmd == "transcode":
        cmd_transcode(args, auto_decode)
    elif cmd == "convert":
        # Determine direction for convert
        if args.input.suffix.lower() == '.jxl' or args.decode:
            cmd_convert(args, from_jxl=True)
        else:
            cmd_convert(args, from_jxl=False)
    elif cmd == "auto":
        # Auto-detect per-file for directories
        cmd_auto(args)
    else:
        # Fallback - should not reach here
        print(f"ERROR: Unknown command state: {cmd}")
        sys.exit(1)

if __name__ == "__main__":
    main()