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

import subprocess, os, platform, tempfile, threading, zlib, struct, logging, sys, shutil, base64, uuid, hashlib, json
import re
import functools
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import time
from typing import Any, Dict, Optional
import argparse
import numpy as np
import tifffile
from PIL import Image

# Module-level logger; setup_logger() replaces it with a configured instance when main() runs.
logger = logging.getLogger("jxl_convert")


def _verify_jxl_integrity(jxl_path: Path) -> bool:
    """Verify JXL file integrity before deleting source.

    Checks:
    1. File exists and size > 0
    2. File has valid JXL signature (0xFF 0x0A for bare JXL or ISOBMFF box)
    3. For container files: the box chain is walked and must be well-formed
       and end exactly at EOF — catches truncated/short-written files, which
       a signature-only check would accept.
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

        # Bare JXL starts with 0xFF 0x0A. Every output this toolkit produces
        # is a CONTAINER (exiftool always injects metadata boxes), so a bare
        # codestream at the delete gate means something went wrong mid-write —
        # and a bare file gets no structural validation at all, so a 2-byte
        # stub would pass. Refuse deletion: the source stays.
        if header[0:2] == b'\xff\x0a':
            logger.warning(f"Integrity check: bare codestream (no container boxes) — refusing | {jxl_path.name}")
            return False

        # Container format starts with 0x00 0x00 0x00 0x0C 0x4A 0x58 0x4C 0x20 0x0D 0x0A 0x87 0x0A
        if header != b'\x00\x00\x00\x0cJXL \r\n\x87\n':
            return False

        # Walk the ISOBMFF box chain; every box must be well-formed and the
        # chain must end exactly at EOF. A codestream box (jxlc/jxlp) must be
        # present — a metadata-only file must never pass the delete gate.
        file_size = stat.st_size
        i = 12
        has_codestream = False
        with open(jxl_path, 'rb') as f:
            while i < file_size:
                if i + 8 > file_size:
                    return False
                f.seek(i)
                box_header = f.read(8)
                size = int.from_bytes(box_header[0:4], "big")
                if box_header[4:8] in (b"jxlc", b"jxlp"):
                    has_codestream = True
                if size == 0:
                    # Box extends to end of file; must be the last one
                    return has_codestream
                if size == 1:
                    # Extended 64-bit size
                    if i + 16 > file_size:
                        return False
                    ext = f.read(8)
                    size = int.from_bytes(ext, "big")
                    if size < 16:
                        return False
                elif size < 8:
                    return False
                if i + size > file_size:
                    return False
                i += size
        return has_codestream and i == file_size
    except (OSError, IOError):
        return False


def _warn_if_libjxl_too_old(exe: str) -> None:
    """libjxl < 0.11 is not supported (see README). Say so once, up front.

    The RAM pipeline (default) pipes the PNG through stdin (`cjxl - out.jxl`),
    which old cjxl builds cannot read: every single file fails with a bare
    "Reading image data failed" and nothing points at the version.
    """
    v = _tool_version(exe)
    if v is not None and v[:3] < (0, 11, 2):
        logger.warning(
            f"{exe} {'.'.join(map(str, v))} is older than the supported minimum "
            f"(0.11.2). The default in-RAM pipeline needs a cjxl that reads PNG "
            f"from stdin; on older builds every file fails with "
            f"'Reading image data failed'. Upgrade libjxl, or use --no-ram.")


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
    # A non-token match must NOT stop the search: in 'MyTIFF_TIFF' the
    # embedded 'TIFF' fails the left-boundary test, but the trailing '_TIFF'
    # is a valid token and gets replaced.
    for m in pat.finditer(name):
        if m.start() == 0 or name[m.start() - 1] in '_- ':
            return name[:m.start()] + suffix_to + name[m.end():]
    return name


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


# --- Directory-scan progress ----------------------------------------------
# Duplicated across the backend scripts on purpose (see AGENTS.md): each stays
# standalone. Fix bugs in ALL copies.
#
# Walking a large tree on a slow or network drive costs real time -- measured
# at 25s for 3312 files on an external drive with a cold OS cache -- and the
# run printed NOTHING between "Input: ..." and "Files found: N". Twenty-five
# silent seconds reads as a freeze, not as work, and the natural reaction is to
# kill the run. (The page analysis that follows was never the problem: it
# reports progress as it goes, and a warm cache brings it down from 39s to
# under 2s.)
#
# Silent on a fast scan: nothing is printed until the walk has already taken
# longer than a person would wait without wondering.
_SCAN_QUIET_SECONDS = 3.0
_SCAN_REPORT_EVERY = 3.0
# The gap grows by this factor after each report, up to the cap below. A fixed
# 3s gap has no ceiling on line count: a five-minute network scan produced 100
# lines, which is the same "wall of noise" problem in a different costume.
#
# 1.618 (the golden ratio) rather than 2: measured over a 25s scan -- the real
# cold-cache case -- phi and 2 both give 3 lines, while e and pi drop to 2,
# losing a checkpoint exactly where the pause is long enough to worry about.
# Past that the cap dominates anyway: over five minutes phi/2/e/pi land on
# 9/8/7/7 lines, so the factor is nearly free and the gentler one wins.
_SCAN_REPORT_FACTOR = 1.618
_SCAN_REPORT_MAX = 60.0
_SCAN_CHECK_INTERVAL = 512   # entries between clock reads


def _scan_state(root):
    """Bookkeeping for a directory walk. See _scan_tick."""
    now = time.monotonic()
    return {"root": str(root) if root is not None else "", "scanned": 0,
            "t0": now, "next": now + _SCAN_QUIET_SECONDS,
            "gap": _SCAN_REPORT_EVERY, "announced": False}


def _scan_tick(st, found):
    """Report that a slow walk is still moving; stay quiet while it is fast.

    The clock is read once every _SCAN_CHECK_INTERVAL entries rather than on
    every one: this runs for every file on the volume, not just the matches.
    """
    st["scanned"] += 1
    if st["scanned"] % _SCAN_CHECK_INTERVAL:
        return
    now = time.monotonic()
    if now < st["next"]:
        return
    if not st["announced"]:
        logger.info(f"Searching for files under {st['root']} -- a large or "
                    f"network drive can take a while...")
        st["announced"] = True
    logger.info(f"  Scanned {st['scanned']} entries, {found} match(es) so far "
                f"({now - st['t0']:.0f}s)")
    st["gap"] = min(st["gap"] * _SCAN_REPORT_FACTOR, _SCAN_REPORT_MAX)
    st["next"] = now + st["gap"]


def _scan_done(st, found):
    """Close the report, but only if one was ever opened."""
    if st["announced"]:
        logger.info(f"  Scan finished: {found} match(es) from {st['scanned']} "
                    f"entries in {time.monotonic() - st['t0']:.0f}s")


# --- Staging leftovers ------------------------------------------------------
# Duplicated across the backend scripts on purpose (see AGENTS.md): each stays
# standalone. Fix bugs in ALL copies.
#
# A file whose conversion failed is deliberately KEPT in staging for manual
# recovery ("KEEP in staging" below), and nothing ever swept it. Over weeks of
# scheduled runs that is a slow leak on precisely the small scratch SSD the
# disk-full abort exists to protect -- the leftovers eventually cause the
# condition they were evidence of.
#
# Every staging name this tool writes starts with a uuid4 hex prefix, and that
# is what makes a sweep safe: a staging directory is frequently a shared scratch
# folder, so nothing that did not come from here may be touched. The scan is
# non-recursive for the same reason.
_STAGING_PREFIX_RE = re.compile(r"^[0-9a-f]{32}_")
# A file still being written belongs to a run in flight, possibly a CONCURRENT
# one sharing this directory. Only sweep what has been sitting still a while.
_STAGING_MIN_AGE_SECONDS = 3600


def _fmt_size(n):
    """Human size that stays informative below a gigabyte.

    A fixed GB format printed "0.0 GB" for everything under ~50 MB, which is
    exactly the reading someone with a nearly-full staging drive needs to see.
    """
    for unit, step in (("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2)):
        if n >= step:
            return f"{n / step:.1f} {unit}"
    return f"{n / 1024:.0f} KB"


def _staging_leftovers(staging_dir):
    """(paths, total_bytes) for files this tool left behind in staging."""
    found, total = [], 0
    try:
        entries = list(Path(staging_dir).iterdir())
    except OSError:
        return [], 0
    for f in entries:
        if not _STAGING_PREFIX_RE.match(f.name):
            continue
        try:
            if not f.is_file():
                continue
            total += f.stat().st_size
        except OSError:
            continue
        found.append(f)
    return found, total


def _report_staging_leftovers(staging_dir):
    """Say what is still sitting in staging, so the leak cannot stay invisible."""
    if not staging_dir:
        return
    found, total = _staging_leftovers(staging_dir)
    if not found:
        return
    logger.warning(f"Staging holds {len(found)} leftover file(s) ({_fmt_size(total)}) "
                   f"in {staging_dir}")
    for f in sorted(found)[:5]:
        logger.warning(f"    {f.name}")
    if len(found) > 5:
        logger.warning(f"    ... and {len(found) - 5} more")
    logger.warning("  These are outputs whose conversion failed, kept for inspection. "
                   "Pass --clean-staging on a later run to sweep the older ones.")


def _clean_staging(staging_dir):
    """Delete leftovers that have been idle long enough to be nobody's.

    Deliberately runs BEFORE the batch, not after: sweeping at the end would
    delete this run's own failures, which are the evidence the KEEP path exists
    to preserve. Sweeping first clears the previous runs' orphans instead.
    """
    if not staging_dir:
        return 0, 0
    found, _ = _staging_leftovers(staging_dir)
    if not found:
        return 0, 0
    cutoff = time.time() - _STAGING_MIN_AGE_SECONDS
    removed, freed = 0, 0
    for f in found:
        try:
            st = f.stat()
            if st.st_mtime > cutoff:
                continue        # young enough that a live run may own it
            size = st.st_size
            f.unlink()
        except OSError as e:
            logger.warning(f"  Could not remove staging leftover {f.name}: {e}")
            continue
        removed += 1
        freed += size
    if removed:
        logger.info(f"Staging: removed {removed} leftover file(s), freed {_fmt_size(freed)}")
    return removed, freed


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
        s = len(part_lower) - len(marker_lower)
        # endswith also needs a left anchor: either the marker brings its own
        # (a leading underscore, as in the default '_EXPORT') or the name
        # must boundary it — otherwise marker 'EXPORT' would match 'ReExport'.
        if s == 0 or marker_lower[0] in '_- ' or part_lower[s - 1] in '_- ':
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

# cjxl detection - early exit with a clear message if the encoder is missing
_cjxl_cmd = None
def _get_cjxl_cmd():
    global _cjxl_cmd
    if _cjxl_cmd is None:
        candidates = ["cjxl", "cjxl.exe"]
        for cmd in candidates:
            if shutil.which(cmd) is not None:
                _cjxl_cmd = cmd
                break
    return _cjxl_cmd

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
    if CJXL_BUFFERING is not None and _tool_at_least(_get_cjxl_cmd() or "cjxl", 0, 12):
        return [f"--buffering={CJXL_BUFFERING}"]
    return []

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

CJXL_BUFFERING = None
# [libjxl >= 0.12 only] Encoder buffering level passed to cjxl.
# None (default) = do not pass the flag; cjxl uses its own default (2), which is
#   the fast path — ~6x faster than 0 on large lossless TIFFs (45MP) with only
#   ~1.2% larger files (measured on real 16-bit Capture One exports).
# 0 = buffer entire image = best compression / most RAM (restores pre-0.12
#     behavior; use for maximum density when encode time doesn't matter).
# 2 = cjxl's v0.12 default. Ignored automatically when cjxl is < 0.12
#     (flag doesn't exist there).

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

USE_RAM_FOR_PNG = True
# True  -> PNG intermediate stays entirely in RAM (faster, ~400MB RAM per worker)
# False -> PNG is written to disk in TEMP_DIR (useful if RAM is limited)

PIL_MAX_IMAGE_PIXELS = None
# PIL's decompression bomb protection limit (prevents DOS attacks with malicious images).
# None  -> Disable the limit completely (recommended for trusted local files/panoramas)
# N     -> Maximum number of pixels (e.g., 500_000_000 for ~500MP limit)
# Applied module-wide right below (not only inside the thumbnail branch).
Image.MAX_IMAGE_PIXELS = PIL_MAX_IMAGE_PIXELS

TEMP_DIR = None
# Temporary directory for small intermediate files (EXIF binary, PNG if USE_RAM_FOR_PNG=False).
# None -> use system temp (usually C:\Users\...\AppData\Local\Temp on Windows)
# Ex:  -> r"E:\\temp_jxl"

CJXL_TIMEOUT = 900
# Timeout (seconds) for each cjxl invocation. 600s can be tight for very large
# lossless TIFFs (45-100MP) with many workers competing for CPU/disk; a timeout
# becomes a per-file error (output cleaned up), never a hung batch.

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

ICC_PNG_STRATEGY = "cautious"
# Controls whether the ICC profile is embedded in the intermediate PNG's iCCP chunk
# when feeding the image to cjxl in lossy mode (d > 0). Lossless (d = 0) always
# embeds the ICC so the JXL stores the profile as a native blob.
#
# Why this matters: some large scanner profiles (e.g. SilverFast SFprofT) cause
# cjxl/djxl to produce extremely dark or black images in lossy mode. Skipping the
# iCCP chunk avoids that, but the JXL file may display with the wrong colors in some
# viewers because the native primaries fall back to sRGB. The original ICC is still
# preserved as base64 metadata in the JXL and is restored into the reconstructed TIFF.
# For scanner workflows, the JXL is a backup container and the round-trip TIFF is the
# final image.
#
# "cautious"  -> Default. Run a small round-trip test on each unseen ICC and cache
#                the result. The first time a profile is seen it is tested with a
#                64x64 8/16-bit image; safe profiles are embedded, problematic ones
#                are skipped. Results are cached per-user so later runs are instant.
#                Safest choice for mixed workflows (camera + scanner).
# "heuristic" -> Skip iCCP for profiles that look problematic:
#                (a) ICC size > ICC_PROBLEMATIC_SIZE_THRESHOLD, OR
#                (b) ICC profile class is "scnr" (scanner input device).
#                Faster rule-based fallback.
# "always"    -> Always embed ICC in the PNG. Best for normal camera images; produces
#                JXLs with correct colors in most viewers.
# "skip"      -> Never embed ICC in the PNG. Produces correct pixel data for every
#                source, but JXL colors may look wrong in viewers until decoded to TIFF.

ICC_PROBLEMATIC_SIZE_THRESHOLD = 51200
# Size threshold (bytes) used by the "heuristic" strategy.
# ICC profiles larger than this are treated as potentially problematic and are not
# embedded in the intermediate PNG. The value is exposed because some workflows may
# use unusually large RGB profiles. 50 KB covers known scanner profiles while
# leaving normal camera profiles (typically 1-4 KB) untouched.

ICC_CACHE_DIR_OVERRIDE: Optional[Path] = None
# Override the default cross-platform ICC cache directory. None -> use the default
# (%APPDATA%/jxl-photo/icc-cache on Windows, ~/.config/jxl-photo/icc-cache on Linux).

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

MULTIPAGE_TIFF_MODE = "split"
# How to handle TIFFs with more than one page.
#
# DEFAULT CHANGED (was "ignore"): a single-page TIFF produces exactly the same
# output under "split", so the only files affected are the ones that were losing
# pages. Many TIFFs are multi-page without looking like it — Capture One and most
# scanners append an embedded preview, film scanners add an IR/mask page — and
# under "ignore" + mode 8 the source was deleted after encoding page 0 only,
# destroying the rest for good.
#
# "ignore"    -> Always encode page 0 (series[0]) and ignore extra pages.
#                This is the original behavior; dropped pages are reported, and
#                mode 8 refuses to delete a source whose pages were dropped.
# "skip"      -> If the TIFF has more than one "real" page (non-thumbnail),
#                skip the entire file and log a warning.
# "split"     -> [DEFAULT] Encode each real page to a separate JXL:
#                page 0 -> photo.jxl, page N -> photo_pageN.jxl.
#                A TIFF with a single real page yields exactly photo.jxl, i.e.
#                identical to "ignore" for the overwhelming majority of files.
#                Thumbnails are handled according to THUMBNAIL_MODE below.
# "split_all" -> Encode every page, including thumbnails, to separate JXLs.
#
# A "real" page is one where is_reduced=False and is_subifd=False.
# Thumbnails/pyramids are detected via the standard TIFF SubfileType flags.

THUMBNAIL_MODE = "exclude"
# Only used when MULTIPAGE_TIFF_MODE is "split".
# "exclude" -> Do not encode thumbnail pages (default).
# "include" -> Encode thumbnail pages too, with a _thumbnail suffix.
#
# A thumbnail is a REDUCED-resolution copy of another page (TIFF SubfileType
# is_reduced / is_subifd), i.e. derived data — which is why excluding it does not
# block source deletion in mode 8, while dropping a real page does. Use "include"
# if you want the decoded TIFF to reproduce the original page structure exactly.

THUMBNAIL_SUFFIX = "_thumbnail"
# Suffix appended to the output name when THUMBNAIL_MODE="include".
# Example: photo_page1_thumbnail.jxl

WARN_DISCARDED_THUMBNAILS = False
# Only used when MULTIPAGE_TIFF_MODE="split" and THUMBNAIL_MODE="exclude".
# False (default) -> excluding thumbnails is what the user explicitly asked for,
#   so it is reported ONCE in the run summary instead of once per file. A library
#   where every TIFF carries an embedded preview would otherwise produce one
#   WARNING line per file and bury the real errors.
# True  -> also log a per-file warning for each TIFF whose thumbnail is dropped.
#   Enable with --warn-thumbnail-discard when you want the file names.
# The summary line is printed either way: the drop is never silent.

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

DEPTH_XMP_PREFIX = "jxlphoto-depth:"
# Prefix for the original BitsPerSample value stored in dc:Relation. The decoder
# uses this to restore the original bit depth per page according to the user's
# --depth-policy (force16 / preserve_thumbnails / preserve_original).

PAGE_XMP_PREFIX = "jxlphoto-page:"
# Prefix for the TIFF page index stored in dc:Relation on split pages. Without
# it the decoder infers the page index from the FILENAME, which breaks when the
# source TIFF itself is named *_page<N> or *_thumbnail.

THUMB_XMP_FLAG = "jxlphoto-thumb"
# Marker appended to dc:Relation on thumbnail pages of a split (instead of
# relying on the _thumbnail filename suffix).


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
counter_lock = threading.Lock()
_counter = {"done": 0, "total": 0}
_d50_patch_count = {"applied": 0, "skipped": 0, "already_correct": 0, "skipped_needed": 0,
                    "applied_already_correct": 0, "skipped_already_correct": 0}
# Unique ICC profiles patched (by md5 of the original ICC bytes). The counters
# above increment per page in multipage splits; this set deduplicates profiles.
_d50_patched_hashes = set()
_d50_patch_lock = threading.Lock()
# Pages silently dropped by --multipage-mode ignore. Counted so the run summary
# can say it out loud: per-file warnings scroll away in a large batch.
_multipage_ignored = {"files": 0, "pages": 0}
_multipage_ignored_lock = threading.Lock()
# Thumbnail pages dropped by --multipage-mode split + --thumbnail-mode exclude.
# Tracked SEPARATELY from _multipage_ignored: mixing them made the summary blame
# "--multipage-mode ignore" for a drop that happened in split mode.
_thumbnails_dropped = {"files": 0, "pages": 0}
# Source TIFFs that lost REAL image pages at planning time (--multipage-mode
# ignore). Mode 8 consults this before deleting: encoding page 0 and then
# unlinking a 3-page scan destroys the other two pages permanently, and no
# integrity check downstream can notice — the one JXL that was written is
# perfectly valid. Keyed by os.path.normcase(str(tiff_path)).
_discarded_real_page_sources = set()
# Same, for thumbnail pages only. These do NOT block deletion — a thumbnail is a
# reduced-resolution copy of a page that IS in the output — but the delete line
# says so, because the decoded TIFF will not have that preview page back.
_discarded_thumb_sources = set()
# Per-file discard warnings are capped: a 5000-file library must not push every
# real error off the screen. The full totals always land in the run summary.
DISCARD_WARN_LIMIT = 20
_discard_warned = {"count": 0, "suppressed": 0}

def _capped_discard_warning(msg: str):
    """Log a per-file discard warning, but only up to DISCARD_WARN_LIMIT of them.

    Callers must already hold _multipage_ignored_lock (the counters and the cap
    have to move together, or a parallel scan prints N+workers lines).
    """
    if _discard_warned["count"] < DISCARD_WARN_LIMIT:
        _discard_warned["count"] += 1
        logger.warning(msg)
        if _discard_warned["count"] == DISCARD_WARN_LIMIT:
            logger.warning(f"... further per-file discard warnings suppressed "
                           f"(limit {DISCARD_WARN_LIMIT}) — totals in the run summary")
    else:
        _discard_warned["suppressed"] += 1


def _log_discard_summary():
    """Report every page the run chose not to encode.

    Called from BOTH the dry-run exit and the end of a real run: a dry run is
    where the user checks for page loss, and it never reaches the real summary.
    """
    # Pages dropped by the "ignore" policy — a WARNING, because the user did not
    # ask for it (ignore is the default) and it is real image data.
    if _multipage_ignored["files"]:
        logger.warning(
            f"Multi-page: DISCARDED {_multipage_ignored['pages']} page(s) from "
            f"{_multipage_ignored['files']} TIFF(s) (--multipage-mode ignore). "
            f"Re-run with --multipage-mode split to keep them.")

    # Thumbnails excluded on purpose: informational, not a warning — the user
    # asked for it. Still printed so the count is never a surprise.
    if _thumbnails_dropped["files"]:
        logger.info(
            f"Thumbnails: excluded {_thumbnails_dropped['pages']} thumbnail page(s) from "
            f"{_thumbnails_dropped['files']} TIFF(s) (--thumbnail-mode exclude, as requested). "
            f"Use --thumbnail-mode include to encode them too.")

    if _discard_warned["suppressed"]:
        logger.info(f"({_discard_warned['suppressed']} per-file discard warning(s) suppressed; "
                    f"see the totals above)")


def _abort_on_duplicate_outputs(pairs):
    """Abort the run if two outputs map to the same destination file.

    Modes 6/7 drop the first subfolder level under EXPORT_MARKER, so same-named
    files in different recipe subfolders would silently overwrite each other
    (and with mode 8 + delete, a single validated output could justify deleting
    several distinct sources). Better to stop loudly than to lose data.

    pairs: list of (source_path, dest_path) tuples (dest may be None-filtered).
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

def setup_logger():
    global logger
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file  = LOG_DIR / f"{timestamp}.log"

    logger = logging.getLogger("jxl_convert")
    logger.setLevel(logging.INFO)

    # Remove old handlers so a second call in the same process (tests,
    # wrapper-driven runs) does not duplicate log lines.
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

# A pathological run (unreadable drive) could fail every file; the line still
# has to fit in a pipe buffer, so the list is capped and the wrapper is told.
SUMMARY_MAX_FAILURES = 200


def emit_summary_json(enabled: bool, *, ok: int, overwritten: int, skipped: int,
                      errors: int, log_file, extras: Optional[Dict[str, int]] = None,
                      failures: Optional[list] = None, dry_run: bool = False,
                      unreadable: Optional[list] = None) -> None:
    """Print one JSON line the wrapper can aggregate. No-op without the flag.

    `extras` is a plain label -> count map (e.g. {"Thumbnails excluded": 758}),
    so the wrapper can sum and render script-specific stats without knowing
    what they mean. `failures` is a list of (file, reason) pairs. `dry_run`
    marks counts as "would have" so the wrapper can label the block instead of
    reporting a simulation as finished work.
    """
    if not enabled:
        return
    failures = failures or []
    unreadable = unreadable or []
    payload = {
        "script": Path(__file__).stem,
        "ok": ok,
        "overwritten": overwritten,
        "skipped": skipped,
        "errors": errors,
        "unreadable": len(unreadable),
        "dry_run": dry_run,
        "extras": {k: v for k, v in (extras or {}).items() if v},
        "failures": [{"file": f, "reason": r} for f, r in failures[:SUMMARY_MAX_FAILURES]],
        "failures_truncated": len(failures) > SUMMARY_MAX_FAILURES,
        "unreadable_files": [{"file": f, "reason": r} for f, r in unreadable[:SUMMARY_MAX_FAILURES]],
        "log": str(log_file),
    }
    try:
        # Straight to stdout, not through the logger: the timestamp prefix and
        # the log file copy would both be noise, and the wrapper matches on the
        # line starting with the prefix.
        print(SUMMARY_PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)
    except Exception:
        pass  # a summary line must never take down a finished run


_rejected_log_lock = threading.Lock()


def _log_rejected_file(file_path, reason):
    """Log rejected files to Logs/jxl_tiff_encoder/rejected_files.log for easy review."""
    try:
        rej_dir = SCRIPT_DIR / "Logs" / "jxl_tiff_encoder"
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

    def _warn_if_outside(result: Path) -> Path:
        # Modes 4/5 can land OUTSIDE the selected input tree for files at its
        # root — surface that once per file instead of surprising the user
        # later.
        if result is not None and not _is_relative_to(result, input_root):
            logger.warning(f"Output outside input tree: {tiff_path.name} -> {result}")
        return result

    if mode == 2:
        # Flat directory: input_root/photo.jxl
        return input_root / tiff_path.with_suffix(".jxl").name

    elif mode == 3:
        # Subfolder inside each TIFF folder
        return tiff_path.parent / JXL_FOLDER_NAME / tiff_path.with_suffix(".jxl").name

    elif mode == 4:
        # Rename folder replacing TIFF suffix with JXL suffix
        old_name = tiff_path.parent.name
        new_name = _replace_suffix_token(old_name, TIFF_SUFFIX_TO_REPLACE, JXL_SUFFIX_REPLACE)
        if new_name == old_name:
            new_name = old_name + "_" + JXL_SUFFIX_REPLACE
            logger.warning(f"'{TIFF_SUFFIX_TO_REPLACE}' not found as a token in '{old_name}', using '{new_name}'")
        return _warn_if_outside(tiff_path.parent.parent / new_name / tiff_path.with_suffix(".jxl").name)

    elif mode == 5:
        # Sibling folder next to each TIFF folder
        return _warn_if_outside(tiff_path.parent.parent / JXL_FOLDER_NAME / tiff_path.with_suffix(".jxl").name)

    elif mode == 6:
        # EXPORT_MARKER anchor — only TIFFs INSIDE export marker folder
        # Match only path *directory* parts (parts[:-1]); a TIFF whose own
        # filename happens to start/end with the marker is not an anchor.
        parts = tiff_path.parts
        marker_lower = EXPORT_MARKER.lower()
        # Match folders starting or ending with EXPORT_MARKER case-insensitively
        export_idx = next((i for i, p in enumerate(parts[:-1])
                           if _marker_matches(p.lower(), marker_lower)), None)
        if export_idx is None:
            return None  # Skip files outside export marker folder

        export_dir = Path(*parts[:export_idx + 1])
        rel_parts = tiff_path.relative_to(export_dir).parts
        if not rel_parts:
            return None  # The marker matched the filename itself; not inside it
        if len(rel_parts) > 1:
            rel = Path(*rel_parts[1:])
        else:
            rel = Path(rel_parts[0])
        return export_dir / EXPORT_JXL_FOLDER / rel.with_suffix(".jxl")

    elif mode == 7:
        # EXPORT_MARKER anchor — only TIFFs inside export marker/[subfolder]
        parts = tiff_path.parts
        marker_lower = EXPORT_MARKER.lower()
        export_idx = next((i for i, p in enumerate(parts[:-1])
                           if _marker_matches(p.lower(), marker_lower)), None)
        if export_idx is None:
            return None  # Skip files outside export marker folder

        export_dir = Path(*parts[:export_idx + 1])

        if EXPORT_TIFF_SUBFOLDER:
            # Case-insensitive like find_tiffs_mode7: the finder admits
            # '_EXPORT/jxl' for subfolder 'JXL', so the resolver must too
            # (Path.relative_to is case-sensitive on Linux).
            rel_parts = tiff_path.relative_to(export_dir).parts
            if not rel_parts or rel_parts[0].lower() != EXPORT_TIFF_SUBFOLDER.lower():
                return None  # Not inside the specific subfolder
            rel = Path(*rel_parts[1:]) if len(rel_parts) > 1 else Path(tiff_path.name)
        else:
            rel_parts = tiff_path.relative_to(export_dir).parts
            if not rel_parts:
                return None  # The marker matched the filename itself
            rel = Path(*rel_parts[1:]) if len(rel_parts) > 1 else Path(rel_parts[0])

        return export_dir / EXPORT_JXL_FOLDER / rel.with_suffix(".jxl")

    elif mode == 8:
        # In-place recursive: JXL goes to the same folder as the source TIFF.
        return tiff_path.parent / tiff_path.with_suffix(".jxl").name

    raise ValueError(f"Invalid mode: {mode}")

def _argfile_safe(value) -> str:
    """Sanitize a value for inclusion in an exiftool argfile (-@).

    Argfiles are parsed one argument per line, so embedded newlines in XMP
    text (multi-line captions, etc.) would split one argument into several
    bogus ones. Collapse all CR/LF runs into a single space.
    """
    return re.sub(r"[\r\n]+", " ", str(value))


# Charset directives for exiftool argfiles:
# - FileName=UTF8: file paths in the argfile are UTF-8 (Windows default is the
#   system codepage, so non-ASCII paths would not be found).
# - UTF8: tag VALUES read/written are UTF-8, so non-ASCII metadata round-trips.
_ARGFILE_CHARSET = "-charset\nFileName=UTF8\n-charset\nUTF8\n"


def _run_exiftool_argfile(args_lines, timeout=60):
    """Run exiftool with an argfile (UTF-8 + FileName charset).

    Using an argfile instead of raw argv avoids two Windows pitfalls:
    paths containing [ ] being treated as wildcards, and non-ASCII paths
    being decoded with the wrong codepage.
    Returns the CompletedProcess (stdout decoded as UTF-8).
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


def extract_exif_raw(tiff_path, tmp_dir):
    arg_file = tmp_dir / "exif_extract.args"
    arg_file.write_text(f"{_ARGFILE_CHARSET}-b\n-Exif\n{tiff_path}\n", encoding="utf-8")
    r = subprocess.run([_get_exiftool_cmd(), "-@", str(arg_file)], capture_output=True, timeout=60)
    if r.returncode == 0 and r.stdout and len(r.stdout) > 8:
        p = tmp_dir / f"{tiff_path.stem}.exif.bin"
        p.write_bytes(r.stdout)
        return p
    return None

@functools.lru_cache(maxsize=1024)
def get_exif_software(tiff_path_str):
    """Extracts Software field from EXIF metadata.
    Returns software string or empty string if not found.
    Cached per path: D50 auto-detection calls this once per PAGE, so a
    split_all scan would otherwise spawn one exiftool per page. Bounded so
    a huge library scan cannot grow the cache without limit."""
    tiff_path = Path(tiff_path_str)
    try:
        # Use -@ argument file to avoid wildcard expansion issues with brackets in paths
        with tempfile.TemporaryDirectory(prefix="exiftmp_") as tmp:
            arg_file = Path(tmp) / "args.txt"
            arg_file.write_text(f"{_ARGFILE_CHARSET}-s\n-Software\n{tiff_path}\n", encoding="utf-8")
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

def apply_d50_policy(icc_bytes, tiff_path):
    """Apply the D50 illuminant patch (if policy says so) and update stats.
    Accepts and returns raw ICC bytes. Safe to call with None (returns None)."""
    if icc_bytes is None:
        return None
    icc = bytearray(icc_bytes)

    if should_apply_d50_patch(tiff_path):
        if len(icc) < 80:
            logger.warning(f"ICC profile too short ({len(icc)} bytes) for D50 patch; skipping patch for {Path(tiff_path).name}")
            with _d50_patch_lock:
                _d50_patch_count["skipped"] += 1
                _d50_patch_count["skipped_needed"] += 1
            return bytes(icc)
        was_correct = _is_d50_already_correct(bytes(icc))
        icc[68:80] = bytes.fromhex("0000f6d6000100000000d32d")  # fix D50 illuminant
        with _d50_patch_lock:
            _d50_patch_count["applied"] += 1
            if was_correct:
                _d50_patch_count["already_correct"] += 1
                _d50_patch_count["applied_already_correct"] += 1
            else:
                _d50_patched_hashes.add(hashlib.md5(icc_bytes).hexdigest())
        logger.debug(f"Applied D50 patch to {Path(tiff_path).name}" + (" (was already correct)" if was_correct else ""))
    else:
        was_correct = _is_d50_already_correct(bytes(icc))
        with _d50_patch_lock:
            _d50_patch_count["skipped"] += 1
            if was_correct:
                _d50_patch_count["already_correct"] += 1
                _d50_patch_count["skipped_already_correct"] += 1
            else:
                _d50_patch_count["skipped_needed"] += 1
        logger.debug(f"D50 patch skipped for {Path(tiff_path).name}" + (" (already correct)" if was_correct else " (would have needed patch)"))

    return bytes(icc)


def _get_icc_profile_class(icc_bytes):
    """Return the 4-byte ICC profile/device class from the header, e.g. b'scnr'."""
    if not icc_bytes or len(icc_bytes) < 16:
        return None
    return icc_bytes[12:16]


# ─────────────────────────────────────────────
# CAUTIOUS ICC STRATEGY — Fase 2
# ─────────────────────────────────────────────
# Some ICC profiles make cjxl darken the image when they are embedded in the
# intermediate PNG (lossy mode). The "cautious" strategy runs a tiny round-
# trip for each unseen profile, caches the result, and uses that decision.
# Cache is stored per-user so the test only runs once per profile.

_CAU_MIN_MEAN = 10.0
_CAU_MIN_RATIO = 0.7
_CAU_TEST_SIZE = 64

# Serializes the cautious ICC test + cache read-modify-write across worker
# threads. Without it, two threads testing unseen profiles concurrently
# interleave cache reads/writes and lose entries (or corrupt the JSON file).
_icc_test_lock = threading.Lock()


def _icc_cache_dir() -> Path:
    if ICC_CACHE_DIR_OVERRIDE is not None:
        return ICC_CACHE_DIR_OVERRIDE
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "jxl-photo" / "icc-cache"
        return Path.home() / "AppData" / "Roaming" / "jxl-photo" / "icc-cache"
    elif system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "jxl-photo" / "icc-cache"
    else:
        return Path.home() / ".config" / "jxl-photo" / "icc-cache"


def _icc_cache_path() -> Path:
    return _icc_cache_dir() / "icc_cache.json"


def _load_icc_cache() -> Dict[str, Any]:
    path = _icc_cache_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception as e:
        logger.warning(f"ICC cache unreadable, resetting: {e}")
        return {}


def _save_icc_cache(cache: Dict[str, Any]) -> None:
    path = _icc_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: unique temp file + os.replace so a concurrent reader
        # (or ANOTHER jxl-photo process — the thread lock is per-process)
        # never sees a truncated JSON file.
        fd, tmp_name = tempfile.mkstemp(suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2)
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.warning(f"Failed to save ICC cache: {e}")


def _clear_icc_cache() -> bool:
    """Remove the ICC cache file and return whether something was deleted."""
    path = _icc_cache_path()
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except Exception as e:
        logger.warning(f"Failed to clear ICC cache: {e}")
        return False


def _synthetic_image_for_icc(depth: int) -> np.ndarray:
    """Create a small neutral RGB gradient image with no true black values.

    All channels are identical so that a well-behaved ICC round-trip preserves
    the mean value.  Color shifts caused by the ICC would otherwise make the
    simple mean-ratio check unreliable.
    """
    size = _CAU_TEST_SIZE
    if depth == 8:
        # 8-bit values from 128 to 255
        base = np.linspace(128, 255, size * size, dtype=np.uint8).reshape(size, size)
    else:
        # 16-bit values from 0x8000 to 0xFFFF
        base = np.linspace(0x8000, 0xFFFF, size * size, dtype=np.uint16).reshape(size, size)
    img = np.stack([base, base, base], axis=2)
    return img


def _cautious_test_icc_depth(icc_bytes: bytes, depth: int) -> bool:
    """Run one round-trip test at the given bit depth. Returns True if safe."""
    img = _synthetic_image_for_icc(depth)
    if depth == 8:
        original_norm = float(img.mean()) / 255.0
    else:
        original_norm = float(img.mean()) / 65535.0

    tmp = Path(tempfile.mkdtemp(prefix="jxl_icc_test_"))
    try:
        # Build a PNG with the ICC embedded in iCCP, matching the real encoder path.
        png_path = tmp / "test.png"
        png_bytes = make_png_bytes(img, icc_bytes=icc_bytes)
        png_path.write_bytes(png_bytes)

        jxl_path = tmp / "test.jxl"
        # Use effort=1 for the test: the goal is to detect severe darkening, not to
        # match the final quality/size.  The current distance is still used because
        # lossy behaviour varies with distance.
        cmd = [
            _get_cjxl_cmd() or "cjxl",
            str(png_path),
            str(jxl_path),
            "-d", str(CJXL_DISTANCE),
            "--effort", "1",
            "--container=1",
        ]
        if CJXL_MODULAR and CJXL_DISTANCE > 0:
            cmd.append("--modular=1")

        r = subprocess.run(cmd, capture_output=True, timeout=120)
        if r.returncode != 0:
            logger.debug(f"Cautious ICC test encode failed at {depth}-bit: {r.stderr.decode(errors='replace')[:200]}")
            return False

        out_png = tmp / "out.png"
        r = subprocess.run(["djxl", str(jxl_path), str(out_png)], capture_output=True, timeout=120)
        if r.returncode != 0 or not out_png.exists():
            logger.debug(f"Cautious ICC test decode failed at {depth}-bit: {r.stderr.decode(errors='replace')[:200]}")
            return False

        decoded = np.array(Image.open(out_png).convert("RGB"))
        decoded_norm = float(decoded.mean()) / 255.0
        ratio = decoded_norm / original_norm if original_norm > 0 else 0.0
        safe = decoded_norm >= (_CAU_MIN_MEAN / 255.0) and ratio >= _CAU_MIN_RATIO
        logger.debug(
            f"Cautious ICC test {depth}-bit: original_norm={original_norm:.3f}, "
            f"decoded_norm={decoded_norm:.3f}, ratio={ratio:.3f}, safe={safe}"
        )
        return safe
    except Exception as e:
        logger.debug(f"Cautious ICC test failed at {depth}-bit: {e}")
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _cautious_should_embed_icc(icc_bytes: bytes, tiff_path: Path) -> bool:
    """Test an ICC profile with a small round-trip before trusting it."""
    with _icc_test_lock:
        cache = _load_icc_cache()
        key = hashlib.sha256(icc_bytes).hexdigest()
        # Include the cjxl version in the key: an encoder upgrade can change
        # how a profile behaves, so stale verdicts from older cjxl builds
        # must not be trusted.
        cjxl_ver = _tool_version(_get_cjxl_cmd() or "cjxl") or (0, 0, 0)
        versioned_key = f"{key}:d={CJXL_DISTANCE}:m={1 if CJXL_MODULAR else 0}:v={'.'.join(map(str, cjxl_ver))}"
        cached = cache.get(versioned_key)
        if cached is not None:
            if isinstance(cached, dict):
                embed = bool(cached.get("embed"))
            else:
                embed = bool(cached)
            logger.debug(f"Cautious ICC: cache hit for {tiff_path.name} -> {'embed' if embed else 'skip'}")
            return embed

        if not _get_cjxl_cmd() or not shutil.which("djxl"):
            logger.warning("cautious ICC strategy requires cjxl and djxl; falling back to heuristic")
            return _should_embed_icc_heuristic(icc_bytes)

        safe = True
        for depth in (8, 16):
            if not _cautious_test_icc_depth(icc_bytes, depth):
                safe = False
                break

        cache[versioned_key] = {"embed": safe, "ts": datetime.now().isoformat()}
        _save_icc_cache(cache)
        logger.info(
            f"Cautious ICC: {'embed' if safe else 'skip'} for {tiff_path.name} "
            f"({len(icc_bytes)} bytes, profile class {_get_icc_profile_class(icc_bytes) or b'?'})"
        )
        return safe


# ─────────────────────────────────────────────
# HEURISTIC ICC STRATEGY
# ─────────────────────────────────────────────

def _should_embed_icc_heuristic(icc_bytes: bytes) -> bool:
    if len(icc_bytes) >= ICC_PROBLEMATIC_SIZE_THRESHOLD:
        logger.debug(f"ICC profile ({len(icc_bytes)} bytes) exceeds size threshold; skipping iCCP")
        return False
    profile_class = _get_icc_profile_class(icc_bytes)
    if profile_class == b"scnr":
        logger.debug("ICC profile class is 'scnr' (scanner); skipping iCCP")
        return False
    return True


def should_embed_icc_in_png(icc_bytes, lossy=True, tiff_path=None):
    """Decide whether the ICC profile should be embedded in the PNG iCCP chunk.

    For lossless encoding the ICC is always embedded because the JXL stores the
    ICC as a native blob and the colors are preserved correctly. For lossy encoding
    the strategy depends on ICC_PNG_STRATEGY.

    Returns True  -> embed ICC in PNG (cjxl will use it for XYB conversion)
    Returns False -> skip iCCP (cjxl treats pixels as generic RGB; ICC is injected
                      into the JXL container via exiftool afterwards)
    """
    if not lossy or not icc_bytes:
        return True

    strategy = ICC_PNG_STRATEGY.lower()
    if strategy == "always":
        return True
    if strategy == "skip":
        return False
    if strategy == "cautious":
        return _cautious_should_embed_icc(icc_bytes, tiff_path or Path("unknown"))

    if strategy == "heuristic":
        return _should_embed_icc_heuristic(icc_bytes)

    # Unknown strategy -> default to safe behavior (embed)
    logger.warning(f"Unknown ICC_PNG_STRATEGY '{ICC_PNG_STRATEGY}', defaulting to embed")
    return True



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
    # Use an argfile (like the other exiftool calls here): paths containing
    # [ ] would be treated as wildcards on the command line, and non-ASCII
    # paths need the UTF-8 charset directive.
    # Correct order: -o output.xmp -b -XMP input.tif
    arg_file = tmp_dir / "xmp_extract.args"
    arg_file.write_text(f"{_ARGFILE_CHARSET}-o\n{xmp_path}\n-b\n-XMP\n{tiff_path}\n", encoding="utf-8")
    subprocess.run(
        [_get_exiftool_cmd(), "-@", str(arg_file)],
        capture_output=True, timeout=60
    )
    if xmp_path.exists() and xmp_path.stat().st_size > 0:
        return xmp_path
    return None

def read_existing_description(xmp_path):
    """Read existing dc:description from XMP file if present.
    Returns empty string if not found."""
    if not xmp_path or not xmp_path.exists():
        return ""
    try:
        r = _run_exiftool_argfile(
            ["-s", "-XMP-dc:Description", str(xmp_path)], timeout=15
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

_INTERNAL_RELATION_PREFIXES = (
    "jxlphoto-mpg:",
    "jxlphoto-subfiletype:",
    "jxlphoto-depth:",
    "jxlphoto-page:",
    "jxlphoto-icc:inherited",
    "jxlphoto-grayscale",
    "jxlphoto-thumb",
)


def read_existing_relation(xmp_path):
    """Read user dc:Relation items from an XMP file via exiftool JSON output,
    filtering out this tool's own internal round-trip markers (jxlphoto-*).
    Returns a list of strings (may be empty)."""
    if not xmp_path or not Path(xmp_path).exists():
        return []
    try:
        r = _run_exiftool_argfile(
            ["-j", "-XMP-dc:Relation", str(xmp_path)], timeout=15
        )
        if r.returncode != 0 or not r.stdout:
            return []
        data = json.loads(r.stdout)
        if not data:
            return []
        rel = data[0].get("Relation")
        if rel is None:
            return []
        values = rel if isinstance(rel, list) else [str(rel)]
        return [str(v).strip() for v in values
                if str(v).strip() and not str(v).strip().startswith(_INTERNAL_RELATION_PREFIXES)]
    except Exception as e:
        logger.debug(f"Failed to read dc:Relation: {e}")
        return []


def read_existing_creator_tool(xmp_path):
    """Read existing CreatorTool from XMP file if present.
    Returns empty string if not found."""
    if not xmp_path or not xmp_path.exists():
        return ""
    try:
        r = _run_exiftool_argfile(
            ["-s", "-XMP-xmp:CreatorTool", str(xmp_path)], timeout=15
        )
        if r.returncode == 0 and r.stdout:
            stdout = r.stdout.strip()
            if " : " in stdout:
                return stdout.split(" : ", 1)[1].strip()
            elif ":" in stdout:
                return stdout.split(":", 1)[1].strip()
    except Exception as e:
        logger.debug(f"Failed to read CreatorTool: {e}")
    return ""

def build_metadata_injection_args(tiff_path, write_path, tmp_dir, exif_bin, icc_bytes, xmp_original, original_depth=16, strip_metadata=False):
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
        # Write args file (UTF-8 charset first so non-ASCII paths work)
        arg_file = tmp_dir / "inject.args"
        arg_file.write_text(_ARGFILE_CHARSET + "\n".join(args_lines) + "\n", encoding="utf-8")
        return arg_file
    
    # 1. Inject raw EXIF binary blob if extracted
    if exif_bin:
        args_lines.append(f"-Exif<={exif_bin}")
    
    # 2. Copy tags from source file (preserves original XMP, EXIF, etc.)
    # NOTE: Orientation is copied like any other tag — this pipeline never
    # rotates pixels (tifffile.asarray() and djxl both keep stored pixel
    # order), so the tag is REQUIRED for correct display of rotated files.
    # (An earlier version stripped it to "prevent double-rotation", which
    # could not happen here and silently de-rotated scans/camera TIFFs.)
    args_lines.append("-tagsfromfile")
    args_lines.append(str(tiff_path))
    args_lines.append("-exif:all")
    args_lines.append("-xmp:all")

    # 2b. The copied XMP may carry STALE internal round-trip markers
    # (jxlphoto-mpg/depth/grayscale/...) from a previous encode. Clear
    # dc:Relation and re-add only the user's own values, otherwise the bag
    # ends up with two group ids / two depths and reconstruction is ambiguous.
    if xmp_original:
        user_relation = read_existing_relation(xmp_original)
    else:
        user_relation = []
    args_lines.append("-XMP-dc:Relation=")
    for rel_value in user_relation:
        args_lines.append(f"-XMP-dc:Relation+={_argfile_safe(rel_value)}")
    
    # 3. Handle encoding parameters and ICC embedding in XMP
    encoding_desc = f"cjxl d={CJXL_DISTANCE} e={CJXL_EFFORT}"
    
    # Read existing description from original XMP if available
    existing_desc = ""
    if xmp_original:
        existing_desc = read_existing_description(xmp_original)
    
    # Build final dc:Description (concatenate if original exists)
    if ENCODE_TAG_MODE == "xmp":
        if existing_desc and existing_desc != encoding_desc and encoding_desc not in existing_desc:
            # Concatenate: original | encoding_params (skip if already tagged,
            # e.g. re-encoding a TIFF produced by the decoder)
            final_description = f"{existing_desc} | {encoding_desc}"
        elif existing_desc:
            final_description = existing_desc
        else:
            final_description = encoding_desc

        # Set dc:Description with concatenated content
        args_lines.append(f"-xmp-dc:Description={_argfile_safe(final_description)}")

    elif ENCODE_TAG_MODE == "software":
        # For software mode, we don't modify dc:Description
        # Instead, we update the EXIF Software field
        sw_arg = tmp_dir / "sw_read.args"
        sw_arg.write_text(f"{_ARGFILE_CHARSET}-s\n-s\n-s\n-Software\n{tiff_path}\n", encoding="utf-8")
        r_sw = subprocess.run([_get_exiftool_cmd(), "-@", str(sw_arg)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        original_sw = r_sw.stdout.strip() if r_sw.returncode == 0 and r_sw.stdout else "cjxl"
        new_sw = f"{original_sw} | {encoding_desc}"
        args_lines.append(f"-Software={_argfile_safe(new_sw)}")
    
    # Always store original bit depth in dc:Relation so the decoder can restore
    # the original BitsPerSample per page according to --depth-policy.
    args_lines.append(f"-XMP-dc:Relation+={DEPTH_XMP_PREFIX}{original_depth}")
    
    # 4. Embed ICC in XMP CreatorTool if enabled (for round-trip preservation)
    # This operates independently of ENCODE_TAG_MODE
    if EMBED_ICC_IN_JXL and icc_bytes:
        icc_b64 = base64.b64encode(icc_bytes).decode('ascii')
        
        # Read existing CreatorTool to concatenate if present
        existing_creator = ""
        if xmp_original:
            existing_creator = read_existing_creator_tool(xmp_original)
            # Strip any stale ICC:<base64> blob (e.g. from a previous encode
            # that skipped cleanup): the decoder extracts the FIRST valid ICC
            # segment, so an old blob would shadow the new one.
            if existing_creator and "ICC:" in existing_creator:
                # Blob is bounded: base64 chars only up to the next pipe or EOL,
                # so a trailing " | Real App" segment is never eaten.
                existing_creator = re.sub(r'ICC:[A-Za-z0-9+/=]+(?=\s*(\||$))', '', existing_creator, flags=re.MULTILINE).strip()
                if 'ICC:' in existing_creator and '|' not in existing_creator:
                    # No pipe separators: the lookahead could not fire, but a
                    # long base64 blob is unambiguous (real words are never
                    # 64+ base64 chars).
                    existing_creator = re.sub(r'ICC:[A-Za-z0-9+/=]{64,}', '', existing_creator).strip()
                existing_creator = re.sub(r'\s*\|\s*$', '', existing_creator).strip()
                existing_creator = re.sub(r'^\s*\|\s*', '', existing_creator).strip()
                existing_creator = re.sub(r'\s*\|\s*\|\s*', ' | ', existing_creator)

        # Build CreatorTool content: existing | ICC:base64 or just ICC:base64
        if existing_creator:
            creator_tool = f"{existing_creator} | ICC:{icc_b64}"
        else:
            creator_tool = f"ICC:{icc_b64}"

        args_lines.append(f"-xmp-xmp:CreatorTool={_argfile_safe(creator_tool)}")
    
    # 5. Cleanup legacy ICC markers from XMP if requested
    if CLEANUP_XMP_ICC_MARKER:
        # Remove common legacy ICC marker tags that might conflict
        args_lines.append("-xmp-icc:all=")  # Clear any XMP ICC tags if present
        args_lines.append("-xmp-photoshop:ICCProfile=")  # Clear Photoshop ICC refs if any
    
    # 6. Ensure byte order consistency
    args_lines.append("-ExifByteOrder=Little-endian")
    
    # 7. Target file
    args_lines.append(str(write_path))

    # Write args file (UTF-8 charset first so non-ASCII paths work)
    arg_file = tmp_dir / "inject.args"
    arg_file.write_text(_ARGFILE_CHARSET + "\n".join(args_lines) + "\n", encoding="utf-8")
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
    elif c == 2:
        color_type = 4  # gray + alpha (LA)
    elif c == 3:
        color_type = 2
    elif c == 4:
        color_type = 6
    else:
        raise ValueError(f"Unsupported channel count: {c}. Expected 1, 2 (LA), 3, or 4.")

    def chunk(name, data):
        p = name + data
        return struct.pack(">I", len(data)) + p + struct.pack(">I", zlib.crc32(p) & 0xFFFFFFFF)

    # Write PNG at the input bit depth. The main encode path passes uint16 data;
    # the cautious ICC test passes uint8 data and needs a true 8-bit PNG to test
    # the 8-bit path.
    if img.dtype == np.uint8:
        bit_depth = 8
        img_be = img
        raw = b"".join(b"\x00" + row.tobytes() for row in img_be)
    else:
        bit_depth = 16
        img_be = img.astype(">u2")
        raw = b"".join(b"\x00" + row.tobytes() for row in img_be)

    out = b"\x89PNG\r\n\x1a\n"
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, bit_depth, color_type, 0, 0, 0))
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

    # Bare codestream has no boxes to reorder; leave as-is.
    if data[:2] == b'\xff\x0a':
        return

    boxes = []

    i = 0
    MAX_BOX_SIZE = min(file_size, MAX_JXL_SIZE)
    
    while i < file_size:
        if i + 8 > file_size:
            # Do NOT rewrite the file with only the parsed boxes — that would
            # silently drop the trailing bytes and turn a file that fails the
            # integrity gate into one that passes it (this runs BEFORE
            # _verify_jxl_integrity, and mode 8 deletes the source on pass).
            raise RuntimeError(f"Truncated box header at offset {i}: {file_size - i} trailing byte(s)")

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
    META_ORDER = {b"JXL ", b"ftyp", b"jxll"}       # required structure first
    META_EXTRA = {b"Exif", b"xml ", b"jbrd", b"brob"}  # metadata before codestream
    # brob is the Brotli-compressed metadata box libjxl/exiftool may write;
    # leaving it after the codestream is exactly the bug this function exists
    # to fix (IrfanView stops reading at the codestream).

    # Group by type preserving appearance order (important for multiple jxlp)
    meta_order_boxes  = []
    meta_extra_boxes  = []
    codestream_boxes  = []
    other_boxes       = []

    for name, h, p in boxes:
        if name in META_ORDER:
            meta_order_boxes.append((name, h, p))
        elif name in META_EXTRA:
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

# --- Space preflight (advisory) -------------------------------------------
# Measured, not guessed. Encoding a few small crops of the batch's OWN files
# answers the only unknown in the projection; everything else (the file list,
# each file's size, which destination folder each output lands in, and the fact
# that staging drains per folder) is already known before the pool starts.
#
# Why crops of real files, and not a synthetic probe or a fixed ratio table:
# measured across 130 full-size encodes of real 16-bit photos at 13 distances,
#   * a 2048 crop reproduces its own full file's ratio to within 3% at every
#     distance from 0 to 1.0 -- so the sample can be cheap;
#   * a synthetic image matched real photos only by coincidence, inside one
#     narrow distance band, and was off by 4x outside it;
#   * fitting one curve on one photo and rescaling it to another broke on 1 of
#     4 photos (38% error), so there is no universal shape to lean on either.
# Compression is content-dependent; nothing substitutes for encoding the
# actual content.
_SAMPLE_CROP = 2048
_SAMPLE_COUNT = 3
# Below this a run cannot plausibly fill a disk, and a ~15s probe would be a
# visible tax on a job that only takes a couple of minutes.
_PREFLIGHT_MIN_BYTES = 5 * 1024 ** 3
# cjxl clamps every distance at or below this to the same value: --distance
# 0.005 and 0.05 were measured producing BYTE-IDENTICAL output. Projecting from
# a requested 0.01 would therefore model a file that cjxl will never write.
_MIN_EFFECTIVE_DISTANCE = 0.05
# TIFF compression tag -> what tifffile needs to reproduce it. The crop must be
# stored the way the source is, because the ratio's denominator is a TIFF size:
# the same pixels as Deflate vs uncompressed shift the ratio measurably.
_TIFF_COMP_WRITE = {1: None, 5: "lzw", 8: "zlib", 32946: "zlib"}


def _sample_one_ratio(tiff_path: Path, distance: float, effort: int):
    """jxl_bytes / tiff_bytes for a centre crop of one real file, or None."""
    try:
        with tifffile.TiffFile(str(tiff_path)) as tf:
            page = tf.pages[0]
            comp = page.compression
            comp = comp.value if hasattr(comp, "value") else int(comp)
            if comp not in _TIFF_COMP_WRITE:
                return None  # unknown container; the denominator would not mean anything
            img = page.asarray()
    except Exception:
        return None

    if img.ndim < 2 or img.dtype.kind == 'f' or img.dtype not in (np.uint8, np.uint16):
        return None
    h, w = img.shape[:2]
    n = min(_SAMPLE_CROP, h, w)
    y, x = (h - n) // 2, (w - n) // 2
    crop = np.ascontiguousarray(img[y:y + n, x:x + n])
    del img

    tmp = Path(tempfile.mkdtemp(prefix="jxl_preflight_", dir=TEMP_DIR))
    try:
        crop_tif = tmp / "s.tif"
        kw = {"photometric": "rgb" if (crop.ndim == 3 and crop.shape[2] >= 3) else "minisblack"}
        if _TIFF_COMP_WRITE[comp]:
            kw["compression"] = _TIFF_COMP_WRITE[comp]
        tifffile.imwrite(str(crop_tif), crop, **kw)

        png = tmp / "s.png"
        png.write_bytes(make_png_bytes(crop if crop.dtype == np.uint16
                                       else crop.astype(np.uint16) * 257))
        jxl = tmp / "s.jxl"
        cmd = [_get_cjxl_cmd() or "cjxl", str(png), str(jxl),
               "-d", str(distance), "--effort", str(effort), "--container=1"]
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        if r.returncode != 0 or not jxl.exists():
            return None
        den = crop_tif.stat().st_size
        return (jxl.stat().st_size / den) if den else None
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _measure_batch_ratio(tiffs, distance: float, effort: int):
    """Worst observed jxl/tiff ratio over a few samples spanning the size range.

    The WORST, never the mean: photos in one batch spread 1.1x-2.1x apart
    depending on distance, and an estimate that under-promises space is worse
    than none. Samples are picked across the size distribution so one unusual
    file cannot define the run.
    """
    uniq = sorted({Path(t) for t in tiffs}, key=lambda p: p.stat().st_size)
    if not uniq:
        return None, []
    if len(uniq) <= _SAMPLE_COUNT:
        picks = uniq
    else:
        idx = [int(round(i * (len(uniq) - 1) / (_SAMPLE_COUNT - 1)))
               for i in range(_SAMPLE_COUNT)]
        picks = [uniq[i] for i in sorted(set(idx))]

    out = []
    for p in picks:
        r = _sample_one_ratio(p, distance, effort)
        if r is not None:
            out.append((p.name, r))
    if not out:
        return None, []
    return max(r for _, r in out), out


def _preflight_space(groups: Dict[Path, list], distance: float, effort: int,
                     staging_dir) -> None:
    """Warn -- never block -- when the projected output will not fit.

    Advisory on purpose. The estimate is measured but still an estimate, and a
    projection that REFUSED a run would eventually refuse one that fits. What
    stops a run stays evidence-based (see the disk-full abort): a real failure
    on a volume that is really full.
    """
    per_group = {}
    for dest, items in groups.items():
        # Unique sources: a split multi-page TIFF appears once per page, but
        # its bytes only exist once.
        srcs = {}
        for t, _j, _pi, _th, _sf, _sp in items:
            try:
                srcs[str(t)] = Path(t).stat().st_size
            except OSError:
                pass
        per_group[dest] = sum(srcs.values())
    total_in = sum(per_group.values())
    if total_in < _PREFLIGHT_MIN_BYTES:
        return

    effective = max(distance, _MIN_EFFECTIVE_DISTANCE) if distance > 0 else 0.0
    logger.info(f"Preflight: measuring compression on {_SAMPLE_COUNT} sample crop(s)...")
    ratio, samples = _measure_batch_ratio(
        [t for items in groups.values() for t, *_ in items], effective, effort)
    if ratio is None:
        logger.info("Preflight: could not measure a ratio; skipping the space estimate.")
        return
    detail = ", ".join(f"{n}: {r*100:.0f}%" for n, r in samples)
    logger.info(f"Preflight: worst measured ratio {ratio*100:.0f}% of source ({detail})")

    def _volume(p):
        try:
            rp = Path(p).resolve()
        except OSError:
            rp = Path(p)
        return (os.path.splitdrive(str(rp))[0] or str(rp.anchor) or str(rp)).upper()

    ordered = sorted(per_group.values(), reverse=True)
    staging_vol = _volume(staging_dir) if staging_dir else None

    # Staging is the headline, and usually the only line printed. It is the
    # drive nobody watches -- typically a small scratch SSD -- and the one
    # whose peak is not obvious: it holds a destination folder's output until
    # that folder's LAST file lands, and the pool deliberately no longer drains
    # at folder boundaries, so two folders can accumulate at once. Not the sum
    # of all of them (that would refuse runs that fit), not just the largest.
    if staging_dir:
        peak = sum(ordered[:2]) * ratio
        try:
            free = shutil.disk_usage(str(staging_dir)).free
            verdict = "OK" if free > peak else "NOT ENOUGH"
            line = (f"Preflight: staging {staging_dir} -- {_fmt_size(free)} free, "
                    f"peak ~{_fmt_size(peak)} ({verdict})")
            logger.warning(line) if free <= peak else logger.info(line)
        except OSError:
            pass

    # Destination volumes accumulate for the whole run, so there the sum IS the
    # number. Split by volume: two output folders can live on two drives.
    #
    # Reported far more quietly than staging, on purpose. It is the drive the
    # user chose and watches, and when it shares a volume with staging the two
    # lines showed the same free space twice while meaning different things
    # (a peak vs a total), which read as a duplicate. On a shared volume the
    # staged file is MOVED, not copied -- same-filesystem rename, no extra
    # space -- so the destination total already covers it. Only speak up when
    # it is a different drive, when there is no staging at all, or when the
    # answer is bad.
    by_vol = {}
    for dest, in_bytes in per_group.items():
        by_vol.setdefault(_volume(dest), [Path(dest), 0])[1] += in_bytes
    for vol, (probe, in_bytes) in by_vol.items():
        need = in_bytes * ratio
        try:
            free = shutil.disk_usage(str(probe)).free
        except OSError:
            continue
        tight = free <= need
        if not tight and staging_vol is not None and vol == staging_vol:
            continue
        line = (f"Preflight: destination {vol or probe} -- {_fmt_size(free)} free, "
                f"needs ~{_fmt_size(need)} ({'NOT ENOUGH' if tight else 'OK'})")
        logger.warning(line) if tight else logger.info(line)


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
    # The pool submits every task up front, so a run that has given up cannot
    # stop scheduling — it stops HERE instead. Queued files return untouched
    # and are reported as "not attempted", never as errors: nothing was tried,
    # so nothing failed.
    _why = _aborted()
    if _why:
        return ((str(tiff_path), page_idx), "aborted", _why, None)

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
            try:
                tiff_mtime = tiff_path.stat().st_mtime
                jxl_mtime  = final_path.stat().st_mtime
            except OSError:
                # Source or destination vanished mid-run (TOCTOU): treat as
                # stale and attempt the conversion instead of crashing.
                tiff_mtime, jxl_mtime = 1, 0
            if tiff_mtime <= jxl_mtime:
                n, total = next_count()
                logger.info(f"[{n}/{total}] SKIP (sync: JXL up to date) | {tiff_path.name}")
                return ((str(tiff_path), page_idx), "skipped", str(final_path), None)
            logger.info(f"  >SYNC: TIFF newer than JXL, reconverting | {tiff_path.name}")

    try:
        write_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # Another thread may have created it

    # Tracks whether this run has started writing to write_path. Used by the
    # error handler: a partial/corrupt output produced by THIS run is deleted
    # (so smart-sync can't mistake it for an up-to-date file), but a
    # pre-existing JXL is never touched when we failed before writing.
    output_dirty = False

    # Identity of a pre-existing output (non-staging only). The error handler
    # compares against this: a file whose identity is UNCHANGED was never
    # touched by this run (e.g. cjxl failed at startup) and must be kept.
    _pre_identity = None
    if write_path == final_path and final_path.exists():
        try:
            _st = final_path.stat()
            _pre_identity = (_st.st_mtime_ns, _st.st_size)
        except OSError:
            pass

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
                # Reject unsupported TIFFs early with clear messages.
                photometric = page.photometric
                if photometric == tifffile.PHOTOMETRIC.SEPARATED:
                    _log_rejected_file(str(tiff_path), "CMYK not supported")
                    raise ValueError("CMYK TIFFs are not supported")
                if photometric == tifffile.PHOTOMETRIC.PALETTE:
                    _log_rejected_file(str(tiff_path), "palette-color not supported")
                    raise ValueError("Palette-color TIFFs are not supported. Convert to RGB first.")
                # Planar-separate TIFFs return (samples, H, W) from asarray(),
                # which make_png_bytes would misread as (H, W, C) — reject
                # clearly instead of scrambling the image.
                planar = getattr(page, 'planarconfig', None)
                if planar is not None and int(planar) != 1:
                    _log_rejected_file(str(tiff_path), "planar-separate not supported")
                    raise ValueError("Planar-separate TIFFs (PLANARCONFIG=2) are not supported. Convert to chunky/contig first.")
                spp = int(page.samplesperpixel) if page.samplesperpixel else 1
                if spp not in (1, 2, 3, 4):
                    _log_rejected_file(str(tiff_path), f"unsupported samples-per-pixel: {spp}")
                    raise ValueError(f"Unsupported channel count: {spp}. Expected 1 (gray), 2 (gray+alpha), 3 (RGB) or 4 (RGBA).")
                icc_original, icc_inherited = get_page_icc(tif, page_idx)
                img = page.asarray()
                # Reject float/double and other unsupported integer dtypes before casting.
                if img.dtype.kind == 'f':
                    _log_rejected_file(str(tiff_path), f"float dtype {img.dtype} not supported")
                    raise ValueError(f"Unsupported TIFF dtype: {img.dtype}. Float TIFFs are not supported.")
                if img.dtype not in (np.uint8, np.uint16):
                    _log_rejected_file(str(tiff_path), f"dtype {img.dtype} not supported")
                    raise ValueError(f"Unsupported TIFF dtype: {img.dtype}. Expected uint8 or uint16.")
                # Capture original bit depth before converting to 16-bit for the JXL pipeline.
                original_depth = 8 if img.dtype == np.uint8 else 16
                # Convert 8-bit to 16-bit with proper scaling (multiply by 257)
                if img.dtype == np.uint8:
                    img = img.astype(np.uint16) * 257  # 0-255 -> 0-65535
                else:
                    img = img.astype(np.uint16)
            # Grayscale detection is driven ONLY by the ACTUAL array: a 2D
            # array or a single-channel 3D array is grayscale; a 2-channel
            # array is gray+alpha (LA). The planning-time samples-per-pixel is
            # deliberately NOT consulted — if metadata said 1 channel but the
            # pixels are RGB, flagging the JXL as grayscale would make the
            # decoder discard G and B.
            is_grayscale = (img.ndim == 2) or (img.ndim == 3 and img.shape[2] in (1, 2))
            if img.ndim == 2:
                img = img[:, :, np.newaxis]

            # If a page inherited its ICC from IFD0 but is actually grayscale, do not
            # apply the inherited RGB ICC. Keep icc_inherited=True so the decoder knows
            # the original page had no own ICC tag and must not write one on restore.
            if icc_inherited and is_grayscale:
                icc_original = None
                icc_inherited = True
                icc_bytes = None
            else:
                # D50 policy/counters only when the ICC is actually used —
                # discarded (grayscale-inherited) profiles must not inflate
                # the "D50 patch applied" summary.
                icc_bytes = apply_d50_policy(icc_original, tiff_path)

            # 5. Encode PNG with optional ICC in iCCP chunk (for cjxl encoding)
            # --container=1 is required for lossy JXL (d>0): without it, cjxl outputs a raw
            # codestream and exiftool cannot inject EXIF. Do NOT use for lossless (d=0):
            # it changes how the ICC is stored (blob instead of native primaries) and
            # breaks color display in IrfanView.
            #
            # Lossy caveat: cjxl can darken/scramble images when the PNG iCCP chunk
            # carries large scanner profiles (e.g. SilverFast SFprofT). For those cases
            # (controlled by ICC_PNG_STRATEGY) we skip the iCCP chunk and rely on
            # exiftool to inject the ICC into the JXL container. The original pixels are
            # preserved, and the ICC is restored into the output TIFF by the decoder.
            container_flag = ["--container=1"] if CJXL_DISTANCE > 0 else []

            # --modular=1 forces the Modular encoder for lossy output.
            # Only applied when CJXL_MODULAR=True and d>0 (lossless always uses Modular).
            # Modular lossy produces 2-3x larger files than VarDCT for photos.
            modular_flag = ["--modular=1"] if (CJXL_MODULAR and CJXL_DISTANCE > 0) else []

            # Decide whether to put the ICC into the PNG iCCP chunk.
            lossy = CJXL_DISTANCE > 0
            if not lossy:
                png_icc_bytes = icc_bytes  # lossless: always embed ICC natively
            else:
                embed = should_embed_icc_in_png(icc_bytes, lossy=True, tiff_path=tiff_path)
                png_icc_bytes = icc_bytes if embed else None
                if not embed and icc_bytes:
                    logger.info(f"  >Skipping ICC in PNG iCCP for {tiff_path.name} (page {page_idx}); profile will be preserved via XMP")

            if USE_RAM_FOR_PNG:
                png_input = make_png_bytes(img, png_icc_bytes)
                del img
                cjxl_cmd = [_get_cjxl_cmd() or "cjxl", "-", str(write_path), "-d", str(CJXL_DISTANCE), "--effort", str(CJXL_EFFORT)] + container_flag + modular_flag + _cjxl_buffering_flag()
                output_dirty = True
                r = subprocess.run(cjxl_cmd, input=png_input, capture_output=True, timeout=CJXL_TIMEOUT)
                del png_input
            else:
                png_path = tmp_dir / f"{tiff_path.stem}.png"
                png_bytes = make_png_bytes(img, png_icc_bytes)
                del img
                png_path.write_bytes(png_bytes)
                del png_bytes
                cjxl_cmd = [_get_cjxl_cmd() or "cjxl", str(png_path), str(write_path), "-d", str(CJXL_DISTANCE), "--effort", str(CJXL_EFFORT)] + container_flag + modular_flag + _cjxl_buffering_flag()
                output_dirty = True
                r = subprocess.run(cjxl_cmd, capture_output=True, timeout=CJXL_TIMEOUT)

            if r.returncode != 0:
                err = (r.stderr or b"").decode(errors='replace')[:200]
                raise RuntimeError(f"cjxl: {err}")

            # 6. Build and execute unified metadata injection (CORRECTED - replaces old steps 5+7)
            # This preserves original XMP, adds encoding tags, and embeds ICC if configured
            # Uses icc_original (unmodified) for round-trip preservation
            inject_args = build_metadata_injection_args(
                tiff_path, write_path, tmp_dir, exif_bin, icc_original, xmp_original,
                original_depth=original_depth,
                strip_metadata=STRIP_METADATA
            )
            
            r2 = subprocess.run([_get_exiftool_cmd(), "-@", str(inject_args)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            if r2.returncode != 0:
                err_msg = (r2.stderr or r2.stdout or "no output")[:300].strip()
                raise RuntimeError(f"exiftool failed: {err_msg}")

            # 7. Embed JPEG thumbnail if enabled
            # 7. Embed JPEG thumbnail if enabled (suppressed under --strip:
            # "strip all metadata" must not leave an EXIF thumbnail behind)
            if EMBED_JPEG_THUMBNAIL and not STRIP_METADATA:
                try:
                    from PIL import Image, ImageCms
                    import io
                    # Apply user's PIL pixel limit setting (for large panoramas)
                    Image.MAX_IMAGE_PIXELS = PIL_MAX_IMAGE_PIXELS
                    # Read the original TIFF to generate thumbnail (use the page being encoded)
                    with Image.open(str(tiff_path)) as img:
                        if page_idx > 0 and page_idx < getattr(img, 'n_frames', 1):
                            img.seek(page_idx)
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
                        
                        # Calculate thumbnail size (max 256px, never upscale)
                        max_size = 256
                        ratio = min(1.0, max_size / img.width, max_size / img.height)
                        new_size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
                        thumb = img.resize(new_size, Image.Resampling.LANCZOS)
                        # Save as JPEG temporary (sRGB, no ICC embedded)
                        thumb_path = tmp_dir / "thumbnail.jpg"
                        thumb.save(str(thumb_path), "JPEG", quality=85)
                        # Inject thumbnail into JXL
                        r_thumb = _run_exiftool_argfile(
                            ["-overwrite_original", "-ThumbnailImage<=" + str(thumb_path), str(write_path)],
                            timeout=30
                        )
                        if r_thumb.returncode == 0:
                            logger.debug(f"  >Embedded sRGB thumbnail ({new_size[0]}x{new_size[1]})")
                        else:
                            logger.debug(f"  >Thumbnail embedding failed (non-critical)")
                except Exception as e:
                    logger.debug(f"  >Thumbnail PIL approach failed: {e}")

                    # Fallback: try tifffile approach (only if PIL AND the
                    # optional names this block needs are actually available —
                    # otherwise we'd die with NameError here and hide the cause)
                    if 'Image' not in locals() or 'ImageCms' not in locals() or 'io' not in locals():
                        logger.debug("  >PIL/ImageCms not available, skipping thumbnail entirely")
                    else:
                        try:
                            # Read TIFF with tifffile (preserves ICC and bit depth)
                            with tifffile.TiffFile(str(tiff_path)) as tif:
                                # Read the page being encoded
                                page = tif.pages[page_idx] if page_idx < len(tif.pages) else tif.pages[0]
                                img_array = page.asarray()
                                # Extract ICC profile
                                icc_profile = None
                                try:
                                    icc_profile = page.icc_profile
                                except Exception:
                                    pass

                            # Convert 16-bit to 8-bit if necessary (rounded,
                            # like the rest of the pipeline)
                            if img_array.dtype == np.uint16:
                                img_8bit = np.rint(img_array / 257).astype(np.uint8)
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

                            # Calculate thumbnail size (max 256px, never upscale)
                            max_size = 256
                            ratio = min(1.0, max_size / pil_img.width, max_size / pil_img.height)
                            new_size = (max(1, int(pil_img.width * ratio)), max(1, int(pil_img.height * ratio)))
                            thumb = pil_img.resize(new_size, Image.Resampling.LANCZOS)

                            # Save as JPEG temporary (sRGB, no ICC embedded)
                            thumb_path = tmp_dir / "thumbnail.jpg"
                            thumb.save(str(thumb_path), "JPEG", quality=85)

                            # Inject thumbnail into JXL
                            r_thumb = _run_exiftool_argfile(
                                ["-overwrite_original", "-ThumbnailImage<=" + str(thumb_path), str(write_path)],
                                timeout=30
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
            # Skipped entirely under --strip (no metadata = no reconstruction markers).
            if multipage_group and not STRIP_METADATA:
                try:
                    relation_args = [
                        "-XMP-dc:Relation+=" + MULTIPAGE_XMP_MARKER + multipage_group,
                        # Page index and thumbnail role are stored as markers,
                        # not inferred from the filename: a source TIFF named
                        # *_page<N> or *_thumbnail would otherwise corrupt the
                        # reconstruction order/naming.
                        "-XMP-dc:Relation+=" + PAGE_XMP_PREFIX + str(page_idx),
                    ]
                    if is_thumbnail:
                        relation_args.append("-XMP-dc:Relation+=" + THUMB_XMP_FLAG)
                    if icc_inherited and page_idx > 0:
                        relation_args.append("-XMP-dc:Relation+=" + ICC_INHERITED_XMP_FLAG)
                    if subfiletype != 0:
                        relation_args.append("-XMP-dc:Relation+=" + SUBFILETYPE_XMP_PREFIX + str(subfiletype))
                    r_mark = _run_exiftool_argfile(
                        ["-overwrite_original"] + relation_args + [str(write_path)],
                        timeout=30
                    )
                    # A failed marker write must not be silent: without the marker
                    # the decoder can never reconstruct this multi-page TIFF.
                    if r_mark.returncode != 0:
                        err_msg = (r_mark.stderr or r_mark.stdout or "no output")[:200]
                        raise RuntimeError(f"exiftool multipage marker write failed: {err_msg.strip()}")
                except RuntimeError:
                    raise
                except Exception as e_mark:
                    raise RuntimeError(f"multipage marker write failed: {e_mark}") from e_mark

            # Grayscale marker: must be written for standalone files too, otherwise
            # read_png_to_numpy returns a 3-channel RGB array and the decoder cannot
            # restore the original single-channel TIFF page.
            if is_grayscale and not STRIP_METADATA:
                try:
                    r_gray = _run_exiftool_argfile(
                        ["-overwrite_original",
                         "-XMP-dc:Relation+=" + GRAYSCALE_XMP_FLAG, str(write_path)],
                        timeout=30
                    )
                    if r_gray.returncode != 0:
                        err_msg = (r_gray.stderr or r_gray.stdout or "no output")[:200]
                        raise RuntimeError(f"exiftool grayscale marker write failed: {err_msg.strip()}")
                except RuntimeError:
                    raise
                except Exception as e_mark:
                    raise RuntimeError(f"grayscale marker write failed: {e_mark}") from e_mark

            # Reorder JXL boxes so Exif/XMP come before the codestream. This must
            # run after all exiftool operations (metadata, thumbnail, markers)
            # because each exiftool edit can re-append boxes after the codestream.
            reorder_jxl_boxes(write_path)

            # Validate EVERY successful output, not just mode-8 delete gates:
            # cjxl returning 0 does not guarantee a well-formed file (disk
            # full, AV truncation, killed mid-write). A corrupt output marked
            # OK would be skipped forever by the next smart-sync run.
            # (The except handler deletes it via output_dirty.)
            if not _verify_jxl_integrity(write_path):
                raise RuntimeError("cjxl returned 0 but the output failed the JXL integrity check")

            n, total = next_count()
            status = "overwrite" if overwritten else "ok"
            label  = "OVERWRITE" if overwritten else "OK"
            page_label = f" page{page_idx}" if page_idx > 0 else ""
            thumb_label = " [thumbnail]" if is_thumbnail else ""
            logger.info(f"[{n}/{total}] {label}{page_label}{thumb_label} | {tiff_path.name} -> {final_path}")
            return ((str(tiff_path), page_idx), status, str(final_path), tiff_path)

        except Exception as e:
            # Remove any partial/corrupt output produced by THIS run so the
            # next smart-sync run does not mistake it for a fresh, up-to-date
            # JXL and skip it forever. The delete only happens when THIS run
            # actually wrote (staging UUID file, or the on-disk identity
            # changed): a pre-existing JXL that cjxl never touched (e.g. it
            # failed at startup with rc!=0) is KEPT.
            if output_dirty and write_path != final_path:
                try:
                    if write_path.exists():
                        write_path.unlink()
                except OSError:
                    pass
            elif output_dirty and _pre_identity is None:
                # No pre-existing file: anything at write_path is this run's partial
                try:
                    if write_path.exists():
                        write_path.unlink()
                except OSError:
                    pass
            elif output_dirty and _pre_identity is not None:
                try:
                    _st = write_path.stat()
                    if (_st.st_mtime_ns, _st.st_size) != _pre_identity:
                        write_path.unlink()
                except OSError:
                    pass
            # Was the disk the real cause? cjxl exits 0 while writing a
            # truncated file when the volume is full, so this arrives as an
            # integrity failure with nothing pointing at the drive. Checked
            # only here, on a path that has already failed, so a healthy run
            # never pays for the stat.
            try:
                _need = tiff_path.stat().st_size
            except OSError:
                _need = 0
            _abort_if_disk_full(write_path.parent, _need)

            n, total = next_count()
            page_label = f" page{page_idx}" if page_idx > 0 else ""
            logger.error(f"[{n}/{total}] ERROR{page_label} | {tiff_path.name} | {e}")
            return ((str(tiff_path), page_idx), "error", str(e), None)

class UnreadableTiff(Exception):
    """The TIFF yielded no pages at all — corrupt or truncated.

    Distinct from an empty return, which means "policy asked me to drop this"
    (--multipage-mode skip on a multi-page file, --thumbnail-mode exclude on a
    thumbnail-only file). Both used to be counted as "skipped", so a corrupt
    file was reported as "skipped by multipage policy" — a policy that never
    asked for it. Kept out of the error count so exit codes (and the automation
    reading them) keep their current meaning.
    """


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
    #
    # We still need to determine the real samples-per-pixel of page 0 so that
    # single-channel TIFFs are encoded as grayscale rather than RGB.
    if mp_mode == "ignore":
        final_jxl = output_dir / _page_output_name(stem, 0, False)
        extra_pages = 0
        try:
            with tifffile.TiffFile(str(tiff_path)) as tif:
                samples = int(tif.pages[0].samplesperpixel) if tif.pages[0].samplesperpixel else 1
                # Counting the IFD chain only follows offsets (no pixel decode),
                # and this is the ONLY place that can tell the user pages are
                # being dropped: "ignore" encodes page 0 and discards the rest,
                # which used to happen without a single line of output.
                extra_pages = max(0, len(tif.pages) - 1)
        except Exception:
            # If we cannot read the page, let convert_one report the error later
            # and fall back to RGB to avoid a planning-time crash.
            samples = 3
        if extra_pages:
            with _multipage_ignored_lock:
                _multipage_ignored["files"] += 1
                _multipage_ignored["pages"] += extra_pages
                # NOTE: in "ignore" mode we never opened the pages to classify
                # them, so an extra page may well be just a thumbnail. Deletion
                # is irreversible: treat every unclassified dropped page as real.
                _discarded_real_page_sources.add(os.path.normcase(str(tiff_path)))
                _capped_discard_warning(
                    f"DISCARDING {extra_pages} extra page(s) | {tiff_path.name} | "
                    f"--multipage-mode ignore encodes page 0 only "
                    f"(use split / split_all to keep them)")
        return [(tiff_path, final_jxl, 0, False, 0, samples)]

    real_pages, thumb_pages, page_info = _analyze_tiff_pages(tiff_path)

    pages_to_encode = []

    if mp_mode == "skip":
        if len(real_pages) > 1:
            logger.warning(f"SKIP multi-page TIFF ({len(real_pages)} real pages) | {tiff_path.name}")
            return []
        # Single real page (or only thumbnails) -> encode the correct page
        if len(real_pages) == 1:
            idx = real_pages[0]
            is_thumb = idx in thumb_pages
        else:
            idx = 0
            is_thumb = 0 in thumb_pages
        info = page_info.get(idx, {'subfiletype': 0, 'samples': 3})
        pages_to_encode.append((idx, is_thumb, info['subfiletype'], info['samples']))

    elif mp_mode == "split":
        for idx in real_pages:
            info = page_info.get(idx, {'subfiletype': 0, 'samples': 3})
            pages_to_encode.append((idx, False, info['subfiletype'], info['samples']))
        if THUMBNAIL_MODE.lower() == "include":
            for idx in thumb_pages:
                info = page_info.get(idx, {'subfiletype': 1, 'samples': 3})
                pages_to_encode.append((idx, True, info['subfiletype'], info['samples']))
        elif thumb_pages:
            # Dropping thumbnails here is what --thumbnail-mode exclude ASKED
            # for, so it is reported once in the summary rather than once per
            # file (--warn-thumbnail-discard brings the per-file lines back).
            # Counted apart from _multipage_ignored so the summary can name the
            # right setting.
            with _multipage_ignored_lock:
                _thumbnails_dropped["files"] += 1
                _thumbnails_dropped["pages"] += len(thumb_pages)
                _discarded_thumb_sources.add(os.path.normcase(str(tiff_path)))
                if WARN_DISCARDED_THUMBNAILS:
                    _capped_discard_warning(
                        f"DISCARDING {len(thumb_pages)} thumbnail page(s) | {tiff_path.name} | "
                        f"--thumbnail-mode exclude (use include to keep them)")
        if not pages_to_encode:
            if not real_pages and not thumb_pages:
                raise UnreadableTiff("no readable pages (corrupt or truncated TIFF)")
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
            # split_all encodes every page there is, so an empty result can only
            # mean the file has none — always a broken file, never a policy skip.
            raise UnreadableTiff("no readable pages (corrupt or truncated TIFF)")

    else:
        # Unknown mode, fall back to ignore
        info = page_info.get(0, {'subfiletype': 0, 'samples': 3})
        pages_to_encode.append((0, 0 in thumb_pages, info['subfiletype'], info['samples']))
        if len(real_pages) > 1:
            # Same rule as the real "ignore" path: pages are being dropped, so
            # mode 8 must not delete this source.
            with _multipage_ignored_lock:
                _discarded_real_page_sources.add(os.path.normcase(str(tiff_path)))

    # Resolve final output path for each page
    results = []
    # When the file yields exactly one non-thumbnail output, use the plain
    # stem (photo.jxl) even if that page sits at an index > 0 — consistent
    # with ignore mode and the documented examples.
    single_output = len(pages_to_encode) == 1 and not pages_to_encode[0][1]
    for page_idx, is_thumbnail, subfiletype, samples in pages_to_encode:
        if single_output:
            name = f"{stem}.jxl"
        else:
            name = _page_output_name(stem, page_idx, is_thumbnail)
        final_jxl = output_dir / name
        results.append((tiff_path, final_jxl, page_idx, is_thumbnail, subfiletype, samples))

    return results

def process_group(group_items: list, workers: int, mode: int = 0):
    """
    Converts (tiff, final_jxl, page_idx, is_thumbnail, subfiletype, samples) items
    in parallel. If TEMP2_DIR is set, writes to staging first then moves in bulk.

    Called ONCE with every planned item. It used to be called once per output
    FOLDER, which quietly throttled the whole run: a folder holding fewer files
    than `workers` could never fill the pool, and the pool had to drain at every
    folder boundary. On a photo library — modes 3/5/6/7 create one output folder
    per shoot — that turned 12 workers into roughly one. Measured on 8 real
    45 MP TIFFs with 8 workers: 10s in a single folder vs 33s spread over eight
    folders (47s fully serial).

    The staging move stays per-folder and still runs in bulk: each folder's files
    are moved as soon as that folder's last item finishes, so staging holds only
    the folders still in flight rather than the entire batch.
    """
    use_staging = TEMP2_DIR is not None
    staging_dir = Path(TEMP2_DIR) if use_staging else None

    if use_staging:
        staging_dir.mkdir(parents=True, exist_ok=True)

    # A source TIFF that yields more than one output is a genuine split; every
    # page from it gets a stable group marker so the decoder can safely rejoin
    # them. Single-output TIFFs get no marker (standalone).
    # The group id is a hash of the absolute path to avoid leaking folder
    # structure / user names into distributed JXL files.
    outputs_per_tiff: Dict[str, int] = {}
    for tiff, _final_jxl, _page_idx, _is_thumbnail, _subfiletype, _samples in group_items:
        outputs_per_tiff[str(tiff.resolve())] = outputs_per_tiff.get(str(tiff.resolve()), 0) + 1

    def _make_group_id(tiff_path: Path) -> str:
        key = str(tiff_path.resolve()).encode("utf-8")
        return hashlib.sha256(key).hexdigest()[:16]

    tasks = []
    for tiff, final_jxl, page_idx, is_thumbnail, subfiletype, samples in group_items:
        if use_staging:
            # Unique staging name to avoid collisions across different source folders/pages
            write_jxl = staging_dir / f"{uuid.uuid4().hex}_{tiff.stem}_p{page_idx}.jxl"
        else:
            write_jxl = final_jxl
        tiff_key = str(tiff.resolve())
        group_id = _make_group_id(tiff) if outputs_per_tiff.get(tiff_key, 0) > 1 else None
        tasks.append((tiff, write_jxl, final_jxl, page_idx, is_thumbnail, subfiletype, samples, group_id))

    moved_finals = set()

    def _move_dest_from_staging(dest_tasks: list, status_map: Dict):
        """Bulk-move one destination folder's outputs out of staging."""
        moved = 0
        for tiff, write_jxl, final_jxl, page_idx, _, _, _, _ in dest_tasks:
            status = status_map.get((str(tiff), page_idx), "error")
            if status not in ("ok", "overwrite"):
                # "aborted" is as silent as "skipped": nothing was written, so
                # there is no partial output to report — and one line per
                # never-attempted file is precisely the wall of noise the
                # abort exists to prevent. The count is in the summary.
                if status not in ("skipped", "aborted"):
                    if write_jxl.exists():
                        logger.warning(f"  KEEP in staging ({status}) | {write_jxl.name}")
                    else:
                        # Partial output was already discarded by the error handler
                        logger.warning(f"  Partial output discarded ({status}) | {tiff.name}")
                continue
            if not write_jxl.exists():
                logger.warning(f"  KEEP (staging file missing) | {write_jxl.name}")
                continue
            try:
                final_jxl.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(write_jxl), str(final_jxl))
                moved += 1
                moved_finals.add(os.path.normcase(str(final_jxl)))
            except OSError as e:
                # A locked/readonly destination must not abort the whole batch:
                # keep the file in staging and log it for manual recovery.
                logger.error(f"  MOVE FAILED, kept in staging | {write_jxl.name} -> {final_jxl} | {e}")
        if moved:
            logger.info(f"  -> Moved {moved} file(s) from staging to {dest_tasks[0][2].parent}")

    # Per-destination bookkeeping so a folder can be flushed the moment its last
    # item lands, instead of stalling the pool at every folder boundary.
    tasks_by_dest: Dict[Path, list] = {}
    pending_by_dest: Dict[Path, int] = {}
    for task in tasks:
        dest = task[2].parent
        tasks_by_dest.setdefault(dest, []).append(task)
        pending_by_dest[dest] = pending_by_dest.get(dest, 0) + 1

    results = []
    status_map: Dict = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(convert_one, t, w, f, p, th, sft, spl, g): (t, w, f, p, th, sft, spl, g)
                   for t, w, f, p, th, sft, spl, g in tasks}
        for fut in as_completed(futures):
            task = futures[fut]
            try:
                result = fut.result()
            except Exception as e:
                # An exception escaped convert_one entirely (e.g. temp-dir
                # failure, source vanished before the sync check). One bad
                # file must not kill the whole batch.
                n, total = next_count()
                logger.error(f"[{n}/{total}] ERROR | {task[0].name} | {e}")
                result = ((str(task[0]), task[3]), "error", str(e), None)
            results.append(result)
            status_map[result[0]] = result[1]

            # Flush this destination folder once every one of its items is done.
            # Runs on the main thread (inside the as_completed loop), so no two
            # moves ever race for the same file.
            dest = task[2].parent
            pending_by_dest[dest] -= 1
            if use_staging and pending_by_dest[dest] == 0:
                _move_dest_from_staging(tasks_by_dest[dest], status_map)

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

        # Index tasks by final path ONCE (the old nested loop was O(n^2)
        # and rebuilt the result set for every source TIFF).
        by_final = {}
        for _, _, final_jxl, _, _, _, _, _ in tasks:
            by_final[str(final_jxl)] = final_jxl

        for tiff_key, tiff_results in results_by_tiff.items():
            # Every page must have freshly succeeded. Skipped pages (r[3] is None)
            # intentionally block deletion: if a page was skipped we can't be sure
            # this run produced/verified it, so we keep the source rather than risk
            # deleting a TIFF whose JXLs weren't all (re)written this pass.
            all_ok = all(r[1] in ("ok", "overwrite") and r[3] is not None for r in tiff_results)
            if not all_ok:
                logger.warning(f"  KEEP source (not all pages succeeded) | {Path(tiff_key).name}")
                continue

            # Pages this run chose NOT to encode. Every downstream check passes
            # in this case — the JXL that was written is valid and complete —
            # so this is the only place that can stop the deletion. Fail closed:
            # a page that exists only in the source must keep the source alive.
            if os.path.normcase(tiff_key) in _discarded_real_page_sources:
                logger.warning(
                    f"  KEEP source (pages were discarded, deleting would lose them) | "
                    f"{Path(tiff_key).name} | re-run with --multipage-mode split to encode "
                    f"every page, then delete")
                continue

            # All final JXLs must exist and pass integrity check
            can_delete = True
            for expected in {r[2] for r in tiff_results}:
                final_jxl = by_final.get(expected)
                if final_jxl is None:
                    # No task matched this result's final path (path-format
                    # divergence would be the cause). Deletion is irreversible:
                    # an unverifiable page must block it, never wave it through.
                    can_delete = False
                    break
                if not Path(final_jxl).exists():
                    can_delete = False
                    break
                # A staged page whose move FAILED leaves a stale pre-existing
                # JXL at the final path: it passes exists()+integrity below,
                # but it is not the file this run wrote and verified. The
                # delete gate must certify THIS run's output, not whatever
                # was already sitting there.
                if use_staging and os.path.normcase(str(final_jxl)) not in moved_finals:
                    can_delete = False
                    break
                if not _verify_jxl_integrity(Path(final_jxl)):
                    can_delete = False
                    break

            if not can_delete:
                logger.warning(f"  KEEP source (JXL integrity check failed) | {Path(tiff_key).name}")
                continue

            src_tiff = Path(tiff_key)
            try:
                src_tiff.unlink()
                deleted += 1
                # A dropped thumbnail does not block the delete (it is a reduced
                # copy of a page that IS in the output), but the decoded TIFF
                # will not have that preview page back — say so at delete time.
                thumb_note = (" (embedded thumbnail page not encoded)"
                              if os.path.normcase(tiff_key) in _discarded_thumb_sources else "")
                logger.info(f"  DELETED source | {src_tiff.name}{thumb_note}")
            except OSError as e:
                logger.warning(f"  KEEP (could not delete source) | {src_tiff.name}: {e}")
        if deleted:
            logger.info(f"  -> Deleted {deleted} source TIFF(s)")

    return results

# Matched case-insensitively against f.suffix.lower(). Globbing the four
# spellings ("*.tif", "*.TIF", ...) missed mixed case like "Photo.Tif" on
# case-sensitive filesystems (Linux/macOS) — the file was skipped in silence.
TIFF_EXTS = frozenset({".tif", ".tiff"})


def _iter_tiffs(paths, root=None):
    seen = set()
    files = []
    _scan = _scan_state(root)
    for f in paths:
        _scan_tick(_scan, len(files))
        if f.suffix.lower() not in TIFF_EXTS:
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
    _scan_done(_scan, len(files))
    return sorted(files)


def find_files_mode0(input_path: Path):
    return _iter_tiffs(input_path.glob("*"), input_path)

def find_tiffs_recursive(input_path: Path):
    return _iter_tiffs(input_path.rglob("*"), input_path)

# Default output folder names of the DECODER (jxl_tiff_decoder.py). After a
# decode, those folders live INSIDE the export tree, and modes 6/7 collapse
# the first subfolder level — so scanning them would either collide with the
# original exports (duplicate output -> abort) or silently re-encode decoded
# TIFFs (generational loss at d>0). Only the decoder's names are skipped:
# TIFFs in 16bit/ or any user folder are still found normally.
_DECODER_OUTPUT_FOLDERS = frozenset({"16b_tiff", "tiff_16bits", "converted_tiff"})


def _skip_decoder_output(parts_below_marker) -> bool:
    """True if any part below the export marker is a decoder output folder —
    EXCEPT the one explicitly requested via EXPORT_TIFF_SUBFOLDER: an
    explicit user request always wins over this heuristic."""
    requested = EXPORT_TIFF_SUBFOLDER.lower()
    return any(b in _DECODER_OUTPUT_FOLDERS and b != requested
               for b in parts_below_marker)


def find_tiffs_mode6(input_path: Path):
    """Mode 6: only TIFFs inside folders containing EXPORT_MARKER in their path (any subfolder)."""
    all_tiffs = find_tiffs_recursive(input_path)
    filtered = []
    skipped_decoder_out = 0
    marker_lower = EXPORT_MARKER.lower()
    for t in all_tiffs:
        # Match only directory parts; the filename itself is not an anchor
        parts_str = list(t.parts[:-1])
        # Match folders starting or ending with EXPORT_MARKER case-insensitively
        export_idx = next((i for i, p in enumerate(parts_str)
                           if _marker_matches(p.lower(), marker_lower)), None)
        if export_idx is not None:
            # Skip decoder output folders (16B_TIFF etc.) below the marker
            below = [p.lower() for p in parts_str[export_idx + 1:]]
            if _skip_decoder_output(below):
                skipped_decoder_out += 1
                continue
            filtered.append(t)
    if skipped_decoder_out:
        logger.info(f"Ignored {skipped_decoder_out} TIFF(s) in decoder output folders "
                    f"({', '.join(sorted(_DECODER_OUTPUT_FOLDERS))}) — use a different mode to re-encode them")
    return sorted(filtered)

def find_tiffs_mode7(input_path: Path):
    """Mode 7: only TIFFs inside EXPORT_MARKER/EXPORT_TIFF_SUBFOLDER specific subfolder."""
    all_tiffs = find_tiffs_recursive(input_path)
    filtered = []
    skipped_decoder_out = 0
    marker_lower = EXPORT_MARKER.lower()
    subfolder_lower = EXPORT_TIFF_SUBFOLDER.lower()
    for t in all_tiffs:
        # Match only directory parts; the filename itself is not an anchor
        parts_str = list(t.parts[:-1])
        export_idx = next((i for i, p in enumerate(parts_str)
                           if _marker_matches(p.lower(), marker_lower)), None)
        if export_idx is None:
            continue
        # Skip decoder output folders (16B_TIFF etc.) below the marker
        below = [p.lower() for p in parts_str[export_idx + 1:]]
        if _skip_decoder_output(below):
            skipped_decoder_out += 1
            continue
        if EXPORT_TIFF_SUBFOLDER:
            if export_idx + 1 < len(parts_str) and parts_str[export_idx + 1].lower() == subfolder_lower:
                filtered.append(t)
        else:
            filtered.append(t)
    if skipped_decoder_out:
        logger.info(f"Ignored {skipped_decoder_out} TIFF(s) in decoder output folders "
                    f"({', '.join(sorted(_DECODER_OUTPUT_FOLDERS))}) — use a different mode to re-encode them")
    return sorted(filtered)

def main():
    parser = argparse.ArgumentParser(description="Batch TIFF 16-bit -> JPEG XL converter")
    parser.add_argument("input",             type=Path, nargs="?", help="Input root folder")
    parser.add_argument("output", nargs="?", type=Path, help="Output folder (mode 0 only)")
    parser.add_argument("--mode",            type=int, default=0, choices=[0,1,2,3,4,5,6,7,8])
    parser.add_argument("--workers",         type=int, default=min(os.cpu_count() or 4, 16))
    parser.add_argument("--overwrite",       action="store_true",
                        help="Always overwrite existing JXLs")
    parser.add_argument("--sync",            action="store_true",
                        help="Only reconvert TIFFs newer than their existing JXL")
    parser.add_argument("--distance",        type=float, default=None,
                        help="JXL distance (0=lossless, 0.1=near-lossless, higher=more lossy)")
    parser.add_argument("--effort",          type=int, default=None, choices=range(1,11),
                        help="Compression effort 1-10 (default: 7)")
    parser.add_argument("--buffering",       type=int, default=None, choices=[0,1,2,3],
                        help="[libjxl >= 0.12] cjxl buffering level 0-3 (default: off = "
                             "use cjxl's own default; 0 = best compression, much slower "
                             "on large lossless images; ignored on older cjxl).")
    parser.add_argument("--ram",            action="store_true", default=None,
                        help="Keep PNG intermediate in RAM (faster, more memory)")
    parser.add_argument("--no-ram",         action="store_true", default=None,
                        help="Write PNG intermediate to disk (slower, less memory)")
    parser.add_argument("--delete-source",   action="store_true",
                        help="Delete source TIFFs after successful encode (mode 8 only)")
    parser.add_argument("--delete-confirm-off", action="store_true",
                        help="Skip the interactive delete confirmation. For automation/"
                             "wrappers that already asked the user (DELETE_CONFIRM stays "
                             "untouched for interactive runs).")
    parser.add_argument("--export-subfolder", type=str, default=None,
                        help="[Mode 7] Only process files inside this subfolder of the "
                             "export marker (default: script setting EXPORT_TIFF_SUBFOLDER, "
                             "empty = all subfolders). Overrides EXPORT_TIFF_SUBFOLDER.")
    parser.add_argument("--clean-staging", action="store_true",
                        help="Before converting, delete staging leftovers from EARLIER "
                             "runs (failed outputs kept for inspection). Files touched in "
                             "the last hour are left alone: a concurrent run may own them")
    parser.add_argument("--no-preflight", action="store_true",
                        help="Skip the pre-run space estimate (it encodes a few small "
                             "sample crops to measure this batch's compression)")
    parser.add_argument("--multipage-mode",  type=str, default=None,
                        choices=["ignore", "skip", "split", "split_all"],
                        help="How to handle multi-page TIFFs: split (default), ignore, skip, split_all")
    parser.add_argument("--thumbnail-mode",  type=str, default=None,
                        choices=["exclude", "include"],
                        help="When splitting: exclude thumbnails (default) or include them")
    parser.add_argument("--thumbnail-suffix", type=str, default=None,
                        help="Suffix for thumbnail outputs when --thumbnail-mode=include (default: _thumbnail)")
    parser.add_argument("--warn-thumbnail-discard", action="store_true",
                        help="Log one warning per file whose thumbnail page is dropped by "
                             "--thumbnail-mode exclude (default: only a single summary line)")
    parser.add_argument("--dry-run",         action="store_true",
                        help="Show what would be converted without converting")
    parser.add_argument("--summary-json",    action="store_true",
                        help=argparse.SUPPRESS)  # internal: machine-readable summary for jxl_photo.py
    parser.add_argument("--staging",         type=str, default=None,
                        help="Staging directory for output JXLs (reduces HDD seek contention)")
    parser.add_argument("--export-marker",  type=str, default=None,
                        help="Folder name marker for modes 6/7 (default: script setting EXPORT_MARKER)")
    parser.add_argument("--encode-tag",     type=str, default=None, choices=["xmp", "software", "off"],
                        help="Where to record encoding params: xmp (default), software, or off")
    parser.add_argument("--d50-patch",      type=str, default=None, choices=["on", "off", "auto"],
                        help="D50 illuminant patch: on (always), off (never), auto (detect from software)")
    parser.add_argument("--icc-png-strategy", type=str, default=None,
                        choices=["heuristic", "always", "skip", "cautious"],
                        help="How to handle ICC in the PNG intermediate for lossy encoding: "
                             "cautious (default: test each unseen ICC with a round-trip and cache the result), "
                             "heuristic (skip for large/scanner profiles), "
                             "always (embed always), skip (never embed).")
    parser.add_argument("--icc-cache-dir", type=str, default=None,
                        help="Directory for the ICC cautious-strategy cache "
                             "(default: APPDATA/jxl-photo/icc-cache on Windows, "
                             "~/.config/jxl-photo/icc-cache elsewhere).")
    parser.add_argument("--clear-icc-cache", action="store_true",
                        help="Clear the ICC cautious-strategy cache and exit.")
    parser.add_argument("--strip",           action="store_true",
                        help="Strip all metadata from output (no EXIF/XMP preservation)")
    parser.add_argument("--embed-thumbnail", action="store_true",
                        help="Embed a 256px JPEG thumbnail in EXIF for fast preview in viewers (~20KB)")
    args = parser.parse_args()

    global OVERWRITE, CJXL_DISTANCE, CJXL_EFFORT, CJXL_BUFFERING, USE_RAM_FOR_PNG, DELETE_SOURCE, DELETE_CONFIRM, TEMP2_DIR, ENCODE_TAG_MODE, D50_PATCH_MODE, EMBED_JPEG_THUMBNAIL, MULTIPAGE_TIFF_MODE, THUMBNAIL_MODE, THUMBNAIL_SUFFIX, WARN_DISCARDED_THUMBNAILS, ICC_PNG_STRATEGY, ICC_CACHE_DIR_OVERRIDE

    # ICC cache override and clearing must be processed before any logging or conversion.
    if args.icc_cache_dir is not None:
        ICC_CACHE_DIR_OVERRIDE = Path(args.icc_cache_dir)
    if args.clear_icc_cache:
        cleared = _clear_icc_cache()
        print(f"ICC cache {'cleared' if cleared else 'was already empty'}: {_icc_cache_dir()}")
        return

    if args.input is None:
        parser.error("the following arguments are required: input")

    if not args.input.exists():
        parser.error(f"input path does not exist: {args.input}")

    if args.workers < 1:
        parser.error("--workers must be >= 1")

    # Modes 6/7 anchor on an EXPORT *folder* in the path and scan recursively.
    # rglob() over a FILE yields nothing, so a single-file input reported
    # "Files found: 0" and exited 0 — a silent no-op that looks like success.
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

    if args.sync:
        OVERWRITE = "smart"
    elif args.overwrite:
        OVERWRITE = True

    if args.delete_source:
        DELETE_SOURCE = True
    if args.delete_confirm_off:
        DELETE_CONFIRM = False

    if args.export_subfolder is not None:
        global EXPORT_TIFF_SUBFOLDER
        EXPORT_TIFF_SUBFOLDER = args.export_subfolder

    if args.distance is not None:
        if not 0 <= args.distance <= 15:
            parser.error("--distance must be between 0 and 15")
        # cjxl clamps: --distance 0.005 and 0.05 were measured producing
        # byte-identical output. Someone asking for 0.02 to get "closer to
        # lossless" gets exactly the 0.05 file and should hear so, rather than
        # believe they bought quality they did not.
        if 0 < args.distance < _MIN_EFFECTIVE_DISTANCE:
            logger.warning(
                f"--distance {args.distance} behaves exactly like "
                f"{_MIN_EFFECTIVE_DISTANCE}: cjxl clamps every lossy distance below "
                f"that to the same output. Use --distance 0 for true lossless.")
        CJXL_DISTANCE = args.distance
    if args.effort is not None:
        CJXL_EFFORT = args.effort
    if args.buffering is not None:
        CJXL_BUFFERING = args.buffering
    if args.no_ram:
        USE_RAM_FOR_PNG = False
    elif args.ram is not None:
        USE_RAM_FOR_PNG = args.ram
    if args.staging is not None:
        TEMP2_DIR = args.staging
    if args.clean_staging:
        # Clean the EFFECTIVE staging dir — requiring --staging alongside
        # made the flag silently inert whenever staging came from the
        # script setting (the documented way).
        if TEMP2_DIR is not None:
            _clean_staging(TEMP2_DIR)
        else:
            logger.warning("--clean-staging: no staging directory configured; nothing to clean")
    if args.export_marker:
        global EXPORT_MARKER
        EXPORT_MARKER = args.export_marker
    if args.encode_tag is not None:
        ENCODE_TAG_MODE = args.encode_tag
    if args.d50_patch is not None:
        D50_PATCH_MODE = args.d50_patch
    if args.icc_png_strategy is not None:
        ICC_PNG_STRATEGY = args.icc_png_strategy

    if args.embed_thumbnail:
        EMBED_JPEG_THUMBNAIL = True

    if args.multipage_mode is not None:
        MULTIPAGE_TIFF_MODE = args.multipage_mode
    if args.thumbnail_mode is not None:
        THUMBNAIL_MODE = args.thumbnail_mode
    if args.thumbnail_suffix is not None:
        if not args.thumbnail_suffix.strip():
            parser.error("--thumbnail-suffix must not be empty (thumbnail names would collide with page 0)")
        THUMBNAIL_SUFFIX = args.thumbnail_suffix
    if args.warn_thumbnail_discard:
        WARN_DISCARDED_THUMBNAILS = True

    # Handle --strip flag - store in global for use in convert_one
    global STRIP_METADATA
    if args.strip:
        STRIP_METADATA = True
    log_file = setup_logger()

    # cjxl availability is checked AFTER the dry-run block (a simulation
    # never invokes cjxl, so it must not require it). See below.
    if not args.dry_run and _get_cjxl_cmd() is None:
        logger.error("cjxl not found in PATH. Install libjxl and add cjxl to PATH.")
        sys.exit(1)
    # exiftool is just as required: without it no ICC/EXIF is preserved and the
    # multi-page/grayscale markers are never written, so the decoder cannot
    # reconstruct anything. Failing here beats N cryptic per-file errors.
    if not args.dry_run and shutil.which(_get_exiftool_cmd()) is None:
        logger.error("exiftool not found in PATH. It is REQUIRED: without it ICC/EXIF "
                     "are not preserved and the round-trip markers are never written. "
                     "https://exiftool.org")
        sys.exit(1)
    if not args.dry_run:
        _warn_if_libjxl_too_old(_get_cjxl_cmd() or "cjxl")

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
        # Mode 2: recursive, all files to output_root (single file also accepted)
        if args.input.is_file():
            tiffs = [args.input]
        else:
            tiffs = find_tiffs_recursive(args.input)
        output_root = args.output or args.input
        # mkdir deferred until after the dry-run check — a simulation must
        # not create folders on disk.
    elif args.mode == 6:
        tiffs = find_tiffs_mode6(args.input)
        output_root = args.input
    elif args.mode == 7:
        tiffs = find_tiffs_mode7(args.input)
        output_root = args.input
    elif args.mode == 8:
        # Mode 8: in-place recursive + delete source (single file also accepted)
        if args.input.is_file():
            tiffs = [args.input]
        else:
            tiffs = find_tiffs_recursive(args.input)
        output_root = args.input
    else:
        # Modes 3/4/5: a single FILE input is valid too (find_tiffs_recursive
        # only works on directories and would silently find nothing).
        if args.input.is_file():
            tiffs = [args.input]
        else:
            tiffs = find_tiffs_recursive(args.input)
        output_root = args.input

    logger.info(f"Files found: {len(tiffs)}")

    # Build (tiff, final_jxl, page_idx, is_thumbnail) items.
    # Each TIFF may produce one or more JXLs depending on MULTIPAGE_TIFF_MODE.
    #
    # Every mode opens each TIFF here to read its IFD chain (page count, samples
    # per pixel, thumbnail flags). That is header-only — no pixel decode — but at
    # one open per file it is dominated by seek latency, so a library on an
    # external/network drive can sit here for minutes. The scan CANNOT simply be
    # folded into the conversion loop: the duplicate-destination guard below has
    # to see every planned output BEFORE the first byte is written, or two inputs
    # silently overwrite each other. So it runs in parallel and reports progress
    # instead, which is where the "it just hangs reading" impression came from.
    all_items = []
    skipped_files = 0
    analyze_errors = 0
    multipage_skipped = 0

    def _plan_one(t: Path):
        """Resolve the output path and page list for one TIFF. Thread-safe:
        touches only locals plus the lock-protected discard counters."""
        # Resolve the main JXL path just to know the output directory
        if args.mode == 0:
            if output_root != args.input:
                main_jxl = output_root / t.with_suffix(".jxl").name
            else:
                main_jxl = t.parent / t.with_suffix(".jxl").name
        elif args.mode == 1:
            main_jxl = t.parent / CONVERTED_JXL_FOLDER / t.with_suffix(".jxl").name
        elif args.mode == 2:
            if args.output is not None:
                main_jxl = output_root / t.with_suffix(".jxl").name
            elif args.input.is_file():
                main_jxl = t.parent / CONVERTED_JXL_FOLDER / t.with_suffix(".jxl").name
            else:
                main_jxl = output_root / t.with_suffix(".jxl").name
        else:
            main_jxl = resolve_output(t, args.mode, args.input)

        if main_jxl is None:
            return ("skipped_by_mode", None)  # e.g. outside _EXPORT

        try:
            items = convert_multipage(t, main_jxl.parent, args.mode)
        except UnreadableTiff as e:
            # Caught before the generic handler below: a file with no readable
            # pages is a broken file, not a policy skip and not a crash.
            return ("unreadable", str(e))
        except Exception as e:
            # A corrupt/unreadable TIFF must not abort the whole batch at
            # planning time — log one error and move on, matching pre-multipage
            # behavior where such files failed individually during conversion.
            return ("error", str(e))
        if not items:
            # Multipage "skip" mode (or no encodable pages): the file was seen
            # and intentionally skipped — count it so the summary accounts for
            # every input file.
            return ("multipage_skipped", None)
        return ("items", items)

    # Threads, not processes: the work is blocking file I/O inside tifffile, so
    # the GIL is released where it matters. Capped because more concurrent reads
    # than the drive can serve just thrashes the heads on spinning media.
    scan_workers = max(1, min(args.workers, 16, len(tiffs) or 1))
    scan_started = datetime.now()
    if len(tiffs) > 1:
        logger.info(f"Analyzing TIFF pages ({scan_workers} workers)...")
    progress_step = max(250, len(tiffs) // 20) if tiffs else 0
    results_by_index: Dict[int, Any] = {}
    with ThreadPoolExecutor(max_workers=scan_workers) as pool:
        futures = {pool.submit(_plan_one, t): i for i, t in enumerate(tiffs)}
        done_count = 0
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results_by_index[idx] = fut.result()
            except Exception as e:  # defensive: _plan_one already catches its own
                results_by_index[idx] = ("error", str(e))
            done_count += 1
            if progress_step and done_count % progress_step == 0:
                logger.info(f"  Analyzed {done_count}/{len(tiffs)} TIFF(s)...")

    # Which files actually failed, for the wrapper's end-of-run FAILURES list.
    # Counts alone don't answer "did something break in the middle?" — the user
    # walks away from a multi-hour manifest and needs the paths on return.
    failed_files = []
    # Files that are simply broken. Tracked apart from both buckets: they are
    # not policy skips (nothing asked for them to be dropped) and not errors
    # (the run did not fail), but the user still has to know about them.
    unreadable_files = []

    # Consume in INPUT order so the conversion order (and the log) stays
    # deterministic regardless of how the threads finished.
    for i, t in enumerate(tiffs):
        kind, payload = results_by_index[i]
        if kind == "skipped_by_mode":
            skipped_files += 1
        elif kind == "error":
            logger.error(f"SKIP (cannot analyze TIFF) | {t.name} | {payload}")
            analyze_errors += 1
            failed_files.append((str(t), f"cannot analyze TIFF: {payload}"))
        elif kind == "unreadable":
            logger.warning(f"UNREADABLE (not converted) | {t.name} | {payload}")
            unreadable_files.append((str(t), payload))
        elif kind == "multipage_skipped":
            multipage_skipped += 1
        else:
            all_items.extend(payload)

    if len(tiffs) > 1:
        logger.info(f"Page analysis done in {(datetime.now() - scan_started).total_seconds():.1f}s")

    planned_msg = f"JXL outputs planned: {len(all_items)} (from {len(tiffs)} TIFFs, {skipped_files} skipped by mode"
    if multipage_skipped:
        planned_msg += f", {multipage_skipped} skipped by multipage policy"
    if unreadable_files:
        planned_msg += f", {len(unreadable_files)} corrupt"
    if analyze_errors:
        # Distinct from "corrupt" above: analysis raised rather than returning
        # an empty page list. Both are broken files; only this one is an error.
        planned_msg += f", {analyze_errors} failed to analyze"
    planned_msg += ")"
    logger.info(planned_msg)

    _abort_on_duplicate_outputs([(item[0], item[1]) for item in all_items])
    _counter["total"] = len(all_items)

    # Dry run
    if args.dry_run:
        for t, j, page_idx, is_thumb, subfiletype, samples in all_items:
            thumb_label = " [thumbnail]" if is_thumb else ""
            gray_label = " [grayscale]" if samples == 1 else ""
            logger.info(f" DRY | {t.name} page{page_idx}{thumb_label}{gray_label} > {j}")
        logger.info(f"Dry run: {len(all_items)} output(s) would be generated from {len(tiffs)} TIFF(s).")
        # A dry run is exactly where the user checks whether pages get dropped,
        # so the discard totals must print here too — the real-run summary below
        # is never reached.
        _log_discard_summary()
        emit_summary_json(
            args.summary_json,
            ok=len(all_items), overwritten=0,
            skipped=skipped_files + multipage_skipped,
            errors=analyze_errors,
            log_file=log_file,
            extras={
                "Thumbnails excluded": _thumbnails_dropped["pages"],
                "Multipage pages ignored": _multipage_ignored["pages"],
            },
            failures=failed_files,
            unreadable=unreadable_files,
            dry_run=True,
        )
        return

    # Create the mode-2 output dir only for real runs (dry-run must not write)
    if args.mode == 2 and not args.input.is_file():
        output_root.mkdir(parents=True, exist_ok=True)

    if args.mode == 8 and DELETE_SOURCE:
        logger.info("Mode 8 -- in-place recursive | DELETE_SOURCE=True: source TIFFs will be deleted after successful encode")
        if DELETE_CONFIRM:
            is_lossy = CJXL_DISTANCE > 0
            if not confirm_deletion_tiff(is_lossy):
                logger.info("Deletion not confirmed -- exiting.")
                sys.exit(3)
    elif args.mode == 8:
        logger.info("Mode 8 -- in-place recursive | DELETE_SOURCE=False: TIFF and JXL will coexist")

    # Group by output folder (one bulk move per group)
    groups: Dict[Path, list] = {}
    for t, j, page_idx, is_thumb, subfiletype, samples in all_items:
        groups.setdefault(j.parent, []).append((t, j, page_idx, is_thumb, subfiletype, samples))

    logger.info(f"Output groups: {len(groups)}")

    # Advisory space estimate, before a single real file is converted. Skipped
    # for dry runs (nothing gets written) and behind --no-preflight for anyone
    # who does not want to pay the sample encodes.
    if not args.dry_run and not args.no_preflight:
        try:
            _preflight_space(groups, CJXL_DISTANCE, CJXL_EFFORT, TEMP2_DIR)
        except Exception as e:
            # An advisory check must never be the thing that kills a run.
            logger.debug(f"Preflight skipped: {e}")

    ok = skipped = overwritten = synced = aborted = 0
    err = analyze_errors  # count TIFFs that couldn't be analyzed at planning time
    _reset_abort()  # a fresh run must not inherit a previous one's latch

    # ONE pool for the whole run. Feeding it folder by folder meant a folder with
    # fewer files than --workers could never fill the pool, and every folder
    # boundary drained it — modes 3/5/6/7 create one output folder per shoot, so
    # a big library ran at a fraction of the requested concurrency. The staging
    # move is still per-folder and still in bulk; it now fires when that folder's
    # last file lands (see process_group).
    results = process_group(all_items, args.workers, args.mode)

    for result in results:
        status = result[1]
        if   status == "ok":
            ok += 1
            if args.sync:
                synced += 1
        elif status == "overwrite":
            ok += 1; overwritten += 1; synced += 1
        elif status == "skipped":   skipped += 1
        # Counted apart from BOTH skipped and errors: the run gave up before
        # these were tried. Calling them "skipped" would hide them behind a
        # policy nobody chose, and calling them errors would invent thousands
        # of failures that never happened.
        elif status == "aborted":   aborted += 1
        elif status == "error":
            err += 1
            # result[0] is (path, page_idx); result[2] is the exception text.
            failed_files.append((str(result[0][0]), str(result[2])))

    # Account for files intentionally skipped by the multipage policy
    skipped += multipage_skipped

    logger.info(f"\n{'-'*50}")
    if args.sync:
        logger.info(f"SYNC done: {synced} reconverted | {skipped} up to date | {err} errors")
        logger.info(f"  -> Reconverted: TIFFs newer than their existing JXL")
        logger.info(f"  -> Up to date: JXL is newer than or equal to TIFF")
    else:
        logger.info(f"Done: {ok} OK | {overwritten} overwrites | {skipped} skipped | {err} errors")

    # Loudest line in the block, and last: on a batch that ran for hours the
    # reason it stopped must not be buried above the per-file scrollback.
    if _aborted():
        logger.error(f"RUN ABORTED: {_aborted()}")
        logger.error(f"  {aborted} file(s) were never attempted (not failures).")

    # Named separately from "skipped": these files were not dropped by any
    # setting the user chose, they simply could not be read.
    if unreadable_files:
        logger.warning(f"Corrupt: {len(unreadable_files)} file(s) had no readable pages and were NOT converted:")
        for path, _reason in unreadable_files[:10]:
            logger.warning(f"  -> {path}")
        if len(unreadable_files) > 10:
            logger.warning(f"  -> ... and {len(unreadable_files) - 10} more (see the list above in this log)")

    _log_discard_summary()

    # D50 patch summary
    applied = _d50_patch_count["applied"]
    d50_skipped = _d50_patch_count["skipped"]
    already_correct = _d50_patch_count["already_correct"]
    skipped_needed = _d50_patch_count["skipped_needed"]
    applied_already_correct = _d50_patch_count["applied_already_correct"]
    skipped_already_correct = _d50_patch_count["skipped_already_correct"]

    # Total files analyzed for D50 = those where patch was applied + those skipped
    total_analyzed = applied + d50_skipped

    if total_analyzed > 0:
        if D50_PATCH_MODE == "off":
            # For mode off, we still tracked correctness so user knows how many would have needed patch
            logger.info(f"D50 patch: {already_correct} already correct | {skipped_needed} would have needed (mode: off)")
        else:
            # Files that were actually patched (had wrong D50)
            actually_patched = applied - applied_already_correct
            # Total files that needed patching vs total that were already correct
            total_needed_patch = actually_patched + skipped_needed
            # Counters increment per page in multipage splits; unique profiles
            # deduplicates pages that share the same ICC.
            unique_profiles = len(_d50_patched_hashes)
            unique_label = f" ({unique_profiles} unique profiles)" if unique_profiles != actually_patched else ""
            logger.info(f"D50 patch: {actually_patched} applied{unique_label} | {skipped_already_correct} skipped (already correct) | {total_needed_patch} needed patch, {already_correct} already correct (mode: {D50_PATCH_MODE})")

    logger.info(f"Log: {log_file}")

    # Emitted BEFORE the exit below: a run with failures is exactly the run the
    # wrapper most needs the summary from.
    emit_summary_json(
        args.summary_json,
        ok=synced if args.sync else ok,
        overwritten=overwritten,
        skipped=skipped,
        errors=err,
        log_file=log_file,
        extras={
            "Thumbnails excluded": _thumbnails_dropped["pages"],
            "Multipage pages ignored": _multipage_ignored["pages"],
            "D50 patched": (applied - applied_already_correct) if D50_PATCH_MODE != "off" else 0,
            "Not attempted (run aborted)": aborted,
        },
        failures=failed_files,
        unreadable=unreadable_files,
    )

    # Whatever this run could not move out is still sitting on the staging
    # drive; saying so is what keeps the leak from being invisible.
    _report_staging_leftovers(TEMP2_DIR)

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
