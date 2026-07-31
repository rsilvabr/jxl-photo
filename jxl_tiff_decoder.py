#!/usr/bin/env python3
"""
jxl_tiff_decoder.py — Batch JPEG XL → TIFF 16-bit converter with ICC preservation

Features:
- JPEG preview includes ICC profile (correct colors in Windows Explorer)
- TIFF structure: 16-bit as page 0 (primary), JPEG preview as page 1 (thumbnail flag)
- Windows Explorer shows color-managed thumbnails
- File integrity verification before source deletion (v1.3+)

Usage:
 py jxl_tiff_decoder.py input/ [--mode 0-8] [--workers N] [--overwrite] [--sync]
 py jxl_tiff_decoder.py photo.jxl --mode 1

Requirements:
 pip install tifffile numpy Pillow
 djxl (libjxl) → https://github.com/libjxl/libjxl/releases
 exiftool → https://exiftool.org
"""

import subprocess, os, tempfile, threading, logging, sys, shutil, re, base64, struct, uuid, io, json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import argparse
import numpy as np
from PIL import Image
# PIL_MAX_IMAGE_PIXELS (user setting, below) is applied to Image.MAX_IMAGE_PIXELS
# right after its definition — not here.
try:
    from PIL import ImageCms
except ImportError:
    ImageCms = None
import tifffile

logger = logging.getLogger(__name__)

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


# --- Disk-full abort ------------------------------------------------------
# Duplicated across the backend scripts on purpose (see AGENTS.md): each stays
# standalone. Fix bugs in ALL copies.
#
# A staging drive is usually a small, cheap SSD nobody watches, and it holds a
# whole destination folder's output until that folder's last file lands -- for
# a flat run (one destination) that is the ENTIRE batch. When it fills, cjxl and
# djxl still exit 0 while writing truncated files, the integrity check rejects
# each one, and the run grinds on: one error per remaining file, thousands of
# identical lines, none of them naming the disk. Latch the first one instead and
# let the queued work fall straight through.
_MIN_FREE_BYTES = 64 * 1024 * 1024
_abort_lock = threading.Lock()
_abort_reason = None


def _reset_abort():
    """Clear the latch. Called when a run starts (and by the tests)."""
    global _abort_reason
    with _abort_lock:
        _abort_reason = None


def _aborted():
    """The reason the run gave up, or None while it is healthy."""
    return _abort_reason


def _signal_abort(reason):
    """Latch the FIRST reason and announce it once.

    Racing workers all fail within milliseconds of each other, so the latch has
    to be first-wins: the earliest failure is the one that explains the run.
    """
    global _abort_reason
    with _abort_lock:
        if _abort_reason is not None:
            return
        _abort_reason = reason
    logger.error(f"ABORTING RUN: {reason}")
    logger.error("  Queued files were NOT attempted and nothing was deleted. "
                 "Free space, then re-run: sync mode resumes where this stopped.")


def _abort_if_disk_full(write_dir, needed):
    """Latch an abort when `write_dir` can no longer take a `needed`-byte file.

    Only ever called from a failure path, so a healthy run never pays for the
    stat. A volume that cannot be queried returns False: "cannot tell" must
    never be reported to the user as "disk full".
    """
    try:
        free = shutil.disk_usage(write_dir).free
    except OSError:
        return False
    required = max(int(needed or 0), _MIN_FREE_BYTES)
    if free >= required:
        return False
    _signal_abort(f"no space left on {write_dir} "
                  f"({free // (1024 * 1024)} MB free, "
                  f"needs at least {required // (1024 * 1024)} MB)")
    return True


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


def _argfile_safe(value) -> str:
    """Sanitize a value for inclusion in an exiftool argfile (-@).

    Argfiles are parsed one argument per line, so embedded newlines in XMP
    text would split one argument into several bogus ones.
    """
    return re.sub(r"[\r\n]+", " ", str(value))


# Charset directives for exiftool argfiles:
# - FileName=UTF8: file paths in the argfile are UTF-8 (Windows default is the
#   system codepage, so non-ASCII paths would not be found).
# - UTF8: tag VALUES read/written are UTF-8, so non-ASCII metadata (captions,
#   CreatorTool, Relation items) round-trips without codepage corruption.
_ARGFILE_CHARSET = "-charset\nFileName=UTF8\n-charset\nUTF8\n"


def _run_exiftool_argfile(args_lines, timeout=60):
    """Run exiftool with an argfile (UTF-8 + FileName charset).

    Using an argfile instead of raw argv avoids two Windows pitfalls:
    paths containing [ ] being treated as wildcards, and non-ASCII paths
    being decoded with the wrong codepage.
    Returns the CompletedProcess.
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


def _check_external_tools(dry_run: bool = False) -> None:
    """Fail fast when a required external program is missing.

    Without this the batch dies file by file with a cryptic FileNotFoundError —
    or, worse, succeeds in a degraded way: with no exiftool the multi-page
    markers cannot be read at all, every page decodes as a STANDALONE single
    page TIFF, and `--mode 8 --delete-source` then removes the JXLs that held
    those markers. The pixels survive, the multi-page structure does not.
    """
    missing = [name for name in ("djxl", "exiftool")
               if shutil.which(_get_exiftool_cmd() if name == "exiftool" else name) is None]
    if not missing:
        return
    if dry_run:
        logger.warning(f"Missing external tool(s): {', '.join(missing)} "
                       f"(dry run continues, but a real run would fail)")
        return
    for name in missing:
        if name == "djxl":
            logger.error("djxl not found in PATH. Install libjxl and add djxl to PATH.")
        else:
            logger.error("exiftool not found in PATH. It is REQUIRED: without it the "
                         "multi-page markers cannot be read and every page would decode "
                         "as a separate single-page TIFF. https://exiftool.org")
    sys.exit(1)


def _warn_if_libjxl_too_old(exe: str = "djxl") -> None:
    """libjxl < 0.11 is not supported (see README). Say so once, up front:
    the failures it produces otherwise are cryptic and affect every file."""
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
        out = (r.stdout or "") + " " + (r.stderr or "")
    except Exception:
        return
    m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", out)
    if not m:
        return
    ver = tuple(int(x) for x in m.groups())
    if ver[:2] < (0, 11):
        logger.warning(
            f"{exe} {'.'.join(map(str, ver))} is older than the supported minimum "
            f"(0.11.2). Conversions may fail in ways that are hard to diagnose — "
            f"upgrade libjxl: https://github.com/libjxl/libjxl/releases")


def _verify_tiff_integrity(tiff_path: Path) -> bool:
    """Verify TIFF file integrity before deleting source JXL.

    Checks:
    1. File exists and size > 0
    2. Valid TIFF signature (II* or MM*)
    3. Can be opened by tifffile
    """
    if not tiff_path.exists():
        return False

    try:
        stat = tiff_path.stat()
        if stat.st_size == 0:
            return False

        # Check TIFF signature (first 4 bytes)
        with open(tiff_path, 'rb') as f:
            header = f.read(4)

        if len(header) < 4:
            return False

        # TIFF signature: II (little-endian) or MM (big-endian) followed by 42 (0x002A)
        if header[0:2] not in (b'II', b'MM'):
            return False
        if header[2:4] not in (b'\x2a\x00', b'\x00\x2a'):
            return False

        # Try to open with tifffile to verify structure
        with tifffile.TiffFile(str(tiff_path)) as tif:
            if len(tif.pages) == 0:
                return False
            # Force a REAL read of the last page's last pixel — tifffile is
            # lazy, so a truncated TIFF can pass a header-only open while its
            # pixel data is missing. Only the last strip/tile is decoded.
            last = tif.pages[-1].asarray()
            _ = last.flat[-1]

        return True
    except Exception:
        # OSError/IOError/tifffile.TiffFileError and any parser error on
        # malformed data (struct.error, ValueError) all mean "do not delete
        # the source".
        return False


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

JPEG_PREVIEW_SIZE = 256
# Maximum dimension (width or height) of the JPEG preview.
# Default: 256 pixels (similar to Capture One's ~160px).

THUMBNAIL_HANDLING = "include"
# How to handle JXL files with a _thumbnail suffix when reconstructing multi-page TIFFs.
# "ignore"   → Ignore _thumbnail.jxl files; the reconstructed TIFF will contain only
#              real pages.
# "include"  → Include _thumbnail.jxl pages in the reconstructed TIFF (default).
# "generate" → [NOT YET IMPLEMENTED] Generate a thumbnail from page 0 if no
#              _thumbnail.jxl exists. Currently shows a warning/fallback message.

THUMBNAIL_SUFFIX = "_thumbnail"
# Suffix used to identify thumbnail JXLs produced by the encoder.
# Must match the encoder's THUMBNAIL_SUFFIX setting.

RECONSTRUCT_MULTIPAGE = True
# When True, JXLs carrying the encoder's multi-page marker are rejoined into a
# single multi-page TIFF. When False, every JXL decodes to its own TIFF. Only
# marked files are ever merged, so independently-named files are safe either way;
# this flag exists to fully disable reconstruction if desired (--no-reconstruct-multipage).

DEPTH_POLICY = "preserve_thumbnails"
# Bit depth policy per page. "force16" always outputs 16-bit. "preserve_thumbnails"
# keeps real pages at 16-bit but restores 8-bit thumbnails if the original was 8-bit
# (default). "preserve_original" keeps each page's original bit depth. Pages without
# a jxlphoto-depth marker fall back to 16-bit.

MULTIPAGE_MARKER_PREFIX = "jxlphoto-mpg:"
# Must match the encoder's MULTIPAGE_XMP_MARKER. Stored in XMP-dc:Relation (a bag/list).

ICC_INHERITED_FLAG = "jxlphoto-icc:inherited"
# Must match the encoder's ICC_INHERITED_XMP_FLAG. Indicates that a page inherited
# its effective ICC from IFD0; the reconstructed TIFF should not write an ICC tag on
# that page, matching the original structure.

SUBFILETYPE_PREFIX = "jxlphoto-subfiletype:"
# Must match the encoder's SUBFILETYPE_XMP_PREFIX. Carries the original SubfileType
# value for non-standard page types (e.g. 4 for transparency/IR masks).

GRAYSCALE_FLAG = "jxlphoto-grayscale"
# Must match the encoder's GRAYSCALE_XMP_FLAG. Indicates the page was encoded as
# single-channel grayscale and should be restored as a 2D TIFF page.

DEPTH_FLAG = "jxlphoto-depth:"
# Must match the encoder's DEPTH_XMP_PREFIX. Carries the original BitsPerSample
# value (8 or 16) for the page so the decoder can honor --depth-policy.

PAGE_PREFIX = "jxlphoto-page:"
# Must match the encoder's PAGE_XMP_PREFIX. Carries the TIFF page index of the
# split page so reconstruction does not depend on the filename (which breaks
# for sources named *_page<N> or *_thumbnail).

THUMB_FLAG = "jxlphoto-thumb"
# Must match the encoder's THUMB_XMP_FLAG. Marks thumbnail pages of a split
# without relying on the _thumbnail filename suffix.

TEMP_DIR = None
# Temporary directory for intermediate files.
# None → use system temp
# Ex: → r"E:\\temp_jxl"

DJXL_TIMEOUT = 900
# Timeout (seconds) for each djxl invocation. 600s can be tight for very large
# JXLs (45-100MP) with many workers competing for CPU/disk; a timeout becomes
# a per-file error (output cleaned up), never a hung batch.

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

PIL_MAX_IMAGE_PIXELS = None
# PIL's decompression bomb protection limit (prevents DOS attacks with malicious images).
# None  -> Disable the limit completely (recommended for trusted local files/panoramas)
# N     -> Maximum number of pixels (e.g., 500_000_000 for ~500MP limit)
Image.MAX_IMAGE_PIXELS = PIL_MAX_IMAGE_PIXELS

# ICC Color Management
CLEANUP_XMP_ICC_MARKER = True
# Remove ICC:base64 marker from XMP CreatorTool after extraction

USE_MATRIX_MODE = False
# Use Matrix decode mode (linear + LittleCMS color transform)

FORCE_NONE_MODE = False
# Force None mode (no ICC handling at all)

FORCE_BASIC_MODE = False
# Force Basic mode (use ICC from JXL if available, no XMP handling)

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
# Mode 6: processes ALL JXLs inside EXPORT_MARKER recursively (ignores JXLs outside)
# Mode 7: only processes JXLs inside a specific subfolder of EXPORT_MARKER
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
logger = logging.getLogger("jxl_decode")
counter_lock = threading.Lock()
_counter = {"done": 0, "total": 0}

def setup_logger():
    global logger
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"{timestamp}.log"

    logger = logging.getLogger("jxl_decode")
    logger.setLevel(logging.INFO)

    # Remove old handlers so a second call in the same process (tests,
    # wrapper-driven runs) does not duplicate log lines.
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    # Ensure stdout writes UTF-8 to prevent Mojibake on Windows console
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ch = logging.StreamHandler(sys.stdout)

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.info(f"Log saved to: {log_file}")
    return log_file


# Machine-readable run summary for the jxl_photo.py wrapper.
#
# A manifest run spawns one child PER ENTRY, each writing its own log file, so
# the wrapper had no way to total a multi-entry run: the user saw only the last
# entry's "Done:" line and had to open N logs to find out whether anything
# failed. The wrapper consumes this line and does NOT print it.
#
# Gated behind --summary-json so a direct human run never sees the JSON. Keep
# the key names stable: jxl_photo.py parses them.
SUMMARY_PREFIX = "##JXLSUM## "
SUMMARY_MAX_FAILURES = 200


def emit_summary_json(enabled, *, ok, overwritten, skipped, errors, log_file,
                      extras=None, failures=None, dry_run=False):
    """Print one JSON line the wrapper can aggregate. No-op without the flag.

    `extras` is a plain label -> count map so the wrapper can render
    script-specific stats without knowing what they mean. `failures` is a list
    of (file, reason) pairs. `dry_run` marks counts as "would have".
    """
    if not enabled:
        return
    failures = failures or []
    payload = {
        "script": Path(__file__).stem,
        "ok": ok,
        "overwritten": overwritten,
        "skipped": skipped,
        "errors": errors,
        "dry_run": dry_run,
        "extras": {k: v for k, v in (extras or {}).items() if v},
        "failures": [{"file": f, "reason": r} for f, r in failures[:SUMMARY_MAX_FAILURES]],
        "failures_truncated": len(failures) > SUMMARY_MAX_FAILURES,
        "log": str(log_file),
    }
    try:
        # Straight to stdout, not through the logger: the timestamp prefix and
        # the log file copy would both be noise.
        print(SUMMARY_PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)
    except Exception:
        pass  # a summary line must never take down a finished run


def next_count():
    with counter_lock:
        _counter["done"] += 1
        return _counter["done"], _counter["total"]

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
    counts = Counter(os.path.normcase(str(d)) for _, d in pairs)
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
        logger.error("  Hint: marker-anchored modes (6/7) drop ONE folder level under the marker, "
                     "so nested marker folders (X/X/photo.tif and X/photo.tif) and same-named files "
                     "in sibling recipe folders both collapse onto the same .jxl name.")
        sys.exit(2)

def confirm_deletion_jxl():
    """Interactive confirmation before deleting source JXLs"""
    from datetime import datetime as _dt
    print("\n\n")
    print(" [!] WARNING -- DELETE_SOURCE is enabled")
    print(" Source JXLs will be deleted after successful decode.")
    print(" This deletion is IRREVERSIBLE.")
    now = _dt.now()
    token = now.strftime("%H%M")
    print(f" Current time: {now.strftime('%H:%M')} > to confirm, type: {token}")
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
        r = _run_exiftool_argfile(
            ["-b", "-XMP-xmp:CreatorTool", str(jxl_path)], timeout=30
        )
        if r.returncode != 0 or not r.stdout:
            return None

        # CreatorTool may contain multiple tokens separated by '|'. Find the one
        # that carries the ICC payload and validate it before returning.
        for segment in r.stdout.split("|"):
            segment = segment.strip()
            if not segment.startswith("ICC:"):
                continue
            b64_data = segment[len("ICC:"):].strip()
            if not b64_data:
                continue
            try:
                data = base64.b64decode(b64_data, validate=True)
            except Exception:
                continue
            if len(data) < 128:
                continue
            # Validate ICC magic number 'acsp' at header offset 36-39
            if data[36:40] != b"acsp":
                continue
            return data
    except Exception as e:
        logger.debug(f"XMP ICC extraction failed: {e}")
    return None

def extract_icc_native(jxl_path, tmp_dir):
    """Extract ICC profile directly from JXL (for lossless files).
    Returns ICC bytes or None.
    NOTE: -o must come BEFORE the input file (exiftool is order-sensitive;
    the reversed order fails with 'No writable tags set'). Some exiftool
    versions do not expose ICC_Profile from JXL at all — this is a
    best-effort fallback; the XMP blob (encoder) is the primary path.
    """
    try:
        icc_path = tmp_dir / "native.icc"
        _run_exiftool_argfile(
            ["-o", str(icc_path), "-b", "-ICC_Profile", str(jxl_path)], timeout=30
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
    """Load target ICC profile from file path or built-in alias.

    Built-in alias: sRGB only (PIL's ImageCms.createProfile only supports sRGB/LAB/XYZ).
    Returns ICC bytes or None.
    """
    if not path:
        return None

    # Built-in aliases
    aliases = {
        'srgb': 'sRGB',
    }
    key = str(path).strip().lower()
    if key in aliases:
        if not ImageCms:
            logger.error("ImageCms (Pillow) is required for built-in ICC profiles")
            return None
        try:
            profile_name = aliases[key]
            profile = ImageCms.createProfile(profile_name)
            return ImageCms.getOpenProfile(profile).tobytes()
        except Exception as e:
            logger.error(f"Failed to create built-in ICC profile '{path}': {e}")
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
        elif 'srgb' in data_str:
            return 'srgb'
        elif 'adobe' in data_str and 'rgb' in data_str:
            return 'adobe'
        elif '2020' in data_str or 'bt2020' in data_str or 'rec.2020' in data_str:
            return '2020'
        elif 'p3' in data_str or 'display p3' in data_str or 'dci-p3' in data_str:
            return 'p3'
    except Exception:
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
    - None: No ICC handling. djxl auto only, output has no ICC profile.
            For consumer JXLs without embedded ICC
    - Basic: Default when no XMP ICC. djxl auto + ICC from JXL (if present).
             Preserves ICC generated by djxl
    - Matrix: Linear decode + LittleCMS transformation.
              For special color space conversion needs

    Returns: (mode, reason) tuple
             mode: 'roundtrip', 'none', 'basic', or 'matrix'
             reason: human-readable explanation
    """
    # Matrix mode override (for special color conversion needs)
    if USE_MATRIX_MODE:
        return 'matrix', "Matrix mode (linear + LittleCMS transform)"

    # Force none mode (ignore all ICC)
    if FORCE_NONE_MODE:
        return 'none', "None mode (no ICC handling)"

    # Force basic mode (use JXL ICC, ignore XMP)
    if FORCE_BASIC_MODE:
        return 'basic', "Basic mode (ICC from JXL)"

    # Default logic: XMP ICC present >Roundtrip, native ICC >Basic, no ICC >None
    if has_original_icc:
        return 'roundtrip', "Roundtrip mode (ICC from XMP + djxl auto)"
    else:
        return 'basic', "Basic mode (djxl auto + native ICC if present)"

# ═══════════════════════════════════════════════════════════════════════════════
# DECODING
# ═══════════════════════════════════════════════════════════════════════════════

def decode_auto_png(jxl_path, output_png):
    """
    Decode JXL using djxl auto mode to PNG format.
    Returns True on success.
    Raises RuntimeError on failure.
    """
    cmd = ["djxl", str(jxl_path), str(output_png)]
    r = subprocess.run(cmd, capture_output=True, timeout=DJXL_TIMEOUT)
    if r.returncode != 0:
        err = (r.stderr or b"").decode(errors='replace')[:200]
        raise RuntimeError(f"djxl auto failed: {err}")
    return True

def extract_icc_from_png(png_path):
    """
    Extract ICC profile from PNG file using PIL.
    Returns ICC bytes or None.
    """
    try:
        with Image.open(png_path) as img:
            icc = img.info.get('icc_profile')
            if icc:
                return icc
    except Exception as e:
        logger.debug(f"PNG ICC extraction failed: {e}")
    return None

def read_png_to_numpy(png_path, target_depth=16):
    """
    Read PNG file and convert to numpy array.
    Handles 8-bit and 16-bit RGB/RGBA.
    Scales 8-bit to 16-bit when target_depth=16.

    Uses imagecodecs when available because PIL cannot faithfully read
    16-bit RGB/RGBA PNGs (it returns uint8 data even for 16-bit files).
    """
    # Try imagecodecs first for faithful 16-bit RGB/RGBA support.
    # Only the DECODE call lives in this try: an unexpected array shape is a
    # hard error (unsupported/corrupt PNG) and must NOT fall through to the
    # 8-bit PIL path silently.
    arr = None
    imagecodecs_unusable = False
    try:
        import imagecodecs
        arr = imagecodecs.png_decode(Path(png_path).read_bytes())
    except ImportError:
        imagecodecs_unusable = True
    except Exception as e:
        # A failed decode (MemoryError, unsupported PNG variant, version
        # mismatch) leaves us exactly as helpless as a missing import: the only
        # remaining reader is PIL, which downgrades 16-bit RGB/RGBA to 8-bit.
        # Flag it as unusable so the IHDR hard-fail below also runs here,
        # instead of degrading precision behind a warning.
        imagecodecs_unusable = True
        logger.warning(f"imagecodecs png_decode failed ({e}); falling back to PIL.")

    if imagecodecs_unusable and target_depth == 16:
        # Without imagecodecs, PIL silently quantizes 16-bit RGB/RGBA/LA PNGs
        # to 8-bit — the output TIFF would still be "16-bit" but with degraded
        # data, breaking the 16-bit fidelity promise. Detect it from the IHDR
        # and fail loudly instead of degrading in silence.
        try:
            # Only the 26-byte signature+IHDR is needed; reading the whole file
            # would allocate hundreds of MB per worker on large 16-bit PNGs —
            # in exactly the low-resource scenario that got us here.
            with open(png_path, 'rb') as f:
                head = f.read(26)
            if (len(head) == 26 and head[:8] == b'\x89PNG\r\n\x1a\n'
                    and head[24] == 16 and head[25] in (2, 4, 6)):
                raise RuntimeError(
                    "imagecodecs is required for faithful 16-bit RGB/RGBA decoding "
                    "(PIL would degrade to 8-bit). Install it: pip install imagecodecs")
        except RuntimeError:
            raise
        except Exception:
            pass
        logger.warning("imagecodecs unavailable; falling back to PIL.")

    if arr is not None:
        if arr.ndim == 2 or (arr.ndim == 3 and arr.shape[2] == 1):
            # Grayscale — keep it 2D so single-channel JXLs decode back to
            # single-channel TIFF pages instead of being expanded to RGB.
            if arr.ndim == 3:
                arr = arr[:, :, 0]
            if target_depth == 16 and arr.dtype == np.uint8:
                arr = arr.astype(np.uint16) * 257
            elif target_depth == 8 and arr.dtype == np.uint16:
                arr = np.rint(arr / 257).astype(np.uint8)
            return arr, None
        elif arr.ndim == 3 and arr.shape[2] == 2:
            # Grayscale + alpha (PNG color type 4): return 2D gray + alpha
            gray = arr[:, :, 0]
            alpha = arr[:, :, 1]
            if target_depth == 16 and gray.dtype == np.uint8:
                gray = gray.astype(np.uint16) * 257
                alpha = alpha.astype(np.uint16) * 257
            elif target_depth == 8 and gray.dtype == np.uint16:
                gray = np.rint(gray / 257).astype(np.uint8)
                alpha = np.rint(alpha / 257).astype(np.uint8)
            return gray, alpha
        elif arr.ndim == 3 and arr.shape[2] in (3, 4):
            rgb = arr[:, :, :3]
            alpha = arr[:, :, 3] if arr.shape[2] == 4 else None
            if target_depth == 16 and rgb.dtype == np.uint8:
                rgb = rgb.astype(np.uint16) * 257
            elif target_depth == 8 and rgb.dtype == np.uint16:
                rgb = np.rint(rgb / 257).astype(np.uint8)
            if alpha is not None:
                if target_depth == 16 and alpha.dtype == np.uint8:
                    alpha = alpha.astype(np.uint16) * 257
                elif target_depth == 8 and alpha.dtype == np.uint16:
                    alpha = np.rint(alpha / 257).astype(np.uint8)
            return rgb, alpha
        else:
            # Unexpected shape from imagecodecs — fail loudly (per-file error)
            # instead of silently degrading through the PIL fallback.
            raise ValueError(f"Unsupported PNG array shape from imagecodecs: {arr.shape}")

    with Image.open(png_path) as img:
        # Handle 16-bit modes (I;16, I) - return as 2D uint16 grayscale
        if img.mode in ('I;16', 'I'):
            arr = np.array(img)
            if arr.dtype == np.int32:
                # PIL I mode returns int32, convert to uint16
                arr = arr.astype(np.uint16)
            # Downscale to 8-bit if requested
            if target_depth == 8 and arr.dtype == np.uint16:
                arr = np.rint(arr / 257).astype(np.uint8)
            # Keep grayscale 2D (no RGB expansion)
            return arr, None
        elif img.mode == 'L':
            # 8-bit grayscale — keep 2D
            arr = np.array(img)
            if target_depth == 16:
                arr = arr.astype(np.uint16) * 257
            return arr, None
        elif img.mode == 'LA':
            # Grayscale + alpha — keep both channels
            arr = np.array(img)
            gray, alpha = arr[:, :, 0], arr[:, :, 1]
            if target_depth == 16:
                gray = gray.astype(np.uint16) * 257
                alpha = alpha.astype(np.uint16) * 257
            return gray, alpha
        elif img.mode == 'RGBA':
            arr = np.array(img)
            rgb = arr[:, :, :3]
            alpha = arr[:, :, 3]
        elif img.mode == 'RGB':
            rgb = np.array(img)
            alpha = None
        else:
            rgb_img = img.convert('RGB')
            rgb = np.array(rgb_img)
            alpha = None

        # Scale 8-bit → 16-bit when writing 16-bit TIFF, or 16-bit → 8-bit when
        # writing 8-bit TIFF.
        if target_depth == 16 and rgb.dtype == np.uint8:
            rgb = rgb.astype(np.uint16) * 257  # 0-255 → 0-65535
        elif target_depth == 8 and rgb.dtype == np.uint16:
            rgb = np.rint(rgb / 257).astype(np.uint8)
        if alpha is not None:
            if target_depth == 16 and alpha.dtype == np.uint8:
                alpha = alpha.astype(np.uint16) * 257
            elif target_depth == 8 and alpha.dtype == np.uint16:
                alpha = np.rint(alpha / 257).astype(np.uint8)

        return rgb, alpha

def decode_rec2020_linear(jxl_path, output_ppm, icc_out_path):
    """
    Decode JXL to Rec.2020 linear color space.
    Also extracts ICC profile generated by djxl for verification.
    Format inferred from .ppm extension — do NOT pass --output_format flag.
    Raises RuntimeError on failure.
    """
    cmd = [
        "djxl", str(jxl_path), str(output_ppm),
        "--color_space=RGB_D65_202_Per_Lin",   # libjxl token for Rec.2020/BT.2100 primaries is "202"
        f"--icc_out={icc_out_path}",
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=DJXL_TIMEOUT)
    if r.returncode != 0:
        err = (r.stderr or b"").decode(errors='replace')[:200]
        raise RuntimeError(f"djxl Rec.2020 failed: {err}")
    return True

def read_ppm_to_numpy(ppm_path):
    """
    Read PPM/PGM file and convert to numpy array.
    Supports P6 (RGB) and P5 (grayscale) with 8-bit or 16-bit depth.
    Validates that file is complete (not truncated).

    Returns a uint16 array that is ALWAYS 3-channel: this feeds matrix mode
    only, whose LittleCMS transform is RGB-only, so a P5 grayscale page is
    expanded to RGB. Grayscale pages therefore come back as 3-channel RGB in
    matrix mode — like the alpha and 8-bit-precision losses, that is a
    known matrix-mode limitation; roundtrip/basic/none keep grayscale 2D via
    read_png_to_numpy().
    """
    with open(ppm_path, 'rb') as f:
        # Read header tokens, skipping comment-only lines/inline comments, until
        # we have magic, width, height and maxval. The magic line may carry
        # MORE tokens on the same line (single-line headers like
        # "P6 640 480 65535\n" are valid PNM), and may itself contain an
        # inline comment ("P6 # note\n").
        tokens = []
        for part in f.readline().strip().split():
            if part.startswith(b'#'):
                break
            tokens.append(part)
        while len(tokens) < 4:
            line = f.readline()
            if not line:
                break
            for part in line.strip().split():
                if part.startswith(b'#'):
                    break
                tokens.append(part)
        if len(tokens) < 4:
            raise ValueError(f"Invalid PPM header: could not read magic/width/height/maxval from {ppm_path}")
        magic = tokens[0]
        if magic not in (b'P6', b'P5'):
            raise ValueError(f"Unsupported PPM/PGM format: {magic}")
        width = int(tokens[1])
        height = int(tokens[2])
        maxval = int(tokens[3])

        raw = f.read()

        # Validate data size (prevent truncated PPM from djxl crash)
        bytes_per_pixel = 1 if maxval <= 255 else 2
        channels = 3 if magic == b'P6' else 1
        expected_size = width * height * channels * bytes_per_pixel
        
        if len(raw) < expected_size:
            raise RuntimeError(
                f"PPM file truncated: expected {expected_size} bytes, got {len(raw)}. "
                f"djxl may have crashed during decoding."
            )
        if len(raw) > expected_size:
            # Trim extra data (just in case)
            raw = raw[:expected_size]

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
            # Expand grayscale → RGB: the matrix-mode ICC transform downstream
            # is RGB-only. Say so instead of silently tripling the page size.
            logger.info(" >Matrix mode note: grayscale page expanded to RGB (PPM path); "
                        "use roundtrip/basic/none to keep it single-channel")
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
                            # ICC spec: s15Fixed16Number (signed 16.16 fixed point)
                            gamma_fixed = struct.unpack_from('>i', icc_bytes, data_offset+12)[0]
                            gamma = gamma_fixed / 65536.0
                            curves[name] = ('gamma', gamma)
                        elif func_type == 1:
                            # Gamma with offset: Y = (aX + b)^gamma
                            # ICC spec: params start at +12; g is the FIRST param
                            # (s15Fixed16Number). (+16 holds 'a', not gamma.)
                            gamma_fixed = struct.unpack_from('>i', icc_bytes, data_offset+12)[0]
                            gamma = gamma_fixed / 65536.0
                            curves[name] = ('gamma', gamma)
                        elif func_type == 2:
                            # Segmented curve (ProPhoto uses type 2)
                            # Same layout: g at +12, then a, b, c, d, e
                            gamma_fixed = struct.unpack_from('>i', icc_bytes, data_offset+12)[0]
                            gamma = gamma_fixed / 65536.0
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
        logger.info(f" >Target TRC extracted: {curve_type}={curve_data if curve_type=='gamma' else 'LUT'}")

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

                # Workaround: Pillow's ImageCms only accepts 8-bit RGB images,
                # so 16-bit input is quantized to 8-bit for the transform. The result
                # is then restored to uint16, but the effective precision is limited
                # to 8 bits. This is a known limitation of the Pillow/LittleCMS path.
                logger.warning("Matrix mode uses 8-bit internal precision via LittleCMS; "
                               "16-bit values are quantized during the color transform")
                temp_8bit = (img_array.astype(np.float32) / 257.0).astype(np.uint8)
                pil_img = Image.fromarray(temp_8bit, mode='RGB')

                result = ImageCms.applyTransform(pil_img, transform)

                # Back to float 0-1 range
                result_float = np.array(result).astype(np.float32) / 255.0
                lcms_success = True

                logger.debug(" >LittleCMS: matrix + curve applied")

            except Exception as e:
                logger.warning(f"LittleCMS failed: {e}")

        # Apply manual TRC only if LittleCMS failed
        if lcms_success and result_float is not None:
            # LittleCMS already did everything (matrix + curve), just convert to 16-bit
            logger.debug(" >Using LittleCMS result (no manual curve)")
            result_array = (result_float * 65535.0).astype(np.uint16)
        else:
            # Fallback: apply TRC curve manually (assumes same primaries or already converted)
            logger.debug(" >Applying TRC manually as fallback")
            pixels = img_array.astype(np.float32) / 65535.0

            if curve_type == 'gamma':
                gamma = curve_data
                if gamma > 0 and abs(gamma - 1.0) > 0.001:
                    pixels = np.power(pixels, 1.0 / gamma)
                    logger.debug(f" >Applied gamma {gamma} TRC")
            elif curve_type == 'lut':
                lut = np.array(curve_data)
                for c in range(3):
                    channel = pixels[:,:,c]
                    indices = (channel * (len(lut)-1)).astype(np.int32)
                    indices = np.clip(indices, 0, len(lut)-2)
                    frac = (channel * (len(lut)-1)) - indices
                    pixels[:,:,c] = lut[indices] + frac * (lut[indices+1] - lut[indices])
                logger.debug(f" >Applied LUT TRC")

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

def copy_metadata(jxl_path, tiff_path, tmp_dir, is_multipage=False):
    """Copy metadata from JXL to TIFF using exiftool."""
    try:
        # Copy all metadata from JXL. Writes on large TIFFs can take a while
        # on slow disks — use generous timeouts (was 10s, which silently
        # dropped metadata on big files).
        _run_exiftool_argfile(
            ["-overwrite_original", "-tagsfromfile", str(jxl_path),
             "-exif:all", str(tiff_path)], timeout=180
        )
        _run_exiftool_argfile(
            ["-overwrite_original", "-tagsfromfile", str(jxl_path),
             "-xmp:all", "-iptc:all", str(tiff_path)], timeout=180
        )
        # Fix Software and ImageDescription tags that tifffile may have written
        # on IFD0 or IFD1. For single-page TIFFs with a JPEG preview, only the
        # preview page (IFD1) needs these defaults cleared; the main image keeps
        # its metadata from the JXL. For multi-page TIFFs, IFD1 is a real page
        # and must keep its metadata, so we only clear on single-page files.
        if not is_multipage:
            _run_exiftool_argfile(
                ["-overwrite_original", "-ifd1:Software=", str(tiff_path)], timeout=60
            )
            _run_exiftool_argfile(
                ["-overwrite_original", "-ifd1:ImageDescription=", str(tiff_path)], timeout=60
            )
        # Clear the page-0 Software tag if it still holds tifffile's default.
        # The main image is IFD0 (a JPEG preview, when present, is IFD1);
        # tifffile's default Software string can survive on IFD0, so only clear
        # it when the JXL didn't supply its own (i.e. it still reads "tifffile").
        r_sw = _run_exiftool_argfile(
            ["-s", "-s", "-s", "-IFD0:Software", str(tiff_path)], timeout=30
        )
        if r_sw.returncode == 0 and r_sw.stdout and 'tifffile' in r_sw.stdout:
            _run_exiftool_argfile(
                ["-overwrite_original", "-IFD0:Software=", str(tiff_path)], timeout=60
            )
        # Also fix ImageDescription if it holds tifffile's shaped-JSON metadata.
        # Match the actual tifffile pattern (JSON starting with "shape") — a
        # substring check would wipe legitimate user captions like "Beautiful
        # shapes at dawn".
        r = _run_exiftool_argfile(
            ["-ImageDescription", str(tiff_path)], timeout=30
        )
        if r.returncode == 0 and r.stdout:
            _desc_value = r.stdout.split(":", 1)[1].strip() if ":" in r.stdout else r.stdout.strip()
            if _desc_value.startswith('{"shape"') or _desc_value.startswith("{'shape'"):
                _run_exiftool_argfile(
                    ["-overwrite_original", "-ImageDescription=", str(tiff_path)], timeout=60
                )

        # Strip the internal multi-page marker from dc:Relation but keep any
        # Relation values the user had. We rewrite the bag with only the
        # non-marker items (or clear it if the marker was the only value).
        # Read via exiftool JSON so list items are parsed exactly — splitting
        # on commas/semicolons would corrupt legitimate user values
        # (e.g. "Smith, John").
        try:
            rr = _run_exiftool_argfile(
                ["-j", "-XMP-dc:Relation", str(tiff_path)], timeout=30
            )
            kept = []
            if rr.returncode == 0 and rr.stdout:
                import json as _json
                try:
                    _data = _json.loads(rr.stdout)
                    _rel = _data[0].get("Relation") if _data else None
                except Exception:
                    _rel = None
                if _rel is not None:
                    _values = _rel if isinstance(_rel, list) else [str(_rel)]
                    def _is_internal_marker(token: str) -> bool:
                        t = token.strip()
                        return (
                            t.startswith(MULTIPAGE_MARKER_PREFIX)
                            or t.startswith(SUBFILETYPE_PREFIX)
                            or t.startswith(DEPTH_FLAG)
                            or t.startswith(PAGE_PREFIX)
                            or t == ICC_INHERITED_FLAG
                            or t == GRAYSCALE_FLAG
                            or t == THUMB_FLAG
                        )
                    kept = [str(v).strip() for v in _values
                            if str(v).strip() and not _is_internal_marker(str(v))]
                    has_internal = len(kept) != len([v for v in _values if str(v).strip()])
                else:
                    has_internal = False
            else:
                has_internal = False
            if has_internal:
                _run_exiftool_argfile(
                    ["-overwrite_original", "-XMP-dc:Relation=", str(tiff_path)], timeout=60
                )
                if kept:
                    add_lines = ["-overwrite_original"]
                    add_lines += [f"-XMP-dc:Relation+={_argfile_safe(v)}" for v in kept]
                    add_lines.append(str(tiff_path))
                    _run_exiftool_argfile(add_lines, timeout=60)
        except Exception as e_rel:
            logger.debug(f"Relation marker cleanup skipped: {e_rel}")
    except Exception as e:
        logger.warning(f"Metadata copy warning: {e}")

def cleanup_xmp_icc(tiff_path):
    """Remove ICC:base64 marker from XMP CreatorTool"""
    if not CLEANUP_XMP_ICC_MARKER:
        return
    try:
        r = _run_exiftool_argfile(
            ["-s", "-s", "-s", "-XMP-xmp:CreatorTool", str(tiff_path)], timeout=30
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
            clean = re.sub(r'\s*\|\s*$', '', clean)   # trailing pipe
            clean = re.sub(r'^\s*\|\s*', '', clean)   # leading pipe (ICC was mid-string)
            if not clean:
                clean = "jxl_tiff_decoder"
            _run_exiftool_argfile(
                ["-overwrite_original",
                 f"-XMP-xmp:CreatorTool={_argfile_safe(clean)}", str(tiff_path)], timeout=60
            )
            logger.debug(f" >Cleaned up XMP CreatorTool")
    except Exception as e:
        logger.debug(f"XMP cleanup skipped: {e}")

def add_jpeg_preview(tiff_path, tmp_dir, icc_data):
    """Add JPEG preview as second page of TIFF with proper thumbnail structure.

    v2 changes:
    - ICC is embedded in the main 16-bit image (page 0) for color-managed viewers
    - 16-bit main image is kept as page 0 (primary image for Windows Explorer)
    - 8-bit JPEG preview is written as page 1 with subfiletype=1 (thumbnail/reduced flag)
    - Windows Explorer uses page 0's ICC for thumbnails, showing correct colors

    This replaces the old approach of appending JPEG as page 1 while keeping the
    main image as the primary page.
    """
    if not ADD_JPEG_PREVIEW:
        return
    
    logger.info(f" >Adding JPEG preview to {tiff_path.name}...")
    
    try:
        # Read the current TIFF (16-bit data that was just written). For
        # single-page files (the only case this function runs) series[0] IS
        # page 0 — keep the array for the rewrite below instead of decoding
        # the full image a second time (~2x RAM and I/O on large files).
        with tifffile.TiffFile(str(tiff_path)) as tif:
            main_data = tif.series[0].asarray()
            img_data = main_data
            # Preserve page 0's SubfileType for the rewrite below (a restored
            # non-zero SubfileType would otherwise be lost).
            try:
                main_subfiletype = int(tif.pages[0].subfiletype or 0)
            except Exception:
                main_subfiletype = 0
            # Try to get ICC if not passed
            if icc_data is None:
                try:
                    icc_data = tif.pages[0].icc_profile
                except Exception:
                    icc_data = None

        if img_data.ndim == 3 and img_data.shape[2] == 4:
            # JPEG preview cannot carry alpha; drop the alpha channel before
            # generating the preview. The main image retains alpha in the TIFF.
            img_data = img_data[:, :, :3]
        elif img_data.ndim == 3 and img_data.shape[2] == 2:
            # Gray+alpha (LA): keep only the gray channel for the preview —
            # Image.save() cannot write mode LA as JPEG. The main image
            # retains the alpha extrasample in the TIFF.
            img_data = img_data[:, :, 0]

        if img_data.ndim == 2:
            h, w = img_data.shape
        else:
            h, w = img_data.shape[:2]

        # Safety check: skip preview for empty/corrupted images
        if h == 0 or w == 0 or img_data.size == 0:
            logger.warning(f" >Skipping JPEG preview: empty image dimensions ({h}x{w})")
            return

        # Calculate resize dimensions — never UPSCALE a small image for a
        # "preview" (a preview larger than the source is always wrong).
        max_dim = min(JPEG_PREVIEW_SIZE, max(w, h))
        if w >= h:
            new_w = max_dim
            new_h = max(1, int(h * max_dim / w))
        else:
            new_h = max_dim
            new_w = max(1, int(w * max_dim / h))

        # Convert to 8-bit for JPEG preview (rounded, like the rest of the
        # pipeline — not a bit-shift truncation)
        if img_data.dtype == np.uint16:
            img_8bit = np.rint(img_data / 257).astype(np.uint8)
        elif img_data.dtype == np.uint8:
            img_8bit = img_data
        else:
            mx = img_data.max()
            # Handle NaN, Inf, or zero max values safely
            if np.isnan(mx) or np.isinf(mx) or mx <= 0:
                logger.warning(f" >Invalid max value ({mx}) for 8-bit conversion, using direct cast")
                img_8bit = np.clip(img_data, 0, 255).astype(np.uint8)
            else:
                img_8bit = ((img_data.astype(np.float32) / mx) * 255).astype(np.uint8)

        # Resize using high-quality resampling
        pil_img = Image.fromarray(img_8bit)
        try:
            resample = Image.Resampling.LANCZOS   # Pillow >= 9.1
        except AttributeError:
            resample = Image.LANCZOS              # Pillow < 9.1
        preview = pil_img.resize((new_w, new_h), resample)

        # Convert preview to sRGB for Windows Explorer compatibility
        # (similar to Capture One behavior).  Preserve grayscale inputs
        # as single-channel instead of forcing them to RGB.
        original_mode = preview.mode
        if icc_data and ImageCms:
            try:
                # Create sRGB profile
                srgb_profile = ImageCms.createProfile('sRGB')
                
                # Load source profile from ICC bytes
                src_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc_data))
                
                # Convert from source ICC to sRGB
                preview_srgb = ImageCms.profileToProfile(preview, src_profile, srgb_profile)
                preview = preview_srgb
                if original_mode == 'L' and preview.mode == 'RGB':
                    preview = preview.convert('L')
                logger.debug(f" >Preview converted to sRGB")
            except Exception as e:
                logger.debug(f" >Preview color conversion failed: {e}, using original")
        
        # Save JPEG to temp file WITHOUT ICC (sRGB assumed by Windows)
        jpeg_path = tmp_dir / "preview.jpg"
        preview.save(str(jpeg_path), format='JPEG', quality=90)

        # Now rewrite the TIFF with proper structure:
        # Page 0: 16-bit main image with ICC (primary image for Windows Explorer)
        # Page 1: JPEG preview with thumbnail flag (NEWSubfileType=1)

        # TIFFTAG_NEWSubfileType = 254 (0xFE) — set to 1 for thumbnail
        # TIFFTAG_IMAGEWIDTH, etc. for each page

        # Write new TIFF with main image as page 0 and JPEG preview as page 1
        # Using tifffile's ability to write multipage TIFFs with different configurations per page

        # Create the multipage TIFF properly
        # Page 0: 16-bit main image with ICC (primary image)
        # Page 1: JPEG data with NEWSubfileType=1 (thumbnail/reduced resolution)

        # tifffile multipage writing with explicit page config
        compression_map = {"uncompressed": None, "lzw": "lzw", "zip": "zlib", "none": None}
        tiff_comp = compression_map.get(TIFF_COMPRESSION, "zlib")

        # We need to restructure the TIFF
        # Strategy: write to a temp file, then use tifffile to create proper structure
        temp_tiff = tmp_dir / "output.tif"

        # ICC fallback if not passed (uses the already-loaded array above —
        # no second full-image decode)
        if icc_data is None:
            try:
                with tifffile.TiffFile(str(tiff_path)) as tif:
                    icc_data = tif.pages[0].icc_profile
            except Exception:
                icc_data = None

        # Write TIFF with main image as page 0 and JPEG preview as page 1
        # Using photometric interpretation for page 0 (RGB, grayscale, or RGBA)
        # and page 1 (JPEG data is YCbCr or RGB).

        # Save JPEG preview; load it as an array via PIL
        with Image.open(jpeg_path) as jpeg_preview:
            jpeg_arr = np.array(jpeg_preview)

        # Write multipage TIFF following Capture One structure:
        # Page 0: main image (primary image for Windows Explorer)
        # Page 1: 8-bit preview with subfiletype=1 (thumbnail flag)
        # This ensures Windows Explorer uses the correct ICC from page 0

        with tifffile.TiffWriter(str(temp_tiff)) as tif_writer:
            try:
                write_method = tif_writer.write
            except AttributeError:
                write_method = tif_writer.save
            
            # Page 0: main image (primary)
            # Windows Explorer uses this page for thumbnail with ICC
            if main_data.ndim == 2 or (main_data.ndim == 3 and main_data.shape[2] == 1):
                main_photometric = 'MINISBLACK'
            elif main_data.ndim == 3 and main_data.shape[2] == 2:
                # Gray+alpha page
                main_photometric = 'MINISBLACK'
            else:
                main_photometric = 'RGB'
            kwargs_main = {
                'photometric': main_photometric,
                'compression': tiff_comp,
                # Same anti-default-tag treatment as the main writer: without
                # these, tifffile injects Software=tifffile.py and a shaped-JSON
                # ImageDescription on single-page outputs whenever exiftool is
                # missing/slow.
                'metadata': None,
                'software': '',
            }
            if main_subfiletype:
                kwargs_main['subfiletype'] = main_subfiletype
            if main_data.ndim == 3 and main_data.shape[2] in (2, 4):
                kwargs_main['extrasamples'] = 'UNASSALPHA'
            if icc_data:
                kwargs_main['iccprofile'] = icc_data
            
            write_method(main_data, **kwargs_main)

            # Page 1: 8-bit preview as thumbnail (subfiletype=1)
            # This marks it as a reduced-resolution image
            if jpeg_arr.ndim == 2 or (jpeg_arr.ndim == 3 and jpeg_arr.shape[2] == 1):
                preview_photometric = 'MINISBLACK'
            else:
                preview_photometric = 'RGB'
            kwargs_preview = {
                'photometric': preview_photometric,
                'compression': 'jpeg',
                'subfiletype': 1,  # Marks as thumbnail/reduced resolution
            }
            # Preview doesn't need ICC - Windows uses page 0's ICC
            
            write_method(jpeg_arr, **kwargs_preview)

        # Replace original TIFF with properly structured one
        if temp_tiff.exists():
            logger.debug(f" >Temp file created: {temp_tiff.stat().st_size} bytes")
            shutil.move(str(temp_tiff), str(tiff_path))
            logger.info(f" >Added JPEG preview ({new_w}x{new_h}) with ICC")
        else:
            logger.warning(f" >Temp file not created!")

    except Exception as e:
        logger.warning(f"Preview generation failed: {e}")
        import traceback
        logger.debug(f"Preview error traceback: {traceback.format_exc()}")

# ═══════════════════════════════════════════════════════════════════════════════
# PATH RESOLUTION (ALL MODES 0-8)
# ═══════════════════════════════════════════════════════════════════════════════

def resolve_output(jxl_path: Path, mode: int, input_root: Path) -> Path:
    """Resolve output TIFF path based on mode (0-8)"""

    def _warn_if_outside(result: Path) -> Path:
        # Modes 4/5 can land OUTSIDE the selected input tree for files at its
        # root — surface that instead of surprising the user later.
        if result is not None and not _is_relative_to(result, input_root):
            logger.warning(f"Output outside input tree: {jxl_path.name} -> {result}")
        return result

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
        new_name = _replace_suffix_token(old_name, JXL_SUFFIX_TO_REPLACE, TIFF_SUFFIX_REPLACE)
        if new_name == old_name:
            new_name = old_name + "_" + TIFF_SUFFIX_REPLACE
            logger.warning(f"'{JXL_SUFFIX_TO_REPLACE}' not found as a token in '{old_name}', using '{new_name}'")
        return _warn_if_outside(jxl_path.parent.parent / new_name / jxl_path.with_suffix(".tif").name)

    elif mode == 5:
        # Mode 5: Sibling folder next to each JXL folder
        return _warn_if_outside(jxl_path.parent.parent / TIFF_FOLDER_NAME / jxl_path.with_suffix(".tif").name)

    elif mode == 6:
        # Mode 6: EXPORT anchor - only JXLs INSIDE export marker folder
        # Match only path *directory* parts (parts[:-1]); a JXL whose own
        # filename happens to start/end with the marker is not an anchor.
        parts = list(jxl_path.parts)
        marker_lower = EXPORT_MARKER.lower()
        # Match folders starting or ending with EXPORT_MARKER case-insensitively
        export_idx = next((i for i, p in enumerate(parts[:-1])
                           if _marker_matches(p.lower(), marker_lower)), None)
        if export_idx is None:
            return None  # Skip files outside export marker folder

        export_dir = Path(*parts[:export_idx + 1])
        rel_parts = jxl_path.relative_to(export_dir).parts
        if not rel_parts:
            return None  # The marker matched the filename itself; not inside it
        if len(rel_parts) > 1:
            rel = Path(*rel_parts[1:])
        else:
            rel = Path(rel_parts[0])
        return export_dir / EXPORT_TIFF_FOLDER / rel.with_suffix(".tif")

    elif mode == 7:
        # Mode 7: EXPORT anchor - only JXLs inside export marker/[subfolder]
        parts = list(jxl_path.parts)
        marker_lower = EXPORT_MARKER.lower()
        export_idx = next((i for i, p in enumerate(parts[:-1])
                           if _marker_matches(p.lower(), marker_lower)), None)
        if export_idx is None:
            return None  # Skip files outside export marker folder

        export_dir = Path(*parts[:export_idx + 1])

        if EXPORT_JXL_SUBFOLDER:
            # Case-insensitive like find_jxls_mode7: the finder admits
            # '_EXPORT/jxl' for subfolder 'JXL', so the resolver must too
            # (Path.relative_to is case-sensitive on Linux).
            rel_parts = jxl_path.relative_to(export_dir).parts
            if not rel_parts or rel_parts[0].lower() != EXPORT_JXL_SUBFOLDER.lower():
                return None  # Not inside the specific subfolder
            rel = Path(*rel_parts[1:]) if len(rel_parts) > 1 else Path(jxl_path.name)
        else:
            rel_parts = jxl_path.relative_to(export_dir).parts
            if not rel_parts:
                return None  # The marker matched the filename itself
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

def decode_jxl_to_numpy(jxl_path, tmp_dir, target_icc_path=None, target_depth=None):
    """
    Decode a single JXL to a numpy array using the same strategy logic as convert_one.

    Returns (pixels, final_icc_bytes, reason, strategy) where:
      - pixels is a numpy array ready for TIFF writing
      - final_icc_bytes is the ICC profile to embed (may be None)
      - reason is a human-readable decode strategy description
      - strategy is one of 'roundtrip', 'matrix', 'none', 'basic'

    target_depth: 8 or 16. Defaults to the global DJXL_OUTPUT_DEPTH.
    """
    if target_depth is None:
        target_depth = DJXL_OUTPUT_DEPTH

    ppm_path = tmp_dir / "decoded.ppm"
    djxl_icc_path = tmp_dir / "djxl.icc"

    # Extract ICC first to decide strategy
    original_icc, icc_source = get_source_icc(jxl_path, tmp_dir)

    # Analyze ICC to get hint for logging
    if original_icc:
        original_icc_hint = analyze_icc_profile(original_icc)
        logger.debug(f" >ICC extracted from {icc_source} ({original_icc_hint})")

    # Select decode strategy based on ICC presence
    mode, reason = select_decode_strategy(has_original_icc=original_icc is not None)
    logger.debug(f" >{reason}")

    if mode == 'roundtrip':
        png_path = tmp_dir / "decoded_roundtrip.png"
        decode_auto_png(jxl_path, png_path)
        rgb, alpha = read_png_to_numpy(png_path, target_depth=target_depth)
        if alpha is not None:
            pixels = np.dstack([rgb, alpha])
        else:
            pixels = rgb
        return pixels, original_icc, reason, mode

    elif mode == 'matrix':
        # PPM has no alpha channel: RGBA sources lose alpha in matrix mode.
        # (Use roundtrip/basic/none to preserve it.)
        logger.info(f" >Matrix mode note: alpha channels are not preserved (PPM path)")
        decode_rec2020_linear(jxl_path, ppm_path, djxl_icc_path)
        pixels = read_ppm_to_numpy(ppm_path)
        djxl_icc = djxl_icc_path.read_bytes() if djxl_icc_path.exists() else None

        if target_icc_path:
            target_icc = load_target_icc(target_icc_path)
            final_pixels = apply_icc_transform(pixels, djxl_icc, target_icc, tmp_dir)
            final_icc = target_icc
        elif original_icc:
            final_pixels = apply_icc_transform(pixels, djxl_icc, original_icc, tmp_dir)
            final_icc = original_icc
        else:
            final_pixels = pixels
            final_icc = djxl_icc

        if target_depth == 8 and final_pixels.dtype == np.uint16:
            final_pixels = np.rint(final_pixels / 257).astype(np.uint8)
        elif target_depth == 16 and final_pixels.dtype == np.uint8:
            final_pixels = final_pixels.astype(np.uint16) * 257

        return final_pixels, final_icc, reason, mode

    elif mode == 'none':
        png_path = tmp_dir / "decoded_none.png"
        decode_auto_png(jxl_path, png_path)
        rgb, alpha = read_png_to_numpy(png_path, target_depth=target_depth)
        if alpha is not None:
            pixels = np.dstack([rgb, alpha])
        else:
            pixels = rgb
        return pixels, None, reason, mode

    else:  # basic
        png_path = tmp_dir / f"{jxl_path.stem}_basic.png"
        decode_auto_png(jxl_path, png_path)
        rgb, alpha = read_png_to_numpy(png_path, target_depth=target_depth)
        if alpha is not None:
            pixels = np.dstack([rgb, alpha])
        else:
            pixels = rgb
        djxl_icc = extract_icc_from_png(png_path)
        if djxl_icc:
            logger.debug(" >ICC extracted from djxl output")
        else:
            logger.debug(" >No ICC in djxl output")

        return pixels, djxl_icc, reason, mode


def convert_multipage_jxl_group(main_jxl, page_entries, write_path, final_path, target_icc_path=None):
    """
    Convert a group of JXLs belonging to the same multi-page TIFF into a single
    multi-page TIFF.

    page_entries: sorted list of (jxl_path, page_idx, is_thumbnail, icc_inherited,
                                  subfiletype, grayscale, depth) tuples.
    """
    # The pool submits every task up front, so a run that has given up cannot
    # stop scheduling — it stops HERE instead. Queued files return untouched
    # and are reported as "not attempted", never as errors.
    _why = _aborted()
    if _why:
        return str(main_jxl), "aborted", _why

    already_exists = final_path.exists()

    if already_exists:
        if OVERWRITE is False:
            n, total = next_count()
            logger.info(f"[{n}/{total}] SKIP (exists) | {main_jxl.name}")
            return str(main_jxl), "skipped", str(final_path)
        elif OVERWRITE == "smart":
            # Use the newest JXL mtime in the group for sync decision
            try:
                newest_jxl_mtime = max(j.stat().st_mtime for j, _, _, _, _, _, _ in page_entries)
                final_mtime = final_path.stat().st_mtime
            except OSError:
                # A file vanished mid-run (TOCTOU): treat as stale and attempt
                # the conversion instead of crashing the whole batch.
                newest_jxl_mtime, final_mtime = 1, 0
            if newest_jxl_mtime <= final_mtime:
                n, total = next_count()
                logger.info(f"[{n}/{total}] SKIP (sync: TIFF up to date) | {main_jxl.name}")
                return str(main_jxl), "skipped", str(final_path)
            logger.info(f" >SYNC: JXL newer than TIFF, reconverting | {main_jxl.name}")

    overwritten = already_exists

    try:
        write_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    # Identity of a pre-existing output (non-staging only). The error handler
    # compares against this: a file whose identity is UNCHANGED was never
    # touched by this run (e.g. djxl failed before TiffWriter ever opened the
    # output — in that case the original TIFF is still intact on disk) and
    # must be KEPT, not deleted.
    _pre_identity = None
    if write_path == final_path and already_exists:
        try:
            _st = final_path.stat()
            _pre_identity = (_st.st_mtime_ns, _st.st_size)
        except OSError:
            pass

    with tempfile.TemporaryDirectory(prefix="tiff_", dir=TEMP_DIR) as tmp:
        tmp_dir = Path(tmp)
        try:
            page_arrays = []
            page_icc = None
            strategy = "unknown"

            for jxl_path, page_idx, is_thumb, icc_inherited, subfiletype, grayscale, depth in page_entries:
                # Decide target depth according to policy and original depth marker.
                # Explicit --depth 8 forces 8-bit output for backward compatibility.
                original_depth = depth  # may be None for old JXLs
                if DJXL_OUTPUT_DEPTH == 8:
                    target_depth = 8
                elif DEPTH_POLICY == "force16" or original_depth is None:
                    target_depth = 16
                elif DEPTH_POLICY == "preserve_original":
                    target_depth = original_depth
                else:  # preserve_thumbnails (default)
                    target_depth = 8 if (is_thumb and original_depth == 8) else 16

                pixels, icc_data, page_reason, page_strategy = decode_jxl_to_numpy(
                    jxl_path, tmp_dir, target_icc_path, target_depth=target_depth
                )
                page_arrays.append((pixels, page_idx, is_thumb, icc_data, icc_inherited, subfiletype, grayscale, target_depth))
                # Use ICC/strategy from the first (main/anchor) page for the whole TIFF
                if not is_thumb and page_icc is None:
                    page_icc = icc_data
                    strategy = page_strategy

            if not page_arrays:
                raise RuntimeError("No pages to write")

            # Sort by page index just in case
            page_arrays.sort(key=lambda x: x[1])

            is_multipage = len(page_arrays) > 1

            compression_map = {"uncompressed": None, "lzw": "lzw", "zip": "zlib", "none": None}
            tiff_comp = compression_map.get(TIFF_COMPRESSION, "zlib")

            # Write TIFF (single or multi-page).
            # metadata=None suppresses tifffile's shaped-JSON ImageDescription;
            # software='' suppresses the default "tifffile.py" Software tag.
            # Both otherwise leak into the final TIFF when the source JXL has no
            # EXIF/XMP to overwrite them (e.g. consumer JXLs, None mode).
            with tifffile.TiffWriter(str(write_path)) as tif_writer:
                try:
                    write_method = tif_writer.write
                except AttributeError:
                    write_method = tif_writer.save

                for i, (pixels, page_idx, is_thumb, entry_icc, entry_inherited, entry_subfiletype, entry_grayscale, target_depth) in enumerate(page_arrays):
                    kwargs = {
                        'compression': tiff_comp,
                        'metadata': None,
                        'software': '',
                    }
                    if entry_grayscale or pixels.ndim == 2:
                        # Restore single-channel grayscale page as 2D
                        if pixels.ndim == 3:
                            if pixels.shape[2] == 2:
                                # Grayscale + alpha: keep both, alpha as extrasample
                                kwargs['photometric'] = 'minisblack'
                                kwargs['extrasamples'] = 'UNASSALPHA'
                            else:
                                pixels = pixels[:, :, 0]
                                kwargs['photometric'] = 'minisblack'
                        else:
                            kwargs['photometric'] = 'minisblack'
                    elif pixels.ndim == 3 and pixels.shape[2] == 2:
                        # Gray+alpha from a JXL WITHOUT the encoder's grayscale
                        # marker (third-party files, --strip): same handling,
                        # otherwise tifffile would reject "expected 3, got 2".
                        kwargs['photometric'] = 'minisblack'
                        kwargs['extrasamples'] = 'UNASSALPHA'
                    elif pixels.ndim == 3 and pixels.shape[2] == 4:
                        # Preserve RGBA (alpha channel) in the output TIFF
                        kwargs['photometric'] = 'RGB'
                        kwargs['extrasamples'] = 'UNASSALPHA'
                    else:
                        kwargs['photometric'] = 'RGB'
                    # Attach ICC only to pages that carried their own ICC.
                    # Inherited pages are reconstructed without an ICC tag,
                    # matching the original TIFF structure.
                    if entry_icc and not entry_inherited:
                        kwargs['iccprofile'] = entry_icc
                    # Restore the original SubfileType when it was non-zero.
                    # Thumbnails are marked with subfiletype=1 regardless.
                    # SubfileType 4 (MASK) is not accepted by tifffile for normal
                    # image pages; map it to 2 (PAGE) which preserves the
                    # "additional page" semantics.
                    if is_thumb:
                        kwargs['subfiletype'] = 1
                    elif entry_subfiletype != 0:
                        if entry_subfiletype == 4:
                            kwargs['subfiletype'] = 2
                        else:
                            kwargs['subfiletype'] = entry_subfiletype

                    write_method(pixels, **kwargs)

            # JPEG preview: only for single-page, non-None groups.
            # add_jpeg_preview recreates the file via tifffile (pixels + ICC only),
            # so it must run BEFORE copy_metadata or all EXIF/XMP would be wiped.
            # Multi-page TIFFs skip it (add_jpeg_preview operates on series[0]),
            # and None mode skips it to preserve the v1.6.0 minimal-output contract.
            # ICC: when the anchor page was ICC-INHERITED, pass None — the
            # preview rewrite must not re-attach an ICC tag that the original
            # page never had (the main writer deliberately omits it).
            if ADD_JPEG_PREVIEW and not is_multipage and strategy != 'none':
                _anchor_inherited = page_arrays[0][4] if page_arrays else False
                add_jpeg_preview(write_path, tmp_dir, None if _anchor_inherited else page_icc)
            elif ADD_JPEG_PREVIEW and is_multipage:
                logger.info(f" >Skipping JPEG preview for multi-page TIFF ({len(page_arrays)} pages)")

            if strategy != 'none':
                # Full metadata copy (order: preview already added above)
                copy_metadata(main_jxl, write_path, tmp_dir, is_multipage=is_multipage)
                cleanup_xmp_icc(write_path)
            else:
                # Minimal metadata for None mode: EXIF only, no XMP/IPTC. Clear
                # the tifffile-injected Software/ImageDescription tags ONLY if
                # they still hold tifffile defaults — a legitimate value copied
                # from the source JXL must survive.
                _run_exiftool_argfile(
                    ["-overwrite_original", "-tagsfromfile", str(main_jxl),
                     "-exif:all", str(write_path)], timeout=180
                )
                r_sw0 = _run_exiftool_argfile(
                    ["-s", "-s", "-s", "-IFD0:Software", str(write_path)], timeout=30
                )
                if r_sw0.returncode == 0 and r_sw0.stdout and 'tifffile' in r_sw0.stdout:
                    _run_exiftool_argfile(
                        ["-overwrite_original", "-IFD0:Software=", "-Software=", str(write_path)], timeout=60
                    )
                r_id0 = _run_exiftool_argfile(
                    ["-ImageDescription", str(write_path)], timeout=30
                )
                if r_id0.returncode == 0 and r_id0.stdout:
                    _desc0 = r_id0.stdout.split(":", 1)[1].strip() if ":" in r_id0.stdout else r_id0.stdout.strip()
                    if _desc0.startswith('{"shape"') or _desc0.startswith("{'shape'"):
                        _run_exiftool_argfile(
                            ["-overwrite_original", "-IFD1:ImageDescription=", "-ImageDescription=", str(write_path)], timeout=60
                        )

            # Validate EVERY successful output, not just mode-8 delete gates:
            # a tool returning 0 does not guarantee a well-formed file (disk
            # full, AV truncation). A corrupt output marked OK would be
            # skipped forever by the next smart-sync run.
            # (The except handler deletes it.)
            if not _verify_tiff_integrity(write_path):
                raise RuntimeError("output TIFF failed the integrity check")

            n, total = next_count()
            status = "overwrite" if overwritten else "ok"
            thumb_count = sum(1 for _, _, is_thumb, _, _, _, _ in page_entries if is_thumb)
            real_count = len(page_entries) - thumb_count
            detail = f"{real_count} page(s)"
            if thumb_count:
                detail += f", {thumb_count} thumbnail(s)"
            logger.info(f"[{n}/{total}] {status.upper()} | {main_jxl.name} ({detail})")
            return str(main_jxl), status, str(final_path)

        except Exception as e:
            # Remove any partial output produced by THIS run so the next
            # smart-sync run does not mistake it for a fresh, up-to-date TIFF
            # and skip it forever. The delete only happens when THIS run
            # actually wrote: staging UUID file, no pre-existing file, or the
            # on-disk identity changed. A pre-existing TIFF that was never
            # touched (e.g. djxl/decode failed before TiffWriter opened the
            # output) is KEPT — the old comment claiming "TiffWriter already
            # truncated it" was only true for failures inside the writer.
            if write_path != final_path:
                try:
                    if write_path.exists():
                        write_path.unlink()
                except OSError:
                    pass
            elif _pre_identity is None:
                try:
                    if write_path.exists():
                        write_path.unlink()
                except OSError:
                    pass
            else:
                try:
                    _st = write_path.stat()
                    if (_st.st_mtime_ns, _st.st_size) != _pre_identity:
                        write_path.unlink()
                except OSError:
                    pass
            # Was the disk the real cause? djxl exits 0 while writing a
            # truncated file when the volume is full, so this arrives as a
            # decode/verify failure with nothing pointing at the drive.
            #
            # The size passed is the SOURCE group's, which for a decode is a
            # lower bound on the TIFF about to be written (a TIFF is several
            # times its JXL). So this reliably catches a volume that is truly
            # full and may miss one that is merely tight — it never reports a
            # healthy disk as full, which is the direction that matters.
            _need = 0
            for _entry in page_entries:
                try:
                    _need += Path(_entry[0]).stat().st_size
                except (OSError, IndexError):
                    pass
            _abort_if_disk_full(write_path.parent, _need)

            n, total = next_count()
            logger.error(f"[{n}/{total}] ERROR | {main_jxl.name} | {e}")
            return str(main_jxl), "error", str(e)

def process_group(group_tasks, workers, mode, target_icc=None):
    """Process a group of tasks in parallel.

    Each task is a dict with keys:
      - type: 'multi'
      - main_jxl: Path to the main JXL
      - entries: list of (jxl_path, page_idx, is_thumbnail, icc_inherited, subfiletype, grayscale, depth)
      - final_tiff: Path to final TIFF destination
    """
    use_staging = TEMP2_DIR is not None
    staging_dir = Path(TEMP2_DIR) if use_staging else None

    if use_staging:
        staging_dir.mkdir(parents=True, exist_ok=True)

    tasks = []
    for task in group_tasks:
        main_jxl = task["main_jxl"]
        final_tiff = task["final_tiff"]
        if use_staging:
            write_tiff_path = staging_dir / f"{uuid.uuid4().hex}_{main_jxl.stem}.tif"
        else:
            write_tiff_path = final_tiff
        tasks.append({
            "main_jxl": main_jxl,
            "entries": task["entries"],
            "ignored_thumbs": task.get("ignored_thumbs", []),
            "write_path": write_tiff_path,
            "final_tiff": final_tiff,
        })

    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {}
        for task in tasks:
            fut = ex.submit(
                convert_multipage_jxl_group,
                task["main_jxl"],
                task["entries"],
                task["write_path"],
                task["final_tiff"],
                target_icc,
            )
            futures[fut] = task
        for fut in as_completed(futures):
            task = futures[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                # An exception escaped convert_multipage_jxl_group entirely
                # (e.g. temp-dir failure). One bad group must not kill the run.
                n, total = next_count()
                logger.error(f"[{n}/{total}] ERROR | {task['main_jxl'].name} | {e}")
                results.append((str(task["main_jxl"]), "error", str(e)))

    if use_staging:
        moved = 0
        status_map = {r[0]: r[1] for r in results}
        for task in tasks:
            main_jxl = task["main_jxl"]
            write_path = task["write_path"]
            final_tiff = task["final_tiff"]
            status = status_map.get(str(main_jxl), "error")
            if status not in ("ok", "overwrite"):
                # "aborted" is as silent as "skipped": nothing was
                # written, and one line per never-attempted file is
                # the wall of noise the abort exists to prevent.
                if status not in ("skipped", "aborted"):
                    if write_path.exists():
                        logger.warning(f"  KEEP in staging ({status}) | {write_path.name}")
                    else:
                        # Partial output was already discarded by the error handler
                        logger.warning(f"  Partial output discarded ({status}) | {main_jxl.name}")
                continue
            if not write_path.exists():
                logger.warning(f"  KEEP (staging file missing) | {write_path.name}")
                continue
            try:
                final_tiff.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(write_path), str(final_tiff))
                moved += 1
            except OSError as e:
                # A locked/readonly destination must not abort the whole batch:
                # keep the file in staging and log it for manual recovery.
                logger.error(f"  MOVE FAILED, kept in staging | {write_path.name} -> {final_tiff} | {e}")
        if moved:
            logger.info(f" >Moved {moved} file(s) from staging to final")

    if DELETE_SOURCE and mode == 8:
        deleted = 0
        # Map status by main_jxl path — results arrive in completion order
        # (as_completed), NOT submission order, so a positional zip with `tasks`
        # would cross statuses under concurrency and could delete sources of
        # failed conversions. Key explicitly on the returned identifier.
        status_by_main = {r[0]: r[1] for r in results}
        for task in tasks:
            status = status_by_main.get(str(task["main_jxl"]), "error")
            if status not in ("ok", "overwrite"):
                continue
            final_tiff = task["final_tiff"]
            if not final_tiff.exists():
                continue
            if not _verify_tiff_integrity(final_tiff):
                logger.warning(f" KEEP (TIFF failed integrity check) | {task['main_jxl'].name}")
                continue
            # Delete only the sources whose pixels actually made it into the
            # TIFF. Thumbnails excluded by --thumbnail-handling ignore are NOT
            # in the output, so deleting them destroys data the user never got
            # back — "don't put it in the TIFF" is not "erase it". They are
            # kept and reported instead; the cost is a skip-forever
            # thumbnail-only group on later runs, which is noisy but harmless.
            _orphan_thumbs = list(task.get("ignored_thumbs", []))
            if _orphan_thumbs:
                logger.warning(
                    f" KEPT {len(_orphan_thumbs)} ignored thumbnail source(s) | "
                    f"{task['main_jxl'].name} | their pixels are NOT in the TIFF "
                    f"(--thumbnail-handling ignore); delete them yourself if you "
                    f"do not want them")
            for jxl_path, _, _, _, _, _, _ in task["entries"]:
                try:
                    jxl_path.unlink()
                    logger.info(f" DELETED source | {jxl_path.name}")
                    deleted += 1
                except Exception as e:
                    logger.warning(f" KEEP (could not delete) | {jxl_path.name}: {e}")
        if deleted:
            logger.info(f" >Deleted {deleted} source JXL(s)")

    return results

# Matched case-insensitively: globbing "*.jxl"/"*.JXL" missed "Photo.Jxl" on
# case-sensitive filesystems (Linux/macOS) — skipped without a word.
JXL_EXTS = frozenset({".jxl"})


def _iter_jxls(paths):
    seen = set()
    files = []
    for f in paths:
        if f.suffix.lower() not in JXL_EXTS:
            continue
        try:
            if not f.is_file():
                continue
            key = f.resolve()
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            files.append(f)
    return sorted(files)


def find_jxls_flat(path):
    """Find JXL files in the top-level directory only (no subfolders) — modes 0 and 1."""
    return _iter_jxls(path.glob("*"))

def find_jxls_recursive(path):
    """Find all JXL files recursively"""
    return _iter_jxls(path.rglob("*"))

def find_jxls_mode6(input_path):
    """Mode 6: only JXLs inside folders containing EXPORT_MARKER (any subfolder)."""
    all_jxls = find_jxls_recursive(input_path)
    filtered = []
    marker_lower = EXPORT_MARKER.lower()
    for j in all_jxls:
        # Match only directory parts; the filename itself is not an anchor
        parts_str = list(j.parts[:-1])
        # Match folders starting or ending with EXPORT_MARKER case-insensitively
        export_idx = next((i for i, p in enumerate(parts_str)
                           if _marker_matches(p.lower(), marker_lower)), None)
        if export_idx is not None:
            filtered.append(j)
    return sorted(filtered)

def find_jxls_mode7(input_path):
    """Mode 7: only JXLs inside EXPORT_MARKER/EXPORT_JXL_SUBFOLDER."""
    all_jxls = find_jxls_recursive(input_path)
    filtered = []
    marker_lower = EXPORT_MARKER.lower()
    subfolder_lower = EXPORT_JXL_SUBFOLDER.lower()
    for j in all_jxls:
        # Match only directory parts; the filename itself is not an anchor
        parts_str = list(j.parts[:-1])
        export_idx = next((i for i, p in enumerate(parts_str)
                           if _marker_matches(p.lower(), marker_lower)), None)
        if export_idx is None:
            continue
        if EXPORT_JXL_SUBFOLDER:
            if export_idx + 1 < len(parts_str) and parts_str[export_idx + 1].lower() == subfolder_lower:
                filtered.append(j)
        else:
            filtered.append(j)
    return sorted(filtered)

def _is_thumbnail_jxl(jxl_path: Path) -> bool:
    """Return True if the JXL filename ends with the configured thumbnail suffix."""
    return bool(THUMBNAIL_SUFFIX) and jxl_path.stem.endswith(THUMBNAIL_SUFFIX)


def _has_internal_markers(info: dict) -> bool:
    """True when the marker map entry carries any jxlphoto-* marker."""
    return bool(info.get('group') or info.get('inherited') or info.get('subfiletype')
                or info.get('grayscale') or info.get('depth'))

def _parse_jxl_page_suffix(name: str):
    """Parse a JXL filename and return (stem, page_idx, is_thumbnail).

    Examples:
        "photo.jxl"              -> ("photo", 0, False)
        "photo_page2.jxl"        -> ("photo", 2, False)
        "photo_page1_thumbnail"  -> ("photo", 1, True)
    """
    stem = name
    is_thumbnail = False
    if THUMBNAIL_SUFFIX and stem.endswith(THUMBNAIL_SUFFIX):
        is_thumbnail = True
        stem = stem[:-len(THUMBNAIL_SUFFIX)]

    page_idx = 0
    m = re.search(r'_page(\d+)$', stem)
    if m:
        page_idx = int(m.group(1))
        stem = stem[:m.start()]

    return stem, page_idx, is_thumbnail

def _group_naming_path(main_jxl: Path, entries: list) -> Path:
    """Path whose NAME should name the reconstructed TIFF.

    The group's anchor is the lowest REAL page, so when page 0 of the original
    TIFF was a thumbnail the anchor is `scan_page1.jxl` and `scan.tif` came back
    as `scan_page1.tif`. Strip the page suffix to recover the original stem.

    Only for genuine multi-page groups: a STANDALONE third-party file named
    `photo_page2.jxl` is a whole image in its own right and must keep its name
    (renaming it to `photo.tif` could also collide with a real `photo.jxl`).
    """
    if len(entries) <= 1:
        return main_jxl
    stem, _page, _thumb = _parse_jxl_page_suffix(main_jxl.stem)
    if stem and stem != main_jxl.stem:
        return main_jxl.with_name(stem + main_jxl.suffix)
    return main_jxl


def _read_multipage_markers_batch(jxls: list) -> dict:
    """Read the multi-page marker and related flags for many JXLs in as few
    exiftool calls as possible. Returns {jxl_path: {group_id, inherited,
    subfiletype, grayscale}}.

    Spawning one exiftool per file is far too slow for large libraries
    (~100ms/file -> tens of minutes for tens of thousands of files), so we pass
    files in large batches and parse the per-file output. exiftool prints one
    "======== <path>" header per file with -G/-s style output; we use a JSON
    output which is unambiguous and easy to parse.
    """
    import json as _json
    markers: dict = {str(j): {'group': None, 'inherited': False, 'subfiletype': 0, 'grayscale': False, 'depth': None, 'page': None, 'thumb': False} for j in jxls}
    if not jxls:
        return markers

    BATCH = 400
    exe = _get_exiftool_cmd()
    for i in range(0, len(jxls), BATCH):
        chunk = jxls[i:i + BATCH]
        try:
            # Pass the file list via an exiftool argfile (-@): avoids the ~32k
            # Windows command-line limit with large batches, and prevents paths
            # containing [ ] from being treated as wildcards. UTF-8 charset for
            # non-ASCII paths.
            argfile = None
            try:
                with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                                 dir=TEMP_DIR, encoding="utf-8", newline="\n") as af:
                    argfile = af.name
                    af.write("-j\n-s\n-s\n-XMP-dc:Relation\n-charset\nFileName=UTF8\n-charset\nUTF8\n")
                    for j in chunk:
                        af.write(str(j) + "\n")
                r = subprocess.run(
                    [exe, "-@", argfile],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
                )
            finally:
                if argfile:
                    try:
                        os.unlink(argfile)
                    except OSError:
                        pass
            if not r.stdout:
                logger.warning(f"Multipage marker read failed for a batch of {len(chunk)} file(s) (rc={r.returncode}); treating them as standalone")
                continue
            # exiftool exits non-zero when it fails on ANY file of the batch,
            # but still prints valid JSON for the rest. Discarding that JSON
            # would strip the markers from up to 399 healthy files because of
            # one unreadable one — multi-page groups would split into single
            # pages and grayscale/depth/subfiletype flags would be lost.
            # Parse what it did return; files absent from the JSON keep the
            # standalone defaults already seeded in `markers`.
            data = _json.loads(r.stdout)
            if r.returncode != 0:
                logger.warning(f"Marker read: exiftool rc={r.returncode} on a batch of {len(chunk)} file(s); "
                               f"using the {len(data)} entry/entries it returned, the rest are standalone")
            for entry in data:
                src = entry.get("SourceFile")
                rel = entry.get("Relation")
                if src is None or rel is None:
                    continue
                # Relation may be a string or a list depending on cardinality
                if isinstance(rel, list):
                    values = rel
                else:
                    values = str(rel).replace(";", ",").split(",")
                info = {'group': None, 'inherited': False, 'subfiletype': 0, 'grayscale': False, 'depth': None, 'page': None, 'thumb': False}
                for token in values:
                    token = str(token).strip()
                    if token.startswith(MULTIPAGE_MARKER_PREFIX):
                        info['group'] = token[len(MULTIPAGE_MARKER_PREFIX):]
                    elif token == ICC_INHERITED_FLAG:
                        info['inherited'] = True
                    elif token.startswith(SUBFILETYPE_PREFIX):
                        try:
                            info['subfiletype'] = int(token[len(SUBFILETYPE_PREFIX):])
                        except ValueError:
                            pass
                    elif token == GRAYSCALE_FLAG:
                        info['grayscale'] = True
                    elif token.startswith(DEPTH_FLAG):
                        try:
                            info['depth'] = int(token[len(DEPTH_FLAG):])
                        except ValueError:
                            pass
                    elif token.startswith(PAGE_PREFIX):
                        try:
                            info['page'] = int(token[len(PAGE_PREFIX):])
                        except ValueError:
                            pass
                    elif token == THUMB_FLAG:
                        info['thumb'] = True
                # Match back to our path key (exiftool may normalize separators)
                key = str(Path(src))
                if key in markers:
                    markers[key] = info
                else:
                    # Normalization mismatch (drive-letter case, \\?\ prefix):
                    # the file falls back to standalone — make it visible
                    # instead of silent.
                    logger.warning(f"Marker read: path mismatch, treated as standalone | {src}")
                    markers[src] = info
        except Exception as e:
            # On any batch failure, leave those files as standalone (safe default)
            logger.warning(f"Multipage marker batch error ({e}); {len(chunk)} file(s) treated as standalone")
            continue
    return markers



def collect_multipage_groups(jxls: list) -> dict:
    """Group JXLs that belong to the same multi-page TIFF.

    Returns a dict mapping the main JXL path to a sorted list of
    (jxl_path, page_idx, is_thumbnail, icc_inherited, subfiletype, grayscale, depth) tuples.

    Grouping is driven by the encoder's XMP marker, NOT by filename. Only files
    that carry a matching group marker are merged; every unmarked file becomes
    its own single-page group. This prevents independently-named files such as
    scan.jxl + scan_page2.jxl from being silently merged and, with --mode 8,
    from having a source deleted after an unintended merge.

    When RECONSTRUCT_MULTIPAGE is False, grouping is disabled entirely and each
    JXL is treated as a standalone page (page suffix still parsed only to keep
    output filenames stable).
    """
    groups: dict = {}

    # Read markers first; they are needed even when reconstruction is disabled
    # so that per-file metadata (inheritance, grayscale, depth) is available.
    marker_map = _read_multipage_markers_batch(jxls)

    _DEFAULT_INFO = {'group': None, 'inherited': False, 'subfiletype': 0, 'grayscale': False, 'depth': None, 'page': None, 'thumb': False}

    if not RECONSTRUCT_MULTIPAGE:
        for j in jxls:
            info = marker_map.get(str(j), _DEFAULT_INFO)
            # Thumbnail role only counts when the file carries an internal
            # marker — a third-party *_thumbnail.jxl is a REAL photo to us.
            is_thumb = (info['thumb'] or _is_thumbnail_jxl(j)) and _has_internal_markers(info)
            groups[j] = [(j, 0, is_thumb, info['inherited'], info['subfiletype'], info['grayscale'], info['depth'])]
        return groups

    by_group: dict = {}
    standalone: list = []

    for j in jxls:
        info = marker_map.get(str(j), _DEFAULT_INFO)
        _stem, parsed_page, parsed_thumb = _parse_jxl_page_suffix(j.stem)
        if info['group']:
            # Page index and thumbnail role come from the MARKERS when present
            # (the encoder writes jxlphoto-page:/jxlphoto-thumb and they are
            # authoritative); the filename is only a fallback for files
            # encoded before those markers existed — it breaks for sources
            # named *_page<N> or *_thumbnail.
            if info['page'] is not None:
                page_idx = info['page']
                is_thumb = info['thumb']
            else:
                page_idx = parsed_page
                is_thumb = parsed_thumb
            # Group key is (folder, group_id), NOT just group_id: the id is a
            # hash of the SOURCE TIFF path, so two encodes of the same TIFF to
            # different folders share it. Merging across folders would produce
            # one TIFF with duplicated pages — and mode 8 would then delete
            # every copy after validating a single output. Pages of a genuine
            # split are always written to the same folder by the encoder.
            by_group.setdefault((str(j.parent), info['group']), []).append((j, page_idx, is_thumb, info['inherited'], info['subfiletype'], info['grayscale'], info['depth']))
        else:
            # Standalone (no group marker): never treat as thumbnail based on
            # the filename alone — grouping is marker-based, and so is the
            # thumbnail role. A third-party *_thumbnail.jxl must decode as a
            # normal full image (not be skipped by --thumbnail-handling ignore
            # or tagged subfiletype=1).
            standalone.append((j, parsed_page, False, info['inherited'], info['subfiletype'], info['grayscale'], info['depth']))


    # Marked groups: reconstruct multi-page TIFFs
    for _marker, entries in by_group.items():
        # Demote duplicate (page_idx, is_thumbnail) entries to standalone:
        # they mean two marker-carrying copies ended up in the SAME folder
        # (e.g. photo.jxl plus a renamed copy), which is not a valid group.
        seen_keys = set()
        unique_entries = []
        for e in sorted(entries, key=lambda e: (e[1], e[2], str(e[0]))):
            k = (e[1], e[2])
            if k in seen_keys:
                logger.warning(f"Duplicate page marker in group; decoding as standalone | {e[0].name}")
                standalone.append(e)
                continue
            seen_keys.add(k)
            unique_entries.append(e)
        if not unique_entries:
            continue
        entries = unique_entries
        # Prefer a real page 0 as the main/anchor; fall back to lowest real page,
        # then lowest page overall. Thumbnails are never chosen as main.
        real_page0 = [e for e in entries if e[1] == 0 and not e[2]]
        if real_page0:
            main_entry = real_page0[0]
        else:
            real_entries = [e for e in entries if not e[2]]
            if real_entries:
                main_entry = min(real_entries, key=lambda e: e[1])
            else:
                main_entry = min(entries, key=lambda e: e[1])
        main_jxl = main_entry[0]
        groups[main_jxl] = sorted(entries, key=lambda e: e[1])

    # Standalone files: one single-page group each
    for entry in standalone:
        groups[entry[0]] = [entry]

    return groups

# ═══════════════════════════════════════════════════════════════════════════════
# ARGUMENT PARSING AND MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="JPEG XL to TIFF converter with ICC preservation",
        epilog="""
Decode modes:
  Roundtrip (default with XMP ICC) - djxl auto + original ICC from XMP
  Basic (default with native ICC) - djxl auto + ICC from JXL (if present)
  None (--none) - djxl auto only, no ICC handling
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
    parser.add_argument("--workers", type=int, default=min(os.cpu_count() or 4, 16),
                      help="Number of parallel workers")
    parser.add_argument("--overwrite", action="store_true",
                      help="Overwrite existing files")
    parser.add_argument("--sync", action="store_true",
                      help="Only reconvert if JXL is newer than TIFF")

    # Decode mode flags
    parser.add_argument("--matrix", action="store_true", dest="use_matrix",
                      help="Use Matrix decode mode (linear + LittleCMS)")
    parser.add_argument("--basic", action="store_true", dest="force_basic",
                      help="Force Basic mode (djxl auto + native ICC from JXL)")
    parser.add_argument("--none", action="store_true", dest="force_none",
                      help="Force None mode (djxl auto, no ICC handling)")

    # ICC options
    parser.add_argument("--target-icc", type=str, default=None,
                        help="Convert to specific ICC profile. Can be a file path or built-in: sRGB")
    parser.add_argument("--no-icc-cleanup", action="store_true", dest="no_icc_clean",
                      help="Keep ICC:base64 marker in XMP")

    # Output options
    parser.add_argument("--depth", type=int, choices=[8, 16], default=None,
                      help="Output bit depth")
    parser.add_argument("--compression", choices=["zip", "lzw", "none", "uncompressed"], default=None,
                      help="TIFF compression")
    parser.add_argument("--staging", type=str, default=None,
                        help="Staging directory for output files")
    parser.add_argument("--export-marker", type=str, default=None,
                        help="Folder name marker for modes 6/7 (default: script setting EXPORT_MARKER)")
    parser.add_argument("--delete-source", action="store_true",
                        help="Delete source JXLs after successful decode (mode 8 only)")
    parser.add_argument("--delete-confirm-off", action="store_true",
                        help="Skip the interactive delete confirmation. For automation/"
                             "wrappers that already asked the user.")
    parser.add_argument("--export-subfolder", type=str, default=None,
                        help="[Mode 7] Only process JXLs inside this subfolder of the "
                             "export marker (default: script setting EXPORT_JXL_SUBFOLDER, "
                             "empty = all subfolders). Overrides EXPORT_JXL_SUBFOLDER.")
    parser.add_argument("--dry-run", action="store_true",
                      help="Preview operations without converting")
    parser.add_argument("--summary-json", action="store_true",
                        help=argparse.SUPPRESS)  # internal: machine-readable summary for jxl_photo.py
    parser.add_argument("--no-preview", action="store_true",
                      help="Skip JPEG preview generation (smaller TIFF files)")
    parser.add_argument("--thumbnail-handling", type=str, default=None,
                        choices=["ignore", "include", "generate"],
                        help="How to handle _thumbnail.jxl files when reconstructing multi-page TIFFs (default: include)")
    parser.add_argument("--thumbnail-suffix", type=str, default=None,
                        help="Suffix used to identify thumbnail JXLs (default: _thumbnail)")
    parser.add_argument("--no-reconstruct-multipage", action="store_true",
                        help="Disable multi-page reconstruction; decode every JXL to its own TIFF. "
                             "(Only marker-tagged split files are ever merged; this fully disables even that.)")
    parser.add_argument("--depth-policy", type=str, default=None,
                        choices=["force16", "preserve_thumbnails", "preserve_original"],
                        help="Bit depth policy per page: force16 = always 16-bit; "
                             "preserve_thumbnails = 8-bit only for thumbnails originally 8-bit (default); "
                             "preserve_original = keep each page's original bit depth. "
                             "Pages without a depth marker fall back to 16-bit.")

    args = parser.parse_args()

    if not args.input.exists():
        parser.error(f"input path does not exist: {args.input}")

    if args.workers < 1:
        parser.error("--workers must be >= 1")

    # Modes 6/7 scan a folder tree for the EXPORT marker; rglob() over a FILE
    # yields nothing, so a single-file input silently found 0 files and exited 0.
    if args.mode in (6, 7) and args.input.is_file():
        parser.error(f"--mode {args.mode} needs a DIRECTORY: it scans the folder "
                     f"tree for the export marker. Got a file. "
                     f"Use --mode 0 or 8 for a single file.")

    # Validate the temp dir up front: a bad TEMP_DIR would otherwise crash a
    # worker mid-batch (TemporaryDirectory(dir=TEMP_DIR)).
    if TEMP_DIR is not None:
        try:
            Path(TEMP_DIR).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            parser.error(f"TEMP_DIR is not usable: {TEMP_DIR} ({e})")
    if args.staging is not None:
        try:
            Path(args.staging).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            parser.error(f"staging directory is not usable: {args.staging} ({e})")

    # Apply globals
    global OVERWRITE, USE_MATRIX_MODE, FORCE_BASIC_MODE, FORCE_NONE_MODE
    global CLEANUP_XMP_ICC_MARKER, DJXL_OUTPUT_DEPTH, TIFF_COMPRESSION, TEMP2_DIR, DELETE_SOURCE, DELETE_CONFIRM, ADD_JPEG_PREVIEW, THUMBNAIL_HANDLING, THUMBNAIL_SUFFIX, RECONSTRUCT_MULTIPAGE, DEPTH_POLICY

    if args.sync:
        OVERWRITE = "smart"
    elif args.overwrite:
        OVERWRITE = True

    if args.delete_source:
        DELETE_SOURCE = True
    if args.delete_confirm_off:
        DELETE_CONFIRM = False
    if args.export_subfolder is not None:
        global EXPORT_JXL_SUBFOLDER
        EXPORT_JXL_SUBFOLDER = args.export_subfolder

    if args.depth_policy:
        DEPTH_POLICY = args.depth_policy

    if args.use_matrix:
        USE_MATRIX_MODE = True
    if args.force_basic:
        FORCE_BASIC_MODE = True
    if args.force_none:
        FORCE_NONE_MODE = True

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
    if args.export_marker:
        global EXPORT_MARKER
        EXPORT_MARKER = args.export_marker
    if args.no_preview:
        ADD_JPEG_PREVIEW = False
    if args.thumbnail_handling is not None:
        THUMBNAIL_HANDLING = args.thumbnail_handling
        if THUMBNAIL_HANDLING == "generate":
            THUMBNAIL_HANDLING = "include"
    if args.thumbnail_suffix is not None:
        if not args.thumbnail_suffix.strip():
            parser.error("--thumbnail-suffix must not be empty")
        THUMBNAIL_SUFFIX = args.thumbnail_suffix
    if getattr(args, "no_reconstruct_multipage", False):
        RECONSTRUCT_MULTIPAGE = False

    log_file = setup_logger()

    # Required tools first: a missing djxl/exiftool must be one clear message,
    # not N cryptic per-file errors (and, for exiftool, not a silent downgrade
    # to standalone pages).
    _check_external_tools(dry_run=args.dry_run)
    _warn_if_libjxl_too_old("djxl")

    # Warnings that must go to the configured log file
    if args.target_icc and not USE_MATRIX_MODE:
        logger.warning("--target-icc only applies in --matrix mode; ignoring target-icc in roundtrip/basic/none modes")
    if args.thumbnail_handling == "generate":
        logger.warning("--thumbnail-handling=generate is not yet implemented; falling back to include behavior")

    # Mode 8 delete confirmation happens AFTER the dry-run block below, so a
    # simulation never asks for the deletion token (consistent with the
    # encoder and transcoder).

    _overwrite_str = "sync" if args.sync else ("yes" if args.overwrite else ("smart" if OVERWRITE == "smart" else "no"))
    logger.info(f"Mode: {args.mode} | Depth: {DJXL_OUTPUT_DEPTH} | "
                f"Compression: {TIFF_COMPRESSION} | Workers: {args.workers}")
    logger.info(f"Matrix: {USE_MATRIX_MODE} | Basic: {FORCE_BASIC_MODE} | None: {FORCE_NONE_MODE} | "
                f"Overwrite: {_overwrite_str} | Thumbnail: {THUMBNAIL_HANDLING}")
    logger.info(f"Input: {args.input}")

    # Collect files
    if args.input.is_file():
        jxls = [args.input]
        output_root = args.output or args.input.parent
    else:
        if args.mode in (0, 1):
            jxls = find_jxls_flat(args.input)      # flat — subfolders not touched
        elif args.mode == 6:
            jxls = find_jxls_mode6(args.input)
        elif args.mode == 7:
            jxls = find_jxls_mode7(args.input)
        else:
            jxls = find_jxls_recursive(args.input)
        if args.mode == 2:
            output_root = args.output or args.input
            # mkdir deferred until after the dry-run check — a simulation must
            # not create folders on disk.
        else:
            output_root = args.output or args.input

    logger.info(f"Files found: {len(jxls)}")

    if len(jxls) == 0:
        logger.warning("No JXL files found")
        return

    # Group JXLs into multi-page TIFF sets
    mp_groups = collect_multipage_groups(jxls)

    # Build tasks: each task represents one output TIFF
    tasks = []
    for main_jxl, entries in mp_groups.items():
        # Filter thumbnail pages if requested — but KEEP the ignored entries
        # in the task so mode-8 delete can remove them with the group
        # (otherwise they become permanent orphans: next run they form a
        # thumbnails-only group that is skipped forever).
        ignored_thumbs = []
        if THUMBNAIL_HANDLING == "ignore":
            ignored_thumbs = [e for e in entries if e[2]]
            entries = [e for e in entries if not e[2]]
            if not entries:
                logger.warning(f"SKIP group with only thumbnails | {main_jxl.name}")
                continue

        tiff = resolve_output(_group_naming_path(main_jxl, entries), args.mode, output_root)
        if tiff is None:
            continue
        tasks.append({
            "type": "multi",
            "main_jxl": main_jxl,
            "entries": entries,
            "ignored_thumbs": ignored_thumbs,
            "final_tiff": tiff,
        })

    logger.info(f"TIFF outputs planned: {len(tasks)} (from {len(jxls)} JXLs, {len(mp_groups)} group(s))")
    _counter["total"] = len(tasks)

    _abort_on_duplicate_outputs([(task["main_jxl"], task["final_tiff"]) for task in tasks])

    # Dry run
    if args.dry_run:
        for task in tasks:
            entries = task["entries"]
            detail = ", ".join(f"{j.name}(p{idx}{' thumb' if th else ''}{' gray' if gray else ''})" for j, idx, th, _, _, gray, _ in entries)
            logger.info(f" DRY | {task['main_jxl'].name} -> {task['final_tiff']} | {detail}")
        logger.info(f"Dry run: {len(tasks)} output(s) would be generated from {len(jxls)} JXL(s).")
        emit_summary_json(
            args.summary_json,
            ok=len(tasks), overwritten=0, skipped=0, errors=0,
            log_file=log_file, dry_run=True,
        )
        return

    # Mode 8 confirmation (after dry-run so simulations never prompt)
    if args.mode == 8 and DELETE_SOURCE:
        logger.info("Mode 8 -- DELETE_SOURCE=True: source JXLs will be deleted after successful decode")
        if DELETE_CONFIRM:
            if not confirm_deletion_jxl():
                logger.info("Deletion not confirmed -- exiting.")
                sys.exit(3)

    if args.mode == 2 and not args.input.is_file():
        output_root.mkdir(parents=True, exist_ok=True)

    # Group by output folder
    groups = {}
    for task in tasks:
        groups.setdefault(task["final_tiff"].parent, []).append(task)

    logger.info(f"Output groups: {len(groups)}")

    # Process
    ok = err = skipped = overwritten = aborted = 0
    _reset_abort()  # a fresh run must not inherit a previous one's latch
    # Which files actually failed, for the wrapper's end-of-run FAILURES list.
    # Counts alone don't answer "did something break in the middle?" — the user
    # walks away from a multi-hour manifest and needs the paths on return.
    failed_files = []

    for dest_folder, group_tasks in groups.items():
        if len(groups) > 1:
            logger.info(f"-- Group: {dest_folder} ({len(group_tasks)} file(s))")

        results = process_group(group_tasks, args.workers, args.mode,
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
            # Counted apart from BOTH skipped and errors: the run gave up
            # before these were tried, so they are neither a policy decision
            # nor a failure.
            elif status == "aborted":
                aborted += 1
            elif status == "error":
                err += 1
                # result[0] is the source JXL path; result[2] is the reason.
                failed_files.append((str(result[0]),
                                     str(result[2]) if len(result) > 2 else "unknown error"))

    logger.info("\n" + "-"*50)
    if args.sync:
        logger.info(f"SYNC done: {ok} reconverted | {skipped} up to date | {err} errors")
    else:
        logger.info(f"Done: {ok} OK | {overwritten} overwrites | {skipped} skipped | {err} errors")

    # Loudest line in the block: on a batch that ran for hours the reason it
    # stopped must not be buried above the per-file scrollback.
    if _aborted():
        logger.error(f"RUN ABORTED: {_aborted()}")
        logger.error(f"  {aborted} file(s) were never attempted (not failures).")
    logger.info(f"Log: {log_file}")

    # Emitted BEFORE the exit below: a run with failures is exactly the run the
    # wrapper most needs the summary from.
    emit_summary_json(
        args.summary_json,
        ok=ok, overwritten=overwritten, skipped=skipped, errors=err,
        log_file=log_file, failures=failed_files,
        extras={"Not attempted (run aborted)": aborted},
    )

    # Exit 2 = aborted, and it outranks exit 1: a run that gave up early is not
    # the same event as a run that finished with some bad files, and automation
    # has to be able to tell them apart (the aborted one is worth retrying).
    if _aborted():
        sys.exit(2)

    # Non-zero exit when any file failed so wrappers/automation can detect it.
    if err > 0:
        sys.exit(1)

if __name__ == "__main__":
    main()
