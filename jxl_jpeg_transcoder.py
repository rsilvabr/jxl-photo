#!/usr/bin/env python3
"""
jxl_jpeg_transcoder.py — Unified JPEG XL toolkit (Round-trip optimized)

Auto-detect workflow:
  JPEG input -&gt; transcode (lossless encode to JXL)
  JXL input  -&gt; checks for jbrd box -&gt; transcode decode (lossless recovery) if present
               otherwise convert (lossy to JPEG/PNG)
  PNG input  -&gt; convert (to JXL, lossy or modular lossless)

Usage:
  python jxl_jpeg_transcoder.py photo.jpg                    # auto: transcode encode
  python jxl_jpeg_transcoder.py photo.jxl                  # auto: transcode decode (if brob present)
  python jxl_jpeg_transcoder.py photo.jxl --format png     # auto: convert to PNG (if no brob)
  python jxl_jpeg_transcoder.py --help

Requirements:
  cjxl / djxl -&gt; https://github.com/libjxl/libjxl/releases
  exiftool    -&gt; https://exiftool.org
  magick      -&gt; https://imagemagick.org (optional, for ICC)
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
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

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

RECONVERT = "smart"
# False = skip existing. True = always reconvert. "smart" = only if source newer.

# ImageMagick detection (auto, do not modify)
MAGICK_AVAILABLE = shutil.which("magick") is not None

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
    logger.setLevel(logging.DEBUG)

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.info(f"Log: {log_file}")
    return log_file

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

def read_md5_db(jxl_path: Path) -> str | None:
    db_path = jxl_path.parent / CHECKSUMS_FILENAME
    if not db_path.exists():
        return None
    target = jxl_path.name
    with open(db_path, "r", encoding="utf-8") as f:
        for line in f:
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
    Reads up to 16KB to handle files with large metadata headers."""
    try:
        with open(jxl_path, 'rb') as f:
            header = f.read(16384)  # Increased from 4KB to 16KB for safety
            # jbrd box signature
            return b'jbrd' in header
    except Exception:
        return False

def jxl_has_any_exif(jxl_path: Path) -> bool:
    with tempfile.TemporaryDirectory(dir=TEMP_DIR) as tmp:
        arg = Path(tmp) / "check.args"
        arg.write_text(f"-v3\n{jxl_path}\n", encoding="utf-8")
        r = subprocess.run(["exiftool", "-@", str(arg)], capture_output=True, text=True)
        return ("Tag 'Exif'" in r.stdout) or ("BrotliEXIF" in r.stdout)

def reorder_jxl_boxes(jxl_path: Path):
    """Reorder boxes so Exif comes BEFORE codestream (IrfanView compatibility)."""
    data = jxl_path.read_bytes()
    boxes = []
    i = 0
    while i < len(data):
        if i + 8 > len(data):
            break
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

def inject_exif_to_jxl_from_jpeg(jxl_path: Path, jpeg_path: Path, tmp_dir: Path):
    """Extract raw EXIF from JPEG and inject into JXL as a proper Exif box.

    cjxl with --lossless_jpeg=1 stores EXIF as BrotliEXIF (not readable by IrfanView).
    This function extracts the raw EXIF and re-injects it using exiftool,
    which creates a proper Exif box that all viewers can read.
    """
    # Extract raw EXIF binary from JPEG
    arg_file = tmp_dir / "exif_extract.args"
    arg_file.write_text(f"-b\n-Exif\n{jpeg_path}\n", encoding="utf-8")
    r = subprocess.run(["exiftool", "-@", str(arg_file)], capture_output=True, text=True)
    if r.returncode != 0 or len(r.stdout) <= 8:
        logger.debug(f"  No EXIF to inject from {jpeg_path.name}")
        return

    exif_bin = tmp_dir / f"{jpeg_path.stem}.exif.bin"
    exif_bin.write_bytes(r.stdout)

    # Inject EXIF into JXL
    r2 = subprocess.run(
        ["exiftool", "-overwrite_original", f"-Exif<={exif_bin}", str(jxl_path)],
        capture_output=True, text=True
    )
    if r2.returncode != 0:
        logger.warning(f"  EXIF injection failed for {jpeg_path.name}: {r2.stderr[:100]}")
        return

    # Reorder boxes so Exif comes before codestream (IrfanView requirement)
    reorder_jxl_boxes(jxl_path)
    logger.debug(f"  EXIF injected as raw Exif box for {jpeg_path.name}")
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

        if ext in ('.jpg', '.jpeg'):
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
        export_idx = next((i for i, p in enumerate(parts) if EXPORT_MARKER in p), None)
        if export_idx is None:
            logger.warning(f"'{EXPORT_MARKER}' not found in {src_path}, using local folder")
            return src_path.parent / exp_out / src_path.with_suffix(out_ext).name
        export_dir = Path(*parts[:export_idx + 1])
        if mode == 6:
            if src_path.is_relative_to(export_dir):
                rel_parts = src_path.relative_to(export_dir).parts
                rel = Path(*rel_parts[1:]) if len(rel_parts) > 1 else Path(rel_parts[0])
            else:
                rel = src_path.relative_to(Path(*parts[:export_idx]))
        else:
            if EXPORT_JPEG_SUBFOLDER:
                anchor = export_dir / EXPORT_JPEG_SUBFOLDER
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
        elif reconvert_val:
            logger.info(f"[{n}/{total}] SKIP (exists) | {src_path.name}")
        else:
            logger.info(f"[{n}/{total}] SKIP (exists) | {src_path.name}")
        return (str(src_path), "skipped", str(final_path), None)
    
    overwritten = final_path.exists()

    write_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        src_md5 = md5_of_file(src_path) if STORE_MD5 else None

        r = subprocess.run(
            ["cjxl", str(src_path), str(write_path), "--lossless_jpeg=1", 
             "--effort", str(effort)],
            capture_output=True
        )
        if r.returncode != 0:
            raise RuntimeError(f"cjxl: {r.stderr.decode(errors='replace')[:200]}")

        reorder_jxl_boxes(write_path)

        if src_md5:
            store_md5_db(write_path, src_md5)

        n, total = next_count()
        label = "RECONVERT" if overwritten else "OK"
        logger.info(f"[{n}/{total}] {label} | {src_path.name} -&gt; {write_path.name}")
        return (str(src_path), "reconvert" if overwritten else "ok", str(final_path), src_md5)
    except Exception as e:
        n, total = next_count()
        logger.error(f"[{n}/{total}] ERROR | {src_path.name} | {e}")
        return (str(src_path), "error", str(e), None)

def decode_one_transcode(jxl_path: Path, write_path: Path, final_path: Path, 
                         verify: bool, reconvert_val: bool, smart: bool) -> tuple:
    # Check if should process - pass both smart and reconvert_val
    if not should_process(jxl_path, final_path, smart, reconvert_val):
        n, total = next_count()
        if smart:
            logger.info(f"[{n}/{total}] SKIP (destination newer or exists) | {jxl_path.name}")
        elif reconvert_val:
            logger.info(f"[{n}/{total}] SKIP (exists) | {jxl_path.name}")
        else:
            logger.info(f"[{n}/{total}] SKIP (exists) | {jxl_path.name}")
        return (str(jxl_path), "skipped", str(final_path))

    overwritten = final_path.exists()
    write_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        stored_md5 = read_md5_db(jxl_path) if verify else None

        r = subprocess.run(
            ["djxl", str(jxl_path), str(write_path)],
            capture_output=True
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
                    return (str(jxl_path), "md5_fail", str(final_path))
        else:
            logger.info(f"[{n}/{total}] OK | {jxl_path.name} -&gt; {write_path.name}")

        return (str(jxl_path), "ok", str(final_path))
    except Exception as e:
        n, total = next_count()
        logger.error(f"[{n}/{total}] ERROR | {jxl_path.name} | {e}")
        return (str(jxl_path), "error", str(e))

def process_group_transcode(group_pairs: list, workers: int, decode: bool, 
                            verify: bool, mode: int, reconvert_val: bool, smart: bool) -> list:
    use_staging = TEMP2_DIR is not None
    staging_dir = Path(TEMP2_DIR) if use_staging else None
    if use_staging:
        staging_dir.mkdir(parents=True, exist_ok=True)

    ext = ".jpg" if decode else ".jxl"
    tasks = []
    for src, final_out in group_pairs:
        write_out = (staging_dir / f"{src.parent.name}__{src.stem}{ext}") if use_staging else final_out
        tasks.append((src, write_out, final_out))

    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        if decode:
            futures = {ex.submit(decode_one_transcode, s, w, f, verify, reconvert_val, smart): (s, w, f) 
                      for s, w, f in tasks}
        else:
            futures = {ex.submit(encode_one_transcode, s, w, f, reconvert_val, CJXL_EFFORT, smart): (s, w, f) 
                      for s, w, f in tasks}
        for fut in as_completed(futures):
            results.append(fut.result())

    if use_staging:
        moved = 0
        for _, write_out, final_out in tasks:
            if write_out.exists():
                final_out.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(write_out), str(final_out))
                moved += 1

        if not decode:  # Only for encode (decode doesn't create checksums in staging)
            staging_db = staging_dir / CHECKSUMS_FILENAME
            if staging_db.exists() and tasks:
                final_db = Path(list({f for _, _, f in tasks})[0]).parent / CHECKSUMS_FILENAME
                final_db.parent.mkdir(parents=True, exist_ok=True)
                with _md5_db_lock:
                    with open(final_db, "a", encoding="utf-8") as dst:
                        dst.write(staging_db.read_text(encoding="utf-8"))
                staging_db.unlink()

        if moved:
            logger.info(f" -&gt; Moved {moved} file(s) from staging to destination")

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
            src_path.unlink()
            deleted += 1
            logger.info(f" DELETED source | {src_path.name}")
        if deleted:
            logger.info(f" -&gt; Deleted {deleted} source file(s)")

    return results

def cmd_transcode(args, auto_decode: bool = False):
    global _counter, STORE_MD5, DELETE_SOURCE, TEMP2_DIR, RECONVERT
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
    direction_str = "DECODE (JXL -&gt; JPEG)" if decode else "ENCODE (JPEG -&gt; JXL)"

    op_type = "TRANSCODE lossless" if not decode else "TRANSCODE decode (lossless recovery)"
    # Determine mode string
    if smart_mode:
        mode_str = "smart (source newer -&gt; reconvert)"
    elif reconvert_explicit:
        mode_str = "reconvert=ON"
    else:
        mode_str = "reconvert=OFF (skip existing)"
    logger.info(f"{op_type} | Mode: {args.mode} | Effort: {CJXL_EFFORT} | "
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
    else:
        files = find_jpegs_flat(args.input) if args.mode == 0 else find_jpegs_recursive(args.input)
        output_root = args.output or args.input

    if not files:
        logger.warning("No input files found.")
        return

    _counter["total"] = len(files)
    logger.info(f"Files found: {len(files)}")

    # Build pairs
    pairs = []
    for f in files:
        out = resolve_output_transcode(f, args.mode, output_root, decode)
        pairs.append((f, out))

    # Group by output folder
    groups = {}
    for f, out in pairs:
        groups.setdefault(out.parent, []).append((f, out))

    if args.mode == 8 and DELETE_SOURCE:
        if DELETE_CONFIRM:
            if not confirm_deletion_jpeg():
                logger.info("Deletion not confirmed -- exiting.")
                return

    logger.info(f"Output groups: {len(groups)}")

    ok = err = skipped = overwritten = md5_fail = 0
    for dest_folder, group_pairs in groups.items():
        if len(groups) > 1:
            logger.info(f"-- Group: {dest_folder} ({len(group_pairs)} file(s))")

        results = process_group_transcode(group_pairs, args.workers, decode,
                                         not args.no_verify, args.mode, reconvert_explicit, smart_mode)

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
        # Export marker modes
        parts = src_path.parts
        export_idx = next((i for i, p in enumerate(parts) if EXPORT_MARKER in p), None)
        if export_idx is None:
            logger.warning(f"'{EXPORT_MARKER}' not found in {src_path}, using local folder")
            return src_path.parent / exp_out / f"{stem}.{ext}"
        export_dir = Path(*parts[:export_idx + 1])
        if mode == 6:
            if src_path.is_relative_to(export_dir):
                rel_parts = src_path.relative_to(export_dir).parts
                rel = Path(*rel_parts[1:]) if len(rel_parts) > 1 else Path(rel_parts[0])
            else:
                rel = src_path.relative_to(Path(*parts[:export_idx]))
        else:
            # Mode 7: only process inside EXPORT_MARKER subfolder
            if EXPORT_JPEG_SUBFOLDER:
                anchor = export_dir / EXPORT_JPEG_SUBFOLDER
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
                  effort: int, reconvert_val: bool, smart: bool) -> tuple:
    """Convert any image (JPEG/PNG) to JXL."""
    # Use should_process for consistent logic
    if not should_process(src_path, final_path, smart, reconvert_val):
        n, total = next_count()
        if smart:
            logger.info(f"[{n}/{total}] SKIP (destination newer or exists) | {src_path.name}")
        else:
            logger.info(f"[{n}/{total}] SKIP (exists) | {src_path.name}")
        return (str(src_path), "skipped", str(final_path))
    
    overwritten = final_path.exists()
    write_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Build cjxl command
        cmd = ["cjxl", str(src_path), str(write_path), "--effort", str(effort)]

        # Add container flag for metadata support (needed for EXIF in IrfanView)
        if FORCE_CONTAINER_FOR_LOSSY:
            cmd.append("--container=1")

        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(f"cjxl: {r.stderr.decode(errors='replace')[:200]}")

        # Reorder boxes for IrfanView compatibility
        reorder_jxl_boxes(write_path)

        n, total = next_count()
        label = "RECONVERT" if overwritten else "OK"
        logger.info(f"[{n}/{total}] {label} | {src_path.name} -&gt; {write_path.name}")
        return (str(src_path), "reconvert" if overwritten else "ok", str(final_path))
    except Exception as e:
        n, total = next_count()
        logger.error(f"[{n}/{total}] ERROR | {src_path.name} | {e}")
        return (str(src_path), "error", str(e))

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
        return (str(jxl_path), "skipped", str(final_path))
    
    overwritten = final_path.exists()
    write_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        actual_out = write_path

        if fmt == "jpeg":
            if bit_depth == 16:
                logger.warning(f" JPEG doesn't support 16-bit, switching to PNG | {jxl_path.name}")
                fmt = "png"
                actual_out = write_path.with_suffix(".png")

            # JPEG output via djxl directly (no magick needed unless ICC conversion)
            if output_icc and MAGICK_AVAILABLE:
                # Handle built-in color spaces vs ICC file paths
                builtins = ('sRGB', 'Adobe RGB', 'ProPhoto RGB')
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
                    djxl_proc = subprocess.Popen(
                        ["djxl", str(jxl_path), "-", "--output_format=png"],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE
                    )
                    magick_cmd = ["magick", "-"] + magick_output + [str(actual_out)]
                    magick_proc = subprocess.Popen(
                        magick_cmd, stdin=djxl_proc.stdout,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE
                    )
                    djxl_proc.stdout.close()
                    magick_proc.communicate()
                    djxl_proc.wait()
                    if djxl_proc.returncode != 0 or magick_proc.returncode != 0:
                        raise RuntimeError("djxl/magick failed")
                else:
                    with tempfile.TemporaryDirectory(dir=TEMP_DIR) as tmp:
                        tmp_png = Path(tmp) / "tmp.png"
                        subprocess.run(["djxl", str(jxl_path), str(tmp_png)], check=True)
                        subprocess.run(["magick", str(tmp_png)] + magick_output + [str(actual_out)], check=True)
            else:
                # Direct djxl to JPG (preserves embedded ICC)
                r = subprocess.run(["djxl", str(jxl_path), str(actual_out)], capture_output=True)
                if r.returncode != 0:
                    raise RuntimeError(f"djxl: {r.stderr.decode(errors='replace')[:200]}")

        else:  # PNG output
            if use_ram and MAGICK_AVAILABLE and output_icc:
                # Handle built-in color spaces vs ICC file paths
                builtins = ('sRGB', 'Adobe RGB', 'ProPhoto RGB')
                if output_icc in builtins:
                    cs_name = output_icc.replace(' ', '')
                    magick_output = ["-colorspace", cs_name, "-depth", str(bit_depth)]
                    logger.debug(f"Using colorspace conversion: {cs_name}")
                else:
                    magick_output = ["-profile", output_icc, "-depth", str(bit_depth)]
                    logger.debug(f"Using ICC profile: {output_icc}")
                djxl_proc = subprocess.Popen(
                    ["djxl", str(jxl_path), "-", "--output_format=png"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                magick_cmd = ["magick", "-"] + magick_output + [str(actual_out)]
                magick_proc = subprocess.Popen(
                    magick_cmd, stdin=djxl_proc.stdout,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                djxl_proc.stdout.close()
                magick_proc.communicate()
                djxl_proc.wait()
            else:
                # Direct djxl to PNG
                r = subprocess.run(["djxl", str(jxl_path), str(actual_out)], capture_output=True)
                if r.returncode != 0:
                    raise RuntimeError(f"djxl: {r.stderr.decode(errors='replace')[:200]}")

        n, total = next_count()
        label = "RECONVERT" if overwritten else "OK"
        logger.info(f"[{n}/{total}] {label} | {jxl_path.name} -&gt; {actual_out.name}")
        return (str(jxl_path), "reconvert" if overwritten else "ok", str(actual_out))

    except Exception as e:
        n, total = next_count()
        logger.error(f"[{n}/{total}] ERROR | {jxl_path.name} | {e}")
        return (str(jxl_path), "error", str(e))

def process_group_convert(group_pairs: list, workers: int, direction: str,
                          quality: int, fmt: str, bit_depth: int,
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
            write_out = staging_dir / f"{src.parent.name}__{src.stem}{ext}"
        else:
            write_out = final_out
        tasks.append((src, write_out, final_out))

    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        if direction == "to_jxl":
            futures = {ex.submit(encode_to_jxl, s, w, f, effort, reconvert_val, smart): (s, w, f) 
                      for s, w, f in tasks}
        else:
            futures = {ex.submit(decode_to_image, s, w, f, quality, fmt, bit_depth, 
                                output_icc, use_ram, reconvert_val, smart): (s, w, f) 
                      for s, w, f in tasks}
        for fut in as_completed(futures):
            results.append(fut.result())

    if use_staging:
        moved = 0
        for _, write_out, final_out in tasks:
            if write_out.exists():
                final_out.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(write_out), str(final_out))
                moved += 1
        if moved:
            logger.info(f" -&gt; Moved {moved} file(s) from staging to destination")

    return results

def cmd_convert(args, from_jxl: bool = True):
    global _counter, TEMP2_DIR, RECONVERT, DELETE_SOURCE
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
        args.bit_depth = 8  # Irrelevant for JXL output

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
    else:
        files = find_jxls_flat(args.input) if args.mode == 0 else find_jxls_recursive(args.input)
        output_root = args.output or args.input

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
        pairs.append((f, out))

    if args.dry_run:
        for f, out in pairs:
            logger.info(f" DRY | {f.name} -&gt; {out}")
        logger.info(f"Dry run: {len(pairs)} files would be converted.")
        return

    groups = {}
    for f, out in pairs:
        groups.setdefault(out.parent, []).append((f, out))

    logger.info(f"Output groups: {len(groups)}")

    # Safety confirmation for Mode 8 + DELETE_SOURCE (lossy operation)
    if args.mode == 8 and DELETE_SOURCE:
        if DELETE_CONFIRM:
            if not confirm_deletion_lossy():
                logger.info("Deletion not confirmed -- exiting.")
                return

    ok = err = skipped = overwritten = 0
    for dest_folder, group_pairs in groups.items():
        if len(groups) > 1:
            logger.info(f"-- Group: {dest_folder} ({len(group_pairs)} file(s))")

        results = process_group_convert(
            group_pairs, args.workers, direction,
            args.quality, args.format, args.bit_depth,
            args.icc_profile, args.ram, args.effort, reconvert_explicit,
            False, smart_mode
        )
        
        # Handle DELETE_SOURCE for convert mode (lossy)
        if DELETE_SOURCE and args.mode == 8:
            deleted = 0
            src_map = {str(s): s for s, _ in group_pairs}
            for result in results:
                status = result[1]
                if status not in ("ok", "reconvert"):
                    continue
                src_path = src_map.get(result[0])
                if src_path is None:
                    continue
                src_path.unlink()
                deleted += 1
                logger.info(f" DELETED source | {src_path.name}")
            if deleted:
                logger.info(f" -&gt; Deleted {deleted} source file(s)")

        for _, status, _ in results:
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
# MAIN ENTRY POINT (Auto-routing)
# --------------------------------------------─

def main():
    parser = argparse.ArgumentParser(
        description="JPEG XL Toolkit - Auto-routing edition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Auto-detection (no subcommand needed for single files):
  photo.jpg       -&gt; transcode encode (lossless to JXL)
  photo.jxl       -&gt; transcode decode if jbrd present, else convert
  photo.png       -&gt; convert to JXL

Explicit subcommands (for directories or override):
  %(prog)s transcode <input> [options]   # lossless JPEG<->JXL
  %(prog)s convert <input> [options]     # lossy with ICC support

Examples:
  %(prog)s photo.jpg --mode 1            # auto: transcode to converted_jxl/
  %(prog)s photo.jxl --format png        # auto: to PNG (16-bit default)
  %(prog)s transcode ./folder --mode 8   # explicit: batch transcoding
  %(prog)s convert photo.jxl --to-jpeg --quality 95
        """
    )

    # Global options
    parser.add_argument("input", type=Path, help="Input file or folder")
    parser.add_argument("--mode", type=int, default=None,
                        help="Output mode (0=in-place, 1=subfolder, etc)")
    parser.add_argument("--workers", type=int, default=min(os.cpu_count(), 16),
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
    parser.add_argument("--bit-depth", type=int, choices=[8, 16], default=None,
                        help="Output bit depth (PNG only, default: 16)")
    parser.add_argument("--icc-profile", type=str, default=None,
                        help="ICC profile for color conversion (requires ImageMagick). "
                             "Can be a file path or built-in name: sRGB, Adobe RGB, ProPhoto RGB")
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
    parser.add_argument("output", nargs="?", type=str, default=None,
                        help="Output directory (mode 0 single file)")
    parser.add_argument("--output-suffix", type=str, default=CONVERT_OUTPUT_SUFFIX,
                        help="Suffix for mode 2")
    parser.add_argument("--rename-from", type=str, default="", help="Rename pattern")
    parser.add_argument("--rename-to", type=str, default="", help="Rename replacement")

    # Force override
    parser.add_argument("--force-transcode", action="store_true", 
                        help="Force transcode command")
    parser.add_argument("--force-convert", action="store_true",
                        help="Force convert command")

    args = parser.parse_args()

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
    else:
        # Auto with directory - requires explicit subcommand
        print("ERROR: Directory input requires explicit 'transcode' or 'convert' subcommand.")
        print("Use: python jxl_jpeg_transcoder.py transcode <folder> [options]")
        print("Or:  python jxl_jpeg_transcoder.py convert <folder> [options]")
        sys.exit(1)

if __name__ == "__main__":
    main()