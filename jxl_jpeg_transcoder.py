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
import functools
import re
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional


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

        # Read the header once; the JXL box walk below reuses the same handle.
        with open(file_path, 'rb') as f:
            header = f.read(12)

        if len(header) < 2:
            return False
        
        if ext == '.jxl':
            # Bare JXL: 0xFF 0x0A. Every output this toolkit produces is a
            # CONTAINER (metadata boxes are always injected), so a bare
            # codestream here means a broken mid-write — and bare files get no
            # structural validation (a 2-byte stub would pass). Refuse.
            if header[0:2] == b'\xff\x0a':
                return False
            if header != b'\x00\x00\x00\x0cJXL \r\n\x87\n':
                return False
            # Walk the box chain: every box must be well-formed and the chain
            # must end exactly at EOF. A codestream box (jxlc/jxlp) must be
            # present — a metadata-only file must never pass the delete gate.
            file_size = stat.st_size
            i = 12
            has_codestream = False
            # Box walk re-opens the file only on the delete-gate path (JXL).
            with open(file_path, 'rb') as f:
                while i < file_size:
                    if i + 8 > file_size:
                        return False
                    f.seek(i)
                    box_header = f.read(8)
                    size = int.from_bytes(box_header[0:4], "big")
                    if box_header[4:8] in (b"jxlc", b"jxlp"):
                        has_codestream = True
                    if size == 0:
                        return has_codestream
                    if size == 1:
                        if i + 16 > file_size:
                            return False
                        size = int.from_bytes(f.read(8), "big")
                        if size < 16:
                            return False
                    elif size < 8:
                        return False
                    if i + size > file_size:
                        return False
                    i += size
            return has_codestream and i == file_size
        
        elif ext in ('.jpg', '.jpeg', '.jfif', '.jpe'):
            # SOI at the start AND an EOI (0xFFD9) near the end — a truncated
            # or short-written JPEG must never pass the delete gate. The EOI
            # does NOT have to be the last two bytes: jbrd bit-exact
            # reconstruction preserves trailing data after the EOI (Motion
            # Photos, appended thumbnails, scanner payloads), so search the
            # tail instead of requiring EOI at EOF.
            if header[0:2] != b'\xff\xd8':
                return False
            with open(file_path, 'rb') as f:
                f.seek(max(0, stat.st_size - 65536))
                return b'\xff\xd9' in f.read()

        elif ext == '.png':
            # PNG signature AND the IEND chunk closing the stream (search the
            # tail: PNGs with appended data are valid too).
            if header[0:8] != b'\x89PNG\r\n\x1a\n':
                return False
            if stat.st_size < 20:
                return False
            with open(file_path, 'rb') as f:
                f.seek(max(0, stat.st_size - 65536))
                return b'IEND' in f.read()

        elif ext in ('.tif', '.tiff'):
            # TIFF: signature, then force a real read of the last page's last
            # pixel — tifffile is lazy and a truncated file can pass a
            # header-only check.
            if header[0:2] not in (b'II', b'MM'):
                return False
            if header[2:4] not in (b'\x2a\x00', b'\x00\x2a'):
                return False
            try:
                import tifffile
                with tifffile.TiffFile(str(file_path)) as tif:
                    if len(tif.pages) == 0:
                        return False
                    last = tif.pages[-1].asarray()
                    _ = last.flat[-1]
                return True
            except Exception:
                return False
        
        # Unknown extension — refuse deletion. The old "conservative allow"
        # was inverted: an unverifiable output must keep its source.
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


def _replace_suffix_token(name: str, suffix_from: str, suffix_to: str) -> str:
    """Replace the FIRST occurrence of suffix_from in a folder name, but only
    when it is a complete token (bounded by _, -, space, or string edges) —
    otherwise 'MyJXLArchive' would become 'MyTIFFArchive'. No token match
    returns the name unchanged (caller applies the append fallback).
    """
    import re as _re
    pat = _re.compile(
        _re.escape(suffix_from) + r'(?=$|[_\- ])', _re.IGNORECASE)
    m = pat.search(name)
    if not m:
        return name
    left_ok = m.start() == 0 or name[m.start() - 1] in '_- '
    if not left_ok:
        return name
    return name[:m.start()] + suffix_to + name[m.end():]


def _marker_matches(part_lower: str, marker_lower: str) -> bool:
    """Folder-name part matches the export marker.

    Matches start/end with the marker (e.g. _EXPORT, _Export_2024, My_EXPORT)
    and, for underscore-wrapped markers, also the bare word — so the default
    '_EXPORT' also detects 'Export_Lightroom' and 'Lightroom_Export', as the
    documentation promises.
    """
    # startswith needs a token boundary after the marker, otherwise '_EXPORTS'
    # (a backup folder, different thing) would match the '_EXPORT' marker.
    # endswith is inherently safe: the marker's own leading underscore anchors it.
    if part_lower.startswith(marker_lower):
        rest = part_lower[len(marker_lower):]
        if not rest or rest[0] in '_- ':
            return True
    if part_lower.endswith(marker_lower):
        return True
    bare = marker_lower.strip('_')
    if not bare or bare == marker_lower:
        return False
    # The bare word must be a complete TOKEN at the START or END of the name
    # (bounded by _, -, space, or the string edges). This keeps the documented
    # cases (Export_Lightroom, Lightroom_Export, My_EXPORT) while rejecting
    # 'exports', 'EXPORTED_RAWS', 'reexport' — and mid-name tokens like
    # 'backup_export_old'.
    import re as _re
    if part_lower.startswith(bare):
        e = len(bare)
        if e == len(part_lower) or part_lower[e] in '_- ':
            return True
    if part_lower.endswith(bare):
        s = len(part_lower) - len(bare)
        if s == 0 or part_lower[s - 1] in '_- ':
            return True
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
# 1 = subfolder inside each source folder (converted_jxl/ or recovered_jpeg/)
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

CJXL_BUFFERING = None
# [libjxl >= 0.12 only] Encoder buffering level passed to cjxl on pixel-encode
# paths (lossy convert). None (default) = do not pass the flag (cjxl default 2
# is the fast path; 0 is ~1.2% smaller but ~6x slower on large images).
# See the detailed comment in jxl_tiff_encoder.py.
# Ignored automatically when cjxl is < 0.12 (flag doesn't exist there).

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

CODEC_TIMEOUT = 900
# Timeout (seconds) for each cjxl/djxl/magick invocation. 600s can be tight
# for very large images (45-100MP) with many workers competing for CPU/disk;
# a timeout becomes a per-file error (output cleaned up), never a hung batch.

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

# libjxl version detection - used to gate flags that only exist in newer
# cjxl/djxl builds. Unknown versions are treated as "old" (safe fallback:
# no new flags are ever appended).
@functools.lru_cache(maxsize=None)
def _tool_version(exe: str):
    """Return (major, minor, patch) of a cjxl/djxl-like tool, or None if unknown."""
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
        out = (r.stdout or "") + " " + (r.stderr or "")
    except Exception:
        return None
    m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(x) for x in m.groups()) if m else None

def _tool_at_least(exe: str, major: int, minor: int) -> bool:
    v = _tool_version(exe)
    return v is not None and v[:2] >= (major, minor)

def _cjxl_buffering_flag():
    """--buffering flag for cjxl >= 0.12; empty list otherwise (flag doesn't exist there)."""
    if CJXL_BUFFERING is not None and _tool_at_least("cjxl", 0, 12):
        return [f"--buffering={CJXL_BUFFERING}"]
    return []

def _abort_on_duplicate_outputs(pairs):
    """Abort the run if two outputs map to the same destination file.

    Modes 6/7 drop the first subfolder level under EXPORT_MARKER, so same-named
    files in different recipe subfolders would silently overwrite each other
    (and with mode 8 + delete, a single validated output could justify deleting
    several distinct sources). Better to stop loudly than to lose data.

    pairs: list of (source_path, dest_path) tuples.
    """
    from collections import Counter, defaultdict
    norm = {}
    by_dest = defaultdict(list)
    for src, dst in pairs:
        norm.setdefault(os.path.normcase(str(dst)), str(dst))
        by_dest[os.path.normcase(str(dst))].append(str(src))
    counts = Counter(os.path.normcase(str(out)) for _, out in pairs)
    dupes = sorted(norm[d] for d, c in counts.items() if c > 1)
    if dupes:
        for d in dupes[:10]:
            srcs = by_dest[os.path.normcase(d)]
            logger.error(f"Duplicate output destination: {d}")
            for s in srcs[:4]:
                logger.error(f"    <- from: {s}")
            if len(srcs) > 4:
                logger.error(f"    <- ... and {len(srcs) - 4} more source(s)")
        if len(dupes) > 10:
            logger.error(f"... and {len(dupes) - 10} more")
        logger.error("Aborting: multiple inputs map to the same output file. "
                     "Rename inputs, pick another mode/folder, or split the run to avoid silent overwrites.")
        sys.exit(2)

# Charset directives for exiftool argfiles:
# - FileName=UTF8: file paths in the argfile are UTF-8.
# - UTF8: tag VALUES read/written are UTF-8 (non-ASCII metadata round-trips
#   without codepage corruption).
_ARGFILE_CHARSET = "-charset\nFileName=UTF8\n-charset\nUTF8\n"


def _run_exiftool_argfile(args_lines, timeout=60):
    """Run exiftool with an argfile (UTF-8 + FileName charset).

    Using an argfile instead of raw argv avoids two Windows pitfalls:
    paths containing [ ] being treated as wildcards, and non-ASCII paths
    being decoded with the wrong codepage.
    """
    argfile = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         dir=TEMP_DIR, encoding="utf-8", newline="\n") as af:
            argfile = af.name
            af.write(_ARGFILE_CHARSET)
            af.write("\n".join(str(a) for a in args_lines))
            af.write("\n")
        return subprocess.run(
            [_get_exiftool_cmd(), "-@", argfile],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
        )
    finally:
        if argfile:
            try:
                os.unlink(argfile)
            except OSError:
                pass


def _copy_metadata(src_path: Path, dst_path: Path) -> None:
    """Best-effort copy of EXIF/XMP/IPTC metadata using exiftool.

    Used for lossy convert paths where cjxl/djxl may not preserve metadata.
    Failures are ignored so the conversion itself always succeeds.
    Also strips any 'ICC:<base64>' blob from XMP CreatorTool (written by the
    TIFF encoder for TIFF round-trips): it bloats delivered JPEG/PNGs and,
    after an ICC conversion, points at the wrong profile.
    """
    if shutil.which(_get_exiftool_cmd()) is None:
        return
    try:
        _run_exiftool_argfile(
            ["-overwrite_original", "-tagsfromfile", str(src_path),
             "-exif:all", "-xmp:all", "-iptc:all", str(dst_path)],
            timeout=120
        )
        # Strip ICC:<base64> segments from CreatorTool (same logic as the
        # TIFF decoder's cleanup_xmp_icc).
        try:
            r = _run_exiftool_argfile(
                ["-s", "-s", "-s", "-XMP-xmp:CreatorTool", str(dst_path)], timeout=30
            )
            if r.returncode == 0 and r.stdout and "ICC:" in r.stdout:
                content = r.stdout.strip()
                # Blob is bounded: base64 chars only up to the next pipe or EOL,
                # so a trailing " | Real App" segment is never eaten.
                clean = re.sub(r'ICC:[A-Za-z0-9+/=]+(?=\s*(\||$))', '', content, flags=re.MULTILINE).strip()
                if 'ICC:' in clean and '|' not in content:
                    # No pipe separators: the lookahead could not fire, but a
                    # long base64 blob is unambiguous (real words are never
                    # 64+ base64 chars).
                    clean = re.sub(r'ICC:[A-Za-z0-9+/=]{64,}', '', clean).strip()
                clean = re.sub(r'\s*\|\s*$', '', clean).strip()   # trailing pipe
                clean = re.sub(r'^\s*\|\s*', '', clean).strip()   # leading pipe
                clean = re.sub(r'\s*\|\s*\|\s*', ' | ', clean)    # doubled pipe
                # When the blob was the WHOLE CreatorTool (the common case:
                # encoder writes bare "ICC:<b64>" when the source had no
                # CreatorTool of its own), rewriting is still required —
                # otherwise the blob survives intact.
                if not clean:
                    clean = "jxl_jpeg_transcoder"
                _run_exiftool_argfile(
                    ["-overwrite_original",
                     f"-XMP-xmp:CreatorTool={clean}", str(dst_path)], timeout=60
                )
        except Exception:
            pass
    except Exception:
        pass

# --------------------------------------------─
# USER SETTINGS - TRANSCODE MODE CONFIGURATION
# --------------------------------------------─

# Mode 1 folders
CONVERTED_JXL_FOLDER = "converted_jxl"
RECOVERED_JPEG_FOLDER = "recovered_jpeg"

# Mode 5 (sibling)
JXL_SIBLING_FOLDER = "JXL_jpeg"
JPEG_SIBLING_FOLDER = "JPEG_recovered"

# Mode 4 (suffix replacement)
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
CONVERT_OUTPUT_SUFFIX = ""
# [Convert mode 2] Folder suffix used ONLY when the user explicitly opts in
# (non-empty here or via --output-suffix). Default "" = flat output to the
# input root, matching the transcode path and the README mode-2 tables.
# (A non-empty default used to split a single auto-mode run into two
# different layouts: transcode outputs flat, convert outputs in
# <folder>_converted/.)

# Container flag for lossy JXL encoding
# True = adds --container=1 for IrfanView EXIF compatibility
# Required for lossy (d>0) to allow exiftool to inject metadata
FORCE_CONTAINER_FOR_LOSSY = True

# --------------------------------------------─
# GLOBAL SETUP
# --------------------------------------------─

SCRIPT_DIR = Path(__file__).parent
LOG_DIR = SCRIPT_DIR / "Logs" / Path(__file__).stem
# Module-level logger (same pattern as the other scripts): resolvers must be
# safe to call before setup_logger() (tests, isolated imports).
logger = logging.getLogger("jxl_jpeg_transcoder")
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


_rejected_log_lock = threading.Lock()


def _log_rejected_file(file_path, reason):
    """Log rejected files to Logs/jxl_jpeg_transcoder/rejected_files.log for easy review."""
    try:
        rej_dir = SCRIPT_DIR / "Logs" / "jxl_jpeg_transcoder"
        rej_dir.mkdir(parents=True, exist_ok=True)
        rej_file = rej_dir / "rejected_files.log"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Locked: called from worker threads; concurrent appends would interleave.
        with _rejected_log_lock:
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
        return False
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
                # Do NOT rewrite the file with only the parsed boxes — that
                # would silently discard the rest (same rule as the encoder).
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

    # Final order: structure -> metadata -> codestream -> others
    ordered = meta_order_boxes + meta_extra_boxes + codestream_boxes + other_boxes

    # A box that declared size 0 ("extends to EOF") is only valid as the LAST
    # box in the file. If regrouping moved it earlier, rewrite its header with
    # the real computed size, otherwise everything after it becomes payload
    # and the file is corrupt.
    out = b""
    for idx, (name, h, p) in enumerate(ordered):
        declared = int.from_bytes(h[0:4], "big")
        if declared == 0 and idx < len(ordered) - 1:
            real_size = 8 + len(p)
            h = real_size.to_bytes(4, "big") + h[4:8]
        out += h + p
    jxl_path.write_bytes(out)

# --------------------------------------------─
# FILE FINDERS
# --------------------------------------------─

def find_jpegs_flat(input_path: Path):
    seen, files = set(), []
    for ext in ("*.jpg", "*.jpeg", "*.JPG", "*.JPEG", "*.jfif", "*.JFIF", "*.jpe", "*.JPE"):
        for f in input_path.glob(ext):
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                files.append(f)
    return sorted(files)

# Folder names created ONLY by this tool's DECODE direction (recovered JPEGs
# and generic convert outputs). ENCODE-direction scans (JPEG/PNG -> JXL) skip
# them, otherwise auto mode re-encodes its own recovered files on every rerun
# (ping-pong). JXL finders are deliberately NOT filtered: encoder-produced
# folders (converted_jxl/, 16B_JXL/, ...) are legitimate decode sources.
_TOOL_DECODE_OUTPUT_FOLDERS = frozenset({
    "recovered_jpeg", "jpeg_recovered", "converted",
})


def _is_tool_output_path(p: Path, root: Path) -> bool:
    """True if any folder component BETWEEN the scan root and the file is a
    known decode-output folder. Evaluated RELATIVE to the scan root, so
    pointing the tool directly AT a folder named 'converted'/'recovered_jpeg'
    (e.g. to re-archive recovered JPEGs) still works — only files NESTED
    inside such folders below the root are skipped.
    """
    try:
        rel = p.relative_to(root)
    except ValueError:
        rel = p
    return any(part.lower() in _TOOL_DECODE_OUTPUT_FOLDERS for part in rel.parts[:-1])


def find_jpegs_recursive(input_path: Path):
    seen, files = set(), []
    skipped = 0
    for ext in ("*.jpg", "*.jpeg", "*.JPG", "*.JPEG", "*.jfif", "*.JFIF", "*.jpe", "*.JPE"):
        for f in input_path.rglob(ext):
            if _is_tool_output_path(f, input_path):
                skipped += 1
                continue
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                files.append(f)
    if skipped:
        logger.info(f"Skipped {skipped} JPEG file(s) inside toolkit output folders "
                    f"({', '.join(sorted(_TOOL_DECODE_OUTPUT_FOLDERS))})")
    return sorted(files)

def find_jxls_flat(input_path: Path):
    seen, files = set(), []
    for ext in ("*.jxl", "*.JXL"):
        for f in input_path.glob(ext):
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                files.append(f)
    return sorted(files)

def find_jxls_recursive(input_path: Path):
    # Unfiltered on purpose: encoder/transcoder-produced JXL folders are
    # legitimate decode sources (the round-trip depends on finding them).
    seen, files = set(), []
    for ext in ("*.jxl", "*.JXL"):
        for f in input_path.rglob(ext):
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                files.append(f)
    return sorted(files)

def find_pngs_recursive(input_path: Path):
    seen, files = set(), []
    skipped = 0
    for ext in ("*.png", "*.PNG"):
        for f in input_path.rglob(ext):
            if _is_tool_output_path(f, input_path):
                skipped += 1
                continue
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                files.append(f)
    if skipped:
        logger.info(f"Skipped {skipped} PNG file(s) inside toolkit output folders")
    return sorted(files)

def find_pngs_flat(input_path: Path):
    seen, files = set(), []
    for ext in ("*.png", "*.PNG"):
        for f in input_path.glob(ext):
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                files.append(f)
    return sorted(files)

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
        # Forced transcode on a JXL means forced lossless DECODE (jbrd-gated
        # per file); on anything else it's a lossless encode.
        auto_decode = input_path.suffix.lower() == '.jxl'
        return ("transcode", auto_decode, "Forced transcode")
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

    def _warn_if_outside(result: Path) -> Path:
        # Modes 4/5 can land OUTSIDE the input tree for files at its root —
        # surface that (same warning as the TIFF encoder/decoder).
        if result is not None and not _is_relative_to(result, input_root):
            logger.warning(f"Output outside input tree: {src_path.name} -> {result}")
        return result

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
        # Folder suffix replacement (aligned with encoder/decoder mode 4)
        old_name = src_path.parent.name
        new_name = _replace_suffix_token(old_name, sfx_from, sfx_to)
        if new_name == old_name:
            new_name = old_name + "_" + sfx_to
            logger.warning(f"'{sfx_from}' not found as a token in '{old_name}', using '{new_name}'")
        return _warn_if_outside(src_path.parent.parent / new_name / src_path.with_suffix(out_ext).name)
    elif mode == 5:
        # Sibling folder (aligned with encoder/decoder mode 5)
        return _warn_if_outside(src_path.parent.parent / sibling_jxl / src_path.with_suffix(out_ext).name)
    elif mode in (6, 7):
        # Match only path *directory* parts (parts[:-1]); a file whose own
        # name happens to start/end with the marker is not an anchor.
        parts = src_path.parts
        marker_lower = EXPORT_MARKER.lower()
        # Match folders starting or ending with EXPORT_MARKER case-insensitively
        export_idx = next((i for i, p in enumerate(parts[:-1])
                           if _marker_matches(p.lower(), marker_lower)), None)
        if export_idx is None:
            # Files outside the export marker must be ignored in modes 6/7.
            return None
        export_dir = Path(*parts[:export_idx + 1])
        if mode == 6:
            # Mode 6: any file inside export marker folder
            rel_parts = src_path.relative_to(export_dir).parts
            if not rel_parts:
                return None  # The marker matched the filename itself
            rel = Path(*rel_parts[1:]) if len(rel_parts) > 1 else Path(rel_parts[0])
        else:
            if EXPORT_JPEG_SUBFOLDER:
                # Case-insensitive match on the subfolder component
                # (Path.relative_to is case-sensitive on Linux).
                rel_parts = src_path.relative_to(export_dir).parts
                if not rel_parts or rel_parts[0].lower() != EXPORT_JPEG_SUBFOLDER.lower():
                    return None
                rel = Path(*rel_parts[1:]) if len(rel_parts) > 1 else Path(src_path.name)
            else:
                rel_parts = src_path.relative_to(export_dir).parts
                if not rel_parts:
                    return None  # The marker matched the filename itself
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
            logger.info(f"[{n}/{total}] SKIP (up to date) | {src_path.name}")
        else:
            logger.info(f"[{n}/{total}] SKIP (exists) | {src_path.name}")
        return (str(src_path), "skipped", str(final_path), None)
    
    overwritten = final_path.exists()

    # Initialized BEFORE any statement that can raise (md5 read, mkdir, the
    # codec itself) so the except handler always has them (same pattern as
    # the decode functions).
    output_dirty = False
    _pre_identity = _capture_output_identity(write_path, final_path)

    try:
        write_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    try:
        src_md5 = md5_of_file(src_path) if STORE_MD5 else None

        # Determine if this is an encode (JPEG -> JXL) or decode (JXL -> JPEG)
        is_jpeg_encode = src_path.suffix.lower() in ('.jpg', '.jpeg', '.jfif', '.jpe')

        # From this point the tool writes to write_path; on failure the
        # partial output must be removed (see except below).
        if not is_jpeg_encode:
            # Unreachable by construction (encode_pairs only ever receives
            # JPEG-family extensions — the split in _process_file_group is by
            # extension). If it is ever reached, a plain djxl would re-encode
            # the JXL lossy with NO jbrd/MD5/integrity protection — refuse.
            raise RuntimeError(f"encode_one_transcode received a non-JPEG input: {src_path.name}")

        output_dirty = True
        r = subprocess.run(
            ["cjxl", str(src_path), str(write_path), "--lossless_jpeg=1",
             "--effort", str(effort)],
            capture_output=True, timeout=CODEC_TIMEOUT
        )
        if r.returncode != 0:
            raise RuntimeError(f"cjxl: {r.stderr.decode(errors='replace')[:200]}")

        reorder_jxl_boxes(write_path)

        # Validate EVERY successful output (a tool returning 0 does not
        # guarantee a well-formed file); only then record the checksum —
        # the MD5 db must never claim coverage of an invalid output.
        if not _verify_file_integrity(write_path):
            raise RuntimeError("tool returned 0 but the output failed the integrity check")

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
        # Remove any partial output produced by THIS run (identity-checked) so
        # the next run does not mistake it for a completed conversion — but
        # never a good pre-existing file the codec never touched.
        if output_dirty:
            _delete_partial_if_written(write_path, final_path, _pre_identity)
        n, total = next_count()
        logger.error(f"[{n}/{total}] ERROR | {src_path.name} | {e}")
        return (str(src_path), "error", str(e), None)

def decode_one_transcode(jxl_path: Path, write_path: Path, final_path: Path,
                         verify: bool, reconvert_val: bool, smart: bool) -> tuple:
    # Lossless JXL -> JPEG recovery (djxl, jbrd-gated below). Only .jxl inputs
    # ever reach this function — _process_file_group splits encode (JPEG ->
    # encode_one_transcode) from decode before submitting.
    is_jxl_decode = jxl_path.suffix.lower() == '.jxl'

    # Check if should process - pass both smart and reconvert_val
    if not should_process(jxl_path, final_path, smart, reconvert_val):
        n, total = next_count()
        if smart:
            logger.info(f"[{n}/{total}] SKIP (up to date) | {jxl_path.name}")
        else:
            logger.info(f"[{n}/{total}] SKIP (exists) | {jxl_path.name}")
        return (str(jxl_path), "skipped", str(final_path), None)

    # Force-transcode decode is documented as requiring a jbrd box for lossless
    # recovery. Without jbrd, djxl would re-encode lossy and silently label it as
    # lossless, risking data loss (especially with --delete-source). Reject these
    # files early and log them for review.
    overwritten = final_path.exists()

    # Initialized before ANY early raise (jbrd check, mkdir) so the except
    # handler never hits unbound variables.
    output_dirty = False
    _pre_identity = _capture_output_identity(write_path, final_path)

    try:
        if is_jxl_decode and not has_jbrd_box(jxl_path):
            _log_rejected_file(str(jxl_path), "force-transcode decode requires jbrd box")
            raise RuntimeError(
                f"{jxl_path.name}: force-transcode decode requires jbrd box. "
                "Use auto mode or --force-convert for lossy decode."
            )

        write_path.parent.mkdir(parents=True, exist_ok=True)

        stored_md5 = read_md5_db(jxl_path) if verify and is_jxl_decode else None

        djxl_cmd = ["djxl", str(jxl_path), str(write_path)]
        # libjxl >= 0.12: force lossless JPEG reconstruction and fail if impossible.
        # Mutually exclusive with --jpeg_quality / --pixels_to_jpeg (not used on this path).
        if is_jxl_decode and _tool_at_least("djxl", 0, 12):
            djxl_cmd.insert(1, "--reconstruct_jpeg")
        output_dirty = True
        r = subprocess.run(djxl_cmd, capture_output=True, timeout=CODEC_TIMEOUT)
        if r.returncode != 0:
            raise RuntimeError(f"djxl: {r.stderr.decode(errors='replace')[:200]}")

        # Validate the decoded output itself (rc=0 does not guarantee a
        # well-formed file). The MD5 comparison below is an even stronger
        # check, but only runs when a checksum was stored.
        if not _verify_file_integrity(write_path):
            raise RuntimeError("djxl returned 0 but the output failed the integrity check")

        n, total = next_count()

        # result[3] carries "md5 verified this run" for the delete gate:
        # the source may only be deleted without djxl>=0.12's authoritative
        # --reconstruct_jpeg when the MD5 comparison actually ran AND passed.
        md5_verified = None
        if verify:
            if stored_md5 is None:
                logger.warning(f"[{n}/{total}] OK (no MD5 stored) | {jxl_path.name}")
            else:
                recovered_md5 = md5_of_file(write_path)
                if recovered_md5 == stored_md5:
                    md5_verified = True
                    logger.info(f"[{n}/{total}] OK [MD5 PASS] | {jxl_path.name}")
                else:
                    # Verification failed: delete the bad output so a re-run is
                    # not skipped by should_process seeing the file exists.
                    try:
                        if write_path.exists():
                            write_path.unlink()
                    except OSError:
                        pass
                    logger.error(f"[{n}/{total}] MD5 FAIL (output deleted) | {jxl_path.name}")
                    return (str(jxl_path), "md5_fail", str(final_path), None)
        else:
            logger.info(f"[{n}/{total}] OK | {jxl_path.name} -> {write_path.name}")

        status = "reconvert" if overwritten else "ok"
        return (str(jxl_path), status, str(final_path), md5_verified)
    except Exception as e:
        # Remove any partial output produced by THIS run (identity-checked) so
        # the next run does not mistake it for a completed conversion — but
        # never a good pre-existing file the codec never touched.
        if output_dirty:
            _delete_partial_if_written(write_path, final_path, _pre_identity)
        n, total = next_count()
        logger.error(f"[{n}/{total}] ERROR | {jxl_path.name} | {e}")
        return (str(jxl_path), "error", str(e), None)

def _capture_output_identity(write_path: Path, final_path: Path):
    """Capture (mtime_ns, size) of a pre-existing output (non-staging only).
    Returns None for staging paths or nonexistent outputs."""
    if write_path != final_path or not final_path.exists():
        return None
    try:
        st = final_path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _delete_partial_if_written(write_path: Path, final_path: Path, pre_identity) -> None:
    """Delete write_path ONLY if this run actually wrote to it.

    - Staging (write != final): always this run's file -> delete.
    - No pre-existing identity: anything there is this run's partial -> delete.
    - Identity changed: this run truncated/rewrote it -> delete.
    - Identity UNCHANGED: this run never touched it (e.g. the codec failed at
    startup with rc!=0) -> the good pre-existing file is KEPT.
    """
    try:
        if write_path != final_path or pre_identity is None:
            if write_path.exists():
                write_path.unlink()
            return
        st = write_path.stat()
        if (st.st_mtime_ns, st.st_size) != pre_identity:
            write_path.unlink()
    except OSError:
        pass


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
            task = futures[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                # An exception escaped the worker entirely — one bad file must
                # not kill the whole batch.
                n, total = next_count()
                logger.error(f"[{n}/{total}] ERROR | {task[0].name} | {e}")
                results.append((str(task[0]), "error", str(e), None))

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
            try:
                final_out.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(write_out), str(final_out))
                moved += 1
            except OSError as e:
                # A locked/readonly destination must not abort the whole batch:
                # keep the file in staging and log it for manual recovery.
                logger.error(f"  MOVE FAILED, kept in staging | {write_out.name} -> {final_out} | {e}")

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
                try:
                    staging_db.unlink()
                except OSError:
                    pass

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
            if not decode:
                # Encode direction: structural validity is not enough — the
                # JXL must also carry jbrd, otherwise the original JPEG can
                # never be recovered bit-exactly (README's promise). This
                # check is INDEPENDENT of MD5 storage (--no-md5 must not
                # weaken it).
                if final_file.suffix.lower() == '.jxl' and not has_jbrd_box(final_file):
                    logger.warning(f" KEEP (output has no jbrd box; JPEG not recoverable) | {src_path.name}")
                    continue
            if decode and not _tool_at_least("djxl", 0, 12):
                # djxl < 0.12 has no --reconstruct_jpeg, so bit-exact recovery
                # is NOT guaranteed by the tool. The source may only be
                # deleted when the MD5 comparison ran AND passed THIS run
                # (result[3]). --no-verify skips that comparison entirely —
                # without it, deletion would rest on the structural SOI/EOI
                # check alone: too weak for an irreversible gate.
                if not result[3]:
                    logger.warning(f" KEEP (djxl<0.12 and recovery not MD5-verified) | {src_path.name}")
                    continue
            if not _verify_file_integrity(final_file):
                logger.warning(f" KEEP (output failed integrity check) | {src_path.name}")
                continue
            try:
                src_path.unlink()
                deleted += 1
                logger.info(f" DELETED source | {src_path.name}")
            except OSError as e:
                # PermissionError is common on Windows (AV, Explorer preview,
                # open viewer) — warn and continue instead of killing the batch.
                logger.warning(f" KEEP (could not delete source) | {src_path.name}: {e}")
        if deleted:
            logger.info(f" -> Deleted {deleted} source file(s)")

    return results

def cmd_transcode(args, auto_decode: bool = False):
    """Returns (errors, cancelled): error count and whether the user declined
    the delete confirmation."""
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

    # A stale staging checksums.md5 from a crashed previous run would leak
    # wrong entries into this run's destination folders — start clean.
    if TEMP2_DIR is not None:
        try:
            stale = Path(TEMP2_DIR) / CHECKSUMS_FILENAME
            if stale.exists():
                stale.unlink()
        except OSError:
            pass

    # Determine direction
    decode = args.decode or auto_decode

    log_file = setup_logger()

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
        files = find_jxls_flat(args.input) if args.mode in (0, 1) else find_jxls_recursive(args.input)
        output_root = args.output or args.input
    else:
        files = find_jpegs_flat(args.input) if args.mode in (0, 1) else find_jpegs_recursive(args.input)
        output_root = args.output or args.input

    if not files:
        logger.warning("No input files found.")
        return (0, False)

    logger.info(f"Files found: {len(files)}")

    # Build pairs
    pairs = []
    for f in files:
        out = resolve_output_transcode(f, args.mode, output_root, decode)
        if out is None:
            continue  # Skip files outside _EXPORT for modes 6/7
        pairs.append((f, out))

    # Progress total must reflect modes 6/7 filtering, not the raw scan
    _counter["total"] = len(pairs)
    if len(pairs) != len(files):
        logger.info(f"Planned: {len(pairs)} (filtered by mode)")

    _abort_on_duplicate_outputs(pairs)

    if args.dry_run:
        for f, out in pairs:
            logger.info(f" DRY | {f.name} -> {out}")
        logger.info(f"Dry run: {len(pairs)} files would be processed.")
        return (0, False)

    # Create the mode-2 output dir only for real runs (dry-run must not write)
    if args.mode == 2 and not args.input.is_file():
        output_root.mkdir(parents=True, exist_ok=True)

    # Group by output folder
    groups = {}
    for f, out in pairs:
        groups.setdefault(out.parent, []).append((f, out))

    if args.mode == 8 and DELETE_SOURCE:
        if DELETE_CONFIRM:
            # Transcode is lossless in both directions (decode requires the jbrd
            # box, checked per file in decode_one_transcode), so the simple
            # 'yes' confirmation applies — the HHMM lossy confirmation is only
            # for lossy converts (cmd_convert/cmd_auto).
            if not confirm_deletion_jpeg():
                logger.info("Deletion not confirmed -- exiting.")
                return (0, True)

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
    return (err, False)

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
        # Subfolder inside each source folder (aligned with transcode mode 1).
        # A non-default --output-name still wins for explicit overrides.
        folder = output_name if output_name != CONVERT_OUTPUT_FOLDER else conv_folder
        return src_path.parent / folder / f"{stem}.{ext}"
    elif mode == 2:
        if output_root:
            return Path(output_root) / f"{stem}.{ext}"
        if suffix:
            # Opt-in: per-folder sibling "<folder><suffix>/"
            new_folder = src_path.parent.name + suffix
            return src_path.parent.parent / new_folder / f"{stem}.{ext}"
        # Default: flat into the input root (same layout as the transcode path)
        return src_path.parent / f"{stem}.{ext}"
    elif mode == 3:
        # Subfolder inside each source folder, recursive variant (aligned with
        # transcode mode 3). Previously this flattened every recursively-found
        # file into a single <input>/converted/ folder, causing cross-folder
        # name collisions.
        folder = output_name if output_name != CONVERT_OUTPUT_FOLDER else conv_folder
        return src_path.parent / folder / f"{stem}.{ext}"
    elif mode == 4:
        # Folder suffix replacement (aligned with encoder/decoder mode 4)
        old_name = src_path.parent.name
        new_name = _replace_suffix_token(old_name, sfx_from, sfx_to)
        if new_name == old_name:
            new_name = old_name + "_" + sfx_to
            logger.warning(f"'{sfx_from}' not found as a token in '{old_name}', using '{new_name}'")
        result = src_path.parent.parent / new_name / f"{stem}.{ext}"
        if output_root and not _is_relative_to(result, Path(output_root)):
            logger.warning(f"Output outside input tree: {src_path.name} -> {result}")
        return result
    elif mode == 5:
        # Sibling folder (e.g., JXL_jpeg/ or JPEG_recovered/) — aligned with
        # encoder/decoder mode 5
        result = src_path.parent.parent / sibling_folder / f"{stem}.{ext}"
        if output_root and not _is_relative_to(result, Path(output_root)):
            logger.warning(f"Output outside input tree: {src_path.name} -> {result}")
        return result
    elif mode in (6, 7):
        # Export marker modes - only process files INSIDE export marker folder
        # Match only path *directory* parts (parts[:-1]); a file whose own
        # name happens to start/end with the marker is not an anchor.
        parts = src_path.parts
        marker_lower = EXPORT_MARKER.lower()
        # Match folders starting or ending with EXPORT_MARKER case-insensitively
        export_idx = next((i for i, p in enumerate(parts[:-1])
                           if _marker_matches(p.lower(), marker_lower)), None)
        if export_idx is None:
            return None  # Skip files outside export marker folder
        export_dir = Path(*parts[:export_idx + 1])
        if mode == 6:
            # Mode 6: any file inside export marker folder
            rel_parts = src_path.relative_to(export_dir).parts
            if not rel_parts:
                return None  # The marker matched the filename itself
            rel = Path(*rel_parts[1:]) if len(rel_parts) > 1 else Path(rel_parts[0])
        else:
            # Mode 7: only files inside export marker / EXPORT_JPEG_SUBFOLDER
            if EXPORT_JPEG_SUBFOLDER:
                # Case-insensitive match on the subfolder component
                # (Path.relative_to is case-sensitive on Linux; the encoder/
                # decoder resolvers use the same rule).
                rel_parts = src_path.relative_to(export_dir).parts
                if not rel_parts or rel_parts[0].lower() != EXPORT_JPEG_SUBFOLDER.lower():
                    return None  # Not in the specific subfolder
                rel = Path(*rel_parts[1:]) if len(rel_parts) > 1 else Path(src_path.name)
            else:
                rel_parts = src_path.relative_to(export_dir).parts
                if not rel_parts:
                    return None  # The marker matched the filename itself
                rel = Path(*rel_parts[1:]) if len(rel_parts) > 1 else Path(rel_parts[0])
        # The renamed stem (rename_from/rename_to, computed at the top of this
        # function) must replace the filename component of rel — otherwise
        # modes 6/7 silently ignore the rename.
        rel = rel.parent / f"{stem}{rel.suffix}"
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
            logger.info(f"[{n}/{total}] SKIP (up to date) | {src_path.name}")
        else:
            logger.info(f"[{n}/{total}] SKIP (exists) | {src_path.name}")
        return (str(src_path), "skipped", str(final_path), None)
    
    overwritten = final_path.exists()

    # Initialized BEFORE any statement that can raise (mkdir, the codec
    # itself) so the except handler always has them.
    output_dirty = False
    _pre_identity = _capture_output_identity(write_path, final_path)

    try:
        write_path.parent.mkdir(parents=True, exist_ok=True)

        # Build cjxl command
        cmd = ["cjxl", str(src_path), str(write_path), "--effort", str(effort), "-d", str(distance)]
        is_jpeg_input = src_path.suffix.lower() in ('.jpg', '.jpeg', '.jfif', '.jpe')
        # --lossless_jpeg=0 only for d>0 (convert = pixel re-encode). At d=0
        # the cjxl default (lossless_jpeg=1) yields a CONTAINER with jbrd —
        # which the integrity gate requires, and which makes a d=0 "convert"
        # behave as a lossless transcode (noted in the log).
        if distance > 0:
            cmd.append("--lossless_jpeg=0")
        elif is_jpeg_input:
            logger.debug("d=0 on the convert path behaves as lossless transcode (jbrd kept)")

        # Add container flag for metadata support (needed for EXIF in IrfanView).
        # Lossy only (d>0) for JPEG inputs: on lossless it changes how the ICC
        # is stored and breaks color display in IrfanView (same rule as the
        # TIFF encoder), and cjxl --lossless_jpeg=1 already yields a container.
        # NON-JPEG inputs (PNG...) ALWAYS need it: at d=0 cjxl writes a bare
        # codestream that exiftool cannot inject metadata into — and the
        # integrity gate (container required) would reject our own output.
        is_jpeg_input = src_path.suffix.lower() in ('.jpg', '.jpeg', '.jfif', '.jpe')
        if not is_jpeg_input or (FORCE_CONTAINER_FOR_LOSSY and distance > 0):
            cmd.append("--container=1")

        # [libjxl >= 0.12] optional --buffering (off by default; see CJXL_BUFFERING)
        cmd += _cjxl_buffering_flag()

        output_dirty = True
        r = subprocess.run(cmd, capture_output=True, timeout=CODEC_TIMEOUT)
        if r.returncode != 0:
            raise RuntimeError(f"cjxl: {r.stderr.decode(errors='replace')[:200]}")

        # Preserve EXIF/XMP/IPTC metadata that cjxl may drop in lossy mode
        _copy_metadata(src_path, write_path)

        # Reorder boxes for IrfanView compatibility — must be the LAST mutation,
        # otherwise exiftool re-appends metadata boxes after the codestream.
        reorder_jxl_boxes(write_path)

        # Validate EVERY successful output (rc=0 does not guarantee a
        # well-formed file).
        if not _verify_file_integrity(write_path):
            raise RuntimeError("cjxl returned 0 but the output failed the integrity check")

        n, total = next_count()
        label = "RECONVERT" if overwritten else "OK"
        logger.info(f"[{n}/{total}] {label} | {src_path.name} -> {write_path.name}")
        return (str(src_path), "reconvert" if overwritten else "ok", str(final_path), None)
    except Exception as e:
        # Remove any partial output produced by THIS run (identity-checked) so
        # the next run does not mistake it for a completed conversion — but
        # never a good pre-existing file the codec never touched.
        if output_dirty:
            _delete_partial_if_written(write_path, final_path, _pre_identity)
        n, total = next_count()
        logger.error(f"[{n}/{total}] ERROR | {src_path.name} | {e}")
        return (str(src_path), "error", str(e), None)

_srgb_icc_cache = None
_srgb_icc_lock = threading.Lock()


def _get_srgb_icc_path() -> Optional[str]:
    """Return the path of a real sRGB ICC profile generated via Pillow's
    LittleCMS, or None if unavailable.

    Used for --to-srgb: `magick -colorspace sRGB` is a mathematical color-model
    conversion, not an ICC-managed transform — for wide-gamut sources
    (ProPhoto/AdobeRGB) it reinterprets more than it converts. `-profile` with
    a real sRGB ICC performs the proper gamut mapping.

    The profile is written once to a STABLE path (no per-process temp leak)
    and created under a lock (workers may race here).
    """
    global _srgb_icc_cache
    if _srgb_icc_cache is not None:
        return _srgb_icc_cache
    with _srgb_icc_lock:
        if _srgb_icc_cache is not None:  # re-check after acquiring the lock
            return _srgb_icc_cache
        try:
            from PIL import ImageCms
            icc_path = Path(tempfile.gettempdir()) / "jxl_photo_sRGB.icc" if TEMP_DIR is None else Path(TEMP_DIR) / "jxl_photo_sRGB.icc"
            if not icc_path.exists():
                profile_bytes = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
                icc_path.write_bytes(profile_bytes)
            _srgb_icc_cache = str(icc_path)
        except Exception:
            _srgb_icc_cache = False  # do not retry every file
    return _srgb_icc_cache or None


def _magick_icc_args(output_icc: str, extra: list, tmp_dir: Path = None) -> list:
    """Build the ImageMagick color args for an output ICC.

    File path -> `-profile <file>`. The 'sRGB' built-in alias prefers a real
    sRGB profile via `-profile` (proper ICC transform); falls back to
    `-colorspace sRGB` if Pillow is unavailable.
    """
    if output_icc == "sRGB":
        icc_path = _get_srgb_icc_path()
        if icc_path:
            return ["-profile", icc_path] + extra
        logger.debug("Pillow/ImageCms unavailable for sRGB profile; using -colorspace fallback")
        return ["-colorspace", "sRGB"] + extra
    return ["-profile", output_icc] + extra


def decode_to_image(jxl_path: Path, write_path: Path, final_path: Path,
                    quality: int, fmt: str, bit_depth: int,
                    output_icc: str, use_ram: bool, reconvert_val: bool, smart: bool) -> tuple:
    """Convert JXL to JPEG or PNG."""
    # Use should_process for consistent logic
    if not should_process(jxl_path, final_path, smart, reconvert_val):
        n, total = next_count()
        if smart:
            logger.info(f"[{n}/{total}] SKIP (up to date) | {jxl_path.name}")
        else:
            logger.info(f"[{n}/{total}] SKIP (exists) | {jxl_path.name}")
        return (str(jxl_path), "skipped", str(final_path), None)
    
    overwritten = final_path.exists()
    # Initialized before the try so the except handler can never hit an
    # unbound variable (e.g. if mkdir itself raises).
    output_dirty = False
    actual_out = write_path
    _pre_identity = _capture_output_identity(write_path, final_path)

    try:
        write_path.parent.mkdir(parents=True, exist_ok=True)

        is_png = (fmt == "png")
        # NOTE: the JPEG+16-bit -> PNG switch happens in the CALLERS
        # (cmd_convert / _process_file_group) before output pairs are built,
        # so staging and final paths always agree. A runtime switch here would
        # make should_process consult the wrong extension (dead code, removed).

        if output_icc and not MAGICK_AVAILABLE:
            # Fail loudly: silently keeping the embedded ICC would deliver
            # files in the wrong color space while the log says "converting".
            # (Before output_dirty is set: a pre-existing output is NOT touched.)
            raise RuntimeError(
                "ICC conversion requested but ImageMagick (magick) is not in PATH. "
                "Install ImageMagick or drop --icc-profile/--to-srgb.")

        # From here on, a tool writes to actual_out; on failure the partial
        # output must be removed (see except below).
        output_dirty = True

        if is_png:
            # PNG output
            if output_icc and MAGICK_AVAILABLE:
                # Color conversion: real ICC profile via -profile when possible
                # (proper ICC transform, not a color-model reinterpretation).
                magick_output = _magick_icc_args(output_icc, ["-depth", str(bit_depth)])
                logger.debug(f"Using ICC conversion: {magick_output[:2]}")
                # djxl does not support --output_format; write a temporary PNG (format by
                # extension) and then convert with ImageMagick. Same as the --no-ram path.
                # capture_output keeps worker logs clean and preserves stderr for errors.
                with tempfile.TemporaryDirectory(dir=TEMP_DIR) as tmp:
                    tmp_png = Path(tmp) / "tmp.png"
                    try:
                        subprocess.run(["djxl", str(jxl_path), str(tmp_png)], check=True, capture_output=True, timeout=CODEC_TIMEOUT)
                        subprocess.run(["magick", str(tmp_png)] + magick_output + [str(actual_out)], check=True, capture_output=True, timeout=CODEC_TIMEOUT)
                    except subprocess.CalledProcessError as cpe:
                        err = (cpe.stderr or b"").decode(errors="replace")[:200] if isinstance(cpe.stderr, bytes) else str(cpe.stderr or "")[:200]
                        raise RuntimeError(f"{cpe.cmd[0]}: {err}") from cpe
            else:
                # Direct djxl to PNG
                r = subprocess.run(["djxl", str(jxl_path), str(actual_out), f"--bits_per_sample={bit_depth}"], capture_output=True, timeout=CODEC_TIMEOUT)
                if r.returncode != 0:
                    raise RuntimeError(f"djxl: {r.stderr.decode(errors='replace')[:200]}")
        else:
            # JPEG output via djxl directly (no magick needed unless ICC conversion)
            if output_icc and MAGICK_AVAILABLE:
                # Color conversion: real ICC profile via -profile when possible
                # (proper ICC transform, not a color-model reinterpretation).
                magick_output = _magick_icc_args(output_icc, ["-quality", str(quality)])
                logger.debug(f"Using ICC conversion: {magick_output[:2]}")
                # djxl does not support --output_format; decode to a temporary PNG (format by
                # extension) and let ImageMagick convert to the final JPEG.
                with tempfile.TemporaryDirectory(dir=TEMP_DIR) as tmp:
                    tmp_png = Path(tmp) / "tmp.png"
                    try:
                        subprocess.run(["djxl", str(jxl_path), str(tmp_png)], check=True, capture_output=True, timeout=CODEC_TIMEOUT)
                        subprocess.run(["magick", str(tmp_png)] + magick_output + [str(actual_out)], check=True, capture_output=True, timeout=CODEC_TIMEOUT)
                    except subprocess.CalledProcessError as cpe:
                        err = (cpe.stderr or b"").decode(errors="replace")[:200] if isinstance(cpe.stderr, bytes) else str(cpe.stderr or "")[:200]
                        raise RuntimeError(f"{cpe.cmd[0]}: {err}") from cpe
            else:
                # Direct djxl to JPG (preserves embedded ICC)
                quality_flag = f"--jpeg_quality={quality}"
                r = subprocess.run(["djxl", quality_flag, str(jxl_path), str(actual_out)], capture_output=True, timeout=CODEC_TIMEOUT)
                if r.returncode != 0:
                    raise RuntimeError(f"djxl: {r.stderr.decode(errors='replace')[:200]}")

        # Preserve EXIF/XMP/IPTC metadata that djxl/ImageMagick may drop
        _copy_metadata(jxl_path, actual_out)

        # Validate EVERY successful output (rc=0 does not guarantee a
        # well-formed file).
        if not _verify_file_integrity(actual_out):
            raise RuntimeError("decode returned 0 but the output failed the integrity check")

        n, total = next_count()
        label = "RECONVERT" if overwritten else "OK"
        logger.info(f"[{n}/{total}] {label} | {jxl_path.name} -> {actual_out.name}")
        return (str(jxl_path), "reconvert" if overwritten else "ok", str(final_path), None)

    except Exception as e:
        # Remove any partial output produced by THIS run (identity-checked),
        # including an alternate-extension orphan from the bit_depth fallback —
        # but never a good pre-existing file the codec never touched.
        if output_dirty:
            for candidate in {actual_out, write_path}:
                _delete_partial_if_written(
                    candidate, final_path if candidate == actual_out else write_path,
                    _pre_identity if candidate == actual_out else None)
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
            task = futures[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                # An exception escaped the worker entirely — one bad file must
                # not kill the whole batch.
                n, total = next_count()
                logger.error(f"[{n}/{total}] ERROR | {task[0].name} | {e}")
                results.append((str(task[0]), "error", str(e), None))

    if use_staging:
        moved = 0
        status_map = {r[0]: r[1] for r in results}
        for src, write_out, final_out in tasks:
            status = status_map.get(str(src), "error")
            if status in ("ok", "reconvert", "overwrite") and write_out.exists():
                try:
                    final_out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(write_out), str(final_out))
                    moved += 1
                except OSError as e:
                    # A locked/readonly destination must not abort the batch:
                    # keep the file in staging and log it for manual recovery.
                    logger.error(f"  MOVE FAILED, kept in staging | {write_out.name} -> {final_out} | {e}")
            elif write_out.exists():
                # Failed conversion: do not promote a partial/corrupt file to the
                # final destination. Leave it in staging so the user can inspect.
                logger.warning(f"Staging: not promoting {write_out.name} (status={status})")
        if moved:
            logger.info(f" -> Moved {moved} file(s) from staging to destination")

    return results

def cmd_convert(args, from_jxl: bool = True):
    """Returns (errors, cancelled)."""
    global _counter, TEMP2_DIR, DELETE_SOURCE
    _counter = {"done": 0, "total": 0}

    # NOTE: the --icc-profile guards live AFTER direction auto-detection
    # below (a --force-convert on a JXL folder flips from_jxl at file
    # collection; validating here would check the wrong direction).

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
        # Leave args.bit_depth as None: it is irrelevant for JXL output, and a
        # later direction fallback (JXL folder detected) must still apply the
        # per-format defaults (PNG 16-bit / JPEG 8-bit).


    # Collect files (ICC label, op_type, and mode_str are set after this)
    if args.input.is_file():
        files = [args.input]
        output_root = args.output or args.input.parent
    elif direction == "to_jxl":
        jpegs = find_jpegs_flat(args.input) if args.mode in (0, 1) else find_jpegs_recursive(args.input)
        pngs = find_pngs_flat(args.input) if args.mode in (0, 1) else find_pngs_recursive(args.input)
        # --from-jpeg: restrict to JPEG inputs only. The wrapper's
        # "JPEG -> JXL lossy" workflow uses this so PNGs in the folder are
        # never converted (or deleted in mode 8) behind the user's back.
        if getattr(args, "from_jpeg", False):
            if pngs:
                logger.info(f"--from-jpeg: ignoring {len(pngs)} PNG file(s)")
            pngs = []
        files = jpegs + pngs
        # If no JPEGs/PNGs found, fall back to JXLs (auto-detect direction).
        # Suppressed under --from-jpeg (the direction was explicit).
        if not files and not getattr(args, "from_jpeg", False):
            jxls = find_jxls_flat(args.input) if args.mode in (0, 1) else find_jxls_recursive(args.input)
            if jxls:
                files = jxls
                direction = "from_jxl"
                if args.format is None:
                    args.format = "jpeg"
                # Same per-format defaults as the normal from_jxl branch:
                # PNG keeps 16-bit, JPEG is 8-bit (JPEG has no 16-bit).
                if args.bit_depth is None:
                    args.bit_depth = PNG_DEFAULT_BIT_DEPTH if args.format == "png" else 8
                logger.debug("Auto-detected JXL content, switching to from_jxl")
        elif direction == "to_jxl":
            # Mixed folder with --force-convert: JXLs are silently ignored
            # without this note (use auto mode for mixed content instead).
            jxls_present = find_jxls_flat(args.input) if args.mode in (0, 1) else find_jxls_recursive(args.input)
            if jxls_present:
                logger.warning(f"{len(jxls_present)} JXL file(s) present but ignored "
                               f"(JPEG/PNG -> JXL direction). Use auto mode for mixed folders.")
        output_root = args.output or args.input
    else:
        files = find_jxls_flat(args.input) if args.mode in (0, 1) else find_jxls_recursive(args.input)
        output_root = args.output or args.input

    if not files:
        logger.warning("No input files found.")
        return (0, False)

    # --icc-profile guards, AFTER direction auto-detection is final.
    # Decode + ICC conversion without ImageMagick would silently produce
    # unconverted files — hard fail. Encode + ICC has no conversion step —
    # warn instead of silently ignoring.
    if direction == "from_jxl" and args.icc_profile and not MAGICK_AVAILABLE:
        logger.error("--icc-profile/--to-srgb requires ImageMagick (magick) in PATH.")
        sys.exit(1)
    if direction == "to_jxl" and args.icc_profile:
        logger.warning("--icc-profile/--to-srgb is ignored for JPEG/PNG -> JXL encodes "
                       "(no ICC-conversion step on that pipeline).")

    # JPEG does not support 16-bit. Switch format to PNG before building output
    # pairs so that staging files and final paths use the correct extension.
    if direction == "from_jxl" and args.format == "jpeg" and args.bit_depth == 16:
        logger.warning("JPEG output does not support 16-bit; switching to PNG")
        args.format = "png"

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

    _bd_label = str(args.bit_depth) if direction == "from_jxl" else "n/a"
    logger.info(f"{op_type} | Mode: {args.mode} | "
                f"Format: {args.format} | Quality: {args.quality} | "
                f"Bit depth: {_bd_label} | ICC: {icc_label} | "
                f"RAM: {args.ram} | delete_source={DELETE_SOURCE} | {mode_str} | "
                f"Staging: {TEMP2_DIR or 'disabled'} | Workers: {args.workers}")
    if args.rename_from:
        logger.info(f"Filename rename: '{args.rename_from}' -> '{args.rename_to}'")
    logger.info(f"Input: {args.input}")
    logger.info(f"Files found: {len(files)}")

    # Build pairs
    pairs = []
    for f in files:
        # Mode 2: explicit output dir -> flat into it. Otherwise: opt-in
        # --output-suffix -> <folder><suffix>/ sibling folders; default ->
        # flat into the input root (same layout as the transcode path).
        # For a FILE input the "root" is its parent folder.
        _suffix = args.output_suffix if args.output_suffix is not None else (CONVERT_OUTPUT_SUFFIX or None)
        _input_root = args.input.parent if args.input.is_file() else args.input
        resolve_root = (args.output or (None if _suffix else _input_root)) if args.mode == 2 else output_root
        _eff_suffix = args.output_suffix if args.output_suffix is not None else (CONVERT_OUTPUT_SUFFIX or "")
        if direction == "to_jxl":
            out = resolve_output_convert(f, args.mode, args.output_name,
                                         _eff_suffix, "jxl",
                                         args.rename_from, args.rename_to,
                                         resolve_root, decode=False)
        else:
            # Default to jpg if format is somehow None, else use specified
            fmt = args.format if args.format else "jpeg"
            ext = "jpg" if fmt == "jpeg" else "png"
            out = resolve_output_convert(f, args.mode, args.output_name,
                                         _eff_suffix, ext,
                                         args.rename_from, args.rename_to,
                                         resolve_root, decode=True)
        if out is None:
            continue  # Skip files outside _EXPORT for modes 6/7
        pairs.append((f, out))

    # Progress total must reflect modes 6/7 filtering, not the raw scan
    _counter["total"] = len(pairs)
    if len(pairs) != len(files):
        logger.info(f"Planned: {len(pairs)} (filtered by mode)")

    _abort_on_duplicate_outputs(pairs)

    if args.dry_run:
        for f, out in pairs:
            logger.info(f" DRY | {f.name} -> {out}")
        logger.info(f"Dry run: {len(pairs)} files would be converted.")
        return (0, False)

    # Create the mode-2 output dir only for real runs (dry-run must not write)
    if args.mode == 2 and not args.input.is_file():
        output_root.mkdir(parents=True, exist_ok=True)

    groups = {}
    for f, out in pairs:
        groups.setdefault(out.parent, []).append((f, out))

    logger.info(f"Output groups: {len(groups)}")

    # Safety confirmation for Mode 8 + DELETE_SOURCE
    # Determine if operation is lossy based on direction and settings
    if args.mode == 8 and DELETE_SOURCE:
        if DELETE_CONFIRM:
            if direction == "to_jxl":
                # PNG/JPEG -> JXL: lossy if distance > 0
                is_lossy = args.distance > 0
            else:
                # JXL -> JPEG/PNG: the transcode path (lossless recovery) is a
                # different command; everything on the CONVERT path is a
                # re-encode that can never reproduce the original JXL, so it
                # gets the strict HHMM token — same as cmd_auto's rule.
                is_lossy = True

            if is_lossy:
                if not confirm_deletion_lossy():
                    logger.info("Deletion not confirmed -- exiting.")
                    return (0, True)
            else:
                if not confirm_deletion_jpeg():
                    logger.info("Deletion not confirmed -- exiting.")
                    return (0, True)

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
                try:
                    src_path.unlink()
                    deleted += 1
                    logger.info(f" DELETED source | {src_path.name}")
                except OSError as e:
                    logger.warning(f" KEEP (could not delete source) | {src_path.name}: {e}")
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
    return (err, False)

# --------------------------------------------─
# AUTO MODE (Per-file detection for directories)
# --------------------------------------------─

def cmd_auto(args):
    """Auto-detect per-file for batch processing.

    For directories containing JPEG and/or JXL files:
    - JPEG files         -> lossless transcode encode to JXL
    - JXL files WITH jbrd box -> lossless transcode decode to JPEG
    - JXL files WITHOUT jbrd  -> lossy convert

    Returns (errors, cancelled).
    """
    global _counter, TEMP2_DIR, DELETE_SOURCE, STORE_MD5
    _counter = {"done": 0, "total": 0}

    if args.icc_profile and not MAGICK_AVAILABLE:
        # Same guard as cmd_convert: without ImageMagick the ICC conversion
        # would be silently skipped and outputs would keep the embedded ICC.
        print("ERROR: --icc-profile/--to-srgb requires ImageMagick (magick) in PATH.")
        sys.exit(1)

    TEMP2_DIR = args.staging

    # Handle --no-md5 (was silently ignored on this path)
    if args.no_md5:
        STORE_MD5 = False

    # Handle DELETE_SOURCE from CLI (same as cmd_transcode/cmd_convert)
    if args.delete_source:
        DELETE_SOURCE = True

    # A stale staging checksums.md5 from a crashed previous run would leak
    # wrong entries into this run's destination folders — start clean.
    if TEMP2_DIR is not None:
        try:
            stale = Path(TEMP2_DIR) / CHECKSUMS_FILENAME
            if stale.exists():
                stale.unlink()
        except OSError:
            pass

    log_file = setup_logger()
    
    # Collect JPEG files (encode direction), PNG files (convert encode direction)
    # and JXL files (decode/convert direction). Modes 0 and 1 are flat.
    if args.mode in (0, 1):
        jpeg_files = find_jpegs_flat(args.input)
        png_files = find_pngs_flat(args.input)
        jxl_files = find_jxls_flat(args.input)
    else:
        jpeg_files = find_jpegs_recursive(args.input)
        png_files = find_pngs_recursive(args.input)
        jxl_files = find_jxls_recursive(args.input)

    # --from-jxl: restrict auto mode to the JXL decode direction. JPEG/PNG
    # inputs are left untouched (used by the wrapper's "JXL -> JPEG auto"
    # workflow, which must never create new JXLs from folder JPEGs).
    if getattr(args, "from_jxl", False):
        if jpeg_files or png_files:
            logger.info(f"--from-jxl: ignoring {len(jpeg_files)} JPEG and {len(png_files)} PNG file(s)")
        jpeg_files = []
        png_files = []

    if not jpeg_files and not png_files and not jxl_files:
        logger.warning("No JPEG, PNG or JXL files found.")
        return (0, False)

    # Separate JXL files by jbrd presence
    jxl_transcode_files = []  # Have jbrd - can decode losslessly to JPEG
    jxl_convert_files = []    # No jbrd - must do lossy convert

    for f in jxl_files:
        if has_jbrd_box(f):
            jxl_transcode_files.append(f)
        else:
            jxl_convert_files.append(f)

    total_files = len(jpeg_files) + len(png_files) + len(jxl_transcode_files) + len(jxl_convert_files)
    _counter["total"] = total_files

    # Used by the output-vs-input collision check below (would-write test)
    smart_mode = args.sync
    reconvert_explicit = args.overwrite

    logger.info(f"AUTO MODE | Directory: {args.input}")
    logger.info(f"JPEG files (lossless encode): {len(jpeg_files)}")
    logger.info(f"PNG files (convert encode): {len(png_files)}")
    logger.info(f"JXL with jbrd (lossless decode): {len(jxl_transcode_files)}")
    logger.info(f"JXL without jbrd (lossy): {len(jxl_convert_files)}")
    logger.info(f"Mode: {args.mode} | Workers: {args.workers} | Staging: {TEMP2_DIR or 'disabled'}")

    # JPEG does not support 16-bit. Switch format to PNG before any group is
    # processed so that staging files and final paths use the correct extension.
    if args.format == "jpeg" and args.bit_depth == 16:
        logger.warning("JPEG output does not support 16-bit; switching to PNG")
        args.format = "png"

    # Cross-group duplicate detection: the groups below are processed in separate
    # calls, so per-group checks cannot see collisions across them (e.g.
    # photo.jpg + photo.png both -> converted_jxl/photo.jxl). The per-group
    # pair lists are kept so empty groups (everything filtered by mode 6/7)
    # never print a "Processing N" header.
    groups_plan = [
        ("JPEG", jpeg_files, dict(use_transcode=True)),
        ("PNG", png_files, dict(use_transcode=False, direction="to_jxl")),
        ("JXL-jbrd", jxl_transcode_files, dict(use_transcode=True)),
        ("JXL-lossy", jxl_convert_files, dict(use_transcode=False)),
    ]
    planned = {}
    all_pairs = []
    for key, files, kw in groups_plan:
        lst = []
        _process_file_group(files, args, collect_only=lst, **kw)
        planned[key] = lst
        all_pairs.extend(lst)
        # Groups where the mode filter rejected everything never reach the
        # discount in _process_file_group — discount them here so the
        # progress denominator matches reality.
        if not lst and files:
            _counter["total"] = max(0, _counter.get("total", 0) - len(files))
    _abort_on_duplicate_outputs(all_pairs)

    # Output-vs-input collision: e.g. photo.jpg (encode) + photo.jxl (decode) in
    # the same folder. Encoding first would overwrite the decode source before
    # it is ever read — refuse loudly instead of silently losing the original.
    # BUT: if EVERY colliding pair would be skipped anyway (outputs exist and
    # are up to date — i.e. a re-run of a completed batch), it is not a
    # collision at all: log and continue, keeping auto mode idempotent.
    import os as _os
    inputs_norm = set()
    for f in jpeg_files + png_files + jxl_transcode_files + jxl_convert_files:
        inputs_norm.add(_os.path.normcase(str(f)))
    collisions = []
    for src, out in all_pairs:
        if _os.path.normcase(str(out)) in inputs_norm and _os.path.normcase(str(out)) != _os.path.normcase(str(src)):
            # Would this pair actually WRITE? (same rule as should_process:
            # smart mode writes only when the source is newer)
            if should_process(src, out, smart_mode, reconvert_explicit):
                collisions.append((src, out))
    if collisions:
        for src, out in collisions[:10]:
            logger.error(f"Output would overwrite another input: {src.name} -> {out.name}")
        if len(collisions) > 10:
            logger.error(f"... and {len(collisions) - 10} more")
        logger.error("Aborting: an output path equals another file that must be processed. "
                     "Rename files or use a different mode/folder.")
        sys.exit(2)

    # Confirm source deletion BEFORE any processing — but AFTER the collision
    # checks above, so a doomed run never asks for the token in vain.
    # Lossy conversion requires stricter confirmation than lossless transcode.
    # Only meaningful in mode 8. Skipped on dry runs (nothing is converted, so
    # nothing would be deleted) and when DELETE_CONFIRM is off.
    if args.mode == 8 and DELETE_SOURCE and not args.dry_run and DELETE_CONFIRM:
        # Lossiness from the PLANNED pairs (post mode-6/7 filter), not the raw
        # lists — otherwise a fully filtered-out group still asks for the token.
        has_lossy = bool(planned["JXL-lossy"]) or bool(planned["PNG"])
        has_lossless = bool(planned["JPEG"]) or bool(planned["JXL-jbrd"])
        if has_lossy:
            if not confirm_deletion_lossy():
                logger.info("Deletion not confirmed -- exiting.")
                return (0, True)
        elif has_lossless:
            if not confirm_deletion_jpeg():
                logger.info("Deletion not confirmed -- exiting.")
                return (0, True)

    # Decode groups run BEFORE encode groups, so a same-stem pair can never
    # have its JXL source overwritten before decoding (extra belt on top of
    # the collision guard above).
    totals = {"ok": 0, "err": 0, "skipped": 0}

    def _merge(tally):
        for k in totals:
            totals[k] += tally.get(k, 0)

    # Process JXL transcode files (lossless decode to JPEG) — only when the
    # mode filter left something to do (otherwise the header lies).
    if planned["JXL-jbrd"]:
        logger.info(f"\n--- Processing {len(planned['JXL-jbrd'])} JXL files with jbrd (lossless) ---")
        _merge(_process_file_group(jxl_transcode_files, args, use_transcode=True))

    # Process JXL convert files (lossy)
    if planned["JXL-lossy"]:
        logger.info(f"\n--- Processing {len(planned['JXL-lossy'])} JXL files without jbrd (lossy) ---")
        _merge(_process_file_group(jxl_convert_files, args, use_transcode=False))

    # Process JPEG files (lossless encode to JXL)
    if planned["JPEG"]:
        logger.info(f"\n--- Processing {len(planned['JPEG'])} JPEG files (lossless encode) ---")
        _merge(_process_file_group(jpeg_files, args, use_transcode=True))

    # Process PNG files (convert encode to JXL; no lossless transcode for PNG)
    if planned["PNG"]:
        logger.info(f"\n--- Processing {len(planned['PNG'])} PNG files (convert encode) ---")
        _merge(_process_file_group(png_files, args, use_transcode=False, direction="to_jxl"))

    logger.info(f"\n{'-'*50}")
    logger.info(f"AUTO MODE complete | Total: {total_files} files | "
                f"{totals['ok']} OK | {totals['skipped']} skipped | {totals['err']} errors")
    logger.info(f"Log: {log_file}")
    return (totals["err"], False)

def _process_file_group(files, args, use_transcode=True, direction="from_jxl", collect_only=None):
    """Process a group of files with the same method.
    use_transcode=True: lossless JPEG<->JXL (direction per extension).
    use_transcode=False: convert; direction='from_jxl' (decode to image) or
    'to_jxl' (encode image to JXL, e.g. PNG inputs in auto mode).
    collect_only: when a list is given, only build output pairs into it and
    return (pre-pass for cross-group duplicate detection)."""
    # Use explicit output directory if provided, otherwise fall back to input
    # root. Mode 2 convert is the exception: it needs the RAW args.output
    # (possibly None) so the suffix-folder branch in resolve_output_convert
    # can fire (see cmd_convert). For a FILE input the "root" is its parent.
    output_root = args.output if args.output is not None else args.input
    _suffix = args.output_suffix if args.output_suffix is not None else (CONVERT_OUTPUT_SUFFIX or None)
    _input_root = args.input.parent if args.input.is_file() else args.input
    resolve_root = (args.output or (None if _suffix else _input_root)) if args.mode == 2 else output_root

    # Build output pairs
    pairs = []
    default_depth = 8  # safe default; refined below for lossy convert output
    if not use_transcode:
        if direction == "to_jxl":
            out_ext = "jxl"
        else:
            # Lossy convert: output format defaults to JPEG if not specified.
            # Compute once (loop-invariant).
            fmt_eff = args.format or "jpeg"
            # Default bit depth per format, matching cmd_convert behavior
            default_depth = 8 if fmt_eff in ("jpeg", "jpg") else PNG_DEFAULT_BIT_DEPTH
            bit_depth_eff = args.bit_depth or default_depth
            # JPEG does not support 16-bit: switch to PNG *here* so staging files
            # and final paths agree (same fix as cmd_convert #124). Otherwise
            # decode_to_image switches extension at runtime and the staged file is
            # orphaned (never promoted to the destination).
            if fmt_eff in ("jpeg", "jpg") and bit_depth_eff == 16:
                if collect_only is None:
                    logger.warning("JPEG output does not support 16-bit; switching to PNG")
                out_ext = "png"
            else:
                out_ext = "jpg" if fmt_eff in ("jpeg", "jpg") else "png"
    for f in files:
        if use_transcode:
            # Lossless transcode: direction depends on input extension
            is_jpeg_input = f.suffix.lower() in ('.jpg', '.jpeg', '.jfif', '.jpe')
            out = resolve_output_transcode(f, args.mode, output_root, decode=not is_jpeg_input)
        else:
            out = resolve_output_convert(
                f, args.mode, args.output_name,
                args.output_suffix if args.output_suffix is not None else (CONVERT_OUTPUT_SUFFIX or ""),
                out_ext, args.rename_from, args.rename_to,
                resolve_root, decode=(direction == "from_jxl")
            )
        if out:
            pairs.append((f, out))

    if collect_only is not None:
        # Pre-pass: only collect pairs for cross-group duplicate detection
        collect_only.extend(pairs)
        return {"ok": 0, "err": 0, "skipped": 0}

    # Discount files filtered out by modes 6/7 from the progress total
    _counter["total"] = max(0, _counter.get("total", 0) - (len(files) - len(pairs)))

    _abort_on_duplicate_outputs(pairs)

    if args.dry_run:
        for f, out in pairs:
            logger.info(f" DRY | {f.name} -> {out}")
        return {"ok": 0, "err": 0, "skipped": 0}

    if not pairs:
        return {"ok": 0, "err": 0, "skipped": 0}

    tally = {"ok": 0, "err": 0, "skipped": 0}

    def _accumulate(results):
        for r in results:
            st = r[1]
            if st in ("ok", "reconvert", "overwrite"):
                tally["ok"] += 1
            elif st == "skipped":
                tally["skipped"] += 1
            else:
                tally["err"] += 1

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
                _accumulate(process_group_transcode(
                    encode_pairs, args.workers, decode=False,
                    verify=not args.no_verify, mode=args.mode,
                    reconvert_val=args.overwrite, smart=args.sync, effort=args.effort
                ))
            if decode_pairs:
                _accumulate(process_group_transcode(
                    decode_pairs, args.workers, decode=True,
                    verify=not args.no_verify, mode=args.mode,
                    reconvert_val=args.overwrite, smart=args.sync, effort=args.effort
                ))
        else:
            results = process_group_convert(
                group_pairs, args.workers, direction=direction,
                quality=args.quality, distance=args.distance,
                fmt=args.format or "jpeg",
                bit_depth=args.bit_depth or (default_depth if direction == "from_jxl" else 8),
                output_icc=args.icc_profile, use_ram=args.ram,
                effort=args.effort, reconvert_val=args.overwrite,
                use_internal_srgb=False, smart=args.sync
            )
            _accumulate(results)
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
                    try:
                        src_path.unlink()
                        deleted += 1
                        logger.info(f" DELETED source | {src_path.name}")
                    except OSError as e:
                        logger.warning(f" KEEP (could not delete source) | {src_path.name}: {e}")
                if deleted:
                    logger.info(f" -> Deleted {deleted} source file(s)")

    return tally

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
    parser.add_argument("--delete-confirm-off", action="store_true",
                        help="Skip the interactive delete confirmation. For automation/"
                             "wrappers that already asked the user.")
    parser.add_argument("--export-subfolder", type=str, default=None,
                        help="[Mode 7] Only process files inside this subfolder of the "
                             "export marker (default: empty = all subfolders).")
    parser.add_argument("--from-jxl", action="store_true",
                        help="[Auto mode] Restrict processing to .jxl files only "
                             "(JPEG/PNG files in the folder are left untouched).")
    parser.add_argument("--from-jpeg", action="store_true",
                        help="[Convert mode] Restrict JPEG->JXL conversion to JPEG "
                             "files only (PNGs in the folder are left untouched).")
    parser.add_argument("--effort", type=int, default=CJXL_EFFORT, choices=range(1, 11),
                        help="cjxl effort 1-10")

    # Convert specific
    parser.add_argument("--ram", action="store_true", default=True, help="Accepted for CLI compatibility; decode currently always uses temporary files (no in-RAM pipeline yet)")
    parser.add_argument("--no-ram", dest="ram", action="store_false", help="Use disk")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen")
    parser.add_argument("--output-name", type=str, default=CONVERT_OUTPUT_FOLDER,
                        help="Output folder name override for convert modes 1 and 3")
    parser.add_argument("output", nargs="?", type=Path, default=None,
                        help="Output directory (mode 0 single file)")
    parser.add_argument("--output-suffix", type=str, default=None,
                        help="[Convert mode 2] Opt-in: put outputs in <folder><suffix>/ "
                             "sibling folders instead of flat into the input root "
                             "(default: flat, matching the transcode path)")
    parser.add_argument("--rename-from", type=str, default="", help="Rename pattern")
    parser.add_argument("--rename-to", type=str, default="", help="Rename replacement")

    # Export marker (must match wrapper's configured marker)
    parser.add_argument("--export-marker", type=str, default=None,
                        help="Folder name marker for modes 6/7 (default: script setting EXPORT_MARKER)")

    # Force override
    parser.add_argument("--force-transcode", action="store_true",
                        help="Force transcode command")
    parser.add_argument("--force-convert", action="store_true",
                        help="Force convert command")

    args = parser.parse_args()

    if not args.input.exists():
        print(f"ERROR: input path does not exist: {args.input}")
        sys.exit(1)

    if args.workers < 1:
        print("ERROR: --workers must be >= 1")
        sys.exit(1)

    if args.staging is not None:
        try:
            Path(args.staging).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"ERROR: staging directory is not usable: {args.staging} ({e})")
            sys.exit(1)

    # --decode only makes sense for JXL inputs; reject it for other single
    # files instead of silently misrouting (e.g. djxl on a PNG).
    if args.decode and args.input.is_file() and args.input.suffix.lower() != '.jxl':
        print(f"ERROR: --decode requires a .jxl input (got {args.input.name})")
        sys.exit(1)

    # Normalize format: jpg -> jpeg
    if args.format == "jpg":
        args.format = "jpeg"

    # Apply configurable export marker before resolving outputs
    global EXPORT_MARKER, EXPORT_JPEG_SUBFOLDER, DELETE_CONFIRM
    if args.export_marker:
        EXPORT_MARKER = args.export_marker
    if args.export_subfolder is not None:
        EXPORT_JPEG_SUBFOLDER = args.export_subfolder
    if args.delete_confirm_off:
        DELETE_CONFIRM = False

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

    # Route to appropriate command. Each cmd_* returns a (errors, cancelled)
    # tuple so automation/wrappers can detect failures and user cancellations.
    if cmd == "transcode":
        errors, cancelled = cmd_transcode(args, auto_decode)
    elif cmd == "convert":
        # Determine direction for convert
        if args.input.suffix.lower() == '.jxl' or args.decode:
            errors, cancelled = cmd_convert(args, from_jxl=True)
        else:
            errors, cancelled = cmd_convert(args, from_jxl=False)
    elif cmd == "auto":
        # Auto-detect per-file for directories. --decode forces the lossless
        # recovery direction: JXL-only, jbrd-gated (non-jbrd files error out
        # per file instead of being silently lossy-converted).
        if args.decode:
            errors, cancelled = cmd_transcode(args, auto_decode=True)
        else:
            errors, cancelled = cmd_auto(args)
    else:
        # Fallback - should not reach here
        print(f"ERROR: Unknown command state: {cmd}")
        sys.exit(1)

    if cancelled:
        sys.exit(3)
    if errors:
        sys.exit(1)

if __name__ == "__main__":
    main()